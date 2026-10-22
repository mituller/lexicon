import base64
import hashlib
import urllib.parse

from lexicon.cognito import build_authorize_url, build_logout_url, generate_pkce_pair


def test_pkce_challenge_is_s256_of_verifier() -> None:
    verifier, challenge = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    assert challenge == expected
    assert "=" not in challenge


def test_pkce_pairs_are_unique() -> None:
    assert generate_pkce_pair()[0] != generate_pkce_pair()[0]


def test_build_authorize_url_contains_pkce_params() -> None:
    _, challenge = generate_pkce_pair()
    url = build_authorize_url(
        domain="lexicon-123.auth.us-east-1.amazoncognito.com",
        client_id="client123",
        redirect_uri="http://127.0.0.1:4200/callback",
        code_challenge=challenge,
        state="xyz",
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client123"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [challenge]
    assert query["redirect_uri"] == ["http://127.0.0.1:4200/callback"]
    assert query["state"] == ["xyz"]


def test_build_logout_url() -> None:
    url = build_logout_url(
        domain="lexicon-123.auth.us-east-1.amazoncognito.com",
        client_id="client123",
        logout_uri="http://localhost:5173",
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["client_id"] == ["client123"]
    assert query["logout_uri"] == ["http://localhost:5173"]
