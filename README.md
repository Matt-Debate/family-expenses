# Family Expenses

A tiny, private household expense portal. One bookmarkable link for a family
member to **submit and edit expenses that need paying** (with a "mark paid on
date" check-off and a full edit history), plus an **MCP server** so the owner
can query the ledger from Claude or ChatGPT.

Built to replace free-text WeChat messages — not a business system.

## How it fits together

- **Portal** — one mobile-first page (中文 default / English toggle) served at
  `/t/<token>`. Anyone with the link can add, edit, and mark expenses paid; no
  accounts. Every change is recorded in an append-only history.
- **Store** — Postgres (Neon) in production; the same portable SQL runs the
  test suite on sqlite with no database server.
- **MCP** — streamable-HTTP server (Python `mcp` SDK) on Cloud Run, exposing
  `expenses_list / expenses_summary / expenses_add / expenses_mark_paid /
  expenses_mint_link`.
- **Access** — random 64-hex tokens with expiry + revocation, minted only by
  the owner (MCP tool or `scripts/mint_link.py`).

## Repository layout

| path | contents |
|---|---|
| `db/schema.sql` | portable DDL (Postgres + sqlite), applied idempotently at startup |
| `app/` | store, web portal, MCP server |
| `tests/` | suite runs on sqlite — no live DB needed |
| `scripts/` | operator tooling (mint links) |
| `docs/` | feature contract, implementation plan, changelog, runbook |

## Status

Under active development on `claude/family-expenses-setup-8uvrks` — see
`docs/IMPLEMENTATION_PLAN.md` for the chunk-by-chunk state and
`docs/CHANGELOG.md` for history.
