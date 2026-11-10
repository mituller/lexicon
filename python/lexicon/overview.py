"""Progress page: level over time, what's coming up, and the words that keep slipping."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from .models import CardState, LevelPoint, ProgressRecord, Rating, TermRecord, UserAggregate, utcnow

FORECAST_DAYS = 7
MAX_STRUGGLING = 12
# FSRS stability (days) past which a word counts as well known.
KNOWN_STABILITY = 21


class DueDay(BaseModel):
    date: str
    count: int


class StrugglingWord(BaseModel):
    term_id: str
    term: str
    definition: str
    lapses: int


class Overview(BaseModel):
    level: float | None
    level_history: list[LevelPoint] = Field(default_factory=list)
    learning: int = 0  # seen, not yet stable
    known: int = 0  # stability >= KNOWN_STABILITY days
    queued: int = 0  # imported words waiting for a session
    due_forecast: list[DueDay] = Field(default_factory=list)  # [0] includes anything overdue
    struggling: list[StrugglingWord] = Field(default_factory=list)


def due_forecast(records: Sequence[ProgressRecord], *, now: datetime, days: int = FORECAST_DAYS) -> list[DueDay]:
    today = now.date()
    counts = [0] * days
    for record in records:
        offset = max(0, (record.due.date() - today).days)
        if offset < days:
            counts[offset] += 1
    return [DueDay(date=(today + timedelta(days=i)).isoformat(), count=c) for i, c in enumerate(counts)]


def struggling_ids(records: Sequence[ProgressRecord]) -> list[str]:
    """Words lapsed at least twice, or missed last time after being learned; worst first."""
    shaky = [
        r for r in records
        if r.lapses >= 2 or (r.lapses >= 1 and r.last_rating is Rating.AGAIN)
    ]
    shaky.sort(key=lambda r: (r.lapses, r.last_review or r.updated_at), reverse=True)
    return [r.term_id for r in shaky[:MAX_STRUGGLING]]


def build_overview(
    aggregate: UserAggregate,
    records: Sequence[ProgressRecord],
    terms: dict[str, TermRecord],
    *,
    queued: int = 0,
    now: datetime | None = None,
) -> Overview:
    now = now or utcnow()
    known = sum(1 for r in records if r.state is CardState.REVIEW and r.stability >= KNOWN_STABILITY)
    by_id = {r.term_id: r for r in records}
    return Overview(
        level=aggregate.level,
        level_history=aggregate.level_history,
        known=known,
        learning=len(records) - known,
        queued=queued,
        due_forecast=due_forecast(records, now=now),
        struggling=[
            StrugglingWord(
                term_id=term_id, term=terms[term_id].term,
                definition=terms[term_id].definition, lapses=by_id[term_id].lapses,
            )
            for term_id in struggling_ids(records)
            if term_id in terms
        ],
    )
