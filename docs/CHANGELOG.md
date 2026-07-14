# Changelog — Family Expenses

Semantic versioning. Unreleased work accumulates under [Unreleased] and is cut
to a release entry when a chunk set ships.

## [Unreleased]
### Changed
- **v0.2.0 architecture pivot (2026-07-14, owner direction):** the feature
  moves from a `work-dashboards` in-repo portal to this **standalone repo**.
  `work-dashboards` is reference-only (patterns: portal-token links, Neon,
  Cloud Run streamable-HTTP MCP) and receives no commits or pushes. Isolation
  from the business system is now structural (separate repo / DB / services).
  MCP hosting: Cloud Run (owner: "already works; no need to introduce new
  tech").
### Removed
- Superseded localStorage prototype (`index.html`) — replaced by the
  server-backed portal (history preserved in git).
### Added
- Feature contract v0.2.0, implementation plan (chunked), portable
  `db/schema.sql` (`expenses`, `expense_history`, `access_tokens`).

## Planning history (v0.1.x, in work-dashboards — superseded)
- `0.1.1` — contract + plan revised per independent adversarial verification
  (3 must-fix / 6 should-fix / 3 nits). Carried forward: M2 (first-class token
  minting), M3 (same-transaction audit writes). Moot after pivot: M1/S2/S3
  (work-dashboards SPA), S5 (tenancy), S6 (money-as-cents audit).
- `0.1.0` — initial contract + plan.
