"""GitHub Actions OIDC verification for result uploads."""

from dataclasses import dataclass
from time import monotonic
from typing import Annotated, Any, cast

import httpx
import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from jwt.algorithms import RSAAlgorithm

from product_api.config import Settings, get_settings

GITHUB_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_JWKS_URL = f"{GITHUB_ISSUER}/.well-known/jwks"
logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class UploadIdentity:
    repository_id: int
    full_name: str
    run_id: int
    run_attempt: int
    sha: str
    ref: str


class GithubOidcVerifier:
    """Verifies GitHub JWTs using a short-lived, process-local JWKS cache."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks: dict[str, Any] | None = None
        self._jwks_expires_at = 0.0

    def _keys(self) -> dict[str, Any]:
        if self._jwks is None or monotonic() >= self._jwks_expires_at:
            response = httpx.get(GITHUB_JWKS_URL, timeout=5.0)
            response.raise_for_status()
            self._jwks = response.json()
            self._jwks_expires_at = monotonic() + self._settings.oidc_jwks_ttl_seconds
        assert self._jwks is not None
        return self._jwks

    def verify(self, token: str) -> UploadIdentity:
        try:
            kid = jwt.get_unverified_header(token)["kid"]
            jwk = next(key for key in self._keys()["keys"] if key["kid"] == kid)
            claims = jwt.decode(
                token,
                cast(Any, RSAAlgorithm.from_jwk(jwk)),
                algorithms=["RS256"],
                issuer=GITHUB_ISSUER,
                audience=self._settings.oidc_audience,
                options={
                    "require": [
                        "exp",
                        "nbf",
                        "repository_id",
                        "run_id",
                        "run_attempt",
                        "sha",
                        "ref",
                    ]
                },
            )
            identity = UploadIdentity(
                repository_id=int(claims["repository_id"]),
                full_name=str(claims["repository"]),
                run_id=int(claims["run_id"]),
                run_attempt=int(claims["run_attempt"]),
                sha=str(claims["sha"]),
                ref=str(claims["ref"]),
            )
        except (KeyError, StopIteration, TypeError, ValueError, jwt.PyJWTError, httpx.HTTPError):
            logger.warning("oidc_authentication_failed", reason="invalid_token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid OIDC token"
            ) from None

        owner = identity.full_name.partition("/")[0]
        if (
            self._settings.allowed_repository_owners
            and owner not in self._settings.allowed_repository_owners
        ):
            logger.warning("oidc_authorization_denied", repository_id=identity.repository_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="repository owner is not allowed"
            )
        return identity


def get_oidc_verifier(settings: Annotated[Settings, Depends(get_settings)]) -> GithubOidcVerifier:
    return GithubOidcVerifier(settings)


def require_upload_auth(
    request: Request, verifier: Annotated[GithubOidcVerifier, Depends(get_oidc_verifier)]
) -> UploadIdentity:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        logger.warning("oidc_authentication_failed", reason="missing_bearer_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required"
        )
    return verifier.verify(token)
