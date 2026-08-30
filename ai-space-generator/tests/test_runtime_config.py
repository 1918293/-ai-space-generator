import pytest

from src.runtime_config import RuntimeEnvironment, RuntimeRole, RuntimeSettings


def production_values(**overrides):
    values = {
        "HAO_RUNTIME_ENV": "production",
        "HAO_RUNTIME_ROLE": "api",
        "HAO_RUNTIME_REGION": "asia-east1",
        "HAO_PUBLIC_MCP_URL": "https://runtime.example.com/mcp",
        "HAO_MCP_ALLOWED_HOSTS": "runtime.example.com,runtime.example.com:*",
        "HAO_MCP_ALLOWED_ORIGINS": "",
        "HAO_MCP_REQUEST_STATE_KEYS": ("11" * 32) + "," + ("22" * 32),
        "HAO_MCP_REQUEST_STATE_AUDIENCE": "hao-system-control",
        "HAO_DATABASE_URL": "postgresql://runtime@db/runtime",
        "HAO_TEMPORAL_ENDPOINT": "hao-runtime.tmprl.cloud:7233",
        "HAO_TEMPORAL_NAMESPACE": "hao-runtime-prod",
        "HAO_TEMPORAL_TASK_QUEUE": "hao-runtime-v2",
        "HAO_TEMPORAL_API_KEY": "temporal-secret-from-secret-manager",
        "HAO_OAUTH_ISSUER_URL": "https://hao-runtime.auth0.com/",
        "HAO_OAUTH_RESOURCE_URL": "https://runtime.example.com/mcp",
        "HAO_OAUTH_AUDIENCE": "https://runtime.example.com/mcp",
        "HAO_OAUTH_JWKS_URL": "https://hao-runtime.auth0.com/.well-known/jwks.json",
        "HAO_EXPECTED_SUBJECT": "auth0|hao-subject",
        "HAO_ATTESTATION_KEY_ID": "completion-signing-v1",
        "HAO_ATTESTATION_SECRET": "x" * 64,
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
    assert settings.request_state_key_bytes == (bytes.fromhex("11" * 32), bytes.fromhex("22" * 32))


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


def test_production_requires_https_public_mcp_oauth_issuer_and_jwks():
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
        RuntimeSettings.from_mapping(production_values(HAO_MCP_REQUEST_STATE_KEYS="not-hex"))
    with pytest.raises(ValueError, match="REQUEST_STATE_KEY_MIN_32_BYTES:0"):
        RuntimeSettings.from_mapping(production_values(HAO_MCP_REQUEST_STATE_KEYS="11" * 16))
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_MCP_REQUEST_STATE_AUDIENCE"):
        RuntimeSettings.from_mapping(production_values(HAO_MCP_REQUEST_STATE_AUDIENCE=""))


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
        RuntimeSettings.from_mapping(production_values(HAO_TEMPORAL_API_KEY=""))
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_OTEL_ENDPOINT"):
        RuntimeSettings.from_mapping(production_values(HAO_OTEL_ENDPOINT=""))


def test_weak_attestation_secret_is_rejected():
    with pytest.raises(ValueError, match="ATTESTATION_SECRET_MIN_32_BYTES"):
        RuntimeSettings.from_mapping(
            production_values(HAO_ATTESTATION_SECRET="too-short")
        )


def test_missing_expected_hao_subject_is_fatal():
    with pytest.raises(ValueError, match="MISSING_CONFIG:HAO_EXPECTED_SUBJECT"):
        RuntimeSettings.from_mapping(production_values(HAO_EXPECTED_SUBJECT=""))
