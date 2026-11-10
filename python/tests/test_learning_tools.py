"""Importer, quotations, lesson and progress overview (no AWS or network)."""

from datetime import timedelta
from typing import Any

import pytest

from lexicon import importer
from lexicon.importer import (
    ImportFailed,
    ImportRequest,
    WordImporter,
    html_to_text,
    parse_extracted,
)
from lexicon.lesson import LessonService
from lexicon.models import (
    CardState,
    ProgressRecord,
    QueuedWord,
    Rating,
    TermRecord,
    UserAggregate,
    utcnow,
)
from lexicon.overview import build_overview, due_forecast, struggling_ids
from lexicon.quotations import parse_definitions


def term(term_id: str, word: str, difficulty: int, **extra: Any) -> TermRecord:
    return TermRecord(term_id=term_id, term=word, definition=f"definition of {word}", difficulty=difficulty, **extra)


# --- importer ---


def test_html_to_text_drops_scripts_and_keeps_title() -> None:
    title, text = html_to_text(
        "<html><head><title>On &amp; Off</title><script>var x=1</script></head>"
        "<body><p>A <b>laconic</b> reply.</p><style>p{}</style></body></html>"
    )
    assert title == "On & Off"
    assert text == "On & Off A laconic reply."


def test_parse_extracted_keeps_plausible_words_only() -> None:
    raw = (
        '{"words": [{"word": "obfuscate", "sentence": "They  obfuscated it."},'
        ' {"word": "<b>x</b>", "sentence": ""}, {"word": "ad hoc", "sentence": "An ad hoc fix."}, "junk"]}'
    )
    assert parse_extracted(raw) == [("obfuscate", "They obfuscated it."), ("ad hoc", "An ad hoc fix.")]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/", "http://169.254.169.254/latest", "http://10.0.0.1/"])
def test_fetch_page_refuses_non_public_addresses(url: str) -> None:
    with pytest.raises(ImportFailed):
        importer.fetch_page(url)


class FakeProgress:
    def __init__(self, records: list[ProgressRecord]) -> None:
        self.records, self.queued = records, []

    def list_progress(self, user_id: str) -> list[ProgressRecord]:
        return self.records

    def queue_words(self, user_id: str, entries: list[QueuedWord]) -> None:
        self.queued.extend(entries)


class FakePool:
    def publish_headwords(self, headwords, *, source: str):
        published = {"obfuscate": term("g_obfuscate", "obfuscate", 60), "tired": term("g_tired", "tired", 5),
                     "laconic": term("g_laconic", "laconic", 55)}
        return [published[h] for h in headwords if h in published], [h for h in headwords if h not in published]


class FakeBedrock:
    def generate(self, prompt: str, **_: Any) -> str:
        return (
            '{"words": [{"word": "obfuscate", "sentence": "They obfuscated it."},'
            ' {"word": "tired", "sentence": "I was tired."}, {"word": "laconic", "sentence": "A laconic man."},'
            ' {"word": "zzyzx", "sentence": "?"}]}'
        )


def test_import_queues_new_words_at_or_above_level() -> None:
    progress = FakeProgress([ProgressRecord(user_id="u", term_id="g_laconic")])
    run = WordImporter(progress=progress, pool=FakePool(), bedrock=FakeBedrock())  # type: ignore[arg-type]
    result = run.run("u", ImportRequest(text="They obfuscated it. I was tired. A laconic man."), level=50)
    assert [w.term for w in result.added] == ["obfuscate"]
    assert result.added[0].context == "They obfuscated it."
    assert result.too_easy == ["tired"] and result.already_studying == ["laconic"]
    assert result.rejected == ["zzyzx"]
    assert [q.term_id for q in progress.queued] == ["g_obfuscate"]


def test_import_needs_some_text() -> None:
    run = WordImporter(progress=FakeProgress([]), pool=FakePool(), bedrock=FakeBedrock())  # type: ignore[arg-type]
    with pytest.raises(ImportFailed):
        run.run("u", ImportRequest(text="too short"), level=50)


# --- quotations ---


def test_parse_definitions_prefers_the_terms_part_of_speech_and_full_sentences() -> None:
    payload = {
        "en": [
            {"partOfSpeech": "Noun", "definitions": [{"examples": ["A noun use of the word laconic in a long sentence here."]}]},
            {"partOfSpeech": "Adjective", "definitions": [
                {"examples": ["a <i>laconic</i> reply", "He was <b>laconic</b> to the point of rudeness, answering in single words."]}
            ]},
        ]
    }
    assert parse_definitions(payload, pos="adjective") == [
        "He was laconic to the point of rudeness, answering in single words.",
        "A noun use of the word laconic in a long sentence here.",
    ]


# --- lesson ---


class FakeTerms:
    def __init__(self, terms: list[TermRecord]) -> None:
        self.terms = {t.term_id: t for t in terms}
        self.writes = 0

    def get_terms(self, ids: list[str]) -> dict[str, TermRecord]:
        return {i: self.terms[i] for i in ids if i in self.terms}

    def put_term(self, t: TermRecord) -> None:
        self.writes += 1
        self.terms[t.term_id] = t


class LessonBedrock:
    def __init__(self, reply: str) -> None:
        self.reply, self.calls = reply, 0

    def generate(self, prompt: str, **_: Any) -> str:
        self.calls += 1
        return self.reply


def test_lesson_is_written_once_and_stored_on_the_term() -> None:
    terms = FakeTerms([term("t", "circumlocution", 70, examples=["e1"])])
    bedrock = LessonBedrock('{"ipa": "/ˌsɝkəmloʊˈkjuʃən/", "parts": "circum- (around) + locut- (speak)", "hook": "Like a circus of words."}')
    service = LessonService(terms=terms, bedrock=bedrock)  # type: ignore[arg-type]
    first = service.lesson("t")
    assert first is not None and first.parts.startswith("circum-") and first.examples == ["e1"]
    service.lesson("t")
    assert bedrock.calls == 1 and terms.writes == 1
    assert service.lesson("missing") is None


def test_lesson_failure_still_returns_the_basics() -> None:
    terms = FakeTerms([term("t", "laconic", 50)])
    lesson = LessonService(terms=terms, bedrock=LessonBedrock("nope")).lesson("t")  # type: ignore[arg-type]
    assert lesson is not None and lesson.definition == "definition of laconic" and lesson.hook == ""
    assert terms.writes == 0  # retried next time


# --- overview ---


def test_due_forecast_folds_overdue_into_today() -> None:
    now = utcnow()
    records = [ProgressRecord(user_id="u", term_id=str(i), due=now + timedelta(days=d)) for i, d in enumerate([-3, 0, 1, 1, 9])]
    assert [d.count for d in due_forecast(records, now=now)] == [2, 2, 0, 0, 0, 0, 0]


def test_overview_counts_and_struggling_words_worst_first() -> None:
    now = utcnow()
    records = [
        ProgressRecord(user_id="u", term_id="a", state=CardState.REVIEW, stability=40, due=now),
        ProgressRecord(user_id="u", term_id="b", lapses=2, due=now),
        ProgressRecord(user_id="u", term_id="c", lapses=3, due=now),
        ProgressRecord(user_id="u", term_id="d", lapses=1, last_rating=Rating.AGAIN, due=now),
        ProgressRecord(user_id="u", term_id="e", lapses=1, last_rating=Rating.GOOD, due=now),
    ]
    assert struggling_ids(records) == ["c", "b", "d"]
    terms = {i: term(i, f"word{i}", 50) for i in "abcd"}
    aggregate = UserAggregate(user_id="u", level=55)
    aggregate.record_level(today="2026-11-09")
    view = build_overview(aggregate, records, terms, queued=2, now=now)
    assert (view.known, view.learning, view.queued) == (1, 4, 2)
    assert [w.term for w in view.struggling] == ["wordc", "wordb", "wordd"]
    assert view.level_history[0].level == 55
