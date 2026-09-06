import json

import pytest

from src.runtime_config import RuntimeRole, RuntimeSettings
from src.runtime_migrations import CURRENT_RUNTIME_SCHEMA_VERSION


def _common_values(role: str) -> dict[str, str]:
    return {
        "HAO_RUNTIME_ENV": "production",
        "HAO_RUNTIME_ROLE": role,
        "HAO_RUNTIME_REGION": "asia-east1",
        "HAO_RELEASE_ID": "runtime-v2.0.0-rc1",
        "HAO_DEPLOYMENT_ID": "r2-role-scope-test",
        "HAO_PUBLIC_MCP_URL": "https://runtime.example.com/mcp",
        "HAO_MCP_ALLOWED_HOSTS": "runtime.example.com,runtime.example.com:*",
        "HAO_MCP_ALLOWED_ORIGINS": "",
        "HAO_MCP_REQUEST_STATE_AUDIENCE": "hao-system-control",
        "HAO_DATABASE_URL": "postgresql://runtime@db.internal/runtime",
        "HAO_DATABASE_SCHEMA_VERSION": str(CURRENT_RUNTIME_SCHEMA_VERSION),
        "HAO_DATABASE_MIN_SCHEMA_VERSION": str(CURRENT_RUNTIME_SCHEMA_VERSION),
        "HAO_STORAGE_COMPATIBILITY_EPOCHS": "1",
        "HAO_DATABASE_RPO_SECONDS": "300",
        "HAO_DATABASE_RTO_SECONDS": "3600",
        "HAO_TEMPORAL_ENDPOINT": "hao-runtime.tmprl.cloud:7233",
        "HAO_TEMPORAL_NAMESPACE": "hao-runtime-prod",
        "HAO_TEMPORAL_TASK_QUEUE": "hao-runtime-v2",
        "HAO_TEMPORAL_WORKER_VERSION": "r2-role-scope-test",
        "HAO_TEMPORAL_API_KEY": "temporal-secret-from-secret-manager",
        "HAO_WORKER_INSTANCE_COUNT": "1",
        "HAO_GRACEFUL_SHUTDOWN_SECONDS": "8",
        "HAO_OAUTH_ISSUER_URL": "https://hao-runtime.auth0.com/",
        "HAO_OAUTH_RESOURCE_URL": "https://runtime.example.com/mcp",
        "HAO_OAUTH_AUDIENCE": "https://runtime.example.com/mcp",
        "HAO_OAUTH_JWKS_URL": "https://hao-runtime.auth0.com/.well-known/jwks.json",
        "HAO_EXPECTED_SUBJECT": "auth0|hao-subject",
        "HAO_ATTESTATION_KEY_ID": "completion-signing-v1",
        "HAO_OTEL_ENDPOINT": "https://otel.example.com/v1/traces",
    }


def _worker_values(**overrides: str) -> dict[str, str]:
    values = _common_values("worker")
    values["HAO_SECRET_BINDINGS_JSON"] = json.dumps(
        {
            "HAO_TEMPORAL_API_KEY": (
                "projects/hao-prod/secrets/temporal-api-key/versions/7"
            )
        }
    )
    values.update(overrides)
    return values


def _api_values(**overrides: str) -> dict[str, str]:
    values = _common_values("api")
    values.update(
        {
            "HAO_MCP_REQUEST_STATE_KEYS": ("11" * 32) + "," + ("22" * 32),
            "HAO_ATTESTATION_SECRET": "x" * 64,
            "HAO_ATTESTATION_PREVIOUS_KEYS_JSON": "{}",
            "HAO_SECRET_BINDINGS_JSON": json.dumps(
                {
                    "HAO_TEMPORAL_API_KEY": (
                        "projects/hao-prod/secrets/temporal-api-key/versions/7"
                    ),
                    "HAO_ATTESTATION_SECRET": (
                        "projects/hao-prod/secrets/completion-signing-v1/versions/3"
                    ),
                    "HAO_MCP_REQUEST_STATE_KEYS": (
                        "projects/hao-prod/secrets/mcp-request-state-keys/versions/5"
                    ),
                }
            ),
        }
    )
    values.update(overrides)
    return values


def test_worker_requires_only_temporal_secret_and_carries_no_api_secret_material():
    settings = RuntimeSettings.from_mapping(_worker_values())

    assert settings.role == RuntimeRole.WORKER
    assert settings.temporal_api_key == "temporal-secret-from-secret-manager"
    assert settings.mcp_request_state_keys == ()
    assert settings.request_state_key_bytes == ()
    assert settings.attestation_secret == ""
    assert settings.attestation_previous_keys == ()
    assert settings.secret_binding_map == {
        "HAO_TEMPORAL_API_KEY": "projects/hao-prod/secrets/temporal-api-key/versions/7"
    }


def test_worker_no_longer_requires_api_only_secret_bindings():
    with pytest.raises(ValueError, match="MISSING_SECRET_BINDINGS:HAO_TEMPORAL_API_KEY"):
        RuntimeSettings.from_mapping(
            _worker_values(HAO_SECRET_BINDINGS_JSON=json.dumps({}))
        )

    settings = RuntimeSettings.from_mapping(_worker_values())
    assert "HAO_ATTESTATION_SECRET" not in settings.secret_binding_map
    assert "HAO_MCP_REQUEST_STATE_KEYS" not in settings.secret_binding_map


def test_api_keeps_attestation_and_mcp_secret_requirements():
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_MCP_REQUEST_STATE_KEYS"):
        RuntimeSettings.from_mapping(_api_values(HAO_MCP_REQUEST_STATE_KEYS=""))

    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_ATTESTATION_SECRET"):
        RuntimeSettings.from_mapping(_api_values(HAO_ATTESTATION_SECRET=""))

    with pytest.raises(ValueError, match="MISSING_SECRET_BINDINGS:HAO_ATTESTATION_SECRET"):
        RuntimeSettings.from_mapping(
            _api_values(
                HAO_SECRET_BINDINGS_JSON=json.dumps(
                    {
                        "HAO_TEMPORAL_API_KEY": (
                            "projects/hao-prod/secrets/temporal-api-key/versions/7"
                        ),
                        "HAO_MCP_REQUEST_STATE_KEYS": (
                            "projects/hao-prod/secrets/mcp-request-state-keys/versions/5"
                        ),
                    }
                )
            )
        )


def test_api_and_minimal_worker_keep_identical_non_secret_deployment_identity():
    api = RuntimeSettings.from_mapping(_api_values())
    worker = RuntimeSettings.from_mapping(_worker_values())

    assert api.deployment_identity == worker.deployment_identity
    assert api.deployment_identity_fingerprint == worker.deployment_identity_fingerprint


def test_worker_password_bearing_database_url_still_requires_database_secret_binding():
    password_url = "postgresql://runtime:db-secret@db.internal/runtime"
    with pytest.raises(ValueError, match="DATABASE_URL_PASSWORD_SECRET_BINDING_REQUIRED"):
        RuntimeSettings.from_mapping(_worker_values(HAO_DATABASE_URL=password_url))

    bindings = {
        "HAO_TEMPORAL_API_KEY": "projects/hao-prod/secrets/temporal-api-key/versions/7",
        "HAO_DATABASE_URL": "projects/hao-prod/secrets/database-url/versions/4",
    }
    settings = RuntimeSettings.from_mapping(
        _worker_values(
            HAO_DATABASE_URL=password_url,
            HAO_SECRET_BINDINGS_JSON=json.dumps(bindings),
        )
    )
    assert settings.database_url == password_url
    assert "db-secret" not in repr(settings.deployment_identity)
