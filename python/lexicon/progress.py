"""Derivation of per-user aggregates from per-term FSRS records.

The aggregate is always computed, never hand-edited (see prompt.md Q-i). Streaks are tracked
from distinct study days; mastery is a UI-friendly rollup of FSRS state.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from .models import CardState, ProgressRecord, UserAggregate, utcnow


def update_streak(aggregate: UserAggregate, today: date) -> UserAggregate:
    """Advance the streak for a study day, idempotently.

    Same day -> unchanged; consecutive day -> +1; gap -> reset to 1.
    """
    if aggregate.last_study_date == today.isoformat():
        return aggregate
    if aggregate.last_study_date:
        last = date.fromisoformat(aggregate.last_study_date)
        if today - last == timedelta(days=1):
            aggregate.current_streak += 1
        elif today > last:
            aggregate.current_streak = 1
    else:
        aggregate.current_streak = 1
    aggregate.longest_streak = max(aggregate.longest_streak, aggregate.current_streak)
    aggregate.last_study_date = today.isoformat()
    return aggregate


def summarize(records: Sequence[ProgressRecord], aggregate: UserAggregate) -> UserAggregate:
    """Recompute derived counts (learned, due) onto ``aggregate``."""
    now = utcnow()
    aggregate.total_learned = sum(
        1 for record in records if record.state is not CardState.NEW and record.reps > 0
    )
    aggregate.due_today = sum(1 for record in records if record.due <= now)
    return aggregate


def mastery(record: ProgressRecord) -> int:
    """Map FSRS state + stability to a 0-5 mastery badge for the UI."""
    if record.state is CardState.NEW:
        return 0
    if record.state in {CardState.LEARNING, CardState.RELEARNING}:
        return 1
    if record.stability < 7:
        return 2
    if record.stability < 30:
        return 3
    if record.stability < 90:
        return 4
    return 5


def bookkeeping(
    records: Sequence[ProgressRecord], aggregate: UserAggregate, *, today: date | None = None
) -> UserAggregate:
    """One call that refreshes counts and advances the streak."""
    summarize(records, aggregate)
    return update_streak(aggregate, today or utcnow().date())
