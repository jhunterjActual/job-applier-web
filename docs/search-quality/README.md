# SEARCH-002 offline baseline

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.

October 7, 2026. Owner: CareerTrellis product/engineering. This completes the first
bounded SEARCH-002 slice; end-to-end baseline acceptance remains open.

The [versioned reference](reference-v1.json) contains 42 fabricated listings over
the six COV-12 cells: software engineer, data engineer and enterprise architect,
each in Dayton OH and US remote. Each cell has two distinct positive requisitions,
a second-source duplicate and wrong-role, wrong-location, unknown-location and
closed controls. Judgments are explicit, independent of the filter output. These
are deliberately small regression examples, not measured market inventory.

The [baseline](baseline-20261007.json) executes the pure role and detail-location
filters from local release `4095417`. Their SHA-256 is recorded in the receipt and
matches the later local hostname/UX checkout. It does not import the application,
open a database, read credentials or call providers. The
[repeat comparison](replay-20261007.json) establishes reproducibility, not a
product improvement: non-regression passes, improvement and acceptance are false.

| Cell (both geographies) | Known-set recall | Unique eligible jobs/employers | P@10 |
| --- | --- | --- | --- |
| Software engineer | 2/2 | 2/2 | 0.2 |
| Data engineer | 1/2 | 1/1 | 0.1 |
| Enterprise architect | 1/2 | 1/1 | 0.1 |

The data/architecture alternate-title positives are missed. They are candidates
for SEARCH-003's explicit optional description scope, not instructions to silently
widen the current default. Returned order is fixture order, not production rank.
P@10 always divides by ten, including empty positions; precision at returned count
is reported separately. Duplicate identities cannot inflate precision, recall or
employer counts. Recall measures only the known positive identities in each cell.

All six synthetic cells are present. **Missing evidence:** fresh independent
real-world reference coverage, discovery recall, extraction success, full
eligibility, AI scoring/ranking, real latency and HTTP cost. Those metrics remain
null or explicitly unmeasured, never zero. Closed and duplicate controls pass the
two selected filters because those filters do not perform stale checking or
deduplication; the reported rates are not production defect rates.

## Reproduce and compare

From the repository root, using the existing Python environment:

```powershell
backend/venv/Scripts/python.exe -B scripts/search_quality.py --reference docs/search-quality/reference-v1.json --filters docs/search-quality/fixtures/candidate_filters_baseline.py --output baseline-new.json
backend/venv/Scripts/python.exe -B scripts/search_quality.py --reference docs/search-quality/reference-v1.json --filters PATH_TO_CANDIDATE/backend/candidate_filters.py --before baseline-new.json --output comparison-new.json
```

Output creation is exclusive: choose a new path for each report. `--filters`
executes a trusted local Python source file. Use the exact retained source/hash
to reproduce the baseline; use a candidate source to measure a proposed change.
An unchanged-source comparison should not pass the improvement requirement.

Proposed thresholds were fixed in the reference before this baseline: no per-cell
P@10 or known-set-recall decrease; at least one cell gains 0.10 absolute recall.
These are regression proposals, not live acceptance floors. Changing the reference
or thresholds requires a new baseline; the comparator checks the complete hash.
Do not compare synthetic fixture-order ranking against real provider ranking.

For future complete offline runs, use `--observations PATH` instead of `--filters`.
Use the receipt's `observations` object as the schema: `reference_version`, `kind`,
and cells with `id`, ordered `ranked_ids`, optional integer `http_attempts`,
`detail_attempts`, `detail_successes`, nonnegative finite `latency_ms`, aggregate
`diagnostics`, and `unmeasured_stages`. IDs must already have reference judgments;
unjudged results and conflicting canonical identities are rejected. HTTP attempts
include retries; detail successes must not exceed attempts. Empty denominators
produce null rates. Missing cells are explicitly listed and cannot pass comparison.

## COV-10 diagnostic review

The current source exposes aggregate `coverage_*` counters in
`backend/source_diagnostics.py` on the release checkout. The evaluator accepts a
bounded selection of those names, rejects content fields, and preserves one
snapshot per cell. It never sums repeated diagnostic-history rows.

| Stage | Counters to retain in an authorized full-run observation | Limit |
| --- | --- | --- |
| Discovery | coverage_rows_read, coverage_pages_read, coverage_cached_pages, coverage_http_attempts, coverage_advertised_rows, coverage_failed_boards, catalog_page_limits | Advertised totals and cached rows are not independent eligible inventory |
| Role/location | coverage_role_excluded, coverage_location_excluded, coverage_location_unknown, coverage_metadata_matches | Unknown location is not eligibility; detail-stage exclusions must be kept distinct |
| Detail | coverage_detail_cap_excluded, accepted, rejected, provider_error, timeouts | Supply separate detail attempt/success totals; rejection can be a successful extraction |
| Eligibility | query_mismatch, eligibility_unknown, stale | Use adjudicated reference identity for quality, not AI score as truth |
| Scoring | No dedicated COV-10 scoring counter established in this review | Missing: add a scoped offline observation before claiming scoring loss attribution |

The pure-filter replay has no full-run COV-10 snapshot; its diagnostics are empty.
Historical references and their restrictions remain in
the existing COV-12 record (local-only evidence: `../../artifacts/COVERAGE_REFERENCE_BASELINE_20260928.md`).
No new live collection or paid requests were performed.

## Verification and retention

The [isolated receipt](verification-20261007.json) records 295 passing tests,
zero failures/errors/skips, successful syntax compilation and temporary-directory
removal. After the final validation cases were added, all 10 evaluator tests also
passed. The earlier combined run included the first eight evaluator tests.

Run evaluator regressions with `python -B -m unittest discover -s scripts -p test_search_quality.py`.
The isolated [verification runner](../../scripts/verify_search_quality.py) also runs
the repository's required P0/object suites with a temporary data directory and
temporary bytecode, then compiles backend project source (excluding the existing
third-party virtual environment). It writes a new verification receipt and removes
its temporary directory. For repeat verification, pass `--output NEW_RECEIPT_PATH`;
existing receipts are refused before running tests. The existing root checkout is older than the filter
source: root P0 results validate the checkout, not deployment of release 4095417.

No runtime/UI changes: a server smoke is not applicable. No Docker resources,
downloads, paid calls, deployment, commits, pushes or PRs are part of this slice.
Retain the scripts, synthetic reference and small JSON reports as source/evidence;
owner CareerTrellis engineering, review when SEARCH-003 changes the candidate or
the reference version changes. Local free space before verification was about
1.15 TiB; the temporary test directory is disposable and its removal is checked.
Retained task source, fixtures and receipts total approximately 60 KiB. The new
scripts pass Git's whitespace check. The broader backlog diff has pre-existing
trailing whitespace outside this task's four-line addition; it was preserved.
Nothing is staged. The bounded friction note (local-only evidence: `../../artifacts/search002-friction-20261007.md`)
is indexed for later shared-log consolidation.

SEARCH-002 remains open for the explicitly missing full-pipeline measurements.
Per the balanced backlog order, AI-001 and MON-001/002 are the next independent
baseline slices before another cycle solely on search.
