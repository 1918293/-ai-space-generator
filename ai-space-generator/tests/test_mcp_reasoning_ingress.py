import asyncio
from types import SimpleNamespace

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
from src.mcp_control_server import SCOPE_ACCESS
from src.mcp_http import build_mcp_http_app
from src.mcp_reasoning_ingress import (
    AuthenticatedMCPReasoningIngress,
    build_reasoning_enabled_mcp_control_server,
)


EXPECTED_HAO_SUBJECT = "hao-user"
PUBLIC_HOST = "hao.example.com"
PUBLIC_URL = "https://hao.example.com/mcp"


class StaticVerifier(TokenVerifier):
    async def verify_token(self, token: str):
        tokens = {
            "hao-execute-token": AccessToken(
                token="hao-execute-token",
                client_id="chatgpt-test",
                scopes=[SCOPE_ACCESS, SCOPE_EXECUTE],
                subject=EXPECTED_HAO_SUBJECT,
            ),
            "hao-read-token": AccessToken(
                token="hao-read-token",
                client_id="chatgpt-test",
                scopes=[SCOPE_ACCESS, SCOPE_READ],
                subject=EXPECTED_HAO_SUBJECT,
            ),
            "wrong-subject-execute-token": AccessToken(
                token="wrong-subject-execute-token",
                client_id="chatgpt-test",
                scopes=[SCOPE_ACCESS, SCOPE_EXECUTE],
                subject="not-hao",
            ),
        }
        return tokens.get(token)


class UnusedBridge:
    pass


class FixtureConsumer:
    def __init__(self):
        self.calls = []

    def prepare_user_turn(
        self,
        user_text,
        *,
        run_id,
        event_id="",
        sequence=1,
    ):
        self.calls.append((user_text, run_id, event_id, sequence))
        return SimpleNamespace(
            code="CONTEXT_REASONING_PREPARED",
            action_selected=True,
            decision_id="DECISION:fixture",
            action_id="RUN-FIXTURE:A0001:formal.intake.append",
            authorization_scope="HAO_DRIVE_WRITE:Hao System Intake",
        )


def settings():
    return AuthSettings(
        issuer_url=AnyHttpUrl("https://auth.example.com"),
        resource_server_url=AnyHttpUrl(PUBLIC_URL),
        required_scopes=[SCOPE_ACCESS],
    )


def server(consumer):
    ingress = AuthenticatedMCPReasoningIngress(
        consumer=consumer,
        identity_policy=HaoMCPIdentityPolicy(EXPECTED_HAO_SUBJECT),
    )
    return build_reasoning_enabled_mcp_control_server(
        UnusedBridge(),
        ingress=ingress,
        token_verifier=StaticVerifier(),
        auth_settings=settings(),
    )


def http_app(mcp):
    return build_mcp_http_app(
        mcp,
        allowed_hosts=[PUBLIC_HOST, PUBLIC_HOST + ":*"],
    )


async def call_tool_with_token(mcp, token, arguments):
    transport = httpx2.ASGITransport(app=http_app(mcp))
    headers = {"Authorization": f"Bearer {token}"}
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
            return await client.call_tool("hao_reasoning_prepare_user_turn", arguments)


def test_reasoning_tool_schema_accepts_only_raw_user_text():
    async def scenario():
        consumer = FixtureConsumer()
        async with Client(server(consumer)) as client:
            result = await client.list_tools()
            tools = {tool.name: tool for tool in result.tools}
            tool = tools["hao_reasoning_prepare_user_turn"]
            assert set(tool.input_schema["properties"]) == {"user_text"}
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert tool.annotations.idempotent_hint is False
            assert tool.annotations.open_world_hint is True
            forbidden = {
                "mode",
                "task",
                "run_id",
                "event_id",
                "authority_refs",
                "receipt",
                "model_intent",
                "binding_id",
                "provider",
                "action_id",
                "authorization_scope",
            }
            assert forbidden.isdisjoint(tool.input_schema["properties"])

    asyncio.run(scenario())


def test_execute_scoped_hao_token_forces_raw_turn_through_consumer_with_server_ids():
    async def scenario():
        consumer = FixtureConsumer()
        mcp = server(consumer)
        raw_text = "  Auto > continue Runtime v2 exactly as typed  "
        result = await call_tool_with_token(
            mcp,
            "hao-execute-token",
            {"user_text": raw_text},
        )

        assert result.is_error is False
        payload = result.structured_content
        assert payload["code"] == "CONTEXT_REASONING_PREPARED"
        assert payload["action_selected"] is True
        assert payload["reasoning_run_id"].startswith("RUN-MCP-REASONING-")
        assert payload["decision_id"] == "DECISION:fixture"
        assert len(consumer.calls) == 1
        user_text, run_id, event_id, sequence = consumer.calls[0]
        assert user_text == raw_text
        assert run_id == payload["reasoning_run_id"]
        assert run_id.startswith("RUN-MCP-REASONING-")
        assert event_id.startswith("EVENT-MCP-REASONING-")
        assert sequence == 1

    asyncio.run(scenario())


def test_read_only_token_is_denied_before_reasoning_consumer():
    async def scenario():
        consumer = FixtureConsumer()
        result = await call_tool_with_token(
            server(consumer),
            "hao-read-token",
            {"user_text": "Auto > continue"},
        )
        assert result.is_error is True
        assert "MISSING_SCOPE:hao:execute" in result.content[0].text
        assert consumer.calls == []

    asyncio.run(scenario())


def test_wrong_subject_is_denied_before_reasoning_consumer():
    async def scenario():
        consumer = FixtureConsumer()
        result = await call_tool_with_token(
            server(consumer),
            "wrong-subject-execute-token",
            {"user_text": "Auto > continue"},
        )
        assert result.is_error is True
        assert "HAO_IDENTITY_REQUIRED" in result.content[0].text
        assert consumer.calls == []

    asyncio.run(scenario())


def test_blank_user_text_fails_before_consumer():
    async def scenario():
        consumer = FixtureConsumer()
        result = await call_tool_with_token(
            server(consumer),
            "hao-execute-token",
            {"user_text": "   "},
        )
        assert result.is_error is True
        assert "USER_TEXT_REQUIRED" in result.content[0].text
        assert consumer.calls == []

    asyncio.run(scenario())
