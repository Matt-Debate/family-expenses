-- Family Expenses — constraints applied BEST-EFFORT at startup.
--
-- Why this is not in schema.sql: everything there is unconditional, and a
-- failure there rightly stops the service. These statements can fail against
-- data that already exists — and this is a live household portal, so a
-- constraint that cannot be applied must degrade to a logged warning rather
-- than a service that will not boot at 7am. app/db.py runs each statement in
-- its own transaction and warns on failure (see Database.init).
--
-- Each statement must therefore be idempotent AND independently skippable.

-- Per-expense history sequence. `seq` is computed inside the write
-- transaction, so two concurrent mutations of the SAME expense could read the
-- same value and both insert it — silently corrupting the audit order that
-- history() sorts by. Household-scale concurrency makes that very unlikely,
-- which is exactly why it would go unnoticed; this turns it into a loud error.
-- Operator check on 2026-08-11 found 0 duplicate groups in production, which
-- is why this could ship as a plain index rather than a repair migration.
-- That was a point-in-time observation, not a repo-verifiable fact.
CREATE UNIQUE INDEX IF NOT EXISTS uq_expense_history_expense_seq
  ON expense_history (expense_id, seq);
