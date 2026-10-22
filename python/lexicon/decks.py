"""Vocabulary decks, retention review, and quizzes.

A *deck* is a slice of the seeded corpus by ``domain`` + ``difficulty_tier`` (each term also
carries a ``cefr_level``). The learner picks a deck, studies flashcards, self-rates, and the
FSRS scheduler decides when each term comes back. Quizzes are deterministic multiple-choice
(no LLM cost) and grade back into FSRS.

S3 Vectors cannot filter by metadata when *listing*, so we enumerate the whole index and slice
it in Python. That's fine at the current corpus size (hundreds of terms); the scaling path is a
DynamoDB term index if the corpus grows to tens of thousands.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from .dynamo import ProgressStore
from .models import CardState, Rating
from .srs import get_scheduler
from .vectorstore import VectorStore


def vector_to_term(item: dict[str, Any]) -> dict[str, Any]:
    """Map an S3 Vectors row to a plain term dict (term_id == vector key)."""
    metadata: dict[str, Any] = item.get("metadata") or {}
    synonyms = metadata.get("synonyms") or []
    return {
        "term_id": item.get("key", ""),
        "term": str(metadata.get("term", "")),
        "definition": str(metadata.get("definition", "")),
        "example": str(metadata.get("example", "")),
        "synonyms": [str(s) for s in synonyms],
        "pos": str(metadata.get("pos", "")),
        "domain": str(metadata.get("domain", "")),
        "subfield": str(metadata.get("subfield", "")),
        "difficulty_tier": str(metadata.get("difficulty_tier", "")),
        "cefr_level": str(metadata.get("cefr_level", "")),
    }


class DeckSummary(BaseModel):
    """A selectable group of vocabulary terms."""

    domain: str
    difficulty_tier: str
    count: int
    cefr_levels: list[str] = []


class QuizQuestion(BaseModel):
    """One multiple-choice question (correct answer is shuffled server-side)."""

    term_id: str
    term: str
    options: list[str]
    answer_index: int


class Quiz(BaseModel):
    domain: str
    difficulty_tier: str
    questions: list[QuizQuestion]


class DueTerm(BaseModel):
    """A term due for review, joined with its current FSRS progress."""

    term_id: str
    term: str
    definition: str
    example: str = ""
    synonyms: list[str] = []
    domain: str = ""
    difficulty_tier: str = ""
    cefr_level: str = ""
    state: str = CardState.NEW.value
    reps: int = 0
    stability: float = 0.0


class DeckService:
    """Reads the corpus and composes decks, review queues, and quizzes."""

    def __init__(self, store: VectorStore | None = None) -> None:
        self._store = store or VectorStore()

    def all_terms(self) -> list[dict[str, Any]]:
        """Every global term in the index (user-scoped and global)."""
        return [vector_to_term(item) for item in self._store.list_all_vectors()]

    def list_decks(self) -> list[DeckSummary]:
        """Group the corpus into ``(domain, difficulty_tier)`` decks."""
        grouped: dict[tuple[str, str], Counter[str]] = {}
        for term in self.all_terms():
            if not term["term"]:
                continue
            key = (term["domain"], term["difficulty_tier"])
            grouped.setdefault(key, Counter())[term["cefr_level"]] += 1
        decks: list[DeckSummary] = []
        for (domain, tier), cefr_counter in sorted(grouped.items()):
            decks.append(
                DeckSummary(
                    domain=domain,
                    difficulty_tier=tier,
                    count=sum(cefr_counter.values()),
                    cefr_levels=sorted(cefr_counter.keys()),
                )
            )
        return decks

    def deck_terms(
        self,
        *,
        domain: str,
        difficulty_tier: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Terms in one deck, deterministic order, capped at ``limit``."""
        matches = [
            term
            for term in self.all_terms()
            if term["domain"] == domain and term["difficulty_tier"] == difficulty_tier
        ]
        matches.sort(key=lambda term: (term["cefr_level"], term["term"]))
        return matches[:limit]

    def start_deck(
        self,
        progress: ProgressStore,
        user_id: str,
        *,
        domain: str,
        difficulty_tier: str,
    ) -> int:
        """Create a progress record for every term in the deck (idempotent)."""
        scheduler = get_scheduler()
        created = 0
        for term in self.deck_terms(domain=domain, difficulty_tier=difficulty_tier):
            if progress.get_progress(user_id, term["term_id"]) is None:
                progress.put_progress(scheduler.new_record(user_id, term["term_id"]))
                created += 1
        return created

    def due_terms(
        self,
        progress: ProgressStore,
        user_id: str,
        *,
        now=None,
        limit: int = 20,
    ) -> list[DueTerm]:
        """Terms due now, joined with their payloads, oldest-due first."""
        from .models import utcnow

        reference = now or utcnow()
        records = [
            record
            for record in progress.list_progress(user_id)
            if record.due <= reference
        ]
        records.sort(key=lambda record: record.due)
        records = records[:limit]
        if not records:
            return []

        keys = [record.term_id for record in records]
        payloads = {item.get("key"): vector_to_term(item) for item in self._store.get_vectors(keys)}
        due: list[DueTerm] = []
        for record in records:
            payload = payloads.get(record.term_id, {})
            due.append(
                DueTerm(
                    term_id=record.term_id,
                    term=payload.get("term", record.term_id),
                    definition=payload.get("definition", ""),
                    example=payload.get("example", ""),
                    synonyms=payload.get("synonyms", []),
                    domain=payload.get("domain", ""),
                    difficulty_tier=payload.get("difficulty_tier", ""),
                    cefr_level=payload.get("cefr_level", ""),
                    state=record.state.value,
                    reps=record.reps,
                    stability=record.stability,
                )
            )
        return due

    def make_quiz(
        self,
        *,
        domain: str,
        difficulty_tier: str,
        count: int = 10,
        rng: random.Random | None = None,
    ) -> Quiz:
        """Build a multiple-choice quiz from the deck.

        Each question offers the correct definition plus three distractor definitions drawn from
        the same deck (or the whole corpus if the deck is small).
        """
        rng = rng or random.Random()
        deck = self.deck_terms(domain=domain, difficulty_tier=difficulty_tier)
        if not deck:
            return Quiz(domain=domain, difficulty_tier=difficulty_tier, questions=[])

        pool = deck if len(deck) >= 4 else self.all_terms()
        selected = rng.sample(deck, k=min(count, len(deck)))
        questions: list[QuizQuestion] = []
        for term in selected:
            distractors = [t["definition"] for t in pool if t["term_id"] != term["term_id"]]
            if len(distractors) >= 3:
                options = [term["definition"]] + rng.sample(distractors, k=3)
            else:
                options = [term["definition"]] + distractors
            rng.shuffle(options)
            questions.append(
                QuizQuestion(
                    term_id=term["term_id"],
                    term=term["term"],
                    options=options,
                    answer_index=options.index(term["definition"]),
                )
            )
        return Quiz(domain=domain, difficulty_tier=difficulty_tier, questions=questions)


def grade_answer(correct: bool) -> Rating:
    """Map a quiz answer to an FSRS rating: correct -> Good, wrong -> Again."""
    return Rating.GOOD if correct else Rating.AGAIN


def apply_quiz_results(
    progress: ProgressStore,
    user_id: str,
    answers: Sequence[dict[str, Any]],
) -> dict[str, int]:
    """Grade answers and apply them to FSRS, returning a score summary."""
    scheduler = get_scheduler()
    correct = 0
    for answer in answers:
        term_id = str(answer.get("term_id", ""))
        is_correct = bool(answer.get("correct"))
        if not term_id:
            continue
        if is_correct:
            correct += 1
        record = progress.get_progress(user_id, term_id) or scheduler.new_record(user_id, term_id)
        record = scheduler.review(record, grade_answer(is_correct))
        progress.put_progress(record)
    progress.put_aggregate(progress.recompute_aggregate(user_id))
    return {"correct": correct, "total": len(answers)}