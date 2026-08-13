# Stage 3: PostgreSQL Saved-Search Write Slice

This slice completes the low-coupling saved-search aggregate in the dual-mode
repository. Standalone local use retains SQLite. The hosted implementation adds
owner-scoped saved-search creation/upsert and post-run reminder advancement in
PostgreSQL. It does **not** complete the hosted repository transition and must
not be activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to the live staging environment, apply this
migration to the live staging database, import the permanent SQLite source,
change data authority, activate a release, restart services, or change R2,
Caddy, users, or secret files for this slice.

SQLite and generated files remain authoritative. PostgreSQL and R2 rehearsal
targets remain disposable until the complete repository transition passes the
final import, reconciliation, recovery, and authorization gates.

## Converted operations and transaction contract

The discovery repository now owns:

- creation/upsert keyed by owner, normalized keywords, and location;
- re-enabling an existing matching saved search while preserving `created_at`;
- server-computed `last_run_at` and daily/weekly `next_alert_at` values; and
- atomic post-run advancement of an enabled saved search by owner-scoped ID.

Hosted ownership comes only from authenticated server context. Each
PostgreSQL operation runs in one transaction with trusted transaction-local
tenant context, an explicit owner predicate, and forced RLS. The request cannot
choose an owner ID. A missing, disabled, or other-owner saved-search ID returns
the same not-found result before background work is enqueued.

Timestamps are generated server-side as aware UTC values. Reminder frequency
is restricted to `none`, `daily`, or `weekly`; `none` clears `next_alert_at`.
Monotonic updates prevent an older concurrent request from replacing the name,
frequency, recorded run, or next reminder from a newer request. Returned rows
omit `owner_user_id`.

Selecting an existing saved search advances that record's scheduling metadata,
while the submitted keywords and location are run. This preserves the existing
editor behavior: a user can select a saved search and adjust the terms for that
run without silently reverting the query. Persisting those edits remains an
explicit save operation.

## Migration and rollback

Migration `20260812_0004` adds and validates only the saved-search frequency
check constraint. It does not copy data, change RLS, or change repository
authority. `20260812_0003` remains the supporting owner/due index. Apply both
only to disposable PostgreSQL test targets while the hard gate is false.

The constraint is installed `NOT VALID` and then validated so the initial
installation does not scan existing rows under the stronger lock. Validation
fails closed if a rehearsal import contains an unsupported legacy frequency.
Staging and production use forward migrations only during the final reviewed
maintenance window.

After an eventual first PostgreSQL/R2 authoritative write, a code-only rollback
to writable SQLite is forbidden. This slice does not reach that boundary.

## Explicit exclusions

This slice does not convert or authorize:

- search-provider execution, job-result persistence, process-global search
  state, durable work claiming, retries, leases, or worker scheduling;
- job creation, lifecycle, verification, archive/restore, cleanup, permanent
  deletion, or coupled suppression creation;
- applications, engagements, interview preparation, generated materials,
  headquarters cache, export/account deletion, or hosted backup/restore; or
- deployment activation, permanent import, repository authority transition,
  R2 changes, public signup, or additional users.

Advancing reminder metadata before an in-process background search preserves
the current standalone behavior but is not a durable claim. Crash-safe
claim/retry semantics belong to the later worker aggregate.

Owner-scoped manual/discovery job creation and initial score completion are
handled by the next bounded slice documented in
`STAGE_3_POSTGRES_JOB_CATALOG_WRITES.md`; its inclusion does not expand this
saved-search transaction or authorize lifecycle operations.

## Verification gates

- [ ] Clean local SQLite tests prove create, conflict update, re-enable,
      daily/weekly/none scheduling, stored-query execution, and missing-ID
      rejection without touching the user's workspace.
- [ ] Route tests prove both write branches use the configured repository and
      enqueue no work after failed authorization.
- [ ] Alembic reports one head at `20260812_0004`; upgrade is idempotent and the
      validated frequency constraint rejects unsupported values.
- [ ] Disposable PostgreSQL 18 tests prove same-query isolation between two
      owners, owner-scoped conflict updates, cross-owner advance denial,
      timezone validation, and monotonic concurrent writes.
- [ ] Returned data and logs omit internal owner IDs, WorkOS IDs, credentials,
      database URLs, and unrelated search data.
- [ ] Local tests, syntax checks, dependency checks, exact-diff security review,
      required GitHub checks, and loopback build/dashboard/listener smoke pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live staging
      state changes.

The complete data-authority contract and eventual cutover sequence remain in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
