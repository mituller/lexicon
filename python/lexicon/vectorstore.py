"""Amazon S3 Vectors wrapper.

Important design facts (verified against the S3 Vectors API):

* S3 Vectors has **no namespaces**. Isolation between the shared corpus and a user's private
  lists is done with the *filterable* metadata keys ``scope`` and ``owner_id``.
* Metadata is split into **filterable** (small, queried) and **non-filterable** (large text,
  returned but not filterable) via ``metadataConfiguration.nonFilterableMetadataKeys``, which
  is fixed at index creation.
* Vectors are stored as ``float32``.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import get_settings
from .models import Scope, VocabularyTerm

_RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "standard"})

# Large payload fields stored as non-filterable metadata (returned with results).
NON_FILTERABLE_KEYS = ["term", "definition", "example", "synonyms", "pos", "source"]

# S3 Vectors accepts at most this many vectors per put request.
PUT_BATCH_SIZE = 100


def to_float32(values: Sequence[float]) -> list[float]:
    """Cast values to true 32-bit floats without needing numpy.

    S3 Vectors stores vectors as float32; this makes the precision explicit (and keeps ~46 MB
    of native binaries out of the Lambda bundle).
    """
    return [struct.unpack("<f", struct.pack("<f", float(value)))[0] for value in values]


def drop_empty(metadata: dict[str, Any]) -> dict[str, Any]:
    """Remove empty values from metadata.

    S3 Vectors rejects empty arrays (`Empty arrays are not allowed in metadata`) and empty
    strings add nothing, so omit them entirely.
    """
    return {
        key: value
        for key, value in metadata.items()
        if value is not None and value != "" and value != []
    }


@lru_cache(maxsize=1)
def _vectors_client() -> Any:
    return boto3.client(
        "s3vectors",
        region_name=get_settings().aws_region,
        config=_RETRY_CONFIG,
    )


def build_scope_filter(user_id: str | None) -> dict[str, Any]:
    """Filter that scopes a query to the global corpus plus (optionally) the user's lists.

    A ``user_id`` of ``None``/empty restricts results to the global corpus.
    """
    if user_id:
        return {
            "$or": [
                {"scope": {"$eq": Scope.GLOBAL.value}},
                {"owner_id": {"$eq": user_id}},
            ]
        }
    return {"scope": {"$eq": Scope.GLOBAL.value}}


class VectorStore:
    """Read/write access to a single S3 Vectors index."""

    def __init__(
        self,
        bucket: str | None = None,
        index: str | None = None,
        client: Any | None = None,
    ) -> None:
        settings = get_settings()
        self.bucket = bucket or settings.vector_bucket
        self.index = index or settings.vector_index
        self._client = client or _vectors_client()

    # --- provisioning ---

    def ensure_index(self, *, dimensions: int | None = None) -> None:
        """Create the vector bucket + index if they do not already exist (idempotent)."""
        settings = get_settings()
        dims = dimensions or settings.embedding_dimensions
        try:
            self._client.create_vector_bucket(vectorBucketName=self.bucket)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {
                "ConflictException",
                "BucketAlreadyExists",
            }:
                raise
        try:
            self._client.create_index(
                vectorBucketName=self.bucket,
                indexName=self.index,
                dataType="float32",
                dimension=dims,
                distanceMetric="cosine",
                metadataConfiguration={"nonFilterableMetadataKeys": NON_FILTERABLE_KEYS},
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {"ConflictException"}:
                raise

    # --- writes ---

    def put_terms(self, terms: Sequence[VocabularyTerm], embeddings: Sequence[Sequence[float]]) -> int:
        """Upsert ``terms`` with their precomputed ``embeddings``. Returns the count written."""
        if len(terms) != len(embeddings):
            raise ValueError("terms and embeddings must be the same length")

        batch: list[dict[str, Any]] = []
        written = 0
        for term, embedding in zip(terms, embeddings, strict=True):
            batch.append(
                {
                    "key": term.payload.term_id,
                    "data": {"float32": to_float32(embedding)},
                    "metadata": drop_empty(
                        {
                            **term.meta.to_vector_metadata(),
                            "term": term.payload.term,
                            "definition": term.payload.definition,
                            "example": term.payload.example,
                            "synonyms": term.payload.synonyms,
                            "pos": term.payload.pos,
                            "source": term.payload.source,
                        }
                    ),
                }
            )
            if len(batch) == PUT_BATCH_SIZE:
                self._put_batch(batch)
                written += len(batch)
                batch = []
        if batch:
            self._put_batch(batch)
            written += len(batch)
        return written

    def _put_batch(self, vectors: list[dict[str, Any]]) -> None:
        self._client.put_vectors(
            vectorBucketName=self.bucket,
            indexName=self.index,
            vectors=vectors,
        )

    def delete_terms(self, term_ids: Iterable[str]) -> None:
        """Remove vectors by key (used when a user deletes a list or term)."""
        keys = list(term_ids)
        if not keys:
            return
        self._client.delete_vectors(
            vectorBucketName=self.bucket,
            indexName=self.index,
            keys=keys,
        )

    def delete_by_owner(self, owner_id: str, *, page_size: int = 500) -> int:
        """Best-effort removal of every vector owned by a user (account deletion / list delete).

        S3 Vectors `list_vectors` cannot filter by metadata, so we page through and match
        `owner_id` client-side. Fine for per-user volumes; revisit if a user owns many vectors.
        """
        keys: list[str] = []
        next_token: str | None = None
        while True:
            request: dict[str, Any] = {
                "vectorBucketName": self.bucket,
                "indexName": self.index,
                "returnMetadata": True,
                "maxResults": page_size,
            }
            if next_token:
                request["nextToken"] = next_token
            response = self._client.list_vectors(**request)
            for vector in response.get("vectors", []):
                metadata = vector.get("metadata") or {}
                if metadata.get("owner_id") == owner_id and vector.get("key"):
                    keys.append(vector["key"])
            next_token = response.get("nextToken")
            if not next_token:
                break
        if keys:
            self.delete_terms(keys)
        return len(keys)

    # --- enumeration & keyed reads ---

    def list_vectors(
        self, *, max_results: int = 500, next_token: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List vectors (with metadata) in storage order. Does not filter by metadata."""
        request: dict[str, Any] = {
            "vectorBucketName": self.bucket,
            "indexName": self.index,
            "maxResults": max_results,
            "returnMetadata": True,
        }
        if next_token:
            request["nextToken"] = next_token
        response = self._client.list_vectors(**request)
        return list(response.get("vectors", [])), response.get("nextToken")

    def list_all_vectors(self, *, page_size: int = 500) -> list[dict[str, Any]]:
        """Drain every vector in the index with its metadata."""
        out: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            items, token = self.list_vectors(max_results=page_size, next_token=token)
            out.extend(items)
            if not token:
                return out

    def get_vectors(self, keys: Sequence[str], *, batch_size: int = 100) -> list[dict[str, Any]]:
        """Fetch full vectors (with metadata) by key, in batches."""
        out: list[dict[str, Any]] = []
        for start in range(0, len(keys), batch_size):
            chunk = list(keys[start : start + batch_size])
            if not chunk:
                continue
            response = self._client.get_vectors(
                vectorBucketName=self.bucket,
                indexName=self.index,
                keys=chunk,
                returnMetadata=True,
            )
            out.extend(response.get("vectors", []))
        return out

    # --- reads ---

    def query(
        self,
        vector: Sequence[float],
        *,
        top_k: int = 8,
        filter: dict[str, Any] | None = None,  # noqa: A002 - matches the AWS API name
        return_metadata: bool = True,
    ) -> list[dict[str, Any]]:
        """Approximate nearest-neighbour search, optionally metadata-filtered."""
        response = self._client.query_vectors(
            vectorBucketName=self.bucket,
            indexName=self.index,
            topK=top_k,
            queryVector={"float32": to_float32(vector)},
            filter=filter or {},
            returnMetadata=return_metadata,
            returnDistance=True,
        )
        return list(response.get("vectors", []))
