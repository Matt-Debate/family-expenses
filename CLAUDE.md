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
python3 -m unittest discover -s tests     # 126 tests, sqlite, no DB server
python3 -m app.main                       # local run, http://localhost:8080
python3 scripts/mint_link.py --label X --base-url URL   # mint portal link
DATABASE_URL=postgres://… python3 scripts/smoke_live.py --base-url URL  # post-deploy
```

## Map

| path | what |
|---|---|
| `app/store.py` | ALL reads/writes; every mutation writes an `expense_history` row in the same transaction; token mint/validate/revoke. `summarize(rows)` is the ONE totals implementation — a filtered list must be summarised from its own rows, never the whole table |
| `app/db.py` | portable layer: Postgres (`DATABASE_URL`) / sqlite (tests, shared locked conn); `:name` params both drivers |
| `app/web.py` + `api.py` + `portal.html` | `/t/<token>` bilingual portal + `POST /api/*` (token revalidated every request) |
| `app/mcp_server.py` | 10 tools + 记账/对账/修复 persona prompts + optional bearer middleware |
| `app/auth.py` | portal Auth0 login; inert unless all four `AUTH0_*`/`SESSION_SECRET` vars are set. Guards the portal only, never `/mcp` |
| `app/main.py` | one service: portal + API + `/mcp`; env: `DATABASE_URL`, `APP_TZ` (Asia/Shanghai), `MCP_SECRET` (leave unset), `PORT`, `PORTAL_DEV_RELOAD` |
| `db/schema.sql` | portable DDL, applied idempotently at startup; **first breaking change must start dated migration files** |
| `docs/` | contract · MCP design · runbook · changelog (semver — entry with every behavior change) · **backlog** (deferred work + known unfixed issues) · first-deploy plan (historical evidence record) |

## Conventions

- Semver + `docs/CHANGELOG.md` entry for every behavior change; keep
  contract/runbook in sync in the same commit.
- Tests are the guardrails (agent-channel + compatibility pins); suite must
  stay runnable with zero external services.
- Timestamps are app-managed UTC ISO text; "today" defaults use `APP_TZ`.
- `date` is the **due** date. `category='borrow'` (exact string, no synonyms)
  means she fronted the money and is owed it back: excluded from every household
  total, reported separately. Everything else counts as ordinary spending.
- Portal writes are attributed server-side from the link's label; a
  client-supplied author is ignored.
- Development branch: `claude/family-expenses-setup-8uvrks`.

## Current state (2026-08-11)

**This is live and in daily use by two people.** She was onboarded on
2026-08-11 and enters expenses from her phone; the owner works through the MCP
connector on claude.ai. Treat production data as real — it is.

- **v0.8.1**, Cloud Run revision `family-expenses-00011-j96`, 126 tests.
- Service `family-expenses` in `asia-southeast1`, Singapore Neon, at
  `https://family-expenses-bejtu5m47a-as.a.run.app`.
- Her live link is token id `4981964a048d` (label `wife`, never expires). An
  `owner` link also exists. Never print a token value into a transcript.
- Version history is `docs/CHANGELOG.md`; known-and-deferred problems are
  `docs/BACKLOG.md`. Read the backlog before "discovering" a bug.

### The portal has a login; `/mcp` does not — do not conflate them

Since v0.5.0 the **portal** sits behind Auth0 Universal Login (tenant
`work-os.jp.auth0.com`, allowlist `PORTAL_ALLOWED_EMAILS`). She depends on that
login now, so:
- rotating `SESSION_SECRET` signs her out;
- changing `AUTH0_DOMAIN`/`AUTH0_CLIENT_ID` forces her to re-authenticate;
- removing her from the allowlist locks her out silently.

`/mcp` is **untouched by all of that** — still open, still header-free,
`MCP_SECRET` still unset. §5.1's no-auth clause is about `/mcp`. Adding portal
login was not a contract breach; putting a header on `/mcp` would be.

### Two failure modes this project has actually hit

- **Edits that silently do not land.** Two string replacements this session
  reported success and changed nothing (a missing import, a `find()` status
  branch) — both passed the full suite because the affected path was
  short-circuited. Assert your anchors, and verify the change is in the file.
- **Handoffs smuggle things in.** A design pass arrived carrying an in-memory
  demo backend reachable at `if (!TOKEN) return demoApi(...)`. It never fired,
  but a ledger that silently accepts writes into a fake is the worst failure
  available here. Verify any external file against the constraints in
  `docs/BACKLOG.md` §1 and the portal tests before installing it.

Cross-model review (`/codex-verify`) found six real defects that all passed a
117-test suite. It is worth running on anything touching money.
