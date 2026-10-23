"""Difficulty scoring on a 0-100 scale for a fluent adult reader.

Three signals, combined in :func:`combine`:

* **source baseline** — where the word came from (NAWL < Octanove C1 < C2 < your own hard lists);
* **word frequency** — rarer words are harder. Frequency comes from the Datamuse API (per million
  words, Google Books n-grams) and is converted to the Zipf scale;
* **model estimate** — Bedrock's 0-100 judgement, available only after enrichment.

The score is stored as a number in both DynamoDB (sort key) and S3 Vectors (filterable metadata),
so "words just above the learner's level" is a cheap range query.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

DATAMUSE_URL = "https://api.datamuse.com/words"

# Shared by every prompt that asks a model for a difficulty, so models calibrate the same way
# (without anchors Claude rates nearly everything below 30 and Nova Lite nearly everything above 60).
DIFFICULTY_SCALE = (
    "Difficulty is 0-100 for a fluent, well-read adult native speaker. Anchors: "
    '10 = "reluctant", 30 = "ubiquitous" or "pragmatic", 50 = "laconic" or "obsequious", '
    '70 = "antediluvian" or "perspicacious", 90 = "pulchritudinous" or "sesquipedalian".'
)

# Zipf 6+ (e.g. "house") is trivial; Zipf 1 (about one per hundred million words) is very rare.
_ZIPF_EASY, _ZIPF_RARE = 6.0, 1.0


class FrequencyLookup(Protocol):
    def per_million(self, word: str) -> float | None: ...


def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def zipf(per_million: float) -> float:
    """Zipf scale: log10 of frequency per billion words."""
    return math.log10(per_million) + 3 if per_million > 0 else 0.0


def frequency_difficulty(per_million: float) -> int:
    """Map frequency to 0-100: common words score low, rare ones high."""
    return clamp((_ZIPF_EASY - zipf(per_million)) / (_ZIPF_EASY - _ZIPF_RARE) * 100)


def combine(base: int, per_million: float | None = None, model: int | None = None) -> int:
    """Blend the available signals. Frequency is the most objective, so it gets the most weight."""
    weighted = [(float(base), 0.3)]
    if per_million is not None:
        weighted.append((float(frequency_difficulty(per_million)), 0.5))
    if model is not None:
        weighted.append((float(model), 0.2))
    total = sum(weight for _, weight in weighted)
    return clamp(sum(value * weight for value, weight in weighted) / total)


# (suffix, replacement) pairs that undo common transparent derivations.
_DERIVATIONS = [
    ("ably", "able"),  # unfeasibly -> unfeasible (checked before the bare "ly" rule)
    ("ibly", "ible"),
    ("ily", "y"),  # happily -> happy
    ("ly", ""),  # bereftly -> bereft, contractually -> contractual
    ("iness", "y"),  # tidiness -> tidy
    ("ness", ""),  # bereftness -> bereft
    ("ical", "ic"),  # choreographical -> choreographic
]


def possible_bases(word: str) -> list[str]:
    """Base words ``word`` might be a transparent derivation of (unverified)."""
    lowered = word.lower()
    return [
        lowered[: -len(suffix)] + replacement
        for suffix, replacement in _DERIVATIONS
        if lowered.endswith(suffix) and len(lowered) - len(suffix) >= 4
    ]


def derived_from(word: str, frequency: FrequencyLookup) -> str | None:
    """The more common base word if ``word`` is just a derivation of it, else ``None``.

    Learning "indolent" teaches "indolently"; the derived form isn't worth its own card. A base
    counts if it is at least as frequent as the derived word ("derisive" is only ~1.3x
    "derisively"), and stripping a suffix off a non-derived word ("homily") yields no real base.
    """
    own = frequency.per_million(word) or 0.0
    for base in possible_bases(word):
        base_frequency = frequency.per_million(base)
        if base_frequency is not None and base_frequency >= max(own, 0.01):
            return base
    return None


def cefr_for(difficulty: int) -> str:
    """Coarse CEFR label for display and legacy filters (this app's floor is B2)."""
    if difficulty < 45:
        return "B2"
    if difficulty < 65:
        return "C1"
    return "C2"


def spelling_variants(word: str) -> list[str]:
    """"ad lib" / "ad-lib" / "adlib": corpora split counts across spellings."""
    variants = [word]
    if " " in word or "-" in word:
        for joiner in (" ", "-", ""):
            variant = joiner.join(word.replace("-", " ").split())
            if variant not in variants:
                variants.append(variant)
    return variants


class DatamuseFrequency:
    """Frequency lookups against the free Datamuse API (no key; ~100k requests/day)."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self._cache: dict[str, float | None] = {}

    def per_million(self, word: str) -> float | None:
        """Frequency of the most common spelling, so variants don't read as rare words."""
        found = [f for f in (self._cached(v) for v in spelling_variants(word.lower())) if f is not None]
        return max(found) if found else None

    def _cached(self, key: str) -> float | None:
        if key not in self._cache:
            self._cache[key] = self._fetch(key)
        return self._cache[key]

    def _fetch(self, word: str) -> float | None:
        query = urllib.parse.urlencode({"sp": word, "md": "f", "max": 1})
        try:
            with urllib.request.urlopen(f"{DATAMUSE_URL}?{query}", timeout=self.timeout) as resp:  # noqa: S310
                rows = json.loads(resp.read())
        except (OSError, ValueError):
            return None  # treat lookup failures as "unknown", never as "rare"
        return parse_datamuse(rows, word)

    def many(self, words: Iterable[str], *, workers: int = 8) -> dict[str, float | None]:
        unique = sorted({w.lower() for w in words})
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return dict(zip(unique, pool.map(self.per_million, unique), strict=True))


def parse_datamuse(rows: list[dict[str, object]], word: str) -> float | None:
    """Extract ``f:<per million>`` for an exact match from a Datamuse response."""
    for row in rows:
        if str(row.get("word", "")).lower() != word.lower():
            continue
        tags = row.get("tags")
        for tag in tags if isinstance(tags, list) else []:
            if isinstance(tag, str) and tag.startswith("f:"):
                try:
                    return float(tag[2:])
                except ValueError:
                    return None
    return None
