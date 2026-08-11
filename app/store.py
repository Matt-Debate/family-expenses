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
from math import fsum
from typing import Any, Optional

from .db import Database
from .models import Expense, HistoryEntry, generate_id


class ValidationError(ValueError):
    """Caller-supplied data failed validation."""


class NotFoundError(KeyError):
    """No expense with that id.

    Subclasses KeyError so the HTTP layer's existing ``except KeyError`` → 404
    mapping is untouched, but ``str()`` is coaching rather than a repr'd id.
    The MCP path has no translation layer of its own, so the agent used to see
    the bare id — which says nothing about how to retry.
    """

    def __init__(self, expense_id: Any):
        self.expense_id = expense_id
        super().__init__(
            f"no expense with id {expense_id!r} — ids come from expenses_list; "
            "or target it by query=<a word from its description> instead"
        )

    def __str__(self) -> str:  # KeyError.__str__ repr()s args[0]
        return self.args[0]


_ALLOWED_UPDATE_FIELDS = frozenset(
    {"date", "amount", "currency", "category", "description", "submitted_by"}
)
_EXPENSE_COLS = (
    "id, date, amount, currency, category, description, "
    "paid, paid_date, submitted_by, created_at, updated_at"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Canonical category list. `category` stays free text in the database (the MCP
# can write anything), but these are what the portal offers and what analytics
# group by. Grouped for the phone dropdown; the group labels are UI-only.
# Mirrored in app/portal.html — CategoryParityTests fails if the two drift.
CATEGORIES = (
    ("living",        "生活费",       "Living"),
    ("aden-edu",      "Aden 教育",    "Aden · education"),
    ("aden-sports",   "Aden 运动",    "Aden · sports"),
    ("aden-clothes",  "Aden 衣服",    "Aden · clothes"),
    ("aden-other",    "Aden 其他",    "Aden · other"),
    ("food",          "食品",         "Food"),
    ("home",          "家居",         "Home"),
    ("utilities",     "水电",         "Utilities"),
    ("internet",      "网络",         "Internet"),
    ("mobile",        "手机",         "Mobile"),
    ("transport",     "交通",         "Transport"),
    ("travel",        "旅行",         "Travel"),
    ("entertainment", "娱乐",         "Entertainment"),
    ("clothes",       "衣服",         "Clothes"),
    ("medical",       "医疗",         "Medical"),
    ("borrow",        "我垫付",       "I paid (owed back to me)"),
    ("other",         "其他",         "Other"),
)
CATEGORY_KEYS = tuple(key for key, _zh, _en in CATEGORIES)

# 'borrow' is the one category with arithmetic attached: she fronted the money,
# so it is owed back to HER rather than being household spending. It is kept out
# of every expense total and reported on its own — repaying her must not read as
# the family having spent that money.
BORROW_CATEGORY = "borrow"
# category is nullable, and `category <> 'borrow'` is NULL (not true) for a NULL
# category, which would silently drop uncategorised rows from every total.
_NOT_BORROW = "COALESCE(category, '') <> 'borrow'"
_IS_BORROW = "COALESCE(category, '') = 'borrow'"


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
    except Exception as exc:
        # Falling back to UTC is the safe move for a live service — but it puts
        # a China household on the wrong day after 08:00 CST, so it must not be
        # silent. `tzdata` is a pinned runtime dependency precisely so this
        # branch stays unreachable; BacklogRegressionTests proves the zone data
        # is present and that APP_TZ is honoured.
        import sys

        print(
            f"WARNING: APP_TZ={tz_name!r} unusable ({exc!r}); dates fall back to UTC",
            file=sys.stderr,
        )
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# tolerated decoration around spoken/pasted amounts: ¥300, 300块, 1,200元, "300 rmb"
_AMOUNT_NOISE_RE = re.compile(r"[¥￥,，\s]|元|块|rmb|cny", re.IGNORECASE)

# LIKE metacharacters in a user's search string. Unescaped, query='%' matched
# every row in the ledger — an agent asked to find one expense got all of them.
_LIKE_SPECIALS_RE = re.compile(r"([\\%_])")


class Store:
    #: the household's only currency — see _validate_currency
    CURRENCY = "CNY"

    def __init__(self, db: Database):
        self.db = db

    # ── validation ────────────────────────────────────────────────────────
    # Error strings double as agent coaching: an LLM that calls wrongly reads
    # the message and self-corrects on the next call.
    @staticmethod
    def _validate_amount(amount: Any) -> float:
        original = amount
        if isinstance(amount, str):
            amount = _AMOUNT_NOISE_RE.sub("", amount)
        try:
            val = float(amount)
        except (TypeError, ValueError):
            raise ValidationError(
                f"amount {original!r} not understood — pass digits, e.g. 300, "
                "'¥300' or '300块' (Chinese numerals like 三百 must be converted "
                "to digits first)"
            )
        if not (val > 0):
            raise ValidationError(f"amount must be greater than 0, got {val}")
        return val

    @staticmethod
    def _validate_date(value: Any, field: str = "date") -> str:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValidationError(
                f"{field} is required — YYYY-MM-DD, or omit it to default to today"
            )
        if not _DATE_RE.match(text):
            raise ValidationError(
                f"{field} {text!r} invalid — use YYYY-MM-DD (e.g. 2026-07-14); "
                "convert relative words like 昨天/yesterday to a real date, or "
                "omit the field to default to today"
            )
        return text

    @classmethod
    def _validate_currency(cls, value: Any) -> str:
        """This ledger is CNY-only, and says so rather than quietly lying.

        `currency` has always been stored, and every total — summarize(), the
        portal cards, the charts — adds `amount` without ever consulting it. One
        non-CNY row would therefore make every monetary figure in the app
        silently wrong. Nothing can write one today (the portal has no currency
        field and the MCP exposes no parameter), so refusing is free; carrying
        an exchange rate would be a real feature this household has no use for.
        """
        text = (str(value).strip().upper() if value else "") or cls.CURRENCY
        if text != cls.CURRENCY:
            raise ValidationError(
                f"currency {text!r} not supported — this ledger is "
                f"{cls.CURRENCY} only. Every total adds amounts without "
                "converting them, so one foreign row would make all of them "
                f"wrong. Convert the amount to {cls.CURRENCY} first."
            )
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
        paid: bool = False, paid_date: Optional[str] = None,
    ) -> Expense:
        """Insert one expense, optionally already paid.

        ``paid`` exists so "昨天交了300的足球课" is a single transaction. The MCP
        used to create the row and then mark it paid in a second transaction: if
        the second failed, the row persisted as unpaid while the tool reported
        failure — the same-transaction history guarantee broken at the tool
        boundary. One row in, one ``create`` history entry describing it.
        """
        date = self._validate_date(date)
        amount = self._validate_amount(amount)
        currency = self._validate_currency(currency)
        paid = bool(paid)
        # an expense recorded as already-paid but with no date given was paid
        # when it came due — that is the only date the caller has actually told us
        paid_date = self._validate_date(paid_date or date, field="paid_date") if paid else None
        now = _utc_now_iso()
        expense = Expense(
            id=generate_id(), date=date, amount=amount, currency=currency,
            category=category, description=description, paid=paid,
            paid_date=paid_date, submitted_by=submitted_by,
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
            hint = (
                " — to mark paid/unpaid use mark_paid (expenses_mark_paid), not update"
                if {"paid", "paid_date"} & unknown else ""
            )
            raise ValidationError(f"unknown update fields: {sorted(unknown)}{hint}")
        clean: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "amount":
                clean[key] = self._validate_amount(value)
            elif key == "date":
                clean[key] = self._validate_date(value)
            elif key == "currency":
                clean[key] = self._validate_currency(value)
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
                raise NotFoundError(expense_id)
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
                raise NotFoundError(expense_id)
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
        self._status_clause(status, clauses, params)
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

    @staticmethod
    def _status_clause(status: Optional[str], clauses: list, params: dict) -> None:
        """Shared by list() and find(). They previously each implemented this,
        and drifted: find() silently treated 'overdue' — and any typo — as
        'all', so a query search could return paid and future rows."""
        if status == "paid":
            clauses.append("paid = :paid")
            params["paid"] = True
        elif status == "unpaid":
            clauses.append("paid = :paid")
            params["paid"] = False
        elif status == "overdue":
            clauses.append("paid = :paid")
            clauses.append("date < :today")
            params["paid"] = False
            params["today"] = today_str()
        elif status not in ("all", None, ""):
            raise ValidationError(
                f"invalid status filter: {status!r} — use all, paid, unpaid, "
                "or overdue (unpaid and past its due date)"
            )

    def find(self, query: str, *, status: str = "all") -> list["Expense"]:
        """Case-insensitive substring match on description/category.

        Powers natural-language targeting from the MCP ("the football class")
        so callers don't need ids.
        """
        # escape the caller's own %/_/\ so they match literally: query='%'
        # is someone looking for a percent sign, not for the whole ledger
        needle = "%" + _LIKE_SPECIALS_RE.sub(r"\\\1", str(query or "").strip().lower()) + "%"
        clauses = [
            r"(LOWER(COALESCE(description,'')) LIKE :q ESCAPE '\' "
            r"OR LOWER(COALESCE(category,'')) LIKE :q ESCAPE '\')"
        ]
        params: dict[str, Any] = {"q": needle}
        self._status_clause(status, clauses, params)
        with self.db.tx() as tx:
            rows = tx.query(
                f"SELECT {_EXPENSE_COLS} FROM expenses WHERE {' AND '.join(clauses)} "
                "ORDER BY date DESC, created_at DESC",
                params,
            )
        return [self._row_to_expense(row) for row in rows]

    UPCOMING_WINDOW_DAYS = 30

    @classmethod
    def summarize(
        cls, expenses: list[Expense], *, today: Optional[str] = None
    ) -> dict[str, Any]:
        """Totals for exactly the rows handed in — one code path, no second query.

        This exists because the aggregate MUST agree with the row set beside it.
        Computing the summary with its own SQL let a filtered list be rendered
        under a whole-ledger headline: ask "what's owed this month" and get two
        rows worth ¥5,780 beneath a ¥247,780 total. Deriving both from the same
        list makes that disagreement unrepresentable.

        Buckets (see also BORROW_CATEGORY):
          due_now   unpaid expense, due on or before today (includes overdue)
          upcoming  unpaid expense, due within the next UPCOMING_WINDOW_DAYS —
                    a window, not "everything future": recurring costs are
                    entered a year ahead, and a card summing all of them answers
                    a question nobody asked
          borrow_*  she fronted it; owed back to her, never household spending
        """
        today = today or today_str()
        horizon = (
            datetime.strptime(today, "%Y-%m-%d") + timedelta(days=cls.UPCOMING_WINDOW_DAYS)
        ).strftime("%Y-%m-%d")

        # collected per bucket, then fsum'd: plain += is order-dependent in
        # binary floating point, so a large or extreme ledger could land a cent
        # away from the SQL aggregate this replaced.
        buckets: dict[str, list] = {
            k: [] for k in ("total", "paid", "unpaid", "due_now", "upcoming",
                            "borrow_owed", "borrow_repaid")
        }
        counts = {k: 0 for k in ("unpaid_count", "due_now_count",
                                 "upcoming_count", "borrow_owed_count")}
        for e in expenses:
            amount = float(e.amount or 0)
            buckets["total"].append(amount)
            if (e.category or "") == BORROW_CATEGORY:
                if e.paid:
                    buckets["borrow_repaid"].append(amount)
                else:
                    buckets["borrow_owed"].append(amount)
                    counts["borrow_owed_count"] += 1
                continue
            if e.paid:
                buckets["paid"].append(amount)
                continue
            buckets["unpaid"].append(amount)
            counts["unpaid_count"] += 1
            if e.date <= today:
                buckets["due_now"].append(amount)
                counts["due_now_count"] += 1
            elif e.date <= horizon:
                buckets["upcoming"].append(amount)
                counts["upcoming_count"] += 1
        out: dict[str, Any] = {"count": len(expenses)}
        out.update({k: round(fsum(v), 2) for k, v in buckets.items()})
        out.update(counts)
        return out

    def summary(self, *, today: Optional[str] = None) -> dict[str, Any]:
        """Whole-ledger totals. Callers showing a FILTERED list must use
        summarize() on those rows instead, or the total contradicts the list."""
        return self.summarize(self.list(status="all"), today=today)

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
