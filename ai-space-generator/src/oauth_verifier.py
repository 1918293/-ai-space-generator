from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Protocol
from urllib.error import URLError

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError
from mcp.server.auth.provider import AccessToken, TokenVerifier


class SigningKeyResolver(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class JWKSAccessTokenVerifier(TokenVerifier):
    """Fail-closed JWT access-token verifier for the Runtime v2 MCP resource.

    Cryptographic verification is delegated to PyJWT/PyJWKClient. The verifier
    fixes the accepted asymmetric algorithms in trusted configuration; it never
    chooses an algorithm from attacker-controlled token headers.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: Sequence[str] = ("RS256",),
        leeway_seconds: int = 30,
        signing_key_resolver: SigningKeyResolver | None = None,
    ) -> None:
        self._issuer = issuer.strip()
        self._audience = audience.strip()
        self._jwks_url = jwks_url.strip()
        self._algorithms = tuple(item.strip() for item in algorithms if item.strip())
        self._leeway_seconds = int(leeway_seconds)

        if not self._issuer:
            raise ValueError("OAUTH_ISSUER_REQUIRED")
        if not self._audience:
            raise ValueError("OAUTH_AUDIENCE_REQUIRED")
        if not self._jwks_url:
            raise ValueError("OAUTH_JWKS_URL_REQUIRED")
        if not self._algorithms:
            raise ValueError("OAUTH_ALLOWED_ALGORITHMS_REQUIRED")
        if any(not alg.startswith(("RS", "ES", "Ed")) for alg in self._algorithms):
            raise ValueError("OAUTH_ASYMMETRIC_ALGORITHM_REQUIRED")
        if self._leeway_seconds < 0 or self._leeway_seconds > 300:
            raise ValueError("OAUTH_LEEWAY_OUT_OF_RANGE")

        self._resolver = signing_key_resolver or PyJWKClient(
            self._jwks_url,
            cache_jwk_set=True,
            lifespan=300,
            cache_keys=True,
            max_cached_keys=16,
            timeout=10,
        )

    @staticmethod
    def _scopes(claims: dict[str, Any]) -> list[str] | None:
        raw = claims.get("scope", "")
        if raw is None:
            return []
        if not isinstance(raw, str):
            return None
        result: list[str] = []
        for scope in raw.split():
            scope = scope.strip()
            if scope and scope not in result:
                result.append(scope)
        return result

    @staticmethod
    def _client_id(claims: dict[str, Any]) -> str:
        # Auth0 commonly emits `azp`; other providers may emit `client_id`.
        value = claims.get("azp") or claims.get("client_id") or ""
        return value.strip() if isinstance(value, str) else ""

    def _verify_sync(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._resolver.get_signing_key_from_jwt(token)
            key = getattr(signing_key, "key", signing_key)
            claims = jwt.decode(
                token,
                key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "verify_sub": True,
                },
            )
        except (PyJWTError, PyJWKClientError, URLError, TimeoutError, OSError):
            return None

        subject = claims.get("sub")
        expires_at = claims.get("exp")
        client_id = self._client_id(claims)
        scopes = self._scopes(claims)

        if not isinstance(subject, str) or not subject.strip():
            return None
        if not isinstance(expires_at, int):
            return None
        if not client_id:
            return None
        if scopes is None:
            return None

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            resource=self._audience,
            subject=subject.strip(),
            claims=claims,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        token = (token or "").strip()
        if not token:
            return None
        # PyJWKClient performs synchronous HTTP/network work. Keep it off the
        # event loop used by the MCP ASGI application.
        return await asyncio.to_thread(self._verify_sync, token)
