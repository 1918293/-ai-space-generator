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


def _https_url(value: str, *, field: str, production: bool) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"INVALID_URL:{field}")
    if production and parsed.scheme != "https":
        raise ValueError(f"HTTPS_REQUIRED:{field}")
    return value.rstrip("/")


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


@dataclass(frozen=True)
class RuntimeSettings:
    environment: RuntimeEnvironment
    role: RuntimeRole
    region: str
    public_mcp_url: str
    mcp_allowed_hosts: tuple[str, ...]
    mcp_allowed_origins: tuple[str, ...]
    database_url: str
    temporal_endpoint: str
    temporal_namespace: str
    temporal_task_queue: str
    temporal_api_key: str
    oauth_issuer_url: str
    oauth_resource_url: str
    oauth_audience: str
    expected_hao_subject: str
    attestation_key_id: str
    attestation_secret: str
    otel_endpoint: str

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
        if not temporal_endpoint.strip():
            raise ValueError("TEMPORAL_ENDPOINT_REQUIRED")

        # API-facing configuration is mandatory for both roles in one settings
        # contract so API and worker are guaranteed to describe the same runtime
        # deployment rather than drifting into incompatible configurations.
        public_mcp_url = _https_url(
            _required(values, "HAO_PUBLIC_MCP_URL"),
            field="HAO_PUBLIC_MCP_URL",
            production=production,
        )
        if not public_mcp_url.endswith("/mcp"):
            raise ValueError("PUBLIC_MCP_URL_MUST_END_WITH_MCP")

        hosts = _csv(values, "HAO_MCP_ALLOWED_HOSTS")
        _validate_allowlist(hosts, _host_from_url(public_mcp_url))
        origins = _csv(values, "HAO_MCP_ALLOWED_ORIGINS")
        for origin in origins:
            _https_url(origin, field="HAO_MCP_ALLOWED_ORIGINS", production=production)
            if origin == "*" or origin.startswith("*."):
                raise ValueError("MCP_ALLOWED_ORIGINS_WILDCARD_NOT_ALLOWED")

        oauth_issuer_url = _https_url(
            _required(values, "HAO_OAUTH_ISSUER_URL"),
            field="HAO_OAUTH_ISSUER_URL",
            production=production,
        )
        oauth_resource_url = _https_url(
            _required(values, "HAO_OAUTH_RESOURCE_URL"),
            field="HAO_OAUTH_RESOURCE_URL",
            production=production,
        )
        if oauth_resource_url != public_mcp_url:
            raise ValueError("OAUTH_RESOURCE_MUST_EQUAL_PUBLIC_MCP_URL")

        oauth_audience = _required(values, "HAO_OAUTH_AUDIENCE")
        expected_hao_subject = _required(values, "HAO_EXPECTED_SUBJECT")
        attestation_key_id = _required(values, "HAO_ATTESTATION_KEY_ID")
        attestation_secret = _required(values, "HAO_ATTESTATION_SECRET")
        if len(attestation_secret.encode("utf-8")) < 32:
            raise ValueError("ATTESTATION_SECRET_MIN_32_BYTES")

        if production and region != "asia-east1":
            raise ValueError("PRODUCTION_REGION_MUST_BE_ASIA_EAST1")

        return cls(
            environment=environment,
            role=role,
            region=region,
            public_mcp_url=public_mcp_url,
            mcp_allowed_hosts=hosts,
            mcp_allowed_origins=origins,
            database_url=database_url,
            temporal_endpoint=temporal_endpoint,
            temporal_namespace=temporal_namespace,
            temporal_task_queue=temporal_task_queue,
            temporal_api_key=temporal_api_key,
            oauth_issuer_url=oauth_issuer_url,
            oauth_resource_url=oauth_resource_url,
            oauth_audience=oauth_audience,
            expected_hao_subject=expected_hao_subject,
            attestation_key_id=attestation_key_id,
            attestation_secret=attestation_secret,
            otel_endpoint=otel_endpoint,
        )
