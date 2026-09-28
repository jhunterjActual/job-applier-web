# Coverage diagnostics and filtering release candidate

Candidate build: 20260928.78, local only. Base: release .77,
`acfed3e535244cb0a6b0ff6003dc19947a3d91d6`. Live state must be rediscovered before
assigning the final release identity or preparing deployment.

The September 25 filtering changes remain in this candidate. September 28 adds
aggregate diagnostics without increasing request, page, time or detail budgets.
UD and ZipRecruiter acquisition remains disabled in code; Serper remains disabled.

## Counter semantics

- `coverage_pages_read`: successfully validated employer catalog pages, including
  cache hits. `coverage_cached_pages` is the subset served by process cache.
  `coverage_http_attempts` counts budget reservations including failed catalog
  transport, not page reads. Public feed refresh accounting is separate.
- `coverage_queries_with_total` and `coverage_advertised_rows`: first-page
  advertised totals for queries that provide totals (Greenhouse and
  SmartRecruiters). Query overlap exists; summing is NOT unique job inventory.
  Lever/Ashby absence of a total is not an advertised zero.
- `coverage_rows_read`: row observations, including repeats across pages/queries.
  For valid catalog rows, the mutually exclusive categories are unlisted rows,
  role exclusions, location exclusions, unknown location, and metadata matches.
  Invalid pages are failures, not empty inventories. Metadata matches still need
  detail validation and are not accepted postings.
- Unknown location candidates retain existing per-board/provider caps and
  `unknown_location_limit`. No affirmative eligibility is inferred from missing
  metadata. This limit is an event counter, not an exact count of omitted jobs.
- Duplicate candidate URLs and candidates excluded by the downstream detail cap
  have separate counters. This is canonical-URL deduplication, not a completed
  employer/requisition cross-provider identity solution.
- Complete and failed boards are separate from page/request caps. A finite
  public feed snapshot is counted as a snapshot and rows; it never claims full
  inventory, an advertised total, or a new HTTP request.

The existing diagnostics allowlist now retains these bounded integer counters,
query mismatches, unknown eligibility and unknown-location cap events. No search
terms, locations, board names, URLs or job contents are added. Existing persistence
is alert-driven: a healthy source without a notice need not create a history row.
Multiple notices can contain the same provider counters; do not sum those rows.
The maintainer-report export retains its existing narrower counter allowlist.
Boards skipped after the shared budget expires appear under request-limit events,
not `coverage_failed_boards`, which counts exceptions from attempted boards.
No database migration or retention change is needed.

## Acceptance limits

The synthetic six-cell controls are not a current known-positive inventory.
The root artifact `COVERAGE_REFERENCE_BASELINE_20260928.md` identifies historical
references, missing cells and the duty-based Dune data-engineer recall limitation.
Incremental cursors, employer expansion, broader role recall, source permission,
full detail adapters and a new bounded two-window acceptance series remain open.
Neither prior failed window is revised by these changes.
