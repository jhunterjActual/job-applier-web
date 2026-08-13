# Stage 3: PostgreSQL Lifecycle Write Slice

This slice adds one dual-mode repository boundary for user-confirmed job and
application lifecycle transitions and latest-change undo. Standalone local use
retains SQLite. This is not the final hosted repository cutover and must not be
activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not set live
`CAREERTRELLIS_DATA_STORE=postgres`, migrate or import the permanent staging
database, change data authority, activate a release, restart services, or
change R2, Caddy, users, runtime environment files, or secret files for this
slice.

SQLite and generated files remain authoritative. PostgreSQL and R2 rehearsal
targets remain disposable until every repository domain, final import,
reconciliation, recovery, and authorization gate has passed.

## Converted transaction boundary

The lifecycle repository owns these operations as one transaction:

- lock the authenticated owner's job and current application;
- create or update the application lifecycle fields;
- update the matching job status;
- append an owner-scoped status-history record; and
- undo the latest active history record, preserving an application that still
  references generated material and removing only a material-free application
  returned to `matched`.

Hosted ownership comes only from authenticated server context. Every
PostgreSQL write uses transaction-local tenant context, explicit owner
predicates, and forced RLS. Foreign and missing job IDs use the same not-found
result. Mutations lock rows in job-then-application order so concurrent changes
serialize without splitting job, application, and history state. Returned data
omits owner IDs, WorkOS IDs, object identifiers, paths, credentials, database
URLs, and unrelated tenant data.

## Migration and rollback

Migration `20260813_0005` adds only the partial owner/job/history index used to
find the latest non-undone lifecycle record. It builds and drops concurrently
with bounded lock and statement timeouts. It does not copy data, alter RLS, or
change repository authority.

The later retention slice advances the single head to `20260813_0006` with one
concurrent owner/status/id retention-candidate index. Apply these migrations
only to disposable PostgreSQL 18 targets while the activation gate is false.
SQLite remains authoritative. After an eventual first authoritative
PostgreSQL/R2 write, a code-only rollback to writable SQLite is forbidden; this
slice does not reach that boundary.

## Explicit exclusions

This slice does not convert or authorize:

- deletion of jobs with applications, lifecycle history, generated materials,
  stored objects, or other protected dependent records;
- tailoring, generated-material creation/replacement/deletion, private-object
  mutations, application downloads, engagements, interview preparation, or
  follow-up management beyond retaining the lifecycle form fields;
- durable saved-search claims, provider execution, process-global search state,
  retries, leases, or worker execution;
- headquarters cache, export/account deletion, hosted backup/restore, public
  signup, additional users, deployment activation, permanent import, or the
  data-authority transition.

## Verification gates

- [ ] Clean local tests prove SQLite compatibility, repository route dispatch,
      rollback, lifecycle history, undo, and material preservation.
- [ ] Alembic reports exactly one head at `20260813_0006`; repeated upgrade is a
      no-op and both concurrent indexes are valid and ready.
- [ ] Disposable PostgreSQL 18 tests prove missing-context and cross-owner
      denial, atomic job/application/history writes, reversible undo, and
      deterministic concurrent same-job transitions.
- [ ] Responses, exceptions, and logs omit internal owner/auth identifiers,
      object metadata, credentials, database URLs, and unrelated tenant data.
- [ ] Local tests, compilation, dependency and shell-syntax checks, exact-diff
      security review, required GitHub checks, and loopback build/dashboard/
      listener smoke pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live staging
      state changes.

The full data-authority and final cutover contract remains in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.

Protected untouched-job retention is documented separately in
`STAGE_3_POSTGRES_JOB_RETENTION_WRITES.md`.
