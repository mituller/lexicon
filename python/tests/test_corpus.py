from lexicon.corpus import (
    CefrEntry,
    DomainDetails,
    TermDetails,
    build_corpus,
    build_domain_corpus,
    build_record,
    curate,
    dedupe,
    make_term_id,
    parse_cefr_csv,
    parse_details,
    parse_domain_details,
    parse_term_list,
)
from lexicon.ingestion import raw_to_term

SAMPLE_CSV = """headword,pos,CEFR,CoreInventory 1,CoreInventory 2,Threshold
a,determiner,A1,,,
abandon,verb,B1,,,
abandoned,adjective,B2,Personal identification,,
ability,noun,A2,,,
able,adjective,B1,,,
longevity,noun,C2,,,
withstand,verb,C1,Technology,,
"""


def test_parse_cefr_csv_maps_columns() -> None:
    entries = parse_cefr_csv(SAMPLE_CSV)
    assert CefrEntry("abandon", "verb", "B1", "") in entries
    longevity = next(e for e in entries if e.headword == "longevity")
    assert longevity.cefr_level == "C2"


def test_parse_cefr_csv_skips_unknown_levels() -> None:
    csv_text = "headword,pos,CEFR\nfoo,noun,XX\nbar,noun,A1\n"
    entries = parse_cefr_csv(csv_text)
    assert [e.headword for e in entries] == ["bar"]


def test_dedupe_keeps_first_per_headword_pos() -> None:
    entries = [
        CefrEntry("Abandon", "verb", "B1"),
        CefrEntry("abandon", "verb", "B2"),
        CefrEntry("abandon", "noun", "B1"),
    ]
    result = dedupe(entries)
    assert len(result) == 2
    assert result[0].cefr_level == "B1"


def test_curate_limits_per_level() -> None:
    entries = [
        CefrEntry("a", "noun", "A1"),
        CefrEntry("b", "noun", "A1"),
        CefrEntry("c", "noun", "A1"),
        CefrEntry("d", "noun", "B1"),
    ]
    result = curate(entries, limit_per_level=2)
    assert sum(1 for e in result if e.cefr_level == "A1") == 2
    assert any(e.cefr_level == "B1" for e in result)


def test_make_term_id_is_deterministic_and_unique() -> None:
    entry = CefrEntry("abandon", "verb", "B1")
    assert make_term_id(entry, prefix="gen") == make_term_id(entry, prefix="gen")
    other = CefrEntry("abandon", "noun", "B1")
    assert make_term_id(entry, prefix="gen") != make_term_id(other, prefix="gen")


def test_build_record_maps_tier_and_subfield() -> None:
    entry = CefrEntry("withstand", "verb", "C1", topic="Technology")
    record = build_record(entry, TermDetails(definition="def", example="ex", synonyms=["endure"]))
    assert record["cefr_level"] == "C1"
    assert record["difficulty_tier"] == "advanced"
    assert record["subfield"] == "technology"
    assert record["domain"] == "general"
    assert record["synonyms"] == ["endure"]


def test_build_corpus_uses_injected_generator() -> None:
    entries = [CefrEntry("a", "noun", "A1"), CefrEntry("b", "verb", "B1")]
    calls: list[str] = []

    def fake_generate(entry: CefrEntry) -> TermDetails:
        calls.append(entry.headword)
        return TermDetails(definition=f"means {entry.headword}")

    records = build_corpus(entries, fake_generate)
    assert calls == ["a", "b"]
    assert len(records) == 2
    assert records[0]["definition"] == "means a"


def test_generated_record_is_ingestible() -> None:
    entry = CefrEntry("abandon", "verb", "B1")
    record = build_record(entry, TermDetails(definition="d", example="e", synonyms=["leave"]))
    term = raw_to_term(record, source="cefr-j")
    assert term.payload.term == "abandon"
    assert term.meta.cefr_level == "B1"
    assert term.meta.difficulty_tier == "intermediate"


def test_parse_term_list_skips_comments_and_duplicates() -> None:
    text = "# comment\nmachine learning\n\nEmbedding\nembedding\ntoken, noun\n"
    assert parse_term_list(text) == ["machine learning", "Embedding", "token"]


def test_parse_domain_details_reads_cefr_and_subfield() -> None:
    raw = (
        '{"definition": "d", "example": "e", "synonyms": ["x"], '
        '"cefr_level": "C1", "subfield": "LLM Architecture"}'
    )
    details = parse_domain_details(raw)
    assert details.cefr_level == "C1"
    assert details.subfield == "llm_architecture"
    assert details.definition == "d"


def test_parse_domain_details_defaults_invalid_cefr() -> None:
    details = parse_domain_details('{"definition": "d", "cefr_level": "Z9"}')
    assert details.cefr_level == "B2"


def test_build_domain_corpus_assigns_domain_and_tier() -> None:
    def fake_generate(term: str) -> DomainDetails:
        return DomainDetails(
            definition=f"means {term}",
            example="ex",
            synonyms=["s"],
            cefr_level="C1",
            subfield="llm_architecture",
        )

    records = build_domain_corpus(["embedding", "token"], fake_generate, domain="ai")
    assert len(records) == 2
    assert records[0]["domain"] == "ai"
    assert records[0]["cefr_level"] == "C1"
    assert records[0]["difficulty_tier"] == "advanced"
    assert records[0]["subfield"] == "llm_architecture"
    assert records[0]["source"] == "bedrock-generated"
    assert records[0]["term_id"] != records[1]["term_id"]


def test_parse_details_handles_code_fences() -> None:
    raw = '```json\n{"definition": "d", "example": "e", "synonyms": ["x"]}\n```'
    details = parse_details(raw)
    assert details.definition == "d"
    assert details.synonyms == ["x"]


def test_parse_details_accepts_comma_separated_synonyms() -> None:
    details = parse_details('{"definition":"d","example":"e","synonyms":"a, b, c, d"}')
    assert details.synonyms == ["a", "b", "c"]
