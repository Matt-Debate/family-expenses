# Changelog — Family Expenses

Semantic versioning. Unreleased work accumulates under [Unreleased] and is cut
to a release entry when a chunk set ships.

## [0.2.0] — 2026-07-14
First complete implementation (chunks 1–5), ready for first deploy.

### Added
- **Store** (`app/store.py`, `app/db.py`): portable Postgres/sqlite layer;
  create/update/mark-paid/delete/list/summary/history; every mutation writes
  one append-only `expense_history` row in the same transaction (M3); token
  mint/validate/revoke, fail-closed with usage tracking (M2);
  `expense_history.seq` for deterministic ordering.
- **Portal** (`app/web.py`, `app/api.py`, `app/portal.html`): `/t/<token>`
  mobile-first bilingual (中文/EN) page — add, inline edit, mark paid with
  date, filters, totals, per-item history; JSON API revalidates the token on
  every request (401/400/404 mapping).
- **MCP** (`app/mcp_server.py`, `app/main.py`): FastMCP streamable-HTTP with 7
  operator tools incl. `expenses_mint_link`/`expenses_revoke_link`; `/mcp`
  gated by `Authorization: Bearer $MCP_SECRET`, fail-closed when unset; one
  Cloud Run service serves portal + API + MCP.
- **Ops**: `Dockerfile`, `cloudbuild.yaml`, `scripts/mint_link.py`,
  `docs/RUNBOOK.md`.
- **Tests**: 37 (store, HTTP tier, MCP tools, bearer middleware, combined
  app) — run on sqlite with no DB server; plus live smokes: real uvicorn
  portal flow and a real MCP client handshake with bearer auth.

### Changed (architecture pivot, owner direction)
- The feature moved from a `work-dashboards` in-repo portal to this
  **standalone repo**. `work-dashboards` is reference-only (patterns:
  portal-token links, Neon, Cloud Run streamable-HTTP MCP) and receives no
  commits or pushes. Isolation from the business system is structural
  (separate repo / DB / services). MCP hosting: Cloud Run (owner: "already
  works; no need to introduce new tech").
### Removed
- Superseded localStorage prototype (`index.html`) — replaced by the
  server-backed portal (history preserved in git).

## Planning history (v0.1.x, in work-dashboards — superseded)
- `0.1.1` — contract + plan revised per independent adversarial verification
  (3 must-fix / 6 should-fix / 3 nits). Carried forward: M2 (first-class token
  minting), M3 (same-transaction audit writes). Moot after pivot: M1/S2/S3
  (work-dashboards SPA), S5 (tenancy), S6 (money-as-cents audit).
- `0.1.0` — initial contract + plan.
