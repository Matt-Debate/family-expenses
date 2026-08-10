# Backlog — deferred work

Known, deliberately-not-done items. Nothing here blocks onboarding. Each entry
says why it was deferred, so a future session can judge whether that still holds.

---

## 1. Move the portal to its own Auth0 tenant

**Filed 2026-08-11.** Priority: low, but do it *before* she is onboarded if it is
going to happen at all (see the A8 cost below).

**Symptom.** The portal login page shows the WorkOS logo and labeling, which is
wrong for a household expense app.

**Why it can't just be fixed.** Branding is tenant-level. `/branding` on
`work-os.jp.auth0.com` carries `logo_url: https://matt-sd.netlify.app/work-os-logo.png`
and is shared by every application in the tenant — changing it would rebrand the
WorkOS admin login too. The tenant `friendly_name` behaves the same way, which is
where any remaining "WorkOS" *text* comes from. The only isolated lever is the
application's own `logo_uri`, and the tenant is on New Universal Login, which does
not reliably honor per-application logos.

**The real reason to do it.** `CLAUDE.md` states this project is "deliberately
unrelated to the owner's `work-dashboards` repo" and that isolation is
"structural (own repo, own database, own services)". Adding portal OAuth in
v0.5.0 put a shared **Auth0 tenant** underneath both — the first shared
dependency between them. Branding is the visible symptom; the coupling is the
actual issue. A separate tenant restores the stated isolation and makes the
branding question disappear on its own.

**What it involves.** New tenant (e.g. `family-expenses.jp.auth0.com`), new
Regular Web Application, recreate the one user, own branding, then swap
`AUTH0_DOMAIN` / `AUTH0_CLIENT_ID` and the client-secret Secret Manager entry.
No application code changes — `app/auth.py` reads all of it from env.

**A8 cost, and why timing matters.** Changing tenants invalidates existing
sessions: anyone signed in gets signed out once and must log in again against the
new tenant. Before onboarding that costs nothing. After she is connected it is a
one-time re-login for her — survivable, but it is exactly the kind of friction
§5.1 exists to prevent, so it stops being free.

---

## 2. Correctness items found in the 2026-08-10 audit, still unfixed

Reported and verified during the audit; none were repaired.

- **`expenses_list` silently drops `since`/`until` when `query` is set**
  (`app/mcp_server.py`). `store.find` has no date-range support, so
  `expenses_list(query='足球', since='2026-08-01')` returns *all time* with no
  error. Wrong answers on the surface an agent trusts most. Highest-value fix here.
- **`store.find` builds its LIKE pattern without escaping `%`/`_`**, so
  `query="%"` matches every row; it also ignores an invalid `status` that
  `store.list` would reject — same argument, different behavior depending on
  whether `query` was passed.
- **Portal DB calls block the event loop** (`app/web.py`). Handlers are
  `async def` but call the store synchronously; `/api/list` is three sequential
  blocking transactions. MCP is unaffected (FastMCP uses a threadpool).
- **`APP_TZ` is unpinned by any test.** Every date assertion is a shape-only
  regex, and `today_str()` falls back to UTC via a bare `except`. On an image
  missing `tzdata` a China household silently gets the wrong day after 08:00 CST
  with the suite green.
- **Bare `KeyError` reaches MCP callers** as just the expense id
  (`app/store.py`). The HTTP path translates it to a clean 404; the MCP path has
  no equivalent, contradicting the "every error string is coaching" design.
- **`expenses_add(paid=true)` spans two transactions** — `create` then
  `mark_paid`. If the second fails the row persists as unpaid while the agent
  reports failure, breaking the same-transaction history invariant at the tool
  boundary.
- **`expense_history.seq` has no uniqueness constraint** and is computed as a
  `COUNT(*)` inside the transaction. Concurrent deletes could duplicate a `seq`.
  Very unlikely at household scale; a `UNIQUE(expense_id, seq)` would turn silent
  corruption into a loud error.

## 3. Operations

- **`maxScale` disagrees between levels** — the serving revision carries
  `autoscaling.knative.dev/maxScale=3`, the service carries
  `run.googleapis.com/maxScale=20`. Which governs was not determined.
- **No GCP budget alert.** `FIRST_DEPLOY_PLAN.md` "Operations after launch"
  calls for one; it was never created.
- **Neon restore window never recorded or rehearsed**, also called for in the
  same section. Backups are currently an assumption, not a verified capability.

## 4. Doc drift

- **`docs/IMPLEMENTATION_PLAN.md` is frozen at v0.2.0** — says "five tools"
  (there are 10) and references `/healthz` as the health endpoint. The contract
  claims this file stays in sync; it does not.
- **`CHANGELOG.md` `[Unreleased]`** still holds work that shipped in 0.4.2–0.4.4.
  Left alone deliberately rather than guess which release each item belongs to.
