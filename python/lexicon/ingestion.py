"""Ingestion pipeline: raw vocabulary records -> embeddings -> S3 Vectors.

Used by the offline CLI (``python -m lexicon seed``) and by the API when a user creates or edits
a list. Embeddings are generated here, never in the request path for the global corpus.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from .bedrock import BedrockClient
from .models import CEFR_TO_TIER, CefrLevel, FilterableMetadata, TermPayload, VocabularyTerm
from .vectorstore import VectorStore


def raw_to_term(raw: dict[str, Any], *, source: str = "") -> VocabularyTerm:
    """Map a raw record (see ``data/schema.md``) to a :class:`VocabularyTerm`.

    ``difficulty_tier`` is derived from ``cefr_level`` when not supplied.
    """
    cefr = CefrLevel(raw["cefr_level"])
    tier = raw.get("difficulty_tier") or CEFR_TO_TIER[cefr].value
    meta = FilterableMetadata(
        domain=raw["domain"],
        subfield=raw.get("subfield", ""),
        difficulty_tier=tier,
        cefr_level=cefr.value,
        scope=raw.get("scope", "global"),
        owner_id=raw.get("owner_id", ""),
        list_id=raw.get("list_id", ""),
    )
    payload = TermPayload(
        term_id=raw["term_id"],
        term=raw["term"],
        definition=raw["definition"],
        example=raw.get("example", ""),
        synonyms=_as_synonyms(raw.get("synonyms")),
        pos=raw.get("pos", ""),
        source=raw.get("source", source),
    )
    return VocabularyTerm(meta=meta, payload=payload)


def _as_synonyms(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return [str(s) for s in value]


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield records from a JSON Lines file."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def embed_terms(terms: Sequence[VocabularyTerm], bedrock: BedrockClient | None = None) -> list[list[float]]:
    """Embed every term's semantic text (one Bedrock call per term)."""
    client = bedrock or BedrockClient()
    return [client.embed(term.embedding_text()) for term in terms]


def ingest(
    terms: Sequence[VocabularyTerm],
    *,
    store: VectorStore | None = None,
    bedrock: BedrockClient | None = None,
) -> int:
    """Embed and upsert ``terms``. Returns the number of vectors written."""
    if not terms:
        return 0
    vectors = embed_terms(terms, bedrock)
    return (store or VectorStore()).put_terms(terms, vectors)


def ingest_records(
    records: Sequence[dict[str, Any]],
    *,
    source: str = "",
    store: VectorStore | None = None,
    bedrock: BedrockClient | None = None,
) -> int:
    """Convenience wrapper: raw records -> terms -> embed -> upsert."""
    terms = [raw_to_term(record, source=source) for record in records]
    return ingest(terms, store=store, bedrock=bedrock)
