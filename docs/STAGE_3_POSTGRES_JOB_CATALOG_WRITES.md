# Stage 3: PostgreSQL Job-Catalog Write Slice

This slice extends the dual-mode discovery repository with owner-scoped manual
and discovery job creation plus initial match-score completion. Standalone local
use retains SQLite. It does **not** complete the hosted repository transition
and must not be activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to the live staging environment, apply a
schema change to the live staging database, import the permanent SQLite source,
change data authority, activate a release, restart services, or change R2,
Caddy, users, environment files, or secret files for this slice.

SQLite and generated files remain authoritative. PostgreSQL and R2 rehearsal
targets remain disposable until the complete repository transition passes the
final import, reconciliation, recovery, and authorization gates.

## Converted operations and transaction contract

The discovery repository now owns:

- owner-scoped creation of a manually entered job;
- owner-scoped persistence of discovered jobs using the existing canonical URL
  identity for that owner;
- deterministic duplicate handling within one owner without revealing whether
  another owner has the same canonical URL; and
- initial match-score and analysis completion for an owner-scoped catalog row.

Hosted ownership comes only from authenticated server context. Every
PostgreSQL operation runs in a transaction with trusted transaction-local tenant
context, an explicit owner predicate, and forced RLS. Requests cannot choose an
owner ID. Missing context exposes no protected row and every failure rolls back
the complete in-scope write rather than leaving partial catalog state.

Inputs retain the existing bounded normalization and validation contract. SQL
values remain parameterized. Responses and errors omit `owner_user_id`, WorkOS
identifiers, URL fingerprints, object identifiers, local paths, credentials,
database URLs, raw provider errors, and unrelated tenant data.

## Migration and rollback

This slice itself adds no migration. The later retention slice advances the
repository migration chain to `20260813_0006`; the existing owner-scoped
uniqueness, relationships, and forced RLS policy remain unchanged.

No data is copied and repository authority does not change. PostgreSQL tests and
imports use disposable targets only while the hard gate is false. After an
eventual first authoritative PostgreSQL/R2 write, a code-only rollback to
writable SQLite is forbidden. This slice does not reach that boundary.

## Explicit exclusions

This slice does not convert or authorize:

- deletion of jobs with applications, history, generated materials, stored
  objects, or other protected dependent records;
- application operations outside separately converted lifecycle, workspace,
  record-list, and insight repository boundaries;
- tailoring, generated-material creation/replacement/download/deletion, or
  private-object operations;
- saved-search claiming, retries, leases, durable work, or worker execution;
- headquarters cache, source-diagnostic expansion, export/account deletion, or
  hosted backup/restore; or
- deployment activation, permanent import, data-authority transition, R2
  changes, public signup, or additional users.

Search-provider network execution and process-global search state remain
outside the repository transaction. This slice persists only the accepted job
catalog result and its initial score completion.

The later provider-context slice routes the manual-import scoring profile and
credential reads through the authenticated owner's existing repositories. The
reviewed job is saved before the external provider call, and successful match
completion remains a separate owner-scoped job-catalog transaction. That
consumer contract is documented in
`STAGE_3_POSTGRES_PROVIDER_CONTEXT_READS.md`.

Atomic lifecycle transitions and undo are handled by the next bounded slice in
`STAGE_3_POSTGRES_LIFECYCLE_WRITES.md`; they remain separate from catalog
creation and initial scoring.

Posting refresh and expiry writes are handled by
`STAGE_3_POSTGRES_VERIFICATION_WRITES.md`, including the coupled closure of a
pre-application application row.

Protected untouched-job archive, restore, and permanent deletion with an atomic
privacy-minimized suppression are handled by
`STAGE_3_POSTGRES_JOB_RETENTION_WRITES.md`.

Owner-scoped engagement tracking and interview-preparation workspace
operations are handled by
`STAGE_3_POSTGRES_APPLICATION_OPERATIONS.md`.

## Verification gates

- [ ] Clean local SQLite tests prove manual/discovery creation, canonical
      duplicate behavior, initial scoring, validation, and full rollback without
      touching the user's workspace.
- [ ] Route tests prove every in-scope write uses the configured repository and
      shared mode has no SQLite fallback.
- [ ] Alembic reports one head at `20260813_0006`; this catalog slice itself
      adds no migration or RLS-policy change.
- [ ] Disposable PostgreSQL 18 tests prove missing-context denial, same-URL
      isolation between two owners, owner-scoped conflict behavior, cross-owner
      score-update denial, deterministic concurrent writes, and no partial batch.
- [ ] Responses, exceptions, and logs omit internal owner/auth identifiers,
      fingerprints, object metadata, credentials, database URLs, and unrelated
      tenant data.
- [ ] Local tests, syntax and dependency checks, deployment shell syntax,
      exact-diff security review, required GitHub checks, and loopback
      build/dashboard/listener smoke pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live staging
      state changes.

The complete data-authority contract and eventual cutover sequence remain in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
