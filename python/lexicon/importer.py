"""Import words from the learner's own reading: pasted text or a web page.

The exercise model picks the words in the text a well-read adult might not know (dictionary
form, with the sentence each appeared in). Words below the learner's level are dropped; the rest
are published like any other term and queued so the learner's next sessions start with them.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field

from .bedrock import BedrockClient
from .config import get_settings
from .dynamo import ProgressStore
from .enrichment import extract_json
from .logging_setup import json_event
from .models import QueuedWord
from .refill import VocabularyPool

logger = logging.getLogger("lexicon")

MAX_WORDS = 10
MAX_TEXT_CHARS = 15_000
MAX_DOWNLOAD_BYTES = 1_000_000
# Words more than this far below the learner's level are too easy to be worth a slot.
EASY_MARGIN = 10
USER_AGENT = "lexicon-vocabulary-tutor/0.1 (educational app)"

EXTRACT_SYSTEM = "You pick vocabulary for an advanced English course. Reply with JSON only."


class ImportRequest(BaseModel):
    text: str = Field(default="", max_length=50_000)
    url: str = Field(default="", max_length=2_000)


class ImportedWord(BaseModel):
    term_id: str
    term: str
    definition: str
    difficulty: int
    context: str = ""


class ImportResult(BaseModel):
    source: str
    added: list[ImportedWord] = Field(default_factory=list)
    already_studying: list[str] = Field(default_factory=list)
    too_easy: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)


class ImportFailed(ValueError):
    """Bad input (unreachable URL, nothing to read); shown to the learner."""


# --- reading the source ------------------------------------------------------

_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def html_to_text(page: str) -> tuple[str, str]:
    """``(title, text)`` of an HTML page."""
    title_match = _TITLE_RE.search(page)
    title = " ".join(html.unescape(title_match.group(1)).split()) if title_match else ""
    body = _TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", page))
    return title, " ".join(html.unescape(body).split())


def _check_public(url: str) -> None:
    """Only public http(s) hosts: the API must not be usable to probe internal addresses."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImportFailed("Use an http(s) link.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as error:
        raise ImportFailed(f"Couldn't find {parsed.hostname}.") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ImportFailed("That address isn't a public web page.")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(url: str, *, timeout: float = 8.0) -> tuple[str, str]:
    _check_public(url)
    opener = urllib.request.build_opener(_CheckedRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(MAX_DOWNLOAD_BYTES)
    except ImportFailed:
        raise
    except Exception as error:  # noqa: BLE001 - any fetch failure is the same to the learner
        raise ImportFailed("Couldn't load that page.") from error
    charset = re.search(r"charset=([\w-]+)", content_type)
    page = raw.decode(charset.group(1) if charset else "utf-8", errors="replace")
    if "html" in content_type or "<html" in page[:2000].lower():
        title, text = html_to_text(page)
        return title or url, text
    return url, page


# --- picking the words -------------------------------------------------------


def extract_prompt(text: str, level: float) -> str:
    return (
        "Below is a text the learner has been reading. The learner is a fluent adult whose "
        f"vocabulary level is {level:.0f} on a 0-100 scale (50 = words like 'ubiquitous', "
        "70 = 'perspicacious', 85 = 'tergiversate').\n\n"
        f"Pick up to {MAX_WORDS} words or set phrases from the text that are at or above that "
        "level and worth learning. Skip proper nouns, technical jargon only specialists use, "
        "and words a well-read adult certainly knows.\n"
        "Give each word in its dictionary (lemma) form, never as it is inflected in the text: "
        '"obfuscating" -> "obfuscate", "exasperated" -> "exasperate", "palimpsests" -> '
        '"palimpsest".\n'
        'Return JSON: {"words": [{"word": the dictionary form, "sentence": the sentence from the '
        "text it appears in, copied exactly}]}\n\n"
        f"TEXT:\n{text[:MAX_TEXT_CHARS]}"
    )


def parse_extracted(raw: str) -> list[tuple[str, str]]:
    payload = extract_json(raw)
    out: list[tuple[str, str]] = []
    for entry in payload.get("words") or []:
        if not isinstance(entry, dict):
            continue
        word = " ".join(str(entry.get("word", "")).split())
        if word and len(word) <= 40 and re.fullmatch(r"[A-Za-z][A-Za-z' -]*", word):
            out.append((word, " ".join(str(entry.get("sentence", "")).split())[:400]))
    return out[:MAX_WORDS]


class WordImporter:
    def __init__(
        self,
        *,
        progress: ProgressStore | None = None,
        pool: VocabularyPool | None = None,
        bedrock: BedrockClient | None = None,
    ) -> None:
        self._progress = progress or ProgressStore()
        self._bedrock = bedrock or BedrockClient()
        self._pool = pool or VocabularyPool(bedrock=self._bedrock)

    def run(self, user_id: str, request: ImportRequest, *, level: float) -> ImportResult:
        if request.url.strip():
            source, text = fetch_page(request.url.strip())
        else:
            source, text = "pasted text", request.text
        if len(text.split()) < 5:
            raise ImportFailed("There isn't enough text to pick words from.")

        picked = parse_extracted(
            self._bedrock.generate(
                extract_prompt(text, level),
                system=EXTRACT_SYSTEM,
                # Sonnet: Nova Pro returned inflected forms ("obfuscating") despite the instruction.
                model_id=get_settings().enrich_model_id or None,
                max_tokens=1200,
                temperature=0.0,
            )
        )
        result = ImportResult(source=source[:200])
        if not picked:
            return result
        contexts = {word.lower(): sentence for word, sentence in picked}
        terms, result.rejected = self._pool.publish_headwords(
            [word for word, _ in picked], source=f"import:{source[:80]}"
        )

        seen = {r.term_id for r in self._progress.list_progress(user_id)}
        queued: list[QueuedWord] = []
        for term in terms:
            if term.term_id in seen:
                result.already_studying.append(term.term)
            elif term.difficulty < level - EASY_MARGIN:
                result.too_easy.append(term.term)
            else:
                context = contexts.get(term.term.lower(), "")
                queued.append(QueuedWord(term_id=term.term_id, context=context, source=result.source))
                result.added.append(
                    ImportedWord(
                        term_id=term.term_id, term=term.term, definition=term.definition,
                        difficulty=term.difficulty, context=context,
                    )
                )
        self._progress.queue_words(user_id, queued)
        logger.info(
            json_event("import", picked=len(picked), added=len(queued), rejected=len(result.rejected))
        )
        return result

