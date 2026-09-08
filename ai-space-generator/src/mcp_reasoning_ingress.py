from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol
import uuid

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from .mcp_control_bridge import (
    HaoMCPIdentityPolicy,
    MCPPrincipal,
    SCOPE_EXECUTE,
)


class ContextReasoningConsumer(Protocol):
    def prepare_user_turn(
        self,
        user_text: str,
        *,
        run_id: str,
        event_id: str = "",
        sequence: int = 1,
    ) -> Any: ...


@dataclass(frozen=True)
class MCPReasoningView:
    code: str
    reasoning_run_id: str
    action_selected: bool
    decision_id: str = ""
    action_id: str = ""
    authorization_scope: str = ""


class MCPReasoningToolResult(BaseModel):
    ok: bool
    code: str
    reasoning_run_id: str = ""
    action_selected: bool = False
    decision_id: str = ""
    action_id: str = ""
    authorization_scope: str = ""


class AuthenticatedMCPReasoningIngress:
    """OAuth-principal gate for one raw Hao user turn before intent formation.

    The caller supplies only the raw user text. Runtime-owned Mode/TASK, canonical
    context, model intent, provider/action metadata and run/event identities are
    deliberately not accepted at this boundary. The adapter performs no provider
    mutation; it asks the existing context-bound consumer to prepare one controlled
    action projection after the normal first-model admission path.
    """

    def __init__(
        self,
        *,
        consumer: ContextReasoningConsumer,
        identity_policy: HaoMCPIdentityPolicy,
    ) -> None:
        self._consumer = consumer
        self._identity_policy = identity_policy

    def prepare_user_turn(
        self,
        principal: MCPPrincipal,
        *,
        user_text: str,
    ) -> MCPReasoningView:
        self._identity_policy.require(principal, SCOPE_EXECUTE)
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("USER_TEXT_REQUIRED")

        reasoning_run_id = "RUN-MCP-REASONING-" + uuid.uuid4().hex
        event_id = "EVENT-MCP-REASONING-" + uuid.uuid4().hex
        result = self._consumer.prepare_user_turn(
            user_text,
            run_id=reasoning_run_id,
            event_id=event_id,
            sequence=1,
        )
        return MCPReasoningView(
            code=str(result.code),
            reasoning_run_id=reasoning_run_id,
            action_selected=bool(result.action_selected),
            decision_id=str(result.decision_id),
            action_id=str(result.action_id),
            authorization_scope=str(result.authorization_scope),
        )


def register_authenticated_reasoning_tool(
    mcp: MCPServer,
    *,
    ingress: AuthenticatedMCPReasoningIngress,
    principal_provider: Callable[[], MCPPrincipal],
    oauth_meta: Callable[[str], dict[str, Any]],
) -> None:
    """Mount one narrow OAuth-authenticated raw-user reasoning tool."""

    @mcp.tool(
        name="hao_reasoning_prepare_user_turn",
        title="Prepare Hao reasoning from raw user text",
        description=(
            "Force one authenticated raw Hao user turn through Runtime-owned Mode/TASK, "
            "canonical context admission, the first-model boundary, and the existing "
            "ControlPlane before any controlled action can be selected. The caller supplies "
            "only user_text; run identity, context, model intent and action metadata are "
            "runtime-owned. This tool prepares a controlled action projection and performs "
            "no provider mutation."
        ),
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
        meta=oauth_meta(SCOPE_EXECUTE),
    )
    def hao_reasoning_prepare_user_turn(user_text: str) -> MCPReasoningToolResult:
        try:
            view = ingress.prepare_user_turn(
                principal_provider(),
                user_text=user_text,
            )
            return MCPReasoningToolResult(
                ok=True,
                code=view.code,
                reasoning_run_id=view.reasoning_run_id,
                action_selected=view.action_selected,
                decision_id=view.decision_id,
                action_id=view.action_id,
                authorization_scope=view.authorization_scope,
            )
        except (PermissionError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
