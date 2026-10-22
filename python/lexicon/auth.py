"""Cognito JWT verification.

The API derives ``user_id`` from the verified JWT ``sub`` claim — never from client input.
When no user pool is configured (local dev), a dev user is assumed.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import jwt
from fastapi import Header, HTTPException, status

from .config import get_settings

DEV_USER_ID = "dev-user"


@lru_cache(maxsize=1)
def _jwk_client() -> Any:
    # PyJWKClient ships with PyJWT's `crypto` extra; typed loosely to avoid stub gaps.
    settings = get_settings()
    return jwt.PyJWKClient(f"{settings.cognito_issuer}/.well-known/jwks.json")  # type: ignore[attr-defined]


def verify_token(token: str) -> dict[str, Any]:
    """Validate a Cognito ID/access token and return its claims."""
    settings = get_settings()
    if not settings.cognito_user_pool_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cognito user pool not configured",
        )
    signing_key = _jwk_client().get_signing_key_from_jwt(token).key
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=settings.cognito_issuer,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {error}",
        ) from error
    return claims


def current_user_id(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency: resolve the caller's ``sub`` (or a dev user locally)."""
    settings = get_settings()
    if not settings.cognito_user_pool_id:
        return DEV_USER_ID
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    claims = verify_token(authorization.split(" ", 1)[1])
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing sub")
    return str(sub)
