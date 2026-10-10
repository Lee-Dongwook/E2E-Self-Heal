"""Unit tests for GitHub Actions OIDC verification without network access."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.algorithms import RSAAlgorithm

from product_api.config import Settings
from product_api.oidc import GITHUB_ISSUER, GithubOidcVerifier


@pytest.fixture
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch, key: rsa.RSAPrivateKey) -> GithubOidcVerifier:
    public_jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    public_jwk["kid"] = "test-key"

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, list[dict[str, Any]]]:
            return {"keys": [public_jwk]}

    monkeypatch.setattr("product_api.oidc.httpx.get", lambda *args, **kwargs: Response())
    return GithubOidcVerifier(
        Settings(database_url="postgresql+psycopg://unused", oidc_audience="test")
    )


def _token(key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": GITHUB_ISSUER,
        "aud": "test",
        "exp": now + timedelta(minutes=5),
        "nbf": now - timedelta(seconds=1),
        "repository_id": "123456",
        "repository": "acme/storefront",
        "run_id": "98765",
        "run_attempt": "1",
        "sha": "a" * 40,
        "ref": "refs/heads/main",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def test_valid_github_token_exposes_signed_identity(
    verifier: GithubOidcVerifier, key: rsa.RSAPrivateKey
) -> None:
    identity = verifier.verify(_token(key))

    assert identity.repository_id == 123456
    assert identity.full_name == "acme/storefront"
    assert identity.run_id == 98765


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
        {"aud": "wrong"},
        {"iss": "https://issuer.example"},
    ],
)
def test_invalid_claims_are_rejected(
    verifier: GithubOidcVerifier, key: rsa.RSAPrivateKey, overrides: dict[str, Any]
) -> None:
    with pytest.raises(HTTPException) as error:
        verifier.verify(_token(key, **overrides))
    assert error.value.detail == "invalid OIDC token"


def test_bad_signature_is_rejected(verifier: GithubOidcVerifier, key: rsa.RSAPrivateKey) -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(HTTPException) as error:
        verifier.verify(_token(other_key))
    assert error.value.detail == "invalid OIDC token"
