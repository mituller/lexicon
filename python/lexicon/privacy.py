"""Data export and account deletion (GDPR-style).

Kept as pure functions where possible so the payload shape is unit-testable without AWS.
"""

from __future__ import annotations

from typing import Any

from .models import ProgressRecord, UserAggregate, UserList, utcnow


def build_export(
    user_id: str,
    *,
    progress: list[ProgressRecord],
    lists: list[UserList],
    aggregate: UserAggregate,
) -> dict[str, Any]:
    """Assemble a complete, portable export of a user's data."""
    return {
        "user_id": user_id,
        "exported_at": utcnow().isoformat(),
        "schema_version": 1,
        "aggregate": aggregate.model_dump(mode="json"),
        "lists": [item.model_dump(mode="json") for item in lists],
        "progress": [item.model_dump(mode="json") for item in progress],
    }
