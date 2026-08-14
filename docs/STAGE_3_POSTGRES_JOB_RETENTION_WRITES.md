# Stage 3: PostgreSQL Job-Retention Write Slice

This slice adds a dual-mode repository for owner-scoped retention of untouched
job records. Standalone local use retains SQLite. Shared-host preview, archive,
restore, and permanent deletion use PostgreSQL transactions and forced RLS, but
the overall repository transition remains incomplete and must not be activated
on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to live staging, apply this migration to the
live database, import the permanent SQLite source, change data authority,
activate a release, restart services, or change R2, Caddy, users, environment
files, or secret files for this slice.

SQLite and generated files remain authoritative. PostgreSQL and R2 rehearsal
targets remain disposable until the final repository, no-omission import,
reconciliation, recovery, and authorization gates pass.

## Converted operations and protected transaction contract

The retention repository owns:

- an owner-only preview of candidate counts and a five-row public sample for
  archive, restore, and permanent deletion;
- archive and restore of jobs that remain untouched and have no application or
  protected history; and
- permanent deletion of those same protected candidates with an atomic,
  privacy-minimized suppression upsert so a later search does not silently
  recreate an intentionally deleted posting.

Jobs with an application, lifecycle history, engagement, or generated-material
reference are excluded. This slice never
deletes an application, application history, stored-object row, or encrypted R2
object. Single-job deletion may address another job status, but it still fails
closed if any application, history, engagement, or generated-material reference
exists; it is not a way to bypass the protected-history boundary.

Hosted ownership comes only from authenticated server context. Every
PostgreSQL operation uses transaction-local tenant context, explicit owner
predicates, and forced RLS. Requests cannot choose an owner ID. Missing and
foreign rows are indistinguishable, and preview counts and samples include only
the authenticated owner's rows.

Apply operations lock the candidate rows in deterministic order and revalidate
the complete protected predicate before writing. The per-process HMAC preview
token is owner- and action-bound optimistic-concurrency state rather than
authorization. A changed, wrong-owner, or wrong-action preview fails without
mutation, and a process restart invalidates every earlier token. Concurrent
lifecycle, verification, and retention writes serialize on the job row.

Each bulk mutation is capped by `MAX_RETENTION_BATCH` at 500 candidates. Its
PostgreSQL transaction has a five-second lock timeout and a 30-second statement
timeout. An oversized or stale request fails closed instead of holding an
unbounded transaction. SQL values are parameterized.
Responses, errors, and minimized operational events omit owner IDs, WorkOS IDs,
complete URLs, URL fingerprints, object IDs or keys, local paths, database
URLs, credentials, and unrelated tenant data.

## Migration and rollback

Migration `20260813_0006` adds only
`idx_jobs_owner_retention_candidates` on `(owner_user_id, status, id)`, including
`archived_from_status`, for rows whose status is `matched` or `archived`. It is
created and dropped concurrently with bounded lock and statement timeouts, does
not copy user data, does not alter RLS or foreign keys, and does not change
repository authority. The migration uses a five-second lock timeout and a
30-minute statement timeout for its concurrent operation.

Apply it only to disposable PostgreSQL 18 targets while the activation gate is
false. After an eventual first authoritative PostgreSQL/R2 write, a code-only
rollback to writable SQLite is forbidden. This slice does not reach that
boundary, and Alembic downgrade is not a staging rollback mechanism.

## Explicit exclusions

This slice does not convert or authorize:

- deletion of any job with applications, lifecycle history, engagements,
  generated materials, stored-object metadata, or other protected dependents;
- stored-object tombstoning, encrypted R2 deletion, cross-store retry or
  reconciliation, or orphan-object cleanup;
- application operations outside the converted lifecycle, engagement,
  interview-preparation, record-list, and insight repository boundaries;
- tailoring or generated-material creation, replacement, download, or deletion;
- saved-search claims, retries, leases, durable work, or worker execution;
- headquarters cache, export/account deletion, or hosted backup/restore; or
- deployment activation, permanent import, authority transition, R2 changes,
  public signup, or additional users.

The later single-job dependent-deletion slice adds that bounded cross-store
contract: it atomically records suppression,
tombstones object metadata exclusive to the deleted application, preserves any
same-owner survivor reference, and removes PostgreSQL dependents, then invokes
the existing reconciler only after commit. See
`STAGE_3_POSTGRES_DEPENDENT_JOB_DELETION.md`. The still later explicit
PostgreSQL-only dependent cleanup action reuses that atomic contract without
widening the original untouched action or changing local SQLite behavior; see
`STAGE_3_POSTGRES_DEPENDENT_BULK_CLEANUP.md`. Account deletion remains a
separate privacy workflow.

## Verification gates

- [ ] Temporary SQLite tests prove preview parity, reversible archive/restore,
      protected-record exclusion, stale-preview rejection, atomic suppression
      plus deletion, candidate limits, and complete rollback.
- [ ] Route tests prove preview, bulk retention, and eligible single deletion use
      the configured repository and shared mode has no SQLite fallback.
- [ ] Alembic reports exactly one current head at `20260813_0008`; repeated
      upgrade is a no-op and migration `20260813_0006`'s concurrent
      cleanup-candidate index is valid and ready.
- [ ] Disposable PostgreSQL 18 tests prove missing/cross-owner denial, owner-only
      counts and samples, protected-dependent denial, bounded batches, atomic
      suppression/deletion, and deterministic concurrent retention, lifecycle,
      and verification behavior.
- [ ] Wrong-owner, wrong-action, changed, and prior-process preview state
      produces a bounded non-disclosing error and no partial write.
- [ ] Responses, exceptions, and logs omit internal ownership, identity, URLs,
      fingerprints, credentials, private-object metadata, and unrelated tenant
      data.
- [ ] Local tests, compilation, dependency and shell checks, exact-diff security
      review, required GitHub checks, and loopback runtime smoke pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live staging
      state changes.

The final authority and rollback contract remains in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.

Owner-scoped engagement tracking and interview-preparation workspace
operations are documented in `STAGE_3_POSTGRES_APPLICATION_OPERATIONS.md`.
