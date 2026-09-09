import asyncio
from contextlib import contextmanager
import socket
import threading
import time
from types import SimpleNamespace

import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from src.execution_control import Mode
from src.mcp_control_bridge import (
    HaoMCPIdentityPolicy,
    MCPControlBridge,
    SQLiteMCPRunRegistry,
    SCOPE_APPROVE,
    SCOPE_EXECUTE,
    SCOPE_READ,
)
from src.mcp_control_server import SCOPE_ACCESS, build_mcp_control_server
from src.mcp_http import build_mcp_http_app
from src.oauth_verifier import JWKSAccessTokenVerifier
from src.operational_state import SQLiteOperationalStateStore


EXPECTED_SUBJECT = "hao-user"


def _bound_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


@contextmanager
def _running_uvicorn(app, sock):
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", lifespan="on", access_log=False)
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [sock]},
        daemon=True,
    )
    thread.start()
    deadline = time.time() + 5
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("UVICORN_TEST_SERVER_DID_NOT_START")
    try:
        yield server
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("UVICORN_TEST_SERVER_DID_NOT_STOP")


class UnusedProduction:
    pass


class OAuthNetworkHarness:
    def __init__(self, tmp_path, *, jwks_status=200):
        self.tmp_path = tmp_path
        self.jwks_status = jwks_status
        self.jwks_requests = 0
        self.bridge = None
        self.mcp = None
        self.mcp_url = ""
        self.issuer = ""
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(
            self.private_key.public_key(), as_dict=True
        )
        jwk.update({"kid": "lane-a-key-1", "use": "sig", "alg": "RS256"})
        self.jwk = jwk

    def token(self, scopes, **overrides):
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": EXPECTED_SUBJECT,
            "aud": self.mcp_url,
            "iat": now,
            "exp": now + 300,
            "azp": "runtime-v2-lane-a",
            "scope": " ".join(scopes),
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "lane-a-key-1"},
        )

    @contextmanager
    def run(self):
        jwks_sock = _bound_socket()
        jwks_port = jwks_sock.getsockname()[1]
        self.issuer = f"http://127.0.0.1:{jwks_port}/"

        async def jwks_endpoint(request):
            del request
            self.jwks_requests += 1
            if self.jwks_status != 200:
                return JSONResponse({"error": "jwks unavailable"}, status_code=self.jwks_status)
            return JSONResponse({"keys": [self.jwk]})

        jwks_app = Starlette(
            routes=[Route("/.well-known/jwks.json", jwks_endpoint, methods=["GET"])]
        )

        with _running_uvicorn(jwks_app, jwks_sock):
            mcp_sock = _bound_socket()
            mcp_port = mcp_sock.getsockname()[1]
            self.mcp_url = f"http://127.0.0.1:{mcp_port}/mcp"

            state = SQLiteOperationalStateStore(str(self.tmp_path / "state.sqlite"))
            state.initialize(mode=Mode.EXP, task="Runtime v2 real HTTP OAuth")
            self.bridge = MCPControlBridge(
                production=UnusedProduction(),
                operational_state=state,
                run_registry=SQLiteMCPRunRegistry(str(self.tmp_path / "runs.sqlite")),
                identity_policy=HaoMCPIdentityPolicy(EXPECTED_SUBJECT),
            )
            verifier = JWKSAccessTokenVerifier(
                issuer=self.issuer,
                audience=self.mcp_url,
                jwks_url=self.issuer + ".well-known/jwks.json",
            )
            auth = AuthSettings(
                issuer_url=AnyHttpUrl(self.issuer),
                resource_server_url=AnyHttpUrl(self.mcp_url),
                required_scopes=[SCOPE_ACCESS],
            )
            self.mcp = build_mcp_control_server(
                self.bridge,
                token_verifier=verifier,
                auth_settings=auth,
                request_state_keys=[bytes.fromhex("11" * 32)],
                request_state_audience="runtime-v2-lane-a",
            )
            app = build_mcp_http_app(
                self.mcp,
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=["https://trusted.example"],
            )
            with _running_uvicorn(app, mcp_sock):
                yield self


def _raw_post(url, token, *, headers=None):
    merged = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    merged.update(headers or {})
    return httpx2.post(url, headers=merged, json={}, timeout=5.0)


def test_real_tcp_streamable_http_jwks_oauth_reaches_runtime_owned_context(tmp_path):
    harness = OAuthNetworkHarness(tmp_path)
    with harness.run():
        token = harness.token([SCOPE_ACCESS, SCOPE_READ])
        response_headers = []

        async def capture(response):
            response_headers.append(dict(response.headers))

        async def scenario():
            http_client = httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
                event_hooks={"response": [capture]},
            )
            transport = streamable_http_client(
                harness.mcp_url,
                http_client=http_client,
            )
            async with Client(transport, mode="auto") as client:
                assert client.protocol_version == "2026-07-28"
                result = await client.call_tool("hao_control_context", {})
                assert result.is_error is False
                assert result.structured_content["mode"] == "EXP"
                assert result.structured_content["task"] == "Runtime v2 real HTTP OAuth"
                assert result.structured_content["operational_version"] == 1
            await http_client.aclose()

        asyncio.run(scenario())
        assert harness.jwks_requests >= 1
        assert response_headers
        assert all("mcp-session-id" not in headers for headers in response_headers)


def test_real_http_oauth_core_negative_token_paths_fail_closed(tmp_path):
    harness = OAuthNetworkHarness(tmp_path)
    with harness.run():
        cases = [
            "not-a-jwt",
            harness.token(
                [SCOPE_ACCESS, SCOPE_READ],
                exp=int(time.time()) - 600,
            ),
            harness.token(
                [SCOPE_ACCESS, SCOPE_READ],
                aud="http://127.0.0.1:1/not-this-resource",
            ),
            harness.token(
                [SCOPE_ACCESS, SCOPE_READ],
                iss=harness.issuer + "wrong",
            ),
        ]
        for token in cases:
            response = _raw_post(harness.mcp_url, token)
            assert response.status_code == 401
            assert response.json()["error"] == "invalid_token"


def test_real_http_global_and_per_tool_scopes_and_subject_are_separate_gates(tmp_path):
    harness = OAuthNetworkHarness(tmp_path)
    with harness.run():
        missing_global = harness.token([SCOPE_READ])
        response = _raw_post(harness.mcp_url, missing_global)
        assert response.status_code == 403
        assert response.json()["error"] == "insufficient_scope"

        read_only = harness.token([SCOPE_ACCESS, SCOPE_READ])

        async def read_only_scenario():
            http_client = httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {read_only}"}, timeout=5.0
            )
            async with Client(
                streamable_http_client(harness.mcp_url, http_client=http_client),
                mode="auto",
            ) as client:
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
            await http_client.aclose()

        asyncio.run(read_only_scenario())

        wrong_subject = harness.token(
            [SCOPE_ACCESS, SCOPE_READ], sub="not-hao"
        )

        async def wrong_subject_scenario():
            http_client = httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {wrong_subject}"}, timeout=5.0
            )
            async with Client(
                streamable_http_client(harness.mcp_url, http_client=http_client),
                mode="auto",
            ) as client:
                result = await client.call_tool("hao_control_context", {})
                assert result.is_error is True
                assert "HAO_IDENTITY_REQUIRED" in result.content[0].text
            await http_client.aclose()

        asyncio.run(wrong_subject_scenario())


def test_real_http_jwks_resolution_failure_is_401(tmp_path):
    harness = OAuthNetworkHarness(tmp_path, jwks_status=503)
    with harness.run():
        response = _raw_post(
            harness.mcp_url,
            harness.token([SCOPE_ACCESS, SCOPE_READ]),
        )
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_token"
        assert harness.jwks_requests >= 1


def test_real_http_dns_rebinding_host_and_origin_boundaries_run_before_mcp(tmp_path):
    harness = OAuthNetworkHarness(tmp_path)
    with harness.run():
        token = harness.token([SCOPE_ACCESS, SCOPE_READ])
        wrong_host = _raw_post(
            harness.mcp_url,
            token,
            headers={"Host": "evil.example"},
        )
        assert (wrong_host.status_code, wrong_host.text) == (
            421,
            "Invalid Host header",
        )

        wrong_origin = _raw_post(
            harness.mcp_url,
            token,
            headers={"Origin": "https://evil.example"},
        )
        assert (wrong_origin.status_code, wrong_origin.text) == (
            403,
            "Invalid Origin header",
        )

        trusted_origin = _raw_post(
            harness.mcp_url,
            token,
            headers={"Origin": "https://trusted.example"},
        )
        assert trusted_origin.status_code != 403


def test_scope_constants_cover_access_read_execute_and_approve():
    assert {SCOPE_ACCESS, SCOPE_READ, SCOPE_EXECUTE, SCOPE_APPROVE} == {
        "hao:access",
        "hao:read",
        "hao:execute",
        "hao:approve",
    }
