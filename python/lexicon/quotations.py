"""Real usage sentences from Wiktionary, for fill-in-the-blank exercises.

Generated sentences tend to be bland and over-explain the word; quotations show how it is
actually used. Fetched once per term (``TermRecord.quotations_checked``) and cached on the term.
Wiktionary text is CC BY-SA, so exercises that use it say where it came from.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

from .logging_setup import json_event

logger = logging.getLogger("lexicon")

WIKTIONARY_URL = "https://en.wiktionary.org/api/rest_v1/page/definition/"
# Wikimedia asks automated clients for a descriptive User-Agent.
USER_AGENT = "lexicon-vocabulary-tutor/0.1 (educational app)"
MIN_WORDS, MAX_WORDS = 8, 45
MAX_QUOTATIONS = 6
ATTRIBUTION = "Real usage, via Wiktionary"

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub("", text)).split())


def parse_definitions(payload: dict[str, Any], *, pos: str = "") -> list[str]:
    """Usage sentences from the REST ``definition`` payload; the term's own part of speech first."""
    sections = payload.get("en") or []
    sections = sorted(sections, key=lambda s: str(s.get("partOfSpeech", "")).lower() != pos.lower())
    out: list[str] = []
    for section in sections:
        for definition in section.get("definitions") or []:
            for example in definition.get("examples") or []:
                sentence = strip_html(str(example))
                if MIN_WORDS <= len(sentence.split()) <= MAX_WORDS and sentence not in out:
                    out.append(sentence)
    return out[:MAX_QUOTATIONS]


def fetch_quotations(word: str, *, pos: str = "", timeout: float = 4.0) -> list[str]:
    url = WIKTIONARY_URL + urllib.parse.quote(word.replace(" ", "_"))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed host
            payload = json.loads(response.read())
    except Exception as error:  # noqa: BLE001 - 404 for unknown words, network errors: no quotations
        logger.info(json_event("quotations_unavailable", term=word, error=str(error)))
        return []
    return parse_definitions(payload, pos=pos) if isinstance(payload, dict) else []
