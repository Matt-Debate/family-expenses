# CLAUDE.md — Family Expenses

One household's expense portal + MCP: a family member adds/edits expenses at
`/t/<token>` from her phone; the owner queries/manages via MCP from
Claude/ChatGPT. Replaces WeChat-message bookkeeping. **Not a business
system** — and deliberately unrelated to the owner's `work-dashboards` repo
(reference-only there; never commit to it from this project's sessions).

## The rule that outranks everything

**Compatibility contract** — `docs/FEATURE_CONTRACT.md` §5.1, criterion A8:
no change may ever force the family member to reconnect, re-auth, or
reconfigure. The dominant risk is disuse-by-friction, not ledger abuse.

Frozen once she's connected: the Cloud Run **service name + region** (URL
stability), her `/t/<token>` link and token (never expire, never casually
revoked), the `/mcp` mount path, and the **no-auth posture** (`MCP_SECRET`
stays unset). `CompatibilityContractTests` enforce what's enforceable —
respect the rest during ops. Freely changeable: portal UI, tools, docs,
additive schema.

## Before editing the MCP

Read `docs/MCP_DESIGN.md` first. Guidance must live ONLY in channels agents
reliably read: tool descriptions (bilingual trigger phrases + cross-refs),
tool results (`note`, candidates+hint), error strings (coaching), and
annotations. Server `instructions` is bonus, never the only home of a rule.
`AgentErgonomicsTests` fail if guidance drifts out of those channels.

## Commands

```bash
python3 -m unittest discover -s tests     # 75 tests, sqlite, no DB server
python3 -m app.main                       # local run, http://localhost:8080
python3 scripts/mint_link.py --label X --base-url URL   # mint portal link
DATABASE_URL=postgres://… python3 scripts/smoke_live.py --base-url URL  # post-deploy
```

## Map

| path | what |
|---|---|
| `app/store.py` | ALL reads/writes; every mutation writes an `expense_history` row in the same transaction; token mint/validate/revoke |
| `app/db.py` | portable layer: Postgres (`DATABASE_URL`) / sqlite (tests, shared locked conn); `:name` params both drivers |
| `app/web.py` + `api.py` + `portal.html` | `/t/<token>` bilingual portal + `POST /api/*` (token revalidated every request) |
| `app/mcp_server.py` | 9 tools + 记账/对账/修复 persona prompts + optional bearer middleware |
| `app/main.py` | one service: portal + API + `/mcp`; env: `DATABASE_URL`, `APP_TZ` (Asia/Shanghai), `MCP_SECRET` (leave unset), `PORT` |
| `db/schema.sql` | portable DDL, applied idempotently at startup; **first breaking change must start dated migration files** |
| `docs/` | contract · MCP design · runbook · changelog (semver — entry with every behavior change) |

## Conventions

- Semver + `docs/CHANGELOG.md` entry for every behavior change; keep
  contract/runbook in sync in the same commit.
- Tests are the guardrails (agent-channel + compatibility pins); suite must
  stay runnable with zero external services.
- Timestamps are app-managed UTC ISO text; "today" defaults use `APP_TZ`.
- Development branch: `claude/family-expenses-setup-8uvrks`.

## Current state (2026-07-14) & next-session checklist (PC, one-time)

v0.4.4, production-hardening in progress, **not yet deployed**. The reviewed
first-deploy plan is `docs/FIRST_DEPLOY_PLAN.md`. Delete this section when the
checklist is done.

1. **Rename repo** `Test` → `family-expenses` (GitHub → Settings → rename;
   old remote URLs redirect, so existing clones keep working).
2. **Merge** `claude/family-expenses-setup-8uvrks` → `main` (no PR was
   opened; merge locally or open one).
3. **Neon**: create a dedicated project (separate from any business DB),
   copy the `postgres://…` connection string.
4. **Deploy** with `scripts/deploy.sh` after its dry-run gate. The script pins
   the permanent service `family-expenses` in `asia-southeast1`, deploys a
   SHA-tagged image, binds the pooled Neon URI from Secret Manager, and leaves
   `MCP_SECRET` unset.
5. **Verify live** through the pooled Neon URI — the one thing sqlite tests
   can't prove (pooler behavior, schema on real Postgres, public URL):
   ```bash
   DATABASE_URL='postgres://…' python3 scripts/smoke_live.py \
     --base-url https://<service-url>
   ```
6. **Mint her link**: `python3 scripts/mint_link.py --label wife --base-url
   https://<service-url>` → send over WeChat → she bookmarks it. Add one on
   her phone's home screen if you can (Safari/Chrome "Add to Home Screen").
7. **Connect MCP**: claude.ai / ChatGPT connector, URL
   `https://<service-url>/mcp`, no header. Try "我还要付什么". Optionally set
   it up on her app too (personas 记账/对账/修复 appear as prompt templates).
8. Optional, later: custom domain; Cloud Build trigger on main; check Neon
   PITR/backups; delete this checklist section.
