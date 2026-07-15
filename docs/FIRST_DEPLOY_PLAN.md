# First Deployment and Verification Plan

**Status:** in progress — Wave 1 implementation
**Prepared:** 2026-07-15
**Scope:** take the code-complete Family Expenses app from its current local
state to a verified first production deployment without breaking the
compatibility contract in `docs/FEATURE_CONTRACT.md` section 5.1.

The existing implementation plan is complete. This plan covers the remaining
production-hardening, infrastructure, deployment, and acceptance work.

## Current baseline

- Repository: public GitHub repository `Matt-Debate/Test`.
- Only branch: `claude/family-expenses-setup-8uvrks`, which is also the
  repository's current default branch. There is no `main` branch yet.
- Application version: 0.4.4 production-hardening release candidate.
- Local verification on 2026-07-15: `Ran 63 tests in 0.421s` and `OK`.
- Test run also emits an unclosed asyncio event-loop `ResourceWarning`; it is
  not a failing assertion, but should be removed before release so warnings do
  not hide future lifecycle defects.
- Nothing family-specific exists in GCP or Neon yet.
- Available operator access:
  - GitHub CLI is authenticated as `Matt-Debate`.
  - gcloud is authenticated as `matthewfarm@gmail.com` against project
    `work-dashboards`.
  - Cloud Run, Cloud Build, Secret Manager, Artifact Registry, and Container
    Registry APIs are enabled.
  - The Work Dashboards Neon API credential is valid and can create a separate
    project in the same Neon organization. The only current Neon project is
    `work_dashboards`.
  - Local `psql` is available; the local Docker daemon is not. Cloud Build
    will therefore be the authoritative container build test.

## Locked production choices

These choices become hard to change after onboarding and must be reviewed once
before the first deploy:

| Choice | Proposed value | Reason |
|---|---|---|
| GCP project | `work-dashboards` | Reuses the working operator identity and enabled APIs while keeping the family service, service account, secret, image, and database separate. |
| Cloud Run service | `family-expenses` | Contract and runbook name. Never rename after a link or connector is distributed. |
| Cloud Run region | `asia-southeast1` | Singapore colocates the service with the Neon database. Each portal/API request makes multiple serialized database round trips, so avoiding Tokyo↔Singapore latency outweighs the smaller difference in the household's client hop. Verified available in Cloud Run before implementation. Never change after onboarding. |
| Neon project | `family-expenses` | Structural separation from business data. |
| Neon region | `aws-ap-southeast-1` | Singapore is the available nearby Neon region and matches the organization's working deployment pattern. |
| App timezone | `Asia/Shanghai` | Required for natural-language meanings of "today". |
| MCP authentication | none | Preserve the explicit no-header compatibility contract. Do not create or set `MCP_SECRET`. |
| Portal token | never-expiring | Mint only after all pre-onboarding tests pass; revocation remains the kill switch. |

## Wave 1 — production-hardening implementation

Complete this wave before creating permanent production resources.

1. Fix stale Postgres connection recovery and production storage posture.
   - Evict a cached psycopg connection when it is already closed.
   - If the first statement in a transaction discovers a stale connection,
     reconnect and retry that statement once. Never replay after any statement
     has succeeded; partial transaction replay would violate atomicity.
   - If a mid-transaction connection error or rollback-on-dead-connection
     occurs, preserve the original exception and evict the cached corpse so
     the next request can recover.
   - When `K_SERVICE` is set, refuse startup unless `DATABASE_URL` is a
     `postgres://` or `postgresql://` URL. Never allow production to fall back
     to an ephemeral SQLite file.
   - Add tests for closed cached connection, stale first statement,
     rollback failure, and both missing/non-Postgres production URLs.

2. Fix FastMCP external-host handling.
   - Construct `FastMCP` with the runtime host (`HOST`, default
     `0.0.0.0`) instead of its localhost default.
   - Add a regression test that sends a realistic Cloud Run `Host` header to
     `/mcp` and completes an MCP initialize/tools-list exchange.
   - Preserve `/mcp`, stateless HTTP, and the no-auth default.
   - Rationale: the current suite logs `421 Misdirected Request` for a
     non-local host, so the present build would likely reject Cloud Run MCP
     traffic even though `/healthz` works.

3. Add a repeatable deployment script.
   - Pin project, service name, and region as explicit constants.
   - Refuse a dirty working tree unless an explicit override is supplied.
   - Resolve the Git commit SHA, pass it to manual Cloud Build as
     `--substitutions=COMMIT_SHA=<sha>`, and deploy the SHA-tagged image rather
     than `latest`.
   - Use the existing `gcr.io` Artifact Registry compatibility repository, or
     create a dedicated Tokyo Artifact Registry repository if the operator
     prefers. Do not mix image locations during the first release.
   - Deploy with `--allow-unauthenticated`, `--min-instances=0`, a small finite
     maximum instance count, `HOST=0.0.0.0`, and
     `APP_TZ=Asia/Shanghai`.
   - Bind `DATABASE_URL` from Secret Manager. Do not pass the Neon URI as a
     literal command-line environment variable and do not set `MCP_SECRET`.
   - Resolve and print the service URL after deployment, then fail if it is
     empty.

4. Add deployment-contract tests or static checks.
   - Manual builds must supply `COMMIT_SHA`; a missing value currently becomes
     an empty image tag.
   - The deploy target must remain `family-expenses` in
     `asia-northeast1`.
   - The deployed image must be SHA-pinned.
   - `DATABASE_URL` must come from the family secret.
   - `MCP_SECRET` must be absent.
   - The deployment script must not reference the Work Dashboards database
     secret.

5. Clean up release hygiene.
   - Resolve the asyncio lifecycle warning.
   - Correct stale deployment comments that still demonstrate setting
     `MCP_SECRET`.
   - Update the changelog, README, runbook, and version together. This is a
     patch release because it makes the already-documented public MCP actually
     reachable and hardens deployment without changing the user contract.

### Wave 1 test gate

Run in a clean Python 3.11 environment:

```bash
python3 -m unittest discover -s tests
```

Required result: zero failures, zero errors, no lifecycle warning, all
compatibility tests green, and the new external-host MCP test green. Also run
the deployment script in dry-run mode and inspect the literal commands for
project, region, service, SHA image, secret binding, and absence of
`MCP_SECRET`.

## Wave 2 — repository release preparation

1. Commit the reviewed Wave 1 implementation and synchronized docs on the
   current branch. (`AGENTS.md` was already introduced by `3bf22b7`; do not
   describe it as newly added.)
2. Create a pull request or review the complete diff against an empty `main`
   baseline; do not deploy an uncommitted tree.
3. Create `main` from the reviewed branch, make `main` the default, and retain
   the development branch until production verification is complete.
4. Rename GitHub repository `Test` to `family-expenses` and update the local
   remote. Confirm the old remote redirects before removing any fallback.
5. Tag the release only after the first production acceptance gate passes.
6. Confirm that the public-repository posture remains intentional. No Neon
   URI, portal token, Cloud Run secret, or generated link may appear in Git,
   shell history, build logs, screenshots, or issue text.

### Wave 2 test gate

- Clean `git status`.
- `origin` resolves to `Matt-Debate/family-expenses`.
- `main` contains the reviewed SHA.
- Secret scan over tracked files and Git diff returns no credentials.
- Full unit suite rerun from the exact commit to be built.

## Wave 3 — isolated Neon provisioning

1. Use the existing Neon API credential to create a new project named
   `family-expenses` in `aws-ap-southeast-1`; never reuse the
   `work_dashboards` project, branch, role, or connection string.
2. Wait for Neon operations to finish before connecting.
3. Retrieve a pooled connection URI for runtime and integration gates and a
   direct URI only for `psql` schema diagnostics if needed.
4. Create Secret Manager secret `family-expenses-database-url`, add the pooled
   URI as version 1, and grant Secret Accessor only to a dedicated
   `family-expenses` Cloud Run service account.
5. Do not copy the URI into repository `.env` files. For one-time local tests,
   inject it directly into the process from a protected temporary operator
   environment.

### Wave 3 database test gate

- Run `Database.init()` twice against Neon; both applications must succeed.
- Use `psql` to verify all three tables, constraints, and indexes.
- Run CRUD, mark-paid/unpaid, delete, token validation/revocation, and history
  reads through the pooled URI against the real Postgres project.
- Repeat the identical token-validation query shape at least six times through
  the pooled URI, crossing psycopg's default prepare threshold so pooler-only
  prepared-statement failures cannot hide behind a direct connection test.
- Force a history-write failure and verify the expense mutation rolls back.
- Verify `paid=true` without `paid_date` fails at the database constraint.
- Verify temporary smoke rows and tokens are deleted/revoked before moving on.
- Capture no connection URI in test output.

## Wave 4 — first Cloud Build and permanent service creation

This is the point where the service name and region become permanent.

1. Build the reviewed commit in Cloud Build with the explicit commit SHA.
2. Deploy the SHA-pinned image to service `family-expenses` in
   `asia-southeast1` using the dedicated service account and database secret.
3. Keep ingress public, leave `MCP_SECRET` unset, and set
   `APP_TZ=Asia/Shanghai`.
4. Record the assigned `run.app` URL in the operator runbook. Never replace it
   with a different service or region after onboarding.
5. Inspect the new revision, IAM policy, environment-variable names, secret
   binding, service account, traffic allocation, and logs. Do not print secret
   values.

### Wave 4 deployed-service test gate

Run `scripts/smoke_live.py` with the pooled Neon URI injected only into that
process. It must prove pooler compatibility, schema initialization, and the public portal/API flow,
then clean up its temporary expense and revoke its temporary token.

Add these checks beyond the current smoke script:

- `/health` returns 200 on a cold and warm request. Do not use `/healthz` for
  the public gate: Cloud Run reserves some paths ending in `z` and intercepts
  it before the container.
- Invalid portal token returns 404.
- Valid temporary portal link loads the Chinese UI.
- Add, list, edit, mark paid, unmark paid, history, and delete all work through
  the deployed API.
- A real MCP client completes initialize, `tools/list` (exactly 9 tools), and
  `prompts/list` (exactly 3 prompts) through the public `/mcp` URL.
- MCP conversational flows work in Chinese and English: add a spoken amount,
  list unpaid, fuzzy mark-paid, correction, ambiguity with candidates, history,
  and cleanup.
- Cloud Run logs show no startup, schema, host-header, or database connection
  errors.
- Neon contains no leftover `[smoke]` expenses or active smoke tokens.

## Wave 5 — phone, browser, and compatibility acceptance

Before creating the real family link:

1. Test the portal at common phone widths in Safari/Chrome or Playwright:
   Chinese default, English toggle, keyboard behavior, amount/date entry,
   inline edit, filters, totals, paid-date flow, history, errors, and reload.
2. Use a temporary never-expiring portal token and a test MCP connection.
3. Redeploy the same reviewed image to the same service and region, creating a
   second revision.
4. Verify the exact same temporary portal URL still works and the MCP client
   reconnects with the same `/mcp` URL and no header. This is the practical A8
   compatibility drill.
5. Test scale-to-zero recovery or an equivalent long-idle database reconnect;
   the first request after idle may be slower but must not require user action.

Required acceptance evidence:

- A1: phone add → list → edit → mark-paid passes.
- A2/A4/A5: atomic history, paid-date constraint, and token rejection pass on
  real Postgres and the deployed API.
- A3/A7: public MCP handshake and natural-language flows pass from at least one
  real client.
- A6: SQLite suite remains green and Postgres schema/integration tests pass.
- A8: same portal link and MCP URL survive a new Cloud Run revision without
  reconfiguration.

## Wave 6 — onboarding and release

Only after all prior gates pass:

1. Mint the real never-expiring `wife` link directly against the production
   Neon database.
2. Send it privately over WeChat, open it on her phone, bookmark it, and add it
   to the home screen if useful.
3. Have her perform one real add/edit/mark-paid flow while the operator checks
   the history and totals.
4. Connect the owner's Claude and/or ChatGPT client to the same permanent
   `<service-url>/mcp` endpoint with no authorization header. Verify a read and
   a reversible write. ChatGPT custom MCP availability and write permissions
   depend on workspace plan and admin settings, so treat ChatGPT as an
   additional client acceptance test rather than the only launch gate.
5. Mark the release in the changelog, tag it, and archive this plan or change
   its status to complete with the deployed revision, commit SHA, service URL,
   Neon project ID, and test summaries. Never record the database URI or portal
   token.

## Operations after launch

- Keep the Cloud Run service name, region, `/mcp` path, no-auth posture, and
  real portal token unchanged.
- Use Neon's restore capability as the primary backup path; record the actual
  restore window for the selected plan and perform a non-destructive recovery
  rehearsal before relying on it.
- Create a small GCP budget alert and periodically inspect Cloud Run error
  logs, revision count, and Neon usage.
- Keep rollback image-based: deploy a previously verified SHA to the same
  service. A rollback must never create a second service or change region.
- For schema changes, take a Neon branch/restore point first. Introduce dated
  migrations before the first breaking change.
- Never solve an incident by revoking the real portal token or enabling
  `MCP_SECRET` unless the owner explicitly accepts the resulting family
  reconfiguration.

## Stop conditions

Do not onboard the family member if any of these remains true:

- MCP returns 421 for a public host or cannot complete a real handshake.
- The Postgres integration test or cleanup fails.
- The image is not traceable to a clean Git SHA.
- Cloud Run uses the wrong project, service name, or region.
- The Work Dashboards database or secret is referenced anywhere.
- `MCP_SECRET` is present.
- A secret or real portal token appears in source control or logs generated by
  the release process.
- The compatibility redeploy drill fails.
- A closed or long-idle pooled Postgres connection requires a second user
  request, service restart, or any manual recovery.
- Cloud Run can boot without a Postgres `DATABASE_URL` secret binding.
