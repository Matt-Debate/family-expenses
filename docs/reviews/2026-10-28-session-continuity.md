# Family Expenses session continuity — 28 October 2026

Status: deployed and live-verified as family-expenses-00019-hln, 100% traffic.

## Release identity

- Production before this work: `family-expenses-00017-ktm`, serving 100% of traffic.
- Source baseline: `6fa146879469a45bc6c63b15a5b6615acb9e4a04` (the deployed OAuth implementation plus rollout documentation).
- Fix commit: `16248cd3eda8aa13b49821b494e6bd9bf67a90f6`.
- Build: `5a26d14b-922f-4e69-95c8-6be8cab91daa`, SUCCESS.
- Built image: `gcr.io/work-dashboards/family-expenses@sha256:6591076f8c683e257abe55083c7f4aaaf7d324bc424a6ee758f8912fd5740b2a`.
- Original checkout's unrelated/uncommitted work was preserved. No merge or push was performed.

## Observed production state

The portal `/login` response issued `Max-Age=1209600` (14 days). The original
checkout contained a separate uncommitted 400-day change, but that change was
absent from the deployed OAuth revision. This is verified from a live header;
no cookie or portal-token value is retained here.

The Auth0 Family Expenses API has offline access enabled. Its existing shared
client allows authorization-code and refresh-token grants and has non-expiring,
non-rotating refresh tokens, with infinite absolute and idle lifetime flags.
Access tokens remain 900 seconds. Exact-Family-Expenses-audience refresh events
succeeded on 28 October, including 11:12:09 UTC (19:12:09 Asia/Shanghai), with
`offline_access`. Connected MCP reads and the user-requested class-package
operation also succeeded. No Auth0 client, tenant, grant or token policy changed.

The signing-key outage issue is a demonstrated source defect: the old code
returned an invalid-token 401 when Auth0's key fetch failed. Available logs do
not establish that this defect caused the user's reported sign-in prompt.

## Verification

- New portal regressions failed against the old 14-day behavior.
- The changed HTTP outage regression failed with 401 instead of 503 before the fix.
- Focused portal/auth tests passed after the fix.
- Full offline suite: 523 tests passed under the existing local environment.
- Full offline suite with exact `requirements.lock` dependencies: 523 tests passed.
- Cloud Build succeeded with the pinned Docker base and dependency lock.
- `git diff --check` passed before commit; the source checkout was clean at build.
- No production test expenses, attendance or links were created by validation.

## Deployment and live acceptance plan

Only update the existing service image to the digest above, in project
`work-dashboards`, region `asia-southeast1`. Preserve environment variables,
secret bindings (especially `SESSION_SECRET`), service identity, member policy,
portal links, URL and database. Verify the new ready revision and traffic,
`/health`, public OAuth metadata, anonymous/invalid-token rejection, and the
400-day cookie attribute. Then verify a read through the existing authenticated
Family Expenses plugin. Do not provoke a provider outage in production or
create an expense/link as a smoke test.

Backend policy cannot guarantee storage by a browser or the connector. Cookie
clearing, explicit logout, revocation or loss of the connector's refresh token
can still require sign-in. Temporary signing-key failures now deny execution
with 503 and no authentication challenge; they never permit stale-key access.


## Live acceptance correction

The initial image deployed as `family-expenses-00018-4kc` with 100% traffic.
Service URL and the complete runtime specification except image were identical;
environment variables and secret references were unchanged. Live `/login`
issued 400-day cookies, health/discovery returned 200, and anonymous/invalid MCP
tokens were rejected with 401. The existing authenticated plugin still worked.

A second request carrying the login-state cookie did NOT receive a renewed
cookie. Pinned Starlette 1.7 persists only modified sessions; the original
regression modified the session on each visit. The regression now uses a
read-only second visit and failed against the first deployed fix. v0.14.2 wraps
the validated session with a mapping touch before routing, so normal reads
renew it too. Focused tests and all 523 tests under the pinned runtime pass.
The owner's existing deployment approval covers this correction to the same fix.


## Completed production acceptance

Owner approved deployment explicitly in this chat on 28 October 2026.
Final source: `473582df34f023cf74d11a3ef8645f854074e497` (v0.14.2).
Build `40516bfa-fb2a-49af-85b5-84b83d118cc0` succeeded; image
`gcr.io/work-dashboards/family-expenses@sha256:02078af05206e7a9040cd0676aedb68163147ffe2adfb8f6e5329e8ddbab7176`
is live as revision `family-expenses-00019-hln`, 100% traffic.

A live `/login` cookie was reused on a read-only `/health` request: HTTP 200
and a renewed `Max-Age=34560000` cookie confirmed actual rolling behavior.
The complete runtime specification except image remains unchanged from revision
00017, including environment and session/member/database secret references.
The existing authenticated Family Expenses connector works after deployment.
No production outage was induced; outage denial/recovery is covered offline.
The owner-approved attendance correction was separately verified through the MCP,
with the original records retained in the ledger audit trail.
