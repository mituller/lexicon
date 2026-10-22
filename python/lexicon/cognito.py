"""Cognito Hosted UI helpers: PKCE, authorize/logout URLs, and code exchange.

The desktop shell and the web client both use OAuth 2.0 Authorization Code + PKCE against the
Cognito Hosted UI. These helpers are the single, testable definition of that flow; the Rust
shell mirrors them so it can drive the desktop loopback login.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEFAULT_SCOPES = ("openid", "email", "profile")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_pkce_pair() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(
    *,
    domain: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
    state: str | None = None,
) -> str:
    """Build the Hosted UI ``/oauth2/authorize`` URL."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state or secrets.token_urlsafe(16),
    }
    return f"https://{domain}/oauth2/authorize?{urllib.parse.urlencode(params)}"


def build_logout_url(*, domain: str, client_id: str, logout_uri: str) -> str:
    """Build the Hosted UI ``/logout`` URL."""
    params = {"client_id": client_id, "logout_uri": logout_uri}
    return f"https://{domain}/logout?{urllib.parse.urlencode(params)}"


@dataclass(frozen=True)
class TokenSet:
    """Tokens returned by the Cognito token endpoint."""

    id_token: str
    access_token: str
    refresh_token: str = ""
    expires_in: int = 0
    token_type: str = "Bearer"


def exchange_code(
    *,
    domain: str,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    timeout: float = 15.0,
) -> TokenSet:
    """Exchange an authorization code for tokens (public client, no secret)."""
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        f"https://{domain}/oauth2/token",
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:  # pragma: no cover - network path
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"token exchange failed ({error.code}): {detail}") from error

    return TokenSet(
        id_token=payload["id_token"],
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", ""),
        expires_in=int(payload.get("expires_in", 0)),
        token_type=payload.get("token_type", "Bearer"),
    )
