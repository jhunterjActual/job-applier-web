# SEARCH-003 title-format compatibility audit

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.

Continued the unpublished title-alternatives candidate with a
read-only compatibility audit (local-only evidence: `../../.worktrees/search-role-alternatives-20261009/backend/title_compatibility_audit.py`)
and tests (local-only evidence: `../../.worktrees/search-role-alternatives-20261009/backend/test_title_compatibility_audit.py`).
This is operational gate preparation, not a new application release. The search UI,
worker implementation and schema were unchanged in this continuation.

The audit covers every saved-search row, including disabled and unscheduled rows;
active and terminal runs are counted separately. Conservative substring detection
includes whitespace-prefixed/interior markers. Any marker, oversized value or
unknown status requires review. No raw search text, row/user identifiers or driver
errors are emitted. PostgreSQL requires an already-authorized global-visibility
principal and a read-only repeatable-read transaction; the tool does not change
RLS or invoke dispatch/claim functions. SQLite opens only an explicit existing file
read-only, with query_only and bounded execution. No credentials or services created.

## Verified evidence

- Final test receipt (local-only evidence: `../../artifacts/title-audit-verification-final-20261010.json`):
  384 tests, zero failures/errors, two expected Linux/systemd/POSIX skips. Includes
  nine new audit tests and the required P0/object-store/import/stored-object suites.
  Synthetic SQLite exercises content preservation, write rejection, collisions,
  historical/disabled rows, privacy and CLI results. PostgreSQL visibility and
  transaction controls are mocked; no PostgreSQL connection was made.
- Final compileall receipt (local-only evidence: `../../artifacts/title-audit-compileall-final-20261010.json`):
  302 source files compiled from byte-verified temporary copies; snapshot removed.
  No application preview was needed because this slice changes only the standalone
  audit and tests. Previous UI evidence remains in the candidate handoff.
- Independent source review found no blocking audit issue; the primary reviewed the
  resulting source and test evidence. Diff whitespace check passed; neither checkout
  has staged files. Root dirty work and protected data were preserved.
- Initial tests exposed unclosed fixture connections, not audit assertion failures.
  Explicit closing fixed the fixtures. Cleanup receipt (local-only evidence: `../../artifacts/title-audit-fixture-cleanup-20261010.json`)
  verifies seven exact synthetic databases/directories removed after process exit,
  schema/row-set checks, containment and hash checks. 86,016 logical bytes removed;
  physical disk reclamation was not measured. Final test temporary tree removed.

## Open acceptance and next action

Use the documented guarded entry points and operator prerequisites (local-only evidence: `../../.worktrees/search-role-alternatives-20261009/docs/SEARCH_TITLE_ALTERNATIVES.md#read-only-compatibility-audit`).
The audit is a snapshot and never authorizes a release. Its private target/service
must be bound separately to the verified database and exact release; receipts
intentionally omit target identifiers. Repeat under existing deployment coordination
before using its counts. No live PostgreSQL SQL/RLS verification, hosted collision
scan, rollback rehearsal, or provider recall/cost/latency measurement occurred.
Conservative matching can overcount; malformed/valid JSON is not classified.
SQLite fixture checks do not prove absence of WAL/shared-memory side effects for
all live configurations. No new privileges, services, deployment, commit, push or PR.

SEARCH-003 remains open. The next hosted step is the independently authorized,
verified-target read-only audit and compatible worker/rollback rehearsal described
in the existing candidate contract.

## Retained resources and helper adoption

This continuation's resource delta (local-only evidence: `../../artifacts/title-audit-resources-20261010.json`)
updates the candidate register with current source hashes, sizes and exact artifact
paths. Owner: Hunter / CareerTrellis agent workflow. Retain the unpublished worktree,
receipts, verifier and guarded document snapshots until integration or explicit
abandonment; review on the next roadmap/release task. No Docker, downloads, installed
packages, live database changes or retained preview processes were created.

Procedure index and PowerShell 7.6.6 FullLanguage were checked. Actual discovery,
bounded reads, Git verification, native compiler/link checks, UTF-8/JSON writes and
guarded Markdown edits used maintained Windows helpers. The existing short-path
compiler workflow was reused successfully. One repeated -Glob argument was rejected;
current signature inspection and a single array parameter recovered. The
indexed pending note (local-only evidence: `../../artifacts/title-audit-friction-20261010.md`) records that
partial adoption honestly. Guarded edits have exact-readback receipts; no manufactured
helper exercise, measured savings or general prevention claim.