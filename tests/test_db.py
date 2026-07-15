"""Database connection lifecycle and production-posture regression tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import psycopg

from app.db import Database


class _Cursor:
    def fetchall(self):
        return []


class _FakePgConnection:
    def __init__(self, *, closed: bool = False, fail_on_execute: set[int] | None = None,
                 rollback_fails: bool = False):
        self.closed = closed
        self.fail_on_execute = set(fail_on_execute or set())
        self.rollback_fails = rollback_fails
        self.execute_count = 0
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=None):
        self.execute_count += 1
        if self.execute_count in self.fail_on_execute:
            raise psycopg.OperationalError("simulated stale connection")
        self.statements.append(sql)
        return _Cursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        if self.rollback_fails:
            raise psycopg.OperationalError("simulated rollback on dead connection")

    def close(self):
        self.closed = True


class PostgresReconnectTests(unittest.TestCase):
    def _db_with_connections(self, *connections: _FakePgConnection) -> Database:
        db = Database("postgresql://example.invalid/family")
        queue = list(connections)
        db._connect = lambda: queue.pop(0)
        return db

    def test_closed_cached_connection_is_replaced_before_next_transaction(self):
        stale = _FakePgConnection(closed=True)
        fresh = _FakePgConnection()
        db = self._db_with_connections(fresh)
        db._local.conn = stale

        with db.tx() as tx:
            tx.execute("SELECT 1")

        self.assertIs(db._local.conn, fresh)
        self.assertEqual(fresh.statements, ["SELECT 1"])
        self.assertEqual(fresh.commits, 1)

    def test_first_statement_on_stale_connection_reconnects_and_retries_once(self):
        stale = _FakePgConnection(fail_on_execute={1})
        fresh = _FakePgConnection()
        db = self._db_with_connections(stale, fresh)

        with db.tx() as tx:
            tx.execute("SELECT token FROM access_tokens WHERE token = :token",
                       {"token": "abc"})

        self.assertIs(db._local.conn, fresh)
        self.assertEqual(fresh.execute_count, 1)
        self.assertEqual(fresh.commits, 1)

    def test_mid_transaction_failure_does_not_leave_poisoned_connection_cached(self):
        stale = _FakePgConnection(fail_on_execute={2}, rollback_fails=True)
        fresh = _FakePgConnection()
        db = self._db_with_connections(stale, fresh)

        with self.assertRaises(psycopg.OperationalError):
            with db.tx() as tx:
                tx.execute("SELECT 1")
                tx.execute("UPDATE expenses SET amount = 2")

        with db.tx() as tx:
            tx.execute("SELECT 2")
        self.assertIs(db._local.conn, fresh)
        self.assertEqual(fresh.statements, ["SELECT 2"])


class ProductionDatabasePostureTests(unittest.TestCase):
    def test_cloud_run_refuses_missing_database_url(self):
        with patch.dict(os.environ, {"K_SERVICE": "family-expenses"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                Database()

    def test_cloud_run_refuses_sqlite_database_url(self):
        env = {"K_SERVICE": "family-expenses", "DATABASE_URL": "sqlite:///bad.db"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Postgres"):
                Database()

    def test_cloud_run_accepts_postgres_database_url(self):
        env = {
            "K_SERVICE": "family-expenses",
            "DATABASE_URL": "postgresql://example.invalid/family",
        }
        with patch.dict(os.environ, env, clear=True):
            db = Database()
        self.assertTrue(db.is_pg)


if __name__ == "__main__":
    unittest.main()
