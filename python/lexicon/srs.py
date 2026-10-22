"""FSRS spaced-repetition scheduling (wrapper around the ``fsrs`` library).

The algorithm is isolated behind :class:`FsrsScheduler` so it can be swapped without touching
the API or storage layers. See ``prompt.md`` §1a (L17) and Q-p.

Note on the ``fsrs`` v6 model: a *new* card is ``State.Learning`` with no stability/difficulty,
and ``Card`` does not track ``reps``/``lapses``. We maintain those counters ourselves so the
stats screen stays accurate.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from fsrs import Card as FsrsCard
from fsrs import Rating as FsrsRating
from fsrs import Scheduler, State

from .models import CardState, ProgressRecord, Rating, utcnow

_STATE_FROM_FSRS: dict[State, CardState] = {
    State.Learning: CardState.LEARNING,
    State.Review: CardState.REVIEW,
    State.Relearning: CardState.RELEARNING,
}
# A new record maps to a fresh Learning card; the other states map 1:1.
_STATE_TO_FSRS: dict[CardState, State] = {
    CardState.NEW: State.Learning,
    CardState.LEARNING: State.Learning,
    CardState.REVIEW: State.Review,
    CardState.RELEARNING: State.Relearning,
}
_RATING_TO_FSRS: dict[Rating, FsrsRating] = {
    Rating.AGAIN: FsrsRating.Again,
    Rating.HARD: FsrsRating.Hard,
    Rating.GOOD: FsrsRating.Good,
    Rating.EASY: FsrsRating.Easy,
}


def record_to_card(record: ProgressRecord) -> FsrsCard:
    """Rebuild an FSRS card from stored progress."""
    if record.state is CardState.NEW and not record.stability:
        return FsrsCard(due=record.due)
    return FsrsCard(
        state=_STATE_TO_FSRS[record.state],
        step=record.step,
        stability=record.stability or None,
        difficulty=record.difficulty or None,
        due=record.due,
        last_review=record.last_review,
    )


def apply_card(
    record: ProgressRecord,
    card: FsrsCard,
    rating: Rating,
    *,
    now: datetime,
) -> ProgressRecord:
    """Copy FSRS card state back onto a progress record and update our own counters."""
    previous_state = record.state
    record.state = _STATE_FROM_FSRS.get(card.state, CardState.LEARNING)
    record.step = card.step
    record.stability = float(card.stability) if card.stability is not None else 0.0
    record.difficulty = float(card.difficulty) if card.difficulty is not None else 0.0
    record.due = card.due
    record.last_review = card.last_review
    record.reps += 1
    if rating is Rating.AGAIN and previous_state in {CardState.REVIEW, CardState.RELEARNING}:
        record.lapses += 1
    if card.last_review is not None and card.due is not None:
        record.scheduled_days = max(0, (card.due - card.last_review).days)
    record.last_rating = rating
    record.updated_at = now
    return record


@lru_cache(maxsize=1)
def get_scheduler() -> FsrsScheduler:
    """Process-wide scheduler singleton."""
    return FsrsScheduler()


class FsrsScheduler:
    """Applies FSRS reviews to progress records."""

    def __init__(self, scheduler: Scheduler | None = None) -> None:
        self._scheduler = scheduler or Scheduler()

    def new_record(self, user_id: str, term_id: str) -> ProgressRecord:
        """Create a fresh, unscheduled progress record."""
        return ProgressRecord(user_id=user_id, term_id=term_id, state=CardState.NEW)

    def review(self, record: ProgressRecord, rating: Rating) -> ProgressRecord:
        """Apply a review rating and return the updated record.

        ``Rating`` maps to Again(1) / Hard(2) / Good(3) / Easy(4).
        """
        now = utcnow()
        if record.last_review is not None:
            record.elapsed_days = max(0, (now - record.last_review).days)
        card = record_to_card(record)
        updated_card, _review_log = self._scheduler.review_card(
            card, _RATING_TO_FSRS[rating], review_datetime=now
        )
        return apply_card(record, updated_card, rating, now=now)

    def is_due(self, record: ProgressRecord, *, now: datetime | None = None) -> bool:
        return record.due <= (now or utcnow())
