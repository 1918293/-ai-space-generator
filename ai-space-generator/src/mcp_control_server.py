from __future__ import annotations

from typing import Annotated, Any

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

    mcp = MCPServer(
        "hao-system-control",
        token_verifier=token_verifier,
        auth=auth_settings,
        instructions=(
            "Use these tools for Hao System controlled execution. Never treat a native/direct "
            "tool result as authoritative completion. Model-visible action arguments are "
            "non-authoritative and accepted only when the runtime ActionBinding allowlists them. "
            "Consequential approval must use hao_control_authorize and human elicitation."
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
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        meta=_oauth_meta(SCOPE_READ),
    )
    def hao_control_context() -> ToolResult:
        try:
            view = bridge.operational_context(principal())
            return ToolResult(
                ok=True,
                code="CONTROL_CONTEXT",
                mode=str(view["mode"]),
                task=str(view["task"]),
                operational_version=int(view["operational_version"]),
            )
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Submit controlled Hao action",
        description=(
            "Submit one action intent to the Hao Execution Runtime. The server owns run IDs, "
            "Mode/TASK, policy, safety classification, provider binding, Authority and workflow "
            "state. action_arguments may contain only keys allowlisted by the selected binding."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
        meta=_oauth_meta(SCOPE_EXECUTE),
    )
    async def hao_control_submit(
        requested_capability: str,
        binding_id: str,
        expected_state_delta: str = "",
        authorization_target: str = "",
        action_arguments: dict[str, str] | None = None,
    ) -> ToolResult:
        try:
            view = await bridge.submit(
                principal(),
                requested_capability=requested_capability,
                binding_id=binding_id,
                expected_state_delta=expected_state_delta,
                authorization_target=authorization_target,
                arguments=action_arguments,
            )
            return ToolResult(
                ok=bool(view.workflow_id),
                code=view.code,
                workflow_id=view.workflow_id,
                mode=view.mode,
                task=view.task,
                phase=view.phase,
                authorization_scope=view.authorization_scope,
            )
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Get controlled run status",
        description=(
            "Read the durable state of a previously submitted Hao controlled run. "
            "The workflow must belong to the authenticated Hao identity."
        ),
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        meta=_oauth_meta(SCOPE_READ),
    )
    async def hao_control_status(workflow_id: str) -> ToolResult:
        try:
            view = await bridge.status(principal(), workflow_id=workflow_id)
            return ToolResult(
                ok=True,
                code=view.failure_code or "CONTROLLED_RUN_STATUS",
                workflow_id=view.workflow_id,
                mode=view.mode,
                task=view.task,
                phase=view.phase,
                authorization_scope=view.authorization_scope,
            )
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Authorize controlled Hao action",
        description=(
            "Approve or reject the exact authorization scope currently requested by a durable run. "
            "This tool always requires a human elicitation confirmation before signaling Temporal."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=True,
        ),
        meta=_oauth_meta(SCOPE_APPROVE),
    )
    async def hao_control_authorize(
        workflow_id: str,
        scope: str,
        approved: bool,
        confirmation: Annotated[
            ElicitationResult[ApprovalConfirmation],
            Resolve(_confirm_authorization),
        ],
        reason: str = "",
    ) -> ToolResult:
        if not isinstance(confirmation, AcceptedElicitation) or not confirmation.data.confirm:
            return ToolResult(
                ok=False,
                code="HUMAN_CONFIRMATION_DECLINED",
                workflow_id=workflow_id,
                phase="AWAITING_HAO",
                authorization_scope=scope,
            )
        try:
            view = await bridge.authorize_after_human_confirmation(
                principal(),
                workflow_id=workflow_id,
                scope=scope,
                approved=approved,
                reason=reason,
                human_confirmed=True,
            )
            return ToolResult(
                ok=True,
                code="HAO_AUTHORIZATION_SIGNAL_ACCEPTED",
                workflow_id=view.workflow_id,
                mode=view.mode,
                task=view.task,
                phase=view.phase,
                authorization_scope=view.authorization_scope,
            )
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Finalize controlled Hao run",
        description=(
            "Finalize a terminal controlled run. Only a CLOSED run with complete evidence can mint "
            "and commit the runtime-signed authoritative completion attestation."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        meta=_oauth_meta(SCOPE_EXECUTE),
    )
    async def hao_control_finalize(workflow_id: str) -> ToolResult:
        try:
            view = await bridge.finalize(principal(), workflow_id=workflow_id)
            return ToolResult(
                ok=view.authoritative,
                code=view.code,
                workflow_id=view.workflow_id,
                phase=view.phase,
                authoritative=view.authoritative,
            )
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return mcp
