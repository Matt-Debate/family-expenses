# Runbook — Family Expenses

Operator guide: deploy, mint the household link, connect MCP clients, rotate.

Production service: `family-expenses` in `asia-southeast1` at
`https://family-expenses-bejtu5m47a-as.a.run.app`. Keep this service, region,
and URL stable after onboarding.

## 1. One-time setup

### Database (Neon)
Create a **separate** Neon project/database for family data (keeps it fully
apart from any business database). Copy the connection string
(`postgres://…`). The schema applies itself idempotently at service startup —
no manual migration step for v1.

### Deploy to Cloud Run
```bash
# after creating the dedicated service account and Secret Manager binding
scripts/deploy.sh --dry-run
scripts/deploy.sh
```
The script permanently pins service `family-expenses` in
`asia-southeast1` (Singapore, colocated with Neon), builds and deploys the
current clean Git SHA, and binds `DATABASE_URL` from
`family-expenses-database-url`. Production refuses to boot on SQLite.
MCP OAuth is mandatory in this implementation. Production will not start until
§9 is configured. The owner-approved cutover completed on 2026-09-30 to
revision `family-expenses-00017-ktm`. Anonymous MCP requests are denied;
old no-auth clients require deliberate OAuth reconnection (see §9.5).
`APP_TZ` stays unchanged. Keep service name, region, URL, `/mcp`, all portal
paths and live links stable. Preserve the existing portal Auth0 client,
email/password database connection, allowlist and SESSION_SECRET binding.

The updated deploy script uses `--update-env-vars` / `--update-secrets` to
preserve existing configuration. It is a **cutover/update** script for the
existing service, not a bootstrap script. It requires an explicitly approved,
pinned `MCP_MEMBERS_SECRET_VERSION`; a dry run shows a placeholder if omitted.
Never execute deployment without owner approval and §9 readiness evidence.

## 2. Mint the household link

Via MCP (from Claude, after §3): *"make a link for my wife"* →
`expenses_mint_link(label="wife")` — never expires.

Or via CLI against the same database:
```bash
DATABASE_URL='postgres://…' python3 scripts/mint_link.py \
  --label wife --base-url https://<service-url>
```
Send the printed `https://<service-url>/t/<token>` link over WeChat; she
bookmarks it. That's her entire onboarding — no account, no password, nothing
to renew, ever.

## 3. Connect MCP clients

After the approved OAuth cutover, create/reconnect an **OAuth** connection to
`https://family-expenses-bejtu5m47a-as.a.run.app/mcp`. Follow §9 for the
pre-registered client ID and secret and the exact callback URI. Each household
member signs in with the existing Auth0 email/password account and completes
consent. An old no-auth connection must be replaced or reconfigured deliberately.
Do not paste bearer tokens into chat or add a static MCP_SECRET header.

Tools (18): `expenses_help`, `expenses_list`, `expenses_add`, `expenses_update`,
`expenses_mark_paid`, `expenses_delete`, `expenses_refund`,
`expenses_refund_delete`, `expenses_history`, `expenses_mint_link`,
`expenses_revoke_link`, `expenses_list_links`, `classes_list`, `classes_add`,
`classes_update`, `classes_delete`, `classes_log`, `classes_log_delete`
(design: `docs/MCP_DESIGN.md`).

`expenses_mint_link` returns the full `https://<service-url>/t/<token>` link
because the service knows its own host from `PORTAL_BASE_URL` (§8) — the same
value the Auth0 redirect uses, set by `scripts/deploy.sh`. If it is unset the
tool says so instead of printing a placeholder that looks like a URL.

**Personas** (appear as prompt templates in Claude apps; optional):
记账 `jizhang` = dictate expenses; 对账 `duizhang` = walk the unpaid list and
check off; 修复 `xiufu` = find and fix a wrong entry. The tools alone handle
cold requests — personas just set the tone and workflow.

## 4. What you (or she) can say to it

The tools are built for casual speech — fuzzy matching by description, dates
defaulting to today (China time), amounts tolerating ¥/块/元/commas. All of
these work as single utterances, Chinese or English:

| say | happens |
|---|---|
| 我还要付什么？/ what do I owe? | lists unpaid + total |
| 足球课300块 / football class 300 | adds it, dated today |
| 足球课付了 / paid the football class | marks it paid today (prefers the unpaid match) |
| 钢琴课改成350 / change piano to 350 | edits the amount |
| 删掉游泳课 / delete swim class | deletes (client confirms first; audit row kept) |
| 这个月花了多少？/ totals? | summary |
| 退了1800 / they refunded us 1800 | records a refund against the paid row — the row keeps its ¥3,600; totals read ¥1,800; a course on it can be resized in the same call |
| 课时改成5 / the pack is 5 classes now | edits the course, never its money; refuses to shrink below the classes already logged |
| 这个课结束了 / archive the course | retires it (kept, hidden from lists) |
| 给我老婆做个链接 / make a link for my wife | mints a never-expiring portal link and returns the full URL |

If a phrase matches several expenses, the tool returns the candidates and the
assistant asks which one — nothing is guessed silently.

## 5. Rotate / revoke

- Lost or leaked link: `expenses_revoke_link` (or
  `scripts/mint_link.py --revoke <token-or-id>`), then mint a new one.
- MCP access: remove the exact subject from `MCP_MEMBERS_JSON` and deploy an
  approved new pinned policy version, or revoke the relevant Auth0 permission.
  Server policy changes deny future requests immediately on the new revision;
  Auth0 role changes affect newly issued tokens, so existing access tokens can
  remain valid until expiry. For urgent removal use local subject policy as well.
  Do not revoke a portal link as a side effect of removing MCP access.
- Inspect links: `scripts/mint_link.py --list` (shows label, expiry, usage).

## 6. Operations notes

- **Backups:** rely on Neon's point-in-time restore; the `expense_history`
  table is additionally an application-level audit of every change.
- **Logs:** Cloud Run request logs; the app logs via uvicorn.
- **Health:** use `GET /health` in Cloud Run checks. `/healthz` is retained
  locally, but Google's front end reserves some paths ending in `z`.
- **Scale:** min-instances=0 is fine (stateless HTTP MCP; Neon serverless).
  Cold starts of a couple seconds are acceptable for this use.
- **Schema changes:** `db/schema.sql` applies idempotently at startup, and
  additive changes go there. **One in-place change exists** (v0.13.0): the
  CHECK on `expense_history.action` had to be widened for refund and
  class-tracker actions, which `CREATE TABLE IF NOT EXISTS` cannot do.
  `Database._migrate_history_actions()` does it at startup, driven by
  inspecting the live constraint (so it runs once and is a no-op after),
  best-effort like the hardening file. If it cannot apply, the startup log
  carries one line starting `WARNING: expense_history action migration did
  not apply`, and the store **refuses every write that needs a new action**
  — refunds, course edits, **and her class logging** (which worked before
  v0.13.0) — with a message that opens in her language and names this section
  after it, rather than half-writing. `scripts/smoke_live.py` gates on it by
  name. To check by hand against production (secret piped, never printed —
  P9):
  ```bash
  DATABASE_URL="$(gcloud secrets versions access latest --secret=family-expenses-database-url --project=work-dashboards)" \
    python3 -c "from app.db import Database; print(Database().history_actions_missing())"
  ```
  `[]` means every action is allowed. A non-empty list means the ALTER has to
  be run by hand (`ALTER TABLE expense_history DROP CONSTRAINT
  expense_history_action_check; ALTER TABLE expense_history ADD CONSTRAINT
  expense_history_action_check CHECK (action IN (…))` with the list from
  `app/models.py HISTORY_ACTIONS`), then restart. **A second one** (v0.13.1)
  adds three nullable columns to `expense_refunds` with plain `ADD COLUMN`;
  check it the same way with `Database().refund_columns_missing()` and, if
  needed, `ALTER TABLE expense_refunds ADD COLUMN <name> <type>` for each
  name it lists (`Database.REFUND_COLUMNS` has the types). Anything beyond
  these should introduce dated migration files.

## 7. Local development

```bash
pip install -r requirements.lock
python3 -m unittest discover -s tests        # 519 tests, sqlite, no server
python3 scripts/mint_link.py --label dev     # local sqlite file
python3 -m app.main                          # http://localhost:8080
```

## 8. Portal OAuth (optional, off by default)

The portal can sit behind Auth0 Universal Login, layered on top of `/t/<token>`
— the token still selects the ledger, Auth0 establishes who is asking. Pattern
copied from work-dashboards (same JP tenant), but server-side session rather
than the SPA library: no build step here, and a CDN script tag is unreliable
from mainland China.

**It is inert until all four vars are set.** Deploy freely with them unset.

1. In Auth0 (`work-os.jp.auth0.com`), create a **Regular Web Application**
   (not SPA — this is a confidential client with a secret).
2. Set **Allowed Callback URLs** to `https://<service-url>/callback` and
   **Allowed Logout URLs** to `https://<service-url>`. Auth0 matches these
   exactly; a trailing-slash mismatch is the usual first failure.
3. Put the client secret in Secret Manager; never a literal env var.
4. Deploy with:
   ```
   AUTH0_DOMAIN=work-os.jp.auth0.com
   AUTH0_CLIENT_ID=<client id>
   AUTH0_CLIENT_SECRET=<from Secret Manager>
   SESSION_SECRET=<random 32+ bytes>
   PORTAL_ALLOWED_EMAILS=her@example.com,you@example.com
   PORTAL_BASE_URL=https://<service-url>
   ```

Notes that bite:
- `PORTAL_ALLOWED_EMAILS` empty = **nobody** gets in. Auth0 authenticates
  anyone who can create an account, so the allowlist is the real gate.
- `PORTAL_BASE_URL` avoids a redirect_uri built as `http://` behind Cloud Run's
  TLS termination, which Auth0 rejects.
- Rotating `SESSION_SECRET` signs everyone out.
- **Do not** use a Google social connection if she is in mainland China — it is
  blocked. Use an Auth0 database connection or email OTP.
- Portal cookies do not authorize `/mcp`. Its independent OAuth access-token
  policy is described in §9; configuring portal login alone is insufficient.
- The login page carries WorkOS branding because branding is tenant-level and
  this tenant is shared with the admin app. Filed as `docs/BACKLOG.md` §1 —
  the fix is a separate Auth0 tenant, cheapest to do before onboarding.

## 9. MCP OAuth setup and deliberate migration (v0.14.0, not deployed)

Sources checked 2026-09-30:
[OpenAI authentication](https://developers.openai.com/plugins/build/auth),
[MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
[Auth0/OpenAI reference](https://github.com/openai/openai-mcpkit/tree/main/python-authenticated-mcp-server-scaffold),
[Auth0 audience behavior](https://auth0.com/docs/secure/tokens/access-tokens/get-access-tokens).
The reference's recommendation to set a tenant default audience is **not** an
authorized side effect here: this is a shared tenant. The resource server does
not implement its own authorization or token endpoint. Auth0 owns those endpoints,
PKCE verification, consent, authorization codes and token issuance.

### 9.1 Required policy and server configuration

- `MCP_AUTH_ISSUER=https://work-os.jp.auth0.com/` (exact trailing slash).
- `MCP_RESOURCE_URL=https://family-expenses-bejtu5m47a-as.a.run.app/mcp`.
  This exact URI is the Auth0 API identifier / token audience and advertised resource.
- `MCP_MEMBERS_JSON`: JSON object keyed by **verified Auth0 user ID (`sub`)**,
  independently approved by the owner. Obtain IDs from Auth0 User Management;
  do not infer them from email or from caller arguments. Example using placeholders:
  ```json
  {
    "auth0|APPROVED_OWNER_ID": {
      "actor": "Owner",
      "permissions": ["expenses:read", "expenses:write", "expenses:links"]
    },
    "auth0|APPROVED_MEMBER_ID": {
      "actor": "Family member",
      "permissions": ["expenses:read", "expenses:write"]
    }
  }
  ```
  The actor is the trusted ledger attribution; no client field can override it.
  A reader receives only `expenses:read`. Every MCP caller needs read, including
  writers and link managers. `expenses:links` covers list, create **and** revoke.
  No email allowlist fallback, wildcard member, signup-based enrollment or machine
  client access. Store the policy in an owner-approved Secret Manager secret,
  `family-expenses-mcp-members`, and pin its numeric version during deployment.
  Creating the secret or granting access to it requires approval first.
- Access tokens must contain both `scope` and Auth0 `permissions` arrays for the
  relevant permissions; all three gates (scope, RBAC, member policy) must agree.
  Only RS256 is accepted. ID tokens, opaque tokens, another API's tokens, legacy
  MCP_SECRET, URL tokens and portal sessions are rejected. Tokens are checked
  on every request; JWKS is cached for 300 seconds. Refreshes are serialized and limited to one
  attempt per 60 seconds (including failures/unknown key IDs), using a dedicated
  single worker permit independent of portal DB workers. Unknown keys and expired
  caches deny access; valid cached known keys keep working during the cooldown.
  Legitimate new signing keys may be denied for up to 60 seconds after the last
  fetch; reconnect/retry after that interval rather than weakening validation.
- Local missing config returns 503 on MCP while portal tests remain available.
  Cloud Run (`K_SERVICE`) with missing/invalid config refuses startup **before DB init**.

Discovery is public at `/.well-known/oauth-protected-resource/mcp` and the root
alias `/.well-known/oauth-protected-resource`. It advertises the canonical
resource, Auth0 issuer, header-only bearer tokens and minimal `expenses:read`.
Every tool advertises its complete required scopes in canonical top-level
`securitySchemes` and its `_meta` mirror: read, plus write/links where applicable.
Missing or invalid tokens produce HTTP 401 with a resource-metadata challenge;
nonmembers and tokens lacking read receive HTTP 403. A read-authorized caller
without a tool permission receives an HTTP 200 MCP result with `isError=true`
and `_meta["mcp/www_authenticate"]` (plus a matching challenge header); the tool
is not executed. The challenge requests read plus the operation permission.

### 9.2 Auth0 and client setup — approval required, not performed

1. Review the current tenant and service settings read-only. Keep the existing
   portal Regular Web Application, `Username-Password-Authentication` connection,
   portal callbacks/logout URLs, `PORTAL_ALLOWED_EMAILS`, session binding and
   portal credentials unchanged. Do not switch tenants or introduce Google login.
2. After approval, create a separate Auth0 API with the exact MCP resource URI
   above, RS256 signing and an Auth0 access-token profile. Add permissions
   `expenses:read`, `expenses:write`, `expenses:links`; enable RBAC and **Add
   Permissions in the Access Token**. Assign only owner-approved household users
   the approved permissions; choose a short access-token lifetime (e.g. 15 minutes).
   No client-credentials grant to the household API.
3. **Selected registration method: pre-registration**, permitted by MCP. In
   ChatGPT's custom OAuth connection setup, verify the UI accepts a client ID and
   optional client secret and copy its **exact displayed production redirect URI**.
   Register a separate Auth0 client for this connection, with authorization-code
   grant and S256 PKCE, exact callback matching, the existing email/password
   database connection enabled and consent enabled. Set the token authentication
   method to the one selected in the ChatGPT setup UI (public `none` if supported,
   otherwise confidential client-secret method). The user enters any client secret
   directly in the secure setup UI. Do not paste it here or store it in this repo.
   Repeat with a separately pre-registered client for each other client type.
   Do not guess a stable ChatGPT callback or reuse the portal client.
4. Verify Auth0's `/.well-known/openid-configuration` (or RFC 8414 metadata)
   reports the exact issuer, authorize/token/JWKS endpoints, code flow and
   `code_challenge_methods_supported` containing `S256`. Verify code exchange
   without the matching verifier is rejected. Do not advertise nonexistent DCR.
   If the current ChatGPT UI lacks pre-registration, stop: the supported alternative
   is approved manual CIMD import using the **exact displayed CIMD URL**, with
   Auth0 per-app access policy; that needs a separately reviewed setup change.
5. **Resource/audience readiness gate:** clients send `resource=<exact MCP URI>`
   in BOTH authorize and token requests. Auth0 historically uses `audience` and
   its reference currently recommends a tenant default audience. First verify
   the existing tenant actually issues a signed access token with this MCP `aud`
   for the selected client flow. If the client supports an explicit additional
   audience parameter, configure the same exact URI there. Never set the shared
   tenant's Default Audience silently. If it is necessary, stop and present its
   impact on other applications for specific owner approval, then configure it
   only after approval. If approved provider configuration cannot produce a
   resource-bound token, rollout is blocked; do not accept a wrong audience,
   hard-code an ID-token exception or add an anonymous bypass. Test wrong-resource
   authorization requests too: resource must not broaden the intended audience.
6. Request read scope initially. Reconnect/consent with `expenses:read
   expenses:write` for writers, and additionally `expenses:links` only for the
   owner. Authenticated tool-level insufficient-scope results carry the documented
   MCP OAuth challenge. Actual ChatGPT scope-upgrade consent must still be verified
   before relying on automatic escalation; no local test proves that UI behavior.
   Refresh-token issuance/rotation, if needed for long-lived connections, is a
   separately approved Auth0/client setting; never extend portal sessions as a fix.

### 9.3 Cutover, recovery and acceptance

1. Finish local tests and the independent adversarial review. Inventory existing
   family MCP clients with the owner, record reconnect instructions and agree a
   cutover window. Announce that the old anonymous connectors will stop working.
   Keep existing phone bookmarks/live links intact throughout.
2. Complete approved Auth0 setup and resource-binding verification first. Use
   an isolated SQLite fixture deployment/local tunnel for OAuth tests if a public
   callback is required; any temporary cloud/tunnel deployment requires approval.
   Never point it at the real household database. No real expense or link mutations.
3. Prepare the approved new policy secret/version and grant only the existing
   service account access to that secret, with explicit approval. Capture the
   current revision, image, environment and existing secret **references**, without
   exposing values. Review `scripts/deploy.sh --dry-run --allow-dirty` locally.
   The script updates only MCP settings and member policy; existing DATABASE_URL,
   Auth0 and SESSION_SECRET bindings remain untouched. Service/region stay fixed.
4. After explicit deployment approval, integrate reviewed changes with concurrent
   portal work, commit to a clean deploy checkout and deploy to the same service
   with `MCP_MEMBERS_SECRET_VERSION=<approved numeric version> scripts/deploy.sh`.
   Do not push a deploy-triggering branch without separate approval. Keep Cloud Run
   reachable publicly for discovery/OAuth redirects; application OAuth gates MCP.
   Do not change Cloud Run IAM without approval.
5. Reconnect ChatGPT and each family client at the same `/mcp` URL using OAuth.
   Let each user enter passwords/secrets and complete consent directly. Verify an
   unauthenticated MCP request returns 401, an invalid/expired/wrong-audience token
   returns 401, a nonmember returns 403, and a reader mutation yields an MCP
   `isError` scope challenge without execution; an approved household read works. Use read-only `expenses_list` for live authorized acceptance; observe
   only pass/fail and avoid copying ledger contents into logs/transcripts. Writes
   and link operations are proven on fixtures only. Verify portal login and an
   existing bookmark without printing its token or changing/revoking links.
6. On failure, keep the protected revision and diagnose discovery, client registration,
   callback, PKCE, issuer, audience, RBAC, scope and member policy in that order.
   Roll back to a previous **protected** revision if available. The pre-cutover
   anonymous revision is not a safe rollback: deploy a reviewed deny-all `/mcp`
   boundary on it before reuse, or keep MCP unavailable temporarily while preserving
   the portal. Do not restore anonymous data access for compatibility.
7. Mark full authentication finished only after approved deployment, real ChatGPT
   OAuth consent/connection, anonymous denial and authorized household-read evidence.
   Local tests alone do not establish that Auth0 setup or real ChatGPT OAuth works.

### 9.4 Logs and operational restrictions

The auth boundary never logs bearer tokens, claims, secrets, bodies or ledger rows;
errors are generic. Uvicorn access logging is disabled because portal URLs carry
credentials. Do not enable SDK DEBUG logging, request/header/body tracing or token
logging. SDK-emitted records, including root-level validation warnings, are
sanitized at LogRecord creation before handlers receive them; unrelated
application diagnostics remain intact. Cloud Run's platform request logs are separate from Uvicorn and may contain
portal paths/query strings: an owner-approved Logging exclusion/redaction policy
for credential-bearing portal/callback URLs must be verified before rollout; do not
change logging/security infrastructure silently. Never run `scripts/smoke_live.py`
for this auth migration: it creates/mutates live rows and portal links. Use only
read-only live acceptance above; the isolated suite covers write behavior.


### 9.5 Family Expenses personal plugin — completed web reconnection

The owner manually removed the old no-auth app definition because its authentication
mode could not be edited in place. Reinstallation alone retained the old mode.
A new **Family Expenses** personal plugin was created with the existing saved
icon, description and server URL. The old plugin ID must not be reused as the new
connection identity. The recreated plugin visible in Safari is
`plugin_asdk_app_6abc962039d88191bb3cc56ff8670715`. No additional local
plugin package or iOS install is needed.

Verified non-secret settings for this connection:

| Setting | Value |
|---|---|
| MCP server / API audience / resource | `https://family-expenses-bejtu5m47a-as.a.run.app/mcp` |
| Issuer | `https://work-os.jp.auth0.com/` |
| Registration method | User-Defined OAuth Client |
| Existing Auth0 application | `mcp-chatgpt-dev` |
| Public client ID | `9hsejf2SLr6OJD086vUkWCPhYgjJKFQn` |
| Token endpoint authentication | `none`; no client secret required |
| Displayed callback | `https://chatgpt.com/connector/oauth/kwT-xa2yfsOd` |
| Base scopes | `expenses:read`, `expenses:write`, `expenses:links` |
| User-delegated grant | `cgr_yeLmVwcDyx8rHVba`, exact three scopes, `subject_type=user`, `allow_all_scopes=false` |

Base scopes accept one value per line or comma-separated values. The client-ID
field contains only the public client ID. The new callback was added while
preserving both existing Auth0 callbacks. A future recreation can display a
different callback; verify its exact value and obtain approval before adding it.
Do not guess a callback, alter tenant default audience or create a machine grant.

Sign in using the approved household's existing Auth0 email/password account and
complete consent securely. Tokens, passwords and client secrets must never be
copied into chat, logs or this runbook. Each caller remains restricted by the
intersection of requested scopes, Auth0 permissions and runtime membership:
Matt has read/write/link permissions; wife has read/write only.

On 2026-09-30 the owner reported successful new-plugin creation, Auth0 sign-in and
consent, and a read-only expense summary on ChatGPT web. This browser success was
user-reported rather than independently observed by this session. At 04:58:35 UTC
(12:58:35 Asia/Shanghai), the parent coordinator independently reported a successful
connected `expenses_list({})` read: 38 records matched the previous baseline,
with no new records or writes. Tools reappeared and watcher access was restored.
No ledger contents or tokens were retained as acceptance evidence.

Desktop and iOS pickup remain acceptance checks. Use the same ChatGPT account,
refresh the app and follow any secure sign-in prompt; do not assume the old plugin
identity survives. Keep the phone portal bookmark unchanged. Live write and link
authorization were not exercised by mutating the household ledger or links.
The isolated test suite covers those permission boundaries; actual step-up consent
is still unverified because this owner connection requests all three base scopes.

### 9.5.1 Approved production rollout evidence

- Reviewed release: `f8285300d84e6d16cc0efd2a155ea474755d6b36`,
  branch `codex/mcp-oauth-local`; no merge required for the manual deployment.
- Cloud Build `40961b99-8382-498b-a47c-ceb059cbf362` succeeded.
  Deployed immutable image:
  `gcr.io/work-dashboards/family-expenses@sha256:5372d2d385ff58e9056d6eed71ff7f7e5bfa31cd228acbb10c37e85d2302f214`.
- Cloud Run `family-expenses-00017-ktm` serves 100% traffic in
  `asia-southeast1`. Original service URL, portal settings, secret bindings,
  service account, household data and live links were preserved.
- Runtime membership uses `family-expenses-mcp-members:1`. Only the existing
  service account was granted access to that new secret.
- Auth0 API `6abc839885659cb0080b4f34` uses RS256, both token lifetimes
  900 seconds, RBAC and permissions in tokens, three explicit scopes, and
  explicit user consent. Machine access is denied. Owner-approved household API
  permissions and runtime membership were verified.
- Scoped Cloud Logging exclusion `family-expenses-credential-urls` protects
  credential-bearing `/t/` and `/callback` request URLs; other logging remains.
- Independent live checks returned healthy service and OAuth discovery, and
  401 bearer challenges for anonymous and invalid-token MCP requests.
- Real web OAuth plus authorized household read is now established by the
  reported acceptance evidence above. Desktop/iOS propagation, wife reconnection
  and live write/step-up behavior remain pending; do not restore anonymous access
  if another client needs reconnection.

### 9.6 Reproducible runtime and review evidence

`requirements.lock` pins the resolved runtime dependencies; Docker installs
that lock rather than broad ranges in `requirements.txt`. The Python slim image
is pinned to an OCI manifest digest read from Docker's official registry on
2026-09-30. Dependency changes require a fresh isolated install, full offline suite
and review; no runtime lock can substitute for provider or deployment acceptance.
The current log scrubber and tool-extension adapter are tested against this lock.
