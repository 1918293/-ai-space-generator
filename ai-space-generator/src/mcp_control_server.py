from __future__ import annotations

from typing import Annotated, Any, Iterable

from pydantic import BaseModel, Field

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import (
    AcceptedElicitation,
    Elicit,
    ElicitationResult,
    Resolve,
)
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.request_state import RequestStateSecurity
from mcp.types import ToolAnnotations

from .mcp_control_bridge import (
    MCPControlBridge,
    MCPPrincipal,
    SCOPE_APPROVE,
    SCOPE_EXECUTE,
    SCOPE_READ,
)


SCOPE_ACCESS = "hao:access"


class ApprovalConfirmation(BaseModel):
    confirm: bool = Field(
        description="Confirm this exact Hao System authorization decision and scope."
    )


class ToolResult(BaseModel):
    ok: bool
    code: str
    workflow_id: str = ""
    mode: str = ""
    task: str = ""
    phase: str = ""
    authorization_scope: str = ""
    authoritative: bool = False
    operational_version: int | None = None
    task_run_id: str = ""
    slot_id: str = ""
    action_id: str = ""
    child_slots: list[str] = Field(default_factory=list)
    failure_code: str = ""
    case_id: str = ""
    reconciliation_kind: str = ""
    disposition: str = ""
    effect_may_have_occurred: bool = False
    reconciliation_evidence: list[str] = Field(default_factory=list)


async def _confirm_authorization(
    workflow_id: str,
    scope: str,
    approved: bool,
    reason: str = "",
) -> ApprovalConfirmation | Elicit[ApprovalConfirmation]:
    decision = "APPROVE" if approved else "REJECT"
    return Elicit(
        (
            f"Confirm Hao System decision: {decision} workflow {workflow_id} for exact scope "
            f"'{scope}'. This may unblock a real side effect."
        ),
        ApprovalConfirmation,
    )


async def _confirm_parent_acceptance(
    task_run_id: str,
    accepted: bool,
) -> ApprovalConfirmation | Elicit[ApprovalConfirmation]:
    decision = "ACCEPT" if accepted else "REJECT"
    return Elicit(
        (
            f"Confirm Hao System parent-task decision: {decision} task {task_run_id}. "
            "This records Hao's explicit task-level acceptance after required child outcomes."
        ),
        ApprovalConfirmation,
    )


async def _confirm_reconciliation_resolution(
    case_id: str,
    disposition: str,
) -> ApprovalConfirmation | Elicit[ApprovalConfirmation]:
    return Elicit(
        (
            f"Confirm Hao System reconciliation decision for case {case_id}: "
            f"'{disposition}'. The runtime will apply this only if existing trusted evidence "
            "satisfies the requested disposition."
        ),
        ApprovalConfirmation,
    )


def _oauth_meta(tool_scope: str) -> dict[str, Any]:
    return {
        "securitySchemes": [
            {"type": "oauth2", "scopes": [SCOPE_ACCESS, tool_scope]}
        ]
    }


def build_mcp_control_server(
    bridge: MCPControlBridge,
    *,
    token_verifier: Any,
    auth_settings: Any,
    request_state_keys: Iterable[bytes] = (),
    request_state_audience: str = "hao-system-control",
) -> MCPServer:
    """Build the private authenticated MCP entrypoint for controlled execution."""
    if token_verifier is None or auth_settings is None:
        raise ValueError("MCP_OAUTH_CONFIGURATION_REQUIRED")

    global_scopes = {
        str(scope).strip()
        for scope in (getattr(auth_settings, "required_scopes", None) or [])
        if str(scope).strip()
    }
    if global_scopes != {SCOPE_ACCESS}:
        raise ValueError("MCP_GLOBAL_REQUIRED_SCOPES_MUST_EQUAL_HAO_ACCESS")

    keys = tuple(request_state_keys)
    state_security = None
    if keys:
        audience = request_state_audience.strip()
        if not audience:
            raise ValueError("MCP_REQUEST_STATE_AUDIENCE_REQUIRED")
        state_security = RequestStateSecurity(keys=list(keys), audience=audience)

    mcp = MCPServer(
        "hao-system-control",
        token_verifier=token_verifier,
        auth=auth_settings,
        request_state_security=state_security,
        instructions=(
            "Use these tools for Hao System controlled execution. Never treat a native/direct "
            "tool result as authoritative completion. Model-visible action arguments are "
            "non-authoritative and accepted only when the runtime ActionBinding allowlists them. "
            "Consequential approval must use hao_control_authorize and human elicitation. "
            "Multi-action work must use deployment-owned parent plans rather than inventing "
            "bindings or bypassing child control workflows. UNKNOWN_EFFECT ambiguity must use "
            "reconciliation; it must never be hidden by blindly replaying the prior side effect."
        ),
    )

    def principal() -> MCPPrincipal:
        access = get_access_token()
        if access is None:
            raise ToolError("AUTHENTICATION_REQUIRED")
        subject = (access.subject or "").strip()
        if not subject:
            raise ToolError("AUTHENTICATED_SUBJECT_REQUIRED")
        return MCPPrincipal(subject, frozenset(access.scopes or ()))

    @mcp.tool(
        title="Get Hao control context",
        description=(
            "Read the runtime-owned Hao System Mode, TASK, and operational version. "
            "Use this instead of inferring active state from chat text or Handoff projections."
        ),
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
        meta=_oauth_meta(SCOPE_READ),
    )
    def hao_control_context() -> ToolResult:
        try:
            view = bridge.operational_context(principal())
            return ToolResult(ok=True, code="CONTROL_CONTEXT", mode=str(view["mode"]), task=str(view["task"]), operational_version=int(view["operational_version"]))
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Submit controlled Hao action",
        description=("Submit one action intent to the Hao Execution Runtime. The server owns run IDs, Mode/TASK, policy, safety classification, provider binding, Authority and workflow state. action_arguments may contain only keys allowlisted by the selected binding."),
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
        meta=_oauth_meta(SCOPE_EXECUTE),
    )
    async def hao_control_submit(requested_capability: str, binding_id: str, expected_state_delta: str = "", authorization_target: str = "", action_arguments: dict[str, str] | None = None) -> ToolResult:
        try:
            view = await bridge.submit(principal(), requested_capability=requested_capability, binding_id=binding_id, expected_state_delta=expected_state_delta, authorization_target=authorization_target, arguments=action_arguments)
            return ToolResult(ok=bool(view.workflow_id), code=view.code, workflow_id=view.workflow_id, mode=view.mode, task=view.task, phase=view.phase, authorization_scope=view.authorization_scope)
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Get controlled run status",
        description="Read the durable state of a previously submitted Hao controlled run. The workflow must belong to the authenticated Hao identity.",
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
        meta=_oauth_meta(SCOPE_READ),
    )
    async def hao_control_status(workflow_id: str) -> ToolResult:
        try:
            view = await bridge.status(principal(), workflow_id=workflow_id)
            return ToolResult(ok=True, code=view.failure_code or "CONTROLLED_RUN_STATUS", workflow_id=view.workflow_id, mode=view.mode, task=view.task, phase=view.phase, authorization_scope=view.authorization_scope, failure_code=view.failure_code)
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Authorize controlled Hao action",
        description="Approve or reject the exact authorization scope currently requested by a durable run. This tool always requires a human elicitation confirmation before signaling Temporal.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
        meta=_oauth_meta(SCOPE_APPROVE),
    )
    async def hao_control_authorize(workflow_id: str, scope: str, approved: bool, confirmation: Annotated[ElicitationResult[ApprovalConfirmation], Resolve(_confirm_authorization)], reason: str = "") -> ToolResult:
        if not isinstance(confirmation, AcceptedElicitation) or not confirmation.data.confirm:
            return ToolResult(ok=False, code="HUMAN_CONFIRMATION_DECLINED", workflow_id=workflow_id, phase="AWAITING_HAO", authorization_scope=scope)
        try:
            view = await bridge.authorize_after_human_confirmation(principal(), workflow_id=workflow_id, scope=scope, approved=approved, reason=reason, human_confirmed=True)
            return ToolResult(ok=True, code="HAO_AUTHORIZATION_SIGNAL_ACCEPTED", workflow_id=view.workflow_id, mode=view.mode, task=view.task, phase=view.phase, authorization_scope=view.authorization_scope)
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Finalize controlled Hao run",
        description="Finalize a terminal controlled run. Only a CLOSED run with complete evidence can mint and commit the runtime-signed authoritative completion attestation.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
        meta=_oauth_meta(SCOPE_EXECUTE),
    )
    async def hao_control_finalize(workflow_id: str) -> ToolResult:
        try:
            view = await bridge.finalize(principal(), workflow_id=workflow_id)
            return ToolResult(ok=view.authoritative, code=view.code, workflow_id=view.workflow_id, phase=view.phase, authoritative=view.authoritative)
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Start Hao parent task",
        description="Start one deployment-owned multi-action parent plan for the current runtime TASK. The runtime, not the model, owns plan task, child capabilities and provider bindings.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False),
        meta=_oauth_meta(SCOPE_EXECUTE),
    )
    def hao_parent_start(plan_id: str) -> ToolResult:
        try:
            view = bridge.parent_start(principal(), plan_id=plan_id)
            return ToolResult(ok=True, code="PARENT_TASK_STARTED", task_run_id=view.task_run_id, phase=view.phase, child_slots=list(view.child_slots))
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Submit Hao parent child",
        description="Submit one configured child slot from a parent task. The plan owns capability, binding and authorization target. Returned workflow_id can be inspected or authorized through the existing control tools.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
        meta=_oauth_meta(SCOPE_EXECUTE),
    )
    async def hao_parent_submit_child(task_run_id: str, slot_id: str, expected_state_delta: str = "", action_arguments: dict[str, str] | None = None) -> ToolResult:
        try:
            view = await bridge.parent_submit_child(principal(), task_run_id=task_run_id, slot_id=slot_id, expected_state_delta=expected_state_delta, arguments=action_arguments)
            return ToolResult(ok=view.accepted, code=view.code, task_run_id=view.task_run_id, slot_id=view.slot_id, workflow_id=view.workflow_id, action_id=view.action_id, phase=view.phase, authorization_scope=view.authorization_scope)
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Refresh Hao parent task",
        description="Reconcile a durable parent task against child workflow states. UNKNOWN/UNSYNCED children are surfaced as reconciliation-required and never blindly replayed.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
        meta=_oauth_meta(SCOPE_EXECUTE),
    )
    async def hao_parent_refresh(task_run_id: str) -> ToolResult:
        try:
            view = await bridge.parent_refresh(principal(), task_run_id=task_run_id)
            return ToolResult(ok=view.phase == "CLOSED", code=view.failure_code or "PARENT_TASK_REFRESHED", task_run_id=view.task_run_id, phase=view.phase, failure_code=view.failure_code)
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Accept Hao parent task",
        description="Record Hao's explicit task-level acceptance only after the parent has all required child outcomes and is actually waiting for Hao acceptance. Human elicitation is required.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
        meta=_oauth_meta(SCOPE_APPROVE),
    )
    async def hao_parent_accept(task_run_id: str, accepted: bool, confirmation: Annotated[ElicitationResult[ApprovalConfirmation], Resolve(_confirm_parent_acceptance)]) -> ToolResult:
        if not isinstance(confirmation, AcceptedElicitation) or not confirmation.data.confirm:
            return ToolResult(ok=False, code="HUMAN_CONFIRMATION_DECLINED", task_run_id=task_run_id, phase="AWAITING_HAO")
        try:
            view = await bridge.parent_accept_after_human_confirmation(principal(), task_run_id=task_run_id, accepted=accepted, human_confirmed=True)
            return ToolResult(ok=accepted, code="HAO_PARENT_ACCEPTANCE_RECORDED", task_run_id=view.task_run_id, phase=view.phase, failure_code=view.failure_code)
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Inspect Hao reconciliation case",
        description=("Owner-check the durable ambiguity case, recover the original runtime-owned action, perform bounded direct provider readback, and persist only trusted runtime/provider evidence. The caller cannot supply provider target, expected digest, or evidence."),
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
        meta=_oauth_meta(SCOPE_READ),
    )
    async def hao_reconciliation_inspect(case_id: str) -> ToolResult:
        try:
            view = await bridge.reconciliation_inspect_with_trusted_readback(principal(), case_id=case_id)
            return ToolResult(ok=True, code=view.resolution_code or "RECONCILIATION_CASE", case_id=view.case_id, workflow_id=view.run_id, action_id=view.action_id, reconciliation_kind=view.kind, phase=view.phase, disposition=view.disposition, effect_may_have_occurred=view.effect_may_have_occurred, reconciliation_evidence=list(view.evidence))
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Resolve Hao reconciliation case",
        description="Apply a reconciliation disposition only against trusted evidence already present in the durable case. This tool cannot upload or manufacture verification evidence and requires Hao human confirmation.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
        meta=_oauth_meta(SCOPE_APPROVE),
    )
    async def hao_reconciliation_resolve(case_id: str, disposition: str, confirmation: Annotated[ElicitationResult[ApprovalConfirmation], Resolve(_confirm_reconciliation_resolution)]) -> ToolResult:
        if not isinstance(confirmation, AcceptedElicitation) or not confirmation.data.confirm:
            return ToolResult(ok=False, code="HUMAN_CONFIRMATION_DECLINED", case_id=case_id)
        try:
            view = bridge.reconciliation_resolve_after_human_confirmation(principal(), case_id=case_id, disposition=disposition, human_confirmed=True)
            return ToolResult(ok=view.phase != "OPEN", code=view.resolution_code or "RECONCILIATION_UPDATED", case_id=view.case_id, workflow_id=view.run_id, action_id=view.action_id, reconciliation_kind=view.kind, phase=view.phase, disposition=view.disposition, effect_may_have_occurred=view.effect_may_have_occurred, reconciliation_evidence=list(view.evidence))
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Retry reconciled Hao action with changed delta",
        description=("Create or attach the one durable retry run reserved for this resolved ambiguity case. The runtime reuses the original trusted capability, provider binding and authorization target; the caller can change only the expected-state delta and allowlisted action arguments."),
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
        meta=_oauth_meta(SCOPE_EXECUTE),
    )
    async def hao_reconciliation_retry_with_delta(case_id: str, expected_state_delta: str, action_arguments: dict[str, str] | None = None) -> ToolResult:
        try:
            view = await bridge.reconciliation_retry_with_delta(principal(), case_id=case_id, expected_state_delta=expected_state_delta, arguments=action_arguments)
            return ToolResult(ok=bool(view.workflow_id), code=view.code, workflow_id=view.workflow_id, mode=view.mode, task=view.task, phase=view.phase, authorization_scope=view.authorization_scope, case_id=case_id)
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return mcp
