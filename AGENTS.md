# AGENTS.md — Family Expenses

One household's expense ledger: she adds and edits expenses from her phone at
`/t/<token>`; the owner queries and manages them through MCP clients such as
Claude and ChatGPT. Not a business system.

**Policies, the read-this-first routing table, commands, and the repository map
live in [`CLAUDE.md`](CLAUDE.md). Read it — it applies to every agent, not just
Claude.** This file carries only what is specific to working across the two
checkouts. Duplicating the policies here is what let them drift apart before;
if something belongs in both, it belongs in `CLAUDE.md` alone.

## Repository identity and companion routing

The stable local path is `/Users/matthewalanfarmer/family-expenses`, currently a
symlink to the legacy checkout `/Users/matthewalanfarmer/Test`. Treat both paths
as the same repository.

The companion Work Dashboards checkout is `/Users/matthewalanfarmer/work_dashboards`
and has its own `AGENTS.md` and `CLAUDE.md`. It is **reference-only** from here:
read its patterns, never commit to it from this project's sessions.

The user may work on both in one conversation. Route Family Expenses portal,
`/t/<token>`, `expenses_*` MCP, and household-deployment work here; route Work
Dashboards product work to the companion checkout. If a referenced commit or
file is absent from the current checkout, check the companion before reporting it
missing. Never mix changes from the two projects in one commit.

## Development workflow

- Work on `claude/family-expenses-setup-8uvrks` unless asked otherwise; it is
  kept in lockstep with `main`.
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
