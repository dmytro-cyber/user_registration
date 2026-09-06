# Client feedback bug fixes — 2026-09-06

Scope: pricing, recommendations, accident/damage data, Won inventory and reliable
updates. New Bidding Hub notes and reconditioning fields are excluded.

## Deployment

Pause workers/imports while deploying schema and application changes. From
`entities`, with the application's environment active, run `alembic upgrade head`.
Deploy both entities and parsers and restart the workers.

New migrations add the manual-recommendation flag, valuation source snapshots,
and inventory records for existing Won cars that lack one. Recovered records keep
unknown purchase prices null; no historical bid is invented. Already-deleted cars
or wins never delivered by an upstream system cannot be recovered by this migration.

## Behavior

- Recommendation requests ignore bundled prices. Change a price using a separate
  price-only request. Resubmitting an unchanged price leaves calculations intact.
- Unchanged auction bids preserve manual recommendations. Changed bids above the
  limit can invalidate them. Pre-migration manual decisions cannot be identified
  reliably; re-save those recommendations to establish the explicit flag.
- Market source values are retained separately. Missing sources do not disappear
  from the average, and unchanged sources preserve a manual valuation. Legacy
  nonzero averages without source details wait for a complete source baseline;
  zero prices can recover from a partial response.
- Bulk batches lock existing cars before reading snapshots, exclude user financial
  fields, and commit cars, damage and photos together. Manual-car damage survives.
  Keep upstream batches bounded as before. Concurrently inserted rows are not
  overwritten by an import that did not lock/read them.
- Damage filtering excludes FRONT END in either primary or secondary damage when
  FRONT END is not selected.
- AutoCheck counts dated collision/accident events, deduplicating by normalized
  date. Missing reports and undated collision rows produce unknown, not zero.
  The available report has dates rather than event IDs; two separate accidents
  on one date cannot be distinguished from duplicate entries using that source.
- Investment edits and deletions lock the parent inventory first, then the child,
  and save totals and history in the same transaction.
- Repeated Won transitions preserve existing inventory costs and do not duplicate
  inventory. Cleanup excludes Won cars, including final filter-delete statements.

## Validation limits

The focused pytest suites cover CRUD, parser tasks, market source merging,
vehicle/Bidding Hub APIs, investment recalculation and AutoCheck parsing (including
the saved report). Tests use SQLite and mocked external services. PostgreSQL
timeout commands are omitted only in the SQLite task fixture; this does not test
PostgreSQL locking, production load, browser flows or live upstream availability.

Row locking serializes database mutations; it cannot identify an obsolete value
submitted by an old browser form as distinct from a deliberate edit to the same
field. Full browser conflict detection requires a version/precondition contract.
