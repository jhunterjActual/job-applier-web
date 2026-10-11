# Explicit title alternatives candidate

The search form offers an unchecked “Use title alternatives” option. When enabled,
semicolon-separated titles are normalized, deduplicated and limited to three
alternatives of 120 characters each. The preview shows the OR interpretation and
the existing Software Engineer / Software Developer alias. Geography, provider
budgets and title-only filtering retain their existing behavior. This does not
adopt description matching or establish improved live recall.

`POST /api/jobs/search/plan` validates and previews without reading a profile,
queuing a search or calling providers. Both endpoints accept `title_alternatives`
(default false). Blank input keeps the existing resume-suggested mode. Preview
failures and stale responses prevent the browser from submitting a stale plan.

## Persistence contract

Opted-in lists use `ct:title-list:v1:` followed by a compact JSON array in the
existing keywords column. Final encoded length is limited to 500 characters,
matching queue/save admission and the scheduler limit. Repository and scheduler
copying remain lossless; the worker decodes the list once before invoking existing
collectors. Exported raw keywords intentionally retain the versioned value.
Saved-search list/due APIs expose readable text plus the boolean mode for UI
restoration. No schema change or user-data migration is performed.

Unversioned values, including ordinary legacy semicolon strings, keep their single
query interpretation. Public admission rejects the reserved prefix, including
leading-whitespace variants. Malformed stored envelopes fail execution before
providers and remain visible/deletable in saved-search lists.

## Read-only compatibility audit

[title_compatibility_audit.py](../backend/title_compatibility_audit.py) counts marker
occurrences in every saved search and run. Disabled/unscheduled saved searches are
included; active and terminal runs are reported separately. Substring matching
conservatively catches leading Unicode whitespace and interior occurrences; it
can overcount and does not classify intentional encoding or valid JSON. Oversized
payloads and unknown run statuses also require review. Only aggregate counts and
tool hashes leave the database; exceptions emit a fixed failure receipt.

For an explicitly selected existing SQLite snapshot, run from the candidate root:

```text
python backend/title_compatibility_audit.py --sqlite PATH_TO_EXISTING_SNAPSHOT
```

The new `--postgres-service` argument names an existing, operator-reviewed libpq
service; no service, credential, environment file or Bitwarden item is created.
Do not pass a database URL or secret. After separately verifying the target and
authorized principal, the corresponding command is:

```text
python backend/title_compatibility_audit.py --postgres-service REVIEWED_SERVICE
```

PostgreSQL execution requires an already-authorized superuser/BYPASSRLS principal
with SELECT access, read-only repeatable-read isolation and bounded timeouts. It
never disables RLS, grants access, dispatches searches or claims jobs. A tenant-only
view fails before payload queries. SQLite uses mode=ro and query_only, with a
bounded query timer. Read-only access does not guarantee absence of WAL/shared-memory
side effects in every live SQLite configuration; prefer the reviewed snapshot.

Exit 0 means no marker/storage blockers observed in that snapshot, 2 means review
required, and 1 means unavailable/incomplete. All receipts explicitly deny release
authorization. Record the target/service and exact release in the surrounding
sanitized operator receipt: this tool intentionally does not output database
identifiers. Bind its hashes to reviewed bytes and recheck under the deployment's
existing writer/worker coordination. No concurrent-write exclusion is established
by this audit alone. Use the guarded entry points, not the internal collect helper.

Synthetic tests cover read-only behavior, exact fixture content preservation,
privacy, conservative collision counts and mocked PostgreSQL visibility checks.
Hosted PostgreSQL execution, snapshot identity and coordinated rollback remain
unverified; a mock result is not hosted acceptance.

## Hosted release gates remain open

Owner: CareerTrellis maintainer. Review before any deployment of this candidate.

- Audit existing queue/saved-search storage for reserved-prefix collisions using
  aggregate counts, without exposing private search text. A pre-existing canonical
  envelope-looking literal is indistinguishable without that audit; resolve any
  collision explicitly before rollout.
- Deploy compatible web and worker code together. Older workers treat versioned
  values as literal queries. PostgreSQL/R2 authority prohibits the standard rollback
  helper. Prepare and test a new immutable forward-correction release retaining the
  decoder and saved-search compatibility; activate it through a reviewed coordinated
  application-only switch preserving installed host configuration. Never retarget
  old incompatible workers while encoded pending/scheduled searches can reach them.
- Exercise authenticated PostgreSQL save, scheduled dispatch, worker claim and
  completion, cancellation, and restored exports in the authorized hosted gate.
- Live provider recall, eligibility, request cost and latency are still unmeasured.
  Existing synthetic checks and loopback preview do not close SEARCH-003.

Implementation: [parser](../backend/role_queries.py),
[API and admission](../backend/app.py), [worker](../backend/searcher.py).
Tests: [persistence and collection](../backend/test_role_queries.py),
[browser races](../backend/test_role_plan_browser.py).