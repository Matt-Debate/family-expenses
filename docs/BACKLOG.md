# Backlog — deferred work

Known, deliberately-not-done items. Nothing here blocks daily use — the ledger
is live and in use by two people since 2026-08-11. Each entry says why it was
deferred, so a future session can judge whether that reasoning still holds.

**Cleared 2026-08-11 (v0.9.0):** every code defect that was filed here is fixed
with a regression test, and the two operational unknowns turned out to need no
action. See `docs/CHANGELOG.md` [0.9.0] for what each one actually cost. What
survives below is one item the owner deliberately deferred, one found by the
review of that release, and one that cannot be verified from inside this repo.

---

## 1. Move the portal to its own Auth0 tenant

**Filed 2026-08-11. Deferred by the owner the same day** ("forget the logo and
app name, file it as a long term improvement"). Priority: low. Do not do this
without asking — it is the one item here that costs her something.

**Symptom.** The portal login page shows the WorkOS logo and labeling, which is
wrong for a household expense app.

**Why it can't just be fixed.** Branding is tenant-level. `/branding` on
`work-os.jp.auth0.com` carries `logo_url: https://matt-sd.netlify.app/work-os-logo.png`
and is shared by every application in the tenant — changing it would rebrand the
WorkOS admin login too. The tenant `friendly_name` behaves the same way, which is
where any remaining "WorkOS" *text* comes from. The only isolated lever is the
application's own `logo_uri`, and the tenant is on New Universal Login, which does
not reliably honor per-application logos.

**The real reason to do it.** The contract calls this project's isolation from
`work-dashboards` "structural (own repo, own database, own services)". Adding
portal OAuth in v0.5.0 put a shared **Auth0 tenant** underneath both. (The GCP
project is shared too — `work-dashboards`, 693424932326 — which the contract
never claimed otherwise, but it is worth knowing when reasoning about
isolation.) Branding is the visible symptom; the coupling is the actual issue.

**What it involves.** New tenant (e.g. `family-expenses.jp.auth0.com`), new
Regular Web Application, recreate the one user, own branding, then swap
`AUTH0_DOMAIN` / `AUTH0_CLIENT_ID` and the client-secret Secret Manager entry.
No application code changes — `app/auth.py` reads all of it from env.

**A8 cost — the free window has closed.** Changing tenants invalidates existing
sessions: anyone signed in is signed out once and must log in again against the
new tenant. This was free before onboarding. **She was onboarded on 2026-08-11**
and is now using the portal daily, so doing this costs her one forced re-login
with a new password — exactly the friction §5.1 exists to prevent. Not fatal,
but it must be scheduled and explained to her rather than done quietly. Weigh it
against the fact that the only symptom is a logo.

---

## 2. MCP tool calls still block the event loop

**Filed 2026-08-11**, during the adversarial review of v0.9.0. Priority: low at
two users; it is the unfinished half of a fix that shipped.

v0.9.0 moved the `/api/*` handlers off the event loop with `run_in_threadpool`.
The ten MCP tools in `app/mcp_server.py` are all plain `def` and were left alone
on the strength of a claim — written in the old backlog — that "FastMCP uses a
threadpool". **That claim is false.** A reviewer read the installed `mcp` 1.26.0:
`FuncMetadata.call_fn_with_arg_validation` ends in `return fn(**arguments)` for
synchronous functions, with no `to_thread`, and confirmed it empirically by
observing `Store.list` execute on `MainThread` during an `expenses_list` call.

**Consequence.** An owner MCP query against a cold Neon compute blocks the loop
for the whole round trip, stalling every concurrent `/api/*` request and
`/health` — the exact failure the API-side fix was meant to remove. At two users
this is a latency wart, not an outage.

**Fix.** Make each tool `async def` and `await run_in_threadpool(...)` around the
store calls, or wrap the bodies in `anyio.to_thread.run_sync`. Ten small edits;
none of them touch the `/mcp` mount path, its no-auth posture, or any tool
signature, so §5.1 is not engaged. Worth doing next time the MCP surface is open
for other reasons rather than on its own.

## 3. The class tracker has no audit trail, and no way to retire a course

**Filed 2026-08-11** during the review of v0.10.0. Priority: low, but it is a
real asymmetry with how the rest of this app treats money.

Every expense mutation writes an `expense_history` row in the same transaction.
Class packages write none: `create_package`, `update_package`, `delete_package`,
`log_class` and `delete_class_event` leave no record, and there is no
`class_history` table. `class_events` is the log of what happened in the
classes, not of who edited the tracker. Changing `class_count` — which divides
the money — is unrecorded, and deleting a package destroys a term of attendance
with nothing remembering it.

Related, from the same review:
- **`archived` is unreachable.** It is threaded through the store, the API and
  `classes_list(include_archived=…)`, but nothing can set it: the portal never
  calls `/api/classes-update` and there is no MCP tool. Retiring a finished
  course therefore means deleting it, which destroys the log. Either wire up
  archiving or drop the flag.
- **No `classes_delete` / `classes_update` MCP tool.** The owner works through
  MCP, so removing a course is portal-only. The delete-refusal on a funding
  payment now says "from the Classes tab in the portal" rather than naming a
  tool that does not exist, but it is still a dead end from the MCP side.
- **Server error strings are English only.** The portal surfaces them verbatim
  in a toast, and its primary user reads the Chinese UI. True of every error in
  the app, not just the class ones, which is why it is filed rather than
  half-fixed here.
- **A borrow-funded package is coherent but unexplained.** Nothing stops a
  package being funded by a `category='borrow'` row, so the same ¥2,200 can read
  as "owed back to me" on the Due tab and "remaining" on the Classes tab. No
  total is corrupted — `Store.summarize` is untouched — but the two views
  describe one payment two ways with nothing reconciling them.

## 4. Neon's restore window has never been recorded or rehearsed

**Filed 2026-08-11.** Priority: medium — this is the only item here that could
cost real data.

`FIRST_DEPLOY_PLAN.md` "Operations after launch" calls for confirming the
point-in-time-restore window and rehearsing a restore. Neither happened.
Backups are currently an assumption, not a verified capability, and this is
19 rows of real money with an append-only audit trail that only exists in one
place.

**Why it is still open.** It cannot be checked from inside this repo: there is
no Neon CLI installed and no API key in the environment, so the retention window
is only visible in the Neon console. Rehearsing a restore also creates a Neon
branch — an action against live infrastructure that needs the owner's
go-ahead rather than an agent's initiative.

**What to do, in order.**
1. Read the retention window: Neon console → project → Settings → *Restore
   window* (free tier has historically been 24h; paid tiers 7–30 days). Record
   the actual number here.
2. Rehearse: create a branch from a timestamp ~1 hour ago, point a throwaway
   `DATABASE_URL` at it, and run
   `python3 -c "import os,psycopg;print(psycopg.connect(os.environ['DATABASE_URL']).execute('select count(*) from expenses').fetchone())"`.
   Delete the branch afterwards. Nothing touches the primary.
3. A cheaper standing backstop, if the window turns out to be short: a periodic
   `pg_dump` to local storage. Note that the dump contains real household
   financial data and every live portal token — treat it like a secret (P9).
