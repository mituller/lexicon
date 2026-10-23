"""Online vocabulary sources: curated advanced word lists -> candidate headwords.

A *source* only says which words exist and roughly how hard they are; it never supplies prose.
Candidates are scored (``difficulty.py``), parked in the DynamoDB candidate pool, and enriched by
Bedrock on demand when a learner's band runs low (``refill.py``).

Default sources (both CC BY-SA 4.0, attribution required if the derived corpus is distributed):

* **Octanove Vocabulary Profile C1/C2** — openlanguageprofiles/olp-en-cefrj.
* **New Academic Word List (NAWL) 1.2** — Browne, C., Culligan, B., & Phillips, J. (2013).

Any other list (e.g. a GRE list you have rights to) plugs in via :class:`TextListSource`.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

OCTANOVE_URL = (
    "https://raw.githubusercontent.com/openlanguageprofiles/olp-en-cefrj/master/"
    "octanove-vocabulary-profile-c1c2-1.0.csv"
)
NAWL_URL = "https://www.newgeneralservicelist.com/s/NAWL_12_lemmatized_for_teaching.csv"

# Baseline difficulty (0-100) by where a word came from; frequency refines it.
OCTANOVE_BASE = {"C1": 60, "C2": 75}
NAWL_BASE = 50

_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\- ]*[A-Za-z]$")


@dataclass(frozen=True)
class Candidate:
    """A headword offered by a source, before enrichment."""

    headword: str
    source: str
    base_difficulty: int
    pos: str = ""
    cefr_level: str = ""


class WordSource(Protocol):
    name: str
    attribution: str

    def fetch(self) -> list[Candidate]: ...


def read_text(location: str, *, encoding: str = "utf-8-sig") -> str:
    """Read a local path or an http(s) URL."""
    if location.startswith(("http://", "https://")):
        request = urllib.request.Request(location, headers={"User-Agent": "lexicon/0.1"})
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return bytes(response.read()).decode(encoding)
    return Path(location).read_text(encoding=encoding)


def is_plausible_headword(word: str) -> bool:
    """Reject numbers, abbreviations with dots, slash variants ("a.m./A.M."), and 1-2 letter words."""
    return len(word) >= 3 and bool(_WORD_RE.match(word))


class OctanoveSource:
    """Octanove C1/C2 profile (CSV: headword,pos,CEFR,notes)."""

    name = "octanove"
    attribution = "Octanove Vocabulary Profile C1/C2 (CC BY-SA 4.0)"

    def __init__(self, location: str = OCTANOVE_URL) -> None:
        self.location = location

    def fetch(self) -> list[Candidate]:
        return parse_octanove(read_text(self.location))


def parse_octanove(text: str) -> list[Candidate]:
    out: list[Candidate] = []
    for row in csv.DictReader(io.StringIO(text)):
        lowered = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        word, level = lowered.get("headword", ""), lowered.get("cefr", "").upper()
        if level not in OCTANOVE_BASE or not is_plausible_headword(word):
            continue
        out.append(
            Candidate(
                headword=word,
                source=OctanoveSource.name,
                base_difficulty=OCTANOVE_BASE[level],
                pos=lowered.get("pos", "").lower(),
                cefr_level=level,
            )
        )
    return out


class NawlSource:
    """NAWL 1.2 lemmatized list (CSV, Latin-1: headword followed by its inflections)."""

    name = "nawl"
    attribution = "New Academic Word List 1.2, Browne, Culligan & Phillips (CC BY-SA 4.0)"

    def __init__(self, location: str = NAWL_URL) -> None:
        self.location = location

    def fetch(self) -> list[Candidate]:
        return parse_nawl(read_text(self.location, encoding="latin-1"))


def parse_nawl(text: str) -> list[Candidate]:
    out: list[Candidate] = []
    for row in csv.reader(io.StringIO(text)):
        word = row[0].strip() if row else ""
        if is_plausible_headword(word):
            out.append(Candidate(headword=word, source=NawlSource.name, base_difficulty=NAWL_BASE))
    return out


class TextListSource:
    """Any one-word-per-line list (``#`` comments, ``word,anything`` tolerated)."""

    def __init__(self, name: str, location: str, *, base_difficulty: int, attribution: str = "") -> None:
        self.name = name
        self.location = location
        self.base_difficulty = base_difficulty
        self.attribution = attribution or name

    def fetch(self) -> list[Candidate]:
        return parse_text_list(read_text(self.location), self.name, self.base_difficulty)


def parse_text_list(text: str, source: str, base_difficulty: int) -> list[Candidate]:
    out: list[Candidate] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        word = re.split(r"[,\t]", stripped, maxsplit=1)[0].strip()
        if is_plausible_headword(word):
            out.append(Candidate(headword=word, source=source, base_difficulty=base_difficulty))
    return out


def default_sources() -> list[WordSource]:
    return [OctanoveSource(), NawlSource()]


def merge_candidates(batches: Iterable[Sequence[Candidate]]) -> list[Candidate]:
    """Dedupe across sources and spellings ("ad lib" = "ad-lib"), keeping the hardest baseline."""
    best: dict[str, Candidate] = {}
    for batch in batches:
        for candidate in batch:
            key = re.sub(r"[^a-z0-9]+", "_", candidate.headword.lower()).strip("_")
            current = best.get(key)
            if current is None or candidate.base_difficulty > current.base_difficulty:
                best[key] = candidate
    return list(best.values())
