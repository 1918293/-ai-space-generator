import pytest

from src.runtime_config import RuntimeEnvironment, RuntimeRole, RuntimeSettings


def production_values(**overrides):
    values = {
        "HAO_RUNTIME_ENV": "production",
        "HAO_RUNTIME_ROLE": "api",
        "HAO_RUNTIME_REGION": "asia-east1",
        "HAO_RELEASE_ID": "runtime-v2.0.0-rc1",
        "HAO_DEPLOYMENT_ID": "r2-20260905-a",
        "HAO_PUBLIC_MCP_URL": "https://runtime.example.com/mcp",
        "HAO_MCP_ALLOWED_HOSTS": "runtime.example.com,runtime.example.com:*",
        "HAO_MCP_ALLOWED_ORIGINS": "",
        "HAO_MCP_REQUEST_STATE_KEYS": ("11" * 32) + "," + ("22" * 32),
        "HAO_MCP_REQUEST_STATE_AUDIENCE": "hao-system-control",
        "HAO_DATABASE_URL": "postgresql://runtime@db/runtime",
        "HAO_DATABASE_SCHEMA_VERSION": "1",
        "HAO_DATABASE_RPO_SECONDS": "300",
        "HAO_DATABASE_RTO_SECONDS": "3600",
        "HAO_TEMPORAL_ENDPOINT": "hao-runtime.tmprl.cloud:7233",
        "HAO_TEMPORAL_NAMESPACE": "hao-runtime-prod",
        "HAO_TEMPORAL_TASK_QUEUE": "hao-runtime-v2",
        "HAO_TEMPORAL_WORKER_VERSION": "r2-20260905-a",
        "HAO_TEMPORAL_API_KEY": "temporal-secret-from-secret-manager",
        "HAO_WORKER_INSTANCE_COUNT": "1",
        "HAO_GRACEFUL_SHUTDOWN_SECONDS": "8",
        "HAO_OAUTH_ISSUER_URL": "https://hao-runtime.auth0.com/",
        "HAO_OAUTH_RESOURCE_URL": "https://runtime.example.com/mcp",
        "HAO_OAUTH_AUDIENCE": "https://runtime.example.com/mcp",
        "HAO_OAUTH_JWKS_URL": "https://hao-runtime.auth0.com/.well-known/jwks.json",
        "HAO_EXPECTED_SUBJECT": "auth0|hao-subject",
        "HAO_ATTESTATION_KEY_ID": "completion-signing-v1",
        "HAO_ATTESTATION_SECRET": "x" * 64,
        "HAO_SECRET_BINDINGS_JSON": """{
          "HAO_TEMPORAL_API_KEY":
            "projects/hao-prod/secrets/temporal-api-key/versions/7",
          "HAO_ATTESTATION_SECRET":
            "projects/hao-prod/secrets/completion-signing-v1/versions/3",
          "HAO_MCP_REQUEST_STATE_KEYS":
            "projects/hao-prod/secrets/mcp-request-state-keys/versions/5"
        }""",
        "HAO_OTEL_ENDPOINT": "https://otel.example.com/v1/traces",
    }
    values.update(overrides)
    return values


def test_valid_production_configuration_loads_as_one_runtime_identity():
    settings = RuntimeSettings.from_mapping(production_values())
    assert settings.environment == RuntimeEnvironment.PRODUCTION
    assert settings.role == RuntimeRole.API
    assert settings.region == "asia-east1"
    assert settings.public_mcp_url == "https://runtime.example.com/mcp"
    assert settings.oauth_resource_url == settings.public_mcp_url
    assert settings.oauth_audience == settings.public_mcp_url
    assert settings.oauth_issuer_url == "https://hao-runtime.auth0.com/"
    assert settings.oauth_jwks_url == "https://hao-runtime.auth0.com/.well-known/jwks.json"
    assert settings.mcp_request_state_audience == "hao-system-control"
    assert settings.request_state_key_bytes == (
        bytes.fromhex("11" * 32),
        bytes.fromhex("22" * 32),
    )
    assert settings.database_schema_version == 1
    assert settings.worker_instance_count == 1


def test_api_and_worker_share_exact_non_secret_deployment_identity():
    api = RuntimeSettings.from_mapping(production_values(HAO_RUNTIME_ROLE="api"))
    worker = RuntimeSettings.from_mapping(production_values(HAO_RUNTIME_ROLE="worker"))
    assert api.deployment_identity == worker.deployment_identity
    assert api.deployment_identity_fingerprint == worker.deployment_identity_fingerprint


def test_oauth_issuer_preserves_exact_trailing_slash_identity():
    settings = RuntimeSettings.from_mapping(
        production_values(HAO_OAUTH_ISSUER_URL="https://issuer.example.com/")
    )
    assert settings.oauth_issuer_url == "https://issuer.example.com/"


def test_production_region_is_explicit_but_not_hardcoded_into_runtime_semantics():
    settings = RuntimeSettings.from_mapping(
        production_values(HAO_RUNTIME_REGION="asia-northeast1")
    )
    assert settings.region == "asia-northeast1"


def test_production_requires_https_public_mcp_oauth_issuer_jwks_and_otel():
    with pytest.raises(ValueError, match="HTTPS_REQUIRED:HAO_PUBLIC_MCP_URL"):
        RuntimeSettings.from_mapping(
            production_values(HAO_PUBLIC_MCP_URL="http://runtime.example.com/mcp")
        )
    with pytest.raises(ValueError, match="HTTPS_REQUIRED:HAO_OAUTH_ISSUER_URL"):
        RuntimeSettings.from_mapping(
            production_values(HAO_OAUTH_ISSUER_URL="http://auth.example.com/")
        )
    with pytest.raises(ValueError, match="HTTPS_REQUIRED:HAO_OAUTH_JWKS_URL"):
        RuntimeSettings.from_mapping(
            production_values(HAO_OAUTH_JWKS_URL="http://auth.example.com/jwks.json")
        )
    with pytest.raises(ValueError, match="HTTPS_REQUIRED:HAO_OTEL_ENDPOINT"):
        RuntimeSettings.from_mapping(
            production_values(HAO_OTEL_ENDPOINT="http://otel.example.com")
        )


def test_public_mcp_hostname_must_be_explicitly_allowlisted():
    with pytest.raises(ValueError, match="PUBLIC_MCP_HOST_NOT_ALLOWLISTED"):
        RuntimeSettings.from_mapping(
            production_values(HAO_MCP_ALLOWED_HOSTS="other.example.com")
        )


def test_wildcard_mcp_hosts_are_rejected():
    with pytest.raises(ValueError, match="MCP_ALLOWED_HOSTS_WILDCARD_NOT_ALLOWED"):
        RuntimeSettings.from_mapping(
            production_values(HAO_MCP_ALLOWED_HOSTS="*")
        )


def test_request_state_keys_are_shared_explicit_and_rotation_ready():
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_MCP_REQUEST_STATE_KEYS"):
        RuntimeSettings.from_mapping(production_values(HAO_MCP_REQUEST_STATE_KEYS=""))
    with pytest.raises(ValueError, match="INVALID_REQUEST_STATE_KEY_HEX:0"):
        RuntimeSettings.from_mapping(
            production_values(HAO_MCP_REQUEST_STATE_KEYS="not-hex")
        )
    with pytest.raises(ValueError, match="REQUEST_STATE_KEY_MIN_32_BYTES:0"):
        RuntimeSettings.from_mapping(
            production_values(HAO_MCP_REQUEST_STATE_KEYS="11" * 16)
        )
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_MCP_REQUEST_STATE_AUDIENCE"):
        RuntimeSettings.from_mapping(
            production_values(HAO_MCP_REQUEST_STATE_AUDIENCE="")
        )


def test_production_requires_transactional_postgres_not_sqlite_reference_store():
    with pytest.raises(ValueError, match="PRODUCTION_POSTGRES_REQUIRED"):
        RuntimeSettings.from_mapping(
            production_values(HAO_DATABASE_URL="sqlite:///runtime.db")
        )


def test_oauth_resource_and_audience_are_bound_to_public_runtime_identity():
    with pytest.raises(ValueError, match="OAUTH_RESOURCE_MUST_EQUAL_PUBLIC_MCP_URL"):
        RuntimeSettings.from_mapping(
            production_values(HAO_OAUTH_RESOURCE_URL="https://other.example.com/mcp")
        )
    with pytest.raises(ValueError, match="OAUTH_AUDIENCE_MUST_EQUAL_PUBLIC_MCP_URL"):
        RuntimeSettings.from_mapping(
            production_values(HAO_OAUTH_AUDIENCE="https://other.example.com/mcp")
        )


def test_production_requires_temporal_credentials_and_observability():
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_TEMPORAL_API_KEY"):
        RuntimeSettings.from_mapping(
            production_values(HAO_TEMPORAL_API_KEY="")
        )
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_OTEL_ENDPOINT"):
        RuntimeSettings.from_mapping(production_values(HAO_OTEL_ENDPOINT=""))


def test_weak_attestation_secret_is_rejected():
    with pytest.raises(ValueError, match="ATTESTATION_SECRET_MIN_32_BYTES"):
        RuntimeSettings.from_mapping(
            production_values(HAO_ATTESTATION_SECRET="too-short")
        )


def test_missing_expected_hao_subject_is_fatal():
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_EXPECTED_SUBJECT"):
        RuntimeSettings.from_mapping(
            production_values(HAO_EXPECTED_SUBJECT="")
        )


def test_secret_manager_bindings_fail_closed_and_reject_latest_alias():
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_SECRET_BINDINGS_JSON"):
        RuntimeSettings.from_mapping(
            production_values(HAO_SECRET_BINDINGS_JSON="")
        )
    with pytest.raises(
        ValueError,
        match="SECRET_BINDING_EXPLICIT_VERSION_REQUIRED:HAO_TEMPORAL_API_KEY",
    ):
        RuntimeSettings.from_mapping(
            production_values(
                HAO_SECRET_BINDINGS_JSON="""{
                  "HAO_TEMPORAL_API_KEY":
                    "projects/hao-prod/secrets/temporal-api-key/versions/latest",
                  "HAO_ATTESTATION_SECRET":
                    "projects/hao-prod/secrets/completion-signing-v1/versions/3",
                  "HAO_MCP_REQUEST_STATE_KEYS":
                    "projects/hao-prod/secrets/mcp-request-state-keys/versions/5"
                }"""
            )
        )


def test_all_required_secret_bindings_must_be_declared():
    with pytest.raises(ValueError, match="MISSING_SECRET_BINDINGS:HAO_ATTESTATION_SECRET"):
        RuntimeSettings.from_mapping(
            production_values(
                HAO_SECRET_BINDINGS_JSON="""{
                  "HAO_TEMPORAL_API_KEY":
                    "projects/hao-prod/secrets/temporal-api-key/versions/7",
                  "HAO_MCP_REQUEST_STATE_KEYS":
                    "projects/hao-prod/secrets/mcp-request-state-keys/versions/5"
                }"""
            )
        )


def test_worker_count_shutdown_window_and_recovery_targets_are_fail_closed():
    with pytest.raises(ValueError, match="POSITIVE_INTEGER_REQUIRED:HAO_WORKER_INSTANCE_COUNT"):
        RuntimeSettings.from_mapping(
            production_values(HAO_WORKER_INSTANCE_COUNT="0")
        )
    with pytest.raises(
        ValueError,
        match="GRACEFUL_SHUTDOWN_MUST_FIT_CLOUD_RUN_SIGTERM_WINDOW",
    ):
        RuntimeSettings.from_mapping(
            production_values(HAO_GRACEFUL_SHUTDOWN_SECONDS="10")
        )
    with pytest.raises(ValueError, match="PRODUCTION_RPO_MUST_BE_300_SECONDS_OR_LESS"):
        RuntimeSettings.from_mapping(
            production_values(HAO_DATABASE_RPO_SECONDS="301")
        )


def test_production_allows_plain_http_only_for_loopback_otel_collector():
    settings = RuntimeSettings.from_mapping(
        production_values(HAO_OTEL_ENDPOINT="http://127.0.0.1:4318")
    )
    assert settings.otel_endpoint == "http://127.0.0.1:4318"

    with pytest.raises(ValueError, match="HTTPS_REQUIRED:HAO_OTEL_ENDPOINT"):
        RuntimeSettings.from_mapping(
            production_values(HAO_OTEL_ENDPOINT="http://otel.example.com:4318")
        )


def test_deployment_identity_never_embeds_database_credentials():
    first = RuntimeSettings.from_mapping(
        production_values(HAO_DATABASE_URL="postgresql://api-secret@db.internal/runtime")
    )
    second = RuntimeSettings.from_mapping(
        production_values(HAO_DATABASE_URL="postgresql://worker-secret@db.internal/runtime")
    )
    identity = dict(first.deployment_identity)
    assert identity["database_identity"] == "postgresql://db.internal/runtime"
    assert "api-secret" not in repr(first.deployment_identity)
    assert first.deployment_identity_fingerprint == second.deployment_identity_fingerprint
