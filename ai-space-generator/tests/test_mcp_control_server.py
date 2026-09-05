import asyncio

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from src.mcp_control_bridge import (
    HaoMCPIdentityPolicy,
    SCOPE_EXECUTE,
    SCOPE_READ,
)
from src.mcp_control_server import SCOPE_ACCESS, build_mcp_control_server
from src.mcp_http import build_mcp_http_app


EXPECTED_HAO_SUBJECT = "hao-user"
PUBLIC_HOST = "hao.example.com"
PUBLIC_URL = "https://hao.example.com/mcp"


class StaticVerifier(TokenVerifier):
    async def verify_token(self, token: str):
        tokens = {
            "hao-read-token": AccessToken(
                token="hao-read-token",
                client_id="chatgpt-test",
                scopes=[SCOPE_ACCESS, SCOPE_READ],
                subject=EXPECTED_HAO_SUBJECT,
            ),
            "wrong-subject-token": AccessToken(
                token="wrong-subject-token",
                client_id="chatgpt-test",
                scopes=[SCOPE_ACCESS, SCOPE_READ],
                subject="not-hao",
            ),
        }
        return tokens.get(token)


class RejectingVerifier(TokenVerifier):
    async def verify_token(self, token: str):
        return None


class FixtureBridge:
    def __init__(self):
        self.policy = HaoMCPIdentityPolicy(EXPECTED_HAO_SUBJECT)
        self.context_calls = 0
        self.submit_calls = 0

    def operational_context(self, principal):
        self.policy.require(principal, SCOPE_READ)
        self.context_calls += 1
        return {
            "mode": "EXP",
            "task": "Runtime v2 OAuth E2E",
            "operational_version": 9,
        }

    async def submit(
        self,
        principal,
        *,
        requested_capability,
        binding_id,
        expected_state_delta="",
        authorization_target="",
        arguments=None,
    ):
        self.policy.require(principal, SCOPE_EXECUTE)
        self.submit_calls += 1
        raise AssertionError("execute-capable fixture path is not used in this test")


class UnusedBridge:
    pass


def settings(*, required_scopes=None):
    return AuthSettings(
        issuer_url=AnyHttpUrl("https://auth.example.com"),
        resource_server_url=AnyHttpUrl(PUBLIC_URL),
        required_scopes=list(required_scopes or [SCOPE_ACCESS]),
    )


def server(*, verifier=None, bridge=None):
    return build_mcp_control_server(
        bridge or UnusedBridge(),
        token_verifier=verifier or RejectingVerifier(),
        auth_settings=settings(),
    )


def http_app(mcp):
    return build_mcp_http_app(
        mcp,
        allowed_hosts=[PUBLIC_HOST, PUBLIC_HOST + ":*"],
    )


def test_mcp_server_fails_closed_without_oauth_configuration():
    import pytest

    with pytest.raises(ValueError, match="MCP_OAUTH_CONFIGURATION_REQUIRED"):
        build_mcp_control_server(UnusedBridge(), token_verifier=None, auth_settings=None)


def test_mcp_server_rejects_overbroad_global_scope_configuration():
    import pytest

    with pytest.raises(ValueError, match="MCP_GLOBAL_REQUIRED_SCOPES_MUST_EQUAL_HAO_ACCESS"):
        build_mcp_control_server(
            UnusedBridge(),
            token_verifier=RejectingVerifier(),
            auth_settings=settings(required_scopes=[SCOPE_READ, SCOPE_EXECUTE, "hao:approve"]),
        )


def test_mcp_http_app_requires_explicit_non_wildcard_host_allowlist():
    import pytest

    mcp = server()
    with pytest.raises(ValueError, match="MCP_ALLOWED_HOSTS_REQUIRED"):
        build_mcp_http_app(mcp, allowed_hosts=[])
    with pytest.raises(ValueError, match="MCP_ALLOWED_HOSTS_WILDCARD_NOT_ALLOWED"):
        build_mcp_http_app(mcp, allowed_hosts=["*"])


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
                "hao_parent_start",
                "hao_parent_submit_child",
                "hao_parent_refresh",
                "hao_parent_accept",
                "hao_reconciliation_inspect",
                "hao_reconciliation_resolve",
                "hao_reconciliation_retry_with_delta",
            }
            assert tools["hao_control_context"].annotations.read_only_hint is True
            assert tools["hao_control_status"].annotations.read_only_hint is True
            assert tools["hao_control_submit"].annotations.read_only_hint is False
            assert tools["hao_control_authorize"].annotations.destructive_hint is True
            assert tools["hao_control_authorize"].annotations.open_world_hint is True
            assert tools["hao_control_finalize"].annotations.idempotent_hint is True
            assert tools["hao_parent_start"].annotations.idempotent_hint is False
            assert tools["hao_parent_submit_child"].annotations.idempotent_hint is True
            assert tools["hao_parent_refresh"].annotations.idempotent_hint is True
            assert tools["hao_parent_accept"].annotations.idempotent_hint is True
            assert tools["hao_reconciliation_inspect"].annotations.read_only_hint is True
            assert tools["hao_reconciliation_resolve"].annotations.idempotent_hint is True
            assert tools["hao_reconciliation_retry_with_delta"].annotations.idempotent_hint is False

            submit_fields = set(tools["hao_control_submit"].input_schema["properties"])
            assert "mode" not in submit_fields
            assert "task" not in submit_fields
            assert "externality" not in submit_fields
            assert "authorization_scope" not in submit_fields
            assert "run_id" not in submit_fields
            assert "spreadsheet_id" not in submit_fields
            assert "range_a1" not in submit_fields
            assert submit_fields == {
                "requested_capability",
                "binding_id",
                "expected_state_delta",
                "authorization_target",
                "action_arguments",
            }

            authorize_fields = set(tools["hao_control_authorize"].input_schema["properties"])
            assert "confirmation" not in authorize_fields
            assert authorize_fields == {"workflow_id", "scope", "approved", "reason"}

            parent_start_fields = set(tools["hao_parent_start"].input_schema["properties"])
            assert parent_start_fields == {"plan_id"}
            parent_child_fields = set(
                tools["hao_parent_submit_child"].input_schema["properties"]
            )
            assert parent_child_fields == {
                "task_run_id",
                "slot_id",
                "expected_state_delta",
                "action_arguments",
            }
            parent_accept_fields = set(
                tools["hao_parent_accept"].input_schema["properties"]
            )
            assert "confirmation" not in parent_accept_fields
            assert parent_accept_fields == {"task_run_id", "accepted"}

            reconciliation_inspect_fields = set(
                tools["hao_reconciliation_inspect"].input_schema["properties"]
            )
            assert reconciliation_inspect_fields == {"case_id"}
            reconciliation_resolve_fields = set(
                tools["hao_reconciliation_resolve"].input_schema["properties"]
            )
            assert "confirmation" not in reconciliation_resolve_fields
            assert reconciliation_resolve_fields == {"case_id", "disposition"}
            reconciliation_retry_fields = set(
                tools["hao_reconciliation_retry_with_delta"].input_schema["properties"]
            )
            assert "binding_id" not in reconciliation_retry_fields
            assert "requested_capability" not in reconciliation_retry_fields
            assert reconciliation_retry_fields == {
                "case_id",
                "expected_state_delta",
                "authorization_target",
                "action_arguments",
            }

    asyncio.run(scenario())


def test_streamable_http_request_without_token_is_rejected_before_tool_execution():
    async def scenario():
        mcp = server()
        transport = httpx2.ASGITransport(app=http_app(mcp))
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
        transport = httpx2.ASGITransport(app=http_app(mcp))
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


def test_streamable_http_publishes_base_protected_resource_scope():
    async def scenario():
        mcp = server()
        transport = httpx2.ASGITransport(app=http_app(mcp))
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="https://hao.example.com",
        ) as http_client:
            response = await http_client.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200
            body = response.json()
            assert body["resource"] == PUBLIC_URL
            assert body["authorization_servers"] == ["https://auth.example.com/"]
            assert body["scopes_supported"] == [SCOPE_ACCESS]

    asyncio.run(scenario())


def test_valid_http_token_reaches_tool_with_verified_hao_identity():
    async def scenario():
        bridge = FixtureBridge()
        mcp = server(verifier=StaticVerifier(), bridge=bridge)
        transport = httpx2.ASGITransport(app=http_app(mcp))
        headers = {"Authorization": "Bearer hao-read-token"}

        async with mcp.session_manager.run():
            async with (
                httpx2.AsyncClient(
                    transport=transport,
                    base_url=PUBLIC_URL,
                    headers=headers,
                    follow_redirects=True,
                ) as http_client,
                Client(streamable_http_client(PUBLIC_URL, http_client=http_client)) as client,
            ):
                result = await client.call_tool("hao_control_context", {})
                assert result.is_error is False
                assert result.structured_content["mode"] == "EXP"
                assert result.structured_content["task"] == "Runtime v2 OAuth E2E"
                assert result.structured_content["operational_version"] == 9

        assert bridge.context_calls == 1

    asyncio.run(scenario())


def test_read_scoped_http_token_cannot_submit_controlled_action():
    async def scenario():
        bridge = FixtureBridge()
        mcp = server(verifier=StaticVerifier(), bridge=bridge)
        transport = httpx2.ASGITransport(app=http_app(mcp))
        headers = {"Authorization": "Bearer hao-read-token"}

        async with mcp.session_manager.run():
            async with (
                httpx2.AsyncClient(
                    transport=transport,
                    base_url=PUBLIC_URL,
                    headers=headers,
                    follow_redirects=True,
                ) as http_client,
                Client(streamable_http_client(PUBLIC_URL, http_client=http_client)) as client,
            ):
                result = await client.call_tool(
                    "hao_control_submit",
                    {
                        "requested_capability": "drive.write",
                        "binding_id": "drive-write",
                        "expected_state_delta": "test",
                    },
                )
                assert result.is_error is True
                assert "MISSING_SCOPE:hao:execute" in result.content[0].text

        assert bridge.submit_calls == 0

    asyncio.run(scenario())


def test_wrong_authenticated_subject_is_rejected_by_application_policy():
    async def scenario():
        bridge = FixtureBridge()
        mcp = server(verifier=StaticVerifier(), bridge=bridge)
        transport = httpx2.ASGITransport(app=http_app(mcp))
        headers = {"Authorization": "Bearer wrong-subject-token"}

        async with mcp.session_manager.run():
            async with (
                httpx2.AsyncClient(
                    transport=transport,
                    base_url=PUBLIC_URL,
                    headers=headers,
                    follow_redirects=True,
                ) as http_client,
                Client(streamable_http_client(PUBLIC_URL, http_client=http_client)) as client,
            ):
                result = await client.call_tool("hao_control_context", {})
                assert result.is_error is True
                assert "HAO_IDENTITY_REQUIRED" in result.content[0].text

        assert bridge.context_calls == 0

    asyncio.run(scenario())
