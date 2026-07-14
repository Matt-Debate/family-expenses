# Runbook — Family Expenses

Operator guide: deploy, mint the household link, connect MCP clients, rotate.

## 1. One-time setup

### Database (Neon)
Create a **separate** Neon project/database for family data (keeps it fully
apart from any business database). Copy the connection string
(`postgres://…`). The schema applies itself idempotently at service startup —
no manual migration step for v1.

### Deploy to Cloud Run
```bash
# from the repo root
gcloud builds submit . --config=cloudbuild.yaml

gcloud run deploy family-expenses \
  --image=gcr.io/$PROJECT_ID/family-expenses:latest \
  --region=<your-region> --allow-unauthenticated \
  --set-env-vars=DATABASE_URL='postgres://…' \
  --set-env-vars=MCP_SECRET='<long random string>'
```
Notes:
- `--allow-unauthenticated` is required for the portal link to work from a
  phone browser; the sensitive surfaces stay protected (portal = token in the
  URL, MCP = bearer secret, fail-closed if `MCP_SECRET` is unset).
- Generate the secret with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Prefer Secret Manager for both env vars (`--set-secrets`) once settled.

## 2. Mint the household link

Via MCP (from Claude, after §3): call `expenses_mint_link` with
`label: "wife"`, `expires_days: 365`.

Or via CLI against the same database:
```bash
DATABASE_URL='postgres://…' python3 scripts/mint_link.py \
  --label wife --days 365 --base-url https://<service-url>
```
Send the printed `https://<service-url>/t/<token>` link over WeChat; she
bookmarks it. That's her entire onboarding.

## 3. Connect MCP clients (operator)

- **Claude (claude.ai / Claude Code):** add a custom connector / MCP server
  with URL `https://<service-url>/mcp` and header
  `Authorization: Bearer <MCP_SECRET>`.
- **ChatGPT (developer mode):** add an MCP server with the same URL + header.

Tools: `expenses_list`, `expenses_summary`, `expenses_add`,
`expenses_mark_paid`, `expenses_history`, `expenses_mint_link`,
`expenses_revoke_link`.

Try: *"What do I still need to pay?"* → `expenses_list` with `status:
"unpaid"`.

## 4. Rotate / revoke

- Lost or leaked link: `expenses_revoke_link` (or
  `scripts/mint_link.py --revoke <token-or-id>`), then mint a new one.
- Rotate the MCP secret by redeploying with a new `MCP_SECRET`.
- Inspect links: `scripts/mint_link.py --list` (shows label, expiry, usage).

## 5. Operations notes

- **Backups:** rely on Neon's point-in-time restore; the `expense_history`
  table is additionally an application-level audit of every change.
- **Logs:** Cloud Run request logs; the app logs via uvicorn.
- **Scale:** min-instances=0 is fine (stateless HTTP MCP; Neon serverless).
  Cold starts of a couple seconds are acceptable for this use.
- **Schema changes:** v1 applies `db/schema.sql` idempotently at startup.
  The first breaking change must introduce a dated migration file — see
  `docs/IMPLEMENTATION_PLAN.md`.

## 6. Local development

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests        # 37 tests, sqlite, no server
python3 scripts/mint_link.py --label dev     # local sqlite file
MCP_SECRET=dev python3 -m app.main           # http://localhost:8080
```
