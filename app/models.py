"""Dataclasses mirroring db/schema.sql."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any, Optional


def generate_id() -> str:
    """12-char hex id — stable, non-sequential, URL-safe."""
    return uuid.uuid4().hex[:12]


@dataclass
class Expense:
    id: str
    date: str
    amount: float
    currency: str = "CNY"
    category: Optional[str] = None
    description: Optional[str] = None
    paid: bool = False
    paid_date: Optional[str] = None
    submitted_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HistoryEntry:
    id: str
    expense_id: str
    seq: int  # monotonic per-expense ordering
    action: str  # create | update | mark_paid | unmark_paid | delete
    changed_by: Optional[str]
    changed_at: str
    snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
