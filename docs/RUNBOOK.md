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
  --set-env-vars=DATABASE_URL='postgres://…'
```
Auth posture (deliberate, per the owner's threat model — low-stakes household
ledger behind unguessable URLs):
- Portal links **never expire**; nobody renews anything. Revocation
  (`expenses_revoke_link`) is the kill switch.
- The MCP endpoint is **open** as deployed above. To gate it later, add
  `--set-env-vars=MCP_SECRET='<random string>'` and put
  `Authorization: Bearer <secret>` in each client — no code change.
- `APP_TZ` (default `Asia/Shanghai`) controls what "today" means for default
  dates.

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

- **Claude (claude.ai / Claude Code):** add a custom connector / MCP server
  with URL `https://<service-url>/mcp`. Add the bearer header only if you set
  `MCP_SECRET`.
- **ChatGPT (developer mode):** same URL (+ header if gated).

Tools: `expenses_help`, `expenses_list`, `expenses_add`, `expenses_update`,
`expenses_mark_paid`, `expenses_delete`, `expenses_history`,
`expenses_mint_link`, `expenses_revoke_link` (design: `docs/MCP_DESIGN.md`).

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
| 给我老婆做个链接 / make a link for my wife | mints a never-expiring portal link |

If a phrase matches several expenses, the tool returns the candidates and the
assistant asks which one — nothing is guessed silently.

## 5. Rotate / revoke

- Lost or leaked link: `expenses_revoke_link` (or
  `scripts/mint_link.py --revoke <token-or-id>`), then mint a new one.
- Rotate the MCP secret by redeploying with a new `MCP_SECRET`.
- Inspect links: `scripts/mint_link.py --list` (shows label, expiry, usage).

## 6. Operations notes

- **Backups:** rely on Neon's point-in-time restore; the `expense_history`
  table is additionally an application-level audit of every change.
- **Logs:** Cloud Run request logs; the app logs via uvicorn.
- **Scale:** min-instances=0 is fine (stateless HTTP MCP; Neon serverless).
  Cold starts of a couple seconds are acceptable for this use.
- **Schema changes:** v1 applies `db/schema.sql` idempotently at startup.
  The first breaking change must introduce a dated migration file — see
  `docs/IMPLEMENTATION_PLAN.md`.

## 7. Local development

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests        # 51 tests, sqlite, no server
python3 scripts/mint_link.py --label dev     # local sqlite file
python3 -m app.main                          # http://localhost:8080
```
