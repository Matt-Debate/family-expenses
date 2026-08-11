"""MCP-tier tests — tool registration/behavior in-process, bearer auth via HTTP."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.db import Database  # noqa: E402
from app.mcp_server import McpBearerMiddleware, build_mcp  # noqa: E402
from app.store import Store  # noqa: E402

EXPECTED_TOOLS = {
    "expenses_help", "expenses_list", "expenses_add", "expenses_mark_paid",
    "expenses_update", "expenses_delete", "expenses_history",
    "expenses_mint_link", "expenses_revoke_link", "expenses_list_links",
    "classes_list", "classes_add", "classes_log",
}

_TEST_LOOP = asyncio.new_event_loop()


def make_store() -> Store:
    db = Database("sqlite:///:memory:")
    db.init()
    return Store(db)


def run(coro):
    return _TEST_LOOP.run_until_complete(coro)


def tearDownModule():
    _TEST_LOOP.close()


def tool_payload(result) -> dict | list:
    """Extract the JSON payload from a FastMCP call_tool result."""
    content, structured = result
    if structured is not None:
        return structured.get("result", structured) if isinstance(structured, dict) else structured
    return json.loads(content[0].text)


class McpToolTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.mcp = build_mcp(self.store)

    def test_expected_tool_set(self):
        tools = run(self.mcp.list_tools())
        self.assertEqual({t.name for t in tools}, EXPECTED_TOOLS)

    def test_add_list_mark_paid_history(self):
        added = tool_payload(run(self.mcp.call_tool("expenses_add", {
            "date": "2026-07-14", "amount": "200", "description": "电费",
        })))
        eid = added["id"]

        listed = tool_payload(run(self.mcp.call_tool("expenses_list", {"status": "unpaid"})))
        self.assertEqual(len(listed["expenses"]), 1)
        self.assertEqual(listed["summary"]["unpaid"], 200.0)

        paid = tool_payload(run(self.mcp.call_tool("expenses_mark_paid", {
            "expense_id": eid, "paid": True, "paid_date": "2026-07-15",
        })))
        self.assertTrue(paid["paid"])

        hist = tool_payload(run(self.mcp.call_tool("expenses_history", {"expense_id": eid})))
        self.assertEqual([h["action"] for h in hist["history"]], ["create", "mark_paid"])

        summary = tool_payload(run(self.mcp.call_tool("expenses_list", {})))["summary"]
        self.assertEqual(summary["paid"], 200.0)

    def test_mint_and_revoke_link(self):
        minted = tool_payload(run(self.mcp.call_tool("expenses_mint_link", {
            "label": "wife", "expires_days": 365,
        })))
        self.assertEqual(len(minted["token"]), 64)
        self.assertIsNotNone(self.store.validate_token(minted["token"]))
        revoked = tool_payload(run(self.mcp.call_tool("expenses_revoke_link", {
            "token_or_id": minted["token"],
        })))
        self.assertTrue(revoked["revoked"])
        self.assertIsNone(self.store.validate_token(minted["token"]))

    def test_list_links_shows_ids_and_usage_but_never_tokens(self):
        minted = tool_payload(run(self.mcp.call_tool("expenses_mint_link", {
            "label": "wife",
        })))
        result = tool_payload(run(self.mcp.call_tool("expenses_list_links", {})))
        self.assertEqual(len(result["links"]), 1)
        link = result["links"][0]
        self.assertEqual(link["label"], "wife")
        self.assertEqual(link["status"], "active")
        self.assertEqual(link["expires_at"], "never")
        self.assertEqual(link["last_used_at"], "never opened")

        # The whole point: an id you can revoke with, and no token anywhere.
        self.assertEqual(link["id"], self.store.list_tokens()[0]["id"])
        self.assertNotIn(minted["token"], json.dumps(result))

    def test_list_links_id_round_trips_into_revoke(self):
        """The gap this tool closes: revoking without the operator holding a token."""
        minted = tool_payload(run(self.mcp.call_tool("expenses_mint_link", {
            "label": "wife",
        })))
        listed = tool_payload(run(self.mcp.call_tool("expenses_list_links", {})))
        revoked = tool_payload(run(self.mcp.call_tool("expenses_revoke_link", {
            "token_or_id": listed["links"][0]["id"],
        })))
        self.assertTrue(revoked["revoked"])
        self.assertIsNone(self.store.validate_token(minted["token"]))

    def test_list_links_hides_revoked_unless_asked(self):
        minted = tool_payload(run(self.mcp.call_tool("expenses_mint_link", {
            "label": "old-phone",
        })))
        run(self.mcp.call_tool("expenses_revoke_link", {"token_or_id": minted["token"]}))

        default = tool_payload(run(self.mcp.call_tool("expenses_list_links", {})))
        self.assertEqual(default["links"], [])

        widened = tool_payload(run(self.mcp.call_tool("expenses_list_links", {
            "include_revoked": True,
        })))
        self.assertEqual([x["status"] for x in widened["links"]], ["revoked"])

    def test_list_links_marks_expired_separately_from_revoked(self):
        self.store.mint_token(label="stale", expires_days=1)
        with self.store.db.tx() as tx:  # age it past expiry without touching revoked
            tx.execute("UPDATE access_tokens SET expires_at = :e",
                       {"e": "2000-01-01T00:00:00Z"})
        listed = tool_payload(run(self.mcp.call_tool("expenses_list_links", {})))
        self.assertEqual([x["status"] for x in listed["links"]], ["expired"])

    def test_validation_errors_propagate(self):
        from mcp.server.fastmcp.exceptions import ToolError
        with self.assertRaises(ToolError):
            run(self.mcp.call_tool("expenses_add", {"date": "2026-07-14", "amount": "-5"}))


class NaturalSpeechTests(unittest.TestCase):
    """'足球课付了' must work without ids, dates, or clean numbers."""

    def setUp(self):
        self.store = make_store()
        self.mcp = build_mcp(self.store)

    def call(self, tool, **args):
        return tool_payload(run(self.mcp.call_tool(tool, args)))

    def test_add_with_spoken_amount_and_no_date(self):
        added = self.call("expenses_add", amount="¥300", description="足球课")
        self.assertEqual(added["amount"], 300.0)
        self.assertRegex(added["date"], r"^\d{4}-\d{2}-\d{2}$")  # defaulted to today

    def test_mark_paid_by_query_defaults_today(self):
        self.call("expenses_add", amount="300", description="足球课")
        result = self.call("expenses_mark_paid", query="足球")
        self.assertTrue(result["paid"])
        self.assertRegex(result["paid_date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_mark_paid_prefers_the_unpaid_match(self):
        old = self.call("expenses_add", amount="300", description="足球课")
        self.call("expenses_mark_paid", expense_id=old["id"], paid_date="2026-07-01")
        self.call("expenses_add", amount="350", description="足球课")
        result = self.call("expenses_mark_paid", query="足球")  # two matches, one unpaid
        self.assertTrue(result["paid"])
        self.assertNotEqual(result["id"], old["id"])

    def test_ambiguous_query_returns_candidates(self):
        self.call("expenses_add", amount="300", description="足球课")
        self.call("expenses_add", amount="200", description="足球装备")
        result = self.call("expenses_delete", query="足球")
        self.assertEqual(result["matched"], 2)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertIn("expense_id", result["hint"])

    def test_no_match_returns_hint_not_error(self):
        result = self.call("expenses_mark_paid", query="不存在的东西")
        self.assertEqual(result["matched"], 0)
        self.assertIn("expenses_list", result["hint"])

    def test_update_by_query_with_spoken_amount(self):
        self.call("expenses_add", amount="300", description="钢琴课")
        result = self.call("expenses_update", query="钢琴", amount="350块")
        self.assertEqual(result["amount"], 350.0)

    def test_delete_by_query_keeps_history(self):
        added = self.call("expenses_add", amount="300", description="旧课程")
        result = self.call("expenses_delete", query="旧课程")
        self.assertTrue(result["deleted"])
        hist = self.call("expenses_history", expense_id=added["id"])
        self.assertEqual([h["action"] for h in hist["history"]], ["create", "delete"])

    def test_list_with_query_filter(self):
        self.call("expenses_add", amount="300", description="足球课")
        self.call("expenses_add", amount="50", description="水果")
        listed = self.call("expenses_list", query="足球")
        self.assertEqual(len(listed["expenses"]), 1)

    def test_mint_link_never_expires_by_default(self):
        minted = self.call("expenses_mint_link", label="wife")
        self.assertIsNone(minted["expires_at"])
        self.assertIsNotNone(self.store.validate_token(minted["token"]))


class AgentErgonomicsTests(unittest.TestCase):
    """The channels agents actually read: descriptions, results, errors,
    prompts, annotations (docs/MCP_DESIGN.md)."""

    def setUp(self):
        self.store = make_store()
        self.mcp = build_mcp(self.store)

    def call(self, tool, **args):
        return tool_payload(run(self.mcp.call_tool(tool, args)))

    def test_help_tool_returns_playbook(self):
        text = self.call("expenses_help")
        for anchor in ("expenses_mark_paid", "足球课", "today", "candidates"):
            self.assertIn(anchor, text)

    def test_descriptions_carry_bilingual_triggers(self):
        # Whitespace-normalized: docstrings wrap, and a trigger phrase split
        # across a line break still reads fine to the agent. Assert on meaning,
        # not on where the source happens to break.
        desc = {
            t.name: " ".join((t.description or "").split())
            for t in run(self.mcp.list_tools())
        }
        self.assertIn("付了", desc["expenses_mark_paid"])
        self.assertIn("paid", desc["expenses_mark_paid"])
        self.assertIn("300块", desc["expenses_add"])
        self.assertIn("expenses_update", desc["expenses_add"])  # cross-ref
        self.assertIn("expenses_mark_paid", desc["expenses_delete"])
        # link tools must point at each other, not at a CLI the agent cannot run
        self.assertIn("谁有链接", desc["expenses_list_links"])
        self.assertIn("list the links", desc["expenses_list_links"])
        self.assertIn("expenses_revoke_link", desc["expenses_list_links"])
        self.assertIn("expenses_mint_link", desc["expenses_list_links"])
        self.assertIn("expenses_list_links", desc["expenses_revoke_link"])
        self.assertNotIn("CLI", desc["expenses_revoke_link"])

    def test_help_lists_the_canonical_categories_and_flags_borrow(self):
        """The agent invented 'loan repayment' because nothing ever told it the
        keys existed. The list has to live where the agent actually reads."""
        from app.store import BORROW_CATEGORY, CATEGORY_KEYS

        text = self.call("expenses_help")
        for key in CATEGORY_KEYS:
            self.assertIn(key, text, f"category {key!r} missing from the playbook")
        self.assertIn("垫付", text)
        self.assertIn(BORROW_CATEGORY, text)

    def test_write_tools_point_at_the_category_keys(self):
        desc = {
            t.name: " ".join((t.description or "").split())
            for t in run(self.mcp.list_tools())
        }
        for tool in ("expenses_add", "expenses_update"):
            self.assertIn("borrow", desc[tool], tool)
            self.assertIn("expenses_help", desc[tool], tool)

    def test_offbook_category_is_coached_in_the_result(self):
        """Saves, but says so — silent mis-bucketing is the failure mode: the
        row looks fine and the money lands where nobody looks."""
        result = self.call("expenses_add", amount="100", category="loan repayment")
        self.assertEqual(result["category"], "loan repayment")  # stored verbatim
        self.assertIn("loan repayment", result["note"])
        self.assertIn("borrow", result["note"])

    def test_canonical_category_gets_no_warning(self):
        result = self.call("expenses_add", amount="100", category="borrow")
        self.assertNotIn("NOTE", result["note"])

    def test_category_note_survives_on_update_too(self):
        added = self.call("expenses_add", amount="100", description="office")
        out = self.call("expenses_update", expense_id=added["id"], category="reimbursement")
        self.assertIn("reimbursement", out["note"])

    def test_annotations_read_vs_destructive(self):
        tools = {t.name: t for t in run(self.mcp.list_tools())}
        self.assertTrue(tools["expenses_list"].annotations.readOnlyHint)
        self.assertTrue(tools["expenses_help"].annotations.readOnlyHint)
        self.assertTrue(tools["expenses_delete"].annotations.destructiveHint)
        self.assertFalse(tools["expenses_add"].annotations.readOnlyHint)
        self.assertTrue(tools["expenses_list_links"].annotations.readOnlyHint)
        self.assertTrue(tools["expenses_revoke_link"].annotations.destructiveHint)

    def test_personas_registered(self):
        prompts = {p.name for p in run(self.mcp.list_prompts())}
        self.assertEqual(prompts, {"jizhang", "duizhang", "xiufu"})

    def test_numeric_amount_accepted(self):
        # agents often pass numbers, not strings — must not be rejected
        added = self.call("expenses_add", amount=300, description="足球课")
        self.assertEqual(added["amount"], 300.0)

    def test_add_already_paid_in_one_call(self):
        """One call, and now one transaction: the row is inserted already paid.

        It used to insert then mark_paid, leaving two history rows and a window
        where a failed second write reported an error over a row that was in
        fact saved (unpaid). One 'create' entry describing the paid row is both
        atomic and a truer account of what happened.
        """
        added = self.call("expenses_add", amount="300", description="足球课",
                          paid=True, submitted_by="Wei")
        self.assertTrue(added["paid"])
        self.assertRegex(added["paid_date"], r"^\d{4}-\d{2}-\d{2}$")
        hist = self.call("expenses_history", expense_id=added["id"])
        self.assertEqual([h["action"] for h in hist["history"]], ["create"])
        self.assertTrue(hist["history"][0]["snapshot"]["paid"])

    def test_a_missing_id_coaches_the_agent_at_the_mcp_boundary(self):
        """docs/BACKLOG.md filed this as reaching *MCP callers* as a bare id.

        The store-level test proves the message exists; only this one proves it
        survives to the agent. The HTTP path deliberately discards it (404
        'expense not found'), so nothing else covers this boundary.
        """
        from mcp.server.fastmcp.exceptions import ToolError

        for tool, args in (
            ("expenses_update", {"expense_id": "nope", "amount": "5"}),
            ("expenses_mark_paid", {"expense_id": "nope"}),
        ):
            with self.assertRaises(ToolError) as ctx:
                run(self.mcp.call_tool(tool, args))
            message = str(ctx.exception)
            self.assertIn("nope", message)
            self.assertIn("expenses_list", message)   # where ids come from
            self.assertIn("query", message)           # the other way to target
            self.assertNotIn("KeyError", message)

    def test_class_tracker_speaks_in_whole_answers(self):
        """The note is the channel the agent reads back to the user, so it has
        to carry the answer — classes AND money — not just confirm the write."""
        self.call("expenses_add", amount="2200", description="足球课 8月")
        added = self.call("classes_add", name="足球课", class_count="10",
                          query="足球", period_label="8月")
        self.assertIn("¥220.00", added["note"])
        self.assertIn("classes_log", added["note"])

        logged = self.call("classes_log", kind="attended", query="足球")
        self.assertIn("9 of 10", logged["note"])
        self.assertIn("¥1980.00", logged["note"])

        listed = self.call("classes_list")
        self.assertIn("9/10", listed["note"])

    def test_period_package_note_names_what_is_reclaimable(self):
        self.call("expenses_add", amount="2000", description="游泳课 9月")
        package = self.call("classes_add", name="游泳课", class_count=8,
                            kind="period", query="游泳", period_label="9月")
        for kind in ("missed_school", "missed_school", "missed_us"):
            note = self.call("classes_log", kind=kind,
                             package_id=package["id"])["note"]
        self.assertIn("¥750.00", note)      # owed in total
        self.assertIn("¥500.00", note)      # the reclaimable half
        self.assertIn("reclaimable", note)

    def test_an_ambiguous_course_returns_candidates_rather_than_guessing(self):
        for month in ("8月", "9月"):
            self.call("expenses_add", amount="1000", description=f"足球课 {month}")
            self.call("classes_add", name="足球课", class_count=5,
                      query=month, period_label=month)
        result = self.call("classes_log", kind="attended", query="足球")
        self.assertEqual(result["matched"], 2)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertIn("package_id", result["candidates"][0])

    def test_a_course_can_be_targeted_by_its_period(self):
        """Two terms of the same course share a name — the month is the only
        thing that tells them apart, so dropping it from the matcher leaves the
        agent in an ambiguity loop it cannot exit."""
        for month in ("8月", "9月"):
            self.call("expenses_add", amount="1000", description=f"足球课 {month}")
            self.call("classes_add", name="足球课", class_count=5,
                      query=month, period_label=month)
        result = self.call("classes_log", kind="attended", query="9月")
        self.assertEqual(result["period_label"], "9月")

    def test_two_same_named_packs_are_told_apart_by_their_payment(self):
        """Since v0.10.1 the portal does not ask a per_class pack for a period
        label, so two terms of 足球课 created there are both called 足球课 with
        nothing else on them. Candidates carrying only name/period_label/kind
        would be three identical rows, and the disambiguation question the agent
        is told to ask the user would have no answer.
        """
        for month in ("8月", "9月"):
            self.call("expenses_add", amount="1000", description=f"足球课 {month}")
            self.call("classes_add", name="足球课", class_count=5, query=month)

        result = self.call("classes_log", kind="attended", query="足球课")
        self.assertEqual(result["matched"], 2)
        payments = [c["payment"] for c in result["candidates"]]
        self.assertEqual(len(set(payments)), 2, f"indistinguishable: {payments}")
        self.assertTrue(any("8月" in p for p in payments))

    def test_a_pack_with_no_period_label_is_reachable_by_its_payment(self):
        """'8月' has nowhere else to match once the portal stops asking for a
        period label — it lives in the payment, "足球课 8月"."""
        for month in ("8月", "9月"):
            self.call("expenses_add", amount="1000", description=f"足球课 {month}")
            self.call("classes_add", name="足球课", class_count=5, query=month)

        result = self.call("classes_log", kind="attended", query="9月")
        self.assertEqual(result["expense"]["description"], "足球课 9月")

    def test_two_packs_bought_in_one_sitting_are_still_distinguishable(self):
        """The normal MCP entry path: expenses_add defaults the date to today,
        so two terms bought in one sitting share a date, a description AND an
        amount. `payment` then prints identically for both, and the hint still
        tells the agent to ask the user to choose between two identical rows —
        which is the exact failure the payment field was added to fix.
        """
        paid = [self.call("expenses_add", amount="2200", description="足球课")["id"]
                for _ in range(2)]
        first = self.call("classes_add", name="足球课", class_count=10,
                          expense_id=paid[0])["id"]
        self.call("classes_log", kind="attended", package_id=first)
        self.call("classes_add", name="足球课", class_count=10, expense_id=paid[1])

        result = self.call("classes_log", kind="attended", query="足球课")
        self.assertEqual(result["matched"], 2)
        rows = [
            (c["payment"], c["classes_logged"], c["started"])
            for c in result["candidates"]
        ]
        self.assertEqual(len(set(rows)), 2, f"indistinguishable: {rows}")

    def test_the_summary_note_names_a_pack_that_has_no_period_label(self):
        """P3: the note is the channel the agent reads back to the owner. Two
        label-less packs both reading "足球课 (—)" answers 还剩几节课 with a
        figure the owner cannot attach to a course."""
        for month in ("8月", "9月"):
            self.call("expenses_add", amount="1000", description=f"足球课 {month}")
            self.call("classes_add", name="足球课", class_count=5, query=month)

        note = self.call("classes_list")["note"]
        self.assertNotIn("—", note, f"the note still has no handle on a pack: {note}")

    def test_the_matcher_asks_rather_than_guessing_when_a_payment_also_matches(self):
        """Matching the funding description widens what resolves, so a course
        can now be matched by another course's payment. That must surface as a
        question, never as a write to the wrong package."""
        self.call("expenses_add", amount="1000", description="游泳课 8月")
        self.call("classes_add", name="游泳", class_count=5, query="游泳课")
        self.call("expenses_add", amount="2000", description="8月课费（钢琴+游泳）")
        self.call("classes_add", name="钢琴", class_count=8, query="钢琴")

        result = self.call("classes_log", kind="attended", query="游泳")
        self.assertEqual(result["matched"], 2)
        self.assertNotIn("id", result, "it acted instead of asking")

    def test_an_archived_course_is_still_reachable_by_the_agent(self):
        """classes_list hides it by default; that must not make it impossible
        to correct a class logged against it."""
        self.call("expenses_add", amount="1000", description="足球课")
        package = self.call("classes_add", name="足球课", class_count=5, query="足球")
        self.store.update_package(package["id"], fields={"archived": True})
        logged = self.call("classes_log", kind="attended", query="足球")
        self.assertEqual(logged["id"], package["id"])

    def test_the_period_note_reports_both_counts_correctly(self):
        self.call("expenses_add", amount="1000", description="游泳课")
        package = self.call("classes_add", name="游泳课", class_count=10,
                            kind="period", query="游泳")
        self.call("classes_log", kind="missed_school", package_id=package["id"])
        for _ in range(2):
            note = self.call("classes_log", kind="missed_us",
                             package_id=package["id"])["note"]
        self.assertIn("1 cancelled by them", note)
        self.assertIn("2 skipped by us", note)

    def test_class_tools_coach_when_the_payment_is_missing(self):
        from mcp.server.fastmcp.exceptions import ToolError

        result = self.call("classes_add", name="足球课", class_count=5, query="足球")
        self.assertEqual(result["matched"], 0)
        self.assertIn("expenses_list", result["hint"])
        with self.assertRaises(ToolError) as ctx:
            run(self.mcp.call_tool("classes_log", {"kind": "nope", "query": "x"}))
        self.assertIn("missed_school", str(ctx.exception))

    def test_class_tool_descriptions_carry_bilingual_triggers(self):
        desc = {
            t.name: " ".join((t.description or "").split())
            for t in run(self.mcp.list_tools())
        }
        self.assertIn("还剩几节课", desc["classes_list"])
        self.assertIn("how many classes left", desc["classes_list"])
        self.assertIn("今天上了足球课", desc["classes_log"])
        # P3: a cross-reference is only guidance if it names something callable
        self.assertIn("classes_add", desc["classes_list"])
        self.assertIn("classes_log", desc["classes_list"])
        self.assertIn("expenses_add", desc["classes_add"])
        # the rate is derived, and the agent must not try to pass one
        self.assertIn("do not pass a rate", desc["classes_add"])

    def test_help_routes_class_questions(self):
        text = self.call("expenses_help")
        for anchor in ("classes_list", "classes_add", "classes_log",
                       "missed_school", "per_class"):
            self.assertIn(anchor, text)

    def test_write_results_carry_unpaid_total_note(self):
        added = self.call("expenses_add", amount="300", description="足球课")
        self.assertIn("unpaid total", added["note"])
        paid = self.call("expenses_mark_paid", expense_id=added["id"])
        self.assertIn("¥0.00", paid["note"])

    def test_error_strings_coach_the_agent(self):
        from mcp.server.fastmcp.exceptions import ToolError
        with self.assertRaises(ToolError) as ctx:
            run(self.mcp.call_tool("expenses_add", {"amount": "三百", "description": "x"}))
        self.assertIn("300块", str(ctx.exception))  # tells the agent what works
        with self.assertRaises(ToolError) as ctx:
            run(self.mcp.call_tool("expenses_add", {"amount": "300", "date": "昨天"}))
        self.assertIn("omit", str(ctx.exception))  # tells it dates can be omitted
        self.call("expenses_add", amount="300", description="足球课")
        with self.assertRaises(ToolError) as ctx:
            run(self.mcp.call_tool("expenses_update", {"query": "足球"}))
        self.assertIn("expenses_mark_paid", str(ctx.exception))  # redirects


class BearerMiddlewareTests(unittest.TestCase):
    """/mcp: open when MCP_SECRET unset (owner's accepted threat model),
    enforced when set. Other paths always untouched."""

    def make_client(self) -> TestClient:
        async def open_ok(request):
            return JSONResponse({"ok": True})

        inner = Starlette(routes=[
            Route("/healthz", open_ok, methods=["GET"]),
            Route("/mcp", open_ok, methods=["GET"]),
        ])
        return TestClient(McpBearerMiddleware(inner))

    def test_no_secret_means_open(self):
        os.environ.pop("MCP_SECRET", None)
        client = self.make_client()
        self.assertEqual(client.get("/mcp").status_code, 200)
        self.assertEqual(client.get("/healthz").status_code, 200)

    def test_secret_set_enforces_bearer(self):
        os.environ["MCP_SECRET"] = "s3cret"
        try:
            client = self.make_client()
            self.assertEqual(client.get("/mcp").status_code, 401)
            self.assertEqual(
                client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            self.assertEqual(
                client.get("/mcp", headers={"Authorization": "Bearer s3cret"}).status_code, 200)
            self.assertEqual(client.get("/healthz").status_code, 200)  # portal unaffected
        finally:
            os.environ.pop("MCP_SECRET", None)


class CompatibilityContractTests(unittest.TestCase):
    """FEATURE_CONTRACT §5.1 / A8: nothing may ever force a connected family
    member to reconnect. These pin the frozen surface — if one of these fails,
    the change would break her bookmark or connector."""

    def test_mcp_mount_path_is_frozen(self):
        self.assertEqual(McpBearerMiddleware(lambda s, r, w: None).prefix, "/mcp")

    def test_portal_path_shape_is_frozen(self):
        from app.web import build_routes
        paths = {r.path for r in build_routes(make_store())}
        self.assertIn("/t/{token}", paths)
        self.assertIn("/health", paths)
        self.assertIn("/healthz", paths)
        for name in ("list", "submit", "update", "mark-paid", "delete", "history"):
            self.assertIn(f"/api/{name}", paths)

    def test_default_posture_needs_no_credentials(self):
        # unset MCP_SECRET = open; minted tokens have no expiry
        os.environ.pop("MCP_SECRET", None)
        store = make_store()
        minted = store.mint_token(label="wife")
        self.assertIsNone(minted["expires_at"])

    def test_revocation_is_explicit_never_automatic(self):
        store = make_store()
        token = store.mint_token(label="wife")["token"]
        for _ in range(50):  # heavy use must never invalidate a link
            self.assertIsNotNone(store.validate_token(token))


class CombinedAppTests(unittest.TestCase):
    """build_asgi_app wires MCP + portal into one service."""

    def test_portal_routes_present_on_combined_app(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        os.environ.pop("MCP_SECRET", None)
        try:
            from app.main import build_asgi_app
            # context manager runs the MCP session-manager lifespan
            with TestClient(build_asgi_app()) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/t/badtoken").status_code, 404)
                # MCP open when no secret configured: transport answers (405
                # for plain GET without SSE accept), not 401/503 gatekeeping.
                self.assertNotIn(client.get("/mcp").status_code, (401, 503))
        finally:
            os.environ.pop("DATABASE_URL", None)

    def test_cloud_run_host_completes_initialize_and_tools_list(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        os.environ.pop("MCP_SECRET", None)
        try:
            from app.main import build_asgi_app

            headers = {
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "cloud-run-gate", "version": "1"},
                },
            }
            tools_list = {
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
            }
            initialized_notification = {
                "jsonrpc": "2.0", "method": "notifications/initialized", "params": {}
            }
            with TestClient(
                build_asgi_app(),
                base_url="https://family-expenses-test.asia-southeast1.run.app",
            ) as client:
                initialized = client.post("/mcp", headers=headers, json=initialize)
                notified = client.post(
                    "/mcp", headers=headers, json=initialized_notification
                )
                listed = client.post("/mcp", headers=headers, json=tools_list)

            self.assertEqual(initialized.status_code, 200, initialized.text)
            self.assertEqual(notified.status_code, 202, notified.text)
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertIn("serverInfo", initialized.json()["result"])
            self.assertEqual(
                {tool["name"] for tool in listed.json()["result"]["tools"]},
                EXPECTED_TOOLS,
            )
        finally:
            os.environ.pop("DATABASE_URL", None)


if __name__ == "__main__":
    unittest.main()
