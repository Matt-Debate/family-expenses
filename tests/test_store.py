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
        s = self.store.summary(today="2026-07-31")
        self.assertEqual(s, {
            "count": 2, "total": 16.0, "paid": 10.25,
            "unpaid": 5.75, "unpaid_count": 1,
            "due_now": 5.75, "due_now_count": 1,
            "upcoming": 0.0, "upcoming_count": 0,
            "borrow_owed": 0.0, "borrow_owed_count": 0, "borrow_repaid": 0.0,
        })

    def test_summary_splits_due_from_upcoming(self):
        """Recurring costs are entered months ahead; a bare unpaid total would
        report a year of rent as owed today."""
        self.store.create(date="2026-07-02", amount=245, category="utilities")
        self.store.create(date="2026-08-10", amount=22000, category="living")
        s = self.store.summary(today="2026-07-31")
        self.assertEqual(s["due_now"], 245.0)
        self.assertEqual(s["upcoming"], 22000.0)
        self.assertEqual(s["unpaid"], 22245.0)  # both, for anything wanting the whole

    def test_upcoming_is_a_window_not_everything_future(self):
        """A year of living payments loaded in advance must not pile into the
        'upcoming' card — it answers 'what is coming', not 'what exists'."""
        self.store.create(date="2026-08-05", amount=100, category="food")     # +5d
        self.store.create(date="2026-08-29", amount=200, category="food")     # +29d
        self.store.create(date="2026-09-30", amount=22000, category="living")  # +61d
        s = self.store.summary(today="2026-07-31")
        self.assertEqual(s["upcoming"], 300.0)
        self.assertEqual(s["upcoming_count"], 2)
        # still counted as owed overall — it is real money, just not imminent
        self.assertEqual(s["unpaid"], 22300.0)

    def test_summary_keeps_borrow_out_of_every_expense_figure(self):
        """She fronted the money — repaying her is not household spending."""
        self.store.create(date="2026-07-02", amount=800, category="borrow")
        repaid = self.store.create(date="2026-07-01", amount=500, category="borrow")
        self.store.mark_paid(repaid.id, paid=True, paid_date="2026-07-05")
        spent = self.store.create(date="2026-07-01", amount=300, category="aden-sports")
        self.store.mark_paid(spent.id, paid=True, paid_date="2026-07-01")
        s = self.store.summary(today="2026-07-31")
        self.assertEqual(s["paid"], 300.0)      # NOT 800
        self.assertEqual(s["unpaid"], 0.0)      # the outstanding borrow is not an expense
        self.assertEqual(s["due_now"], 0.0)
        self.assertEqual(s["borrow_owed"], 800.0)
        self.assertEqual(s["borrow_repaid"], 500.0)

    def test_summary_counts_uncategorised_rows(self):
        """`category <> 'borrow'` is NULL for a NULL category — a naive filter
        would silently drop every uncategorised expense from the totals."""
        self.store.create(date="2026-07-02", amount=42)  # no category at all
        s = self.store.summary(today="2026-07-31")
        self.assertEqual(s["unpaid"], 42.0)
        self.assertEqual(s["due_now"], 42.0)


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




class SummaryMatchesTheRowsTests(unittest.TestCase):
    """The aggregate must describe the rows beside it.

    A filtered list under a whole-ledger headline is a wrong number in the most
    visible place on the page: two rows worth ¥5,780 beneath a ¥247,780 total.
    """

    def setUp(self):
        self.store = make_store()
        self.store.create(date="2026-08-20", amount=3800, category="aden-edu")
        self.store.create(date="2026-08-20", amount=1980, category="aden-sports")
        for month in ("09", "10", "11"):
            self.store.create(date=f"2026-{month}-01", amount=22000, category="living")

    def test_summarize_describes_only_the_rows_given(self):
        rows = self.store.list(status="unpaid", since="2026-08-01", until="2026-08-31")
        s = self.store.summarize(rows, today="2026-08-11")
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["total"], 5780.0)
        self.assertEqual(s["unpaid"], 5780.0)
        self.assertEqual(s["unpaid_count"], 2)

    def test_whole_ledger_summary_still_sees_everything(self):
        s = self.store.summary(today="2026-08-11")
        self.assertEqual(s["count"], 5)
        self.assertEqual(s["unpaid"], 71780.0)

    def test_overdue_status_filter(self):
        self.store.create(date="2026-07-01", amount=500, category="utilities")
        overdue = self.store.list(status="overdue")
        self.assertEqual([e.date for e in overdue], ["2026-07-01"])

    def test_invalid_status_coaches_the_caller(self):
        with self.assertRaises(ValidationError) as ctx:
            self.store.list(status="nope")
        self.assertIn("overdue", str(ctx.exception))


class CodexVerifyRegressionTests(unittest.TestCase):
    """Findings from the cross-model review of v0.8.0 — each one a real defect
    the existing suite let through."""

    def setUp(self):
        self.store = make_store()

    def test_find_honours_overdue_like_list_does(self):
        """find() and list() each hand-rolled the status filter and drifted:
        find() silently treated 'overdue' as 'all', so a query search could
        return paid and future rows."""
        old = self.store.create(date="2026-01-01", amount=10, description="tennis")
        paid = self.store.create(date="2026-01-02", amount=20, description="tennis")
        self.store.mark_paid(paid.id, paid=True, paid_date="2026-01-03")
        self.store.create(date="2099-01-01", amount=30, description="tennis")
        found = self.store.find("tennis", status="overdue")
        self.assertEqual([e.id for e in found], [old.id])

    def test_find_rejects_an_unknown_status_instead_of_ignoring_it(self):
        with self.assertRaises(ValidationError):
            self.store.find("anything", status="bogus")

    def test_upcoming_horizon_boundary_is_exactly_30_days(self):
        """The decisive case the first pass never tested: day 30 in, day 31 out."""
        self.store.create(date="2026-08-30", amount=1, category="food")   # +30
        self.store.create(date="2026-08-31", amount=2, category="food")   # +31
        s = self.store.summary(today="2026-07-31")
        self.assertEqual(s["upcoming"], 1.0)
        self.assertEqual(s["upcoming_count"], 1)
        self.assertEqual(s["unpaid"], 3.0)  # the +31 row is still owed, just not imminent

    def test_totals_do_not_drift_with_row_order(self):
        """Plain += is order-dependent in binary floating point; fsum is not."""
        amounts = [0.1, 0.2, 1e9, 0.3, 1e-9, 4.44, 0.7]
        for a in amounts:
            self.store.create(date="2026-08-01", amount=a, category="food")
        forward = self.store.summarize(self.store.list(), today="2026-08-11")
        backward = self.store.summarize(list(reversed(self.store.list())), today="2026-08-11")
        self.assertEqual(forward["unpaid"], backward["unpaid"])
        self.assertEqual(forward["total"], backward["total"])


class BacklogRegressionTests(unittest.TestCase):
    """Items filed in docs/BACKLOG.md as known-and-deferred, now closed in v0.9.0.

    Each test was written against the unfixed code first and observed to fail;
    the entries in the backlog say what each defect actually costs.
    """

    def setUp(self):
        self.store = make_store()

    # §4 — find() built its LIKE pattern without escaping
    def test_like_wildcards_in_a_query_are_literal(self):
        """query='%' matched every row, so an agent searching for a literal
        percent sign got the whole ledger back and could act on the wrong one."""
        self.store.create(date="2026-08-01", amount=10, description="足球课")
        self.store.create(date="2026-08-02", amount=20, description="100% cotton")
        self.store.create(date="2026-08-03", amount=30, description="a_b test")
        # a row that genuinely contains a backslash: without it, find("\\")
        # returning [] is satisfied just as well by an escape clause that
        # matches nothing at all
        self.store.create(date="2026-08-04", amount=40, description=r"C:\receipts")
        self.assertEqual(
            [e.description for e in self.store.find("%")], ["100% cotton"]
        )
        self.assertEqual([e.description for e in self.store.find("_")], ["a_b test"])
        self.assertEqual(
            [e.description for e in self.store.find("\\")], [r"C:\receipts"]
        )
        self.assertEqual(
            [e.description for e in self.store.find(r"C:\re")], [r"C:\receipts"]
        )
        # the ordinary case must keep working
        self.assertEqual(len(self.store.find("足球")), 1)
        self.assertEqual(len(self.store.find("")), 4)  # empty query still matches all

    # §4 — a bare KeyError reached MCP callers as just the id
    def test_missing_expense_raises_a_coaching_error(self):
        for call in (
            lambda: self.store.update("nope", fields={"amount": 5}),
            lambda: self.store.mark_paid("nope", paid=True, paid_date="2026-08-01"),
        ):
            with self.assertRaises(KeyError) as ctx:  # api.py maps KeyError → 404
                call()
            message = str(ctx.exception)
            self.assertIn("nope", message)
            self.assertIn("query", message, "error must name the way to retry")
            self.assertNotEqual(message, repr("nope"), "bare KeyError repr leaked")

    # §4 — expenses_add(paid=True) spanned two transactions
    def test_creating_an_already_paid_expense_is_one_transaction(self):
        exp = self.store.create(
            date="2026-08-01", amount=300, description="足球课",
            paid=True, paid_date="2026-08-02", submitted_by="Wei",
        )
        self.assertTrue(exp.paid)
        self.assertEqual(exp.paid_date, "2026-08-02")
        history = self.store.history(exp.id)
        self.assertEqual([h.action for h in history], ["create"])
        self.assertTrue(history[0].snapshot["paid"])

    def test_created_paid_without_a_date_is_paid_today_not_when_due(self):
        """Defaulting to the DUE date would record a payment in the future for
        any bill entered ahead of time, filing it in the wrong month's total."""
        from app.store import today_str

        exp = self.store.create(date="2099-12-01", amount=5, paid=True)
        self.assertEqual(exp.paid_date, today_str())

    def test_only_the_store_decides_the_default_payment_date(self):
        """The MCP used to apply its own today-default on top of the store's,
        so the two could disagree and the store's rule was unreachable."""
        src = (
            Path(__file__).resolve().parent.parent / "app" / "mcp_server.py"
        ).read_text(encoding="utf-8")
        self.assertIn("paid=paid, paid_date=paid_date,", src)
        self.assertNotIn("(paid_date or today_str())", src)

    # §4 — expense_history.seq had no uniqueness constraint
    def test_history_seq_is_unique_per_expense(self):
        exp = self.store.create(date="2026-08-01", amount=10)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.db.tx() as tx:
                tx.execute(
                    "INSERT INTO expense_history "
                    "(id, expense_id, seq, action, changed_by, changed_at, snapshot) "
                    "VALUES ('dup', :eid, 0, 'update', NULL, '2026-08-01T00:00:00', '{}')",
                    {"eid": exp.id},
                )

    # §3 — currencies were summed without conversion
    def test_non_cny_currency_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.store.create(date="2026-08-01", amount=10, currency="USD")
        self.assertIn("CNY", str(ctx.exception))
        exp = self.store.create(date="2026-08-01", amount=10)
        with self.assertRaises(ValidationError):
            self.store.update(exp.id, fields={"currency": "usd"})

    def test_editing_other_fields_never_trips_the_currency_guard(self):
        exp = self.store.create(date="2026-08-01", amount=10, description="x")
        self.assertEqual(
            self.store.update(exp.id, fields={"description": "y"}).currency, "CNY"
        )
        self.assertEqual(self.store.update(exp.id, fields={"currency": "cny"}).currency, "CNY")

    # §4 — APP_TZ was unpinned by any test
    def test_today_str_follows_app_tz(self):
        """Every date assertion elsewhere is a shape-only regex, so a silent
        fallback to UTC — an image without tzdata — passed the whole suite.
        These two zones are 25 hours apart: they can never share a date."""
        import os

        from app.store import today_str

        original = os.environ.get("APP_TZ")
        try:
            os.environ["APP_TZ"] = "Pacific/Kiritimati"   # UTC+14
            east = today_str()
            os.environ["APP_TZ"] = "Pacific/Niue"         # UTC-11
            west = today_str()
        finally:
            if original is None:
                os.environ.pop("APP_TZ", None)
            else:
                os.environ["APP_TZ"] = original
        self.assertNotEqual(east, west, "APP_TZ is being ignored")

    def test_the_households_timezone_data_ships_with_the_image(self):
        from zoneinfo import ZoneInfo

        ZoneInfo("Asia/Shanghai")  # raises if tzdata is missing

    def test_the_default_timezone_is_the_households(self):
        """Nothing else pins the default: switching it to UTC left the suite
        green while putting the family a day behind after 08:00 CST."""
        src = (
            Path(__file__).resolve().parent.parent / "app" / "store.py"
        ).read_text(encoding="utf-8")
        self.assertIn('os.environ.get("APP_TZ", "Asia/Shanghai")', src)

    def test_an_unusable_timezone_falls_back_loudly(self):
        """The fallback is correct for a live service but must not be silent —
        a silent UTC fallback is exactly how this went unnoticed."""
        import io
        import os
        from contextlib import redirect_stderr

        from app.store import today_str

        original = os.environ.get("APP_TZ")
        buf = io.StringIO()
        try:
            os.environ["APP_TZ"] = "Not/AZone"
            with redirect_stderr(buf):
                fallback = today_str()
        finally:
            if original is None:
                os.environ.pop("APP_TZ", None)
            else:
                os.environ["APP_TZ"] = original
        self.assertRegex(fallback, r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("Not/AZone", buf.getvalue())


class ClassTrackerTests(unittest.TestCase):
    """The class tracker holds no money of its own — every figure is derived
    from the linked payment, so the ledger and the tracker cannot disagree."""

    def setUp(self):
        self.store = make_store()

    def pack(self, *, amount=2200, count=10, kind="per_class", label="8月"):
        expense = self.store.create(
            date="2026-08-03", amount=amount, description="足球课",
            category="aden-sports",
        )
        package = self.store.create_package(
            expense_id=expense.id, name="足球课", kind=kind,
            class_count=count, period_label=label,
        )
        return expense, package

    # ── (a) per-class packs draw down ────────────────────────────────────
    def test_per_class_pack_reports_classes_and_money_remaining(self):
        _expense, package = self.pack()
        self.assertEqual(package["summary"]["rate"], 220.0)
        self.assertEqual(package["summary"]["remaining"], 10)
        self.assertEqual(package["summary"]["remaining_amount"], 2200.0)
        for day in ("2026-08-05", "2026-08-12", "2026-08-19"):
            package = self.store.log_class(
                package_id=package["id"], kind="attended", date=day
            )
        s = package["summary"]
        self.assertEqual((s["used"], s["remaining"]), (3, 7))
        self.assertEqual(s["remaining_amount"], 1540.0)
        self.assertEqual(s["used_amount"], 660.0)

    def test_attending_more_than_were_bought_is_reported_not_hidden(self):
        _expense, package = self.pack(count=2)
        for _ in range(3):
            package = self.store.log_class(package_id=package["id"], kind="attended")
        s = package["summary"]
        self.assertEqual(s["remaining"], 0)          # never negative
        self.assertEqual(s["remaining_amount"], 0.0)
        self.assertEqual(s["overrun"], 1)            # but the fact survives

    def test_a_missed_class_does_not_consume_a_prepaid_credit(self):
        _expense, package = self.pack()
        package = self.store.log_class(package_id=package["id"], kind="missed_school")
        self.assertEqual(package["summary"]["remaining"], 10)

    # ── (b) period fees owe back what did not happen ─────────────────────
    def test_period_package_splits_owed_into_reclaimable_and_forfeited(self):
        """The example from the spec: ¥2,000 for 8 classes, 3 missed."""
        _expense, package = self.pack(amount=2000, count=8, kind="period", label="9月")
        for kind in ("missed_school", "missed_school", "missed_us"):
            package = self.store.log_class(
                package_id=package["id"], kind=kind, date="2026-09-05"
            )
        s = package["summary"]
        self.assertEqual(s["rate"], 250.0)
        self.assertEqual((s["owed"], s["owed_amount"]), (3, 750.0))
        self.assertEqual((s["reclaimable"], s["reclaimable_amount"]), (2, 500.0))
        self.assertEqual((s["forfeited"], s["forfeited_amount"]), (1, 250.0))
        # owed is the whole of it; the split is only for the argument
        self.assertEqual(s["reclaimable_amount"] + s["forfeited_amount"], s["owed_amount"])

    # ── money correctness ────────────────────────────────────────────────
    def test_amounts_are_exact_ratios_not_a_rounded_rate_times_n(self):
        """¥1,000 over 3 classes: a rounded ¥333.33 rate would lose a cent per
        class and the parts would stop adding up to the payment."""
        _expense, package = self.pack(amount=1000, count=3)
        self.assertEqual(package["summary"]["rate"], 333.33)      # display only
        self.assertEqual(package["summary"]["remaining_amount"], 1000.0)
        package = self.store.log_class(package_id=package["id"], kind="attended")
        s = package["summary"]
        self.assertEqual(s["used_amount"] + s["remaining_amount"], 1000.0)

    def test_the_rate_follows_the_payment_because_it_is_never_stored(self):
        expense, package = self.pack(amount=2200, count=10)
        self.assertEqual(package["summary"]["rate"], 220.0)
        self.store.update(expense.id, fields={"amount": 2500})  # she was told wrong
        self.assertEqual(self.store.package(package["id"])["summary"]["rate"], 250.0)

    def test_one_payment_can_only_fund_one_package(self):
        expense, _package = self.pack()
        with self.assertRaises(ValidationError) as ctx:
            self.store.create_package(
                expense_id=expense.id, name="again", kind="per_class", class_count=5
            )
        self.assertIn("already tracked", str(ctx.exception))

    def test_a_tracked_payment_cannot_be_deleted_out_from_under_its_package(self):
        expense, package = self.pack()
        self.store.log_class(package_id=package["id"], kind="attended")
        with self.assertRaises(ValidationError) as ctx:
            self.store.delete(expense.id)
        self.assertIn("delete that package first", str(ctx.exception))
        self.assertEqual(len(self.store.list()), 1)          # nothing was removed
        self.assertEqual(len(self.store.package(package["id"])["events"]), 1)
        # and it works once the package is gone
        self.assertTrue(self.store.delete_package(package["id"]))
        self.assertTrue(self.store.delete(expense.id))

    def test_the_class_tracker_never_moves_an_expense_total(self):
        """P4: Store.summarize is the one totals implementation and knows
        nothing about classes. Consumption is not spending."""
        _expense, package = self.pack()
        before = self.store.summary(today="2026-08-11")
        for _ in range(4):
            self.store.log_class(package_id=package["id"], kind="attended")
        self.assertEqual(self.store.summary(today="2026-08-11"), before)

    def test_deleting_a_package_takes_its_class_log_with_it(self):
        _expense, package = self.pack()
        self.store.log_class(package_id=package["id"], kind="attended")
        self.assertTrue(self.store.delete_package(package["id"]))
        with self.store.db.tx() as tx:
            left = tx.query("SELECT id FROM class_events WHERE package_id = :p",
                            {"p": package["id"]})
        self.assertEqual(left, [])

    def test_one_logged_class_can_be_taken_back(self):
        _expense, package = self.pack()
        package = self.store.log_class(package_id=package["id"], kind="attended")
        event_id = package["events"][0]["id"]
        self.assertTrue(self.store.delete_class_event(event_id))
        self.assertEqual(self.store.package(package["id"])["summary"]["used"], 0)
        self.assertFalse(self.store.delete_class_event(event_id))  # already gone

    # ── validation, all of it coaching ───────────────────────────────────
    def test_validation_rejects_and_explains(self):
        expense = self.store.create(date="2026-08-03", amount=100)
        for fields, expected in (
            ({"kind": "monthly"}, "per_class"),
            ({"class_count": 0}, "at least 1"),
            ({"class_count": "ten"}, "whole number"),
            ({"name": "  "}, "name is required"),
        ):
            args = dict(expense_id=expense.id, name="足球课",
                        kind="per_class", class_count=10)
            args.update(fields)
            with self.assertRaises(ValidationError) as ctx:
                self.store.create_package(**args)
            self.assertIn(expected, str(ctx.exception))

    def test_a_package_needs_a_payment_that_exists(self):
        with self.assertRaises(KeyError) as ctx:
            self.store.create_package(
                expense_id="nope", name="足球课", kind="per_class", class_count=4
            )
        self.assertIn("expenses_list", str(ctx.exception))

    def test_logging_rejects_an_unknown_event_kind(self):
        _expense, package = self.pack()
        with self.assertRaises(ValidationError) as ctx:
            self.store.log_class(package_id=package["id"], kind="cancelled")
        self.assertIn("missed_school", str(ctx.exception))

    def test_the_price_cannot_be_edited_on_the_package(self):
        """The money lives on the expense; two places to edit it is two places
        for it to be wrong."""
        _expense, package = self.pack()
        with self.assertRaises(ValidationError) as ctx:
            self.store.update_package(package["id"], fields={"amount": 999})
        self.assertIn("lives on the linked expense", str(ctx.exception))

    def test_logging_defaults_to_today(self):
        from app.store import today_str

        _expense, package = self.pack()
        package = self.store.log_class(package_id=package["id"], kind="attended")
        self.assertEqual(package["events"][0]["date"], today_str())

    def test_archived_packages_are_hidden_unless_asked_for(self):
        _expense, package = self.pack()
        self.store.update_package(package["id"], fields={"archived": True})
        self.assertEqual(self.store.list_packages(), [])
        self.assertEqual(len(self.store.list_packages(include_archived=True)), 1)


if __name__ == "__main__":
    unittest.main()
