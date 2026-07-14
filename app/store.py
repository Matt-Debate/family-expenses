"""Store — all reads/writes for the household ledger.

Contract guarantees implemented here (docs/FEATURE_CONTRACT.md §6):
  * server-authoritative validation (amount > 0, YYYY-MM-DD dates,
    paid ⇒ paid_date, unknown update fields rejected);
  * every mutation writes exactly one append-only ``expense_history`` row in
    the SAME transaction as the primary write (finding M3);
  * timestamps are application-managed UTC ISO strings (portable SQL);
  * token minting is a first-class, operator-only capability (finding M2).
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .db import Database
from .models import Expense, HistoryEntry, generate_id


class ValidationError(ValueError):
    """Caller-supplied data failed validation."""


_ALLOWED_UPDATE_FIELDS = frozenset(
    {"date", "amount", "currency", "category", "description", "submitted_by"}
)
_EXPENSE_COLS = (
    "id, date, amount, currency, category, description, "
    "paid, paid_date, submitted_by, created_at, updated_at"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def today_str() -> str:
    """Today's date in the household's timezone (APP_TZ, default China).

    Server clocks run UTC; a family in China adding an expense after
    08:00 CST would otherwise get 'yesterday'.
    """
    import os

    tz_name = os.environ.get("APP_TZ", "Asia/Shanghai")
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# tolerated decoration around spoken/pasted amounts: ¥300, 300块, 1,200元, "300 rmb"
_AMOUNT_NOISE_RE = re.compile(r"[¥￥,，\s]|元|块|rmb|cny", re.IGNORECASE)


class Store:
    def __init__(self, db: Database):
        self.db = db

    # ── validation ────────────────────────────────────────────────────────
    @staticmethod
    def _validate_amount(amount: Any) -> float:
        if isinstance(amount, str):
            amount = _AMOUNT_NOISE_RE.sub("", amount)
        try:
            val = float(amount)
        except (TypeError, ValueError):
            raise ValidationError("amount must be a number")
        if not (val > 0):
            raise ValidationError("amount must be greater than 0")
        return val

    @staticmethod
    def _validate_date(value: Any, field: str = "date") -> str:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValidationError(f"{field} is required")
        if not _DATE_RE.match(text):
            raise ValidationError(f"{field} must be in YYYY-MM-DD format")
        return text

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_expense(row: dict[str, Any]) -> Expense:
        return Expense(
            id=row["id"],
            date=row["date"],
            amount=float(row["amount"]),
            currency=row["currency"],
            category=row["category"],
            description=row["description"],
            paid=bool(row["paid"]),
            paid_date=row["paid_date"],
            submitted_by=row["submitted_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _fetch(self, tx, expense_id: str) -> Optional[dict[str, Any]]:
        return tx.query_one(
            f"SELECT {_EXPENSE_COLS} FROM expenses WHERE id = :id", {"id": expense_id}
        )

    def _write_history(
        self, tx, expense_id: str, action: str,
        changed_by: Optional[str], snapshot: dict[str, Any],
    ) -> None:
        tx.execute(
            "INSERT INTO expense_history (id, expense_id, seq, action, changed_by, changed_at, snapshot) "
            "VALUES (:id, :expense_id, "
            "(SELECT COUNT(*) FROM expense_history WHERE expense_id = :expense_id), "
            ":action, :changed_by, :changed_at, :snapshot)",
            {
                "id": generate_id(),
                "expense_id": expense_id,
                "action": action,
                "changed_by": changed_by,
                "changed_at": _utc_now_iso(),
                "snapshot": json.dumps(snapshot, ensure_ascii=False),
            },
        )

    # ── mutations (each = one atomic transaction incl. history) ──────────
    def create(
        self, *, date: str, amount: Any, currency: str = "CNY",
        category: Optional[str] = None, description: Optional[str] = None,
        submitted_by: Optional[str] = None,
    ) -> Expense:
        date = self._validate_date(date)
        amount = self._validate_amount(amount)
        currency = (str(currency).strip() if currency else "") or "CNY"
        now = _utc_now_iso()
        expense = Expense(
            id=generate_id(), date=date, amount=amount, currency=currency,
            category=category, description=description, paid=False,
            paid_date=None, submitted_by=submitted_by,
            created_at=now, updated_at=now,
        )
        with self.db.tx() as tx:
            tx.execute(
                "INSERT INTO expenses (id, date, amount, currency, category, description, "
                "paid, paid_date, submitted_by, created_at, updated_at) "
                "VALUES (:id, :date, :amount, :currency, :category, :description, "
                ":paid, :paid_date, :submitted_by, :created_at, :updated_at)",
                expense.to_dict(),
            )
            self._write_history(tx, expense.id, "create", submitted_by, expense.to_dict())
        return expense

    def update(
        self, expense_id: str, *, fields: dict[str, Any],
        changed_by: Optional[str] = None,
    ) -> Expense:
        if not isinstance(fields, dict) or not fields:
            raise ValidationError("no fields to update")
        unknown = set(fields) - _ALLOWED_UPDATE_FIELDS
        if unknown:
            raise ValidationError(f"unknown update fields: {sorted(unknown)}")
        clean: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "amount":
                clean[key] = self._validate_amount(value)
            elif key == "date":
                clean[key] = self._validate_date(value)
            elif key == "currency":
                clean[key] = (str(value).strip() if value else "") or "CNY"
            else:
                clean[key] = value
        clean["updated_at"] = _utc_now_iso()
        # keys validated against the frozenset above → safe to interpolate
        set_clause = ", ".join(f"{k} = :{k}" for k in clean)
        with self.db.tx() as tx:
            cur = tx.execute(
                f"UPDATE expenses SET {set_clause} WHERE id = :expense_id",
                dict(clean, expense_id=expense_id),
            )
            if cur.rowcount == 0:
                raise KeyError(expense_id)
            expense = self._row_to_expense(self._fetch(tx, expense_id))
            self._write_history(tx, expense_id, "update", changed_by, expense.to_dict())
        return expense

    def mark_paid(
        self, expense_id: str, *, paid: bool,
        paid_date: Optional[str] = None, changed_by: Optional[str] = None,
    ) -> Expense:
        paid = bool(paid)
        paid_date = self._validate_date(paid_date, field="paid_date") if paid else None
        with self.db.tx() as tx:
            cur = tx.execute(
                "UPDATE expenses SET paid = :paid, paid_date = :paid_date, "
                "updated_at = :updated_at WHERE id = :expense_id",
                {
                    "paid": paid, "paid_date": paid_date,
                    "updated_at": _utc_now_iso(), "expense_id": expense_id,
                },
            )
            if cur.rowcount == 0:
                raise KeyError(expense_id)
            expense = self._row_to_expense(self._fetch(tx, expense_id))
            action = "mark_paid" if paid else "unmark_paid"
            self._write_history(tx, expense_id, action, changed_by, expense.to_dict())
        return expense

    def delete(self, expense_id: str, *, changed_by: Optional[str] = None) -> bool:
        with self.db.tx() as tx:
            row = self._fetch(tx, expense_id)
            if row is None:
                return False
            # history row survives the delete (pre-change snapshot)
            self._write_history(
                tx, expense_id, "delete", changed_by,
                self._row_to_expense(row).to_dict(),
            )
            tx.execute("DELETE FROM expenses WHERE id = :id", {"id": expense_id})
        return True

    # ── reads ─────────────────────────────────────────────────────────────
    def list(
        self, *, status: str = "all",
        since: Optional[str] = None, until: Optional[str] = None,
    ) -> list[Expense]:
        clauses, params = ["1 = 1"], {}
        if status == "paid":
            clauses.append("paid = :paid")
            params["paid"] = True
        elif status == "unpaid":
            clauses.append("paid = :paid")
            params["paid"] = False
        elif status not in ("all", None, ""):
            raise ValidationError(f"invalid status filter: {status!r}")
        if since:
            clauses.append("date >= :since")
            params["since"] = self._validate_date(since, field="since")
        if until:
            clauses.append("date <= :until")
            params["until"] = self._validate_date(until, field="until")
        with self.db.tx() as tx:
            rows = tx.query(
                f"SELECT {_EXPENSE_COLS} FROM expenses WHERE {' AND '.join(clauses)} "
                "ORDER BY date DESC, created_at DESC",
                params,
            )
        return [self._row_to_expense(row) for row in rows]

    def find(self, query: str, *, status: str = "all") -> list["Expense"]:
        """Case-insensitive substring match on description/category.

        Powers natural-language targeting from the MCP ("the football class")
        so callers don't need ids.
        """
        needle = f"%{str(query or '').strip().lower()}%"
        clauses = [
            "(LOWER(COALESCE(description,'')) LIKE :q OR LOWER(COALESCE(category,'')) LIKE :q)"
        ]
        params: dict[str, Any] = {"q": needle}
        if status == "paid":
            clauses.append("paid = :paid")
            params["paid"] = True
        elif status == "unpaid":
            clauses.append("paid = :paid")
            params["paid"] = False
        with self.db.tx() as tx:
            rows = tx.query(
                f"SELECT {_EXPENSE_COLS} FROM expenses WHERE {' AND '.join(clauses)} "
                "ORDER BY date DESC, created_at DESC",
                params,
            )
        return [self._row_to_expense(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self.db.tx() as tx:
            row = tx.query_one(
                "SELECT COUNT(*) AS count, "
                "COALESCE(SUM(amount), 0) AS total, "
                "COALESCE(SUM(amount) FILTER (WHERE paid), 0) AS paid, "
                "COALESCE(SUM(amount) FILTER (WHERE NOT paid), 0) AS unpaid, "
                "COUNT(*) FILTER (WHERE NOT paid) AS unpaid_count "
                "FROM expenses"
            )
        return {
            "count": int(row["count"]),
            "total": round(float(row["total"]), 2),
            "paid": round(float(row["paid"]), 2),
            "unpaid": round(float(row["unpaid"]), 2),
            "unpaid_count": int(row["unpaid_count"]),
        }

    def history(self, expense_id: str) -> list[HistoryEntry]:
        with self.db.tx() as tx:
            rows = tx.query(
                "SELECT id, expense_id, seq, action, changed_by, changed_at, snapshot "
                "FROM expense_history WHERE expense_id = :id "
                "ORDER BY seq ASC",
                {"id": expense_id},
            )
        return [
            HistoryEntry(
                id=r["id"], expense_id=r["expense_id"], seq=r["seq"], action=r["action"],
                changed_by=r["changed_by"], changed_at=r["changed_at"],
                snapshot=json.loads(r["snapshot"]),
            )
            for r in rows
        ]

    # ── access tokens (operator-only minting — finding M2) ───────────────
    def mint_token(
        self, *, label: Optional[str] = None, expires_days: Optional[int] = None
    ) -> dict[str, Any]:
        """Mint a link token. Default: NEVER expires (household links must not
        demand credential renewal from non-technical holders; revocation is
        the kill switch). Pass expires_days for a bounded token."""
        expires_at = None
        if expires_days is not None:
            try:
                expires_days = max(1, min(3650, int(expires_days)))
            except (TypeError, ValueError):
                expires_days = None
            if expires_days is not None:
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(days=expires_days)
                ).strftime("%Y-%m-%dT%H:%M:%S")
        token = secrets.token_hex(32)
        with self.db.tx() as tx:
            tx.execute(
                "INSERT INTO access_tokens (id, token, label, expires_at, revoked, created_at, use_count) "
                "VALUES (:id, :token, :label, :expires_at, :revoked, :created_at, 0)",
                {
                    "id": generate_id(), "token": token, "label": label,
                    "expires_at": expires_at, "revoked": False,
                    "created_at": _utc_now_iso(),
                },
            )
        return {"token": token, "label": label, "expires_at": expires_at}

    def validate_token(self, token: Any) -> Optional[dict[str, Any]]:
        """Return the token row when valid; None when unknown/revoked/expired.

        Fail-closed on every path; bumps last_used_at/use_count on success.
        """
        if not token or not isinstance(token, str):
            return None
        with self.db.tx() as tx:
            row = tx.query_one(
                "SELECT id, token, label, expires_at, revoked, created_at, use_count "
                "FROM access_tokens WHERE token = :token",
                {"token": token},
            )
            if row is None or bool(row["revoked"]):
                return None
            expires_at = row["expires_at"]
            if expires_at and str(expires_at) <= _utc_now_iso():
                return None  # only bounded tokens can expire; NULL = never
            tx.execute(
                "UPDATE access_tokens SET last_used_at = :now, use_count = use_count + 1 "
                "WHERE id = :id",
                {"now": _utc_now_iso(), "id": row["id"]},
            )
        return row

    def revoke_token(self, token_or_id: str) -> bool:
        with self.db.tx() as tx:
            cur = tx.execute(
                "UPDATE access_tokens SET revoked = :revoked "
                "WHERE token = :value OR id = :value",
                {"revoked": True, "value": token_or_id},
            )
            return cur.rowcount > 0

    def list_tokens(self) -> list[dict[str, Any]]:
        with self.db.tx() as tx:
            return tx.query(
                "SELECT id, label, expires_at, revoked, created_at, last_used_at, use_count "
                "FROM access_tokens ORDER BY created_at DESC"
            )
