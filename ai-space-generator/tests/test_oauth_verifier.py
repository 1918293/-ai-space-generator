import asyncio
import time
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientError

from src.oauth_verifier import JWKSAccessTokenVerifier


ISSUER = "https://issuer.example.com/"
AUDIENCE = "https://runtime.example.com/mcp"


@dataclass
class SigningKey:
    key: object


class StaticResolver:
    def __init__(self, key):
        self.key = key
        self.calls = 0

    def get_signing_key_from_jwt(self, token):
        self.calls += 1
        return SigningKey(self.key)


class FailingResolver:
    def get_signing_key_from_jwt(self, token):
        raise PyJWKClientError("jwks unavailable")


def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def claims(**overrides):
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "sub": "hao-user",
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "azp": "chatgpt-runtime-client",
        "scope": "hao:access hao:read hao:read",
    }
    payload.update(overrides)
    return payload


def encode(payload, private, *, algorithm="RS256"):
    return jwt.encode(payload, private, algorithm=algorithm, headers={"kid": "key-1"})


def verifier(public, *, issuer=ISSUER, audience=AUDIENCE, resolver=None):
    return JWKSAccessTokenVerifier(
        issuer=issuer,
        audience=audience,
        jwks_url="https://issuer.example.com/.well-known/jwks.json",
        signing_key_resolver=resolver or StaticResolver(public),
    )


def test_valid_rs256_token_maps_verified_identity_and_scopes():
    private, public = keys()
    result = asyncio.run(verifier(public).verify_token(encode(claims(), private)))
    assert result is not None
    assert result.subject == "hao-user"
    assert result.client_id == "chatgpt-runtime-client"
    assert result.scopes == ["hao:access", "hao:read"]
    assert result.resource == AUDIENCE
    assert result.claims["iss"] == ISSUER


def test_issuer_is_exact_and_trailing_slash_mismatch_is_rejected():
    private, public = keys()
    token = encode(claims(iss="https://issuer.example.com"), private)
    assert asyncio.run(verifier(public).verify_token(token)) is None


def test_wrong_audience_and_expired_token_are_rejected():
    private, public = keys()
    wrong_audience = encode(claims(aud="https://other.example.com/mcp"), private)
    assert asyncio.run(verifier(public).verify_token(wrong_audience)) is None

    expired = encode(claims(exp=int(time.time()) - 600), private)
    assert asyncio.run(verifier(public).verify_token(expired)) is None


def test_algorithm_is_fixed_by_trusted_configuration_not_token_header():
    _, public = keys()
    hs_token = jwt.encode(claims(), "shared-secret", algorithm="HS256", headers={"kid": "key-1"})
    assert asyncio.run(verifier(public).verify_token(hs_token)) is None


def test_scope_claim_must_be_oauth_space_delimited_string():
    private, public = keys()
    token = encode(claims(scope=["hao:access", "hao:read"]), private)
    assert asyncio.run(verifier(public).verify_token(token)) is None


def test_subject_and_client_identity_are_both_required():
    private, public = keys()
    missing_subject = claims()
    missing_subject.pop("sub")
    assert asyncio.run(verifier(public).verify_token(encode(missing_subject, private))) is None

    missing_client = claims()
    missing_client.pop("azp")
    assert asyncio.run(verifier(public).verify_token(encode(missing_client, private))) is None


def test_jwks_resolution_failure_fails_closed():
    private, public = keys()
    token = encode(claims(), private)
    check = verifier(public, resolver=FailingResolver())
    assert asyncio.run(check.verify_token(token)) is None


def test_verifier_configuration_rejects_symmetric_algorithm_and_excessive_leeway():
    _, public = keys()
    import pytest

    with pytest.raises(ValueError, match="OAUTH_ASYMMETRIC_ALGORITHM_REQUIRED"):
        JWKSAccessTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url="https://issuer.example.com/jwks.json",
            algorithms=("HS256",),
            signing_key_resolver=StaticResolver(public),
        )

    with pytest.raises(ValueError, match="OAUTH_LEEWAY_OUT_OF_RANGE"):
        JWKSAccessTokenVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            jwks_url="https://issuer.example.com/jwks.json",
            leeway_seconds=301,
            signing_key_resolver=StaticResolver(public),
        )
