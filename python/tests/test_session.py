import json
import random
from datetime import timedelta
from typing import Any

import pytest

from lexicon.models import CardState, ProgressRecord, Rating, TermRecord, UserAggregate, utcnow
from lexicon.session import (
    CONFIRM_GAP,
    MAX_ATTEMPTS,
    REQUEUE_GAP,
    Exercise,
    ExerciseType,
    Phase,
    SessionEngine,
    SessionItem,
    SessionNotFound,
    SessionState,
    SessionSummary,
    StaleExercise,
    blank_out,
    expected_correct,
    grade_typed,
    interleave,
    update_level,
)

USER = "u1"


def term(term_id: str, word: str, difficulty: int, **extra: Any) -> TermRecord:
    return TermRecord(
        term_id=term_id,
        term=word,
        definition=f"definition of {word}",
        examples=[f"She was {word} about it.", f"A rather {word} remark."],
        difficulty=difficulty,
        **extra,
    )


WORDS = [
    term("t_laconic", "laconic", 45, synonyms=["terse"]),
    term("t_terse", "terse", 46),
    term("t_pithy", "pithy", 47),
    term("t_verbose", "verbose", 48),
    term("t_flaunt", "flaunt", 49, confused_with=["flout"]),
    term("t_laconically", "laconically", 50),
    term("t_feckless", "feckless", 51),
    term("t_halcyon", "halcyon", 70),
]


class FakeProgress:
    def __init__(self, records: list[ProgressRecord] | None = None) -> None:
        self.records = {r.term_id: r for r in records or []}
        self.aggregate = UserAggregate(user_id=USER)
        self.reviews: list[tuple[str, int]] = []
        self.recomputed = 0

    def get_aggregate(self, user_id: str) -> UserAggregate:
        return self.aggregate.model_copy()

    def put_aggregate(self, aggregate: UserAggregate) -> None:
        self.aggregate = aggregate.model_copy()

    def recompute_aggregate(self, user_id: str) -> UserAggregate:
        self.recomputed += 1
        return self.aggregate.model_copy(update={"current_streak": 1})

    def list_progress(self, user_id: str) -> list[ProgressRecord]:
        return list(self.records.values())

    def get_progress(self, user_id: str, term_id: str) -> ProgressRecord | None:
        return self.records.get(term_id)

    def put_progress(self, record: ProgressRecord) -> None:
        self.reviews.append((record.term_id, int(record.last_rating or 0)))
        self.records[record.term_id] = record


class FakeTerms:
    def __init__(self, terms: list[TermRecord]) -> None:
        self.terms = {t.term_id: t for t in terms}

    def terms_in_band(self, lo: int, hi: int, **_: Any) -> list[TermRecord]:
        return [t for t in self.terms.values() if lo <= t.difficulty < hi]

    def get_terms(self, ids: list[str]) -> dict[str, TermRecord]:
        return {i: self.terms[i] for i in ids if i in self.terms}


class FakeVectors:
    """Neighbours = every other term ordered by difficulty distance."""

    def __init__(self, terms: FakeTerms) -> None:
        self.terms = terms
        self.queries: list[dict[str, Any]] = []

    def get_embeddings(self, keys: list[str]) -> dict[str, list[float]]:
        return {k: [float(self.terms.terms[k].difficulty)] for k in keys if k in self.terms.terms}

    def query(self, vector: list[float], *, top_k: int, filter: dict, return_metadata: bool = True):
        self.queries.append(filter)
        bounds = {k: v for c in filter.get("$and", []) for k, v in c.get("difficulty", {}).items()}
        hits = [
            {"key": t.term_id, "distance": abs(t.difficulty - vector[0]),
             "metadata": {"term": t.term, "definition": t.definition, "synonyms": t.synonyms,
                          "pos": t.pos}}
            for t in self.terms.terms.values()
            if t.difficulty >= bounds.get("$gte", 0) and t.difficulty < bounds.get("$lt", 101)
        ]
        return sorted(hits, key=lambda h: h["distance"])[:top_k]


class FakeBedrock:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Write one new sentence"):
            word = prompt.split('"')[1]
            return json.dumps({"sentence": f"Everyone at the table found his {word} reply unsettling."})
        return "Because they differ."


class FakePool:
    def __init__(self, terms: FakeTerms, extra: list[TermRecord] | None = None) -> None:
        self.terms, self.extra, self.calls = terms, extra or [], 0

    def ensure_band(self, lo: int, hi: int, **_: Any):
        self.calls += 1
        for t in self.extra:
            self.terms.terms[t.term_id] = t

        class Report:
            added = list(self.extra)

        return Report()


class FakeSessions:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def put(self, state: SessionState) -> None:
        self.saved[state.session_id] = state.model_dump_json()  # round-trip like DynamoDB

    def get(self, user_id: str, session_id: str) -> SessionState | None:
        raw = self.saved.get(session_id)
        return SessionState.model_validate_json(raw) if raw else None


def make_engine(records=None, words=WORDS, extra=None):
    terms = FakeTerms(list(words))
    progress = FakeProgress(records)
    sessions = FakeSessions()
    pool = FakePool(terms, extra)
    engine = SessionEngine(
        progress=progress, terms=terms, vectors=FakeVectors(terms), bedrock=FakeBedrock(),  # type: ignore[arg-type]
        pool=pool, sessions=sessions, rng=random.Random(3),  # type: ignore[arg-type]
    )
    return engine, progress, sessions, pool


def answer_key(sessions: FakeSessions, session_id: str) -> tuple[int | None, str]:
    current = SessionState.model_validate_json(sessions.saved[session_id]).current
    assert current is not None
    return current.answer_index, current.answer_text


def answer_right(engine, sessions, session_id: str, exercise: Exercise):
    index, text = answer_key(sessions, session_id)
    if exercise.type is ExerciseType.TYPE_WORD:
        return engine.answer(USER, session_id, exercise.exercise_id, text=text)
    return engine.answer(USER, session_id, exercise.exercise_id, choice=index)


def answer_wrong(engine, sessions, session_id: str, exercise: Exercise):
    index, _ = answer_key(sessions, session_id)
    if exercise.type is ExerciseType.TYPE_WORD:
        return engine.answer(USER, session_id, exercise.exercise_id, text="zzzzzz")
    return engine.answer(USER, session_id, exercise.exercise_id, choice=(index + 1) % 4)


# --- pure helpers ---


def test_interleave_spreads_new_words() -> None:
    reviews = [SessionItem(term_id=f"r{i}", kind="review") for i in range(6)]
    new = [SessionItem(term_id=f"n{i}", kind="new") for i in range(2)]
    order = [i.term_id for i in interleave(reviews, new)]
    assert order == ["r0", "r1", "n0", "r2", "r3", "n1", "r4", "r5"]
    assert [i.term_id for i in interleave([], new)] == ["n0", "n1"]


def test_level_moves_more_for_surprising_results_and_discounts_guessing() -> None:
    def moved(difficulty: float, correct: bool, guess: float) -> float:
        aggregate = UserAggregate(user_id=USER, level=40)
        update_level(aggregate, difficulty, correct, guess=guess)
        return (aggregate.level or 0) - 40

    assert moved(60, True, 0.0) > moved(30, True, 0.0) > 0
    assert moved(60, True, 0.25) < moved(60, True, 0.0)  # a right MC answer could be a guess
    assert moved(30, False, 0.25) < moved(60, False, 0.25) < 0
    assert expected_correct(40, 40) == pytest.approx(0.5)


def test_level_starts_from_default_and_slows_down() -> None:
    aggregate = UserAggregate(user_id=USER)
    update_level(aggregate, 40, True, guess=0.0)
    early = (aggregate.level or 0) - 40
    aggregate = UserAggregate(user_id=USER, level=40, level_updates=100)
    update_level(aggregate, 40, True, guess=0.0)
    assert (aggregate.level or 0) - 40 < early


@pytest.mark.parametrize(
    ("typed", "answer", "expected"),
    [
        ("laconic", "laconic", (True, False)),
        (" Laconic ", "laconic", (True, False)),
        ("flaunted", "flaunt", (True, False)),
        ("laconik", "laconic", (True, True)),
        ("ad-hoc", "ad hoc", (True, False)),
        ("terce", "terse", (True, True)),
        ("tese", "terse", (True, True)),
        ("pity", "pithy", (True, True)),
        ("tense", "terse", (True, True)),
        ("sesquipedalain", "sesquipedalian", (True, True)),
        ("verbal", "verbose", (False, False)),
        ("", "terse", (False, False)),
        ("ox", "on", (False, False)),
    ],
)
def test_grade_typed(typed: str, answer: str, expected: tuple[bool, bool]) -> None:
    assert grade_typed(typed, answer) == expected


def test_blank_out_handles_inflection_and_phrases() -> None:
    assert blank_out("He obfuscated the issue.", "obfuscate") == "He _____ the issue."
    assert blank_out("A truly ad-hoc fix.", "ad hoc") == "A truly _____ fix."
    assert blank_out("Nothing here.", "laconic") is None
    assert blank_out("The daunting task seemed to daunt them.", "daunt") is None  # answer would show


# --- distractors ---


def test_distractor_words_put_confusables_first_and_skip_synonyms_and_family() -> None:
    engine, *_ = make_engine()
    flaunt = WORDS[4]
    assert engine.distractor_words(flaunt)[0] == "flout"
    laconic_options = engine.distractor_words(WORDS[0])
    assert "terse" not in laconic_options  # listed synonym: would be a second right answer
    assert "laconically" not in laconic_options  # same word family
    assert len(laconic_options) == 3


def test_distractor_definitions_come_from_neighbours() -> None:
    engine, *_ = make_engine()
    pairs = engine.distractor_definitions(WORDS[2])  # pithy
    assert len(pairs) == 3
    assert all(definition == f"definition of {word}" for word, definition in pairs)
    assert "pithy" not in {word for word, _ in pairs}


# --- picking new words ---


def test_new_words_come_from_just_above_level() -> None:
    engine, *_ = make_engine()
    picked = engine.pick_new_words(USER, 44, [], count=4)
    assert len(picked) == 4
    assert "t_halcyon" not in picked  # 70 is far above 44-56


def test_short_band_triggers_blocking_refill() -> None:
    extra = [term("t_new1", "pellucid", 66), term("t_new2", "limpid", 67)]
    engine, _, _, pool = make_engine(extra=extra)
    picked = engine.pick_new_words(USER, 65, [], count=3)
    assert pool.calls == 1
    assert {"t_new1", "t_new2", "t_halcyon"} <= set(picked)


def test_new_words_prefer_neighbours_of_recent_mastery() -> None:
    mastered = ProgressRecord(
        user_id=USER, term_id="t_halcyon", state=CardState.REVIEW, stability=40,
        last_review=utcnow(), due=utcnow() + timedelta(days=30),
    )
    engine, *_ = make_engine(records=[mastered])
    picked = engine.pick_new_words(USER, 44, [mastered], count=4)
    # FakeVectors ranks by difficulty distance from halcyon (70): the top of the band wins.
    assert "t_feckless" in picked[:2]


# --- full sessions ---


def test_exercise_never_exposes_the_answer() -> None:
    engine, _, sessions, _ = make_engine()
    started = engine.start(USER, new_words=2)
    exercise = engine.next_exercise(USER, started.session_id)
    assert isinstance(exercise, Exercise)
    payload = exercise.model_dump()
    assert "answer_index" not in payload and "answer_text" not in payload and "term_id" not in payload
    assert exercise.type is ExerciseType.CHOOSE_DEFINITION and len(exercise.options) == 4
    # Asking again returns the same exercise rather than a reshuffled one.
    assert engine.next_exercise(USER, started.session_id) == exercise


def test_new_word_known_on_sight_is_confirmed_by_typing_then_rated_easy() -> None:
    engine, progress, sessions, _ = make_engine()
    sid = engine.start(USER, new_words=CONFIRM_GAP + 1).session_id

    first = engine.next_exercise(USER, sid)
    target = SessionState.model_validate_json(sessions.saved[sid]).current.term_id  # type: ignore[union-attr]
    result = answer_right(engine, sessions, sid, first)
    assert result.next_step == "confirm"
    assert progress.reviews == []  # nothing scheduled until it's confirmed

    seen_types = []
    for _ in range(CONFIRM_GAP):
        exercise = engine.next_exercise(USER, sid)
        seen_types.append(exercise.type)
        answer_right(engine, sessions, sid, exercise)
    confirm = engine.next_exercise(USER, sid)
    assert confirm.type is ExerciseType.TYPE_WORD and confirm.hint
    assert SessionState.model_validate_json(sessions.saved[sid]).current.term_id == target  # type: ignore[union-attr]
    assert answer_right(engine, sessions, sid, confirm).next_step == "done"
    assert (target, int(Rating.EASY)) in progress.reviews


def test_missed_word_is_rated_again_once_and_requeued_as_a_different_type() -> None:
    engine, progress, sessions, _ = make_engine()
    sid = engine.start(USER, new_words=REQUEUE_GAP + 2).session_id

    first = engine.next_exercise(USER, sid)
    target = SessionState.model_validate_json(sessions.saved[sid]).current.term_id  # type: ignore[union-attr]
    level_before = progress.aggregate.level
    result = answer_wrong(engine, sessions, sid, first)
    assert not result.correct and result.next_step == "requeued"
    assert result.entry.definition == f"definition of {WORDS[[w.term_id for w in WORDS].index(target)].term}"
    assert progress.reviews == [(target, int(Rating.AGAIN))]
    assert progress.aggregate.level is not None and (level_before or 40) > progress.aggregate.level

    for _ in range(REQUEUE_GAP):
        answer_right(engine, sessions, sid, engine.next_exercise(USER, sid))
    retry = engine.next_exercise(USER, sid)
    assert SessionState.model_validate_json(sessions.saved[sid]).current.term_id == target  # type: ignore[union-attr]
    assert retry.type is not ExerciseType.CHOOSE_DEFINITION
    assert retry.attempt == 2

    level = progress.aggregate.level
    answer_wrong(engine, sessions, sid, retry)  # second miss: no extra FSRS write, no level change
    assert [r for r in progress.reviews if r[0] == target] == [(target, int(Rating.AGAIN))]
    assert progress.aggregate.level == level


def test_relearn_gives_up_after_max_attempts() -> None:
    engine, _, sessions, _ = make_engine(words=WORDS[:4])
    sid = engine.start(USER, new_words=1).session_id
    for _ in range(MAX_ATTEMPTS):
        exercise = engine.next_exercise(USER, sid)
        result = answer_wrong(engine, sessions, sid, exercise)
    assert result.next_step == "done"
    assert result.summary is not None and result.summary.gave_up == 1


def test_session_finishes_with_summary_and_streak_refresh() -> None:
    due = ProgressRecord(
        user_id=USER, term_id="t_pithy", state=CardState.REVIEW, stability=10, difficulty=5,
        last_review=utcnow() - timedelta(days=10), due=utcnow() - timedelta(hours=1),
    )
    engine, progress, sessions, _ = make_engine(records=[due])
    started = engine.start(USER, new_words=1)
    assert (started.reviews, started.new_words) == (1, 1)

    review_seen = False
    result = None
    while True:
        exercise = engine.next_exercise(USER, started.session_id)
        if isinstance(exercise, SessionSummary):
            break
        if exercise.kind == "review":
            review_seen = True
            assert exercise.type is ExerciseType.CLOZE  # stability 10 -> cloze
            assert "_____" in exercise.prompt
        result = answer_right(engine, sessions, started.session_id, exercise)
    assert review_seen and result is not None and result.summary is not None
    assert result.summary.reviews_remembered == 1 and result.summary.already_known == 1
    assert ("t_pithy", int(Rating.GOOD)) in progress.reviews
    assert progress.recomputed == 1
    assert exercise.reviews_remembered == 1  # next() after the end returns the summary


def test_stale_and_unknown_sessions_are_rejected() -> None:
    engine, _, sessions, _ = make_engine()
    sid = engine.start(USER, new_words=2).session_id
    exercise = engine.next_exercise(USER, sid)
    answer_right(engine, sessions, sid, exercise)
    with pytest.raises(StaleExercise):
        engine.answer(USER, sid, exercise.exercise_id, choice=0)  # replayed answer
    with pytest.raises(SessionNotFound):
        engine.next_exercise(USER, "nope")


def test_out_of_range_choice_is_wrong_not_an_error() -> None:
    engine, _, _, _ = make_engine()
    sid = engine.start(USER, new_words=2).session_id
    exercise = engine.next_exercise(USER, sid)
    assert not engine.answer(USER, sid, exercise.exercise_id, choice=9).correct


def test_explain_is_generated_once_and_cached() -> None:
    engine, _, sessions, _ = make_engine()
    sid = engine.start(USER, new_words=2).session_id
    exercise = engine.next_exercise(USER, sid)
    answer_wrong(engine, sessions, sid, exercise)
    bedrock: FakeBedrock = engine._bedrock  # type: ignore[assignment]
    before = len(bedrock.prompts)
    assert engine.explain(USER, sid, exercise.exercise_id) == "Because they differ."
    assert engine.explain(USER, sid, exercise.exercise_id) == "Because they differ."
    assert len(bedrock.prompts) == before + 1


@pytest.mark.parametrize(
    ("state", "stability", "expected"),
    [
        (CardState.LEARNING, 1.0, ExerciseType.CHOOSE_WORD),
        (CardState.REVIEW, 3.0, ExerciseType.CHOOSE_WORD),
        (CardState.REVIEW, 12.0, ExerciseType.CLOZE),
        (CardState.REVIEW, 45.0, ExerciseType.TYPE_WORD),
    ],
)
def test_review_exercise_type_scales_with_stability(state, stability, expected) -> None:
    record = ProgressRecord(user_id=USER, term_id="x", state=state, stability=stability)
    assert SessionEngine.choose_type(SessionItem(term_id="x", kind="review"), record) is expected


def test_relearn_alternates_away_from_last_type() -> None:
    item = SessionItem(term_id="x", kind="new", phase=Phase.RELEARN, misses=1,
                       last_type=ExerciseType.CLOZE)
    assert SessionEngine.choose_type(item, None) is ExerciseType.CHOOSE_WORD


def test_explanation_names_the_word_behind_a_chosen_definition() -> None:
    engine, _, sessions, _ = make_engine()
    sid = engine.start(USER, new_words=2).session_id
    exercise = engine.next_exercise(USER, sid)
    assert exercise.type is ExerciseType.CHOOSE_DEFINITION
    result = answer_wrong(engine, sessions, sid, exercise)
    engine.explain(USER, sid, exercise.exercise_id)
    prompt = engine._bedrock.prompts[-1]  # type: ignore[attr-defined]
    wrong_word = result.chosen.removeprefix("definition of ")
    assert f'the definition of "{wrong_word}"' in prompt


def test_cloze_prompt_rules_out_the_other_options() -> None:
    prompt = __import__("lexicon.session", fromlist=["cloze_prompt"]).cloze_prompt(
        WORDS[0], ["automated", "automatic"]
    )
    assert "clearly wrong in that position: automated, automatic." in prompt
    assert prompt.endswith('Return JSON: {"sentence": "..."}')


def test_distractor_words_prefer_same_part_of_speech() -> None:
    words = [
        term("t_daunt", "daunt", 50, pos="verb"),
        term("t_magpie", "magpie", 50, pos="noun"),
        term("t_bout", "bout", 51, pos="noun"),
        term("t_cow", "intimidate", 52, pos="verb"),
        term("t_deter", "deter", 53, pos="verb"),
        term("t_motley", "motley", 54, pos="adjective"),
    ]
    engine, *_ = make_engine(words=words)
    assert engine.distractor_words(words[0], confusables=False) == ["intimidate", "deter", "magpie"]


def test_cloze_rejects_sentences_that_mention_another_option() -> None:
    engine, *_ = make_engine()
    replies = iter([
        json.dumps({"sentence": "His laconic reply, unlike the verbose one, ended it."}),
        json.dumps({"sentence": "Her laconic answer ended the meeting early."}),
    ])
    engine._bedrock.generate = lambda prompt, **_: next(replies)  # type: ignore[method-assign]
    assert engine.cloze_sentence(WORDS[0], avoid=["verbose"]) == "Her _____ answer ended the meeting early."


def test_cloze_falls_back_to_stored_examples() -> None:
    engine, *_ = make_engine()
    engine._bedrock.generate = lambda prompt, **_: "not json"  # type: ignore[method-assign]
    assert engine.cloze_sentence(WORDS[0]) in {"She was _____ about it.", "A rather _____ remark."}
