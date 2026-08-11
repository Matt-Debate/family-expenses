"""Family MCP surface — engineered for what LLM agents ACTUALLY read.

Channel priority (see docs/MCP_DESIGN.md): agents reliably see (1) tool
names/descriptions/param schemas and (2) tool results & error strings.
Server ``instructions`` and resources are inconsistently surfaced across
clients, and prompts are user-invoked. Therefore:

  * trigger phrases (中文 + EN) live IN the tool descriptions — that is what
    drives correct tool selection;
  * every error string is coaching: it says what to call instead, so a wrong
    call self-corrects in one round trip;
  * results carry the running unpaid total so the agent can confirm naturally;
  * ``expenses_help`` returns the full playbook — works even on clients that
    never show instructions;
  * three personas ship as MCP prompts (记账 / 对账 / 修复) for clients that
    expose prompt templates.

Auth (owner's accepted threat model): links never expire; ``/mcp`` is open
unless ``MCP_SECRET`` is set.
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Optional, Union

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .store import (
    BORROW_CATEGORY, CATEGORY_KEYS, Store, ValidationError, _utc_now_iso,
    today_str,
)

_READ = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

_HELP = """\
家庭开支 Family Expenses — playbook for assistants.

WHAT THIS IS: one household's simple ledger of expenses that still need to be
paid (待付) or were paid (已付). Amounts are CNY (¥). Users speak casually,
Chinese or English. Always reply in the user's language.

INTENT → TOOL:
- "我还要付什么 / what do I owe / 有什么没付" → expenses_list(status="unpaid")
- "足球课300块 / football 300" → expenses_add(amount="300", description="足球课")
- "昨天交了300的足球课(已经付了)" → expenses_add(..., paid=true, paid_date=...)
- "足球课付了 / paid the football class / 交了" → expenses_mark_paid(query="足球课")
- "足球课改成350 / actually it was 350" → expenses_update(query="足球课", amount="350")
- "删掉/不用了 delete the swim class" → expenses_delete(query="游泳课") — confirm first
- "这个月花了多少 / totals" → expenses_list() and read .summary
- "这条是谁改的 / what happened to X" → expenses_history
- "给我老婆做个链接" → expenses_mint_link(label="wife") — link never expires
- "哪些链接还在用 / who has a link / list the links" → expenses_list_links

CATEGORIES — prefer these EXACT keys. Anything else is accepted and counted as
an ordinary household expense (and charted under whatever string you sent), so
an invented key does not vanish — it just is not one of the household's buckets:
  living · aden-edu · aden-sports · aden-clothes · aden-other · food · home ·
  utilities · internet · mobile · transport · travel · entertainment ·
  clothes · medical · borrow · other

- "borrow" is the ONE category with arithmetic behind it: it means she paid out
  of her own pocket (or the company's) and is owed the money BACK. Only the
  exact string "borrow" does this — it is kept out of every household expense
  total and reported on its own. A synonym like "loan repayment" or
  "reimbursement" is NOT recognised: it counts as ordinary household spending
  and inflates the paid/unpaid totals instead. Use it for
  "垫付/她先付的/borrowed from her/she fronted it/I lent".
- "living" is the recurring monthly household payment (生活费).

RULES OF THUMB:
- Dates/paid dates: omit them — the server defaults to today in China time.
- Amounts: pass what the user said — "¥300", "300块", "1,200元" all parse.
- Keep the user's own words as the description (don't translate it).
- query matching: substring on description/category. If a tool returns
  matched>1 with candidates, show them briefly and ask which; then call again
  with expense_id. Never guess.
- Pass the speaker's name as submitted_by / changed_by when you know it —
  the family reads the edit history.
"""


def build_mcp(store: Store) -> FastMCP:
    mcp = FastMCP(
        "family-expenses",
        instructions=_HELP,  # bonus for clients that surface it
        stateless_http=True,
        json_response=True,
        host=os.environ.get("HOST", "0.0.0.0"),
    )

    # ── helpers ───────────────────────────────────────────────────────────
    def _category_note(category) -> str:
        """Flag an off-list category.

        Says what actually happens, not what would be tidier: the row counts as
        an ordinary household expense. The dangerous case is a borrow-synonym,
        which inflates household spending instead of the money-owed-back figure.
        """
        text = (str(category).strip() if category else "")
        if not text or text in CATEGORY_KEYS:
            return ""
        return (
            f" · NOTE: {text!r} is not one of the household's category keys, so "
            "this counts as ordinary household spending. If it is money that "
            f"must be paid back, use category={BORROW_CATEGORY!r} exactly — "
            "no synonym is recognised. See expenses_help for the list."
        )

    def _summary_note() -> str:
        s = store.summary()
        return f"unpaid total now ¥{s['unpaid']:.2f} across {s['unpaid_count']} item(s)"

    def _candidates(matches) -> list[dict[str, Any]]:
        return [
            {
                "expense_id": e.id, "description": e.description,
                "amount": e.amount, "date": e.date, "paid": e.paid,
                "category": e.category,
            }
            for e in matches[:8]
        ]

    def _resolve(expense_id: Optional[str], query: Optional[str], *, prefer_unpaid: bool):
        if expense_id:
            return expense_id, None
        if not query or not str(query).strip():
            raise ValidationError(
                "target missing: pass expense_id, or query with a word from the "
                "expense's description (e.g. query='足球课')"
            )
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
                "hint": (f"nothing matches {query!r} — call "
                         "expenses_list(status='all') and look for it, or ask the user"),
            }
        return None, {
            "matched": len(matches), "candidates": _candidates(matches),
            "hint": ("several matches — show these to the user, ask which one, "
                     "then call again with that expense_id"),
        }

    # ── help ──────────────────────────────────────────────────────────────
    @mcp.tool(annotations=_READ)
    def expenses_help() -> str:
        """START HERE when unsure. Returns the playbook: which tool for which
        user phrase (中文/EN), defaults, and how to resolve ambiguity."""
        return _HELP

    # ── reads ─────────────────────────────────────────────────────────────
    @mcp.tool(annotations=_READ)
    def expenses_list(
        status: str = "all",
        query: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> dict[str, Any]:
        """List expenses AND totals. Use for: '我还要付什么/what do I owe'
        (status='unpaid'), '这个月花了多少/how much did we spend' (read
        .summary), or finding an item ('那个足球的' → query='足球').
        status: all|paid|unpaid|overdue. since/until: YYYY-MM-DD.
        .summary describes exactly the rows returned; when a filter is applied
        .ledger_total carries the whole-ledger figures for context."""
        if query and str(query).strip():
            # validate before comparing: Store.list() validates these, and a
            # malformed date must coach the caller rather than silently
            # producing an arbitrary slice
            if since:
                since = store._validate_date(since, field="since")
            if until:
                until = store._validate_date(until, field="until")
            expenses = store.find(query, status=status)
            # find() has no date support; applying the range here keeps
            # since/until meaningful instead of silently ignored
            if since:
                expenses = [e for e in expenses if e.date >= since]
            if until:
                expenses = [e for e in expenses if e.date <= until]
        else:
            expenses = store.list(status=status, since=since, until=until)
        return {
            "expenses": [e.to_dict() for e in expenses],
            # totals for THESE rows — a filtered list beside a whole-ledger
            # total is a wrong answer to the question that was asked
            "summary": store.summarize(expenses),
            "ledger_total": store.summary() if (query or since or until
                                                or status not in ("all", None, ""))
            else None,
        }

    @mcp.tool(annotations=_READ)
    def expenses_history(expense_id: str) -> dict[str, Any]:
        """Audit trail for ONE expense: every add/edit/paid/delete with who and
        when. Use for: '谁改的/这条怎么回事/what happened to this one'.
        Needs the expense_id (find it via expenses_list first)."""
        return {"history": [h.to_dict() for h in store.history(expense_id)]}

    # ── writes ────────────────────────────────────────────────────────────
    @mcp.tool(annotations=_WRITE)
    def expenses_add(
        amount: Union[str, float],
        description: Optional[str] = None,
        date: Optional[str] = None,
        category: Optional[str] = None,
        submitted_by: Optional[str] = None,
        paid: bool = False,
        paid_date: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record an expense. Use for: '足球课300块', 'football class 300',
        '要交300的班费'. amount accepts 300, '¥300', '300块'. Omit date =
        today (China time). Keep the user's own words as description. If they
        say it's ALREADY paid ('昨天交了...'), pass paid=true (paid_date
        defaults to today). To change an EXISTING expense use expenses_update;
        to pay one off use expenses_mark_paid. category: use an exact key from expenses_help — and for money someone fronted and is owed back, category='borrow' (never a synonym)."""
        # one transaction, even when it arrives already paid: this used to
        # create the row and then mark it paid separately, and a failure in
        # between left the expense unpaid while the tool reported an error
        expense = store.create(
            date=date or today_str(), amount=amount, description=description,
            category=category, submitted_by=submitted_by,
            paid=paid, paid_date=(paid_date or today_str()) if paid else None,
        )
        result = expense.to_dict()
        result["note"] = _summary_note() + _category_note(category)
        return result

    @mcp.tool(annotations=_WRITE)
    def expenses_mark_paid(
        expense_id: Optional[str] = None,
        query: Optional[str] = None,
        paid: bool = True,
        paid_date: Optional[str] = None,
        changed_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Check an expense off as paid. Use for: '足球课付了', '交了', 'paid
        the football class', 'settled it'. Target by query (a word from its
        description — unpaid items are preferred) or expense_id. Omit
        paid_date = today. paid=false undoes a mistaken check-off. To change
        amount/description instead, use expenses_update."""
        eid, ambiguous = _resolve(expense_id, query, prefer_unpaid=True)
        if ambiguous:
            return ambiguous
        if paid and not paid_date:
            paid_date = today_str()
        result = store.mark_paid(
            eid, paid=paid, paid_date=paid_date, changed_by=changed_by
        ).to_dict()
        result["note"] = _summary_note()
        return result

    @mcp.tool(annotations=_WRITE)
    def expenses_update(
        expense_id: Optional[str] = None,
        query: Optional[str] = None,
        amount: Optional[Union[str, float]] = None,
        description: Optional[str] = None,
        date: Optional[str] = None,
        category: Optional[str] = None,
        changed_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Correct an existing expense. Use for: '改成350', 'actually it was
        350', '不是足球是篮球', wrong date. Target by query or expense_id;
        pass ONLY the fields that change. To mark paid/unpaid use
        expenses_mark_paid (this tool cannot set paid). category must be an exact key from expenses_help; 'borrow' means owed back to whoever paid."""
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
        if not fields:
            raise ValidationError(
                "nothing to change — pass amount, description, date or category; "
                "for paid status use expenses_mark_paid"
            )
        result = store.update(eid, fields=fields, changed_by=changed_by).to_dict()
        result["note"] = _summary_note() + _category_note(fields.get("category"))
        return result

    @mcp.tool(annotations=_DESTRUCTIVE)
    def expenses_delete(
        expense_id: Optional[str] = None,
        query: Optional[str] = None,
        changed_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Remove an expense entirely. Use ONLY for '删掉/delete/不用了 it was
        entered by mistake' — and confirm with the user first. If the expense
        was simply paid, use expenses_mark_paid instead. The audit history is
        kept. Target by query or expense_id."""
        eid, ambiguous = _resolve(expense_id, query, prefer_unpaid=False)
        if ambiguous:
            return ambiguous
        return {"deleted": store.delete(eid, changed_by=changed_by),
                "note": _summary_note()}

    # ── link management ───────────────────────────────────────────────────
    @mcp.tool(annotations=_READ)
    def expenses_list_links(include_revoked: bool = False) -> dict[str, Any]:
        """List the portal links that exist and how they are being used. Use
        for: '谁有链接/哪些链接还在用/有几个链接', 'who has a link', 'list the
        links', and ALWAYS before revoking, so you know which one to kill.
        Returns each link's id, label and usage — NOT the token itself, so
        nothing permanent leaks into the chat. Pass an id straight to
        expenses_revoke_link. To create a link use expenses_mint_link."""
        rows = store.list_tokens()
        links = []
        for r in rows:
            if r["revoked"]:
                status = "revoked"
            elif r["expires_at"] and str(r["expires_at"]) <= _utc_now_iso():
                status = "expired"
            else:
                status = "active"
            if status == "revoked" and not include_revoked:
                continue
            links.append({
                "id": r["id"], "label": r["label"], "status": status,
                "expires_at": r["expires_at"] or "never",
                "use_count": r["use_count"],
                "last_used_at": r["last_used_at"] or "never opened",
                "created_at": r["created_at"],
            })
        active = sum(1 for x in links if x["status"] == "active")
        return {
            "links": links,
            "note": (
                f"{active} active link(s). Token values are never listed — "
                "revoke with expenses_revoke_link(token_or_id=<the id above>). "
                + ("Revoked links hidden; pass include_revoked=true to see them."
                   if not include_revoked else "")
            ),
        }

    @mcp.tool(annotations=_WRITE)
    def expenses_mint_link(
        label: Optional[str] = None, expires_days: Optional[int] = None
    ) -> dict[str, Any]:
        """Create a portal link for a family member ('给我老婆做个链接' /
        'make a link for my wife'). Never expires unless expires_days is set.
        Tell the user the URL is https://<this service>/t/<token>."""
        return store.mint_token(label=label, expires_days=expires_days)

    @mcp.tool(annotations=_DESTRUCTIVE)
    def expenses_revoke_link(token_or_id: str) -> dict[str, Any]:
        """Kill a portal link (lost phone, leaked URL). Takes the token or its
        id — call expenses_list_links first to see the ids. Revoking is
        permanent and the family member loses access immediately, so confirm
        with the user which link before calling."""
        return {"revoked": store.revoke_token(token_or_id)}

    # ── personas (MCP prompts — user-invocable in clients that show them) ─
    @mcp.prompt(name="jizhang", title="记账 Quick add")
    def quick_add(said: str = "") -> str:
        """快速记一笔 — paste or say what needs paying."""
        return (
            "You are the family bookkeeper (家庭记账员). The user will dictate "
            "expenses casually, possibly several in one message, Chinese or "
            "English. For each: call expenses_add keeping their exact wording "
            "as description; omit dates (defaults to today); pass amounts "
            "verbatim ('300块' is fine). If they said it's already paid, set "
            "paid=true. Confirm each item back in ONE short line in their "
            "language, ending with the unpaid total from the result's note. "
            "Ask at most one question, and only if the amount is missing."
            + (f"\n\nThe user said: {said}" if said else "")
        )

    @mcp.prompt(name="duizhang", title="对账 Settle up")
    def settle_up() -> str:
        """过一遍待付的，付了的打勾。"""
        return (
            "You are helping settle the family ledger (对账). Call "
            "expenses_list(status='unpaid') and present a short numbered list "
            "in the user's language with amounts and the total. Then walk "
            "through it: for each item they say is paid, call "
            "expenses_mark_paid (today's date unless they say otherwise). "
            "Finish by reporting what's still unpaid."
        )

    @mcp.prompt(name="xiufu", title="修复 Fix a mistake")
    def fix_mistake(problem: str = "") -> str:
        """记错了/改不动了/找不到 — troubleshooting persona."""
        return (
            "You are troubleshooting the family ledger (修复记录). Something "
            "was recorded wrongly or can't be found. Steps: (1) call "
            "expenses_list(status='all') — or with query=<word the user "
            "used> — and locate the item(s); show what you found; (2) if "
            "unclear which item, ask, showing the candidates; (3) apply the "
            "fix: wrong amount/text/date → expenses_update; wrongly marked "
            "paid → expenses_mark_paid(paid=false); duplicate/mistake → "
            "expenses_delete after explicit confirmation; (4) if the user "
            "disputes what happened, call expenses_history for that item and "
            "explain who changed what, when. Never delete without asking."
            + (f"\n\nThe problem: {problem}" if problem else "")
        )

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
