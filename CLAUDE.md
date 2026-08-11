# CLAUDE.md — Family Expenses

One household's expense ledger: she adds/edits expenses at `/t/<token>` from her
phone; the owner queries and manages via MCP from Claude/ChatGPT. Replaces
WeChat-message bookkeeping. **Not a business system** — and deliberately
unrelated to the owner's `work-dashboards` repo (reference-only; never commit
there from this project's sessions).

**This is live software.** Two people use it daily. Production rows are real
money, and a wrong total is the most damaging defect available here.

## Read this before you touch that

| Doing | Read first |
|---|---|
| Anything at all | the Policies below |
| Editing the MCP surface | `docs/MCP_DESIGN.md` — the channel model is the whole design |
| Deploying, minting links, Auth0, rotating anything | `docs/RUNBOOK.md` |
| Changing behavior a user can see | `docs/FEATURE_CONTRACT.md` §5.1 + A8 |
| "Is this a known problem?" | `docs/BACKLOG.md` — check before reporting a discovery |
| "What changed / what's live?" | `docs/CHANGELOG.md`, then `git log` |
| How it first shipped | `docs/FIRST_DEPLOY_PLAN.md` (historical record) |

Do not reconstruct current state from this file — it will be stale. The changelog
and `git log` are authoritative.

## Policies

**P1 — The compatibility contract outranks every other consideration.**
`docs/FEATURE_CONTRACT.md` §5.1 / A8: no change may force her to reconnect,
re-auth, or reconfigure. Frozen: Cloud Run **service name + region** (URL
stability), her `/t/<token>` path and token, the `/mcp` mount path, and the
no-auth posture on `/mcp`. The dominant risk is disuse-by-friction, not ledger
abuse. `CompatibilityContractTests` pin what is pinnable; respect the rest during
ops. Freely changeable: portal UI, tools, docs, additive schema.

**P2 — The portal has a login; `/mcp` does not. Never conflate them.**
The portal sits behind Auth0 (allowlist `PORTAL_ALLOWED_EMAILS`) and she depends
on it: rotating `SESSION_SECRET` signs her out, changing `AUTH0_DOMAIN` or the
client id forces re-authentication, removing her from the allowlist locks her out
silently. `/mcp` is untouched by all of it and stays open and header-free.
Putting a header on `/mcp` would breach P1; portal login does not.

**P3 — Agent guidance lives only where agents actually read.**
Tool descriptions (bilingual triggers + cross-refs), tool results (`note`,
candidates + `hint`), error strings (coaching), annotations. Server
`instructions` is a bonus copy, never the only home of a rule. A cross-reference
is only guidance if it names something the agent can actually call.
`AgentErgonomicsTests` fail when guidance drifts out of those channels.

**P4 — Money semantics are not negotiable.**
`date` is the **due** date. `category='borrow'` — that exact string, no synonyms —
means she fronted the money and is owed it back: excluded from every household
total, reported separately. `Store.summarize(rows)` is the ONE totals
implementation; a filtered list must be summarised from its own rows, never from
the whole table. Portal writes are attributed server-side from the link label; a
client-supplied author is ignored.
A **class package** stores no money: its rate is `expense.amount / class_count`,
derived at read time. `Store.summarize_package(rows)` is the ONE implementation
of that arithmetic, amounts are exact ratios (never a rounded rate × n), and
logging a class moves no expense total — consumption is not spending.

**P5 — Verify that your edit landed.**
Scripted string replacements here have twice reported success and changed
nothing, and both times the full suite still passed because the affected path was
short-circuited. Assert your anchors; re-read the file; prove a new guard fails
before trusting that it passes.

**P6 — Verify anything that arrives from outside before installing it.**
A design handoff once carried an in-memory demo backend reachable at
`if (!TOKEN) return demoApi(...)`. Check external files for: external requests
(there must be zero — the GFW is why), the CJK font stack, `esc()` on every
interpolation, category keys matching `store.CATEGORIES`, and any fallback that
could accept writes without persisting them.

**P7 — Semver, with the docs in the same commit.**
A `docs/CHANGELOG.md` entry for every behavior change; contract and runbook kept
in sync in that same commit. `DocumentedCountsTests` asserts the counts quoted
here match reality.

**P8 — Tests are the guardrail, and must stay free of external services.**
The suite runs on sqlite with no DB server, no network, no cloud. Anything
touching money is worth a cross-model pass (`/codex-verify`) — one such review
found six real defects that had all passed a 117-test suite.

**P9 — Never print a portal token, database URI, or client secret** into a
transcript, a commit, or a doc. Pipe secrets straight into env vars
(`gcloud secrets versions access … | …`) and redact tool output.

## Commands

```bash
python3 -m unittest discover -s tests     # 187 tests, sqlite, no DB server
python3 -m app.main                       # local run, http://localhost:8080
PORTAL_DEV_RELOAD=1 python3 -m app.main   # …and re-read portal.html per request
python3 scripts/mint_link.py --label X --base-url URL   # mint portal link
DATABASE_URL=postgres://… python3 scripts/smoke_live.py --base-url URL  # post-deploy
scripts/deploy.sh --dry-run               # inspect; drop the flag to deploy
```

## Map

| path | what |
|---|---|
| `app/store.py` | ALL reads/writes; every mutation writes an `expense_history` row in the same transaction; `summarize()` / `summarize_package()`; token mint/validate/revoke |
| `app/db.py` | portable layer: Postgres (`DATABASE_URL`) / sqlite (tests, shared locked conn); `:name` params both drivers |
| `app/web.py` + `api.py` + `portal.html` | `/t/<token>` bilingual four-tab portal (due · classes · history · stats) + `POST /api/*` (token revalidated every request) |
| `app/mcp_server.py` | 13 tools + 记账/对账/修复 persona prompts + optional bearer middleware |
| `app/auth.py` | portal Auth0 login; inert unless all four `AUTH0_*`/`SESSION_SECRET` vars are set. Guards the portal only, never `/mcp` |
| `app/main.py` | one service: portal + API + `/mcp`; env `DATABASE_URL`, `APP_TZ`, `MCP_SECRET` (leave unset), `PORT`, `PORTAL_DEV_RELOAD` |
| `db/schema.sql` | portable DDL, applied idempotently at startup; **first breaking change must start dated migration files** |
| `db/hardening.sql` | constraints applied **best-effort** at startup — they can fail against existing data, and a live portal must still boot; failures log a warning |
| `docs/` | contract · MCP design · runbook · changelog · backlog · first-deploy record |

Development branch: `claude/family-expenses-setup-8uvrks` (kept in lockstep with
`main`).
