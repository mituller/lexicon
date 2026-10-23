"""Bedrock-suggested vocabulary: an unbounded source for the hard end of the scale.

The curated lists run thin above difficulty ~70, so when a band has no candidates left, Nova Lite
proposes words "like these, but at this difficulty", seeded with words the learner has (or the
pool already has) at that level. Every suggestion is then checked against Datamuse: it must be
an exact dictionary match with measurable frequency, which drops invented words (Datamuse
returns nothing, or a spell-corrected different word) without dropping genuinely rare ones
("pulchritudinous" is ~0.004 per million).
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from .bedrock import BedrockClient
from .difficulty import DIFFICULTY_SCALE, FrequencyLookup
from .enrichment import extract_json
from .sources import Candidate, is_plausible_headword

SOURCE_NAME = "bedrock-suggest"
MIN_PER_MILLION = 0.002
MAX_SEEDS, MAX_EXCLUDES = 20, 60

SYSTEM_PROMPT = (
    "You are a lexicographer building an advanced vocabulary course for fluent, well-read adult "
    "native English speakers. Always reply with a single JSON object and nothing else."
)


def suggestion_prompt(
    seeds: Sequence[str], *, difficulty: int, count: int, exclude: Sequence[str] = ()
) -> str:
    seed_text = ", ".join(seeds[:MAX_SEEDS]) or "(none yet)"
    exclude_text = ", ".join(exclude[:MAX_EXCLUDES]) or "(none)"
    return (
        f"Suggest {count} English words for the course at difficulty {difficulty}.\n"
        f"{DIFFICULTY_SCALE}\n"
        f"Words already at roughly this level, for calibration and related themes: {seed_text}\n"
        f"Do not suggest any of these: {exclude_text}\n\n"
        "Rules: real dictionary headwords in their base form; no proper nouns, abbreviations, "
        "slang, or offensive words; no plain inflections or transparent derivations of easier "
        'words (e.g. not "indolently" when "indolent" exists); prefer words a reader would '
        "actually meet in good journalism, essays, or literature.\n\n"
        'Return JSON: {"words": [{"word": "...", "pos": "noun|verb|adjective|adverb|phrase"}]}'
    )


def parse_suggestions(raw: str) -> list[tuple[str, str]]:
    """Extract ``(word, pos)`` pairs; tolerates a bare list of strings."""
    payload = extract_json(raw)
    words = payload.get("words") or []
    out: list[tuple[str, str]] = []
    for entry in words if isinstance(words, list) else []:
        if isinstance(entry, str):
            out.append((entry.strip(), ""))
        elif isinstance(entry, dict):
            out.append((str(entry.get("word", "")).strip(), str(entry.get("pos", "")).strip().lower()))
    return [(word, pos) for word, pos in out if word]


class BedrockSuggestionSource:
    """A :class:`~lexicon.sources.WordSource` whose words come from Nova Lite, verified by Datamuse."""

    name = SOURCE_NAME
    attribution = "Suggested by Amazon Nova Lite, verified against Datamuse"

    def __init__(
        self,
        *,
        seeds: Sequence[str],
        difficulty: int,
        count: int,
        frequency: FrequencyLookup,
        exclude: Sequence[str] = (),
        bedrock: BedrockClient | None = None,
    ) -> None:
        self.seeds = list(seeds)
        self.difficulty = difficulty
        self.count = count
        self.exclude = list(exclude)
        self._frequency = frequency
        self._bedrock = bedrock or BedrockClient()
        self.rejected: list[str] = []

    def fetch(self) -> list[Candidate]:
        prompt = suggestion_prompt(
            self.seeds, difficulty=self.difficulty, count=self.count, exclude=self.exclude
        )
        try:
            suggestions = parse_suggestions(self._bedrock.generate(prompt, system=SYSTEM_PROMPT))
        except (ValueError, json.JSONDecodeError):
            return []
        blocked = {w.lower() for w in [*self.seeds, *self.exclude]}
        out: list[Candidate] = []
        for word, pos in suggestions:
            if word.lower() in blocked or not is_plausible_headword(word):
                self.rejected.append(word)
                continue
            per_million = self._frequency.per_million(word)
            if per_million is None or per_million < MIN_PER_MILLION:
                self.rejected.append(word)  # not a verifiable dictionary word
                continue
            blocked.add(word.lower())
            out.append(Candidate(word, SOURCE_NAME, self.difficulty, pos=pos))
        return out
