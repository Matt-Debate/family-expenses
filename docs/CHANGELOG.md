# Changelog — Family Expenses

Semantic versioning. Unreleased work accumulates under [Unreleased] and is cut
to a release entry when a chunk set ships.

## [Unreleased]
### Ops / handoff
- `CLAUDE.md`: project instructions for future sessions (compatibility
  contract first, MCP-editing rules, map, commands) + one-time PC deploy
  checklist (rename repo, merge, Neon, deploy, live smoke, mint link,
  connect MCP).
- `scripts/smoke_live.py`: post-deploy verification — proves schema applies
  to real Postgres and exercises the deployed service end-to-end over the
  public URL, with cleanup. Production entrypoint (`python -m app.main` with
  $PORT) rehearsed in-session: health + portal routes OK.

## [0.4.4] — 2026-07-15

### Operations and verification
- First production deployment accepted on the permanent Cloud Run service in
  `asia-southeast1`, backed by an isolated Neon project. The same portal link
  and public no-header MCP URL survived a revision change; mobile acceptance,
  runtime-contract inspection, cleanup, and final public smoke all passed.
- The live smoke now covers portal update, mark-paid, unmark-paid, exact audit
  history, and a real MCP `ClientSession` handshake with the exact nine-tool
  and three-prompt inventories plus bilingual natural-input, ambiguity,
  correction, fuzzy-payment, history, and cleanup flows.
- `/favicon.ico` returns 204 so mobile/browser acceptance runs have a clean
  console instead of a cosmetic missing-favicon error.
- Suite 74 → **75**.

## [0.4.3] — 2026-07-15

### Fixed
- Added `/health` as the Cloud Run-safe health endpoint and moved live smoke
  checks to it. `/healthz` remains available locally and for compatibility,
  but Google's front end reserves some paths ending in `z` and intercepts
  this one with a 404 before requests reach the container.

## [0.4.2] — 2026-07-15
First-deployment hardening: the public MCP, pooled Postgres lifecycle, and
Cloud Run storage posture now fail safely under the production conditions the
local SQLite suite cannot reproduce.

### Fixed
- FastMCP binds its host policy to runtime `HOST` (default `0.0.0.0`), so a
  real Cloud Run Host header completes initialize and `tools/list` instead of
  returning 421. Stateless HTTP returns JSON responses, avoiding the SDK's
  unclosed SSE receive-stream warning while remaining protocol-compliant.
- Postgres thread-local connections evict closed handles and retry a failed
  first transaction statement once on a fresh connection. Mid-transaction
  failures are never replayed; the poisoned connection is evicted and rollback
  failure cannot mask the original error.
- Cloud Run (`K_SERVICE`) refuses to boot without a Postgres `DATABASE_URL`,
  preventing a missing secret binding from silently writing to ephemeral
  SQLite.
- Async MCP tests now close their event loops and complete the initialized
  notification handshake, removing lifecycle warnings from the release gate.

### Operations
- Added `scripts/deploy.sh`: clean-tree guard, explicit SHA build/deploy,
  Secret Manager binding, no `MCP_SECRET`, finite scale, and permanent
  Singapore region/service constants.
- Cloud Build no longer publishes or deploys a mutable `latest` image.
- Live smoke requires the Neon pooled URI and repeats token validation at
  least six times to cross psycopg's default prepared-statement threshold.
- Added regression/static gates for reconnect, production fail-closed,
  external-host MCP, pooled validation, and deployment contract. Suite 63 →
  **74**.

## [0.4.1] — 2026-07-14
Owner's final risk ranking encoded: the dominant risk is a family member
being forced to reconnect (→ disuse), not unauthorized edits.

### Added
- **Compatibility contract** (FEATURE_CONTRACT §5.1, acceptance A8): frozen
  surface = service URL, `/t/<token>` path + her token, `/mcp` mount,
  no-auth-header posture. Runbook gains "Don't break her setup" rules.
- `CompatibilityContractTests` pin the frozen surface in CI (mount path,
  route shapes, credential-free defaults, heavy use never invalidating a
  link). Suite 59 → **63**.

## [0.4.0] — 2026-07-14
Agent-ergonomics rework, motivated by the owner's experience of MCP guidance
"the agent never sees": all behavior now lives in channels agents reliably
read (tool descriptions, results, error strings, annotations) — codified in
**docs/MCP_DESIGN.md**.

### Added
- `expenses_help` tool — playbook-as-a-tool ("START HERE when unsure");
  works on clients that never surface server `instructions`.
- **Three personas as MCP prompts**: 记账 `jizhang` (quick add), 对账
  `duizhang` (settle up), 修复 `xiufu` (fix a mistake).
- Bilingual trigger phrases and cross-references ("to X use tool Y") inside
  every tool description — the channel that drives tool selection.
- **Coached error strings**: wrong amount says what parses ('¥300', '300块');
  bad date says relative words must be converted or omitted; touching paid
  via update redirects to expenses_mark_paid. One-round-trip self-correction.
- Write results carry a `note` with the running unpaid total.
- `expenses_add` records already-paid expenses in one call (`paid=true`,
  audit trail keeps create + mark_paid).
- Tool annotations: reads flagged read-only (fewer client permission
  prompts), delete/revoke flagged destructive.

### Changed
- `expenses_summary` **removed** (9 tools total): redundant with the summary
  already returned by `expenses_list`; redundant read tools split selection
  probability (see MCP_DESIGN.md).
- `amount` params accept numbers **or** strings — pydantic v2 does not coerce
  int→str, so the old `str` type silently rejected numeric arguments from
  agents (exactly the "worked for the dev, failed for the agent" class).
- `expenses_history` returns `{"history": [...]}` (object, not bare array).

### Tests
- Suite 51 → **59**; new `AgentErgonomicsTests` pin triggers, cross-refs,
  annotations, personas, numeric amounts, one-call paid add, result notes,
  and coaching text in errors — regressions in agent-visible channels fail CI.

## [0.3.0] — 2026-07-14
Auth scaled back to the owner's explicit threat model (low-stakes household
ledger, zero-tech user, unguessable URLs); MCP reworked for natural speech.

### Changed
- **Portal links never expire by default** (`expires_at` nullable; NULL =
  never). The holder never renews anything; revocation stays the kill switch.
  Bounded expiry still available via `expires_days`.
- **MCP bearer is now optional**: `MCP_SECRET` set → enforced (401 on
  mismatch); unset → `/mcp` is open (was fail-closed 503).
- `expenses_add`/`mark_paid` dates optional — default **today in `APP_TZ`**
  (default `Asia/Shanghai`), not UTC.
- Amounts tolerate spoken/pasted forms: `¥300`, `300块`, `1,200元`, `300 rmb`.

### Added
- **Natural-language targeting**: `expenses_mark_paid` / `expenses_update` /
  `expenses_delete` accept a fuzzy `query` ("足球课") instead of an id —
  one match acts (mark-paid prefers the unpaid match), several matches return
  candidates for the assistant to disambiguate; zero matches return a hint.
- New MCP tools `expenses_update` and `expenses_delete`; `expenses_list`
  gains a `query` text filter. Tool count now 9.
- Server instructions coach LLM clients: bilingual example utterances,
  defaults, confirm-before-delete, reply in the user's language.
- `store.find()`, `today_str()`; runbook §4 "what you (or she) can say".

### Tests
- Suite 37 → **51** (natural-speech flows, ambiguity, never-expire tokens,
  open/gated middleware) — all green; live MCP smoke re-run in open mode
  covering the full conversational flow end-to-end.

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
