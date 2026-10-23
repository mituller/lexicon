"""FastAPI service (deployed to AWS Lambda via Mangum).

Endpoints are used by both the desktop app (through the Rust shell) and, later, the web/PWA.
Auth is a Cognito JWT; the caller's ``sub`` scopes every read/write.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from pydantic import BaseModel, Field

from .auth import current_user_id
from .config import get_settings
from .decks import DeckService, DeckSummary, DueTerm, Quiz, apply_quiz_results
from .dynamo import ProgressStore
from .logging_setup import configure_logging, install_request_logging, json_event
from .models import ProgressRecord, Rating, SearchHit, UserAggregate, UserList
from .privacy import build_export
from .retriever import Retriever
from .session import (
    AnswerResult,
    Exercise,
    SessionEngine,
    SessionNotFound,
    SessionStarted,
    SessionSummary,
    StaleExercise,
)
from .srs import get_scheduler
from .tutor_agent import TutorAgent, TutorMode
from .vectorstore import VectorStore

logger = logging.getLogger("lexicon")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    domains: list[str] | None = None
    difficulty_tiers: list[str] | None = None
    cefr_levels: list[str] | None = None
    list_ids: list[str] | None = None
    top_k: int = Field(default=8, ge=1, le=50)


class TutorRequest(SearchRequest):
    mode: TutorMode = TutorMode.EXPLAIN


class TutorResponse(BaseModel):
    answer: str
    hits: list[SearchHit]


class StartDeckRequest(BaseModel):
    domain: str
    difficulty_tier: str


class QuizAnswersRequest(BaseModel):
    answers: list[dict[str, Any]]


class StartDeckResponse(BaseModel):
    created: int


class AnswerRequest(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=64)
    choice: int | None = Field(default=None, ge=0, le=10)
    text: str | None = Field(default=None, max_length=100)


class ExplainRequest(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=64)


class NextResponse(BaseModel):
    """Exactly one is set: the next exercise, or the summary once the session is over."""

    exercise: Exercise | None = None
    summary: SessionSummary | None = None


class ProgressUpdate(BaseModel):
    rating: Rating
    note: str | None = None
    starred: bool | None = None


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(title="lexicon", version="0.1.0")
    install_request_logging(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/v1/me")
    def me(user_id: str = Depends(current_user_id)) -> dict[str, str]:
        """Return the caller's resolved user id (used to confirm login end-to-end)."""
        return {"user_id": user_id}

    @app.post("/v1/search")
    def search(
        request: SearchRequest,
        user_id: str = Depends(current_user_id),
    ) -> list[SearchHit]:
        return Retriever().search(
            request.query,
            user_id=user_id,
            domains=request.domains,
            difficulty_tiers=request.difficulty_tiers,
            cefr_levels=request.cefr_levels,
            list_ids=request.list_ids,
            top_k=request.top_k,
        )

    @app.post("/v1/tutor")
    def tutor(
        request: TutorRequest,
        user_id: str = Depends(current_user_id),
    ) -> TutorResponse:
        hits = Retriever().search(
            request.query,
            user_id=user_id,
            domains=request.domains,
            difficulty_tiers=request.difficulty_tiers,
            cefr_levels=request.cefr_levels,
            list_ids=request.list_ids,
            top_k=request.top_k,
        )
        answer = TutorAgent().respond(request.query, hits, mode=request.mode)
        return TutorResponse(answer=answer, hits=hits)

    @app.get("/v1/progress")
    def list_progress(user_id: str = Depends(current_user_id)) -> list[ProgressRecord]:
        return ProgressStore().list_progress(user_id)

    @app.put("/v1/progress/{term_id}")
    def review_term(
        term_id: str,
        update: ProgressUpdate,
        user_id: str = Depends(current_user_id),
    ) -> ProgressRecord:
        store = ProgressStore()
        record = store.get_progress(user_id, term_id) or get_scheduler().new_record(user_id, term_id)
        record = get_scheduler().review(record, update.rating)
        if update.note is not None:
            record.note = update.note
        if update.starred is not None:
            record.starred = update.starred
        store.put_progress(record)
        store.put_aggregate(store.recompute_aggregate(user_id))
        return record

    @app.get("/v1/stats")
    def stats(user_id: str = Depends(current_user_id)) -> UserAggregate:
        return ProgressStore().get_aggregate(user_id)

    # --- decks & retention ---

    @app.get("/v1/decks")
    def list_decks() -> list[DeckSummary]:
        return DeckService().list_decks()

    @app.get("/v1/vocabulary")
    def vocabulary(
        domain: str,
        difficulty_tier: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Terms in a deck (for browsing and distractor generation)."""
        return DeckService().deck_terms(
            domain=domain, difficulty_tier=difficulty_tier, limit=limit
        )

    @app.post("/v1/deck/start")
    def start_deck(
        request: StartDeckRequest,
        user_id: str = Depends(current_user_id),
    ) -> StartDeckResponse:
        created = DeckService().start_deck(
            ProgressStore(), user_id, domain=request.domain, difficulty_tier=request.difficulty_tier
        )
        return StartDeckResponse(created=created)

    @app.get("/v1/review/due")
    def review_due(
        limit: int = 20,
        user_id: str = Depends(current_user_id),
    ) -> list[DueTerm]:
        return DeckService().due_terms(ProgressStore(), user_id, limit=limit)

    @app.get("/v1/quiz")
    def quiz(
        domain: str,
        difficulty_tier: str,
        count: int = 10,
    ) -> Quiz:
        return DeckService().make_quiz(
            domain=domain, difficulty_tier=difficulty_tier, count=count
        )

    @app.post("/v1/quiz/grade")
    def grade_quiz(
        request: QuizAnswersRequest,
        user_id: str = Depends(current_user_id),
    ) -> dict[str, int]:
        return apply_quiz_results(ProgressStore(), user_id, request.answers)

    # --- adaptive sessions ---

    @app.post("/v1/session")
    def start_session(user_id: str = Depends(current_user_id)) -> SessionStarted:
        return SessionEngine().start(user_id)

    @app.get("/v1/session/{session_id}/next")
    def next_exercise(session_id: str, user_id: str = Depends(current_user_id)) -> NextResponse:
        with _session_errors():
            result = SessionEngine().next_exercise(user_id, session_id)
        if isinstance(result, SessionSummary):
            return NextResponse(summary=result)
        return NextResponse(exercise=result)

    @app.post("/v1/session/{session_id}/answer")
    def answer(
        session_id: str, request: AnswerRequest, user_id: str = Depends(current_user_id)
    ) -> AnswerResult:
        with _session_errors():
            return SessionEngine().answer(
                user_id, session_id, request.exercise_id, choice=request.choice, text=request.text
            )

    @app.post("/v1/session/{session_id}/explain")
    def explain(
        session_id: str, request: ExplainRequest, user_id: str = Depends(current_user_id)
    ) -> dict[str, str]:
        with _session_errors():
            return {"explanation": SessionEngine().explain(user_id, session_id, request.exercise_id)}

    @app.post("/v1/pool/prefetch")
    def prefetch(user_id: str = Depends(current_user_id)) -> dict[str, int]:
        """Grow the learner's next band; clients call this without awaiting the result."""
        return {"added": SessionEngine().prefetch(user_id)}

    @app.get("/v1/lists")
    def list_lists(user_id: str = Depends(current_user_id)) -> list[UserList]:
        return ProgressStore().list_lists(user_id)

    @app.get("/v1/export")
    def export_account(user_id: str = Depends(current_user_id)) -> dict[str, Any]:
        """Return a complete, portable export of the caller's data."""
        store = ProgressStore()
        return build_export(
            user_id,
            progress=store.list_progress(user_id),
            lists=store.list_lists(user_id),
            aggregate=store.get_aggregate(user_id),
        )

    @app.delete("/v1/account")
    def delete_account(user_id: str = Depends(current_user_id)) -> dict[str, Any]:
        """Delete all of the caller's data (progress, lists, and owned vectors)."""
        removed_items = ProgressStore().delete_all(user_id)
        removed_vectors = 0
        try:
            removed_vectors = VectorStore().delete_by_owner(user_id)
        except Exception:  # noqa: BLE001 - best-effort; never block account deletion
            logger.exception(json_event("vector_delete_failed", user_id=user_id))
        logger.info(json_event("account_deleted", user_id=user_id))
        return {
            "deleted_items": removed_items,
            "deleted_vectors": removed_vectors,
        }

    @app.post("/v1/lists")
    def create_list(user_list: UserList, user_id: str = Depends(current_user_id)) -> UserList:
        server_list = user_list.model_copy(update={"user_id": user_id})
        ProgressStore().put_list(server_list)
        return server_list

    return app


@contextmanager
def _session_errors() -> Iterator[None]:
    try:
        yield
    except SessionNotFound as error:
        raise HTTPException(status_code=404, detail="session not found") from error
    except StaleExercise as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


app = create_app()

# AWS Lambda entry point (Function URL -> Mangum -> FastAPI).
handler: Any = Mangum(app)
