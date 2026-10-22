"""DynamoDB single-table access for per-user progress, aggregates, and user lists.

Key design (single table ``lexicon``):

| Entity            | PK              | SK                     |
|-------------------|-----------------|------------------------|
| Progress (term)   | ``USER#<sub>``  | ``PROGRESS#<term_id>`` |
| User list         | ``USER#<sub>``  | ``LIST#<list_id>``     |
| Aggregate         | ``USER#<sub>``  | ``AGGREGATE``          |

Progress is per-term FSRS state; the aggregate is recomputed from it (see ``prompt.md`` Q-i).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from .config import get_settings
from .models import ProgressRecord, UserAggregate, UserList


@lru_cache(maxsize=1)
def _table() -> Any:
    settings = get_settings()
    return boto3.resource("dynamodb", region_name=settings.aws_region).Table(
        settings.dynamodb_table
    )


def _pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _floats_to_decimal(value: Any) -> Any:
    """DynamoDB rejects native floats; store them as Decimal."""
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {k: _floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_floats_to_decimal(v) for v in value]
    return value


def progress_item(record: ProgressRecord) -> dict[str, Any]:
    """Serialize a progress record for DynamoDB."""
    item = record.model_dump(mode="json")
    item["PK"] = _pk(record.user_id)
    item["SK"] = f"PROGRESS#{record.term_id}"
    return _floats_to_decimal(item)


def progress_from_item(item: dict[str, Any]) -> ProgressRecord:
    """Deserialize a DynamoDB progress item."""
    data = {k: v for k, v in item.items() if k not in {"PK", "SK"}}
    return ProgressRecord.model_validate(data)


class ProgressStore:
    """DynamoDB-backed progress, aggregate, and list storage."""

    def __init__(self, table: Any | None = None) -> None:
        self._table = table or _table()

    # --- progress ---

    def put_progress(self, record: ProgressRecord) -> None:
        self._table.put_item(Item=progress_item(record))

    def get_progress(self, user_id: str, term_id: str) -> ProgressRecord | None:
        response = self._table.get_item(Key={"PK": _pk(user_id), "SK": f"PROGRESS#{term_id}"})
        item = response.get("Item")
        return progress_from_item(item) if item else None

    def list_progress(self, user_id: str) -> list[ProgressRecord]:
        response = self._table.query(
            KeyConditionExpression=Key("PK").eq(_pk(user_id))
            & Key("SK").begins_with("PROGRESS#")
        )
        return [progress_from_item(item) for item in response.get("Items", [])]

    def delete_progress(self, user_id: str, term_id: str) -> None:
        self._table.delete_item(Key={"PK": _pk(user_id), "SK": f"PROGRESS#{term_id}"})

    # --- aggregate ---

    def put_aggregate(self, aggregate: UserAggregate) -> None:
        item = aggregate.model_dump(mode="json")
        item["PK"] = _pk(aggregate.user_id)
        item["SK"] = "AGGREGATE"
        self._table.put_item(Item=_floats_to_decimal(item))

    def get_aggregate(self, user_id: str) -> UserAggregate:
        response = self._table.get_item(Key={"PK": _pk(user_id), "SK": "AGGREGATE"})
        item = response.get("Item")
        if not item:
            return UserAggregate(user_id=user_id)
        data = {k: v for k, v in item.items() if k not in {"PK", "SK"}}
        return UserAggregate.model_validate(data)

    def recompute_aggregate(self, user_id: str, *, daily_goal: int = 20) -> UserAggregate:
        """Derive the aggregate from the user's per-term progress (not persisted).

        Callers persist the result via :meth:`put_aggregate`. Counts and streaks live in
        ``lexicon.progress`` so the rules are unit-tested.
        """
        from .progress import bookkeeping  # local import avoids a module cycle

        aggregate = self.get_aggregate(user_id)
        aggregate.daily_goal = daily_goal
        return bookkeeping(self.list_progress(user_id), aggregate)

    # --- user lists ---

    def put_list(self, user_list: UserList) -> None:
        item = user_list.model_dump(mode="json")
        item["PK"] = _pk(user_list.user_id)
        item["SK"] = f"LIST#{user_list.list_id}"
        self._table.put_item(Item=_floats_to_decimal(item))

    def list_lists(self, user_id: str) -> list[UserList]:
        response = self._table.query(
            KeyConditionExpression=Key("PK").eq(_pk(user_id)) & Key("SK").begins_with("LIST#")
        )
        return [
            UserList.model_validate({k: v for k, v in item.items() if k not in {"PK", "SK"}})
            for item in response.get("Items", [])
        ]

    # --- import / delete account ---

    def delete_all(self, user_id: str) -> int:
        """Delete every item for a user (account deletion). Returns items removed."""
        response = self._table.query(
            KeyConditionExpression=Key("PK").eq(_pk(user_id)),
            ProjectionExpression="PK, SK",
        )
        items: Sequence[dict[str, Any]] = response.get("Items", [])
        removed = 0
        with self._table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
                removed += 1
        return removed
