# MCP Design — what agents actually read

Motivation (owner, 2026-07-14): a previous MCP took "dozens of edits" because
guidance was written where the agent never looked, and tools weren't selected
when expected. This doc fixes the channel model in writing so every future
edit lands where it has effect.

## Channel reliability (what an LLM client shows its model)

| channel | reliability | what we put there |
|---|---|---|
| **Tool name + description + param schema** | ~always in context | the ONLY place guidance is guaranteed seen. Trigger phrases (中文+EN), defaults, cross-references ("to X use tool Y") |
| **Tool results** | always read after a call | ambiguity candidates + `hint`, running unpaid total (`note`) so the agent confirms naturally |
| **Error strings** | always read on failure | coaching: what was wrong AND what to call/pass instead — one-round-trip self-correction |
| **Tool annotations** (readOnly/destructive) | used by clients for permission UX | reads flagged read-only (fewer prompts); delete/revoke flagged destructive |
| **Server `instructions`** | inconsistent across clients — may never be shown | bonus copy of the playbook; never the only home of a rule |
| **MCP prompts** | user-invoked only, where the client exposes them | the three personas (记账 / 对账 / 修复) |
| **Resources** | rarely auto-read | not used |

**Rule: if a behavior matters, it must be encoded in the top four rows.**

## Tool inventory (9) and why

| tool | why it exists / selection cue |
|---|---|
| `expenses_help` | playbook-as-a-tool: works on clients that never surface `instructions`; description says "START HERE when unsure" — agents do call help tools when confused |
| `expenses_list` | the one read for items AND totals ("我还要付什么", "花了多少"). A separate `expenses_summary` was **removed**: redundant read-only tools split selection probability and its output was already inside `list` |
| `expenses_history` | disputes/troubleshooting ("谁改的") |
| `expenses_add` | "足球课300块"; accepts already-paid in one call (paid=true) so "昨天交了300" isn't a two-step |
| `expenses_mark_paid` | "付了/交了/paid" — the highest-frequency write, so it gets fuzzy `query` targeting with unpaid-preference |
| `expenses_update` | corrections ("改成350"); cannot touch paid — error redirects to mark_paid |
| `expenses_delete` | mistakes only; destructive-flagged; description says confirm first and redirects "it's paid" to mark_paid |
| `expenses_mint_link` / `expenses_revoke_link` | link lifecycle ("给我老婆做个链接" / kill switch) |

Principles: no two tools answer the same user intent; every mutating tool
takes `query` (fuzzy, candidates-on-ambiguity, never guesses); every write
returns the new unpaid total; params tolerate what speech produces (numbers
or strings for amounts, omitted dates).

## Personas (MCP prompts)

Three, matching the owner's three usage modes. They set role, workflow, and
tone (reply in the user's language, one-line confirmations, confirm deletes):

- **记账 `jizhang`** — quick add: dictated expenses, minimal questions.
- **对账 `duizhang`** — settle up: walk the unpaid list, check off payments.
- **修复 `xiufu`** — fix a mistake: locate → disambiguate → correct → explain
  via history; never delete without asking.

Prompts are user-invoked (a picker in Claude apps); they are an accelerator,
not a dependency — the tools alone carry every rule needed for cold requests.

## Regression guardrails

`tests/test_mcp.py::AgentErgonomicsTests` pins all of this: bilingual triggers
present in descriptions, cross-references intact, annotations correct,
personas registered, numeric amounts accepted, one-call already-paid add,
notes on write results, and coaching text inside error strings. If an edit
moves guidance out of an agent-visible channel, a test fails.
