from lexicon.models import (
    CEFR_TO_TIER,
    CefrLevel,
    DifficultyTier,
    FilterableMetadata,
    TermPayload,
    VocabularyTerm,
)


def make_term(**overrides) -> VocabularyTerm:
    meta = FilterableMetadata(
        domain="ai",
        subfield="llm_architecture",
        difficulty_tier="advanced",
        cefr_level="C1",
    )
    payload = TermPayload(
        term_id="ai_rag_001",
        term="retrieval-augmented generation",
        definition="A technique that grounds a model's output in retrieved documents.",
        example="The assistant uses retrieval-augmented generation to cite sources.",
        synonyms=["RAG"],
    )
    return VocabularyTerm(meta=meta, payload=payload).model_copy(update=overrides)


def test_cefr_to_tier_mapping() -> None:
    assert CEFR_TO_TIER[CefrLevel.A1] == DifficultyTier.BEGINNER
    assert CEFR_TO_TIER[CefrLevel.B2] == DifficultyTier.INTERMEDIATE
    assert CEFR_TO_TIER[CefrLevel.C2] == DifficultyTier.ADVANCED


def test_embedding_text_includes_term_definition_example() -> None:
    text = make_term().embedding_text()
    assert "retrieval-augmented generation" in text
    assert "grounds a model's output" in text
    assert "cite sources" in text
    assert "Synonyms: RAG" in text


def test_filterable_metadata_drops_empty_keys() -> None:
    metadata = FilterableMetadata(
        domain="general",
        difficulty_tier="beginner",
        cefr_level="A1",
        owner_id="",
        list_id="",
        subfield="",
    ).to_vector_metadata()
    assert "owner_id" not in metadata
    assert "list_id" not in metadata
    assert metadata["scope"] == "global"
