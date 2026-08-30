from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class RuntimeRole(StrEnum):
    API = "api"
    WORKER = "worker"


class RuntimeEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


def _required(values: dict[str, str], key: str) -> str:
    value = str(values.get(key, "")).strip()
    if not value:
        raise ValueError(f"MISSING_CONFIG:{key}")
    return value


def _csv(values: dict[str, str], key: str) -> tuple[str, ...]:
    raw = str(values.get(key, ""))
    result: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if item and item not in result:
            result.append(item)
    return tuple(result)


def _validate_url(
    value: str,
    *,
    field: str,
    production: bool,
    normalize_trailing_slash: bool,
) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"INVALID_URL:{field}")
    if production and parsed.scheme != "https":
        raise ValueError(f"HTTPS_REQUIRED:{field}")
    return value.rstrip("/") if normalize_trailing_slash else value


def _host_from_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.hostname:
        raise ValueError("PUBLIC_MCP_HOST_UNRESOLVED")
    return parsed.hostname


def _validate_allowlist(hosts: tuple[str, ...], public_host: str) -> None:
    if not hosts:
        raise ValueError("MCP_ALLOWED_HOSTS_REQUIRED")
    for host in hosts:
        if host == "*" or host.startswith("*."):
            raise ValueError("MCP_ALLOWED_HOSTS_WILDCARD_NOT_ALLOWED")
    allowed_bare_hosts = {host.split(":", 1)[0] for host in hosts}
    if public_host not in allowed_bare_hosts:
        raise ValueError("PUBLIC_MCP_HOST_NOT_ALLOWLISTED")


def _request_state_keys(values: dict[str, str]) -> tuple[str, ...]:
    keys = _csv(values, "HAO_MCP_REQUEST_STATE_KEYS")
    if not keys:
        raise ValueError("MISSING_CONFIG:HAO_MCP_REQUEST_STATE_KEYS")
    for index, key in enumerate(keys):
        try:
            raw = bytes.fromhex(key)
        except ValueError as exc:
            raise ValueError(f"INVALID_REQUEST_STATE_KEY_HEX:{index}") from exc
        if len(raw) < 32:
            raise ValueError(f"REQUEST_STATE_KEY_MIN_32_BYTES:{index}")
    return keys


@dataclass(frozen=True)
class RuntimeSettings:
    environment: RuntimeEnvironment
    role: RuntimeRole
    region: str
    public_mcp_url: str
    mcp_allowed_hosts: tuple[str, ...]
    mcp_allowed_origins: tuple[str, ...]
    mcp_request_state_keys: tuple[str, ...]
    mcp_request_state_audience: str
    database_url: str
    temporal_endpoint: str
    temporal_namespace: str
    temporal_task_queue: str
    temporal_api_key: str
    oauth_issuer_url: str
    oauth_resource_url: str
    oauth_audience: str
    oauth_jwks_url: str
    expected_hao_subject: str
    attestation_key_id: str
    attestation_secret: str
    otel_endpoint: str

    @property
    def request_state_key_bytes(self) -> tuple[bytes, ...]:
        return tuple(bytes.fromhex(value) for value in self.mcp_request_state_keys)

    @classmethod
    def from_mapping(cls, values: dict[str, str]) -> "RuntimeSettings":
        try:
            environment = RuntimeEnvironment(_required(values, "HAO_RUNTIME_ENV").lower())
        except ValueError as exc:
            if str(exc).startswith("MISSING_CONFIG:"):
                raise
            raise ValueError("INVALID_CONFIG:HAO_RUNTIME_ENV") from exc

        try:
            role = RuntimeRole(_required(values, "HAO_RUNTIME_ROLE").lower())
        except ValueError as exc:
            if str(exc).startswith("MISSING_CONFIG:"):
                raise
            raise ValueError("INVALID_CONFIG:HAO_RUNTIME_ROLE") from exc

        production = environment == RuntimeEnvironment.PRODUCTION
        region = _required(values, "HAO_RUNTIME_REGION")
        database_url = _required(values, "HAO_DATABASE_URL")
        temporal_endpoint = _required(values, "HAO_TEMPORAL_ENDPOINT")
        temporal_namespace = _required(values, "HAO_TEMPORAL_NAMESPACE")
        temporal_task_queue = _required(values, "HAO_TEMPORAL_TASK_QUEUE")
        temporal_api_key = _required(values, "HAO_TEMPORAL_API_KEY") if production else str(
            values.get("HAO_TEMPORAL_API_KEY", "")
        ).strip()
        otel_endpoint = _required(values, "HAO_OTEL_ENDPOINT") if production else str(
            values.get("HAO_OTEL_ENDPOINT", "")
        ).strip()

        if production and not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("PRODUCTION_POSTGRES_REQUIRED")

        # API-facing configuration is mandatory for both roles in one settings
        # contract so API and worker describe the same deployment identity rather
        # than drifting into incompatible runtime configurations.
        public_mcp_url = _validate_url(
            _required(values, "HAO_PUBLIC_MCP_URL"),
            field="HAO_PUBLIC_MCP_URL",
            production=production,
            normalize_trailing_slash=True,
        )
        if not public_mcp_url.endswith("/mcp"):
            raise ValueError("PUBLIC_MCP_URL_MUST_END_WITH_MCP")

        hosts = _csv(values, "HAO_MCP_ALLOWED_HOSTS")
        _validate_allowlist(hosts, _host_from_url(public_mcp_url))
        origins = _csv(values, "HAO_MCP_ALLOWED_ORIGINS")
        for origin in origins:
            if origin == "*" or origin.startswith("*."):
                raise ValueError("MCP_ALLOWED_ORIGINS_WILDCARD_NOT_ALLOWED")
            _validate_url(
                origin,
                field="HAO_MCP_ALLOWED_ORIGINS",
                production=production,
                normalize_trailing_slash=True,
            )

        # MCP 2026-07-28 multi-round-trip resolver state is sealed with a per-
        # process key by default. A Cloud Run fleet must share keys and audience
        # or a human-approval retry can land on another instance and fail.
        request_state_keys = _request_state_keys(values)
        request_state_audience = _required(values, "HAO_MCP_REQUEST_STATE_AUDIENCE")

        # OAuth issuer is an exact case-sensitive JWT `iss` identity. Validate
        # its URL shape but never rewrite its trailing slash or other content.
        oauth_issuer_url = _validate_url(
            _required(values, "HAO_OAUTH_ISSUER_URL"),
            field="HAO_OAUTH_ISSUER_URL",
            production=production,
            normalize_trailing_slash=False,
        )
        oauth_resource_url = _validate_url(
            _required(values, "HAO_OAUTH_RESOURCE_URL"),
            field="HAO_OAUTH_RESOURCE_URL",
            production=production,
            normalize_trailing_slash=True,
        )
        if oauth_resource_url != public_mcp_url:
            raise ValueError("OAUTH_RESOURCE_MUST_EQUAL_PUBLIC_MCP_URL")

        oauth_audience = _required(values, "HAO_OAUTH_AUDIENCE").rstrip("/")
        if oauth_audience != public_mcp_url:
            raise ValueError("OAUTH_AUDIENCE_MUST_EQUAL_PUBLIC_MCP_URL")

        oauth_jwks_url = _validate_url(
            _required(values, "HAO_OAUTH_JWKS_URL"),
            field="HAO_OAUTH_JWKS_URL",
            production=production,
            normalize_trailing_slash=False,
        )

        expected_hao_subject = _required(values, "HAO_EXPECTED_SUBJECT")
        attestation_key_id = _required(values, "HAO_ATTESTATION_KEY_ID")
        attestation_secret = _required(values, "HAO_ATTESTATION_SECRET")
        if len(attestation_secret.encode("utf-8")) < 32:
            raise ValueError("ATTESTATION_SECRET_MIN_32_BYTES")

        return cls(
            environment=environment,
            role=role,
            region=region,
            public_mcp_url=public_mcp_url,
            mcp_allowed_hosts=hosts,
            mcp_allowed_origins=origins,
            mcp_request_state_keys=request_state_keys,
            mcp_request_state_audience=request_state_audience,
            database_url=database_url,
            temporal_endpoint=temporal_endpoint,
            temporal_namespace=temporal_namespace,
            temporal_task_queue=temporal_task_queue,
            temporal_api_key=temporal_api_key,
            oauth_issuer_url=oauth_issuer_url,
            oauth_resource_url=oauth_resource_url,
            oauth_audience=oauth_audience,
            oauth_jwks_url=oauth_jwks_url,
            expected_hao_subject=expected_hao_subject,
            attestation_key_id=attestation_key_id,
            attestation_secret=attestation_secret,
            otel_endpoint=otel_endpoint,
        )
