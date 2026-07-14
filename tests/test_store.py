"""Store test suite — runs entirely on sqlite (no DB server), stdlib-only.

Run:  python3 -m unittest discover -s tests -v   (or pytest)
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.store import Store, ValidationError  # noqa: E402


def make_store() -> Store:
    db = Database("sqlite:///:memory:")
    db.init()
    return Store(db)


class CreateAndReadTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store()

    def test_create_and_list(self):
        exp = self.store.create(
            date="2026-07-14", amount=125.5, description="课外书",
            category="Kids", submitted_by="Wei",
        )
        self.assertEqual(exp.currency, "CNY")
        self.assertFalse(exp.paid)
        listed = self.store.list()
        self.assertEqual([e.id for e in listed], [exp.id])
        self.assertEqual(listed[0].description, "课外书")

    def test_list_filters_and_order(self):
        a = self.store.create(date="2026-07-01", amount=10)
        b = self.store.create(date="2026-07-10", amount=20)
        self.store.mark_paid(a.id, paid=True, paid_date="2026-07-02")
        self.assertEqual([e.id for e in self.store.list()], [b.id, a.id])  # newest first
        self.assertEqual([e.id for e in self.store.list(status="paid")], [a.id])
        self.assertEqual([e.id for e in self.store.list(status="unpaid")], [b.id])
        self.assertEqual([e.id for e in self.store.list(since="2026-07-05")], [b.id])
        self.assertEqual([e.id for e in self.store.list(until="2026-07-05")], [a.id])
        with self.assertRaises(ValidationError):
            self.store.list(status="bogus")

    def test_summary(self):
        a = self.store.create(date="2026-07-01", amount=10.25)
        self.store.create(date="2026-07-02", amount=5.75)
        self.store.mark_paid(a.id, paid=True, paid_date="2026-07-03")
        s = self.store.summary()
        self.assertEqual(s, {
            "count": 2, "total": 16.0, "paid": 10.25,
            "unpaid": 5.75, "unpaid_count": 1,
        })


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store()

    def test_amount_must_be_positive_number(self):
        for bad in (0, -5, "abc", None):
            with self.assertRaises(ValidationError):
                self.store.create(date="2026-07-14", amount=bad)

    def test_date_format(self):
        for bad in ("", None, "14/07/2026", "2026-7-4"):
            with self.assertRaises(ValidationError):
                self.store.create(date=bad, amount=1)

    def test_update_rejects_unknown_fields(self):
        exp = self.store.create(date="2026-07-14", amount=1)
        with self.assertRaises(ValidationError):
            self.store.update(exp.id, fields={"paid": True})  # must use mark_paid
        with self.assertRaises(ValidationError):
            self.store.update(exp.id, fields={})

    def test_update_missing_id_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.store.update("nope", fields={"amount": 2})

    def test_mark_paid_requires_paid_date(self):
        exp = self.store.create(date="2026-07-14", amount=1)
        with self.assertRaises(ValidationError):
            self.store.mark_paid(exp.id, paid=True)  # no date
        got = self.store.mark_paid(exp.id, paid=True, paid_date="2026-07-15")
        self.assertTrue(got.paid)
        got = self.store.mark_paid(exp.id, paid=False)  # unmark clears date
        self.assertFalse(got.paid)
        self.assertIsNone(got.paid_date)

    def test_db_check_rejects_paid_without_date(self):
        # A4: the constraint holds even if application validation is bypassed.
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.db.tx() as tx:
                tx.execute(
                    "INSERT INTO expenses (id, date, amount, paid, created_at, updated_at) "
                    "VALUES ('x1', '2026-07-14', 1.0, TRUE, 'now', 'now')"
                )


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store()

    def test_every_mutation_writes_one_history_row(self):
        exp = self.store.create(date="2026-07-14", amount=9, submitted_by="Wei")
        self.store.update(exp.id, fields={"amount": 11}, changed_by="Matt")
        self.store.mark_paid(exp.id, paid=True, paid_date="2026-07-15", changed_by="Matt")
        self.store.mark_paid(exp.id, paid=False, changed_by="Wei")
        self.store.delete(exp.id, changed_by="Matt")
        actions = [h.action for h in self.store.history(exp.id)]
        self.assertEqual(actions, ["create", "update", "mark_paid", "unmark_paid", "delete"])

    def test_snapshots_capture_state(self):
        exp = self.store.create(date="2026-07-14", amount=9)
        self.store.update(exp.id, fields={"amount": 11})
        entries = self.store.history(exp.id)
        self.assertEqual(entries[0].snapshot["amount"], 9)
        self.assertEqual(entries[1].snapshot["amount"], 11)
        self.assertEqual(entries[0].changed_by, None)

    def test_delete_snapshot_is_pre_change_and_survives(self):
        exp = self.store.create(date="2026-07-14", amount=42, description="旧沙发")
        self.assertTrue(self.store.delete(exp.id))
        self.assertEqual(self.store.list(), [])
        entries = self.store.history(exp.id)
        self.assertEqual(entries[-1].action, "delete")
        self.assertEqual(entries[-1].snapshot["description"], "旧沙发")

    def test_delete_missing_returns_false_without_history(self):
        self.assertFalse(self.store.delete("nope"))
        self.assertEqual(self.store.history("nope"), [])


class AtomicityTests(unittest.TestCase):
    """M3: primary write and history row commit together or not at all."""

    def setUp(self):
        self.store = make_store()

    def test_history_failure_rolls_back_create(self):
        original = self.store._write_history

        def boom(*args, **kwargs):
            raise RuntimeError("forced history failure")

        self.store._write_history = boom
        with self.assertRaises(RuntimeError):
            self.store.create(date="2026-07-14", amount=5)
        self.store._write_history = original
        self.assertEqual(self.store.list(), [])          # expense rolled back
        self.assertEqual(self.store.summary()["count"], 0)

    def test_history_failure_rolls_back_update(self):
        exp = self.store.create(date="2026-07-14", amount=5)
        original = self.store._write_history
        self.store._write_history = lambda *a, **k: (_ for _ in ()).throw(RuntimeError)
        with self.assertRaises(RuntimeError):
            self.store.update(exp.id, fields={"amount": 99})
        self.store._write_history = original
        self.assertEqual(self.store.list()[0].amount, 5)  # value unchanged


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store()

    def test_mint_and_validate(self):
        minted = self.store.mint_token(label="wife", expires_days=30)
        self.assertEqual(len(minted["token"]), 64)
        row = self.store.validate_token(minted["token"])
        self.assertIsNotNone(row)
        self.assertEqual(row["label"], "wife")

    def test_default_mint_never_expires(self):
        minted = self.store.mint_token(label="wife")
        self.assertIsNone(minted["expires_at"])
        self.assertIsNotNone(self.store.validate_token(minted["token"]))

    def test_validate_bumps_usage(self):
        minted = self.store.mint_token()
        self.store.validate_token(minted["token"])
        self.store.validate_token(minted["token"])
        tokens = self.store.list_tokens()
        self.assertEqual(tokens[0]["use_count"], 2)
        self.assertIsNotNone(tokens[0]["last_used_at"])

    def test_unknown_and_garbage_tokens_rejected(self):
        self.assertIsNone(self.store.validate_token("deadbeef" * 8))
        self.assertIsNone(self.store.validate_token(""))
        self.assertIsNone(self.store.validate_token(None))
        self.assertIsNone(self.store.validate_token(123))

    def test_revoked_token_rejected(self):
        minted = self.store.mint_token()
        self.assertTrue(self.store.revoke_token(minted["token"]))
        self.assertIsNone(self.store.validate_token(minted["token"]))

    def test_expired_token_rejected(self):
        minted = self.store.mint_token(expires_days=1)
        with self.store.db.tx() as tx:  # force expiry into the past
            tx.execute(
                "UPDATE access_tokens SET expires_at = '2000-01-01T00:00:00' "
                "WHERE token = :t", {"t": minted["token"]},
            )
        self.assertIsNone(self.store.validate_token(minted["token"]))

    def test_expires_days_clamped(self):
        minted = self.store.mint_token(expires_days=99999)
        self.assertIsNotNone(minted["expires_at"])  # bounded when requested


class NaturalInputTests(unittest.TestCase):
    """Spoken/pasted forms the MCP path must tolerate."""

    def setUp(self):
        self.store = make_store()

    def test_amount_accepts_decorated_strings(self):
        for raw, expect in (("¥300", 300.0), ("300块", 300.0),
                            ("1,200元", 1200.0), (" 88.8 rmb ", 88.8)):
            exp = self.store.create(date="2026-07-14", amount=raw)
            self.assertEqual(exp.amount, expect)

    def test_amount_garbage_still_rejected(self):
        for bad in ("三百", "¥", ""):
            with self.assertRaises(ValidationError):
                self.store.create(date="2026-07-14", amount=bad)

    def test_find_matches_chinese_and_english(self):
        a = self.store.create(date="2026-07-01", amount=300, description="足球课")
        b = self.store.create(date="2026-07-02", amount=200, description="Piano lesson")
        self.store.create(date="2026-07-03", amount=50, category="food")
        self.assertEqual([e.id for e in self.store.find("足球")], [a.id])
        self.assertEqual([e.id for e in self.store.find("piano")], [b.id])  # case-insensitive
        self.assertEqual(len(self.store.find("课")), 1)
        self.assertEqual(len(self.store.find("food")), 1)  # category matches too
        self.assertEqual(self.store.find("nothing-like-this"), [])

    def test_find_respects_status_filter(self):
        a = self.store.create(date="2026-07-01", amount=300, description="足球课")
        self.store.create(date="2026-07-02", amount=300, description="足球装备")
        self.store.mark_paid(a.id, paid=True, paid_date="2026-07-02")
        self.assertEqual(len(self.store.find("足球")), 2)
        self.assertEqual(len(self.store.find("足球", status="unpaid")), 1)


if __name__ == "__main__":
    unittest.main()
