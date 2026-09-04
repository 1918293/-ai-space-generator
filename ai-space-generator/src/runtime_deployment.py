from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from .action_catalog import ActionBinding, ActionCatalog
from .authoritative_completion import CompletionAttestor
from .control_gateway import ControlPlaneGateway
from .execution_control import ActionArchetype, ActionExternality, Mode
from .google_drive_control import (
    ControlledGoogleDriveProvider,
    GoogleDriveAuthorityGuard,
    GoogleDriveOutcomeVerifier,
)
from .google_workspace_adapter import (
    AuthorityFileSource,
    ConfiguredSheetsCommandResolver,
    GoogleWorkspaceSheetsClient,
    SheetsMutationTarget,
)
from .idempotent_broker import IdempotentAsyncBroker
from .mcp_control_bridge import HaoMCPIdentityPolicy, MCPControlBridge
from .mcp_control_server import SCOPE_ACCESS, build_mcp_control_server
from .mcp_http import build_mcp_http_app
from .oauth_verifier import JWKSAccessTokenVerifier
from .postgres_persistence import build_postgres_persistence
from .production_execution import ProductionExecutionService
from .reconciliation_persistence import (
    PostgresReconciliationStore,
    ReconciliationAwareBroker,
)
from .runtime_config import RuntimeRole, RuntimeSettings
from .runtime_observability import configure_runtime_telemetry
from .runtime_observability_bridge import (
    ObservableMCPControlBridge,
    ObservableReconciliationBroker,
)
from .runtime_policy import ConfiguredTaskPolicySpec, GoogleAuthorityTaskPolicyProvider
from .temporal_client import TemporalWorkflowStarter
from .temporal_control import ExecutionActivities, HaoExecutionControlWorkflow


FORMAL_SHEETS_ASSURANCE_TAGS = (
    "FORMAL_HAO_PERSISTENCE",
    "EXACT_CONFIGURED_TARGET",
    "DIRECT_STATE_READBACK",
)


def _json_env(values: dict[str, str], key: str) -> object:
    raw = str(values.get(key, "")).strip()
    if not raw:
        raise ValueError(f"MISSING_CONFIG:{key}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"INVALID_JSON_CONFIG:{key}") from exc


def _authority_sources(raw: object) -> tuple[AuthorityFileSource, ...]:
    if not isinstance(raw, list):
        raise ValueError("AUTHORITY_SOURCES_LIST_REQUIRED")
    result: list[AuthorityFileSource] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("AUTHORITY_SOURCE_OBJECT_REQUIRED")
        ref = str(item.get("ref", "")).strip()
        file_id = str(item.get("file_id", "")).strip()
        if not ref or not file_id:
            raise ValueError("AUTHORITY_SOURCE_FIELDS_REQUIRED")
        result.append(AuthorityFileSource(ref, file_id))
    if not result:
        raise ValueError("AUTHORITY_SOURCES_REQUIRED")
    return tuple(result)


def load_sheets_targets(values: dict[str, str]) -> tuple[SheetsMutationTarget, ...]:
    raw = _json_env(values, "HAO_SHEETS_TARGETS_JSON")
    if not isinstance(raw, list):
        raise ValueError("HAO_SHEETS_TARGETS_LIST_REQUIRED")
    targets: list[SheetsMutationTarget] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("HAO_SHEETS_TARGET_OBJECT_REQUIRED")
        targets.append(
            SheetsMutationTarget(
                binding_id=str(item.get("binding_id", "")).strip(),
                spreadsheet_id=str(item.get("spreadsheet_id", "")).strip(),
                range_a1=str(item.get("range_a1", "")).strip(),
                value_input_option=str(item.get("value_input_option", "RAW")).strip(),
                authority_sources=_authority_sources(item.get("authority_sources", [])),
            )
        )
    if not targets:
        raise ValueError("HAO_SHEETS_TARGETS_REQUIRED")
    return tuple(targets)


def load_task_policies(values: dict[str, str]) -> tuple[ConfiguredTaskPolicySpec, ...]:
    raw = _json_env(values, "HAO_TASK_POLICIES_JSON")
    if not isinstance(raw, list):
        raise ValueError("HAO_TASK_POLICIES_LIST_REQUIRED")
    specs: list[ConfiguredTaskPolicySpec] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("HAO_TASK_POLICY_OBJECT_REQUIRED")
        acceptance = tuple(
            str(value).strip()
            for value in item.get("acceptance_criteria", [])
            if str(value).strip()
        )
        gates = {
            str(value).strip()
            for value in item.get("required_acceptance_gate_ids", [])
            if str(value).strip()
        }
        gates.add("DRIVE_EXPECTED_STATE_MATCH")
        required_tags = {
            str(value).strip().upper()
            for value in item.get("required_action_tags", [])
            if str(value).strip()
        }
        required_tags.update(FORMAL_SHEETS_ASSURANCE_TAGS)
        forbidden_tags = tuple(
            str(value).strip().upper()
            for value in item.get("forbidden_action_tags", [])
            if str(value).strip()
        )
        specs.append(
            ConfiguredTaskPolicySpec(
                task=str(item.get("task", "")).strip(),
                acceptance_criteria=acceptance,
                authority_sources=_authority_sources(item.get("authority_sources", [])),
                required_acceptance_gate_ids=tuple(sorted(gates)),
                hao_acceptance_required=bool(item.get("hao_acceptance_required", False)),
                required_action_tags=tuple(sorted(required_tags)),
                forbidden_action_tags=forbidden_tags,
            )
        )
    if not specs:
        raise ValueError("HAO_TASK_POLICIES_REQUIRED")
    return tuple(specs)


def action_catalog_for_targets(targets: tuple[SheetsMutationTarget, ...]) -> ActionCatalog:
    return ActionCatalog(
        ActionBinding(
            binding_id=target.binding_id,
            capability="formal_persistence",
            provider="google-drive",
            action_name="update_cells",
            archetype=ActionArchetype.MUTATE,
            externality=ActionExternality.PRIVATE_REVERSIBLE,
            assurance_tags=FORMAL_SHEETS_ASSURANCE_TAGS,
            authorization_scope_prefix="HAO_DRIVE_WRITE",
            rollback_available=True,
            allowed_argument_keys=("values_json",),
            required_argument_keys=("values_json",),
        )
        for target in targets
    )


async def connect_temporal(settings: RuntimeSettings) -> Any:
    from temporalio.client import Client

    kwargs: dict[str, Any] = {"namespace": settings.temporal_namespace}
    if settings.temporal_api_key:
        kwargs["api_key"] = settings.temporal_api_key
        kwargs["tls"] = True
    return await Client.connect(settings.temporal_endpoint, **kwargs)


def _initialize_operational_state(
    persistence: Any,
    values: dict[str, str],
) -> None:
    try:
        persistence.operational_state.get()
        return
    except ValueError as exc:
        if str(exc) != "OPERATIONAL_STATE_NOT_INITIALIZED":
            raise
    mode_raw = str(values.get("HAO_INITIAL_MODE", "")).strip().upper()
    task = str(values.get("HAO_INITIAL_TASK", "")).strip()
    if not mode_raw or not task:
        raise ValueError("HAO_INITIAL_MODE_AND_TASK_REQUIRED")
    persistence.operational_state.initialize(mode=Mode(mode_raw), task=task)


def _oauth_settings(settings: RuntimeSettings) -> Any:
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    return AuthSettings(
        issuer_url=AnyHttpUrl(settings.oauth_issuer_url),
        resource_server_url=AnyHttpUrl(settings.oauth_resource_url),
        required_scopes=[SCOPE_ACCESS],
    )


def _telemetry(settings: RuntimeSettings) -> Any | None:
    if not settings.otel_endpoint:
        return None
    return configure_runtime_telemetry(
        endpoint=settings.otel_endpoint,
        role=settings.role.value,
        region=settings.region,
    )


async def build_api_app(values: dict[str, str]) -> Any:
    settings = RuntimeSettings.from_mapping(values)
    if settings.role != RuntimeRole.API:
        raise ValueError("API_ROLE_REQUIRED")

    telemetry = _telemetry(settings)
    targets = load_sheets_targets(values)
    specs = load_task_policies(values)
    google_client = GoogleWorkspaceSheetsClient()
    persistence = build_postgres_persistence(settings.database_url)
    _initialize_operational_state(persistence, values)

    policy = GoogleAuthorityTaskPolicyProvider(google_client, specs)
    gateway = ControlPlaneGateway(action_catalog_for_targets(targets), policy)
    temporal_client = await connect_temporal(settings)
    starter = TemporalWorkflowStarter(
        temporal_client,
        task_queue=settings.temporal_task_queue,
    )
    production = ProductionExecutionService(
        gateway=gateway,
        starter=starter,
        attestor=CompletionAttestor(settings.attestation_secret.encode("utf-8")),
        completion_store=persistence.completion,
    )
    base_bridge = MCPControlBridge(
        production=production,
        operational_state=persistence.operational_state,
        run_registry=persistence.run_registry,
        identity_policy=HaoMCPIdentityPolicy(settings.expected_hao_subject),
    )
    bridge = (
        ObservableMCPControlBridge(base_bridge, telemetry)
        if telemetry is not None
        else base_bridge
    )
    verifier = JWKSAccessTokenVerifier(
        issuer=settings.oauth_issuer_url,
        audience=settings.oauth_audience,
        jwks_url=settings.oauth_jwks_url,
    )
    mcp = build_mcp_control_server(
        bridge,
        token_verifier=verifier,
        auth_settings=_oauth_settings(settings),
        request_state_keys=settings.request_state_key_bytes,
        request_state_audience=settings.mcp_request_state_audience,
    )
    mcp_app = build_mcp_http_app(
        mcp,
        allowed_hosts=settings.mcp_allowed_hosts,
        allowed_origins=settings.mcp_allowed_origins,
    )

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    async def healthz(request: Any) -> JSONResponse:
        del request
        try:
            state = persistence.operational_state.get()
            return JSONResponse(
                {
                    "status": "ready",
                    "role": settings.role.value,
                    "mode": state.mode.value,
                    "operational_version": state.version,
                }
            )
        except Exception as exc:
            return JSONResponse(
                {"status": "not_ready", "error": type(exc).__name__},
                status_code=503,
            )

    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Mount("/", app=mcp_app),
        ]
    )


async def run_worker(values: dict[str, str]) -> None:
    settings = RuntimeSettings.from_mapping(values)
    if settings.role != RuntimeRole.WORKER:
        raise ValueError("WORKER_ROLE_REQUIRED")

    telemetry = _telemetry(settings)
    targets = load_sheets_targets(values)
    google_client = GoogleWorkspaceSheetsClient()
    persistence = build_postgres_persistence(settings.database_url)
    reconciliation = PostgresReconciliationStore(settings.database_url)
    resolver = ConfiguredSheetsCommandResolver(targets)
    raw_provider = ControlledGoogleDriveProvider(resolver, google_client)
    idempotent = IdempotentAsyncBroker(raw_provider, persistence.idempotency)
    reconciliation_broker = ReconciliationAwareBroker(idempotent, reconciliation)
    broker = (
        ObservableReconciliationBroker(reconciliation_broker, reconciliation, telemetry)
        if telemetry is not None
        else reconciliation_broker
    )
    verifier = GoogleDriveOutcomeVerifier(resolver, google_client)
    authority_guard = GoogleDriveAuthorityGuard(resolver, google_client)
    activities = ExecutionActivities(
        broker=broker,
        verifier=verifier,
        authority_guard=authority_guard,
    )
    temporal_client = await connect_temporal(settings)

    from temporalio.worker import Worker

    worker = Worker(
        temporal_client,
        task_queue=settings.temporal_task_queue,
        workflows=[HaoExecutionControlWorkflow],
        activities=[
            activities.preflight_authority,
            activities.execute_tool,
            activities.verify_outcome,
        ],
    )
    await worker.run()


async def _main_async(values: dict[str, str]) -> None:
    settings = RuntimeSettings.from_mapping(values)
    if settings.role == RuntimeRole.WORKER:
        await run_worker(values)
        return

    app = await build_api_app(values)
    import uvicorn

    port = int(str(values.get("PORT", "8080")))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def main() -> None:
    asyncio.run(_main_async(dict(os.environ)))


if __name__ == "__main__":
    main()
