# Feature Contract — Family Expenses

**Status:** ACTIVE (v0.2.0 — standalone-repo architecture)
**Owner:** matt-debate
**Repo:** `matt-debate/test` (to be renamed `family-expenses`)
**Branch:** `claude/family-expenses-setup-8uvrks`
**Date:** 2026-07-14

## 1. Purpose

A dead-simple web portal where a household member (the operator's spouse) can
**submit and edit personal expenses the operator needs to pay**, replacing
free-text WeChat messages. Plus an MCP surface so the operator can query the
ledger from Claude / ChatGPT.

## 2. Architecture decision (v0.2.0 pivot)

Originally planned as a feature *inside* `work-dashboards`, reusing its parent
portal. **Superseded by owner direction (2026-07-14):**

- All code lives in **this standalone repo**. `work-dashboards` is
  reference-only — its *patterns* are reused (portal-token links, Neon
  Postgres, Cloud Run streamable-HTTP MCP), never its code, tables, or deploys.
- **Isolation is structural:** separate repo, separate database, separate
  services. Nothing to hide from the business app because nothing touches it.
  (This retires prior invariants I1–I3 by construction.)
- **MCP hosting: Cloud Run** streamable-HTTP (same stack as the existing
  `work-dashboards` cloud MCP: Python `mcp` SDK ≥1.12, Dockerfile + Cloud
  Build), per owner: "Cloud Run already works; no need to introduce new tech."
- Push to the designated branch after every completed chunk.

## 3. Requirements (owner spec)

- **Sa** — No approval workflow. Spouse submits; anyone with the link can edit;
  **every change is kept in an append-only history**.
- **Sb** — Simple model: add an expense; edit it if something changes; a way to
  **check it off as paid on a date**. Not a business system.
- **Sc** — An **MCP** so the operator can query via Anthropic/OpenAI clients.
- **Sd** — Proper documentation, versioning, and testing despite trivial scope.

### Non-goals
No approval flow, no user accounts/roles, no multi-currency conversion (single
default CNY, code stored), no receipts/recurring/budgets in v1.

## 4. Data model (single-tenant, portable SQL)

The app is single-household — no tenant column. SQL is written to run on both
**Postgres (Neon, production)** and **sqlite (tests)**: application-managed
ISO-8601 UTC timestamps (no DB triggers), `snapshot` stored as JSON **TEXT**,
no arrays, no PG-only expressions.

### `expenses`
| column | type | notes |
|---|---|---|
| `id` | TEXT PK | 12-hex app-generated |
| `date` | TEXT NOT NULL | `YYYY-MM-DD` |
| `amount` | REAL NOT NULL CHECK > 0 | |
| `currency` | TEXT NOT NULL DEFAULT 'CNY' | code only, no FX |
| `category` | TEXT | optional, suggested list in UI |
| `description` | TEXT | what it was |
| `paid` | BOOLEAN NOT NULL DEFAULT FALSE | |
| `paid_date` | TEXT | required iff `paid` (CHECK) |
| `submitted_by` | TEXT | free-text name (attribution, not auth) |
| `created_at` / `updated_at` | TEXT NOT NULL | app-managed UTC ISO |

### `expense_history` (append-only)
`id` PK, `expense_id`, `action` CHECK in
(`create`,`update`,`mark_paid`,`unmark_paid`,`delete`), `changed_by`,
`changed_at`, `snapshot` TEXT (JSON; post-change state, pre-change for delete).
Never updated or deleted by application code.

### `access_tokens`
`id` PK, `token` TEXT UNIQUE (`secrets.token_hex(32)`), `label`,
`expires_at` TEXT, `revoked` BOOLEAN DEFAULT FALSE, `created_at`,
`last_used_at`, `use_count`. Mirrors the `work-dashboards` portal-token
pattern, minus tenancy/scoping.

## 5. Access model

- One bookmarkable link per token: `/t/<token>`. Operator mints tokens via the
  MCP tool `expenses_mint_link` or `scripts/mint_link.py` (resolves prior
  verification finding **M2**: minting is an explicit, operator-only,
  first-class capability). No self-serve minting.
- Every API request revalidates the token (revoked + expiry, fail-closed).
- MCP access is separate: protected by Cloud Run/OAuth deployment config, not
  by portal tokens.

## 6. API contract (portal)

JSON POST endpoints, token in body; single Cloud Run service also serving the
portal page and the MCP mount.

| endpoint | body (besides `token`) | effect |
|---|---|---|
| `/api/list` | `status?: all\|paid\|unpaid, since?, until?` | expenses newest-first + summary totals |
| `/api/submit` | `date, amount, currency?, category?, description?, submitted_by?` | insert + history(`create`) |
| `/api/update` | `id, fields{date?,amount?,currency?,category?,description?,submitted_by?}, changed_by?` | update + history(`update`) |
| `/api/mark-paid` | `id, paid, paid_date?, changed_by?` | set paid state + history(`mark_paid`/`unmark_paid`) |
| `/api/delete` | `id, changed_by?` | delete + history(`delete`, pre-change snapshot) |
| `/api/history` | `id` | audit trail for one expense |

**Validation (server-authoritative):** `amount > 0`; `date`/`paid_date` are
`YYYY-MM-DD`; `paid=true ⇒ paid_date`; unknown update fields rejected.
**Atomicity (resolves prior finding M3):** each mutation writes its history row
in the **same DB transaction** as the primary write — one connection,
commit-on-success / rollback-on-error.

## 7. MCP surface (operator)

Tools on the Cloud Run streamable-HTTP MCP: `expenses_list`,
`expenses_summary`, `expenses_add`, `expenses_mark_paid`,
`expenses_mint_link`. Same store as the portal, so history/atomicity rules
apply identically.

## 8. UI

One mobile-first page, bilingual **中文 (default) / English**: add form
(amount, description, date), list newest-first with inline edit, "✓ 已付 /
mark paid" with date, paid/unpaid filter, running totals.

## 9. Acceptance criteria

- **A1** Spouse: open link → add → see listed → edit → mark paid with date, on
  a phone.
- **A2** Every mutation writes exactly one history row, atomically; history is
  never mutated.
- **A3** Operator can list/summarize/mark-paid/mint from an MCP client.
- **A4** `paid=true` without `paid_date` rejected at DB CHECK and API layer.
- **A5** Expired/revoked/unknown tokens rejected on every request.
- **A6** Test suite runs green in CI **without** a live Postgres (sqlite), and
  the schema applies cleanly to Postgres.

## 10. Versioning & docs

Semantic versioning in `docs/CHANGELOG.md`. `README.md` (user-facing),
`docs/RUNBOOK.md` (deploy, mint links, rotate/revoke), this contract, and the
implementation plan stay in sync with behavior changes.
