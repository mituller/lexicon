import json
from typing import Any

import pytest

from lexicon.enrichment import parse_enrichment
from lexicon.models import TermRecord
from lexicon.refill import VocabularyPool, build_term
from lexicon.sources import Candidate
from lexicon.termindex import term_id_for


def reply(**overrides: Any) -> str:
    payload = {
        "skip": False,
        "skip_reason": "",
        "pos": "verb",
        "definition": "to make something unclear on purpose",
        "examples": ["He obfuscated the facts.", "Jargon obfuscates.", "Don't obfuscate."],
        "synonyms": ["obscure", "cloud", "obfuscate"],
        "confused_with": ["obviate"],
        "etymology": "From Latin obfuscare, to darken.",
        "subfield": "Rhetoric & Writing",
        "difficulty": 80,
    }
    payload.update(overrides)
    return "Sure!\n```json\n" + json.dumps(payload) + "\n```"


def test_parse_enrichment_normalises_fields() -> None:
    result = parse_enrichment(reply(), "obfuscate")
    assert result.synonyms == ["obscure", "cloud"]  # the headword itself is dropped
    assert result.subfield == "rhetoric_writing"
    assert result.difficulty == 80
    assert len(result.examples) == 3
    assert not result.skip


def test_parse_enrichment_skips_empty_definition() -> None:
    result = parse_enrichment(reply(definition=""), "x")
    assert result.skip and result.skip_reason


def test_parse_enrichment_rejects_non_json() -> None:
    with pytest.raises(ValueError):
        parse_enrichment("I can't help with that.", "x")


def test_build_term_blends_model_difficulty_and_derives_cefr() -> None:
    item = {"headword": "obfuscate", "difficulty": 60, "source": "nawl", "cefr_level": ""}
    term = build_term(item, parse_enrichment(reply(difficulty=100), "obfuscate"), domain="general")
    assert term.term_id == "general_obfuscate"
    assert 60 < term.difficulty < 100
    assert term.cefr_level == "C2"
    assert term.to_vocabulary_term().meta.difficulty == term.difficulty
    assert term.to_vocabulary_term().payload.example == "He obfuscated the facts."


# --- pipeline with in-memory fakes ---


class FakeIndex:
    def __init__(self) -> None:
        self.candidates: list[dict[str, Any]] = []
        self.terms: dict[str, TermRecord] = {}
        self.skipped: dict[str, str] = {}

    def add_candidate(self, headword: str, difficulty: int) -> None:
        self.candidates.append(
            {"PK": "CANDIDATE#general", "SK": f"D#{difficulty:03d}#{headword}", "headword": headword,
             "difficulty": difficulty, "source": "test", "pos": "", "cefr_level": ""}
        )

    def put_candidates(self, pairs: list[tuple[Candidate, int]], domain: str = "general") -> int:
        for candidate, difficulty in pairs:
            self.add_candidate(candidate.headword, difficulty)
        return len(pairs)

    def all_candidate_headwords(self, domain: str = "general") -> set[str]:
        return {c["headword"].lower() for c in self.candidates}

    def candidates_in_band(self, lo: int, hi: int, *, domain: str = "general", limit: int | None = None):
        found = sorted(
            (c for c in self.candidates if lo <= c["difficulty"] < hi), key=lambda c: c["SK"]
        )
        return found[:limit] if limit is not None else found

    def remove_candidate(self, item: dict[str, Any]) -> None:
        self.candidates.remove(item)

    def put_term(self, term: TermRecord) -> None:
        self.terms[term.term_id] = term

    def mark_skipped(self, term_id: str, headword: str, reason: str) -> None:
        self.skipped[term_id] = reason

    def mark_skipped_many(self, entries: list[tuple[str, str, str]]) -> None:
        for term_id, _, reason in entries:
            self.skipped[term_id] = reason

    def terms_in_band(self, lo: int, hi: int, *, domain: str = "general", limit: int | None = None):
        return [t for t in self.terms.values() if lo <= t.difficulty < hi]

    def known_ids(self, ids: list[str]) -> set[str]:
        return {i for i in ids if i in self.terms or i in self.skipped}


class FakeStore:
    def __init__(self) -> None:
        self.written: list[str] = []

    def put_terms(self, terms, embeddings) -> int:
        assert len(terms) == len(embeddings)
        self.written.extend(t.payload.term_id for t in terms)
        return len(terms)


class FakeBedrock:
    def __init__(self, replies: dict[str, str | Exception]) -> None:
        self.replies = replies

    def generate(self, prompt: str, *, system: str | None = None, **_: Any) -> str:
        for word, answer in self.replies.items():
            if f'English word "{word}"' in prompt:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"unexpected prompt: {prompt[:80]}")

    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]


class FakeFrequency:
    def __init__(self, table: dict[str, float]) -> None:
        self.table = table

    def per_million(self, word: str) -> float | None:
        return self.table.get(word.lower())


class FakeSource:
    name = "fake"
    attribution = "test"

    def __init__(self, words: list[str], base: int = 60) -> None:
        self.words, self.base = words, base

    def fetch(self) -> list[Candidate]:
        return [Candidate(w, self.name, self.base) for w in self.words]


def make_pool(index: FakeIndex, bedrock: FakeBedrock | None = None, freq: dict | None = None):
    store = FakeStore()
    pool = VocabularyPool(
        index=index,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        bedrock=bedrock or FakeBedrock({}),  # type: ignore[arg-type]
        frequency=FakeFrequency(freq or {}),
    )
    return pool, store


def test_sync_sources_scores_and_skips_known_words() -> None:
    index = FakeIndex()
    index.add_candidate("pellucid", 70)
    index.skipped[term_id_for("brexit")] = "proper noun"
    pool, _ = make_pool(index, freq={"house": 236.0, "laconic": 0.4})

    report = pool.sync_sources([FakeSource(["house", "laconic", "pellucid", "Brexit"])])

    assert report.fetched == 4
    assert report.already_known == 2
    assert report.added == 2
    scores = {c["headword"]: c["difficulty"] for c in index.candidates}
    assert scores["house"] < scores["laconic"]


def test_refill_publishes_skips_and_retries_failures() -> None:
    index = FakeIndex()
    for word, difficulty in [("laconic", 62), ("brexit", 64), ("pellucid", 66), ("house", 20)]:
        index.add_candidate(word, difficulty)
    bedrock = FakeBedrock(
        {
            "laconic": reply(definition="using very few words"),
            "brexit": reply(skip=True, skip_reason="proper noun"),
            "pellucid": RuntimeError("throttled"),
        }
    )
    pool, store = make_pool(index, bedrock)

    report = pool.refill(60, 70, count=5, allow_suggest=False)

    assert report.requested == 3  # "house" is outside the band
    assert [t.term for t in report.added] == ["laconic"]
    assert store.written == ["general_laconic"]
    assert "general_laconic" in index.terms
    assert report.skipped == ["brexit"] and "general_brexit" in index.skipped
    assert report.failed == ["pellucid"]
    remaining = {c["headword"] for c in index.candidates}
    assert remaining == {"pellucid", "house"}  # failures stay pooled for a retry


def test_ensure_band_only_refills_when_unseen_terms_run_low() -> None:
    index = FakeIndex()
    index.add_candidate("laconic", 62)
    index.add_candidate("pellucid", 64)
    # No model estimate, so published terms keep their pooled difficulty and stay in the band.
    bedrock = FakeBedrock({"laconic": reply(difficulty=None), "pellucid": reply(difficulty=None)})
    pool, _ = make_pool(index, bedrock)

    assert pool.ensure_band(60, 70, min_available=1).added  # empty band -> refill
    before = len(index.terms)
    assert not pool.ensure_band(60, 70, min_available=1).added  # enough now
    assert len(index.terms) == before

    seen = set(index.terms)
    assert pool.ensure_band(60, 70, exclude_ids=seen, min_available=1).added


# --- Bedrock suggestions ---


def suggestions(*words: str) -> str:
    return json.dumps({"words": [{"word": w, "pos": "adjective"} for w in words]})


class SuggestingBedrock(FakeBedrock):
    """Answers the suggestion prompt, then enrichment prompts per word."""

    def __init__(self, suggested: str, replies: dict[str, str | Exception]) -> None:
        super().__init__(replies)
        self.suggested = suggested
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, system: str | None = None, **_: Any) -> str:
        self.prompts.append(prompt)
        if prompt.startswith("Suggest "):
            return self.suggested
        return super().generate(prompt, system=system)


def test_suggestion_source_drops_unverifiable_and_blocked_words() -> None:
    from lexicon.suggest import BedrockSuggestionSource

    bedrock = SuggestingBedrock(
        suggestions("lugubrious", "flimbertastic", "laconic", "ox", "Lugubrious", "obstreperous"), {}
    )
    source = BedrockSuggestionSource(
        seeds=["laconic"],
        difficulty=75,
        count=6,
        frequency=FakeFrequency({"lugubrious": 0.21, "obstreperous": 0.0001, "laconic": 0.4}),
        bedrock=bedrock,  # type: ignore[arg-type]
    )

    found = source.fetch()

    assert [c.headword for c in found] == ["lugubrious"]
    assert found[0].base_difficulty == 75 and found[0].source == "bedrock-suggest"
    # invented, seed, too short, duplicate, too rare to verify
    assert source.rejected == ["flimbertastic", "laconic", "ox", "Lugubrious", "obstreperous"]
    assert "related themes: laconic\n" in bedrock.prompts[0]


def test_suggestion_source_tolerates_garbage_reply() -> None:
    from lexicon.suggest import BedrockSuggestionSource

    source = BedrockSuggestionSource(
        seeds=[], difficulty=80, count=5, frequency=FakeFrequency({}),
        bedrock=SuggestingBedrock("no json here", {}),  # type: ignore[arg-type]
    )
    assert source.fetch() == []


def test_refill_suggests_when_band_is_empty_and_seeds_from_nearby_terms() -> None:
    index = FakeIndex()
    index.terms["general_laconic"] = build_term(
        {"headword": "laconic", "difficulty": 74}, parse_enrichment(reply(difficulty=None), "laconic"),
        domain="general",
    )
    bedrock = SuggestingBedrock(
        suggestions("lugubrious", "pellucid"),
        {"lugubrious": reply(difficulty=80), "pellucid": reply(difficulty=80)},
    )
    pool, store = make_pool(index, bedrock, freq={"lugubrious": 0.3, "pellucid": 0.3})

    report = pool.refill(70, 90, count=2)

    assert report.suggested == 2
    assert sorted(t.term for t in report.added) == ["lugubrious", "pellucid"]
    assert sorted(store.written) == ["general_lugubrious", "general_pellucid"]
    assert "related themes: laconic\n" in bedrock.prompts[0]  # the nearby published term calibrates the prompt


def test_refill_survives_suggestion_failure() -> None:
    index = FakeIndex()
    index.add_candidate("laconic", 62)

    class Failing(FakeBedrock):
        def generate(self, prompt: str, *, system: str | None = None, **_: Any) -> str:
            if prompt.startswith("Suggest "):
                raise RuntimeError("throttled")
            return super().generate(prompt, system=system)

    pool, _ = make_pool(index, Failing({"laconic": reply()}))
    report = pool.refill(60, 70, count=3)
    assert [t.term for t in report.added] == ["laconic"]
    assert report.suggested == 0


def test_refill_skips_derived_forms_without_calling_bedrock() -> None:
    index = FakeIndex()
    index.add_candidate("bereftly", 77)
    index.add_candidate("laconic", 75)
    pool, _ = make_pool(
        index, FakeBedrock({"laconic": reply()}), freq={"bereftly": 0.001, "bereft": 1.2}
    )

    report = pool.refill(70, 80, count=2, allow_suggest=False)

    assert report.skipped == ["bereftly"]
    assert index.skipped["general_bereftly"] == "derived from bereft"
    assert [t.term for t in report.added] == ["laconic"]


def test_enrich_retries_once_on_malformed_json() -> None:
    from lexicon.enrichment import enrich

    class Flaky:
        calls = 0

        def generate(self, prompt: str, **_: Any) -> str:
            Flaky.calls += 1
            return '{"definition": "broken' if Flaky.calls == 1 else reply()

    assert enrich("obfuscate", bedrock=Flaky()).definition  # type: ignore[arg-type]
    assert Flaky.calls == 2


def test_confused_with_drops_word_family_and_annotations() -> None:
    result = parse_enrichment(
        reply(confused_with=["calamity", "spatter", "commode (toilet)"]), "calamitous"
    )
    assert result.confused_with == ["spatter"]


# --- batch rating ---


def test_parse_ratings_tolerates_bad_entries() -> None:
    from lexicon.rating import parse_ratings

    raw = json.dumps({"words": [
        {"word": "Feckless", "difficulty": 42},
        {"word": "hauntingly", "difficulty": 30, "skip": True, "reason": "derived"},
        {"word": "odd", "difficulty": "n/a"},
        {"difficulty": 10},
        "junk",
    ]})
    ratings = parse_ratings(raw)
    assert ratings["feckless"].difficulty == 42 and not ratings["feckless"].skip
    assert ratings["hauntingly"].skip and ratings["hauntingly"].reason == "derived"
    assert ratings["odd"].difficulty is None
    assert len(ratings) == 3


def test_sync_sources_uses_model_ratings_and_rejects_flagged_words() -> None:
    from lexicon.rating import BatchRater

    class RatingBedrock(FakeBedrock):
        def generate(self, prompt: str, *, system: str | None = None, **_: Any) -> str:
            assert prompt.startswith("Rate each word")
            return json.dumps({"words": [
                {"word": "daunt", "difficulty": 30},
                {"word": "hauntingly", "difficulty": 35, "skip": True, "reason": "derived"},
            ]})

    index = FakeIndex()
    bedrock = RatingBedrock({})
    pool = VocabularyPool(
        index=index,  # type: ignore[arg-type]
        store=FakeStore(),  # type: ignore[arg-type]
        bedrock=bedrock,  # type: ignore[arg-type]
        frequency=FakeFrequency({"daunt": 0.01, "pellucid": 0.01}),
        rater=BatchRater(bedrock),  # type: ignore[arg-type]
    )

    report = pool.sync_sources([FakeSource(["daunt", "hauntingly", "pellucid"], base=70)])

    scores = {c["headword"]: c["difficulty"] for c in index.candidates}
    # frequency alone says ~93 for both; the model rating pulls "daunt" down to its real level
    assert scores["daunt"] < 50 < scores["pellucid"]
    assert index.skipped == {"general_hauntingly": "derived"}
    assert (report.rated, report.rejected, report.added) == (1, 1, 2)


def test_sync_sources_falls_back_to_frequency_when_rating_fails() -> None:
    index = FakeIndex()
    pool, _ = make_pool(index, freq={"daunt": 0.01})  # FakeBedrock rejects the rating prompt
    report = pool.sync_sources([FakeSource(["daunt"], base=70)])
    assert report.added == 1 and report.rated == 0
