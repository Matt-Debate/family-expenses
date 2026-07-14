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
    "expenses_history", "expenses_mint_link", "expenses_revoke_link",
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
            "date": "2026-07-14", "amount": 200, "description": "电费",
        })))
        eid = added["id"]
        self.assertEqual(added["submitted_by"], "operator")

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
            run(self.mcp.call_tool("expenses_add", {"date": "2026-07-14", "amount": -5}))


class BearerMiddlewareTests(unittest.TestCase):
    """The /mcp mount is fail-closed; other paths are untouched."""

    def make_client(self) -> TestClient:
        async def open_ok(request):
            return JSONResponse({"ok": True})

        inner = Starlette(routes=[
            Route("/healthz", open_ok, methods=["GET"]),
            Route("/mcp", open_ok, methods=["GET"]),
        ])
        return TestClient(McpBearerMiddleware(inner))

    def test_no_secret_configured_fails_closed(self):
        os.environ.pop("MCP_SECRET", None)
        client = self.make_client()
        self.assertEqual(client.get("/mcp").status_code, 503)
        self.assertEqual(client.get("/healthz").status_code, 200)  # portal unaffected

    def test_wrong_or_missing_bearer_rejected(self):
        os.environ["MCP_SECRET"] = "s3cret"
        try:
            client = self.make_client()
            self.assertEqual(client.get("/mcp").status_code, 401)
            self.assertEqual(
                client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            self.assertEqual(
                client.get("/mcp", headers={"Authorization": "Bearer s3cret"}).status_code, 200)
        finally:
            os.environ.pop("MCP_SECRET", None)


class CombinedAppTests(unittest.TestCase):
    """build_asgi_app wires MCP + portal into one service."""

    def test_portal_routes_present_on_combined_app(self):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        try:
            from app.main import build_asgi_app
            client = TestClient(build_asgi_app())
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.get("/t/badtoken").status_code, 404)
            self.assertEqual(client.get("/mcp").status_code, 503)  # fail-closed
        finally:
            os.environ.pop("DATABASE_URL", None)


if __name__ == "__main__":
    unittest.main()
