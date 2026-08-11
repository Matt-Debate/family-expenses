"""Web-tier tests — full HTTP path via Starlette TestClient, sqlite-backed."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.testclient import TestClient  # noqa: E402

from app.db import Database  # noqa: E402
from app.store import Store  # noqa: E402
from app.web import build_app  # noqa: E402


def make_client() -> tuple[TestClient, Store, str]:
    db = Database("sqlite:///:memory:")
    db.init()
    store = Store(db)
    token = store.mint_token(label="wife")["token"]
    return TestClient(build_app(store)), store, token


class PortalPageTests(unittest.TestCase):
    def setUp(self):
        self.client, self.store, self.token = make_client()

    def test_healthz(self):
        for path in ("/health", "/healthz"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.json(), {"ok": True})

    def test_favicon_request_is_quiet(self):
        self.assertEqual(self.client.get("/favicon.ico").status_code, 204)

    def test_portal_served_for_valid_token(self):
        r = self.client.get(f"/t/{self.token}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("家庭开支", r.text)

    def test_portal_rejects_bad_token(self):
        r = self.client.get("/t/nope")
        self.assertEqual(r.status_code, 404)
        self.assertIn("链接无效", r.text)

    def test_portal_rejects_revoked_token(self):
        self.store.revoke_token(self.token)
        self.assertEqual(self.client.get(f"/t/{self.token}").status_code, 404)


class ApiAuthTests(unittest.TestCase):
    def setUp(self):
        self.client, self.store, self.token = make_client()

    def test_all_endpoints_reject_missing_token(self):
        for name in ("list", "submit", "update", "mark-paid", "delete", "history"):
            r = self.client.post(f"/api/{name}", json={})
            self.assertEqual(r.status_code, 401, name)
            self.assertFalse(r.json()["ok"])

    def test_invalid_json_is_400(self):
        r = self.client.post(
            "/api/list", content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(r.status_code, 400)


class ApiFlowTests(unittest.TestCase):
    """A1 end-to-end: submit → list → edit → mark paid → history → delete."""

    def setUp(self):
        self.client, self.store, self.token = make_client()

    def post(self, name, **body):
        body["token"] = self.token
        return self.client.post(f"/api/{name}", json=body)

    def test_full_flow(self):
        r = self.post("submit", date="2026-07-14", amount=88.8,
                      description="小提琴课", submitted_by="Wei")
        self.assertEqual(r.status_code, 200)
        eid = r.json()["expense"]["id"]

        r = self.post("list")
        self.assertEqual(r.json()["summary"]["unpaid"], 88.8)
        self.assertEqual(len(r.json()["expenses"]), 1)

        r = self.post("update", id=eid, changed_by="Matt",
                      fields={"amount": 99.9, "category": "kids"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["expense"]["amount"], 99.9)

        r = self.post("mark-paid", id=eid, paid=True,
                      paid_date="2026-07-15", changed_by="Matt")
        self.assertTrue(r.json()["expense"]["paid"])

        r = self.post("list", status="unpaid")
        self.assertEqual(r.json()["expenses"], [])

        r = self.post("history", id=eid)
        self.assertEqual(
            [h["action"] for h in r.json()["history"]],
            ["create", "update", "mark_paid"],
        )

        r = self.post("delete", id=eid, changed_by="Matt")
        self.assertEqual(r.status_code, 200)
        r = self.post("list")
        self.assertEqual(r.json()["expenses"], [])

    def test_validation_maps_to_400(self):
        r = self.post("submit", date="2026-07-14", amount=-1)
        self.assertEqual(r.status_code, 400)
        r = self.post("submit", date="bad", amount=1)
        self.assertEqual(r.status_code, 400)
        r = self.post("mark-paid", id="whatever", paid=True)  # no paid_date
        self.assertEqual(r.status_code, 400)

    def test_missing_id_maps_to_404(self):
        for name, extra in (
            ("update", {"fields": {"amount": 1}}),
            ("mark-paid", {"paid": True, "paid_date": "2026-07-15"}),
            ("delete", {}),
        ):
            r = self.post(name, id="missing", **extra)
            self.assertEqual(r.status_code, 404, name)


class PortalEscapingTests(unittest.TestCase):
    """Regression guard for the stored-XSS fix in 0.4.5.

    render() builds list rows with innerHTML, so every server-supplied value has to
    pass through esc(). `category` is deliberately free-form end-to-end (MCP callers
    pass arbitrary strings and the ledger stores them verbatim), which makes the
    render-time escape the only control standing between a planted category and a
    stolen portal token.
    """

    PORTAL = Path(__file__).resolve().parent.parent / "app" / "portal.html"

    def setUp(self):
        self.html = self.PORTAL.read_text(encoding="utf-8")

    def test_every_use_of_catlabel_is_escaped_or_a_truthiness_guard(self):
        """Account for every `catLabel` occurrence; anything left over is a raw use.

        Deliberately not a `+ catLabel` adjacency check: the original bug read
        `esc(e.description) || catLabel || t("cat")`, where the raw use sits between
        `||` operators and no such pattern matches. Nor is `assertIn("esc(catLabel)")`
        sufficient — the escaped call already existed elsewhere in render() while the
        hole was open. Subtracting the known-safe forms is what actually discriminates.
        """
        offenders = []
        for lineno, line in enumerate(self.html.splitlines(), 1):
            if "catLabel" not in line:
                continue
            residue = line.replace("var catLabel", "")
            residue = residue.replace("esc(catLabel)", "")
            residue = re.sub(r"catLabel\s*\?", "", residue)  # truthiness guard only
            if "catLabel" in residue:
                offenders.append(f"  app/portal.html:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "catLabel reaches innerHTML unescaped — wrap it in esc():\n"
            + "\n".join(offenders),
        )

    def test_category_is_stored_verbatim_not_sanitized_on_write(self):
        """Escaping belongs at render, not on write.

        Mangling stored categories would corrupt the ledger and break MCP round-trips,
        so the API must keep the raw bytes and the portal must escape them.
        """
        client, _store, token = make_client()
        payload = "<img src=x onerror=alert(1)>"
        r = client.post("/api/submit", json={
            "token": token, "date": "2026-07-14", "amount": 5, "category": payload,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["expense"]["category"], payload)


class CategoryParityTests(unittest.TestCase):
    """The portal's category list and app/store.py CATEGORIES must not drift.

    They are two hand-maintained lists in two languages. If the portal offers a
    key analytics does not group by (or vice versa), spending quietly lands in a
    bucket nobody looks at — the kind of wrong that never raises an error.
    """

    PORTAL = Path(__file__).resolve().parent.parent / "app" / "portal.html"

    def test_portal_offers_exactly_the_canonical_categories(self):
        from app.store import CATEGORY_KEYS

        html = self.PORTAL.read_text(encoding="utf-8")
        block = re.search(r"var CATS = \[(.*?)\];", html, re.S)
        self.assertIsNotNone(block, "could not find the CATS array in portal.html")
        portal_keys = tuple(re.findall(r'"([^"]+)"', block.group(1)))
        self.assertEqual(portal_keys, CATEGORY_KEYS)

    def test_portal_labels_every_category_in_both_languages(self):
        from app.store import CATEGORY_KEYS

        html = self.PORTAL.read_text(encoding="utf-8")
        for lang in ("zh", "en"):
            start = html.index(f"{lang}: {{")
            cat = html.index("cat:{", start)
            labels = html[cat:html.index("act:{", cat)]
            for key in CATEGORY_KEYS:
                self.assertRegex(
                    labels, rf'"?{re.escape(key)}"?\s*:',
                    f"{lang} is missing a label for category {key!r}",
                )

    def test_portal_has_no_demo_backend(self):
        """A design pass arrived carrying the artifact's in-memory demo store,
        reachable via `if (!TOKEN) return demoApi(...)`. It never fired in
        production — the portal is only served at /t/<token> — but for a ledger,
        silently accepting writes into a fake is the worst failure available,
        so it must not come back on the next handoff.
        """
        html = self.PORTAL.read_text(encoding="utf-8")
        for marker in ("DEMO_EXP", "demoApi", "demoInit", "demoSummary"):
            self.assertNotIn(marker, html, f"demo scaffolding present: {marker}")

    def test_portal_always_talks_to_the_real_api(self):
        html = self.PORTAL.read_text(encoding="utf-8")
        self.assertIn('fetch("/api/"', html)
        self.assertNotRegex(html, r"if\s*\(\s*!TOKEN\s*\)\s*return")

    def test_borrow_is_the_category_with_arithmetic(self):
        """The portal must special-case exactly the key the store does."""
        from app.store import BORROW_CATEGORY

        html = self.PORTAL.read_text(encoding="utf-8")
        self.assertIn(f'var BORROW = "{BORROW_CATEGORY}"', html)




class AuthorIsAuthoritativeTests(unittest.TestCase):
    """Cross-model review, finding 3: _author() gave the client's value
    precedence, so a request bearing the 'wife' link could write any name into
    the audit trail. The link's label is the only author now."""

    def setUp(self):
        self.client, self.store, self.token = make_client()

    def test_client_cannot_choose_its_own_author(self):
        r = self.client.post("/api/submit", json={
            "token": self.token, "date": "2026-08-11", "amount": 10,
            "submitted_by": "Mallory",
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["expense"]["submitted_by"], "wife")

    def test_edits_are_attributed_to_the_link_too(self):
        eid = self.client.post("/api/submit", json={
            "token": self.token, "date": "2026-08-11", "amount": 10,
        }).json()["expense"]["id"]
        self.client.post("/api/update", json={
            "token": self.token, "id": eid, "changed_by": "Mallory",
            "fields": {"amount": 20},
        })
        actions = [(h.action, h.changed_by) for h in self.store.history(eid)]
        self.assertEqual(actions, [("create", "wife"), ("update", "wife")])

    def test_link_label_never_leaks_into_a_response(self):
        r = self.client.post("/api/list", json={"token": self.token})
        self.assertNotIn("_link_label", r.text)


class DocumentedCountsTests(unittest.TestCase):
    """The living docs quote a test count; it drifted four times in one session.

    Scoped to the three docs that carry the runnable command — CHANGELOG and
    FIRST_DEPLOY_PLAN quote historical counts on purpose and are left alone.
    """

    ROOT = Path(__file__).resolve().parent.parent
    LIVING = ("CLAUDE.md", "README.md", "docs/RUNBOOK.md")

    def actual_test_count(self) -> int:
        return sum(
            len(re.findall(r"^    def test_", p.read_text(encoding="utf-8"), re.M))
            for p in sorted((self.ROOT / "tests").glob("test_*.py"))
        )

    def test_documented_test_count_matches_reality(self):
        actual = self.actual_test_count()
        for name in self.LIVING:
            text = (self.ROOT / name).read_text(encoding="utf-8")
            # any "N tests" claim, not just the one in the run command —
            # a prose mention in the status section drifted the same day this
            # guard was written
            for quoted in re.findall(r"(\d+) tests", text):
                self.assertEqual(
                    int(quoted), actual,
                    f"{name} advertises {quoted} tests; the suite has {actual}",
                )

    def test_documented_tool_count_matches_the_server(self):
        mcp_src = (self.ROOT / "app" / "mcp_server.py").read_text(encoding="utf-8")
        actual = mcp_src.count("@mcp.tool")
        claude_md = (self.ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(f"{actual} tools", claude_md)


class ServerDecidesTodayTests(unittest.TestCase):
    """Backlog (closed in v0.9.0) — the portal read the device clock for every date
    decision while the server used APP_TZ, so the two could disagree."""

    def setUp(self):
        self.client, self.store, self.token = make_client()
        self.portal = (
            Path(__file__).resolve().parent.parent / "app" / "portal.html"
        ).read_text(encoding="utf-8")

    def test_list_response_carries_the_households_today(self):
        from app.store import today_str

        r = self.client.post("/api/list", json={"token": self.token})
        self.assertEqual(r.json()["today"], today_str())

    def test_list_today_follows_app_tz_not_the_server_clock(self):
        import os

        original = os.environ.get("APP_TZ")
        try:
            os.environ["APP_TZ"] = "Pacific/Kiritimati"
            east = self.client.post("/api/list", json={"token": self.token}).json()["today"]
            os.environ["APP_TZ"] = "Pacific/Niue"
            west = self.client.post("/api/list", json={"token": self.token}).json()["today"]
        finally:
            if original is None:
                os.environ.pop("APP_TZ", None)
            else:
                os.environ["APP_TZ"] = original
        self.assertNotEqual(east, west)

    def test_portal_prefers_the_server_date_over_the_device(self):
        self.assertIn("serverToday = j.today;", self.portal)
        # only one `new Date()` may survive — the first-paint fallback inside
        # todayStr(); every other date decision must route through todayStr()
        self.assertEqual(
            self.portal.count("new Date()"), 1,
            "a date decision is still reading the phone's clock directly",
        )

    def test_returning_to_a_backgrounded_tab_resyncs(self):
        self.assertIn('document.addEventListener("visibilitychange"', self.portal)

    def test_the_clock_is_read_exactly_once_per_list(self):
        """Read twice, a request straddling midnight buckets its rows against
        one day and labels them with the next. Counting the reads is the only
        way to pin this — both values look right in isolation."""
        from app import api as api_module
        from app import store as store_module

        calls = []

        def counting_today():
            calls.append(1)
            return "2026-08-11"

        api_real, store_real = api_module.today_str, store_module.today_str
        api_module.today_str = counting_today
        store_module.today_str = counting_today
        try:
            r = self.client.post("/api/list", json={"token": self.token})
        finally:
            api_module.today_str = api_real
            store_module.today_str = store_real
        self.assertEqual(r.json()["today"], "2026-08-11")
        self.assertEqual(len(calls), 1, f"clock read {len(calls)} times, expected 1")

    def test_history_shows_when_an_already_paid_row_was_paid(self):
        """Collapsing create+mark_paid into one entry hid the payment date the
        surviving row carries — the trail stopped saying when money moved."""
        self.assertIn("(snap.paid && snap.paid_date)", self.portal)
        self.assertIn("esc(snap.paid_date)", self.portal)  # P6: never interpolate raw


@unittest.skipUnless(shutil.which("node"), "node not available to run the portal's JS")
class PortalDateArithmeticTests(unittest.TestCase):
    """Executes the portal's own date functions instead of grepping for them.

    Every other guarantee about `portal.html` is pinned by a source-string
    assertion, which is how the first version of this feature shipped a
    regression: it cached the server's date forever, so a tab left open across
    midnight kept offering yesterday as the payment date — and an accepted
    default writes a wrong date into the ledger. A string check cannot observe
    time passing. This runs the real code.
    """

    PORTAL = Path(__file__).resolve().parent.parent / "app" / "portal.html"

    def date_fns(self) -> str:
        """addDays + todayStr, lifted verbatim from the page."""
        src = self.PORTAL.read_text(encoding="utf-8")
        start = src.index("  function addDays(ymd, n) {")
        end = src.index("  function daysBetween(")
        block = src[start:end]
        self.assertIn("function todayStr()", block, "todayStr moved; fix the slice")
        return block

    def run_js(self, setup: str, expr: str) -> str:
        script = (
            "var serverToday = null, serverTodayAt = 0, resyncing = false;\n"
            "var refresh = function () { throw new Error('unexpected refetch'); };\n"
            + self.date_fns()
            + "\n" + setup + "\nconsole.log(String(" + expr + "));\n"
        )
        out = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_the_date_advances_while_the_page_stays_open(self):
        """The regression this class exists for: 25 hours later must be the
        next day, not the day the page happened to load."""
        setup = (
            'serverToday = "2026-08-31"; serverTodayAt = 1000000;\n'
            "Date.now = function () { return 1000000 + 25 * 3600 * 1000; };"
        )
        self.assertEqual(self.run_js(setup, "todayStr()"), "2026-09-01")

    def test_the_date_holds_within_the_same_day(self):
        setup = (
            'serverToday = "2026-08-31"; serverTodayAt = 1000000;\n'
            "Date.now = function () { return 1000000 + 23 * 3600 * 1000; };"
        )
        self.assertEqual(self.run_js(setup, "todayStr()"), "2026-08-31")

    def test_a_forward_clock_jump_cannot_push_the_date_into_the_future(self):
        """Elapsed time is only a proxy for a date: an NTP correction or a
        manual clock change mid-session reads as time passing. Unbounded, that
        writes a payment date in a month that has not happened yet."""
        for jump_days in (3, 40, 400):
            with self.subTest(jump_days=jump_days):
                setup = (
                    'serverToday = "2026-08-31"; serverTodayAt = 0;\n'
                    "refresh = function () {};\n"
                    "Date.now = function () { return %d * 86400000 + 3600000; };"
                    % jump_days
                )
                self.assertEqual(self.run_js(setup, "todayStr()"), "2026-09-01")

    def test_exceeding_the_cap_refetches_exactly_once(self):
        """The clamp keeps the date safe; the refetch is what makes it correct
        again. Removing the refetch left the whole suite green, so it needed
        its own guard — and the one-shot latch is what stops a render storm."""
        setup = (
            'serverToday = "2026-08-31"; serverTodayAt = 0;\n'
            "var calls = 0; refresh = function () { calls++; };\n"
            "Date.now = function () { return 9 * 86400000; };\n"
            "todayStr(); todayStr(); todayStr();"
        )
        self.assertEqual(self.run_js(setup, "calls"), "1")

    def test_the_date_rolls_over_a_year_boundary(self):
        setup = (
            'serverToday = "2026-12-31"; serverTodayAt = 0;\n'
            "Date.now = function () { return 26 * 3600 * 1000; };"
        )
        self.assertEqual(self.run_js(setup, "todayStr()"), "2027-01-01")

    def test_add_days_is_dst_proof(self):
        """Parsed and formatted in UTC on purpose: a local-time round trip
        across a DST boundary lands on the wrong calendar day."""
        for tz in ("America/New_York", "Europe/London", "Asia/Shanghai"):
            with self.subTest(tz=tz):
                out = subprocess.run(
                    ["node", "-e",
                     "var serverToday=null, serverTodayAt=0;\n" + self.date_fns()
                     + '\nconsole.log(addDays("2026-03-07", 1), addDays("2026-11-01", 1));'],
                    capture_output=True, text=True, timeout=30, env={**os.environ, "TZ": tz},
                )
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout.strip(), "2026-03-08 2026-11-02")

    def test_without_a_server_date_it_falls_back_to_the_device(self):
        """`new Date()` does not route through `Date.now` in V8, so stubbing
        the clock proves nothing here — compare against the real device date."""
        import datetime

        out = self.run_js("", "todayStr()")
        self.assertEqual(out, datetime.date.today().strftime("%Y-%m-%d"))


class HandlersRunOffTheEventLoopTests(unittest.TestCase):
    """Backlog (closed in v0.9.0) — every API handler is synchronous and hits the
    database; awaiting them inline blocked the loop for the whole round trip
    to Neon, so one slow query stalled every other request in the process."""

    def test_blocking_store_calls_are_dispatched_to_a_thread(self):
        src = (
            Path(__file__).resolve().parent.parent / "app" / "web.py"
        ).read_text(encoding="utf-8")
        self.assertIn("await run_in_threadpool(handler, store, body)", src)
        self.assertIn("await run_in_threadpool(store.validate_token, token)", src)
        self.assertNotIn("status, payload = handler(store, body)", src)

    def test_the_api_still_works_through_the_threadpool(self):
        client, store, token = make_client()
        r = client.post("/api/submit", json={
            "token": token, "date": "2026-08-01", "amount": 42, "description": "水电",
        })
        self.assertEqual(r.status_code, 200, r.text)
        listed = client.post("/api/list", json={"token": token}).json()
        self.assertEqual(len(listed["expenses"]), 1)
        self.assertEqual(listed["summary"]["unpaid"], 42.0)


class ConstraintHardeningTests(unittest.TestCase):
    """Backlog (closed in v0.9.0) — the seq uniqueness constraint must apply, but must
    never be able to stop a live portal from starting."""

    def test_constraint_applies_on_a_fresh_database(self):
        from app.db import Database

        db = Database("sqlite:///:memory:")
        db.init()
        self.assertEqual(db._apply_hardening(), [], "constraint failed to apply")

    def test_unappliable_constraint_degrades_to_a_warning(self):
        """Data that already violates it must not take the service down."""
        import io
        from contextlib import redirect_stderr

        from app.db import Database
        from app.store import Store

        db = Database("sqlite:///:memory:")
        db.init()
        store = Store(db)
        exp = store.create(date="2026-08-01", amount=10)
        # forge the duplicate the constraint exists to prevent, behind its back
        with db.tx() as tx:
            tx.execute("DROP INDEX uq_expense_history_expense_seq")
            tx.execute(
                "INSERT INTO expense_history "
                "(id, expense_id, seq, action, changed_by, changed_at, snapshot) "
                "VALUES ('dup', :eid, 0, 'update', NULL, '2026-08-01T00:00:00', '{}')",
                {"eid": exp.id},
            )
        buf = io.StringIO()
        with redirect_stderr(buf):
            failed = db._apply_hardening()
        # name the statement, not just the count — otherwise this passes for
        # the wrong reason the moment hardening.sql gains a second entry
        self.assertEqual(len(failed), 1)
        self.assertIn("uq_expense_history_expense_seq", failed[0])
        self.assertIn("WARNING", buf.getvalue())
        # and the app still serves
        self.assertEqual(len(store.list()), 1)


class ClassTrackerApiTests(unittest.TestCase):
    """Tab 4 over the real HTTP path."""

    def setUp(self):
        self.client, self.store, self.token = make_client()

    def post(self, _endpoint, **body):
        # underscored: this feature's bodies carry a "name" field, which would
        # otherwise bind to the positional and raise TypeError
        body["token"] = self.token
        return self.client.post(f"/api/{_endpoint}", json=body)

    def a_payment(self, amount=2200, description="足球课"):
        return self.post("submit", date="2026-08-03", amount=amount,
                         description=description, category="aden-sports"
                         ).json()["expense"]["id"]

    def test_full_flow(self):
        eid = self.a_payment()
        r = self.post("classes-add", expense_id=eid, name="足球课",
                      kind="per_class", class_count=10, period_label="8月")
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["package"]["id"]

        r = self.post("classes-log", package_id=pid, kind="attended", date="2026-08-05")
        s = r.json()["package"]["summary"]
        self.assertEqual((s["remaining"], s["remaining_amount"]), (9, 1980.0))

        listed = self.post("classes-list").json()
        self.assertEqual(len(listed["packages"]), 1)
        self.assertTrue(listed["today"])

        event_id = listed["packages"][0]["events"][0]["id"]
        self.assertEqual(self.post("classes-unlog", event_id=event_id).status_code, 200)
        self.assertEqual(
            self.post("classes-list").json()["packages"][0]["summary"]["remaining"], 10
        )

        self.assertEqual(self.post("classes-delete", id=pid).status_code, 200)
        self.assertEqual(self.post("classes-list").json()["packages"], [])

    def test_candidates_exclude_payments_already_tracked(self):
        tracked = self.a_payment(description="足球课")
        free = self.a_payment(amount=99, description="游泳课")
        self.post("classes-add", expense_id=tracked, name="足球课",
                  kind="per_class", class_count=10)
        ids = [c["id"] for c in self.post("classes-list").json()["candidates"]]
        self.assertEqual(ids, [free], "a tracked payment must not be offered again")

    def test_portal_write_is_attributed_from_the_link_not_the_client(self):
        """Same rule as expenses: the label on the token wins (A8/P4)."""
        eid = self.a_payment()
        pid = self.post("classes-add", expense_id=eid, name="足球课",
                        kind="per_class", class_count=10).json()["package"]["id"]
        r = self.post("classes-log", package_id=pid, kind="attended",
                      logged_by="Mallory")
        self.assertEqual(r.json()["package"]["events"][0]["logged_by"], "wife")

    def test_errors_map_to_status_codes(self):
        self.assertEqual(
            self.post("classes-add", expense_id="nope", name="x",
                      kind="per_class", class_count=1).status_code, 404)
        eid = self.a_payment()
        self.assertEqual(
            self.post("classes-add", expense_id=eid, name="x",
                      kind="weekly", class_count=1).status_code, 400)
        self.assertEqual(self.post("classes-delete", id="nope").status_code, 404)
        self.assertEqual(self.post("classes-unlog", event_id="nope").status_code, 404)

    def test_deleting_a_tracked_payment_is_refused_with_a_reason(self):
        eid = self.a_payment()
        self.post("classes-add", expense_id=eid, name="足球课",
                  kind="per_class", class_count=10)
        r = self.post("delete", id=eid)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Remove that course first", r.json()["error"])


@unittest.skipUnless(shutil.which("node"), "node not available to run the portal's JS")
class ClassLineRenderingTests(unittest.TestCase):
    """Runs the portal's own `clsLine` instead of grepping for it.

    Mutation testing found six ways to corrupt this tab's money display that
    the whole suite let through — showing `owed_amount` where the reclaimable
    half belongs, rendering a period package through the per_class branch,
    dropping the cents. Every one lived in code no test executed.
    """

    PORTAL = Path(__file__).resolve().parent.parent / "app" / "portal.html"

    def cls_line(self, package: dict) -> dict:
        import json

        src = self.PORTAL.read_text(encoding="utf-8")
        start = src.index("  function clsLine(p) {")
        end = src.index("  function renderClasses() {")
        block = src[start:end]
        self.assertIn("function clsLine", block, "block markers moved")
        script = (
            # stubs: the labels are i18n keys, the money format is the portal's
            'function t(k) { return k; }\n'
            'function money(n) { return "¥" + Number(n).toFixed(2); }\n'
            + block
            + f"\nconsole.log(JSON.stringify(clsLine({json.dumps(package)})));\n"
        )
        out = subprocess.run(["node", "-e", script], capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_a_per_class_pack_shows_what_is_left(self):
        line = self.cls_line({"kind": "per_class", "summary": {
            "remaining": 7, "class_count": 10, "rate": 220.0, "used": 3,
            "overrun": 0, "remaining_amount": 1540.0,
        }})
        self.assertEqual(line["big"], "7/10")
        self.assertIn("¥1540.00", line["amt"])   # cents, not ¥1540 rounded
        self.assertIn("¥220.00", line["sub"])

    def test_a_period_package_shows_what_is_owed_and_the_split(self):
        line = self.cls_line({"kind": "period", "summary": {
            "owed": 3, "owed_amount": 750.0, "rate": 250.0,
            "reclaimable": 2, "reclaimable_amount": 500.0,
            "forfeited": 1, "forfeited_amount": 250.0,
        }})
        self.assertEqual(line["big"], "3")
        self.assertIn("¥750.00", line["amt"])
        # the two halves must be their own figures — swapping in owed_amount
        # here was a silent money corruption no test noticed
        self.assertIn("¥500.00", line["sub"])
        self.assertIn("¥250.00", line["sub"])
        self.assertNotIn("¥750.00", line["sub"])
        self.assertNotIn("undefined", line["sub"] + line["amt"] + line["big"])

    def test_the_two_kinds_do_not_render_through_each_other(self):
        """Inverting the branch made a period package print 'undefined/8'."""
        for package in (
            {"kind": "period", "summary": {
                "owed": 0, "owed_amount": 0.0, "rate": 250.0,
                "reclaimable": 0, "reclaimable_amount": 0.0,
                "forfeited": 0, "forfeited_amount": 0.0}},
            {"kind": "per_class", "summary": {
                "remaining": 0, "class_count": 4, "rate": 100.0, "used": 4,
                "overrun": 0, "remaining_amount": 0.0}},
        ):
            with self.subTest(kind=package["kind"]):
                line = self.cls_line(package)
                self.assertNotIn("undefined", "".join(line.values()))
                self.assertNotIn("NaN", "".join(line.values()))

    def test_cents_are_never_rounded_away(self):
        """The server says ¥666.67; whole-yuan rounding showed ¥667 on the tab
        whose job is telling a school what it owes."""
        line = self.cls_line({"kind": "period", "summary": {
            "owed": 2, "owed_amount": 666.67, "rate": 333.33,
            "reclaimable": 1, "reclaimable_amount": 333.33,
            "forfeited": 1, "forfeited_amount": 333.34,
        }})
        self.assertIn("¥666.67", line["amt"])
        self.assertIn("¥333.33", line["sub"])

    def test_an_overrun_is_surfaced_not_swallowed(self):
        line = self.cls_line({"kind": "per_class", "summary": {
            "remaining": 0, "class_count": 2, "rate": 100.0, "used": 3,
            "overrun": 1, "remaining_amount": 0.0,
        }})
        self.assertIn("cls_over", line["sub"])


class ClassKindParityTests(unittest.TestCase):
    """The portal hard-codes the kind strings the store validates against.

    They are two hand-maintained lists, exactly like the category keys — and a
    silent mismatch here means a button that always errors, or a package the
    portal renders as the wrong shape.
    """

    ROOT = Path(__file__).resolve().parent.parent

    def setUp(self):
        self.portal = (self.ROOT / "app" / "portal.html").read_text(encoding="utf-8")

    def test_package_kinds_match_the_store(self):
        from app.store import CLASS_KINDS

        for kind in CLASS_KINDS:
            self.assertIn(f'value="{kind}"', self.portal,
                          f"portal offers no option for package kind {kind!r}")

    def test_event_kinds_match_the_store(self):
        from app.store import CLASS_EVENT_KINDS

        for kind in CLASS_EVENT_KINDS:
            self.assertIn(f'data-c="{kind}"', self.portal,
                          f"portal has no button that logs {kind!r}")

    def test_both_languages_label_every_event_kind(self):
        """A bare `"{kind}:" in portal` check was satisfied by the Chinese
        table alone — deleting only the English label went unnoticed, as did
        replacing the whole map with a comment that happened to name the keys."""
        from app.store import CLASS_EVENT_KINDS

        blocks = re.findall(r"\bev:\s*\{(.*?)\}", self.portal, re.S)
        self.assertEqual(len(blocks), 2, "expected one ev: map per language")
        for lang, block in zip(("zh", "en"), blocks):
            for kind in CLASS_EVENT_KINDS:
                with self.subTest(lang=lang, kind=kind):
                    self.assertRegex(
                        block, rf"{kind}\s*:\s*[\"']",
                        f"{lang} has no label for event kind {kind!r}",
                    )

    def test_the_draw_down_button_belongs_to_per_class_packs_only(self):
        """Inverting this conditional took the '✓ Attended' button away from
        the packs that need it and gave it to period fees, where attending
        means nothing. The strings all still existed, so a grep passed."""
        block = self.portal[
            self.portal.index("  function renderClasses() {"):
            self.portal.index("  function clsEvents(p) {")
        ]
        self.assertRegex(
            block,
            r'p\.kind === "per_class"\s*\?\s*\'<button class="pay" data-c="attended"',
            "the attended button is no longer gated on a per_class package",
        )

    def test_the_expense_row_handler_ignores_class_rows(self):
        """Class rows reuse `.ex-hd`. A document-level handler bound to it ran
        `toggleItem(null)` and re-rendered, closing the row the class handler
        had just opened — tapping a course did nothing at all."""
        for match in re.finditer(r"toggleItem\(item\.getAttribute\(\"data-id\"\)\)",
                                 self.portal):
            line_start = self.portal.rfind("\n", 0, match.start())
            line = self.portal[line_start:match.end()]
            self.assertIn(
                'item.getAttribute("data-id")', line.split("toggleItem")[0],
                "toggleItem is called without first checking the row is an "
                "expense; class rows carry data-pkg and would pass null",
            )

    def test_hidden_row_controls_are_actually_hidden(self):
        """`.btns { display:flex }` is an author rule and out-ranks the UA
        sheet's `[hidden] { display:none }`, so every course row showed its
        action buttons — Delete included — whether open or not."""
        self.assertRegex(
            self.portal,
            r"\.btns\[hidden\][^{]*\{[^}]*display\s*:\s*none",
            "a hidden .btns will still render; add an explicit display:none",
        )

    def test_a_course_row_renders_its_class_log(self):
        """Dropping clsEvents(p) from the row removed the log and the only
        control that can take a mis-logged class back."""
        block = self.portal[
            self.portal.index("  function renderClasses() {"):
            self.portal.index("  function clsEvents(p) {")
        ]
        self.assertIn("clsEvents(p)", block)
        self.assertIn("data-unlog=", self.portal)

    def test_every_class_value_reaching_innerhtml_is_escaped(self):
        """P6/XSS: a course name is free text and the rows are built with
        innerHTML. Account for each interpolation of the package fields."""
        # Scoped to the class-tracker block: `e` means an *expense* everywhere
        # else in this file, so a whole-file scan only rediscovers those.
        start = self.portal.index("  function clsLine(p) {")
        end = self.portal.index("  function render() {")
        block = self.portal[start:end]
        self.assertIn("function renderClasses()", block, "block markers moved")
        base = self.portal[:start].count("\n")
        offenders = []
        for offset, line in enumerate(block.splitlines()):
            for expr in ("p.name", "p.period_label", "e.note", "e.logged_by",
                         "e.date", "p.id", "e.id", "e.description"):
                if expr not in line:
                    continue
                # subtract the known-safe forms, then see what is left — the
                # same shape as PortalEscapingTests, because `esc(x || y)` is
                # safe and a naive `esc(x)` match does not recognise it
                residue = line.replace(f"esc({expr}", "")
                # a property read or truthiness guard is not an interpolation
                residue = re.sub(re.escape(expr) + r"\s*(\?|\)|\.|,|;|=|$)", "", residue)
                if expr in residue:
                    offenders.append(
                        f"  app/portal.html:{base + offset + 1}: {line.strip()}"
                    )
        self.assertEqual(offenders, [], "unescaped class field in innerHTML:\n"
                         + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
