"""Database layer — one portable interface over Postgres (prod) and sqlite
(tests/dev).

Driver selection (in order):
  * explicit ``url`` argument;
  * ``DATABASE_URL`` env var — ``postgres://``/``postgresql://`` → psycopg,
    ``sqlite:///path`` → sqlite;
  * fallback: local sqlite file ``family_expenses.db``.

SQL in the store is written once with ``:name`` parameters (sqlite's native
style) and translated to psycopg's ``%(name)s`` on the fly. The schema
(db/schema.sql) is portable DDL applied idempotently by :meth:`Database.init`.

Transactions: :meth:`Database.tx` yields a connection whose writes commit on
clean exit and roll back on any exception — the mechanism behind the
"expense write + history row are atomic" contract guarantee (M3).
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# :name → %(name)s. Negative lookbehind guards ``::`` casts (none in our SQL,
# but cheap insurance).
_PG_PARAM_RE = re.compile(r"(?<!:):([a-zA-Z_]\w*)")


def _to_pg(sql: str) -> str:
    return _PG_PARAM_RE.sub(r"%(\1)s", sql)


class Database:
    """Thin driver-agnostic wrapper. One instance per process."""

    def __init__(self, url: str | None = None):
        self.url = url or os.environ.get("DATABASE_URL") or "sqlite:///family_expenses.db"
        self.is_pg = self.url.startswith(("postgres://", "postgresql://"))
        self._local = threading.local()

    # ── connections ───────────────────────────────────────────────────────
    def _connect(self):
        if self.is_pg:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self.url, row_factory=dict_row)
        path = self.url[len("sqlite:///"):] if self.url.startswith("sqlite:///") else self.url
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ── transactions ──────────────────────────────────────────────────────
    @contextmanager
    def tx(self):
        """Yield a :class:`_Tx`; commit on success, roll back on exception."""
        conn = self._conn()
        try:
            yield _Tx(conn, self.is_pg)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    # ── schema ────────────────────────────────────────────────────────────
    def init(self, schema_path: Path | str = _SCHEMA_PATH) -> None:
        """Apply the idempotent schema (CREATE TABLE IF NOT EXISTS ...)."""
        script = Path(schema_path).read_text(encoding="utf-8")
        conn = self._conn()
        try:
            if self.is_pg:
                conn.execute(script)  # psycopg: multi-statement OK without params
            else:
                conn.executescript(script)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


class _Tx:
    """Cursor facade bound to one in-flight transaction."""

    def __init__(self, conn, is_pg: bool):
        self._conn = conn
        self._is_pg = is_pg

    def execute(self, sql: str, params: dict | None = None):
        if self._is_pg:
            sql = _to_pg(sql)
        return self._conn.execute(sql, params or {})

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        cur = self.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: dict | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None
