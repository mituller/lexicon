from lexicon.models import ProgressRecord, UserAggregate, UserList
from lexicon.privacy import build_export


def test_build_export_shape() -> None:
    export = build_export(
        "u1",
        progress=[ProgressRecord(user_id="u1", term_id="gen_001", reps=2)],
        lists=[UserList(list_id="l1", user_id="u1", title="My list")],
        aggregate=UserAggregate(user_id="u1", total_learned=1),
    )
    assert export["user_id"] == "u1"
    assert export["schema_version"] == 1
    assert "exported_at" in export
    assert export["progress"][0]["term_id"] == "gen_001"
    assert export["lists"][0]["title"] == "My list"
    assert export["aggregate"]["total_learned"] == 1


def test_build_export_empty() -> None:
    export = build_export(
        "u2",
        progress=[],
        lists=[],
        aggregate=UserAggregate(user_id="u2"),
    )
    assert export["progress"] == []
    assert export["lists"] == []
