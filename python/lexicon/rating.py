"""Batch difficulty rating: score pooled candidates with the enrichment model before enriching.

Frequency alone can't tell "rare" from "hard for a fluent reader": candidates it put at 70-80
were enriched at 30-50. Rating ~50 headwords per call on the anchored scale
(``difficulty.DIFFICULTY_SCALE``) puts pooled scores on the same scale as published ones, so a
band refill delivers words in that band, and it flags junk and derived forms before anyone pays
for a full entry.
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .bedrock import BedrockClient
from .config import get_settings
from .difficulty import DIFFICULTY_SCALE
from .enrichment import extract_json

BATCH_SIZE = 50
WORKERS = 4

SYSTEM_PROMPT = (
    "You are a lexicographer curating an advanced vocabulary course for fluent, well-read adult "
    "native English speakers. Always reply with a single JSON object and nothing else."
)


@dataclass(frozen=True)
class Rating:
    difficulty: int | None
    skip: bool = False
    reason: str = ""


def rating_prompt(words: Sequence[str]) -> str:
    listed = "\n".join(f"- {word}" for word in words)
    return (
        "Rate each word below.\n\n"
        f"{DIFFICULTY_SCALE}\n\n"
        'Set "skip" to true for proper nouns, abbreviations, slang or nonstandard spellings, '
        "offensive words, and plain inflections or transparent derivations of a more common word "
        '(e.g. "hauntingly", "contractually"); otherwise false.\n\n'
        f"Words:\n{listed}\n\n"
        'Return JSON: {"words": [{"word": "...", "difficulty": 0-100, "skip": false, '
        '"reason": "short reason when skip is true"}]} with one entry per word, in order.'
    )


def parse_ratings(raw: str) -> dict[str, Rating]:
    """Map lowercase word -> rating; malformed entries are dropped."""
    entries = extract_json(raw).get("words") or []
    out: dict[str, Rating] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not str(entry.get("word", "")).strip():
            continue
        try:
            difficulty: int | None = max(0, min(100, int(entry.get("difficulty"))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            difficulty = None
        out[str(entry["word"]).strip().lower()] = Rating(
            difficulty=difficulty,
            skip=bool(entry.get("skip")),
            reason=str(entry.get("reason", "")).strip(),
        )
    return out


class BatchRater:
    def __init__(self, bedrock: BedrockClient | None = None, *, model_id: str | None = None) -> None:
        self._bedrock = bedrock or BedrockClient()
        self._model_id = model_id or get_settings().rate_model_id or None

    def rate(self, words: Sequence[str]) -> dict[str, Rating]:
        """Rate every word; words in a failed batch are simply absent (caller falls back)."""
        batches = [list(words[i : i + BATCH_SIZE]) for i in range(0, len(words), BATCH_SIZE)]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(self._rate_batch, batches))
        merged: dict[str, Rating] = {}
        for result in results:
            merged.update(result)
        return merged

    def _rate_batch(self, words: list[str]) -> dict[str, Rating]:
        try:
            reply = self._bedrock.generate(
                rating_prompt(words),
                system=SYSTEM_PROMPT,
                model_id=self._model_id,
                max_tokens=40 * len(words) + 200,  # ~25 tokens per entry, plus skip reasons
            )
            return parse_ratings(reply)
        except Exception:  # noqa: BLE001 - frequency-only scores are an acceptable fallback
            return {}
