from lexicon.models import CardState, Rating
from lexicon.srs import FsrsScheduler


def test_new_record_starts_new_and_unscheduled() -> None:
    scheduler = FsrsScheduler()
    record = scheduler.new_record("user-1", "gen_001")
    assert record.state is CardState.NEW
    assert record.reps == 0
    assert record.stability == 0.0


def test_review_advances_reps_and_sets_due() -> None:
    scheduler = FsrsScheduler()
    record = scheduler.new_record("user-1", "gen_001")
    updated = scheduler.review(record, Rating.GOOD)
    assert updated.reps == 1
    assert updated.last_rating is Rating.GOOD
    assert updated.last_review is not None
    assert updated.due is not None


def test_review_again_records_lapse_eventually() -> None:
    scheduler = FsrsScheduler()
    record = scheduler.new_record("user-1", "gen_001")
    updated = scheduler.review(record, Rating.AGAIN)
    assert updated.reps == 1
    assert updated.last_rating is Rating.AGAIN


def test_is_due_uses_due_date() -> None:
    scheduler = FsrsScheduler()
    record = scheduler.new_record("user-1", "gen_001")
    assert scheduler.is_due(record) is True
