# AGENTS.md — Family Expenses

One household's expense ledger: she adds and edits expenses from her phone at
`/t/<token>`; the owner queries and manages them through Codex/ChatGPT MCP.
Not a business system. This is live software with real household money.

**This file is the authoritative agent policy. Claude is retired and unavailable.**
The owner retired `CLAUDE.md` on 2026-10-28 and moved its relevant guidance here.
Do not depend on Claude tooling or use `CLAUDE.md` as an instruction source.

## Repository identity and companion routing

The stable local path is `/Users/matthewalanfarmer/family-expenses`, currently a
symlink to the legacy checkout `/Users/matthewalanfarmer/Test`. Treat both paths
as the same repository.

The companion Work Dashboards checkout is `/Users/matthewalanfarmer/work_dashboards`
and has its own agent instructions. It is **reference-only** from here:
read its patterns, never commit to it from this project's sessions.

The user may work on both in one conversation. Route Family Expenses portal,
`/t/<token>`, `expenses_*` MCP, and household-deployment work here; route Work
Dashboards product work to the companion checkout. If a referenced commit or
file is absent from the current checkout, check the companion before reporting it
missing. Never mix changes from the two projects in one commit.

## Development workflow

- Reuse the task's existing managed worktree. For new branches use `codex/`.
  The historical `claude/family-expenses-setup-8uvrks` branch name does not
  imply use of Claude. Never reset or merge another checkout to change branches.
- Inspect the existing implementation and the relevant doc before editing.
  Preserve unrelated user changes in a dirty worktree.
- Run the focused tests for what you changed, then the full suite before handing
  off. The suite must keep running without a database or any network.
- **Do not, without an explicit request:** deploy; rotate, revoke, or mint a live
  family link; rename the repository; merge branches; or change permanent Cloud
  Run settings. These are the operations that can break the compatibility
  contract or interrupt someone mid-use.

## Current state

Deliberately not recorded here — it goes stale and then misleads. `docs/CHANGELOG.md`
and `git log` are authoritative for what shipped; `docs/FIRST_DEPLOY_PLAN.md`
holds the deployment evidence record; `docs/BACKLOG.md` holds known, deferred
problems. Check the backlog before reporting a discovery.

## Read this before you touch that

| Doing | Read first |
|---|---|
| Anything at all | the Policies below |
| Editing the MCP surface | `docs/MCP_DESIGN.md` — the channel model is the whole design |
| Deploying, minting links, Auth0, rotating anything | `docs/RUNBOOK.md` |
| Changing behavior a user can see | `docs/FEATURE_CONTRACT.md` §5.1 + A8 |
| "Is this a known problem?" | `docs/BACKLOG.md` — check before reporting a discovery |
| Reviewing, fixing, or testing anything | `docs/LESSONS.md` — what has actually broken here and the rule each failure produced. The policies below are the short form; that file is the evidence |
| "What changed / what's live?" | `docs/CHANGELOG.md`, then `git log` |
| How it first shipped | `docs/FIRST_DEPLOY_PLAN.md` (historical record) |

Do not reconstruct current state from this file — it will be stale. The changelog
and `git log` are authoritative.

## Policies

Each of these came from a specific failure. `docs/LESSONS.md` records what
happened in each case — read it before a review or a fix wave, because a rule
you know the origin of transfers to cases it does not literally name. The two
that cost the most: **a fix is not a verified state** (five of six fix waves in
v0.11.0 introduced a defect of their own), and **a test stub more permissive
than the real object cannot fail**.

**P1 — Preserve the compatibility contract.**
The 2026-09-30 owner request supersedes the old anonymous MCP posture for
implementation and migration planning only. MCP OAuth deployment and family
reconnection require separate approval (RUNBOOK §9). Cloud Run URL/region,
`/mcp`, portal paths, live links, household data, portal email/password login,
SESSION_SECRET and portal allowlists remain protected.

**P2 — Portal sessions and MCP authentication are independent.**
Portal Auth0 cookies do not grant MCP access. MCP requires signed Auth0 access
tokens, an approved subject and read/write/link-management permissions. Never
restore anonymous access or static-secret fallback to work around OAuth errors.
Preserve existing portal client and session bindings during rollout.

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
derived at read time. `Store.summarize_package(package, amount, events)` is the ONE implementation
of that arithmetic, amounts are exact ratios (never a rounded rate × n), and
logging a class moves no expense total — consumption is not spending.
A **refund** is an `expense_refunds` row, never a write-down of the payment:
the stored `amount` stays gross, and every read exposes the **effective**
figure (gross − refunds) under the name `amount`, with `gross_amount` and
`refunded` beside it — so no total, chart or course rate can forget a refund.
The two writes that round-trip an amount (`update(amount=…)`, the portal edit
box) use the gross figure and refuse to go below what came back.

**P5 — Verify that your edit landed, and that your check could have failed.**
Scripted string replacements here have twice reported success and changed
nothing, and both times the full suite still passed because the affected path was
short-circuited. Assert your anchors; re-read the file; prove a new guard fails
before trusting that it passes. When mutation-testing a guard, assert the target
test **exists and passes** before applying the mutation — `unittest` exits
non-zero for a name that does not resolve, so a renamed or mistyped target reads
as "caught". One did, and the mutation behind it turned out to survive.

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

**P8 — Offline tests and independent Codex review.**
The suite runs on sqlite with no DB server, network, or cloud. Use independent
Codex reviewers with a model and reasoning effort appropriate to the risk;
review money changes for atomicity, amounts, attribution, retry behavior and
meaningful tests. Reviewers report findings without editing. Fix every must-fix
and should-fix, then verify the final state; passing tests alone are not review.
Claude is retired and unavailable (owner decision 2026-10-28). Do not ask for
Claude access, run a Claude review, or block work on a mandatory cross-model
review. Cross-model review is optional only when the owner requests it; the
owner may purchase one if issues arise. This instruction supersedes old Claude
or cross-model gates in historical docs and review skills.

**P9 — Never print a portal token, database URI, or client secret** into a
transcript, a commit, or a doc. Pipe secrets straight into env vars
(`gcloud secrets versions access … | …`) and redact tool output.

## Commands

```bash
python3 -m unittest discover -s tests     # 542 tests, sqlite, no DB server
python3 -m app.main                       # local run, http://localhost:8080
PORTAL_DEV_RELOAD=1 python3 -m app.main   # …and re-read portal.html per request
python3 scripts/mint_link.py --label X --base-url URL   # mint portal link
DATABASE_URL=postgres://… python3 scripts/smoke_live.py --base-url URL  # post-deploy
scripts/deploy.sh --dry-run               # inspect; drop the flag to deploy
```

## Map

| path | what |
|---|---|
| `app/store.py` | ALL reads/writes; every mutation — expense, refund, or class-tracker — writes an `expense_history` row in the same transaction, keyed by the payment; `summarize()` / `summarize_package()`; `refund()` (+ resize in one tx); token mint/validate/revoke |
| `app/db.py` | portable layer: Postgres (`DATABASE_URL`) / sqlite (tests, shared locked conn); `:name` params both drivers; `_migrate_history_actions()` — the one in-place constraint change, inspection-driven and idempotent |
| `app/config.py` | `PORTAL_BASE_URL` in one place: the Auth0 redirect and the MCP's full portal link read the same value |
| `app/web.py` + `api.py` + `portal.html` | `/t/<token>` bilingual four-tab portal (due · classes · history · stats) + `POST /api/*` (token revalidated every request) |
| `app/mcp_server.py` | 18 tools + 记账/对账/修复 persona prompts + mandatory OAuth resource-server guards |
| `app/mcp_auth.py` | MCP token verification, discovery, permissions and authenticated attribution |
| `app/auth.py` | portal Auth0 login; inert unless all four `AUTH0_*`/`SESSION_SECRET` vars are set. Guards the portal only, never `/mcp` |
| `app/main.py` | one service: portal + API + `/mcp`; env `DATABASE_URL`, `APP_TZ`, `MCP_AUTH_ISSUER`, `MCP_RESOURCE_URL`, `MCP_MEMBERS_JSON`, `PORT`, `PORTAL_DEV_RELOAD` |
| `db/schema.sql` | portable DDL, applied idempotently at startup; **first breaking change must start dated migration files** |
| `db/hardening.sql` | constraints applied **best-effort** at startup — they can fail against existing data, and a live portal must still boot; failures log a warning |
| `docs/` | contract · MCP design · runbook · changelog · backlog · **lessons (failure → rule)** · first-deploy record |
