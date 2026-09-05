from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import sqlite3
from typing import Any, Mapping, Protocol
import uuid

from .action_catalog import ModelActionIntent
from .control_gateway import ModelIngressRequest
from .execution_control import RunPhase
from .operational_state import ActiveOperationalState
from .production_execution import PendingControlledRun
from .reconciliation import (
    ReconciliationDisposition,
    ReconciliationPhase,
    apply_reconciliation,
)
from .reconciliation_retry import retry_request_fingerprint


SCOPE_READ = "hao:read"
SCOPE_EXECUTE = "hao:execute"
SCOPE_APPROVE = "hao:approve"
_ACTION_BINDING_SUFFIX = re.compile(r":A\d{4}:(.+)\Z")


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


@dataclass(frozen=True)
class MCPReconciliationView:
    case_id: str
    run_id: str
    action_id: str
    kind: str
    phase: str
    effect_may_have_occurred: bool
    disposition: str = ""
    resolution_code: str = ""
    evidence: tuple[str, ...] = ()


def _argument_pairs(arguments: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not arguments:
        return ()
    return tuple((str(key), str(value)) for key, value in arguments.items())


def _binding_from_action_id(action_id: str) -> str:
    match = _ACTION_BINDING_SUFFIX.search(action_id.strip())
    if match is None or not match.group(1).strip():
        raise ValueError("RECONCILIATION_ACTION_BINDING_UNRESOLVED")
    return match.group(1).strip()


def _authorization_target_from_scope(scope: str) -> str:
    scope = scope.strip()
    if not scope:
        return ""
    parts = scope.split(":", 1)
    return parts[1].strip() if len(parts) == 2 else ""


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
        reconciliation_store: Any | None = None,
        reconciliation_inspector: Any | None = None,
        reconciliation_retry_store: Any | None = None,
    ) -> None:
        self._production = production
        self._operational_state = operational_state
        self._run_registry = run_registry
        self._identity_policy = identity_policy
        self._telemetry = telemetry
        self._parent_tasks = parent_tasks
        self._reconciliation_store = reconciliation_store
        self._reconciliation_inspector = reconciliation_inspector
        self._reconciliation_retry_store = reconciliation_retry_store

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

    def _require_reconciliation_store(self) -> Any:
        if self._reconciliation_store is None:
            raise ValueError("RECONCILIATION_RUNTIME_NOT_CONFIGURED")
        return self._reconciliation_store

    def _require_reconciliation_inspector(self) -> Any:
        if self._reconciliation_inspector is None:
            raise ValueError("RECONCILIATION_INSPECTOR_NOT_CONFIGURED")
        return self._reconciliation_inspector

    def _require_reconciliation_retry_store(self) -> Any:
        if self._reconciliation_retry_store is None:
            raise ValueError("RECONCILIATION_RETRY_STORE_NOT_CONFIGURED")
        return self._reconciliation_retry_store

    @staticmethod
    def _reconciliation_view(case: Any) -> MCPReconciliationView:
        return MCPReconciliationView(
            case_id=case.case_id,
            run_id=case.run_id,
            action_id=case.action_id,
            kind=case.kind.value,
            phase=case.phase.value,
            effect_may_have_occurred=bool(case.effect_may_have_occurred),
            disposition=case.disposition.value if case.disposition else "",
            resolution_code=case.resolution_code,
            evidence=tuple(
                f"{item.evidence_id}:{item.kind.value}:{'PASS' if item.passed else 'FAIL'}:{item.source}"
                for item in case.evidence
            ),
        )

    def _owned_reconciliation_case(
        self,
        principal: MCPPrincipal,
        *,
        case_id: str,
        required_scope: str,
    ) -> tuple[Any, RegisteredControlledRun]:
        self._identity_policy.require(principal, required_scope)
        store = self._require_reconciliation_store()
        case = store.get(case_id.strip())
        if case is None:
            raise ValueError("RECONCILIATION_CASE_NOT_FOUND")
        registration = self._run_registry.require_owned(
            workflow_id=case.run_id,
            owner_subject=principal.subject,
        )
        return case, registration

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
        phase = RunPhase.RESOLVED.value if current is None else current.phase.value
        authorization_scope = (
            current.action.authorization_scope
            if current is not None and current.action is not None
            else ""
        )
        provider = current.action.provider if current is not None and current.action is not None else ""
        failure_stage = current.failure_stage.value if current is not None and current.failure_stage else ""
        failure_code = current.failure_code if current is not None else ""
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
        pending = await self._pending_for(principal, workflow_id, required_scope=SCOPE_READ)
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
        pending = await self._pending_for(principal, workflow_id, required_scope=SCOPE_APPROVE)
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
        return await self._status_from_pending(workflow_id, pending)

    async def finalize(self, principal: MCPPrincipal, *, workflow_id: str) -> MCPFinalizeView:
        pending = await self._pending_for(principal, workflow_id, required_scope=SCOPE_EXECUTE)
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
            return MCPFinalizeView(workflow_id, False, code, current.phase.value)
        result = await self._production.finalize(
            pending,
            issued_at=datetime.now(timezone.utc).isoformat(),
        )
        phase = result.record.phase.value if result.record else "UNKNOWN"
        self._record(
            "finalized",
            phase=phase,
            provider=(result.record.action.provider if result.record is not None and result.record.action is not None else ""),
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
        if record.phase.value != "AWAITING_HAO" or len(record.child_outcomes) < len(record.required_action_ids):
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

    def reconciliation_inspect(
        self,
        principal: MCPPrincipal,
        *,
        case_id: str,
    ) -> MCPReconciliationView:
        case, _ = self._owned_reconciliation_case(
            principal,
            case_id=case_id,
            required_scope=SCOPE_READ,
        )
        return self._reconciliation_view(case)

    async def reconciliation_inspect_with_trusted_readback(
        self,
        principal: MCPPrincipal,
        *,
        case_id: str,
    ) -> MCPReconciliationView:
        case, registration = self._owned_reconciliation_case(
            principal,
            case_id=case_id,
            required_scope=SCOPE_READ,
        )
        inspector = self._require_reconciliation_inspector()
        pending = await self._production.resume(
            registration.workflow_id,
            operational_version=registration.operational_version,
        )
        current = await self._production.current_state(pending)
        if current is None or current.action is None:
            raise ValueError("RECONCILIATION_ORIGINAL_ACTION_UNAVAILABLE")
        if current.action.action_id != case.action_id:
            raise ValueError("RECONCILIATION_ACTION_IDENTITY_MISMATCH")
        inspection = await inspector.inspect(case_id=case.case_id, proposal=current.action)
        return self._reconciliation_view(inspection.case)

    def reconciliation_resolve_after_human_confirmation(
        self,
        principal: MCPPrincipal,
        *,
        case_id: str,
        disposition: str,
        human_confirmed: bool,
    ) -> MCPReconciliationView:
        if not human_confirmed:
            raise PermissionError("HUMAN_CONFIRMATION_REQUIRED")
        case, _ = self._owned_reconciliation_case(
            principal,
            case_id=case_id,
            required_scope=SCOPE_APPROVE,
        )
        if case.phase in {ReconciliationPhase.RESOLVED, ReconciliationPhase.PERMANENT_UNRESOLVED}:
            raise ValueError("RECONCILIATION_CASE_TERMINAL")
        try:
            requested = ReconciliationDisposition(disposition.strip().upper())
        except ValueError as exc:
            raise ValueError("INVALID_RECONCILIATION_DISPOSITION") from exc
        resolved = apply_reconciliation(case, requested)
        saved = self._require_reconciliation_store().save(resolved)
        return self._reconciliation_view(saved)

    async def reconciliation_retry_with_delta(
        self,
        principal: MCPPrincipal,
        *,
        case_id: str,
        expected_state_delta: str,
        authorization_target: str = "",
        arguments: Mapping[str, str] | None = None,
    ) -> MCPSubmissionView:
        case, registration = self._owned_reconciliation_case(
            principal,
            case_id=case_id,
            required_scope=SCOPE_EXECUTE,
        )
        if case.phase != ReconciliationPhase.RESOLVED:
            raise ValueError("RECONCILIATION_NOT_RESOLVED")
        if case.disposition not in {
            ReconciliationDisposition.ADOPT_VERIFIED_STATE,
            ReconciliationDisposition.COMPENSATE_VERIFIED,
        }:
            raise ValueError("RECONCILIATION_DISPOSITION_NOT_RETRY_SAFE")

        pending = await self._production.resume(
            registration.workflow_id,
            operational_version=registration.operational_version,
        )
        current = await self._production.current_state(pending)
        if current is None or current.action is None:
            raise ValueError("RECONCILIATION_ORIGINAL_ACTION_UNAVAILABLE")
        if current.action.action_id != case.action_id:
            raise ValueError("RECONCILIATION_ACTION_IDENTITY_MISMATCH")

        new_delta = expected_state_delta.strip()
        if not new_delta or new_delta == current.action.expected_state_delta.strip():
            raise ValueError("RECONCILIATION_RETRY_REQUIRES_CHANGED_DELTA")

        trusted_target = _authorization_target_from_scope(current.action.authorization_scope)
        if authorization_target.strip() and authorization_target.strip() != trusted_target:
            raise PermissionError("RECONCILIATION_AUTHORIZATION_TARGET_MISMATCH")
        fingerprint = retry_request_fingerprint(
            case=case,
            expected_state_delta=new_delta,
            authorization_target=trusted_target,
            arguments=arguments,
        )
        reservation = self._require_reconciliation_retry_store().reserve(
            case=case,
            owner_ref=principal.subject,
            request_fingerprint=fingerprint,
        )
        state = self._operational_state.get()

        if not reservation.created:
            try:
                owned = self._run_registry.require_owned(
                    workflow_id=reservation.retry_run_id,
                    owner_subject=principal.subject,
                )
            except PermissionError as exc:
                if str(exc) != "CONTROLLED_RUN_NOT_FOUND":
                    raise
            else:
                retry_pending = await self._production.resume(
                    owned.workflow_id,
                    operational_version=owned.operational_version,
                )
                retry_current = await self._production.current_state(retry_pending)
                return MCPSubmissionView(
                    workflow_id=owned.workflow_id,
                    code="RECONCILIATION_RETRY_ALREADY_SUBMITTED",
                    mode=state.mode.value if retry_current is None else retry_current.mode.value,
                    task=state.task if retry_current is None else retry_current.task,
                    phase=RunPhase.RESOLVED.value if retry_current is None else retry_current.phase.value,
                    authorization_scope=(
                        retry_current.action.authorization_scope
                        if retry_current is not None and retry_current.action is not None
                        else ""
                    ),
                )

        intent = ModelActionIntent(
            intent_id="INTENT-RECON-" + fingerprint[:24],
            requested_capability=current.action.capability,
            binding_id=_binding_from_action_id(current.action.action_id),
            expected_state_delta=new_delta,
            authorization_target=trusted_target,
            arguments=_argument_pairs(arguments),
        )
        request = ModelIngressRequest(
            run_id=reservation.retry_run_id,
            sequence=1,
            intent=intent,
        )
        try:
            submission = await self._production.submit(state, request)
        except Exception:
            if reservation.created:
                raise
            retry_pending = await self._production.resume(
                reservation.retry_run_id,
                operational_version=state.version,
            )
            retry_current = await self._production.current_state(retry_pending)
            self._run_registry.register(
                workflow_id=reservation.retry_run_id,
                owner_subject=principal.subject,
                operational_version=state.version,
            )
            return MCPSubmissionView(
                workflow_id=reservation.retry_run_id,
                code="RECONCILIATION_RETRY_ATTACHED",
                mode=state.mode.value if retry_current is None else retry_current.mode.value,
                task=state.task if retry_current is None else retry_current.task,
                phase=RunPhase.RESOLVED.value if retry_current is None else retry_current.phase.value,
                authorization_scope=(
                    retry_current.action.authorization_scope
                    if retry_current is not None and retry_current.action is not None
                    else ""
                ),
            )

        if not submission.accepted or submission.pending is None:
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
        retry_current = await self._production.current_state(submission.pending)
        return MCPSubmissionView(
            workflow_id=submission.pending.handle.workflow_id,
            code="RECONCILIATION_RETRY_SUBMITTED",
            mode=state.mode.value if retry_current is None else retry_current.mode.value,
            task=state.task if retry_current is None else retry_current.task,
            phase=RunPhase.RESOLVED.value if retry_current is None else retry_current.phase.value,
            authorization_scope=(
                retry_current.action.authorization_scope
                if retry_current is not None and retry_current.action is not None
                else ""
            ),
        )
