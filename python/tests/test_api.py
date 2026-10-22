"""API tests.

Auth is overridden so results don't depend on whether the developer has a real `python/.env`
with Cognito configured (with it set, requests correctly return 401 before body validation).
"""

import pytest
from fastapi.testclient import TestClient

from lexicon.api import app
from lexicon.auth import current_user_id

client = TestClient(app)


@pytest.fixture(autouse=True)
def _authenticated_as_test_user():
    app.dependency_overrides[current_user_id] = lambda: "test-user"
    yield
    app.dependency_overrides.clear()


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_me_returns_dependency_user() -> None:
    response = client.get("/v1/me")
    assert response.status_code == 200
    assert response.json()["user_id"] == "test-user"


def test_search_rejects_empty_query() -> None:
    assert client.post("/v1/search", json={"query": ""}).status_code == 422


def test_search_rejects_overlong_query() -> None:
    assert client.post("/v1/search", json={"query": "x" * 1001}).status_code == 422


def test_search_rejects_missing_query() -> None:
    assert client.post("/v1/search", json={}).status_code == 422


def test_tutor_rejects_unknown_mode() -> None:
    response = client.post("/v1/tutor", json={"query": "hello", "mode": "nonsense"})
    assert response.status_code == 422
