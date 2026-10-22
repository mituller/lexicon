import random
from datetime import timedelta

from lexicon.decks import (
    DeckService,
    DueTerm,
    apply_quiz_results,
    grade_answer,
    vector_to_term,
)
from lexicon.models import CardState, ProgressRecord, Rating, UserAggregate, utcnow


def make_term(term_id: str, term: str, domain: str, tier: str, cefr: str, definition: str) -> dict:
    return {
        "key": term_id,
        "metadata": {
            "term": term,
            "definition": definition,
            "example": "",
            "synonyms": [],
            "pos": "",
            "domain": domain,
            "subfield": "x",
            "difficulty_tier": tier,
            "cefr_level": cefr,
        },
    }


class FakeVectorStore:
    def __init__(self, terms: list[dict]) -> None:
        self.terms = terms

    def list_all_vectors(self) -> list[dict]:
        return self.terms

    def get_vectors(self, keys: list[str]) -> list[dict]:
        return [t for t in self.terms if t["key"] in set(keys)]


class FakeProgressStore:
    def __init__(self) -> None:
        self.records: dict[str, ProgressRecord] = {}
        self.aggregate = UserAggregate(user_id="u1")

    def get_progress(self, user_id: str, term_id: str) -> ProgressRecord | None:
        assert user_id == "u1"
        return self.records.get(term_id)

    def put_progress(self, record: ProgressRecord) -> None:
        self.records[record.term_id] = record

    def list_progress(self, user_id: str) -> list[ProgressRecord]:
        assert user_id == "u1"
        return list(self.records.values())

    def recompute_aggregate(self, user_id: str) -> UserAggregate:
        return self.aggregate

    def put_aggregate(self, aggregate: UserAggregate) -> None:
        self.aggregate = aggregate


def make_terms() -> list[dict]:
    return [
        make_term("gen_a", "a", "general", "beginner", "A1", "first letter"),
        make_term("gen_b", "big", "general", "beginner", "A1", "large"),
        make_term("gen_c", "calm", "general", "beginner", "A2", "peaceful"),
        make_term("gen_d", "deep", "general", "beginner", "A2", "far down"),
        make_term("gen_e", "abandon", "general", "intermediate", "B1", "leave behind"),
        make_term("ai_x", "embedding", "ai", "advanced", "B2", "vector representation"),
        make_term("ai_y", "transformer", "ai", "advanced", "C1", "attention model"),
        make_term("ai_z", "quantization", "ai", "advanced", "C2", "reduce precision"),
        make_term("ai_w", "lora", "ai", "advanced", "C1", "low-rank adapt"),
    ]


def test_vector_to_term_maps_key_and_metadata() -> None:
    term = vector_to_term(make_term("gen_a", "a", "general", "beginner", "A1", "first letter"))
    assert term["term_id"] == "gen_a"
    assert term["term"] == "a"
    assert term["definition"] == "first letter"
    assert term["cefr_level"] == "A1"


def test_list_decks_groups_by_domain_and_tier() -> None:
    service = DeckService(store=FakeVectorStore(make_terms()))  # type: ignore[arg-type]
    decks = {(d.domain, d.difficulty_tier): d.count for d in service.list_decks()}
    assert decks[("general", "beginner")] == 4
    assert decks[("general", "intermediate")] == 1
    assert decks[("ai", "advanced")] == 4


def test_deck_terms_filters_and_sorts() -> None:
    service = DeckService(store=FakeVectorStore(make_terms()))  # type: ignore[arg-type]
    terms = service.deck_terms(domain="ai", difficulty_tier="advanced")
    assert [t["cefr_level"] for t in terms] == ["B2", "C1", "C1", "C2"]
    assert all(t["domain"] == "ai" for t in terms)


def test_make_quiz_has_four_options_and_correct_answer() -> None:
    service = DeckService(store=FakeVectorStore(make_terms()))  # type: ignore[arg-type]
    quiz = service.make_quiz(domain="ai", difficulty_tier="advanced", count=4, rng=random.Random(7))
    assert len(quiz.questions) == 4
    for question in quiz.questions:
        assert len(question.options) == 4
        assert question.options[question.answer_index] == _definition(question.term_id)


def _definition(term_id: str) -> str:
    return {t["key"]: t["metadata"]["definition"] for t in make_terms()}[term_id]


def test_grade_answer_maps_to_fsrs_rating() -> None:
    assert grade_answer(True) is Rating.GOOD
    assert grade_answer(False) is Rating.AGAIN


def test_apply_quiz_results_updates_progress_and_score() -> None:
    store = FakeProgressStore()
    summary = apply_quiz_results(
        store,  # type: ignore[arg-type]
        "u1",
        [
            {"term_id": "ai_x", "correct": True},
            {"term_id": "ai_y", "correct": False},
            {"term_id": "ai_z", "correct": True},
        ],
    )
    assert summary == {"correct": 2, "total": 3}
    assert store.records["ai_x"].reps == 1
    assert store.records["ai_y"].last_rating is Rating.AGAIN
    assert store.records["ai_z"].last_rating is Rating.GOOD


def test_due_terms_filters_by_due_date() -> None:
    service = DeckService(store=FakeVectorStore(make_terms()))  # type: ignore[arg-type]
    store = FakeProgressStore()
    now = utcnow()
    store.records["ai_x"] = ProgressRecord(
        user_id="u1", term_id="ai_x", state=CardState.NEW, due=now - timedelta(minutes=5)
    )
    store.records["ai_y"] = ProgressRecord(
        user_id="u1", term_id="ai_y", state=CardState.NEW, due=now + timedelta(days=1)
    )
    due = service.due_terms(store, "u1", now=now)  # type: ignore[arg-type]
    assert [d.term_id for d in due] == ["ai_x"]
    assert isinstance(due[0], DueTerm)
    assert due[0].term == "embedding"