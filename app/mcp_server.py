"""Operator/family MCP surface — FastMCP streamable-HTTP.

Designed for NATURAL SPEECH through an LLM client (Claude / ChatGPT), in
Chinese or English, by a non-technical user:

  * mutating tools accept a fuzzy ``query`` ("足球课", "football class")
    instead of an id — exactly one match acts; several matches return
    candidates so the model can ask which one;
  * dates are optional everywhere and default to *today in China time*
    (``APP_TZ``, default Asia/Shanghai);
  * amounts tolerate spoken forms: "¥300", "300块", "1,200元";
  * the server ``instructions`` coach the model on phrasing and defaults.

Auth (owner's threat model — accepted 2026-07-14): the ledger is
deliberately low-stakes; links never expire. ``/mcp`` enforces
``Authorization: Bearer $MCP_SECRET`` only when the env var is set —
unset = open. Set it if you ever want the gate back.
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .store import Store, ValidationError, today_str

_INSTRUCTIONS = """\
Household expense ledger (家庭开支). Amounts are CNY (¥). "unpaid"/待付
expenses are the ones still needing payment; that list is usually what the
user wants first.

The user may speak Chinese or English, casually. Typical requests and the
right calls:
- "我还要付什么 / what do I still need to pay" → expenses_list(status="unpaid")
- "足球课300块 / football class 300" → expenses_add(amount="300", description="足球课")
  (date omitted = today; keep the user's own words as the description)
- "足球课付了 / paid the football class" → expenses_mark_paid(query="足球课")
  (paid_date omitted = today)
- "足球课改成350 / change football to 350" → expenses_update(query="足球课", amount="350")
- "删掉足球课 / delete the football class" → expenses_delete(query="足球课")
- "这个月花了多少 / totals" → expenses_summary()

When a query matches several expenses the tool returns matched>1 with
candidates — show them briefly and ask which one; then call again with
expense_id. Confirm with the user before expenses_delete. Reply in the
language the user spoke.
"""


def build_mcp(store: Store) -> FastMCP:
    mcp = FastMCP(
        "family-expenses",
        instructions=_INSTRUCTIONS,
        stateless_http=True,  # Cloud Run scale-to-zero friendly
    )

    # ── helpers ───────────────────────────────────────────────────────────
    def _candidates(matches) -> list[dict[str, Any]]:
        return [
            {
                "expense_id": e.id, "description": e.description,
                "amount": e.amount, "date": e.date, "paid": e.paid,
                "category": e.category,
            }
            for e in matches[:8]
        ]

    def _resolve(
        expense_id: Optional[str], query: Optional[str], *, prefer_unpaid: bool
    ):
        """Return (expense_id, None) or (None, ambiguity-payload)."""
        if expense_id:
            return expense_id, None
        if not query or not str(query).strip():
            raise ValidationError("provide expense_id or a query to match")
        matches = store.find(query)
        if prefer_unpaid and len(matches) > 1:
            unpaid = [e for e in matches if not e.paid]
            if len(unpaid) == 1:
                return unpaid[0].id, None
        if len(matches) == 1:
            return matches[0].id, None
        if not matches:
            return None, {
                "matched": 0, "candidates": [],
                "hint": f"nothing matches {query!r}; call expenses_list to see everything",
            }
        return None, {
            "matched": len(matches), "candidates": _candidates(matches),
            "hint": "several matches — ask the user which one, then call again with expense_id",
        }

    # ── read tools ────────────────────────────────────────────────────────
    @mcp.tool()
    def expenses_list(
        status: str = "all",
        query: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> dict[str, Any]:
        """List expenses. status: all|paid|unpaid ('unpaid'=待付, usually what
        the user wants). Optional query filters by description/category text
        (Chinese or English). since/until: YYYY-MM-DD."""
        if query and str(query).strip():
            expenses = store.find(query, status=status)
        else:
            expenses = store.list(status=status, since=since, until=until)
        return {
            "expenses": [e.to_dict() for e in expenses],
            "summary": store.summary(),
        }

    @mcp.tool()
    def expenses_summary() -> dict[str, Any]:
        """Totals in CNY: count, total, paid, unpaid, unpaid_count."""
        return store.summary()

    @mcp.tool()
    def expenses_history(expense_id: str) -> list[dict[str, Any]]:
        """Full audit trail (every add/edit/paid/delete) for one expense."""
        return [h.to_dict() for h in store.history(expense_id)]

    # ── write tools ───────────────────────────────────────────────────────
    @mcp.tool()
    def expenses_add(
        amount: str,
        description: Optional[str] = None,
        date: Optional[str] = None,
        category: Optional[str] = None,
        submitted_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add an expense. amount accepts '300', '¥300', '300块'. date
        optional (YYYY-MM-DD, default today in China time). Keep the user's
        own words as description; pass their name as submitted_by if known."""
        return store.create(
            date=date or today_str(), amount=amount, description=description,
            category=category, submitted_by=submitted_by,
        ).to_dict()

    @mcp.tool()
    def expenses_mark_paid(
        expense_id: Optional[str] = None,
        query: Optional[str] = None,
        paid: bool = True,
        paid_date: Optional[str] = None,
        changed_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Mark an expense paid (or paid=false to undo). Target by expense_id
        OR by query text like '足球课'/'football'. paid_date optional
        (default today). Unpaid expenses are preferred when matching."""
        eid, ambiguous = _resolve(expense_id, query, prefer_unpaid=True)
        if ambiguous:
            return ambiguous
        if paid and not paid_date:
            paid_date = today_str()
        return store.mark_paid(
            eid, paid=paid, paid_date=paid_date, changed_by=changed_by
        ).to_dict()

    @mcp.tool()
    def expenses_update(
        expense_id: Optional[str] = None,
        query: Optional[str] = None,
        amount: Optional[str] = None,
        description: Optional[str] = None,
        date: Optional[str] = None,
        category: Optional[str] = None,
        changed_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Edit an expense. Target by expense_id or query text; pass only the
        fields to change (amount tolerates '¥350'/'350块')."""
        eid, ambiguous = _resolve(expense_id, query, prefer_unpaid=True)
        if ambiguous:
            return ambiguous
        fields = {
            k: v
            for k, v in {
                "amount": amount, "description": description,
                "date": date, "category": category,
            }.items()
            if v is not None
        }
        return store.update(eid, fields=fields, changed_by=changed_by).to_dict()

    @mcp.tool()
    def expenses_delete(
        expense_id: Optional[str] = None,
        query: Optional[str] = None,
        changed_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Delete an expense (confirm with the user first). Target by
        expense_id or query text. The audit history row is kept."""
        eid, ambiguous = _resolve(expense_id, query, prefer_unpaid=False)
        if ambiguous:
            return ambiguous
        return {"deleted": store.delete(eid, changed_by=changed_by)}

    # ── link management ───────────────────────────────────────────────────
    @mcp.tool()
    def expenses_mint_link(
        label: Optional[str] = None, expires_days: Optional[int] = None
    ) -> dict[str, Any]:
        """Mint a portal link token. Default NEVER expires (household links
        must not demand renewals). Portal URL: https://<service>/t/<token>."""
        return store.mint_token(label=label, expires_days=expires_days)

    @mcp.tool()
    def expenses_revoke_link(token_or_id: str) -> dict[str, Any]:
        """Revoke a portal link by token or token id (the kill switch)."""
        return {"revoked": store.revoke_token(token_or_id)}

    return mcp


class McpBearerMiddleware:
    """Optional bearer gate on the MCP mount.

    ``MCP_SECRET`` set → require ``Authorization: Bearer $MCP_SECRET`` (401
    otherwise). Unset → /mcp is open, per the owner's accepted threat model
    (obscure URL, low-stakes ledger). Portal and API paths are never touched.
    """

    def __init__(self, app: ASGIApp, protected_prefix: str = "/mcp"):
        self.app = app
        self.prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        secret = os.environ.get("MCP_SECRET", "")
        if secret:
            headers = dict(scope.get("headers") or [])
            supplied = (headers.get(b"authorization") or b"").decode()
            if not hmac.compare_digest(supplied, f"Bearer {secret}"):
                response = JSONResponse(
                    {"ok": False, "error": "unauthorized"}, status_code=401
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
