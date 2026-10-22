from lexicon.retriever import build_query_filter, to_hit
from lexicon.vectorstore import build_scope_filter


def test_scope_filter_without_user_is_global_only() -> None:
    assert build_scope_filter(None) == {"scope": {"$eq": "global"}}


def test_scope_filter_with_user_includes_owner() -> None:
    result = build_scope_filter("user-1")
    assert result == {"$or": [{"scope": {"$eq": "global"}}, {"owner_id": {"$eq": "user-1"}}]}


def test_build_query_filter_combines_scope_and_metadata() -> None:
    result = build_query_filter(
        user_id="user-1",
        domains=["ai"],
        difficulty_tiers=["advanced"],
        cefr_levels=["C1"],
    )
    assert "$and" in result
    clauses = result["$and"]
    assert any("domain" in clause for clause in clauses)
    assert any("difficulty_tier" in clause for clause in clauses)
    assert any("cefr_level" in clause for clause in clauses)
    assert any("$or" in clause for clause in clauses)


def test_build_query_filter_scope_only() -> None:
    assert build_query_filter() == {"scope": {"$eq": "global"}}


def test_to_hit_maps_metadata() -> None:
    item = {
        "key": "ai_rag_001",
        "distance": 0.12,
        "metadata": {
            "term": "RAG",
            "definition": "def",
            "example": "ex",
            "synonyms": ["retrieval-augmented generation"],
            "domain": "ai",
            "difficulty_tier": "advanced",
            "cefr_level": "C1",
            "scope": "global",
        },
    }
    hit = to_hit(item)
    assert hit.term_id == "ai_rag_001"
    assert hit.term == "RAG"
    assert hit.distance == 0.12
    assert hit.synonyms == ["retrieval-augmented generation"]
