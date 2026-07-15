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
        configured_url = url or os.environ.get("DATABASE_URL")
        if os.environ.get("K_SERVICE"):
            if not configured_url:
                raise RuntimeError(
                    "DATABASE_URL is required on Cloud Run; refusing ephemeral SQLite fallback"
                )
            if not configured_url.startswith(("postgres://", "postgresql://")):
                raise RuntimeError(
                    "Cloud Run requires a Postgres DATABASE_URL; refusing SQLite storage"
                )
        self.url = configured_url or "sqlite:///family_expenses.db"
        self.is_pg = self.url.startswith(("postgres://", "postgresql://"))
        self._local = threading.local()  # Postgres: one connection per thread
        self._lock = threading.RLock()   # sqlite: one shared connection, serialized
        self._sqlite_conn: sqlite3.Connection | None = None

    # ── connections ───────────────────────────────────────────────────────
    def _connect(self):
        if self.is_pg:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self.url, row_factory=dict_row)
        path = self.url[len("sqlite:///"):] if self.url.startswith("sqlite:///") else self.url
        # Single shared connection: an in-memory sqlite DB is per-connection,
        # and web servers dispatch requests across threads — per-thread
        # connections would each see their own (empty) database. All sqlite
        # transactions are serialized by self._lock instead.
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _conn(self):
        if self.is_pg:
            conn = getattr(self._local, "conn", None)
            if conn is None or conn.closed:
                if conn is not None:
                    self._discard_pg_connection(conn)
                conn = self._connect()
                self._local.conn = conn
            return conn
        with self._lock:
            if self._sqlite_conn is None:
                self._sqlite_conn = self._connect()
            return self._sqlite_conn

    def _discard_pg_connection(self, conn) -> None:
        """Evict a failed thread-local connection without masking its error."""
        if getattr(self._local, "conn", None) is conn:
            self._local.conn = None
        try:
            conn.close()
        except Exception:
            pass

    def _replace_pg_connection(self, conn):
        self._discard_pg_connection(conn)
        fresh = self._connect()
        self._local.conn = fresh
        return fresh

    def close(self) -> None:
        if self.is_pg:
            conn = getattr(self._local, "conn", None)
            if conn is not None:
                conn.close()
                self._local.conn = None
            return
        with self._lock:
            if self._sqlite_conn is not None:
                self._sqlite_conn.close()
                self._sqlite_conn = None

    # ── transactions ──────────────────────────────────────────────────────
    @contextmanager
    def tx(self):
        """Yield a :class:`_Tx`; commit on success, roll back on exception.

        sqlite transactions hold the process-wide lock for their duration so
        concurrent request threads serialize instead of interleaving writes.
        """
        if self.is_pg:
            tx = _Tx(self._conn(), True, database=self)
            try:
                yield tx
                tx.connection.commit()
            except BaseException as exc:
                try:
                    tx.connection.rollback()
                except BaseException:
                    # A dead connection commonly raises again during rollback.
                    # Preserve the original error and ensure the next request
                    # cannot reuse the corpse.
                    self._discard_pg_connection(tx.connection)
                if _is_pg_connection_error(exc):
                    self._discard_pg_connection(tx.connection)
                raise
            return
        with self._lock:
            conn = self._conn()
            try:
                yield _Tx(conn, False)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    # ── schema ────────────────────────────────────────────────────────────
    def init(self, schema_path: Path | str = _SCHEMA_PATH) -> None:
        """Apply the idempotent schema (CREATE TABLE IF NOT EXISTS ...)."""
        script = Path(schema_path).read_text(encoding="utf-8")
        if self.is_pg:
            with self.tx() as tx:
                tx.execute(script)  # psycopg: multi-statement OK without params
        else:
            conn = self._conn()
            try:
                conn.executescript(script)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise


def _is_pg_connection_error(exc: BaseException) -> bool:
    import psycopg

    return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))


class _Tx:
    """Cursor facade bound to one in-flight transaction."""

    def __init__(self, conn, is_pg: bool, database: Database | None = None):
        self._conn = conn
        self._is_pg = is_pg
        self._database = database
        self._executed = False

    @property
    def connection(self):
        return self._conn

    def execute(self, sql: str, params: dict | None = None):
        if self._is_pg:
            sql = _to_pg(sql)
            try:
                result = self._conn.execute(sql, params or {})
            except BaseException as exc:
                if not _is_pg_connection_error(exc):
                    raise
                if self._executed or self._database is None:
                    if self._database is not None:
                        self._database._discard_pg_connection(self._conn)
                    raise
                # Long-idle pooler disconnects surface on the first statement.
                # No statement has succeeded, so replaying this one statement
                # once on a fresh connection is transaction-safe.
                self._conn = self._database._replace_pg_connection(self._conn)
                result = self._conn.execute(sql, params or {})
            self._executed = True
            return result
        return self._conn.execute(sql, params or {})

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        cur = self.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: dict | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None
