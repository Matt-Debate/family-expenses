# Implementation Plan — Family Expenses (standalone)

**Companion to:** `FEATURE_CONTRACT.md` v0.2.0. Work is delivered in chunks;
each chunk is committed and **pushed to `claude/family-expenses-setup-8uvrks`**
when complete (owner direction, 2026-07-14).

Patterns are adapted from `work-dashboards` (reference-only): portal-token
links, portable store with same-transaction audit writes, Cloud Run
streamable-HTTP MCP. That repo receives **no commits or pushes**.

## Chunk 1 — Pivot: docs, schema, cleanup  ✅
- Remove the superseded localStorage prototype (`index.html`).
- `docs/FEATURE_CONTRACT.md` (v0.2.0), this plan, `docs/CHANGELOG.md`.
- `db/schema.sql` — portable DDL (Postgres + sqlite): `expenses`,
  `expense_history`, `access_tokens` per contract §4.
- New `README.md`.

## Chunk 2 — Core store (Python) + tests  ✅
- `app/db.py` — connection layer: `DATABASE_URL` (Neon/psycopg) or sqlite path
  (tests); named-param SQL translated per driver; transaction context manager
  (commit/rollback); idempotent `init_db(schema.sql)`.
- `app/store.py` — `create / update / mark_paid / delete / list / summary /
  history / mint_token / validate_token`; every mutation + its history row in
  one transaction; validation per contract §6.
- `app/models.py` — dataclasses.
- `tests/test_store.py` — full CRUD/validation/history/atomicity/token suite
  against sqlite (runs in CI with no Postgres — acceptance A6).

## Chunk 3 — Portal web app  ✅
- `app/web.py` — Starlette app: `GET /t/<token>` (portal page), `GET /healthz`,
  the six `/api/*` JSON endpoints (token revalidated per request).
- `app/portal.html` — single-file mobile-first UI, 中文 default + EN toggle.
- `tests/test_web.py` — endpoint tests via Starlette TestClient (sqlite).

## Chunk 4 — MCP + deploy  ✅
- `app/mcp_server.py` — FastMCP (`mcp>=1.12,<2`, same as work-dashboards)
  exposing the five tools (contract §7); mounted at `/mcp` on the same
  Starlette app; `app/main.py` entrypoint (uvicorn, `$PORT`).
- `Dockerfile`, `cloudbuild.yaml` — mirroring the work-dashboards
  `Dockerfile.mcp` / `cloudbuild.mcp.yaml` shape.
- `requirements.txt`.
- `tests/test_mcp.py` — tool registration + calls against sqlite.

## Chunk 5 — Runbook + release  ✅
- `docs/RUNBOOK.md` — create Neon DB, apply schema, deploy to Cloud Run, mint
  the household link, revoke/rotate, connect Claude/ChatGPT to the MCP.
- Changelog `0.1.0` release entry; final README polish.

## Carried-over verification findings (from the v0.1.x review)
- **M2 (minting)** → first-class `mint_token` + MCP tool + CLI script.
- **M3 (atomicity)** → single-connection transaction per mutation; asserted by
  a test that forces a history failure and checks rollback.
- **S4-class risk (migrations)** → v1 uses one idempotent `schema.sql`
  (`CREATE TABLE IF NOT EXISTS`) applied at startup; dated migration files
  begin only when a breaking change first appears.
- Moot after the pivot: M1/S2/S3 (work-dashboards SPA gates), S5 (tenancy —
  single-tenant), S6 (money-as-cents audit — REAL accepted for household use).

## Environment limits (this cloud session)
No live Postgres and no psycopg here — sqlite carries the test suite;
Postgres application of `db/schema.sql` happens at first deploy (RUNBOOK).
