from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Mapping, Protocol
import uuid

from .action_catalog import ModelActionIntent
from .control_gateway import ModelIngressRequest
from .execution_control import RunPhase
from .operational_state import ActiveOperationalState
from .production_execution import PendingControlledRun, ProductionExecutionService


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


def _argument_pairs(arguments: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not arguments:
        return ()
    return tuple((str(key), str(value)) for key, value in arguments.items())


class MCPControlBridge:
    """Stateless authenticated bridge from MCP tools into ProductionExecutionService."""

    def __init__(
        self,
        *,
        production: ProductionExecutionService,
        operational_state: OperationalStateReader,
        run_registry: MCPRunRegistry,
        identity_policy: HaoMCPIdentityPolicy,
    ) -> None:
        self._production = production
        self._operational_state = operational_state
        self._run_registry = run_registry
        self._identity_policy = identity_policy

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
        else:
            phase = current.phase.value
            authorization_scope = (
                current.action.authorization_scope if current.action is not None else ""
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

    async def status(self, principal: MCPPrincipal, *, workflow_id: str) -> MCPStatusView:
        pending = await self._pending_for(
            principal,
            workflow_id,
            required_scope=SCOPE_READ,
        )
        current = await self._production.current_state(pending)
        if current is None:
            return MCPStatusView(workflow_id, "", "", "UNKNOWN")
        action_scope = current.action.authorization_scope if current.action else ""
        return MCPStatusView(
            workflow_id=workflow_id,
            mode=current.mode.value,
            task=current.task,
            phase=current.phase.value,
            failure_stage=current.failure_stage.value if current.failure_stage else "",
            failure_code=current.failure_code,
            authorization_scope=action_scope,
        )

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
        return await self.status(principal, workflow_id=workflow_id)

    async def finalize(self, principal: MCPPrincipal, *, workflow_id: str) -> MCPFinalizeView:
        pending = await self._pending_for(
            principal,
            workflow_id,
            required_scope=SCOPE_EXECUTE,
        )
        current = await self._production.current_state(pending)
        if current is None:
            return MCPFinalizeView(workflow_id, False, "CONTROLLED_RUN_STATE_UNKNOWN", "UNKNOWN")
        if current.phase not in {
            RunPhase.CLOSED,
            RunPhase.BLOCKED,
            RunPhase.FAILED,
            RunPhase.UNSYNCED,
        }:
            return MCPFinalizeView(
                workflow_id,
                False,
                "CONTROLLED_RUN_NOT_TERMINAL:" + current.phase.value,
                current.phase.value,
            )
        result = await self._production.finalize(
            pending,
            issued_at=datetime.now(timezone.utc).isoformat(),
        )
        phase = result.record.phase.value if result.record else "UNKNOWN"
        return MCPFinalizeView(workflow_id, result.authoritative, result.code, phase)
