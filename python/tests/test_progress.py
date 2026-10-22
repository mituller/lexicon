from datetime import date, timedelta

from lexicon.models import CardState, ProgressRecord, UserAggregate, utcnow
from lexicon.progress import mastery, summarize, update_streak


def record(**overrides) -> ProgressRecord:
    base = ProgressRecord(user_id="u1", term_id="t1")
    return base.model_copy(update=overrides)


def test_streak_starts_at_one() -> None:
    aggregate = update_streak(UserAggregate(user_id="u1"), date(2026, 1, 1))
    assert aggregate.current_streak == 1
    assert aggregate.longest_streak == 1
    assert aggregate.last_study_date == "2026-01-01"


def test_streak_same_day_is_idempotent() -> None:
    aggregate = update_streak(UserAggregate(user_id="u1"), date(2026, 1, 1))
    again = update_streak(aggregate, date(2026, 1, 1))
    assert again.current_streak == 1
    assert again.longest_streak == 1


def test_streak_increments_on_consecutive_day() -> None:
    aggregate = update_streak(UserAggregate(user_id="u1"), date(2026, 1, 1))
    aggregate = update_streak(aggregate, date(2026, 1, 2))
    assert aggregate.current_streak == 2
    assert aggregate.longest_streak == 2


def test_streak_resets_after_gap() -> None:
    aggregate = update_streak(UserAggregate(user_id="u1"), date(2026, 1, 1))
    aggregate = update_streak(aggregate, date(2026, 1, 3))
    assert aggregate.current_streak == 1
    assert aggregate.longest_streak == 1


def test_longest_streak_is_preserved_across_resets() -> None:
    aggregate = UserAggregate(user_id="u1", current_streak=5, longest_streak=5, last_study_date="2026-02-05")
    aggregate = update_streak(aggregate, date(2026, 2, 10))
    assert aggregate.current_streak == 1
    assert aggregate.longest_streak == 5


def test_summarize_counts_learned_and_due() -> None:
    now = utcnow()
    records = [
        record(term_id="a", state=CardState.REVIEW, reps=3, due=now - timedelta(days=1)),
        record(term_id="b", state=CardState.NEW, reps=0, due=now + timedelta(days=2)),
        record(term_id="c", state=CardState.LEARNING, reps=1, due=now - timedelta(hours=1)),
    ]
    aggregate = summarize(records, UserAggregate(user_id="u1"))
    assert aggregate.total_learned == 2
    assert aggregate.due_today == 2


def test_mastery_mapping() -> None:
    assert mastery(record(state=CardState.NEW)) == 0
    assert mastery(record(state=CardState.LEARNING, reps=1)) == 1
    assert mastery(record(state=CardState.REVIEW, reps=5, stability=3.0)) == 2
    assert mastery(record(state=CardState.REVIEW, reps=5, stability=10.0)) == 3
    assert mastery(record(state=CardState.REVIEW, reps=5, stability=40.0)) == 4
    assert mastery(record(state=CardState.REVIEW, reps=5, stability=200.0)) == 5
