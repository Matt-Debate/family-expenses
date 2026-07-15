# AGENTS.md — Family Expenses

This repository is one household's expense portal and MCP server. A family
member adds and edits expenses from her phone at `/t/<token>`; the owner
queries and manages them through MCP clients such as Claude and ChatGPT.
This is not a business system. The owner's `work-dashboards` repository is
reference-only: never commit changes to it while working on this project.

## Repository identity and companion routing

The stable local path for this repository is
`/Users/matthewalanfarmer/family-expenses`. It currently resolves to the
legacy checkout `/Users/matthewalanfarmer/Test`; treat both paths as the same
repository. The companion Work Dashboards checkout is
`/Users/matthewalanfarmer/work_dashboards` and has its own `AGENTS.md` and
`CLAUDE.md`.

The user may operate on both repositories in one conversation. Route Family
Expenses portal, `/t/<token>`, `expenses_*` MCP, and household-deployment work
here. Route Work Dashboards product work to the companion checkout. If a
referenced commit or file is absent from the current checkout, check the
companion repository before reporting it missing. Never mix changes from the
two projects in one commit.

## Highest-priority compatibility contract

No change may force the family member to reconnect, re-authenticate, or
reconfigure anything. See `docs/FEATURE_CONTRACT.md` section 5.1, criterion
A8. Avoiding friction and disuse is more important than guarding against
ledger abuse.

Once the service is connected, these values are frozen:

- Cloud Run service name and region, because they determine URL stability.
- The family member's `/t/<token>` link and token. Tokens never expire and
  must never be casually revoked.
- The `/mcp` mount path.
- The no-auth posture: leave `MCP_SECRET` unset.

`CompatibilityContractTests` enforce the parts that can be tested. Preserve
the remaining operational constraints manually. The portal UI, MCP tools,
documentation, and additive schema changes may evolve freely.

## Before changing MCP behavior

Read `docs/MCP_DESIGN.md` in full before editing the MCP server, MCP tools,
prompts, annotations, or agent-facing behavior.

Put essential agent guidance only in channels clients reliably expose:

- Tool descriptions, including bilingual trigger phrases and cross-references.
- Tool results, including `note`, candidates, and hints.
- Error strings that coach the caller toward recovery.
- Annotations.

Server `instructions` may reinforce guidance, but must never be its only
location. `AgentErgonomicsTests` detect guidance that drifts outside reliable
channels.

## Development workflow

- Work on `claude/family-expenses-setup-8uvrks` unless the user explicitly
  requests another branch.
- Inspect the existing implementation and relevant documentation before
  editing. Preserve unrelated user changes in a dirty worktree.
- Keep the test suite runnable without databases or other external services.
- Run the focused tests for the code being changed, then run the full suite
  before handing off when practical.
- For every behavior change, update the semantic version and add an entry to
  `docs/CHANGELOG.md`. Update the feature contract and runbook in the same
  change when behavior or operations affect them.
- Do not deploy, rotate or revoke tokens, mint a live family link, rename the
  repository, merge branches, or change permanent Cloud Run settings unless
  the user explicitly asks for that operation.

## Commands

```bash
python3 -m unittest discover -s tests
python3 -m app.main
python3 scripts/mint_link.py --label X --base-url URL
DATABASE_URL=postgres://... python3 scripts/smoke_live.py --base-url URL
```

The test suite uses SQLite and requires no database server. The local app is
available at `http://localhost:8080`.

## Repository map

| Path | Responsibility |
|---|---|
| `app/store.py` | All reads and writes. Every mutation must write an `expense_history` row in the same transaction. Also owns token mint, validation, and revocation. |
| `app/db.py` | Portable Postgres/SQLite layer. `DATABASE_URL` selects Postgres; tests use a shared locked SQLite connection. Both drivers use `:name` parameters. |
| `app/web.py`, `app/api.py`, `app/portal.html` | Bilingual `/t/<token>` portal and `POST /api/*`; revalidate the token on every request. |
| `app/mcp_server.py` | Nine tools, the 记账/对账/修复 persona prompts, and optional bearer middleware. |
| `app/main.py` | Hosts the portal, API, and `/mcp` in one service. Environment: `DATABASE_URL`, `APP_TZ`, `MCP_SECRET`, and `PORT`. Default timezone is Asia/Shanghai; leave `MCP_SECRET` unset. |
| `db/schema.sql` | Portable idempotent startup DDL. The first breaking schema change must introduce dated migration files. |
| `docs/` | Feature contract, MCP design, runbook, and semantic-versioned changelog. |

## Data and schema invariants

- Every expense mutation and its `expense_history` record belong in the same
  transaction.
- Revalidate portal tokens on every API request.
- Store timestamps as application-managed UTC ISO text.
- Resolve default meanings of "today" using `APP_TZ`, which defaults to
  `Asia/Shanghai`.
- Keep startup DDL portable and idempotent across Postgres and SQLite.
- Additive schema changes are allowed. Begin dated migrations before the
  first breaking schema change.

## Deployment status

As of 2026-07-15, version 0.4.4 is being prepared for first deployment. The
one-time deployment checklist remains in `CLAUDE.md`; consult it before any
deployment work. In particular, the Cloud Run service name and region become
permanent compatibility constraints, production must remain unauthenticated,
and live verification must exercise the real Postgres schema and public URL.
