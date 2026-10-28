# Required course setup for class fees — review evidence

## Scope and acceptance

Owner request: dedicated education-class and sports-class fee categories must
require matching course setup; both records must save together or neither does.
Equipment and other fees remain in the existing broad categories. No historical
expense reclassification, payment-state change, or production test records.
Review base: `f468bee` (deployed session-continuity work is a prior phase).

The implementation uses `aden-edu-classes` / `aden-sports-classes`, a required
inline course object, shared Store validation and one transaction containing
expense, package and both audits. The package carries no independent amount.
Course deletion and expense category changes preserve the required relationship.
The portal previews total / count and submits once; repeated pending taps are
ignored, explicit refusals retain inputs, uncertain outcomes require inspection.
The shared HTTP helper also treats JSON 5xx as uncertain, matching network loss.

## Local evidence

- Exact production dependency environment:
  `/tmp/family-expenses-session-runtime-20261028/bin/python`.
- Full offline suite: **542 tests passed**; SQLite, no live database/network.
  Log: `/tmp/family-expenses-class-fee-tests.log`.
- New missing-course regression failed against the old Store before changes.
  JSON-5xx regression likewise failed before correcting the response classifier.
- New tests execute real Store transactions, portal API, registered MCP tools
  and shipping JavaScript with real promises. Injected package-insert and
  package-audit failures leave zero expenses, packages and history rows.
- Synthetic money case: 1000 / 3 gives display rate 333.33, one attendance
  consumes 333.33 and leaves 666.67, with the expense unchanged at 1000.
- Playwright mobile viewport 390 × 844, isolated in-memory SQLite: sports pack,
  period fee with label, ordinary equipment expense, reload and class rendering,
  server refusal for a fee without course, amount and attribution readback all
  passed. Three synthetic expenses and two courses; no production mutations.
- The task-owned browser was closed and verified disconnected; its local server
  was stopped and the thread verified terminated. No shared browser access used.
- Whitespace check and category/document/tool parity checks passed. Changed
  Python business paths have executable coverage; version-only `__init__` has
  no new behavior. No schema migration, new model field, JSX or external request.

## Review policy and independent reviewers

The original Claude Opus review attempts could not authenticate. The owner then
explicitly retired Claude, made AGENTS.md authoritative, and requested Codex
review at appropriate model/effort. Cross-model review is now optional only on
owner request. The retired CLAUDE.md is a pointer, not an instruction source.

Initial independent read-only Codex reviews:

- GPT-6.1 Terra, xhigh: transaction integrity, concurrency, money and attribution.
- GPT-6.1 Sol, high: real portal behavior, adversarial test quality and policy.

Both reviewers reported should-fix findings; none was waived:

1. API/MCP performed a package lookup after committing the purchase. Fixed by
   returning the package payload captured by `Store.create_submission` inside
   its transaction. `Store.create` retains the Expense-only interface. A real
   API/MCP regression injects failure into the old later lookup and confirms
   successful, complete snapshots without using that lookup.
2. Only Save was disabled, allowing edits during a pending request that success
   then cleared. The whole entry fieldset now disables while pending, including
   across language/category updates. Tests assert lock/release for success,
   refusal and unknown outcomes; browser smoke also checks actual disabled inputs
   across a language switch while holding the submission request.
3. Deletion guidance directed callers into a newly refused operation. Both
   required-course refusal paths now give bilingual, ordered correction steps
   with full-correction confirmation and the archive alternative. MCP guidance
   matches. The regression verifies both refusals retain both records/audits.
4. The historical LESSONS rule retained the retired Claude gate. Its incident
   remains, but the rule now points to the owner's Codex-review policy in AGENTS.

The three bug regressions were run before their fixes and failed (two assertion
failures, one injected post-commit read exception). After fixes, 41 focused tests
passed. Final verification on the fixed state:

- Terra xhigh independently re-reviewed the creation/refusal refactor: CLEAN,
  no remaining must-fix or should-fix. The preexisting MCP summary-note read can
  still fail after a commit; the new package response no longer adds a separate
  lookup. Network outcome uncertainty is not claimed eliminated.
- Sol high independently re-reviewed portal and test behavior: CLEAN, no
  remaining must-fix or should-fix. It also reran the 18 class-fee tests and
  the isolated real mobile browser smoke, including pending language switching.
- Final full suite: 542 passed in the pinned production dependency environment.
  Final focused and browser checks exercise the last fixes. All task-owned
  browsers and local servers were closed/stopped and verified released.

These are two clean independent reviews after all fixes, plus passing final
verification. They establish source/local acceptance, not production acceptance.

## Release state

Prepared locally, **not deployed**. Production remains v0.14.2 with the verified
400-day rolling portal session fix. New feature deployment requires explicit
owner approval after review and final verification.

## Built release

- Reviewed source: `262aa5a2098b5066864c78a58b294a558d83d10a` on
  `codex/required-class-fees`, pushed to origin.
- Cloud Build `31accb73-0833-433c-9577-1949a76a6742`: SUCCESS.
- Immutable image:
  `gcr.io/work-dashboards/family-expenses@sha256:90dfd493c3a89d150d1b768a65d0799ed3dfdb8d3e28f3a053a8520597a6e849`.
- Ready for explicit deployment approval. Building/pushing the artifact did not
  change the running Cloud Run service. The earlier owner approval covered the
  already deployed authentication fix, not this later class-fee feature.
