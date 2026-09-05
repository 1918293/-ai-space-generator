from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any, Mapping, Protocol
import uuid

from .action_catalog import ModelActionIntent
from .control_gateway import ModelIngressRequest
from .execution_control import RunPhase
from .operational_state import ActiveOperationalState
from .production_execution import PendingControlledRun


SCOPE_READ = "hao:read"
SCOPE_EXECUTE = "hao:execute"
SCOPE_APPROVE = "hao:approve"


@dataclass(frozen=True)
class MCPPrincipal:
    subject: str
    scopes: frozenset[str]


class HaoMCPIdentityPolicy:
    """Application policy on top of OAuth token validation."""

    def __init__(self, expected_subject: str) -> None:
        expected_subject = expected_subject.strip()
        if not expected_subject:
            raise ValueError("EXPECTED_HAO_SUBJECT_REQUIRED")
        self._expected_subject = expected_subject

    def require(self, principal: MCPPrincipal, scope: str) -> None:
        if principal.subject.strip() != self._expected_subject:
            raise PermissionError("HAO_IDENTITY_REQUIRED")
        if scope not in principal.scopes:
            raise PermissionError(f"MISSING_SCOPE:{scope}")


@dataclass(frozen=True)
class RegisteredControlledRun:
    workflow_id: str
    owner_subject: str
    operational_version: int


class OperationalStateReader(Protocol):
    def get(self) -> ActiveOperationalState: ...


class MCPRunRegistry(Protocol):
    def register(
        self,
        *,
        workflow_id: str,
        owner_subject: str,
        operational_version: int,
    ) -> RegisteredControlledRun: ...

    def require_owned(
        self,
        *,
        workflow_id: str,
        owner_subject: str,
    ) -> RegisteredControlledRun: ...


class RuntimeTelemetrySink(Protocol):
    def record_run_event(
        self,
        event: str,
        *,
        phase: str,
        provider: str = "",
        failure_stage: str = "",
        failure_code: str = "",
    ) -> None: ...

    def record_authoritative_completion(self) -> None: ...


class SQLiteMCPRunRegistry:
    """Reference durable ownership binding for single-node/test use."""

    def __init__(self, path: str) -> None:
        self._path = path
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_control_runs (
                    workflow_id TEXT PRIMARY KEY,
                    owner_subject TEXT NOT NULL,
                    operational_version INTEGER NOT NULL
                )
                """
            )

    def register(
        self,
        *,
        workflow_id: str,
        owner_subject: str,
        operational_version: int,
    ) -> RegisteredControlledRun:
        workflow_id = workflow_id.strip()
        owner_subject = owner_subject.strip()
        if not workflow_id or not owner_subject or operational_version < 1:
            raise ValueError("INVALID_MCP_RUN_REGISTRATION")
        with sqlite3.connect(self._path, isolation_level=None) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_subject, operational_version FROM mcp_control_runs WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            if row is not None:
                conn.execute("COMMIT")
                if row[0] == owner_subject and row[1] == operational_version:
                    return RegisteredControlledRun(workflow_id, owner_subject, operational_version)
                raise ValueError("MCP_RUN_IDENTITY_CONFLICT")
            conn.execute(
                "INSERT INTO mcp_control_runs(workflow_id, owner_subject, operational_version) VALUES (?, ?, ?)",
                (workflow_id, owner_subject, operational_version),
            )
            conn.execute("COMMIT")
        return RegisteredControlledRun(workflow_id, owner_subject, operational_version)

    def require_owned(
        self,
        *,
        workflow_id: str,
        owner_subject: str,
    ) -> RegisteredControlledRun:
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                "SELECT owner_subject, operational_version FROM mcp_control_runs WHERE workflow_id = ?",
                (workflow_id.strip(),),
            ).fetchone()
        if row is None:
            raise PermissionError("CONTROLLED_RUN_NOT_FOUND")
        if row[0] != owner_subject.strip():
            raise PermissionError("CONTROLLED_RUN_OWNER_MISMATCH")
        return RegisteredControlledRun(workflow_id.strip(), row[0], row[1])


@dataclass(frozen=True)
class MCPSubmissionView:
    workflow_id: str
    code: str
    mode: str
    task: str
    phase: str
    authorization_scope: str = ""


@dataclass(frozen=True)
class MCPStatusView:
    workflow_id: str
    mode: str
    task: str
    phase: str
    failure_stage: str = ""
    failure_code: str = ""
    authorization_scope: str = ""


@dataclass(frozen=True)
class MCPFinalizeView:
    workflow_id: str
    authoritative: bool
    code: str
    phase: str


@dataclass(frozen=True)
class MCPParentTaskView:
    task_run_id: str
    phase: str
    failure_code: str = ""
    child_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class MCPParentChildSubmissionView:
    task_run_id: str
    slot_id: str
    workflow_id: str
    action_id: str
    accepted: bool
    code: str
    phase: str
    authorization_scope: str = ""


def _argument_pairs(arguments: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not arguments:
        return ()
    return tuple((str(key), str(value)) for key, value in arguments.items())


class MCPControlBridge:
    """Stateless authenticated bridge from MCP tools into the Runtime v2 control plane."""

    def __init__(
        self,
        *,
        production: Any,
        operational_state: OperationalStateReader,
        run_registry: MCPRunRegistry,
        identity_policy: HaoMCPIdentityPolicy,
        telemetry: RuntimeTelemetrySink | None = None,
        parent_tasks: Any | None = None,
    ) -> None:
        self._production = production
        self._operational_state = operational_state
        self._run_registry = run_registry
        self._identity_policy = identity_policy
        self._telemetry = telemetry
        self._parent_tasks = parent_tasks

    def _record(
        self,
        event: str,
        *,
        phase: str,
        provider: str = "",
        failure_stage: str = "",
        failure_code: str = "",
    ) -> None:
        if self._telemetry is not None:
            self._telemetry.record_run_event(
                event,
                phase=phase,
                provider=provider,
                failure_stage=failure_stage,
                failure_code=failure_code,
            )

    def _require_parent_tasks(self) -> Any:
        if self._parent_tasks is None:
            raise ValueError("PARENT_TASK_RUNTIME_NOT_CONFIGURED")
        return self._parent_tasks

    def operational_context(self, principal: MCPPrincipal) -> dict[str, object]:
        self._identity_policy.require(principal, SCOPE_READ)
        state = self._operational_state.get()
        return {
            "mode": state.mode.value,
            "task": state.task,
            "operational_version": state.version,
        }

    async def submit(
        self,
        principal: MCPPrincipal,
        *,
        requested_capability: str,
        binding_id: str,
        expected_state_delta: str = "",
        authorization_target: str = "",
        arguments: Mapping[str, str] | None = None,
    ) -> MCPSubmissionView:
        self._identity_policy.require(principal, SCOPE_EXECUTE)
        state = self._operational_state.get()
        run_id = "RUN-MCP-" + uuid.uuid4().hex
        intent = ModelActionIntent(
            intent_id="INTENT-MCP-" + uuid.uuid4().hex,
            requested_capability=requested_capability.strip(),
            binding_id=binding_id.strip(),
            expected_state_delta=expected_state_delta.strip(),
            authorization_target=authorization_target.strip(),
            arguments=_argument_pairs(arguments),
        )
        submission = await self._production.submit(
            state,
            ModelIngressRequest(run_id=run_id, sequence=1, intent=intent),
        )
        if not submission.accepted or submission.pending is None:
            self._record(
                "submit_rejected",
                phase=RunPhase.BLOCKED.value,
                failure_stage="BINDING_OR_POLICY",
                failure_code=submission.code,
            )
            return MCPSubmissionView(
                workflow_id="",
                code=submission.code,
                mode=state.mode.value,
                task=state.task,
                phase=RunPhase.BLOCKED.value,
            )

        self._run_registry.register(
            workflow_id=submission.pending.handle.workflow_id,
            owner_subject=principal.subject,
            operational_version=state.version,
        )
        current = await self._production.current_state(submission.pending)
        if current is None:
            phase = RunPhase.RESOLVED.value
            authorization_scope = ""
            provider = ""
            failure_stage = ""
            failure_code = ""
        else:
            phase = current.phase.value
            authorization_scope = (
                current.action.authorization_scope if current.action is not None else ""
            )
            provider = current.action.provider if current.action is not None else ""
            failure_stage = current.failure_stage.value if current.failure_stage else ""
            failure_code = current.failure_code
        self._record(
            "submitted",
            phase=phase,
            provider=provider,
            failure_stage=failure_stage,
            failure_code=failure_code,
        )
        return MCPSubmissionView(
            workflow_id=submission.pending.handle.workflow_id,
            code=submission.code,
            mode=state.mode.value,
            task=state.task,
            phase=phase,
            authorization_scope=authorization_scope,
        )

    async def _pending_for(
        self,
        principal: MCPPrincipal,
        workflow_id: str,
        *,
        required_scope: str,
    ) -> PendingControlledRun:
        self._identity_policy.require(principal, required_scope)
        registration = self._run_registry.require_owned(
            workflow_id=workflow_id,
            owner_subject=principal.subject,
        )
        return await self._production.resume(
            registration.workflow_id,
            operational_version=registration.operational_version,
        )

    async def _status_from_pending(
        self,
        workflow_id: str,
        pending: PendingControlledRun,
    ) -> MCPStatusView:
        current = await self._production.current_state(pending)
        if current is None:
            self._record("status", phase="UNKNOWN")
            return MCPStatusView(workflow_id, "", "", "UNKNOWN")
        action_scope = current.action.authorization_scope if current.action else ""
        provider = current.action.provider if current.action else ""
        failure_stage = current.failure_stage.value if current.failure_stage else ""
        self._record(
            "status",
            phase=current.phase.value,
            provider=provider,
            failure_stage=failure_stage,
            failure_code=current.failure_code,
        )
        return MCPStatusView(
            workflow_id=workflow_id,
            mode=current.mode.value,
            task=current.task,
            phase=current.phase.value,
            failure_stage=failure_stage,
            failure_code=current.failure_code,
            authorization_scope=action_scope,
        )

    async def status(self, principal: MCPPrincipal, *, workflow_id: str) -> MCPStatusView:
        pending = await self._pending_for(
            principal,
            workflow_id,
            required_scope=SCOPE_READ,
        )
        return await self._status_from_pending(workflow_id, pending)

    async def authorize_after_human_confirmation(
        self,
        principal: MCPPrincipal,
        *,
        workflow_id: str,
        scope: str,
        approved: bool,
        reason: str = "",
        human_confirmed: bool,
    ) -> MCPStatusView:
        if not human_confirmed:
            raise PermissionError("HUMAN_CONFIRMATION_REQUIRED")
        pending = await self._pending_for(
            principal,
            workflow_id,
            required_scope=SCOPE_APPROVE,
        )
        current = await self._production.current_state(pending)
        if current is None or current.phase != RunPhase.AWAITING_HAO or current.action is None:
            raise ValueError("RUN_NOT_AWAITING_HAO_AUTHORIZATION")
        expected_scope = current.action.authorization_scope
        if not expected_scope or scope.strip() != expected_scope:
            raise PermissionError("AUTHORIZATION_SCOPE_MISMATCH")
        await self._production.authorize(
            pending,
            scope=expected_scope,
            approved=approved,
            reason=reason,
        )
        self._record(
            "hao_authorization",
            phase=RunPhase.RESOLVED.value if approved else RunPhase.BLOCKED.value,
            provider=current.action.provider,
        )
        # Approval is its own OAuth capability. Do not require an additional
        # hao:read scope after the signal has already changed durable state.
        return await self._status_from_pending(workflow_id, pending)

    async def finalize(self, principal: MCPPrincipal, *, workflow_id: str) -> MCPFinalizeView:
        pending = await self._pending_for(
            principal,
            workflow_id,
            required_scope=SCOPE_EXECUTE,
        )
        current = await self._production.current_state(pending)
        if current is None:
            self._record(
                "finalize_rejected",
                phase="UNKNOWN",
                failure_stage="COMPLETION",
                failure_code="CONTROLLED_RUN_STATE_UNKNOWN",
            )
            return MCPFinalizeView(workflow_id, False, "CONTROLLED_RUN_STATE_UNKNOWN", "UNKNOWN")
        if current.phase not in {
            RunPhase.CLOSED,
            RunPhase.BLOCKED,
            RunPhase.FAILED,
            RunPhase.UNSYNCED,
        }:
            code = "CONTROLLED_RUN_NOT_TERMINAL:" + current.phase.value
            self._record(
                "finalize_rejected",
                phase=current.phase.value,
                provider=current.action.provider if current.action else "",
                failure_stage="COMPLETION",
                failure_code=code,
            )
            return MCPFinalizeView(
                workflow_id,
                False,
                code,
                current.phase.value,
            )
        result = await self._production.finalize(
            pending,
            issued_at=datetime.now(timezone.utc).isoformat(),
        )
        phase = result.record.phase.value if result.record else "UNKNOWN"
        self._record(
            "finalized",
            phase=phase,
            provider=(
                result.record.action.provider
                if result.record is not None and result.record.action is not None
                else ""
            ),
            failure_code="" if result.authoritative else result.code,
            failure_stage="" if result.authoritative else "COMPLETION",
        )
        if result.authoritative and self._telemetry is not None:
            self._telemetry.record_authoritative_completion()
        return MCPFinalizeView(workflow_id, result.authoritative, result.code, phase)

    def parent_start(self, principal: MCPPrincipal, *, plan_id: str) -> MCPParentTaskView:
        self._identity_policy.require(principal, SCOPE_EXECUTE)
        parent_tasks = self._require_parent_tasks()
        state = self._operational_state.get()
        opened = parent_tasks.start(state, plan_id=plan_id)
        return MCPParentTaskView(
            task_run_id=opened.task_run_id,
            phase=opened.phase.value,
            child_slots=tuple(child.slot_id for child in opened.child_slots),
        )

    async def parent_submit_child(
        self,
        principal: MCPPrincipal,
        *,
        task_run_id: str,
        slot_id: str,
        expected_state_delta: str = "",
        arguments: Mapping[str, str] | None = None,
    ) -> MCPParentChildSubmissionView:
        self._identity_policy.require(principal, SCOPE_EXECUTE)
        parent_tasks = self._require_parent_tasks()
        state = self._operational_state.get()
        submission = await parent_tasks.submit_child(
            state,
            task_run_id=task_run_id,
            slot_id=slot_id,
            expected_state_delta=expected_state_delta,
            arguments=arguments,
        )
        if not submission.accepted or not submission.workflow_id:
            return MCPParentChildSubmissionView(
                task_run_id=submission.task_run_id,
                slot_id=submission.slot_id,
                workflow_id="",
                action_id=submission.action_id,
                accepted=False,
                code=submission.code,
                phase=RunPhase.BLOCKED.value,
            )

        registration = self._run_registry.register(
            workflow_id=submission.workflow_id,
            owner_subject=principal.subject,
            operational_version=state.version,
        )
        pending = await self._production.resume(
            registration.workflow_id,
            operational_version=registration.operational_version,
        )
        current = await self._production.current_state(pending)
        phase = RunPhase.RESOLVED.value if current is None else current.phase.value
        authorization_scope = (
            current.action.authorization_scope
            if current is not None and current.action is not None
            else ""
        )
        return MCPParentChildSubmissionView(
            task_run_id=submission.task_run_id,
            slot_id=submission.slot_id,
            workflow_id=submission.workflow_id,
            action_id=submission.action_id,
            accepted=True,
            code=submission.code,
            phase=phase,
            authorization_scope=authorization_scope,
        )

    async def parent_refresh(
        self,
        principal: MCPPrincipal,
        *,
        task_run_id: str,
    ) -> MCPParentTaskView:
        self._identity_policy.require(principal, SCOPE_EXECUTE)
        parent_tasks = self._require_parent_tasks()
        state = self._operational_state.get()
        record = await parent_tasks.refresh(state, task_run_id=task_run_id)
        return MCPParentTaskView(
            task_run_id=record.task_run_id,
            phase=record.phase.value,
            failure_code=record.failure_code,
        )

    async def parent_accept_after_human_confirmation(
        self,
        principal: MCPPrincipal,
        *,
        task_run_id: str,
        accepted: bool,
        human_confirmed: bool,
    ) -> MCPParentTaskView:
        if not human_confirmed:
            raise PermissionError("HUMAN_CONFIRMATION_REQUIRED")
        self._identity_policy.require(principal, SCOPE_APPROVE)
        parent_tasks = self._require_parent_tasks()
        state = self._operational_state.get()
        record = await parent_tasks.refresh(state, task_run_id=task_run_id)
        if record.phase.value != "AWAITING_HAO" or len(record.child_outcomes) < len(
            record.required_action_ids
        ):
            raise ValueError("PARENT_TASK_NOT_AWAITING_HAO_ACCEPTANCE")
        accepted_record = parent_tasks.record_hao_acceptance(
            task_run_id=task_run_id,
            accepted=accepted,
        )
        return MCPParentTaskView(
            task_run_id=accepted_record.task_run_id,
            phase=accepted_record.phase.value,
            failure_code=accepted_record.failure_code,
        )
