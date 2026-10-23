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


class StubEngine:
    def next_exercise(self, user_id: str, session_id: str):
        from lexicon.session import Exercise, ExerciseType, SessionNotFound, SessionSummary

        if session_id == "missing":
            raise SessionNotFound(session_id)
        if session_id == "over":
            return SessionSummary(new_words=2)
        return Exercise(
            exercise_id="e1", session_id=session_id, type=ExerciseType.CHOOSE_WORD,
            prompt="definition", options=["a", "b", "c", "d"], kind="new", attempt=1, remaining=3,
        )

    def answer(self, *args, **kwargs):
        from lexicon.session import StaleExercise

        raise StaleExercise("that exercise is no longer current")


@pytest.fixture
def stub_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lexicon.api.SessionEngine", StubEngine)


@pytest.mark.usefixtures("stub_engine")
def test_session_next_returns_exercise_or_summary() -> None:
    body = client.get("/v1/session/s1/next").json()
    assert body["summary"] is None and body["exercise"]["options"] == ["a", "b", "c", "d"]
    assert "answer_index" not in body["exercise"]
    body = client.get("/v1/session/over/next").json()
    assert body["exercise"] is None and body["summary"]["new_words"] == 2


@pytest.mark.usefixtures("stub_engine")
def test_session_errors_map_to_http_status() -> None:
    assert client.get("/v1/session/missing/next").status_code == 404
    response = client.post("/v1/session/s1/answer", json={"exercise_id": "old", "choice": 1})
    assert response.status_code == 409


def test_session_answer_validates_input() -> None:
    assert client.post("/v1/session/s1/answer", json={"choice": 1}).status_code == 422
    assert client.post("/v1/session/s1/answer", json={"exercise_id": "e", "text": "x" * 101}).status_code == 422
