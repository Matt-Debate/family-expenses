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
    "expenses_list", "expenses_summary", "expenses_add", "expenses_mark_paid",
    "expenses_update", "expenses_delete", "expenses_history",
    "expenses_mint_link", "expenses_revoke_link",
}


def make_store() -> Store:
    db = Database("sqlite:///:memory:")
    db.init()
    return Store(db)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


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
        self.assertEqual([h["action"] for h in hist], ["create", "mark_paid"])

        summary = tool_payload(run(self.mcp.call_tool("expenses_summary", {})))
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
        self.assertEqual([h["action"] for h in hist], ["create", "delete"])

    def test_list_with_query_filter(self):
        self.call("expenses_add", amount="300", description="足球课")
        self.call("expenses_add", amount="50", description="水果")
        listed = self.call("expenses_list", query="足球")
        self.assertEqual(len(listed["expenses"]), 1)

    def test_mint_link_never_expires_by_default(self):
        minted = self.call("expenses_mint_link", label="wife")
        self.assertIsNone(minted["expires_at"])
        self.assertIsNotNone(self.store.validate_token(minted["token"]))


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


class CombinedAppTests(unittest.TestCase):
    """build_asgi_app wires MCP + portal into one service."""

    def test_portal_routes_present_on_combined_app(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        os.environ.pop("MCP_SECRET", None)
        try:
            from app.main import build_asgi_app
            # context manager runs the MCP session-manager lifespan
            with TestClient(build_asgi_app()) as client:
                self.assertEqual(client.get("/healthz").status_code, 200)
                self.assertEqual(client.get("/t/badtoken").status_code, 404)
                # MCP open when no secret configured: transport answers (405
                # for plain GET without SSE accept), not 401/503 gatekeeping.
                self.assertNotIn(client.get("/mcp").status_code, (401, 503))
        finally:
            os.environ.pop("DATABASE_URL", None)


if __name__ == "__main__":
    unittest.main()
