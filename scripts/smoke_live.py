#!/usr/bin/env python3
"""Post-deploy live smoke — run from your PC right after `gcloud run deploy`.

Proves the two things the sqlite test suite cannot:
  1. db/schema.sql applies cleanly to the real Neon Postgres;
  2. the deployed service works end-to-end over the public URL.

Usage:
  DATABASE_URL='postgres://…' python3 scripts/smoke_live.py \
      --base-url https://family-expenses-xxxx.a.run.app

Creates a temporary link + expense, exercises the full flow, then deletes the
expense and revokes the link. Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.store import Store  # noqa: E402

FAILED = False


def is_neon_pooled_url(url: str) -> bool:
    """Return whether a Postgres URL names Neon's pooled endpoint."""
    hostname = urlparse(url).hostname or ""
    return "-pooler." in hostname and hostname.endswith("neon.tech")


def exercise_pooled_token_gate(store, token: str, repetitions: int = 6) -> bool:
    """Cross psycopg's default prepare threshold on the runtime query shape."""
    if repetitions < 6:
        raise ValueError("pooled token gate requires at least six repetitions")
    return all(store.validate_token(token) is not None for _ in range(repetitions))


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILED
    print(f"  {'✓' if ok else '✗ FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED = True


def get(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.status, r.read().decode()


def post(base: str, name: str, **body):
    req = urllib.request.Request(
        f"{base}/api/{name}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _tool_payload(result):
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured.get("result", structured)
    return json.loads(result.content[0].text)


async def exercise_public_mcp(base: str) -> None:
    """Use the real MCP client stack for inventory and conversational flows."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    created_ids: list[str] = []
    async with streamablehttp_client(f"{base}/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            prompts = await session.list_prompts()
            expected_tools = {
                "expenses_help", "expenses_list", "expenses_add",
                "expenses_mark_paid", "expenses_update", "expenses_delete",
                "expenses_history", "expenses_mint_link", "expenses_revoke_link",
            }
            assert {tool.name for tool in tools.tools} == expected_tools
            assert {prompt.name for prompt in prompts.prompts} == {
                "jizhang", "duizhang", "xiufu",
            }
            try:
                chinese = _tool_payload(await session.call_tool("expenses_add", {
                    "amount": "¥13.57", "description": "[smoke-mcp] 足球课",
                    "submitted_by": "smoke-mcp",
                }))
                created_ids.append(chinese["id"])
                english = _tool_payload(await session.call_tool("expenses_add", {
                    "amount": "9.43 rmb", "description": "[smoke-mcp] football gear",
                    "submitted_by": "smoke-mcp",
                }))
                created_ids.append(english["id"])

                listed = _tool_payload(await session.call_tool(
                    "expenses_list", {"status": "unpaid", "query": "smoke-mcp"}
                ))
                assert {e["id"] for e in listed["expenses"]} == set(created_ids)

                ambiguous = _tool_payload(await session.call_tool(
                    "expenses_delete", {"query": "smoke-mcp"}
                ))
                assert ambiguous["matched"] == 2 and len(ambiguous["candidates"]) == 2

                corrected = _tool_payload(await session.call_tool(
                    "expenses_update", {"expense_id": chinese["id"], "amount": "14.25块"}
                ))
                assert corrected["amount"] == 14.25
                paid = _tool_payload(await session.call_tool(
                    "expenses_mark_paid", {"query": "足球课"}
                ))
                assert paid["id"] == chinese["id"] and paid["paid"]
                history = _tool_payload(await session.call_tool(
                    "expenses_history", {"expense_id": chinese["id"]}
                ))
                assert [h["action"] for h in history["history"]] == [
                    "create", "update", "mark_paid",
                ]
            finally:
                for expense_id in created_ids:
                    await session.call_tool(
                        "expenses_delete",
                        {"expense_id": expense_id, "changed_by": "smoke-mcp"},
                    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="deployed service URL")
    base = parser.parse_args().base_url.rstrip("/")

    print("1. Postgres schema (direct via DATABASE_URL)")
    store = Store(Database())  # requires DATABASE_URL env
    if not store.db.is_pg:
        print("  ✗ DATABASE_URL is not set to a postgres:// URL"); return 1
    if not is_neon_pooled_url(store.db.url):
        print("  ✗ DATABASE_URL must be the Neon pooled URI for the runtime gate")
        return 1
    store.db.init()  # idempotent — proves the portable DDL on real PG
    check("schema.sql applied idempotently to Postgres", True)

    minted = store.mint_token(label="smoke-test")
    token = minted["token"]
    check("temporary smoke link minted", len(token) == 64)
    check(
        "pooled token validation repeated 6x (prepared-statement threshold)",
        exercise_pooled_token_gate(store, token),
    )

    eid = None
    try:
        print("2. Deployed service over the public URL")
        status, _ = get(f"{base}/health")
        check("GET /health", status == 200)

        status, html = get(f"{base}/t/{token}")
        check("portal page serves for valid token", status == 200 and "家庭开支" in html)

        try:
            get(f"{base}/t/definitely-wrong-token")
            check("bad token rejected", False, "expected 404")
        except urllib.error.HTTPError as exc:
            check("bad token rejected (404)", exc.code == 404)

        print("3. Full expense flow (temporary data, cleaned up below)")
        out = post(base, "submit", token=token, date="2026-01-01",
                   amount=1.23, description="[smoke] delete me", submitted_by="smoke")
        eid = out["expense"]["id"]
        check("submit", out["ok"])

        out = post(base, "list", token=token, status="unpaid")
        check("list shows it", any(e["id"] == eid for e in out["expenses"]))

        out = post(base, "update", token=token, id=eid, changed_by="smoke",
                   fields={"amount": 2.34, "description": "[smoke] updated"})
        check("update", out["expense"]["amount"] == 2.34 and
              out["expense"]["description"] == "[smoke] updated")

        out = post(base, "mark-paid", token=token, id=eid,
                   paid=True, paid_date="2026-01-02", changed_by="smoke")
        check("mark paid", out["expense"]["paid"])

        out = post(base, "mark-paid", token=token, id=eid,
                   paid=False, changed_by="smoke")
        check("unmark paid", not out["expense"]["paid"] and
              out["expense"]["paid_date"] is None)

        out = post(base, "history", token=token, id=eid)
        actions = [h["action"] for h in out["history"]]
        check("history atomic trail", actions == [
            "create", "update", "mark_paid", "unmark_paid"
        ], str(actions))

        out = post(base, "delete", token=token, id=eid, changed_by="smoke")
        check("delete (cleanup)", out["ok"])
        eid = None

        status, _ = get(f"{base}/health")  # still alive after the workout
        check("service healthy after flow", status == 200)

        print("4. Public MCP client + bilingual conversational flow")
        try:
            asyncio.run(exercise_public_mcp(base))
            check("initialize, 9 tools, 3 prompts, bilingual writes/reads/cleanup", True)
        except Exception as exc:
            check("public MCP gate", False, f"{type(exc).__name__}: {exc}")
    finally:
        print("5. Cleanup")
        if eid:
            try:
                post(base, "delete", token=token, id=eid, changed_by="smoke")
            except Exception:
                print("  ! could not delete smoke expense — remove '[smoke]' row manually")
        for expense in store.find("smoke-mcp"):
            try:
                store.delete(expense.id, changed_by="smoke-cleanup")
            except Exception:
                print("  ! could not delete MCP smoke expense — remove it manually")
        check("smoke link revoked", store.revoke_token(token))

    print("\nRESULT:", "FAIL — see ✗ above" if FAILED else
          "PASS — deployment verified end-to-end. Mint the real link next "
          "(scripts/mint_link.py --label wife --base-url " + base + ")")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
