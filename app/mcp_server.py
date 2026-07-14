"""Operator MCP surface (contract §7) — FastMCP streamable-HTTP.

Same SDK + transport as the work-dashboards cloud MCP (``mcp>=1.12,<2``,
Cloud Run). Tools close over the shared :class:`Store`, so validation,
atomic history writes, and token rules are identical to the portal's.

Security: the MCP mount is protected by :class:`McpBearerMiddleware` — a
required ``Authorization: Bearer $MCP_SECRET`` header, fail-closed when the
secret is unset (mint-capable tools must never be world-reachable).
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .store import Store


def build_mcp(store: Store) -> FastMCP:
    mcp = FastMCP(
        "family-expenses",
        instructions=(
            "Household expense ledger. Amounts are CNY unless stated. "
            "'unpaid' expenses are the ones the operator still needs to pay."
        ),
        stateless_http=True,  # Cloud Run scale-to-zero friendly
    )

    @mcp.tool()
    def expenses_list(
        status: str = "all",
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> dict[str, Any]:
        """List expenses. status: all|paid|unpaid; since/until: YYYY-MM-DD."""
        return {
            "expenses": [e.to_dict() for e in store.list(status=status, since=since, until=until)],
            "summary": store.summary(),
        }

    @mcp.tool()
    def expenses_summary() -> dict[str, Any]:
        """Totals: count, total, paid, unpaid, unpaid_count (amounts in CNY)."""
        return store.summary()

    @mcp.tool()
    def expenses_add(
        date: str,
        amount: float,
        description: Optional[str] = None,
        category: Optional[str] = None,
        submitted_by: Optional[str] = "operator",
    ) -> dict[str, Any]:
        """Add an expense (date YYYY-MM-DD, amount > 0). Writes audit history."""
        return store.create(
            date=date, amount=amount, description=description,
            category=category, submitted_by=submitted_by,
        ).to_dict()

    @mcp.tool()
    def expenses_mark_paid(
        expense_id: str,
        paid: bool = True,
        paid_date: Optional[str] = None,
        changed_by: Optional[str] = "operator",
    ) -> dict[str, Any]:
        """Mark an expense paid (paid_date required, YYYY-MM-DD) or unpaid."""
        return store.mark_paid(
            expense_id, paid=paid, paid_date=paid_date, changed_by=changed_by
        ).to_dict()

    @mcp.tool()
    def expenses_history(expense_id: str) -> list[dict[str, Any]]:
        """Full append-only audit trail for one expense."""
        return [h.to_dict() for h in store.history(expense_id)]

    @mcp.tool()
    def expenses_mint_link(
        label: Optional[str] = None, expires_days: int = 120
    ) -> dict[str, Any]:
        """Mint a new household portal link token (operator only).

        Returns the raw token; the portal URL is https://<service>/t/<token>.
        """
        return store.mint_token(label=label, expires_days=expires_days)

    @mcp.tool()
    def expenses_revoke_link(token_or_id: str) -> dict[str, Any]:
        """Revoke a portal link by token or token id."""
        return {"revoked": store.revoke_token(token_or_id)}

    return mcp


class McpBearerMiddleware:
    """Require ``Authorization: Bearer $MCP_SECRET`` on the MCP mount.

    Fail-closed: with no ``MCP_SECRET`` configured, /mcp requests are rejected
    (503) rather than served open. Portal and API paths are untouched.
    """

    def __init__(self, app: ASGIApp, protected_prefix: str = "/mcp"):
        self.app = app
        self.prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        secret = os.environ.get("MCP_SECRET", "")
        if not secret:
            response = JSONResponse(
                {"ok": False, "error": "MCP disabled: MCP_SECRET not configured"},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        supplied = (headers.get(b"authorization") or b"").decode()
        if not hmac.compare_digest(supplied, f"Bearer {secret}"):
            response = JSONResponse(
                {"ok": False, "error": "unauthorized"}, status_code=401
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
