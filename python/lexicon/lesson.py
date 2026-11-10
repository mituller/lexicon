"""Teaching card for a missed word: pronunciation, word parts, and a memory hook.

Written once per word by the enrichment model (the one whose etymologies proved reliable) and
stored on the term, so every later learner who misses the word gets it instantly.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from .bedrock import BedrockClient
from .config import get_settings
from .enrichment import extract_json
from .logging_setup import json_event
from .models import TermRecord
from .termindex import TermIndex

logger = logging.getLogger("lexicon")

LESSON_SYSTEM = (
    "You help fluent adult English speakers remember advanced words. Reply with a single JSON "
    "object and nothing else."
)


class Lesson(BaseModel):
    term: str
    pos: str = ""
    definition: str
    ipa: str = ""
    parts: str = ""
    hook: str = ""
    examples: list[str] = Field(default_factory=list)
    etymology: str = ""


def lesson_prompt(term: TermRecord) -> str:
    return (
        f'Word: "{term.term}" ({term.pos or "unknown part of speech"}) — {term.definition}\n'
        + (f"Origin: {term.etymology}\n" if term.etymology else "")
        + "\nReturn JSON with exactly these keys:\n"
        '  "ipa": General American IPA between slashes, e.g. "/ləˈkɑnɪk/",\n'
        '  "parts": the meaningful parts and what each contributes, e.g. '
        '"circum- (around) + locut- (speak) + -ion"; "" if the word has no useful parts or you '
        "are not certain,\n"
        '  "hook": one or two sentences that tie the word to a common English word sharing its '
        "root, or to a vivid image or story, so it is easy to recall. Concrete, not generic "
        "advice; never invent a false etymology.\n"
    )


class LessonService:
    def __init__(self, *, terms: TermIndex | None = None, bedrock: BedrockClient | None = None) -> None:
        self._terms = terms or TermIndex()
        self._bedrock = bedrock or BedrockClient()

    def lesson(self, term_id: str) -> Lesson | None:
        term = self._terms.get_terms([term_id]).get(term_id)
        if term is None:
            return None
        if not term.lesson_ready:
            term = self._write(term)
        return Lesson(
            term=term.term, pos=term.pos, definition=term.definition, ipa=term.ipa,
            parts=term.parts, hook=term.hook, examples=term.examples, etymology=term.etymology,
        )

    def _write(self, term: TermRecord) -> TermRecord:
        try:
            reply = self._bedrock.generate(
                lesson_prompt(term),
                system=LESSON_SYSTEM,
                model_id=get_settings().enrich_model_id or None,
                max_tokens=300,
            )
            payload = extract_json(reply)
        except Exception as error:  # noqa: BLE001 - the card still shows definition + examples
            logger.warning(json_event("lesson_failed", term=term.term, error=str(error)))
            return term
        updated = term.model_copy(
            update={
                "ipa": str(payload.get("ipa", "")).strip(),
                "parts": str(payload.get("parts", "")).strip(),
                "hook": str(payload.get("hook", "")).strip(),
                "lesson_ready": True,
            }
        )
        self._terms.put_term(updated)
        return updated
