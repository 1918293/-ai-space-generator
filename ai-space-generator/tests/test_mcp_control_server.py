import asyncio

from mcp import Client
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from src.mcp_control_server import build_mcp_control_server


class RejectingVerifier(TokenVerifier):
    async def verify_token(self, token: str):
        return None


class UnusedBridge:
    pass


def server():
    return build_mcp_control_server(
        UnusedBridge(),
        token_verifier=RejectingVerifier(),
        auth_settings=AuthSettings(
            issuer_url=AnyHttpUrl("https://auth.example.com"),
            resource_server_url=AnyHttpUrl("https://hao.example.com/mcp"),
            required_scopes=["hao:read", "hao:execute", "hao:approve"],
        ),
    )


def test_mcp_server_fails_closed_without_oauth_configuration():
    import pytest

    with pytest.raises(ValueError, match="MCP_OAUTH_CONFIGURATION_REQUIRED"):
        build_mcp_control_server(UnusedBridge(), token_verifier=None, auth_settings=None)


def test_mcp_tool_surface_is_focused_and_annotations_match_effects():
    async def scenario():
        async with Client(server()) as client:
            result = await client.list_tools()
            tools = {tool.name: tool for tool in result.tools}
            assert set(tools) == {
                "hao_control_context",
                "hao_control_submit",
                "hao_control_status",
                "hao_control_authorize",
                "hao_control_finalize",
            }
            assert tools["hao_control_context"].annotations.read_only_hint is True
            assert tools["hao_control_status"].annotations.read_only_hint is True
            assert tools["hao_control_submit"].annotations.read_only_hint is False
            assert tools["hao_control_authorize"].annotations.destructive_hint is True
            assert tools["hao_control_authorize"].annotations.open_world_hint is True
            assert tools["hao_control_finalize"].annotations.idempotent_hint is True

            submit_fields = set(tools["hao_control_submit"].input_schema["properties"])
            assert "mode" not in submit_fields
            assert "task" not in submit_fields
            assert "externality" not in submit_fields
            assert "authorization_scope" not in submit_fields
            assert "run_id" not in submit_fields
            assert submit_fields == {
                "requested_capability",
                "binding_id",
                "expected_state_delta",
                "authorization_target",
            }

            authorize_fields = set(tools["hao_control_authorize"].input_schema["properties"])
            assert "confirmation" not in authorize_fields
            assert authorize_fields == {"workflow_id", "scope", "approved", "reason"}

    asyncio.run(scenario())


def test_in_memory_tool_call_without_authenticated_request_context_fails_closed():
    async def scenario():
        async with Client(server()) as client:
            result = await client.call_tool("hao_control_context", {})
            assert result.is_error is True
            assert "AUTHENTICATION_REQUIRED" in result.content[0].text

    asyncio.run(scenario())
