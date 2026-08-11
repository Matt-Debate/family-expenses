# Changelog — Family Expenses

Semantic versioning. Unreleased work accumulates under [Unreleased] and is cut
to a release entry when a chunk set ships.

## [Unreleased]

Nothing pending. (The block that sat here described `CLAUDE.md` and
`scripts/smoke_live.py`, both of which shipped in 0.4.2–0.4.4 and were never
moved into a release entry; `git log` places them and the entries below now
carry them.)

## [0.10.0] — 2026-08-11

### Added
- **Class tracker — a fourth portal tab, three MCP tools, two tables.** Prepaid
  courses in the two shapes this household actually buys:
  - **per_class** — a pack of N classes (足球课 · 8月 · 10课). Attending draws one
    down; the tab answers "how many classes and how much money are LEFT".
  - **period** — a flat month/semester fee. Nothing is drawn down; the classes
    that did NOT happen are owed back. Reported as one owed figure **split into
    reclaimable** (`missed_school` — they cancelled) and **forfeited**
    (`missed_us` — we skipped), because only the first is worth arguing about.
    Whether that becomes a rollover or a refund is a conversation, not a field.
- `classes_list`, `classes_add`, `classes_log` on the MCP (13 tools). `classes_add`
  targets the payment by `query` like every other tool, and refuses to invent
  one: the expense must be in the ledger first.
- `db/class_packages` + `db/class_events` in `db/schema.sql` — additive, so no
  dated migration files are started.

### Money semantics
- **A package holds no money of its own.** The per-class rate is derived at read
  time from `expenses.amount / class_count`. Correct the payment and the tracker
  corrects itself; there is no second place for the price to be wrong, and
  `update_package` refuses an `amount` field and says where it lives.
- **`expense_id` is UNIQUE.** Two packages funded by one payment would each
  claim the whole amount and silently double-count it.
- **Amounts are exact ratios, never a rounded rate × n.** ¥1,000 over 3 classes
  reports a ¥333.33 rate for display, but used + remaining still equals ¥1,000.
- **Consumption is not spending**: `Store.summarize` knows nothing about
  classes, so no expense total moves when a class is logged. Attending a class
  you already paid for is not a new expense.
- **A payment that funds a package cannot be deleted** until the package is.
  Cascading would silently destroy an attendance log that took a term to build;
  the error says what to do instead.

### Fixed after review
Four independent passes (two adversarial agents, a cross-model Codex review,
and mutation testing) went over the first draft. All three reviewers
independently found the same two defects, and the mutation run showed **ten
ways to corrupt this feature's money that the suite let through**. Both are
fixed, and every one of those mutations is now caught.

- **The tab did not work.** Class rows reuse the expense list's `.ex-hd`
  markup, and a document-level handler bound to it called `toggleItem(null)` →
  re-render, closing the row the class handler had just opened. Tapping a
  course never revealed its class log. Separately, `.btns { display:flex }`
  out-ranked the UA `[hidden]` rule, so every row showed its action buttons —
  including Delete — permanently. Neither was visible from the API tests,
  which is all the first draft had.
- **A period package could report owing back more than was ever paid.** A
  wrong `class_count` or a bad month made `owed_amount` unbounded: ¥2,000 paid,
  ¥2,750 "owed". Money is now capped at the payment and the excess surfaces as
  `overrun`, matching what the per-class branch already did.
- **The reclaimable/forfeited split did not reconcile with the total.** Three
  independent `round()` calls meant ¥1,000 over 3 classes reported owed ¥666.67
  against parts of ¥333.33 + ¥333.33. (This round's fix — deriving the total
  from its parts — turned out to breach the cap in the other direction; see the
  second review round below for the rule that actually holds.)
- **The class figures were rounded to whole yuan in the portal** while the
  server and MCP reported cents — ¥667 on the tab whose job is telling a school
  what it owes.
- **`scripts/smoke_live.py` asserted an exact 10-tool set** and would have
  failed the next deploy. This is the second release running in which the
  post-deploy gate was broken by a change that never touched it.
- **A missing package reported "no expense with id …"** and pointed at
  `expenses_list`, which cannot produce a package id (P3). `PackageNotFoundError`
  names `classes_list`.
- **A lost UNIQUE race surfaced as a 500.** The duplicate pre-check is not
  atomic with the insert and the handlers run in a threadpool, so the constraint
  can be what fires; it is now translated to the same coaching the pre-check gives.
- **Changing a package's `kind` silently reinterpreted its whole log** —
  attendances stopped drawing down, misses became money owed. Refused once
  anything is logged.
- **Foreign keys** on `class_packages.expense_id` and `class_events.package_id`.
  The application refuses to delete a funding payment, but that check and the
  delete are not one atomic step; without the constraint the loser of that race
  commits an orphan, and an orphan vanishes from every read.
- A fractional `class_count` (JSON `1.9`) silently truncated to `1`, and the
  count divides the money. Refused.
- The Classes tab now tags each fetch with a generation number (a slow earlier
  response could repaint stale totals) and disables a log button in flight (a
  double tap logged the class twice — on a period package, claiming another
  class back from the school).
- `/api/classes-list` read the whole package list twice to collect one column.

### Fixed after the second review round
The fix wave above was itself reviewed — and had introduced a defect of its own,
the third time in this release that a fix wave did. A parallel mutation run
applied 80 mutations and found **41 that the 210-test suite let through**, each
with a probe proving it produced a genuinely wrong number.

- **Deriving a total from two rounded parts let a period package report owing
  back ¥0.01 MORE than was paid**, in ~0.8% of ordinary splits — the exact cap
  the previous round's own contract text promised. The rule is now one-way and
  stated as such: **a part is derived from the total, never the total from its
  parts.** `owed_amount = value(school + ours)`, `forfeited_amount` is the
  remainder, `remaining_amount = total - used_amount`. This also restores the
  exact ratio (¥1,000 over 3 is ¥666.67 again, not ¥666.66).
- **`ClassMoneyInvariantTests`** replaces hand-picked examples with a sweep over
  18 hostile amounts × 9 counts × every event mix — ~3,000 combinations
  asserting that parts sum to their total, nothing exceeds the payment, and
  nothing is negative. Every money defect this feature has shipped survived a
  suite of tidy examples (¥2,200/10 and ¥2,000/8 both divide evenly, so rounding
  could never bite). This is the guard that actually holds.
- **The school's share is now asserted directionally.** Flipping the allocation
  to us-first keeps every total correct and reconciling while handing the school
  back money she was going to claim — no invariant can see that.
- **`class_count = NaN` returned a 500** rather than a 400: `int(nan)` raises a
  bare `ValueError` outside the validator's own try.
- **A foreign-key violation was reported as "already tracked"**, sending someone
  to look for a package that does not exist. The diagnosis now runs after the
  failed transaction has rolled back — a query inside an aborted Postgres
  transaction is itself an error — and names the constraint that actually fired.
- **A non-integrity failure was laundered into a reason.** A dropped connection
  came back as "that payment is already tracked", so the agent stated it
  confidently and the write was lost.
- **Every course row collapsed after each log or unlog** — there was no
  open-state, so the re-render closed the row the tap had just opened.
- **A capped period row now says it is capped.** The counts report what happened
  and the money stops at the payment, so "(5)" beside ¥0.00 read as a bug.
- **`ClassTabInteractionTests`** runs the Classes tab's real click handler under
  node. Inverting one boolean in it made the whole tab inert while 210 tests
  stayed green, because every guarantee there was a string match.
- The HTTP layer was trusted to pass its arguments through: `date`,
  `period_label`, `note` and `include_archived` were all pinned at the store and
  unpinned at the boundary, so dropping any of them changed nothing visible to
  the suite.
- Raw-SQL tests for the constraints the app makes unreachable — the `expenses`
  foreign key, `class_count > 0`, and both kind allow-lists.
- `PackageNotFoundError` landed in three call sites and only one was pinned.

### Fixed after the third review round
The rule from round two — derive a part from the total, never a total from its
parts — was written into a comment but not actually implemented at the boundary.
`value(count)` is `round(amount * count / count, 2)`, which is **not** reliably
`round(amount, 2)`: an amount sitting on a half-cent crosses the tie differently
on the multiply/divide round trip. So a ¥100.035 payment over 6 classes still
reported **¥100.04 owed against ¥100.03 paid**, and the comment asserted that
was impossible.

- `value(n)` now returns the total at `n >= count` — the cap holds by
  construction rather than by assertion. `max(0.0, …)` on `remaining_amount`
  turned out to be masking the same defect rather than guarding anything.
- **The sweep's amounts were all exact 2-decimal values** while its own comment
  claimed "half-cent boundaries". Six third-decimal amounts added; they fail
  immediately against the old arithmetic.
- **An unbounded `class_count` reached the driver as an `OverflowError`** and
  became a 500, the same shape as the NaN case. Capped at 1000.
- **Tapping a course row silently reset the add form's payment picker**, so the
  next course could be linked to whichever payment happened to be listed first.
- `ClassRowRenderingTests` runs the real `renderClasses` and inspects its HTML:
  forcing every row closed — no buttons, no class log, permanently — passed the
  entire suite, because only the click half was executed.
- Two guards were correct but unproven (P5): the `isfinite` check, and the
  `if not _is_integrity_error(exc): raise` that keeps a dropped connection from
  being reported as "already tracked". Both now fail when removed.

### Fixed after the fourth review round
The first round to find **no MUST FIX**: the arithmetic was brute-forced over
6.5 million combinations — every amount shape, counts 1–30, every event mix
including large overruns — with zero invariant violations. What it did find was
seven guards that were correct but unproven, one of them protecting a real
defect:

- **`list_packages` hands each course its own classes**, and nothing checked it.
  It loads every event in one query and groups them in Python; giving the whole
  set to each package makes a course she has never attended report classes
  consumed and money spent. Every prior test had one package or no events, so
  the grouping was invisible. The code was right; the guard was missing.
- The add form's **rate hint** had no test at all — mutating its division to a
  multiplication showed "¥22,000.00 (¥2200 ÷ 10)" on screen at the moment she
  decides whether a course is priced right.
- The **stale-response generation counter**, the **payment-picker preservation**
  and the **empty class-log placeholder** were all unreferenced by the suite.
- `test_a_course_with_no_classes_logged_still_renders` was a grep for a literal
  that also appears in the History tab, so deleting the branch it named left it
  passing. It now renders.

### Changed
- `classes_log` validates the event kind **before** resolving which course was
  meant — a bad kind is wrong whichever course it is, and reporting "no such
  course" first cost a round trip to find the real mistake.
- The portal's candidate `<option>` escapes per value rather than once around
  the concatenation, so the invariant `ClassKindParityTests` checks is literally
  what the code does.

## [0.9.1] — 2026-08-11

### Fixed
- **`scripts/smoke_live.py` could not verify a deployment any more.** Its portal
  check followed the 302 to `/login` into Auth0, whose login page answers 400 to
  a scripted request — so post-deploy verification aborted with a traceback and
  read as a dead service. It now checks the redirect without following it (302 →
  `/login` is the *success* signal when the portal is behind Auth0), asserts
  `/api/*` refuses an unauthenticated caller, and leaves the write path to the
  MCP flow it already had. Broken since v0.5.0 added the portal login; found by
  running it, which is the only way this was ever going to surface.
- **The smoke output printed a live portal token** in the redirect Location.
  Tokens are bearer credentials and this output gets pasted into transcripts —
  P9. All hex runs of 32+ chars are now redacted at the print boundary.
- The closing line told the operator to mint the household link next. That was
  true on deploy day and misleading ever since.

## [0.9.0] — 2026-08-11

Clears `docs/BACKLOG.md`: every deferred code defect is fixed, the two
operational unknowns are resolved, and the doc drift is gone.

Each **code defect** has a regression test that was observed failing against the
unfixed code first. Two items are not code defects and their tests do not fail
against the old code, by design: the `APP_TZ` item was filed as a missing test,
and the constraint-hardening item is new behavior. Said plainly because the first
draft of this entry claimed all eight were failing-first, which was not true.

Reviewed before release by four independent passes. Three (two adversarial agents
and a cross-model Codex review) found the same regression in the first draft — a
portal that cached the server's date forever. A fourth then reviewed the fix for
that and found it guarded a backward clock jump but not a forward one. Both are
fixed below; the honest reading is that the review caught what the tests did not,
twice, and both times on the same code path — the date that gets written into the
ledger. Two things still lack a behavioral test and are named where they occur:
the portal's history rendering, and the `init()` warning path.

### Fixed
- **A search for a literal `%` returned the entire ledger.** `store.find` built
  its LIKE pattern without escaping `%`/`_`/`\`, so those characters in a
  natural-language query acted as wildcards — an agent told to find one expense
  could be handed all of them and act on the wrong row.
- **`expenses_add(paid=true)` spanned two transactions** — create, then
  mark_paid. A failure in between left the expense saved-but-unpaid while the
  tool reported an error, breaking the same-transaction history guarantee at the
  tool boundary. `Store.create` now takes `paid`/`paid_date` and writes one row
  with one `create` history entry. The default payment date (today, in `APP_TZ`)
  is decided in `Store.create` alone — `expenses_add` used to layer a second
  default on top of it, so the two could disagree and the store's rule was
  unreachable. The portal's history view now shows the payment date such a row
  carries, which the collapsed single entry would otherwise have hidden.
- **A missing expense raised a bare `KeyError`**, which the HTTP layer turned
  into a clean 404 but the MCP surfaced to the agent as just the id — no hint
  about how to retry. `NotFoundError` subclasses `KeyError` (so the 404 mapping
  is untouched) and carries coaching text naming `query=` as the alternative.
- **The portal decided "today" from the phone's clock** while the server used
  `APP_TZ`, so a travelling or mis-set device could move a row between Due and
  Upcoming, or a month between History and Scheduled. `/api/list` now returns
  the household's `today` and the page uses it; the device is only the
  first-paint fallback. The page advances that date by however long it has been
  open, using **elapsed** time rather than the device's absolute clock — the
  first draft pinned it forever, so a tab left open overnight would have gone on
  offering yesterday as the payment date, and an accepted default writes a wrong
  date into the ledger. The estimate is **capped at one day**, after which the
  page refetches instead: elapsed time is only sound while the device clock runs
  at the right *rate*, and an NTP correction mid-session reads as time passing —
  unbounded, that writes a payment date into a month that has not happened yet.
  Bounded to ±1 day either way; returning to a backgrounded tab re-syncs.
  `PortalDateArithmeticTests` executes the page's own date functions under node
  instead of grepping for them, which is the only reason either direction of
  this was caught.
- **`/api/list` read the clock twice** — once for the summary, once for the
  `today` it returns — so a request straddling midnight could bucket rows
  against one day and label them with the next. `expenses_list` had the same
  split between `summary` and `ledger_total`. Both now read it once.
- **Every API handler blocked the event loop.** The handlers are synchronous and
  all of them hit the database, so one slow round trip to Neon stalled every
  other request in the process, `/health` included. They now run in a
  threadpool — free, because production is on Neon's pooled endpoint and
  Postgres connections here are already thread-local.
- **`APP_TZ` was pinned by no test.** Every date assertion was a shape-only
  regex and `today_str()` fell back to UTC through a bare `except`, so an image
  without `tzdata` would silently put a China household a day behind with the
  suite green. Two zones 25 hours apart now pin the behavior, a test asserts the
  zone data is present, and the fallback logs a warning instead of hiding.

### Added
- **`db/hardening.sql` + `Database._apply_hardening`** — a `UNIQUE(expense_id,
  seq)` index on `expense_history`. `seq` is computed inside the write
  transaction, so two concurrent mutations of the same expense could both claim
  it and silently corrupt the audit order. Applied **best-effort**, each
  statement in its own transaction with a logged warning on failure: this is a
  live portal, and a constraint that cannot apply to existing data must not be
  able to stop the service from starting; `Database.init` logs a warning naming
  how many constraints are not in force. An operator check on 2026-08-11 found
  production free of duplicates — a point-in-time observation, not something
  this repo can verify.

### Changed
- **The ledger is CNY-only, and now says so.** `currency` was stored but never
  consulted — `summarize()`, the portal cards and the charts all add `amount`
  regardless — so a single foreign row would have made every monetary figure in
  the app silently wrong. Non-CNY is refused at the store with an error that
  explains why. The portal UI has no currency field and the MCP exposes no
  parameter, but `/api/submit` and `/api/update` read `currency` from the
  request body, so any link holder could reach it — the guard is load-bearing,
  not decorative. Every production row was CNY when this shipped (operator
  check, 2026-08-11).
- `docs/IMPLEMENTATION_PLAN.md` is labelled a **historical record** frozen at
  v0.2.0, and contract §10 no longer claims it stays in sync. It said "five
  tools" (ten) and named `/healthz` as the health endpoint; both are annotated
  rather than rewritten, since the point of the file is what was planned.

### Operations (investigated, no change needed)
- **The `maxScale` disagreement was not one.** `autoscaling.knative.dev/maxScale=3`
  on the revision template is the per-revision cap and governs the running
  revision (set by `deploy.sh --max-instances=3`);
  `run.googleapis.com/maxScale=20` on the service is Cloud Run's separate
  service-level ceiling across revisions. Effective limit: 3 instances.
- **The GCP budget alert already exists.** The item assumed a dedicated project;
  the service runs in `work-dashboards` (693424932326), covered by a $15/month
  project-scoped budget alerting at 50/90/100% and by a billing-account-wide $5
  budget. Nothing to create.

## [0.8.2] — 2026-08-11

Documentation pass. No behavior change except the new guard.

### Added
- `docs/BACKLOG.md` §2 — the portal decides "today" from the device clock, not
  `APP_TZ`, so travel or a wrong phone clock can shift a row between Due and
  Upcoming around midnight. §3 — currencies are summed without conversion; one
  non-CNY row makes every total meaningless. Both surfaced by cross-model review.
- `DocumentedCountsTests` — the living docs' test and tool counts are now
  asserted against reality. The count drifted four times in one session; it
  caught a stale prose mention within a minute of being written.

### Fixed
- **`unittest.main()` sat mid-file in three test modules.** Classes appended
  after it were invisible to a direct `python3 tests/test_x.py` run — they only
  executed under `discover`. Found while trying to prove the new guard works,
  which is the only reason it was noticed.
- Docs reconciled against the code: contract status was pinned at v0.4.5 and
  claimed 9 tools; the runbook advertised 99 tests; the README claimed
  onboarding was outstanding; `FIRST_DEPLOY_PLAN` still said "onboarding
  pending" and its re-runnable acceptance gate expected 9 tools.

### Changed
- **`FIRST_DEPLOY_PLAN.md` Wave 6 recorded COMPLETE (2026-08-11).** She is
  onboarded and using the portal daily; the owner's connector is live. This was
  the milestone the whole plan existed to reach.
- `CLAUDE.md` "Current state" rewritten from an accreting reverse-chronological
  changelog into orientation: what is live, that two real people depend on it,
  that the portal has a login while `/mcp` deliberately does not, and the two
  failure modes this project has actually hit (silent no-op edits; handoffs
  smuggling in a demo backend).
- Contract §4/§6/§8 describe the app as it is: `date` is the due date, `borrow`
  is arithmetic-bearing, portal writes are attributed server-side, `/api/list`
  takes `overdue` and summarises the rows it returns, and the UI is three tabs.
- `BACKLOG.md` §1's "do it before onboarding" advice is obsolete — that window
  closed today; the two items fixed in 0.8.0/0.8.1 were struck from §4.

## [0.8.1] — 2026-08-11

Six defects found by cross-model review (Codex/GPT-5.4) of 0.7.0–0.8.0. All six
passed the 117-test suite; each now has a regression test.

### Fixed
- **The category guidance was factually backwards.** `_HELP` and the result note
  told the agent an off-list category "will not appear in the totals or charts".
  It does: `summarize()` counts every category except exact `borrow` as ordinary
  household spending. The advice invited the very mis-bucketing it claimed to
  prevent — a borrow-synonym inflates household paid/unpaid instead of the
  money-owed-back figure. Both now say what actually happens.
- **`find(status="overdue")` ignored the filter entirely.** `list()` and `find()`
  each hand-rolled the status clause and drifted: `find()` handled only
  paid/unpaid, so `expenses_list(query=…, status="overdue")` returned paid and
  future rows, and a typo'd status silently meant "all". Extracted to a shared
  `_status_clause()` so they cannot diverge again; unknown statuses now raise.
- **Portal attribution was still client-spoofable.** `_author()` gave the
  client's `submitted_by`/`changed_by` precedence over the link label, so a
  request bearing the "wife" link could write any name into the audit trail —
  contradicting 0.8.0's own claim. The validated link's label is now the only
  author on this path.
- **History could hide scheduled money.** A ledger holding only future rows
  rendered "Nothing yet" and dropped every scheduled month; and `anyOut` was
  computed from past/current rows only, so a settled history hid the Outstanding
  column for scheduled rows that had money in it.
- **`since`/`until` skipped validation when `query` was set** — the MCP
  post-filter compared raw strings, so `since="not-a-date"` produced an
  arbitrary slice instead of a coached error.
- **Totals are now order-independent.** Bucket sums use `math.fsum` rather than
  `+=`; binary floating-point addition is order-dependent, so the Python
  aggregate is now identical to the SQL one it replaced for every input, not
  just household-sized ones.

### Tests
- Suite 117 → **124**, including the +30/+31 upcoming boundary the first pass
  never tested, `find(overdue)`, authoritative attribution, and order-independent
  summation.

## [0.8.0] — 2026-08-11

### Fixed
- **The summary described the whole ledger while the list was filtered.** Ask
  "what's owed this month" and two rows worth ¥5,780 rendered beneath a
  ¥247,780 headline. `Store.summarize(rows)` now derives totals from exactly the
  rows returned, and `summary()` is a thin wrapper over it — one code path, so
  the disagreement is no longer representable. Urgent because she is reading the
  portal now: the headline is the first thing on the page.
- **`expenses_list` silently dropped `since`/`until` whenever `query` was set**
  (`store.find` has no date support), so a date-bounded search quietly returned
  all time. The range is now applied to the matches.
- **Portal writes carried no author.** The form deliberately does not ask — one
  person types into it — but with two people writing to one ledger, "an expense
  exists" and "who logged it" are different facts. Writes are now stamped with
  the link's label, taken from the validated token row and never from the
  client, so it cannot be spoofed. Applies to edits and deletes too.
- **Removed the demo backend that arrived with the design handoff.**
  `if (!TOKEN) return demoApi(...)` never fired in production — the portal is
  only served at `/t/<token>` — but a ledger that silently accepts writes into
  an in-memory fake is the worst failure mode available here.

### Changed
- **`upcoming` is a 30-day window, not everything future.** Twelve months of
  living payments loaded in advance made the card answer a question nobody
  asked. Items beyond the window still count in `unpaid`.
- **History leads with the most recent month.** Scheduled future months were
  sorting above it — the top of the statement was 2027-07. Past and current
  months now come first (newest first), with future months in their own
  "Scheduled" group below. Items within a month are newest-first too.
- `status="overdue"` (unpaid and past its due date) on both `list` and `find`;
  the invalid-status error names it.

### Tests
- Suite 111 → **117**: summary-matches-rows, the upcoming window, the overdue
  filter, and two guards that the demo backend cannot return on a future design
  pass.

## [0.7.1] — 2026-08-11

### Fixed
- **The MCP never knew the category keys existed.** Nothing in `_HELP` or any
  tool description mentioned them, so asked to record borrowed money the agent
  invented `"loan repayment"` — which saved fine, matched nothing, and silently
  counted ¥31,100 as a paid household expense instead of a borrow. The playbook
  now lists every key and calls out `borrow` as the one with arithmetic behind
  it, explicitly warning against synonyms; `expenses_add` and `expenses_update`
  point at it too. This is the failure `docs/MCP_DESIGN.md` exists to prevent:
  guidance that lives nowhere the agent reads is not guidance.
- Write results now carry a coaching note when a category will not group, so a
  wrong guess self-corrects on the next call rather than mis-bucketing forever.
  Values are still stored verbatim — the MCP can write anything.
- `expenses_update` now returns a `note` at all. It was the only write tool
  without one, contrary to the module's own stated contract.

### Tests
- Suite 105 → **110**. The category-note helper short-circuits on an empty
  category, so a `NameError` in it stayed invisible to every existing test —
  each of which added expenses without one. The new tests pass a real category,
  which is what surfaced it.

## [0.7.0] — 2026-08-11

### Changed
- **Portal visual design, by Claude Design (Fable).** "The page IS the paper" —
  a typographic ledger rather than a stack of cards: warm paper ground, cinnabar
  seal-red accent, green stamp for paid, blue ink for money owed back, hairline
  rules instead of boxes. Dark mode is a designed night-ink palette, not an
  inverted light one. Chinese-first metrics (medium weights, 1.6 line-height,
  tabular numerals) — the gap the previous pass left, where Latin-shaped weights
  were inherited by CJK. Also adopts `font:-apple-system-body`, so the portal
  now tracks iOS Dynamic Type.
- Verified against the hard constraints before installing, not assumed: zero
  external requests (no CDN font, script, or image — the property that makes
  this load behind the GFW), CJK font stack intact, API wiring and
  token-from-path preserved, all 17 category keys still matching
  `store.CATEGORIES`, both language label sets complete, and no unescaped
  `catLabel`. The escaping and category-parity tests pass unchanged.
- **"What I paid for" → "Money I lent"** (English only). Her lending is not
  everything she pays for, so the old label over-claimed. 垫付 already means
  precisely "advanced on someone's behalf", so the Chinese was never the loose
  one. "I fronted" / "I paid" → "I lent" across the state chip, tile, chart
  title and category label.
- **Monthly bar labels round to thousands** — ¥22k, ¥9.8k, exact below ¥1,000.
  Twelve full figures across a ~320px viewBox bled into each other. Chart-only;
  the History tab still carries exact amounts, so nothing is lost.

### Removed
- The artifact wrapper the design arrived in. `.DS_Store` is now gitignored.

## [0.6.0] — 2026-08-11

### Added
- **Three-tab portal.** Due · History · Stats, tabs at the top with the summary
  cards inside the Due pane so they only appear where they apply.
- **Stats tab**, hand-rolled inline SVG — no chart library, because the
  no-build-step / no-CDN property is what makes the portal load on a phone in
  mainland China. Matt's payments by month, spend by category, and a borrow
  block: outstanding, fronted, repaid, and **average days to repay** (derived
  from fronted-date → repaid-date, which the ledger already stored).
- **Expandable months** in History; open months are component state so marking
  something paid from inside one does not snap it shut.
- `PORTAL_DEV_RELOAD=1` re-reads portal.html per request. Off by default, so
  production still serves from memory.

### Changed
- **Cards → compact list.** Two lines per item, hairline separators, actions
  revealed on tap. This screen is read far more than it is touched; every row of
  chrome was costing a row of ledger.
- **Mobile overlap fixed.** The old `.meta` flex row let the due date and
  category collide under ~400px. The sub-line is one wrapping text run now, and
  descriptions carry `min-width:0` so they cannot push the amount off-screen.
- **"Date" is "Due date" throughout** — the ledger answers *when must this be
  paid*, not *when was it entered*.
- **The portal speaks in her voice.** It is her surface; the owner works through
  the MCP. "Owed to her" → 待还我 / "Owed to me"; the borrow category is 我垫付 /
  "I paid (owed back)".
- **Spend figures name Matt** rather than claiming to be household totals — she
  spends too, and the ledger only sees what passes through it. Labelling them
  "Monthly spend" quietly erased her side.
- Removed the submitted_by field from the add form: only one person enters via
  the portal, so it asked a question with one answer. The column stays; history
  rows reference it.
- Category taxonomy replaced with the household's real one — living, the four
  Aden buckets, utilities/internet/mobile, and `borrow`.

### Fixed
- Chart marks are one hue in both modes. The theme's amber and violet fail the
  dark-surface lightness band as marks (they are tuned as text) — caught by
  running the palette validator rather than eyeballing it. Both charts compare
  magnitude, which wants sequential anyway.

### Tests
- Suite 102 → **105**: category parity between portal.html and store.CATEGORIES
  in both languages, and that the portal special-cases exactly the key the store
  does. Two hand-maintained lists that silently disagree would route spending
  into a bucket nobody looks at.

## [0.5.0] — 2026-08-11

### Added
- **`expenses_list_links` (10th tool).** Found by live testing: revocation was
  unreachable in practice. The agent had no way to discover *what* to revoke,
  and `expenses_revoke_link`'s description pointed it at the operator CLI — a
  channel an agent cannot use. A cross-reference is only guidance if it names a
  tool the agent can actually call. Returns id, label, status
  (active/expired/revoked), and usage; hides revoked links unless
  `include_revoked=true`.
- **Portal OAuth via Auth0 Universal Login (`app/auth.py`), off by default.**
  Same tenant as work-dashboards (JP region). Deliberate divergence from that
  app's `@auth0/auth0-spa-js` SPA pattern: this portal has no build step and a
  CDN script tag is an unreliable dependency from mainland China, so the code
  exchange is server-side and the session is a signed httpOnly cookie.
  - Layered *on top of* `/t/<token>`, not replacing it: the token still says
    which ledger, Auth0 says who you are. No tool or table was retired.
  - `PORTAL_ALLOWED_EMAILS` allowlist. Auth0 authenticates anyone who can sign
    up, so an empty list denies everyone — a half-finished config fails closed.
  - New routes `/login`, `/callback`, `/logout` exist **only** when configured.
  - `/mcp` untouched; `MCP_SECRET` still unset. Connected MCP clients see no
    login and need no reconfiguration.

### Compatibility
- With `AUTH0_*` / `SESSION_SECRET` unset, behavior is identical to 0.4.5 —
  pinned by `test_everything_still_open_when_oauth_is_off` and by the route-shape
  test asserting `/t/{token}` and `/api/*` do not move when OAuth is on.

### Tests
- Suite 77 → **98**: link-listing behavior and its ergonomics pins, plus
  `tests/test_auth.py` covering the flag (partial config fails closed), the
  allowlist (case-insensitive, empty = deny), the guards (portal 302, API 401,
  bad token still 404s without a login detour), and open-redirect protection on
  `?next=`.

## [0.4.5] — 2026-08-10

### Operations
- Deployed to the permanent service as revision `family-expenses-00005-5tz`
  (commit `0ce5f68`), serving 100% of traffic at the unchanged public URL.
  `scripts/smoke_live.py` PASS; A8 re-verified across `00004-pvt` → `00005-5tz`
  (same URL, `/mcp` still header-free, `MCP_SECRET` still unset). The fix was
  confirmed in the served HTML using a temporary token that was then revoked —
  no real token was minted or recorded. Wave 6 human onboarding is unblocked.

### Fixed
- **Stored XSS in the portal (security).** `render()` interpolated the category
  label into `innerHTML` unescaped whenever the category was not one of the six
  known keys (`app/portal.html`, the `esc(e.description) || catLabel` fallback);
  the same value was already escaped two lines later, which is what made it a
  slip rather than a decision. `category` is unvalidated end to end — the API
  takes `body.get("category")` raw and the column is bare `TEXT` — so any writer
  could plant markup, and with `/mcp` unauthenticated that means any caller who
  knows the URL. A rendered payload can read `location.pathname` and exfiltrate
  the never-expiring portal token. Now escaped at the render sink; stored values
  are deliberately left verbatim so the ledger and MCP round-trips stay honest.
- An expense with neither description nor category rendered as `[object Object]`
  — the fallback called `t("cat")`, which returns the category *map*, not a
  string. Now renders `–`.
- `data-id` is escaped defensively; ids are server-generated hex, so this is
  depth, not a live hole.

### Changed
- Added `.gitignore` and untracked the 12 committed `__pycache__/*.pyc` files.
  Their churn made `git status --porcelain` permanently non-empty, which tripped
  the clean-tree guard in `scripts/deploy.sh` and blocked every deploy after a
  test run. Also ignores `*.db` and `.env`.

### Tests
- `PortalEscapingTests` pins the fix by accounting for every `catLabel` occurrence
  in `render()` — each must be `var catLabel`, `esc(catLabel)`, or a bare
  truthiness guard; anything left over fails with the offending line number. A
  `+ catLabel` adjacency check would NOT have caught the original bug (the raw use
  sat between `||` operators), and asserting `esc(catLabel)` is present would not
  either (it already appeared elsewhere in `render()` while the hole was open).
  Verified in both directions against the vulnerable line before committing.
- A second test pins that writes are *not* sanitized: escaping belongs at the
  render sink, and mangling stored categories would corrupt MCP round-trips.
- Suite 75 → **77**.

## [0.4.4] — 2026-07-15

### Operations and verification
- First production deployment accepted on the permanent Cloud Run service in
  `asia-southeast1`, backed by an isolated Neon project. The same portal link
  and public no-header MCP URL survived a revision change; mobile acceptance,
  runtime-contract inspection, cleanup, and final public smoke all passed.
- The live smoke now covers portal update, mark-paid, unmark-paid, exact audit
  history, and a real MCP `ClientSession` handshake with the exact nine-tool
  and three-prompt inventories plus bilingual natural-input, ambiguity,
  correction, fuzzy-payment, history, and cleanup flows.
- `/favicon.ico` returns 204 so mobile/browser acceptance runs have a clean
  console instead of a cosmetic missing-favicon error.
- Suite 74 → **75**.

## [0.4.3] — 2026-07-15

### Fixed
- Added `/health` as the Cloud Run-safe health endpoint and moved live smoke
  checks to it. `/healthz` remains available locally and for compatibility,
  but Google's front end reserves some paths ending in `z` and intercepts
  this one with a 404 before requests reach the container.

## [0.4.2] — 2026-07-15
First-deployment hardening: the public MCP, pooled Postgres lifecycle, and
Cloud Run storage posture now fail safely under the production conditions the
local SQLite suite cannot reproduce.

### Fixed
- FastMCP binds its host policy to runtime `HOST` (default `0.0.0.0`), so a
  real Cloud Run Host header completes initialize and `tools/list` instead of
  returning 421. Stateless HTTP returns JSON responses, avoiding the SDK's
  unclosed SSE receive-stream warning while remaining protocol-compliant.
- Postgres thread-local connections evict closed handles and retry a failed
  first transaction statement once on a fresh connection. Mid-transaction
  failures are never replayed; the poisoned connection is evicted and rollback
  failure cannot mask the original error.
- Cloud Run (`K_SERVICE`) refuses to boot without a Postgres `DATABASE_URL`,
  preventing a missing secret binding from silently writing to ephemeral
  SQLite.
- Async MCP tests now close their event loops and complete the initialized
  notification handshake, removing lifecycle warnings from the release gate.

### Operations
- Added `scripts/deploy.sh`: clean-tree guard, explicit SHA build/deploy,
  Secret Manager binding, no `MCP_SECRET`, finite scale, and permanent
  Singapore region/service constants.
- Cloud Build no longer publishes or deploys a mutable `latest` image.
- Live smoke requires the Neon pooled URI and repeats token validation at
  least six times to cross psycopg's default prepared-statement threshold.
- Added regression/static gates for reconnect, production fail-closed,
  external-host MCP, pooled validation, and deployment contract. Suite 63 →
  **74**.

## [0.4.1] — 2026-07-14
Owner's final risk ranking encoded: the dominant risk is a family member
being forced to reconnect (→ disuse), not unauthorized edits.

### Added
- **Compatibility contract** (FEATURE_CONTRACT §5.1, acceptance A8): frozen
  surface = service URL, `/t/<token>` path + her token, `/mcp` mount,
  no-auth-header posture. Runbook gains "Don't break her setup" rules.
- `CompatibilityContractTests` pin the frozen surface in CI (mount path,
  route shapes, credential-free defaults, heavy use never invalidating a
  link). Suite 59 → **63**.

## [0.4.0] — 2026-07-14
Agent-ergonomics rework, motivated by the owner's experience of MCP guidance
"the agent never sees": all behavior now lives in channels agents reliably
read (tool descriptions, results, error strings, annotations) — codified in
**docs/MCP_DESIGN.md**.

### Added
- `expenses_help` tool — playbook-as-a-tool ("START HERE when unsure");
  works on clients that never surface server `instructions`.
- **Three personas as MCP prompts**: 记账 `jizhang` (quick add), 对账
  `duizhang` (settle up), 修复 `xiufu` (fix a mistake).
- Bilingual trigger phrases and cross-references ("to X use tool Y") inside
  every tool description — the channel that drives tool selection.
- **Coached error strings**: wrong amount says what parses ('¥300', '300块');
  bad date says relative words must be converted or omitted; touching paid
  via update redirects to expenses_mark_paid. One-round-trip self-correction.
- Write results carry a `note` with the running unpaid total.
- `expenses_add` records already-paid expenses in one call (`paid=true`,
  audit trail keeps create + mark_paid).
- Tool annotations: reads flagged read-only (fewer client permission
  prompts), delete/revoke flagged destructive.

### Changed
- `expenses_summary` **removed** (9 tools total): redundant with the summary
  already returned by `expenses_list`; redundant read tools split selection
  probability (see MCP_DESIGN.md).
- `amount` params accept numbers **or** strings — pydantic v2 does not coerce
  int→str, so the old `str` type silently rejected numeric arguments from
  agents (exactly the "worked for the dev, failed for the agent" class).
- `expenses_history` returns `{"history": [...]}` (object, not bare array).

### Tests
- Suite 51 → **59**; new `AgentErgonomicsTests` pin triggers, cross-refs,
  annotations, personas, numeric amounts, one-call paid add, result notes,
  and coaching text in errors — regressions in agent-visible channels fail CI.

## [0.3.0] — 2026-07-14
Auth scaled back to the owner's explicit threat model (low-stakes household
ledger, zero-tech user, unguessable URLs); MCP reworked for natural speech.

### Changed
- **Portal links never expire by default** (`expires_at` nullable; NULL =
  never). The holder never renews anything; revocation stays the kill switch.
  Bounded expiry still available via `expires_days`.
- **MCP bearer is now optional**: `MCP_SECRET` set → enforced (401 on
  mismatch); unset → `/mcp` is open (was fail-closed 503).
- `expenses_add`/`mark_paid` dates optional — default **today in `APP_TZ`**
  (default `Asia/Shanghai`), not UTC.
- Amounts tolerate spoken/pasted forms: `¥300`, `300块`, `1,200元`, `300 rmb`.

### Added
- **Natural-language targeting**: `expenses_mark_paid` / `expenses_update` /
  `expenses_delete` accept a fuzzy `query` ("足球课") instead of an id —
  one match acts (mark-paid prefers the unpaid match), several matches return
  candidates for the assistant to disambiguate; zero matches return a hint.
- New MCP tools `expenses_update` and `expenses_delete`; `expenses_list`
  gains a `query` text filter. Tool count now 9.
- Server instructions coach LLM clients: bilingual example utterances,
  defaults, confirm-before-delete, reply in the user's language.
- `store.find()`, `today_str()`; runbook §4 "what you (or she) can say".

### Tests
- Suite 37 → **51** (natural-speech flows, ambiguity, never-expire tokens,
  open/gated middleware) — all green; live MCP smoke re-run in open mode
  covering the full conversational flow end-to-end.

## [0.2.0] — 2026-07-14
First complete implementation (chunks 1–5), ready for first deploy.

### Added
- **Store** (`app/store.py`, `app/db.py`): portable Postgres/sqlite layer;
  create/update/mark-paid/delete/list/summary/history; every mutation writes
  one append-only `expense_history` row in the same transaction (M3); token
  mint/validate/revoke, fail-closed with usage tracking (M2);
  `expense_history.seq` for deterministic ordering.
- **Portal** (`app/web.py`, `app/api.py`, `app/portal.html`): `/t/<token>`
  mobile-first bilingual (中文/EN) page — add, inline edit, mark paid with
  date, filters, totals, per-item history; JSON API revalidates the token on
  every request (401/400/404 mapping).
- **MCP** (`app/mcp_server.py`, `app/main.py`): FastMCP streamable-HTTP with 7
  operator tools incl. `expenses_mint_link`/`expenses_revoke_link`; `/mcp`
  gated by `Authorization: Bearer $MCP_SECRET`, fail-closed when unset; one
  Cloud Run service serves portal + API + MCP.
- **Ops**: `Dockerfile`, `cloudbuild.yaml`, `scripts/mint_link.py`,
  `docs/RUNBOOK.md`.
- **Tests**: 37 (store, HTTP tier, MCP tools, bearer middleware, combined
  app) — run on sqlite with no DB server; plus live smokes: real uvicorn
  portal flow and a real MCP client handshake with bearer auth.

### Changed (architecture pivot, owner direction)
- The feature moved from a `work-dashboards` in-repo portal to this
  **standalone repo**. `work-dashboards` is reference-only (patterns:
  portal-token links, Neon, Cloud Run streamable-HTTP MCP) and receives no
  commits or pushes. Isolation from the business system is structural
  (separate repo / DB / services). MCP hosting: Cloud Run (owner: "already
  works; no need to introduce new tech").
### Removed
- Superseded localStorage prototype (`index.html`) — replaced by the
  server-backed portal (history preserved in git).

## Planning history (v0.1.x, in work-dashboards — superseded)
- `0.1.1` — contract + plan revised per independent adversarial verification
  (3 must-fix / 6 should-fix / 3 nits). Carried forward: M2 (first-class token
  minting), M3 (same-transaction audit writes). Moot after pivot: M1/S2/S3
  (work-dashboards SPA), S5 (tenancy), S6 (money-as-cents audit).
- `0.1.0` — initial contract + plan.
