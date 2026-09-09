from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings


def _normalize(values: Iterable[str], *, field: str) -> list[str]:
    result: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        if item == "*" or item.startswith("*."):
            raise ValueError(f"{field}_WILDCARD_NOT_ALLOWED")
        if item not in result:
            result.append(item)
    if not result:
        raise ValueError(f"{field}_REQUIRED")
    return result


def build_mcp_http_app(
    mcp: MCPServer,
    *,
    allowed_hosts: Iterable[str],
    allowed_origins: Iterable[str] = (),
) -> Any:
    """Create the deployable Streamable HTTP app with an explicit network boundary.

    MCP SDK defaults to localhost-only DNS-rebinding protection. Runtime v2 must
    never silently disable that protection merely to make a remote hostname work.
    Production therefore has to declare the exact public Host values it serves.

    Browser Origins are separately allowlisted when present. An empty origin list
    is valid for non-browser MCP clients, which ordinarily send no Origin header.
    """
    hosts = _normalize(allowed_hosts, field="MCP_ALLOWED_HOSTS")
    origins = []
    for origin in allowed_origins:
        item = str(origin).strip()
        if not item:
            continue
        if item == "*" or item.startswith("*."):
            raise ValueError("MCP_ALLOWED_ORIGINS_WILDCARD_NOT_ALLOWED")
        if item not in origins:
            origins.append(item)

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )
    return mcp.streamable_http_app(transport_security=security)
