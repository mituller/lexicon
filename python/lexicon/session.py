"""Adaptive study sessions: due reviews + new words just above the learner's level.

A session is a queue of items (one per term). Each time an item reaches the front, the engine
builds an exercise whose type depends on how well the word is known, grades the answer
server-side, and decides what happens next:

* **New word, first sight** -> "choose the definition". Right: it comes back later as "type the
  word" to confirm it wasn't a guess; confirmed -> FSRS Easy (already known). Wrong -> FSRS Again,
  then relearn.
* **Review** -> exercise type scales with FSRS stability. Right -> FSRS Good (Hard if it took a
  typo allowance). Wrong -> FSRS Again, then relearn.
* **Relearn** -> the word returns ``REQUEUE_GAP`` cards later as a *different* exercise type,
  until it's answered correctly (at most ``MAX_ATTEMPTS`` attempts).

FSRS is applied once per item per session (its own learning steps are minutes; in-session
repetition is this queue's job). The learner's level moves Elo-style on each item's first
attempt, discounting the chance of guessing a multiple-choice answer.

Session state lives in DynamoDB (``USER#<sub>`` / ``SESSION#<id>``) so the Lambda stays
stateless, and the answer key never leaves the server.
"""

from __future__ import annotations

import math
import random
import re
import time
import uuid
from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .bedrock import BedrockClient
from .config import get_settings
from .dynamo import ProgressStore, _floats_to_decimal, _pk, _table
from .enrichment import extract_json, same_family
from .models import CardState, ProgressRecord, Rating, TermRecord, UserAggregate, utcnow
from .refill import VocabularyPool
from .srs import get_scheduler
from .termindex import TermIndex
from .vectorstore import VectorStore

INITIAL_LEVEL = 40.0
NEW_PER_SESSION = 6
MAX_REVIEWS = 15
NEW_BAND_WIDTH = 12  # new words come from [level, level + width)
REQUEUE_GAP = 3
CONFIRM_GAP = 4
MAX_ATTEMPTS = 6
OPTION_COUNT = 4
SESSION_TTL_SECONDS = 2 * 24 * 3600

# Elo-style level estimate: P(knows word) = sigmoid((level - difficulty) / LEVEL_SCALE).
LEVEL_SCALE = 8.0
K_EARLY, K_LATE, EARLY_UPDATES = 6.0, 2.5, 40


class ExerciseType(StrEnum):
    CHOOSE_DEFINITION = "choose_definition"  # term shown, pick its definition
    CHOOSE_WORD = "choose_word"  # definition shown, pick the term
    CLOZE = "cloze"  # sentence with a blank, pick the term
    TYPE_WORD = "type_word"  # definition shown, type the term


class Phase(StrEnum):
    PROBE = "probe"
    CONFIRM = "confirm"
    RELEARN = "relearn"
    DONE = "done"


class SessionItem(BaseModel):
    term_id: str
    kind: Literal["new", "review"]
    phase: Phase = Phase.PROBE
    attempts: int = 0
    misses: int = 0
    last_type: ExerciseType | None = None
    outcome: str = ""  # known | learned | remembered | relearned | missed


class Exercise(BaseModel):
    """What the client sees. No answer."""

    exercise_id: str
    session_id: str
    type: ExerciseType
    prompt: str
    options: list[str] = Field(default_factory=list)
    hint: str = ""
    kind: Literal["new", "review"]
    attempt: int
    remaining: int


class PendingExercise(Exercise):
    """Server-side copy, with the answer."""

    term_id: str
    answer_index: int | None = None
    answer_text: str = ""
    option_terms: list[str] = Field(default_factory=list)  # whose definition each option is


class GradedExercise(BaseModel):
    exercise_id: str
    term_id: str
    type: ExerciseType
    prompt: str
    options: list[str]
    chosen: str
    chosen_term: str = ""  # for definition options: the word the chosen definition belongs to
    correct_answer: str
    correct: bool
    explanation: str = ""


class SessionSummary(BaseModel):
    new_words: int = 0
    already_known: int = 0
    learned: int = 0
    reviews_remembered: int = 0
    reviews_relearned: int = 0
    gave_up: int = 0
    level_start: float = INITIAL_LEVEL
    level_end: float = INITIAL_LEVEL


class SessionState(BaseModel):
    session_id: str
    user_id: str
    items: list[SessionItem]
    queue: list[int]
    current: PendingExercise | None = None
    graded: list[GradedExercise] = Field(default_factory=list)
    level_start: float
    created_at: float = Field(default_factory=time.time)


class EntryView(BaseModel):
    """The full entry, shown after every answer so a miss is also a lesson."""

    term: str
    pos: str = ""
    definition: str
    examples: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    confused_with: list[str] = Field(default_factory=list)
    etymology: str = ""

    @classmethod
    def of(cls, term: TermRecord) -> EntryView:
        return cls.model_validate(term.model_dump(include=set(cls.model_fields)))


class AnswerResult(BaseModel):
    correct: bool
    typo: bool = False
    chosen: str
    correct_answer: str
    entry: EntryView
    next_step: Literal["confirm", "requeued", "done"]
    remaining: int
    level: float
    summary: SessionSummary | None = None  # set when the session just finished


class SessionStarted(BaseModel):
    session_id: str
    reviews: int
    new_words: int
    level: float


# --- level -----------------------------------------------------------------


def expected_correct(level: float, difficulty: float, *, guess: float = 0.0) -> float:
    know = 1.0 / (1.0 + math.exp(-(level - difficulty) / LEVEL_SCALE))
    return guess + (1.0 - guess) * know


def update_level(aggregate: UserAggregate, difficulty: float, correct: bool, *, guess: float) -> None:
    level = aggregate.level if aggregate.level is not None else INITIAL_LEVEL
    k = K_EARLY if aggregate.level_updates < EARLY_UPDATES else K_LATE
    level += k * ((1.0 if correct else 0.0) - expected_correct(level, difficulty, guess=guess))
    aggregate.level = round(max(0.0, min(100.0, level)), 2)
    aggregate.level_updates += 1


def guess_rate(exercise: PendingExercise) -> float:
    return 1.0 / len(exercise.options) if exercise.options else 0.0


# --- typed answers ---------------------------------------------------------


def normalize(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").split())


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def grade_typed(typed: str, answer: str) -> tuple[bool, bool]:
    """``(correct, typo)``: exact or a simple inflection is correct; a small slip is a typo."""
    got, want = normalize(typed), normalize(answer)
    if not got:
        return False, False
    if got == want or got in {want + suffix for suffix in ("s", "es", "d", "ed", "ing")}:
        return True, False
    allowance = 0 if len(want) < 5 else 1 if len(want) < 9 else 2
    if edit_distance(got, want) <= allowance:
        return True, True
    return False, False


# --- cloze -----------------------------------------------------------------

BLANK = "_____"


def blank_out(sentence: str, term: str) -> str | None:
    """Replace the term (or an inflected form) with a blank.

    ``None`` if it isn't there, or if it's there more than once: blanking one use would leave
    the answer showing ("The _____ task seemed to daunt them").
    """
    words = term.split()
    if len(words) > 1:
        pattern = r"\b" + r"[\s-]+".join(re.escape(w) for w in words) + r"\w*"
    else:
        stem = term[:-1] if len(term) > 4 else term
        pattern = r"\b" + re.escape(stem) + r"\w*"
    blanked, count = re.subn(pattern, BLANK, sentence, flags=re.IGNORECASE)
    return blanked if count == 1 else None


def mentions_any(sentence: str, words: Sequence[str]) -> bool:
    return any(re.search(r"\b" + re.escape(w) + r"\b", sentence, re.IGNORECASE) for w in words)


CLOZE_SYSTEM = "You write example sentences for a vocabulary course. Reply with JSON only."


def cloze_prompt(term: TermRecord, avoid: Sequence[str] = ()) -> str:
    """``avoid`` are the other options; the sentence must rule each of them out."""
    rule_out = (
        f" Each of these words must be clearly wrong in that position: {', '.join(avoid)}."
        " Do not use any of them in the sentence."
        if avoid
        else ""
    )
    return (
        f'Write one new sentence of 12-25 words that uses "{term.term}" exactly once, in this sense: '
        f"{term.definition} The context must make the meaning inferable without being a "
        f"definition.{rule_out} "
        'Return JSON: {"sentence": "..."}'
    )


def explain_prompt(term: TermRecord, graded: GradedExercise) -> str:
    asked = {
        ExerciseType.CHOOSE_DEFINITION: f'Pick the definition of "{term.term}".',
        ExerciseType.CHOOSE_WORD: f"Pick the word that means: {graded.prompt}",
        ExerciseType.CLOZE: f"Fill the blank: {graded.prompt}",
        ExerciseType.TYPE_WORD: f"Type the word that means: {graded.prompt}",
    }[graded.type]
    if graded.chosen_term:
        chosen = f'the definition of "{graded.chosen_term}" ({graded.chosen})'
    else:
        chosen = f'"{graded.chosen}"' if graded.chosen else "nothing"
    return (
        f'Word being practised: "{term.term}" — {term.definition}\n'
        f"Exercise: {asked}\n"
        f"The learner chose {chosen}.\n\n"
        f'In two or three short sentences: say what their choice actually means (if it is not a '
        f'real word, say so), then give one concrete, memorable way to tell "{term.term}" apart '
        "from it. Plain text, no preamble."
    )


# --- engine ----------------------------------------------------------------


def interleave(reviews: list[SessionItem], new: list[SessionItem]) -> list[SessionItem]:
    """Spread new words evenly through the reviews instead of bunching them at either end."""
    if not reviews:
        return list(new)
    out: list[SessionItem] = []
    step = len(reviews) / (len(new) + 1)
    placed = 0
    for index, review in enumerate(reviews, start=1):
        out.append(review)
        while placed < len(new) and (placed + 1) * step <= index:
            out.append(new[placed])
            placed += 1
    return out + new[placed:]


class SessionStore:
    def __init__(self, table: Any | None = None) -> None:
        self._table = table or _table()

    def put(self, state: SessionState) -> None:
        item = _floats_to_decimal(state.model_dump(mode="json"))
        item.update(
            PK=_pk(state.user_id),
            SK=f"SESSION#{state.session_id}",
            ttl=int(state.created_at) + SESSION_TTL_SECONDS,
        )
        self._table.put_item(Item=item)

    def get(self, user_id: str, session_id: str) -> SessionState | None:
        item = self._table.get_item(Key={"PK": _pk(user_id), "SK": f"SESSION#{session_id}"}).get("Item")
        if not item:
            return None
        return SessionState.model_validate({k: v for k, v in item.items() if k not in {"PK", "SK", "ttl"}})


class SessionNotFound(LookupError):
    pass


class StaleExercise(ValueError):
    pass


class SessionEngine:
    def __init__(
        self,
        *,
        progress: ProgressStore | None = None,
        terms: TermIndex | None = None,
        vectors: VectorStore | None = None,
        bedrock: BedrockClient | None = None,
        pool: VocabularyPool | None = None,
        sessions: SessionStore | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._progress = progress or ProgressStore()
        self._terms = terms or TermIndex()
        self._vectors = vectors or VectorStore()
        self._bedrock = bedrock or BedrockClient()
        self._pool = pool or VocabularyPool(index=self._terms, store=self._vectors, bedrock=self._bedrock)
        self._sessions = sessions or SessionStore()
        self._rng = rng or random.Random()

    # --- start ---

    def start(self, user_id: str, *, new_words: int = NEW_PER_SESSION) -> SessionStarted:
        aggregate = self._progress.get_aggregate(user_id)
        level = aggregate.level if aggregate.level is not None else INITIAL_LEVEL
        records = self._progress.list_progress(user_id)
        now = utcnow()
        due = sorted((r for r in records if r.due <= now), key=lambda r: r.due)[:MAX_REVIEWS]
        fresh = self.pick_new_words(user_id, level, records, count=new_words)

        items = interleave(
            [SessionItem(term_id=r.term_id, kind="review") for r in due],
            [SessionItem(term_id=term_id, kind="new") for term_id in fresh],
        )

        state = SessionState(
            session_id=uuid.uuid4().hex[:12],
            user_id=user_id,
            items=items,
            queue=list(range(len(items))),
            level_start=level,
        )
        self._sessions.put(state)
        return SessionStarted(
            session_id=state.session_id, reviews=len(due), new_words=len(fresh), level=level
        )

    def pick_new_words(
        self, user_id: str, level: float, records: Sequence[ProgressRecord], *, count: int
    ) -> list[str]:
        """Unseen terms just above ``level``; half chosen for similarity to recent mastery."""
        if count <= 0:
            return []
        seen = {r.term_id for r in records}
        lo, hi = int(level), int(level) + NEW_BAND_WIDTH
        candidates = [t for t in self._terms.terms_in_band(lo, hi) if t.term_id not in seen]
        if len(candidates) < count:
            # Blocking refill only when this session would otherwise come up short.
            self._pool.ensure_band(
                lo, hi, exclude_ids=seen, min_available=count, batch_size=max(count, 8),
                seeds=self._recent_mastered_terms(records),
            )
            candidates = [t for t in self._terms.terms_in_band(lo, hi) if t.term_id not in seen]
        if len(candidates) < count:
            candidates = [
                t for t in self._terms.terms_in_band(max(0, lo - 10), hi + 10) if t.term_id not in seen
            ]
        candidate_ids = {t.term_id for t in candidates}

        picked: list[str] = []
        for term_id in self._similar_to_mastered(records, lo, hi):
            if term_id in candidate_ids and term_id not in picked and len(picked) < count // 2:
                picked.append(term_id)
        rest = [t.term_id for t in candidates if t.term_id not in picked]
        self._rng.shuffle(rest)
        return picked + rest[: count - len(picked)]

    def _recent_mastered(self, records: Sequence[ProgressRecord]) -> list[ProgressRecord]:
        mastered = [r for r in records if r.state is CardState.REVIEW and r.last_review]
        return sorted(mastered, key=lambda r: r.last_review or utcnow(), reverse=True)[:3]

    def _recent_mastered_terms(self, records: Sequence[ProgressRecord]) -> list[str]:
        ids = [r.term_id for r in self._recent_mastered(records)]
        return [t.term for t in self._terms.get_terms(ids).values()]

    def _similar_to_mastered(self, records: Sequence[ProgressRecord], lo: int, hi: int) -> list[str]:
        seeds = [r.term_id for r in self._recent_mastered(records)]
        out: list[str] = []
        for embedding in self._vectors.get_embeddings(seeds).values():
            hits = self._vectors.query(
                embedding,
                top_k=10,
                filter={"$and": [{"scope": {"$eq": "global"}}, {"difficulty": {"$gte": lo}},
                                 {"difficulty": {"$lt": hi}}]},
                return_metadata=False,
            )
            out.extend(hit["key"] for hit in hits if hit.get("key"))
        return out

    # --- next exercise ---

    def next_exercise(self, user_id: str, session_id: str) -> Exercise | SessionSummary:
        state = self._load(user_id, session_id)
        if state.current is None:
            state.current = self._build(state)
            self._sessions.put(state)
        if state.current is None:
            return self.summarize(state)
        return Exercise.model_validate(state.current.model_dump(exclude={"term_id", "answer_index", "answer_text"}))

    def _build(self, state: SessionState) -> PendingExercise | None:
        """Exercise for the front of the queue, or ``None`` when the session is over."""
        term = None
        while state.queue and term is None:
            item = state.items[state.queue[0]]
            term = self._terms.get_terms([item.term_id]).get(item.term_id)
            if term is None:  # vanished from the index; drop it rather than fail the session
                state.queue.pop(0)
                item.phase, item.outcome = Phase.DONE, "missing"
        if term is None:
            return None
        record = self._progress.get_progress(state.user_id, term.term_id)
        exercise_type = self.choose_type(item, record)
        common: dict[str, Any] = {
            "exercise_id": uuid.uuid4().hex[:12],
            "session_id": state.session_id,
            "kind": item.kind,
            "attempt": item.attempts + 1,
            "remaining": len(state.queue),
            "term_id": term.term_id,
        }

        if exercise_type is ExerciseType.TYPE_WORD:
            letters = len(term.term.replace(" ", ""))
            return PendingExercise(
                type=exercise_type, prompt=term.definition, answer_text=term.term,
                hint=f"{term.term[0]}… ({letters} letters)", **common,
            )
        if exercise_type is ExerciseType.CHOOSE_DEFINITION:
            pairs = self.distractor_definitions(term)
            if len(pairs) >= OPTION_COUNT - 1:
                exercise = self._multiple_choice(
                    exercise_type, term.term, term.definition, [d for _, d in pairs], common
                )
                owners = {d: w for w, d in pairs} | {term.definition: term.term}
                exercise.option_terms = [owners[option] for option in exercise.options]
                return exercise
            exercise_type = ExerciseType.CHOOSE_WORD  # corpus too small for definition options
        if exercise_type is ExerciseType.CLOZE:
            # No confusables here: "automated" can fill "the ___ drone" as well as "autonomous".
            cloze_words = self.distractor_words(term, confusables=False)
            sentence = self.cloze_sentence(term, avoid=cloze_words)
            if sentence:
                return self._multiple_choice(exercise_type, sentence, term.term, cloze_words, common)
        words = self.distractor_words(term)
        return self._multiple_choice(ExerciseType.CHOOSE_WORD, term.definition, term.term, words, common)

    def _multiple_choice(
        self, exercise_type: ExerciseType, prompt: str, answer: str, distractors: list[str],
        common: dict[str, Any],
    ) -> PendingExercise:
        options = [answer, *distractors[: OPTION_COUNT - 1]]
        self._rng.shuffle(options)
        return PendingExercise(
            type=exercise_type, prompt=prompt, options=options,
            answer_index=options.index(answer), answer_text=answer, **common,
        )

    @staticmethod
    def choose_type(item: SessionItem, record: ProgressRecord | None) -> ExerciseType:
        if item.phase is Phase.CONFIRM:
            return ExerciseType.TYPE_WORD
        if item.phase is Phase.RELEARN:
            relearn = [ExerciseType.CHOOSE_WORD, ExerciseType.CLOZE]
            choice = relearn[item.misses % 2]
            return relearn[(item.misses + 1) % 2] if choice is item.last_type else choice
        if item.kind == "new" or record is None:
            return ExerciseType.CHOOSE_DEFINITION
        if record.state is not CardState.REVIEW or record.stability < 7:
            return ExerciseType.CHOOSE_WORD
        if record.stability < 30:
            return ExerciseType.CLOZE
        return ExerciseType.TYPE_WORD

    # --- distractors & cloze ---

    def _neighbours(self, term: TermRecord, top_k: int = 15) -> list[dict[str, Any]]:
        embedding = self._vectors.get_embeddings([term.term_id]).get(term.term_id)
        if embedding is None:
            return []
        hits = self._vectors.query(
            embedding, top_k=top_k,
            filter={"$and": [{"scope": {"$eq": "global"}}, {"difficulty": {"$gte": 0}}]},
        )
        blocked = {s.lower() for s in term.synonyms} | {term.term.lower()}
        out = []
        for hit in hits:
            meta = hit.get("metadata") or {}
            word = str(meta.get("term", ""))
            synonyms = {str(s).lower() for s in meta.get("synonyms") or []}
            if (
                hit.get("key") == term.term_id
                or not word
                or word.lower() in blocked
                or term.term.lower() in synonyms
                or same_family(word, term.term)
            ):
                continue
            out.append(hit)
        return out

    def _fallback_terms(self, term: TermRecord) -> list[TermRecord]:
        nearby = [
            t for t in self._terms.terms_in_band(max(0, term.difficulty - 20), term.difficulty + 20)
            if t.term_id != term.term_id and t.term.lower() not in {s.lower() for s in term.synonyms}
        ]
        self._rng.shuffle(nearby)
        return nearby

    def distractor_definitions(self, term: TermRecord) -> list[tuple[str, str]]:
        """``(word, definition)`` pairs; the word is kept so a miss can be explained."""
        found = [
            (str(meta.get("term", "")), str(meta.get("definition", "")))
            for meta in ((h.get("metadata") or {}) for h in self._neighbours(term))
        ]
        found = [(w, d) for w, d in found if d and d != term.definition]
        if len(found) < OPTION_COUNT - 1:
            known = {d for _, d in found}
            found += [(t.term, t.definition) for t in self._fallback_terms(term) if t.definition not in known]
        return found[: OPTION_COUNT - 1]

    def distractor_words(self, term: TermRecord, *, confusables: bool = True) -> list[str]:
        """Confusables first ("flout" for "flaunt"), then semantic neighbours, then any nearby term."""
        words = list(term.confused_with) if confusables else []
        # Same part of speech first, so options can't be ruled out on grammar alone.
        neighbours = sorted(
            self._neighbours(term),
            key=lambda h: str((h.get("metadata") or {}).get("pos", "")) != term.pos,
        )
        words += [str((h.get("metadata") or {}).get("term")) for h in neighbours]
        if len(words) < OPTION_COUNT - 1:
            fallback = sorted(self._fallback_terms(term), key=lambda t: t.pos != term.pos)
            words += [t.term for t in fallback]
        unique: list[str] = []
        for word in words:
            if word.lower() != term.term.lower() and word.lower() not in {u.lower() for u in unique}:
                unique.append(word)
        return unique[: OPTION_COUNT - 1]

    def cloze_sentence(self, term: TermRecord, avoid: Sequence[str] = ()) -> str | None:
        """A fresh sentence with the term blanked; stored examples are the fallback.

        A generated sentence is rejected if it uses the term twice or mentions another option
        (the model doesn't always follow that instruction), and regenerated once.
        """
        for _ in range(2):
            try:
                reply = self._bedrock.generate(
                    cloze_prompt(term, avoid),
                    system=CLOZE_SYSTEM,
                    model_id=get_settings().exercise_model_id or None,
                    max_tokens=200,
                    temperature=0.9,  # a fresh sentence each time, not the same one reworded
                )
                sentence = str(extract_json(reply).get("sentence", ""))
            except Exception:  # noqa: BLE001 - fall back to a stored example
                break
            blanked = blank_out(sentence, term.term)
            if blanked and not mentions_any(blanked, avoid):
                return blanked
        for example in self._rng.sample(term.examples, k=len(term.examples)):
            blanked = blank_out(example, term.term)
            if blanked and not mentions_any(blanked, avoid):
                return blanked
        return None

    # --- answer ---

    def answer(
        self, user_id: str, session_id: str, exercise_id: str, *, choice: int | None = None,
        text: str | None = None,
    ) -> AnswerResult:
        state = self._load(user_id, session_id)
        exercise = state.current
        if exercise is None or exercise.exercise_id != exercise_id:
            raise StaleExercise("that exercise is no longer current")
        term = self._terms.get_terms([exercise.term_id])[exercise.term_id]
        item = state.items[state.queue[0]]

        typo = False
        if exercise.type is ExerciseType.TYPE_WORD:
            chosen = text or ""
            correct, typo = grade_typed(chosen, exercise.answer_text)
        else:
            valid = choice is not None and 0 <= choice < len(exercise.options)
            chosen = exercise.options[choice] if valid and choice is not None else ""
            correct = choice == exercise.answer_index

        aggregate = self._progress.get_aggregate(user_id)
        if item.attempts == 0:
            update_level(aggregate, term.difficulty, correct, guess=guess_rate(exercise))
        item.attempts += 1
        item.last_type = exercise.type
        rating = self._advance(item, correct, typo)
        if rating is not None:
            self._review(user_id, term.term_id, rating)

        position = state.queue.pop(0)
        if item.phase is not Phase.DONE:
            gap = CONFIRM_GAP if item.phase is Phase.CONFIRM else REQUEUE_GAP
            state.queue.insert(min(gap, len(state.queue)), position)
        state.graded.append(
            GradedExercise(
                exercise_id=exercise.exercise_id, term_id=term.term_id, type=exercise.type,
                prompt=exercise.prompt, options=exercise.options, chosen=chosen,
                chosen_term=(
                    exercise.option_terms[exercise.options.index(chosen)]
                    if chosen and exercise.option_terms else ""
                ),
                correct_answer=exercise.answer_text, correct=correct,
            )
        )
        state.current = None

        self._progress.put_aggregate(aggregate)
        summary = None
        if not state.queue:
            # recompute_aggregate re-reads the level just written and refreshes streak + counts
            self._progress.put_aggregate(self._progress.recompute_aggregate(user_id))
            summary = self.summarize(state, level_end=aggregate.level)
        self._sessions.put(state)

        return AnswerResult(
            correct=correct, typo=typo, chosen=chosen, correct_answer=exercise.answer_text,
            entry=EntryView.of(term),
            next_step="done" if item.phase is Phase.DONE else ("confirm" if item.phase is Phase.CONFIRM else "requeued"),
            remaining=len(state.queue),
            level=aggregate.level if aggregate.level is not None else INITIAL_LEVEL,
            summary=summary,
        )

    @staticmethod
    def _advance(item: SessionItem, correct: bool, typo: bool) -> Rating | None:
        """Move the item through its phases; return the FSRS rating to apply now, if any."""
        if item.phase is Phase.RELEARN:
            if correct:
                item.phase = Phase.DONE
                item.outcome = "learned" if item.kind == "new" else "relearned"
            else:
                item.misses += 1
                if item.attempts >= MAX_ATTEMPTS:
                    item.phase, item.outcome = Phase.DONE, "missed"
            return None
        if not correct:
            item.phase, item.misses = Phase.RELEARN, item.misses + 1
            return Rating.AGAIN
        if item.kind == "new" and item.phase is Phase.PROBE:
            item.phase = Phase.CONFIRM  # right on first sight: confirm it wasn't a guess
            return None
        item.phase = Phase.DONE
        if item.kind == "new":
            item.outcome = "known"
            return Rating.EASY
        item.outcome = "remembered"
        return Rating.HARD if typo else Rating.GOOD

    def _review(self, user_id: str, term_id: str, rating: Rating) -> None:
        scheduler = get_scheduler()
        record = self._progress.get_progress(user_id, term_id) or scheduler.new_record(user_id, term_id)
        self._progress.put_progress(scheduler.review(record, rating))

    # --- explain & summary ---

    def explain(self, user_id: str, session_id: str, exercise_id: str) -> str:
        state = self._load(user_id, session_id)
        graded = next((g for g in state.graded if g.exercise_id == exercise_id), None)
        if graded is None:
            raise StaleExercise("unknown exercise")
        if graded.explanation:
            return graded.explanation
        term = self._terms.get_terms([graded.term_id])[graded.term_id]
        graded.explanation = self._bedrock.generate(
            explain_prompt(term, graded),
            system="You are a precise, encouraging vocabulary tutor. Never give generic study advice.",
            model_id=get_settings().exercise_model_id or None,
            max_tokens=200,
        ).strip()
        self._sessions.put(state)
        return graded.explanation

    @staticmethod
    def summarize(state: SessionState, *, level_end: float | None = None) -> SessionSummary:
        summary = SessionSummary(level_start=state.level_start, level_end=level_end or state.level_start)
        for item in state.items:
            if item.kind == "new":
                summary.new_words += 1
            summary.already_known += item.outcome == "known"
            summary.learned += item.outcome == "learned"
            summary.reviews_remembered += item.outcome == "remembered"
            summary.reviews_relearned += item.outcome == "relearned"
            summary.gave_up += item.outcome == "missed"
        return summary

    def _load(self, user_id: str, session_id: str) -> SessionState:
        state = self._sessions.get(user_id, session_id)
        if state is None:
            raise SessionNotFound(session_id)
        return state

    # --- background growth ---

    def prefetch(self, user_id: str) -> int:
        """Top up the learner's next band ahead of time (client calls this fire-and-forget)."""
        aggregate = self._progress.get_aggregate(user_id)
        level = aggregate.level if aggregate.level is not None else INITIAL_LEVEL
        records = self._progress.list_progress(user_id)
        report = self._pool.ensure_band(
            int(level), int(level) + NEW_BAND_WIDTH,
            exclude_ids={r.term_id for r in records}, min_available=3 * NEW_PER_SESSION,
            seeds=self._recent_mastered_terms(records) or None,
        )
        return len(report.added)
