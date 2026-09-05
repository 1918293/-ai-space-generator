import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest
from starlette.applications import Starlette

import src.runtime_deployment as deployment
from src.runtime_config import RuntimeRole
from src.runtime_migrations import (
    LATEST_RUNTIME_SCHEMA_VERSION,
    apply_runtime_migrations,
)
from src.runtime_observability import RuntimeTelemetry


class Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class MigrationConnection:
    def __init__(self, applied=()):
        self.applied = list(applied)
        self.calls = []
        self.closed = False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT version FROM hao_runtime_schema_migrations"):
            return Cursor([(version,) for version in self.applied])
        if normalized.startswith("INSERT INTO hao_runtime_schema_migrations"):
            self.applied.append(int(params[0]))
        return Cursor()

    def close(self):
        self.closed = True


def test_postgres_migration_is_locked_versioned_and_covers_runtime_tables():
    conn = MigrationConnection()
    result = apply_runtime_migrations(
        "postgresql://runtime/test",
        connect_factory=lambda: conn,
    )
    assert result == (LATEST_RUNTIME_SCHEMA_VERSION,)
    sql = "\n".join(item[0] for item in conn.calls)
    assert "pg_advisory_xact_lock" in sql
    assert "CREATE TABLE IF NOT EXISTS hao_runtime_schema_migrations" in sql
    for table in (
        "operational_state",
        "idempotency_records",
        "authoritative_completions",
        "mcp_control_runs",
        "reconciliation_cases",
        "parent_tasks",
        "parent_task_children",
        "finalization_issue_times",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert conn.closed is True


def test_postgres_migration_fails_closed_against_newer_schema():
    conn = MigrationConnection(applied=(LATEST_RUNTIME_SCHEMA_VERSION + 1,))
    with pytest.raises(RuntimeError, match="DATABASE_SCHEMA_NEWER_THAN_RUNTIME"):
        apply_runtime_migrations(
            "postgresql://runtime/test",
            connect_factory=lambda: conn,
        )
    assert any(call[0] == "ROLLBACK" for call in conn.calls)
    assert conn.closed is True


class Counter:
    def __init__(self):
        self.calls = []

    def add(self, value, attributes=None):
        self.calls.append((value, dict(attributes or {})))


class Tracer:
    class _Span:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def __init__(self):
        self.calls = []

    def start_as_current_span(self, name, attributes=None):
        self.calls.append((name, dict(attributes or {})))
        return self._Span()


class Provider:
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


def test_otel_failure_codes_are_trace_only_and_providers_flush_on_shutdown():
    trace_provider = Provider()
    meter_provider = Provider()
    telemetry = RuntimeTelemetry(
        Tracer(),
        Counter(),
        Counter(),
        Counter(),
        Counter(),
        trace_provider,
        meter_provider,
    )
    telemetry.record_run_event(
        "failed",
        phase="FAILED",
        run_id="RUN-HIGH-CARDINALITY",
        provider="google-drive",
        failure_stage="PERSISTENCE",
        failure_code="PROVIDER_SPECIFIC_DYNAMIC_CODE_12345",
    )
    assert "hao.failure.code" not in telemetry.failures.calls[0][1]
    assert telemetry.tracer.calls[0][1]["hao.failure.code"] == "PROVIDER_SPECIFIC_DYNAMIC_CODE_12345"
    telemetry.shutdown()
    assert trace_provider.shutdown_calls == 1
    assert meter_provider.shutdown_calls == 1


class FakeStateStore:
    def get(self):
        return SimpleNamespace(mode=SimpleNamespace(value="EXP"), version=7)


class FakeSessionManager:
    def __init__(self):
        self.entered = 0
        self.exited = 0

    @asynccontextmanager
    async def run(self):
        self.entered += 1
        try:
            yield
        finally:
            self.exited += 1


class FakeMCP:
    def __init__(self):
        self.session_manager = FakeSessionManager()


def test_api_host_enters_mcp_lifespan_and_exposes_separate_liveness_readiness(monkeypatch):
    settings = SimpleNamespace(
        role=RuntimeRole.API,
        database_url="postgresql://runtime/test",
        oauth_issuer_url="https://issuer.example/",
        oauth_audience="https://runtime.example/mcp",
        oauth_jwks_url="https://issuer.example/jwks.json",
        oauth_resource_url="https://runtime.example/mcp",
        public_mcp_url="https://runtime.example/mcp",
        mcp_allowed_hosts=("runtime.example",),
        mcp_allowed_origins=(),
        request_state_key_bytes=(b"1" * 32,),
        mcp_request_state_audience="hao-control",
        expected_hao_subject="hao-user",
        attestation_secret="x" * 64,
        temporal_task_queue="hao-runtime-v2",
    )
    persistence = SimpleNamespace(
        operational_state=FakeStateStore(),
        completion=object(),
        run_registry=object(),
    )
    fake_mcp = FakeMCP()
    migrations = []

    monkeypatch.setattr(
        deployment.RuntimeSettings,
        "from_mapping",
        staticmethod(lambda values: settings),
    )
    monkeypatch.setattr(deployment, "apply_runtime_migrations", lambda url: migrations.append(url))
    monkeypatch.setattr(deployment, "_telemetry", lambda settings: None)
    monkeypatch.setattr(deployment, "load_sheets_targets", lambda values: (object(),))
    monkeypatch.setattr(deployment, "load_task_policies", lambda values: (object(),))
    monkeypatch.setattr(deployment, "load_parent_task_plans", lambda values: object())
    monkeypatch.setattr(deployment, "GoogleWorkspaceSheetsClient", lambda: object())
    monkeypatch.setattr(deployment, "build_postgres_persistence", lambda url: persistence)
    monkeypatch.setattr(deployment, "PostgresReconciliationStore", lambda url: object())
    monkeypatch.setattr(deployment, "_initialize_operational_state", lambda persistence, values: None)
    monkeypatch.setattr(deployment, "GoogleAuthorityTaskPolicyProvider", lambda *args: object())
    monkeypatch.setattr(deployment, "action_catalog_for_targets", lambda targets: object())
    monkeypatch.setattr(deployment, "ControlPlaneGateway", lambda *args: object())

    async def temporal(_settings):
        return object()

    monkeypatch.setattr(deployment, "connect_temporal", temporal)
    monkeypatch.setattr(deployment, "TemporalWorkflowStarter", lambda *args, **kwargs: object())
    monkeypatch.setattr(deployment, "CompletionAttestor", lambda *args, **kwargs: object())
    monkeypatch.setattr(deployment, "ProductionExecutionService", lambda **kwargs: object())
    monkeypatch.setattr(deployment, "PostgresFinalizationIssueStore", lambda url: object())
    monkeypatch.setattr(deployment, "StableFinalizationProductionService", lambda *args: object())
    monkeypatch.setattr(deployment, "PostgresParentTaskStore", lambda url: object())
    monkeypatch.setattr(deployment, "ProductionParentTaskService", lambda **kwargs: object())
    monkeypatch.setattr(deployment, "HaoMCPIdentityPolicy", lambda subject: object())
    monkeypatch.setattr(deployment, "MCPControlBridge", lambda **kwargs: object())
    monkeypatch.setattr(deployment, "JWKSAccessTokenVerifier", lambda **kwargs: object())
    monkeypatch.setattr(deployment, "_oauth_settings", lambda settings: object())
    monkeypatch.setattr(deployment, "build_mcp_control_server", lambda *args, **kwargs: fake_mcp)
    monkeypatch.setattr(deployment, "build_mcp_http_app", lambda *args, **kwargs: Starlette())

    async def scenario():
        app = await deployment.build_api_app({})
        assert migrations == ["postgresql://runtime/test"]
        async with app.router.lifespan_context(app):
            assert fake_mcp.session_manager.entered == 1
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
                live = await client.get("/livez")
                ready = await client.get("/readyz")
                legacy = await client.get("/healthz")
            assert live.status_code == 200
            assert live.json() == {"status": "alive", "role": "api"}
            assert ready.status_code == 200
            assert ready.json()["status"] == "ready"
            assert ready.json()["schema_version"] == LATEST_RUNTIME_SCHEMA_VERSION
            assert legacy.json()["status"] == "ready"
        assert fake_mcp.session_manager.exited == 1

    asyncio.run(scenario())


def test_cloud_run_worker_grace_budget_leaves_margin_before_forced_termination():
    assert 0 < deployment.CLOUD_RUN_GRACEFUL_SHUTDOWN_SECONDS < 10
