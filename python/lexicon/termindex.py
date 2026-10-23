"""DynamoDB term index + candidate pool (same single table as progress).

S3 Vectors can't list by metadata, so this index answers "which words sit in difficulty band
[lo, hi)?" with a key range query. The difficulty is zero-padded into the sort key.

| Entity                | PK                     | SK                        |
|-----------------------|------------------------|---------------------------|
| Candidate (unenriched)| ``CANDIDATE#<domain>`` | ``D#<ddd>#<slug>``        |
| Term by difficulty    | ``TERMS#<domain>``     | ``D#<ddd>#<term_id>``     |
| Term by id            | ``TERM#<term_id>``     | ``META``                  |

The ``TERM#…/META`` item also records words the model rejected (``status = "skipped"``) so a
source re-sync never offers them again.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import Any

from boto3.dynamodb.conditions import Key

from .dynamo import _floats_to_decimal, _table
from .models import TermRecord
from .sources import Candidate

_SLUG_RE = re.compile(r"[^a-z0-9]+")
BATCH_GET_SIZE = 100


def slugify(value: str) -> str:
    return _SLUG_RE.sub("_", value.lower()).strip("_")


def term_id_for(headword: str, domain: str = "general") -> str:
    """One term per headword per domain, so every source maps to the same id."""
    return f"{domain}_{slugify(headword)}"


def _band_sk(difficulty: int, suffix: str) -> str:
    return f"D#{difficulty:03d}#{suffix}"


def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in {"PK", "SK", "status"}}


class TermIndex:
    """Reads and writes enriched terms and the pending candidate pool."""

    def __init__(self, table: Any | None = None) -> None:
        self._table = table or _table()

    # --- candidates ---

    def put_candidates(self, candidates: Sequence[tuple[Candidate, int]], domain: str = "general") -> int:
        """Store ``(candidate, difficulty)`` pairs in the pool."""
        with self._table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as batch:
            for candidate, difficulty in candidates:
                batch.put_item(
                    Item={
                        "PK": f"CANDIDATE#{domain}",
                        "SK": _band_sk(difficulty, slugify(candidate.headword)),
                        "headword": candidate.headword,
                        "pos": candidate.pos,
                        "source": candidate.source,
                        "cefr_level": candidate.cefr_level,
                        "base_difficulty": candidate.base_difficulty,
                        "difficulty": difficulty,
                    }
                )
        return len(candidates)

    def candidates_in_band(
        self, lo: int, hi: int, *, domain: str = "general", limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Pending candidates with ``lo <= difficulty < hi``, easiest first."""
        return list(self._range(f"CANDIDATE#{domain}", lo, hi, limit=limit))

    def all_candidate_headwords(self, domain: str = "general") -> set[str]:
        return {
            str(item["headword"]).lower()
            for item in self._range(f"CANDIDATE#{domain}", 0, 101)
        }

    def remove_candidate(self, item: dict[str, Any]) -> None:
        self._table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

    # --- terms ---

    def put_term(self, term: TermRecord) -> None:
        body = _floats_to_decimal(term.model_dump(mode="json"))
        with self._table.batch_writer() as batch:
            batch.put_item(Item={**body, "PK": f"TERM#{term.term_id}", "SK": "META", "status": "active"})
            batch.put_item(
                Item={**body, "PK": f"TERMS#{term.domain}", "SK": _band_sk(term.difficulty, term.term_id)}
            )

    def mark_skipped(self, term_id: str, headword: str, reason: str) -> None:
        self._table.put_item(
            Item={
                "PK": f"TERM#{term_id}",
                "SK": "META",
                "status": "skipped",
                "term": headword,
                "skip_reason": reason,
            }
        )

    def mark_skipped_many(self, entries: Sequence[tuple[str, str, str]]) -> None:
        """Batch form of :meth:`mark_skipped` for ``(term_id, headword, reason)`` triples."""
        with self._table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as batch:
            for term_id, headword, reason in entries:
                batch.put_item(
                    Item={
                        "PK": f"TERM#{term_id}",
                        "SK": "META",
                        "status": "skipped",
                        "term": headword,
                        "skip_reason": reason,
                    }
                )

    def terms_in_band(
        self, lo: int, hi: int, *, domain: str = "general", limit: int | None = None
    ) -> list[TermRecord]:
        return [
            TermRecord.model_validate(_strip_keys(item))
            for item in self._range(f"TERMS#{domain}", lo, hi, limit=limit)
        ]

    def get_terms(self, term_ids: Sequence[str]) -> dict[str, TermRecord]:
        """Active terms by id (missing and skipped ids are omitted)."""
        found: dict[str, TermRecord] = {}
        for item in self._batch_get_meta(term_ids):
            if item.get("status") == "active":
                record = TermRecord.model_validate(_strip_keys(item))
                found[record.term_id] = record
        return found

    def known_ids(self, term_ids: Sequence[str]) -> set[str]:
        """Ids that already have a ``META`` item (active *or* skipped)."""
        return {str(item["PK"]).removeprefix("TERM#") for item in self._batch_get_meta(term_ids)}

    # --- internals ---

    def _range(
        self, pk: str, lo: int, hi: int, *, limit: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Query ``lo <= difficulty < hi`` (SKs sort lexically thanks to zero padding)."""
        request: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(pk)
            & Key("SK").between(f"D#{max(lo, 0):03d}#", f"D#{max(hi - 1, 0):03d}#￿"),
        }
        yielded = 0
        while True:
            response = self._table.query(**request)
            for item in response.get("Items", []):
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            last = response.get("LastEvaluatedKey")
            if not last:
                return
            request["ExclusiveStartKey"] = last

    def _batch_get_meta(self, term_ids: Sequence[str]) -> Iterator[dict[str, Any]]:
        client = self._table.meta.client  # resource client: plain Python types in and out
        unique = list(dict.fromkeys(term_ids))
        for start in range(0, len(unique), BATCH_GET_SIZE):
            pending: dict[str, Any] = {
                self._table.name: {
                    "Keys": [
                        {"PK": f"TERM#{term_id}", "SK": "META"}
                        for term_id in unique[start : start + BATCH_GET_SIZE]
                    ]
                }
            }
            while pending:
                response = client.batch_get_item(RequestItems=pending)
                yield from response.get("Responses", {}).get(self._table.name, [])
                pending = response.get("UnprocessedKeys") or {}
