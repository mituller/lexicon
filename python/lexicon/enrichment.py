"""Bedrock enrichment: a bare headword -> a full learning entry for a fluent adult reader.

The model writes the prose (definition, several examples, confusables, etymology) and gives its
own difficulty estimate. It may also *reject* a headword (proper noun, offensive, not really an
English word), so junk from source lists never reaches learners.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .bedrock import BedrockClient
from .config import get_settings
from .difficulty import DIFFICULTY_SCALE

SYSTEM_PROMPT = (
    "You are a lexicographer writing entries for an advanced vocabulary course aimed at fluent, "
    "well-read adult native English speakers. Always reply with a single JSON object and "
    "nothing else."
)


@dataclass
class Enrichment:
    """What Bedrock generated for one headword."""

    definition: str
    examples: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    confused_with: list[str] = field(default_factory=list)
    etymology: str = ""
    pos: str = ""
    subfield: str = ""
    difficulty: int | None = None
    skip: bool = False
    skip_reason: str = ""


def enrichment_prompt(headword: str, *, pos: str = "") -> str:
    pos_hint = f' (part of speech in the source list: "{pos}")' if pos else ""
    return (
        f'Write an entry for the English word "{headword}"{pos_hint}.\n\n'
        "Return JSON with exactly these keys:\n"
        '  "skip": true if this is a proper noun, an abbreviation, offensive, not a real '
        "English word worth teaching, slang or a nonstandard spelling (e.g. \"flatout\"), or a "
        "plain inflection or transparent derivation of a more common word (e.g. "
        '"contractually", "bereftly", "choreographical"); otherwise false,\n'
        '  "skip_reason": short reason when skip is true, else "",\n'
        '  "pos": the part of speech of the sense you define,\n'
        '  "definition": one precise sentence for the most useful sense, without using the word '
        "itself,\n"
        '  "examples": array of 3 natural sentences from different contexts that each use the '
        "word and make its meaning inferable,\n"
        '  "synonyms": array of up to 4 near-synonyms,\n'
        '  "confused_with": array of up to 3 different words that look or sound similar and '
        'are genuinely mixed up with it (e.g. "flout" for "flaunt"); never synonyms; usually '
        "empty,\n"
        '  "etymology": one short sentence on its origin only if you are certain of it, '
        'otherwise "",\n'
        '  "subfield": a one or two word topic label such as "law", "emotion", "science", '
        '"rhetoric", "everyday",\n'
        '  "difficulty": integer 0-100 on the scale below.\n'
        "Do not add any other keys or text.\n\n"
        f"{DIFFICULTY_SCALE}"
    )


def extract_json(raw: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply, tolerating code fences and prose."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model reply: {raw[:200]!r}")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model reply is not a JSON object")
    return payload


def _strings(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def same_family(word: str, headword: str) -> bool:
    """"calamity" for "calamitous" is a relative, not a confusable; "spatter" for "splatter" is."""
    stem = min(len(headword), 5)
    return word.lower()[:stem] == headword.lower()[:stem]


def _unique(values: list[str], *, exclude: set[str]) -> list[str]:
    """Case-insensitive dedupe that also drops anything in ``exclude``."""
    seen = {value.lower() for value in exclude}
    out: list[str] = []
    for value in values:
        if value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    return out


def parse_enrichment(raw: str, headword: str) -> Enrichment:
    payload = extract_json(raw)
    difficulty = payload.get("difficulty")
    try:
        model_difficulty = max(0, min(100, int(difficulty))) if difficulty is not None else None
    except (TypeError, ValueError):
        model_difficulty = None
    synonyms = _unique(_strings(payload.get("synonyms"), 4), exclude={headword})
    enrichment = Enrichment(
        definition=str(payload.get("definition", "")).strip(),
        examples=_strings(payload.get("examples"), 3),
        synonyms=synonyms,
        confused_with=[
            word
            for word in _unique(_strings(payload.get("confused_with"), 3), exclude={headword, *synonyms})
            if not same_family(word, headword) and "(" not in word
        ],
        etymology=str(payload.get("etymology", "")).strip(),
        pos=str(payload.get("pos", "")).strip().lower(),
        subfield=re.sub(r"[^a-z0-9]+", "_", str(payload.get("subfield", "")).lower()).strip("_"),
        difficulty=model_difficulty,
        skip=bool(payload.get("skip")),
        skip_reason=str(payload.get("skip_reason", "")).strip(),
    )
    if not enrichment.skip and not enrichment.definition:
        enrichment.skip, enrichment.skip_reason = True, "no definition generated"
    return enrichment


def enrich(headword: str, *, pos: str = "", bedrock: BedrockClient | None = None) -> Enrichment:
    """One Bedrock call for one headword."""
    client = bedrock or BedrockClient()
    for attempt in range(2):
        reply = client.generate(
            enrichment_prompt(headword, pos=pos),
            system=SYSTEM_PROMPT,
            model_id=get_settings().enrich_model_id or None,
        )
        try:
            return parse_enrichment(reply, headword)
        except ValueError:  # json.JSONDecodeError included; the model occasionally emits bad JSON
            if attempt:
                raise
    raise AssertionError("unreachable")
