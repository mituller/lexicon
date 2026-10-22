"""Domain models shared across ingestion, retrieval, tutoring, and the API.

These pydantic models are the single definition of the vocabulary schema and the progress
record. See ``prompt.md`` §1a (L17–L18) and the data schema at the top of ``prompt.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Scope(StrEnum):
    """Vector visibility scope. S3 Vectors has no namespaces, so this is a metadata filter."""

    GLOBAL = "global"
    USER = "user"


class DifficultyTier(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class CefrLevel(StrEnum):
    PRE_A1 = "Pre-A1"
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


# CEFR -> coarse difficulty tier. See prompt.md Q-o.
CEFR_TO_TIER: dict[CefrLevel, DifficultyTier] = {
    CefrLevel.PRE_A1: DifficultyTier.BEGINNER,
    CefrLevel.A1: DifficultyTier.BEGINNER,
    CefrLevel.A2: DifficultyTier.BEGINNER,
    CefrLevel.B1: DifficultyTier.INTERMEDIATE,
    CefrLevel.B2: DifficultyTier.INTERMEDIATE,
    CefrLevel.C1: DifficultyTier.ADVANCED,
    CefrLevel.C2: DifficultyTier.ADVANCED,
}


class FilterableMetadata(BaseModel):
    """Small, indexed metadata used for boolean/range filtering in S3 Vectors queries."""

    domain: str = Field(description='e.g. "general", "ai"')
    subfield: str = Field(default="", description='e.g. "llm_architecture", "linguistics"')
    difficulty_tier: str = Field(description="beginner | intermediate | advanced")
    cefr_level: str = Field(description="Pre-A1..C2")
    scope: str = Field(default=Scope.GLOBAL.value, description="global | user")
    owner_id: str = Field(default="", description="Cognito sub for user-scoped vectors; '' for global")
    list_id: str = Field(default="", description="User list id, when scope=user")

    def to_vector_metadata(self) -> dict[str, Any]:
        """Return only non-empty keys so ``$exists`` filters behave predictably."""
        return {k: v for k, v in self.model_dump().items() if v != ""}


class TermPayload(BaseModel):
    """Rich, non-filterable content returned with search results."""

    term_id: str
    term: str
    definition: str
    example: str = ""
    synonyms: list[str] = Field(default_factory=list)
    pos: str = ""
    source: str = ""


class VocabularyTerm(BaseModel):
    """A full vocabulary record: filterable metadata + payload."""

    meta: FilterableMetadata
    payload: TermPayload

    def embedding_text(self) -> str:
        """Semantic text fed to the embedding model.

        Combines the term, definition, and example so retrieval matches meaning, not just
        the headword.
        """
        parts = [self.payload.term, self.payload.definition, self.payload.example]
        if self.payload.synonyms:
            parts.append("Synonyms: " + ", ".join(self.payload.synonyms))
        return "\n".join(p.strip() for p in parts if p and p.strip())


class SearchHit(BaseModel):
    """A ranked retrieval result."""

    term_id: str
    term: str
    definition: str
    example: str = ""
    synonyms: list[str] = Field(default_factory=list)
    domain: str = ""
    subfield: str = ""
    difficulty_tier: str = ""
    cefr_level: str = ""
    scope: str = Scope.GLOBAL.value
    distance: float | None = None


# --- Progress / spaced repetition (FSRS) ---


class Rating(IntEnum):
    """FSRS review rating."""

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


class CardState(StrEnum):
    NEW = "new"
    LEARNING = "learning"
    REVIEW = "review"
    RELEARNING = "relearning"


def utcnow() -> datetime:
    return datetime.now(UTC)


class ProgressRecord(BaseModel):
    """Per-term FSRS state for one user. Stored in DynamoDB.

    The per-user aggregate is *derived* from these records (see ``UserAggregate``).
    """

    user_id: str
    term_id: str
    state: CardState = CardState.NEW
    step: int | None = None
    due: datetime = Field(default_factory=utcnow)
    stability: float = 0.0
    difficulty: float = 0.0
    elapsed_days: int = 0
    scheduled_days: int = 0
    reps: int = 0
    lapses: int = 0
    last_review: datetime | None = None
    last_rating: Rating | None = None
    note: str = ""
    starred: bool = False
    updated_at: datetime = Field(default_factory=utcnow)


class UserAggregate(BaseModel):
    """Rollup recomputed from ``ProgressRecord`` rows. Never hand-edited."""

    user_id: str
    current_streak: int = 0
    longest_streak: int = 0
    last_study_date: str = ""
    daily_goal: int = 20
    total_learned: int = 0
    due_today: int = 0
    total_xp: int = 0


class UserList(BaseModel):
    """A user-created vocabulary list."""

    list_id: str
    user_id: str
    title: str
    description: str = ""
    domain: str = "general"
    created_at: datetime = Field(default_factory=utcnow)
    term_count: int = 0
