from lexicon.ingestion import raw_to_term


def test_raw_to_term_derives_tier_from_cefr() -> None:
    term = raw_to_term(
        {
            "term_id": "gen_001",
            "term": "abandon",
            "definition": "to leave something behind",
            "example": "They abandoned the plan.",
            "cefr_level": "B1",
            "domain": "general",
            "subfield": "linguistics",
        }
    )
    assert term.meta.difficulty_tier == "intermediate"
    assert term.meta.scope == "global"
    assert term.payload.synonyms == []


def test_raw_to_term_parses_comma_separated_synonyms() -> None:
    term = raw_to_term(
        {
            "term_id": "gen_002",
            "term": "big",
            "definition": "of considerable size",
            "cefr_level": "A1",
            "domain": "general",
            "synonyms": "large, huge, immense",
        }
    )
    assert term.payload.synonyms == ["large", "huge", "immense"]


def test_raw_to_term_respects_explicit_tier() -> None:
    term = raw_to_term(
        {
            "term_id": "ai_001",
            "term": "embedding",
            "definition": "a dense vector representation",
            "cefr_level": "B2",
            "domain": "ai",
            "difficulty_tier": "advanced",
        }
    )
    assert term.meta.difficulty_tier == "advanced"
