"""Filtered semantic search over S3 Vectors.

Given a user query plus optional domain / difficulty / CEFR constraints, embed the query once
and return the most relevant vocabulary. Scope filtering keeps the shared corpus and private
user lists isolated (S3 Vectors has no namespaces).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .bedrock import BedrockClient
from .models import SearchHit
from .vectorstore import VectorStore, build_scope_filter


def build_query_filter(
    *,
    user_id: str | None = None,
    domains: Sequence[str] | None = None,
    difficulty_tiers: Sequence[str] | None = None,
    cefr_levels: Sequence[str] | None = None,
    list_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Combine the scope filter with optional metadata constraints using ``$and``."""
    clauses: list[dict[str, Any]] = [build_scope_filter(user_id)]
    if domains:
        clauses.append({"domain": {"$in": list(domains)}})
    if difficulty_tiers:
        clauses.append({"difficulty_tier": {"$in": list(difficulty_tiers)}})
    if cefr_levels:
        clauses.append({"cefr_level": {"$in": list(cefr_levels)}})
    if list_ids:
        clauses.append({"list_id": {"$in": list(list_ids)}})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class Retriever:
    """Encapsulates query embedding + S3 Vectors query + response mapping."""

    def __init__(self, store: VectorStore | None = None, bedrock: BedrockClient | None = None) -> None:
        self._store = store or VectorStore()
        self._bedrock = bedrock or BedrockClient()

    def search(
        self,
        query: str,
        *,
        user_id: str | None = None,
        domains: Sequence[str] | None = None,
        difficulty_tiers: Sequence[str] | None = None,
        cefr_levels: Sequence[str] | None = None,
        list_ids: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> list[SearchHit]:
        """Return the top-k matching vocabulary terms."""
        query_vector = self._bedrock.embed(query)
        query_filter = build_query_filter(
            user_id=user_id,
            domains=domains,
            difficulty_tiers=difficulty_tiers,
            cefr_levels=cefr_levels,
            list_ids=list_ids,
        )
        raw = self._store.query(query_vector, top_k=top_k, filter=query_filter)
        return [to_hit(item) for item in raw]


def to_hit(item: dict[str, Any]) -> SearchHit:
    """Map an S3 Vectors result row to a :class:`SearchHit`."""
    metadata: dict[str, Any] = item.get("metadata") or {}
    return SearchHit(
        term_id=item.get("key", ""),
        term=metadata.get("term", ""),
        definition=metadata.get("definition", ""),
        example=metadata.get("example", ""),
        synonyms=metadata.get("synonyms") or [],
        domain=metadata.get("domain", ""),
        subfield=metadata.get("subfield", ""),
        difficulty_tier=metadata.get("difficulty_tier", ""),
        cefr_level=metadata.get("cefr_level", ""),
        scope=metadata.get("scope", "global"),
        distance=item.get("distance"),
    )
