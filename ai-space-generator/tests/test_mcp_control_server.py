import asyncio

import httpx2
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
        # In-memory Client intentionally bypasses HTTP OAuth. Use it only to
        # inspect the registered tool surface and model-visible schemas.
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


def test_streamable_http_request_without_token_is_rejected_before_tool_execution():
    async def scenario():
        mcp = server()
        transport = httpx2.ASGITransport(app=mcp.streamable_http_app())
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://hao.example.com",
        ) as http_client:
            response = await http_client.post("/mcp", json={})
            assert response.status_code == 401
            assert response.json()["error"] == "invalid_token"
            assert "resource_metadata=" in response.headers["www-authenticate"]

    asyncio.run(scenario())


def test_streamable_http_rejected_bearer_token_is_401():
    async def scenario():
        mcp = server()
        transport = httpx2.ASGITransport(app=mcp.streamable_http_app())
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://hao.example.com",
        ) as http_client:
            response = await http_client.post(
                "/mcp",
                json={},
                headers={"Authorization": "Bearer invalid-token"},
            )
            assert response.status_code == 401
            assert response.json()["error"] == "invalid_token"

    asyncio.run(scenario())


def test_streamable_http_publishes_protected_resource_metadata():
    async def scenario():
        mcp = server()
        transport = httpx2.ASGITransport(app=mcp.streamable_http_app())
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://hao.example.com",
        ) as http_client:
            response = await http_client.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200
            body = response.json()
            assert body["resource"] == "https://hao.example.com/mcp"
            assert body["authorization_servers"] == ["https://auth.example.com/"]
            assert set(body["scopes_supported"]) == {"hao:read", "hao:execute", "hao:approve"}

    asyncio.run(scenario())
