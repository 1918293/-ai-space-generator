from __future__ import annotations

import hashlib
import json
import re
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


_SECRET_VERSION_RE = re.compile(
    r"^projects/[^/]+/secrets/[^/]+/versions/(?P<version>[1-9][0-9]*)$"
)
_COMMON_REQUIRED_PRODUCTION_SECRET_ENV_KEYS = frozenset(
    {
        "HAO_TEMPORAL_API_KEY",
    }
)
_API_REQUIRED_PRODUCTION_SECRET_ENV_KEYS = frozenset(
    {
        "HAO_ATTESTATION_SECRET",
        "HAO_MCP_REQUEST_STATE_KEYS",
    }
)


def _required(values: dict[str, str], key: str) -> str:
    value = str(values.get(key, "")).strip()
    if not value:
        raise ValueError(f"MISSING_CONFIG:{key}")
    return value


def _positive_int(values: dict[str, str], key: str, *, production: bool, default: int) -> int:
    raw = _required(values, key) if production else str(values.get(key, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"INVALID_INTEGER_CONFIG:{key}") from exc
    if value < 1:
        raise ValueError(f"POSITIVE_INTEGER_REQUIRED:{key}")
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
    allow_loopback_http: bool = False,
) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"INVALID_URL:{field}")
    loopback_http = (
        allow_loopback_http
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    )
    if production and parsed.scheme != "https" and not loopback_http:
        raise ValueError(f"HTTPS_REQUIRED:{field}")
    return value.rstrip("/") if normalize_trailing_slash else value


def _database_identity(value: str) -> str:
    """Return a credential-free database endpoint identity for compatibility checks."""
    parsed = urlparse(value)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    path = parsed.path or ""
    return f"{parsed.scheme}://{host}{port}{path}"


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


def _secret_bindings(
    values: dict[str, str],
    *,
    production: bool,
    role: RuntimeRole,
) -> tuple[tuple[str, str], ...]:
    raw = str(values.get("HAO_SECRET_BINDINGS_JSON", "")).strip()
    if not raw:
        if production:
            raise ValueError("MISSING_CONFIG:HAO_SECRET_BINDINGS_JSON")
        return ()
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("INVALID_JSON_CONFIG:HAO_SECRET_BINDINGS_JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("HAO_SECRET_BINDINGS_OBJECT_REQUIRED")
    normalized: list[tuple[str, str]] = []
    for env_key, resource in decoded.items():
        env_key = str(env_key).strip()
        resource = str(resource).strip()
        if not env_key or not resource:
            raise ValueError("INVALID_SECRET_BINDING")
        match = _SECRET_VERSION_RE.fullmatch(resource)
        if production and match is None:
            raise ValueError(f"SECRET_BINDING_EXPLICIT_VERSION_REQUIRED:{env_key}")
        normalized.append((env_key, resource))
    required = set(_COMMON_REQUIRED_PRODUCTION_SECRET_ENV_KEYS)
    if role == RuntimeRole.API:
        required.update(_API_REQUIRED_PRODUCTION_SECRET_ENV_KEYS)
    bound_keys = {key for key, _ in normalized}
    missing = sorted(required - bound_keys)
    if production and missing:
        raise ValueError("MISSING_SECRET_BINDINGS:" + ",".join(missing))
    return tuple(sorted(normalized))


def _attestation_previous_keys(
    values: dict[str, str],
    *,
    current_key_id: str,
    current_secret: str,
    production: bool,
    bound_secret_keys: set[str],
) -> tuple[tuple[str, str], ...]:
    raw = str(values.get("HAO_ATTESTATION_PREVIOUS_KEYS_JSON", "")).strip()
    if not raw:
        return ()
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("INVALID_JSON_CONFIG:HAO_ATTESTATION_PREVIOUS_KEYS_JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("ATTESTATION_PREVIOUS_KEYS_OBJECT_REQUIRED")
    if production and decoded and "HAO_ATTESTATION_PREVIOUS_KEYS_JSON" not in bound_secret_keys:
        raise ValueError("ATTESTATION_PREVIOUS_KEYS_SECRET_BINDING_REQUIRED")

    normalized: list[tuple[str, str]] = []
    seen_secrets = {current_secret}
    for key_id, secret in decoded.items():
        key_id = str(key_id).strip()
        secret = str(secret)
        if not key_id:
            raise ValueError("ATTESTATION_PREVIOUS_KEY_ID_REQUIRED")
        if key_id == current_key_id:
            raise ValueError("ATTESTATION_PREVIOUS_KEY_ID_CONFLICT")
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("ATTESTATION_PREVIOUS_SECRET_MIN_32_BYTES")
        if secret in seen_secrets:
            raise ValueError("ATTESTATION_SECRET_REUSE_NOT_ALLOWED")
        seen_secrets.add(secret)
        normalized.append((key_id, secret))
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class RuntimeSettings:
    environment: RuntimeEnvironment
    role: RuntimeRole
    region: str
    release_id: str
    deployment_id: str
    public_mcp_url: str
    mcp_allowed_hosts: tuple[str, ...]
    mcp_allowed_origins: tuple[str, ...]
    mcp_request_state_keys: tuple[str, ...]
    mcp_request_state_audience: str
    database_url: str
    database_schema_version: int
    database_rpo_seconds: int
    database_rto_seconds: int
    temporal_endpoint: str
    temporal_namespace: str
    temporal_task_queue: str
    temporal_worker_version: str
    temporal_api_key: str
    worker_instance_count: int
    graceful_shutdown_seconds: int
    oauth_issuer_url: str
    oauth_resource_url: str
    oauth_audience: str
    oauth_jwks_url: str
    expected_hao_subject: str
    attestation_key_id: str
    attestation_secret: str
    attestation_previous_keys: tuple[tuple[str, str], ...]
    secret_bindings: tuple[tuple[str, str], ...]
    otel_endpoint: str

    @property
    def request_state_key_bytes(self) -> tuple[bytes, ...]:
        return tuple(bytes.fromhex(value) for value in self.mcp_request_state_keys)

    @property
    def secret_binding_map(self) -> dict[str, str]:
        return dict(self.secret_bindings)

    @property
    def attestation_previous_key_map(self) -> dict[str, str]:
        return dict(self.attestation_previous_keys)

    @property
    def deployment_identity(self) -> tuple[tuple[str, str], ...]:
        """Non-secret compatibility identity that API and worker must share."""
        return (
            ("environment", self.environment.value),
            ("region", self.region),
            ("release_id", self.release_id),
            ("deployment_id", self.deployment_id),
            ("public_mcp_url", self.public_mcp_url),
            ("database_identity", _database_identity(self.database_url)),
            ("database_schema_version", str(self.database_schema_version)),
            ("temporal_endpoint", self.temporal_endpoint),
            ("temporal_namespace", self.temporal_namespace),
            ("temporal_task_queue", self.temporal_task_queue),
            ("temporal_worker_version", self.temporal_worker_version),
            ("oauth_issuer_url", self.oauth_issuer_url),
            ("oauth_resource_url", self.oauth_resource_url),
            ("oauth_audience", self.oauth_audience),
            ("expected_hao_subject", self.expected_hao_subject),
            ("attestation_key_id", self.attestation_key_id),
            ("mcp_request_state_audience", self.mcp_request_state_audience),
        )

    @property
    def deployment_identity_fingerprint(self) -> str:
        body = json.dumps(
            dict(self.deployment_identity),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(body).hexdigest()

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
        release_id = _required(values, "HAO_RELEASE_ID") if production else str(
            values.get("HAO_RELEASE_ID", "dev")
        ).strip() or "dev"
        deployment_id = _required(values, "HAO_DEPLOYMENT_ID") if production else str(
            values.get("HAO_DEPLOYMENT_ID", "local")
        ).strip() or "local"

        database_url = _required(values, "HAO_DATABASE_URL")
        database_schema_version = _positive_int(
            values, "HAO_DATABASE_SCHEMA_VERSION", production=production, default=1
        )
        database_rpo_seconds = _positive_int(
            values, "HAO_DATABASE_RPO_SECONDS", production=production, default=300
        )
        database_rto_seconds = _positive_int(
            values, "HAO_DATABASE_RTO_SECONDS", production=production, default=3600
        )
        if production and database_rpo_seconds > 300:
            raise ValueError("PRODUCTION_RPO_MUST_BE_300_SECONDS_OR_LESS")

        temporal_endpoint = _required(values, "HAO_TEMPORAL_ENDPOINT")
        temporal_namespace = _required(values, "HAO_TEMPORAL_NAMESPACE")
        temporal_task_queue = _required(values, "HAO_TEMPORAL_TASK_QUEUE")
        temporal_worker_version = (
            _required(values, "HAO_TEMPORAL_WORKER_VERSION")
            if production
            else str(values.get("HAO_TEMPORAL_WORKER_VERSION", release_id)).strip() or release_id
        )
        temporal_api_key = _required(values, "HAO_TEMPORAL_API_KEY") if production else str(
            values.get("HAO_TEMPORAL_API_KEY", "")
        ).strip()
        worker_instance_count = _positive_int(
            values, "HAO_WORKER_INSTANCE_COUNT", production=production, default=1
        )
        graceful_shutdown_seconds = _positive_int(
            values, "HAO_GRACEFUL_SHUTDOWN_SECONDS", production=production, default=8
        )
        if production and graceful_shutdown_seconds > 9:
            raise ValueError("GRACEFUL_SHUTDOWN_MUST_FIT_CLOUD_RUN_SIGTERM_WINDOW")

        otel_endpoint = _required(values, "HAO_OTEL_ENDPOINT") if production else str(
            values.get("HAO_OTEL_ENDPOINT", "")
        ).strip()
        if otel_endpoint:
            otel_endpoint = _validate_url(
                otel_endpoint,
                field="HAO_OTEL_ENDPOINT",
                production=production,
                normalize_trailing_slash=True,
                allow_loopback_http=True,
            )

        if production and not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("PRODUCTION_POSTGRES_REQUIRED")

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

        request_state_keys = (
            _request_state_keys(values) if role == RuntimeRole.API else ()
        )
        request_state_audience = _required(values, "HAO_MCP_REQUEST_STATE_AUDIENCE")

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

        secret_bindings = _secret_bindings(
            values,
            production=production,
            role=role,
        )
        bound_secret_keys = {key for key, _ in secret_bindings}
        if (
            production
            and urlparse(database_url).password is not None
            and "HAO_DATABASE_URL" not in bound_secret_keys
        ):
            raise ValueError("DATABASE_URL_PASSWORD_SECRET_BINDING_REQUIRED")

        if role == RuntimeRole.API:
            attestation_secret = _required(values, "HAO_ATTESTATION_SECRET")
            if len(attestation_secret.encode("utf-8")) < 32:
                raise ValueError("ATTESTATION_SECRET_MIN_32_BYTES")
            attestation_previous_keys = _attestation_previous_keys(
                values,
                current_key_id=attestation_key_id,
                current_secret=attestation_secret,
                production=production,
                bound_secret_keys=bound_secret_keys,
            )
        else:
            attestation_secret = ""
            attestation_previous_keys = ()

        return cls(
            environment=environment,
            role=role,
            region=region,
            release_id=release_id,
            deployment_id=deployment_id,
            public_mcp_url=public_mcp_url,
            mcp_allowed_hosts=hosts,
            mcp_allowed_origins=origins,
            mcp_request_state_keys=request_state_keys,
            mcp_request_state_audience=request_state_audience,
            database_url=database_url,
            database_schema_version=database_schema_version,
            database_rpo_seconds=database_rpo_seconds,
            database_rto_seconds=database_rto_seconds,
            temporal_endpoint=temporal_endpoint,
            temporal_namespace=temporal_namespace,
            temporal_task_queue=temporal_task_queue,
            temporal_worker_version=temporal_worker_version,
            temporal_api_key=temporal_api_key,
            worker_instance_count=worker_instance_count,
            graceful_shutdown_seconds=graceful_shutdown_seconds,
            oauth_issuer_url=oauth_issuer_url,
            oauth_resource_url=oauth_resource_url,
            oauth_audience=oauth_audience,
            oauth_jwks_url=oauth_jwks_url,
            expected_hao_subject=expected_hao_subject,
            attestation_key_id=attestation_key_id,
            attestation_secret=attestation_secret,
            attestation_previous_keys=attestation_previous_keys,
            secret_bindings=secret_bindings,
            otel_endpoint=otel_endpoint,
        )