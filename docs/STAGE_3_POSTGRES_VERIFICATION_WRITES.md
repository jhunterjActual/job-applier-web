# Stage 3: PostgreSQL Posting-Verification Write Slice

This slice adds a dual-mode repository for owner-scoped posting verification.
Standalone local use retains SQLite. Shared-host verification uses PostgreSQL
transactions and forced RLS, but the overall repository transition remains
incomplete and must not be activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to live staging, migrate or import the live
database, change the authority marker, activate a release, restart services, or
change R2, Caddy, users, environment files, or secret files for this slice.

SQLite and generated files remain authoritative. PostgreSQL and R2 rehearsal
targets remain disposable until the final repository, import, reconciliation,
recovery, and authorization gates pass.

## Converted operation and transaction contract

The verification repository now owns the complete database mutation following
one user-requested posting inspection:

- load the owner-scoped saved URL used for inspection;
- lock and revalidate that exact URL before any write;
- refresh bounded posting metadata and clear a prior expiry marker when the
  source remains active; or
- mark the job expired and, only for pre-application pipeline states, close the
  coupled application row in the same transaction.

An unreadable or temporarily unavailable source does not alter the job. A row
deleted or a URL changed during network inspection produces a non-disclosing
not-found/conflict response and no partial write.

Hosted ownership comes only from authenticated server context. Every
PostgreSQL write uses one transaction with transaction-local tenant context,
explicit owner predicates, row locking, and forced RLS. Request data cannot
select an owner. SQL values are parameterized, and returned results omit owner
IDs, WorkOS IDs, URL fingerprints, object identifiers, local paths,
credentials, database URLs, and raw provider errors.

Verification and lifecycle transitions acquire the job lock before touching an
application row. Concurrent expiry and lifecycle actions therefore serialize
without leaving job and application statuses inconsistent. An already-applied
or later lifecycle state is retained even when the posting is later marked
expired.

## Migration and rollback

This slice adds no migration. Alembic remains at the single head
`20260813_0005`; existing owner-scoped relationships, forced-RLS policies, and
the lifecycle-history index are sufficient.

No data is copied and authority does not change. After an eventual first
authoritative PostgreSQL/R2 write, a code-only rollback to writable SQLite is
forbidden. This slice does not reach that boundary.

## Explicit exclusions

This slice does not convert or authorize:

- cleanup preview/apply, archive/restore, permanent job deletion, or the
  suppression write coupled to deletion;
- application operations outside lifecycle status coupling, engagements,
  interview preparation, or follow-up management;
- tailoring or generated-material object creation, replacement, download, or
  deletion;
- saved-search claims, retries, leases, durable jobs, or worker execution;
- headquarters cache, export/account deletion, or hosted backup/restore; or
- deployment activation, permanent import, authority transition, R2 changes,
  public signup, or additional users.

The source inspection itself remains bounded network work outside the database
transaction. Only its validated outcome enters the owner-scoped transaction.

## Verification gates

- [ ] Temporary SQLite tests prove active refresh, stale closure, application
      coupling, changed-target rollback, and unavailable-source no-write parity.
- [ ] Route tests prove the configured repository is used before inspection and
      shared mode has no SQLite fallback.
- [ ] Alembic reports exactly one head at `20260813_0005`; this slice adds no
      schema or RLS-policy change.
- [ ] Disposable PostgreSQL 18 tests prove missing/cross-owner denial, active
      refresh, atomic pre-application closure, changed-target rollback, and
      serialization with concurrent lifecycle writes.
- [ ] Responses, exceptions, and logs omit internal ownership, identity,
      credentials, private-object metadata, and unrelated tenant data.
- [ ] Local tests, compilation, dependency and shell checks, exact-diff security
      review, required GitHub checks, and loopback runtime smoke pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live staging
      state changes.

The final authority and rollback contract remains in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
