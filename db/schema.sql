-- Family Expenses — schema (v0.2.0)
--
-- PORTABLE DDL: runs unmodified on Postgres (Neon, production) and sqlite
-- (tests). Rules that keep it portable:
--   * application-managed ISO-8601 UTC timestamps (TEXT) — no triggers,
--     no now()/to_char defaults;
--   * JSON audit snapshots stored as TEXT;
--   * no arrays, no PG-only expressions or casts;
--   * idempotent (CREATE TABLE IF NOT EXISTS) — applied at startup; dated
--     migration files begin only when a breaking change first appears.

CREATE TABLE IF NOT EXISTS expenses (
  id            TEXT PRIMARY KEY,
  date          TEXT NOT NULL,
  amount        REAL NOT NULL CHECK (amount > 0),
  currency      TEXT NOT NULL DEFAULT 'CNY',
  category      TEXT,
  description   TEXT,
  paid          BOOLEAN NOT NULL DEFAULT FALSE,
  paid_date     TEXT,
  submitted_by  TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  CONSTRAINT expenses_paid_date_check CHECK (
    paid = FALSE OR (paid_date IS NOT NULL AND paid_date <> '')
  )
);
CREATE INDEX IF NOT EXISTS idx_expenses_paid ON expenses(paid);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date);

-- Append-only audit: one row per mutation; never updated or deleted.
CREATE TABLE IF NOT EXISTS expense_history (
  id          TEXT PRIMARY KEY,
  expense_id  TEXT NOT NULL,
  -- monotonic per-expense sequence (0-based); second-resolution timestamps
  -- alone cannot order same-second mutations deterministically
  seq         INTEGER NOT NULL,
  action      TEXT NOT NULL CHECK (
                action IN ('create', 'update', 'mark_paid', 'unmark_paid', 'delete')
              ),
  changed_by  TEXT,
  changed_at  TEXT NOT NULL,
  snapshot    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expense_history_expense
  ON expense_history(expense_id, seq);

-- Bookmarkable household links (pattern adapted from work-dashboards
-- portal_tokens, minus tenancy/scoping). expires_at NULL = never expires —
-- the household default; the holder never renews anything. Revocation is the
-- kill switch.
CREATE TABLE IF NOT EXISTS access_tokens (
  id            TEXT PRIMARY KEY,
  token         TEXT NOT NULL UNIQUE,
  label         TEXT,
  expires_at    TEXT,
  revoked       BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TEXT NOT NULL,
  last_used_at  TEXT,
  use_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_access_tokens_token ON access_tokens(token);
