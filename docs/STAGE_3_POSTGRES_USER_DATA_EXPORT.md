# Stage 3: Owner-Scoped User-Data Export

This slice converts only `GET /api/data-export` to a dual-mode repository.
Standalone local use retains SQLite. Shared-host export uses PostgreSQL and
forced row-level security, but the overall repository transition remains
incomplete and this release must not be activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not configure the live
service for PostgreSQL, import the live workspace, switch data authority,
activate a release, restart services, invite users, or change PostgreSQL, R2,
Caddy, environment files, credentials, or access restrictions for this slice.
SQLite and generated files remain authoritative; PostgreSQL tests use only an
explicitly approved disposable target. This change adds no migration.

## Snapshot and ownership contract

The repository derives hosted ownership only from authenticated server context;
the request cannot supply an owner identifier. Its PostgreSQL implementation
runs under transaction-local tenant context and binds the owner explicitly on
every table and joined resume predicate. A single `jsonb_build_object` statement
collects the complete export, so all ten domains share one PostgreSQL MVCC
statement snapshot. Local mode reads the same public fields in one explicit
SQLite transaction. Repository transactions and connections close before the
pure serializer constructs the response or the response iterator yields bytes.

Shared mode never opens or falls back to the local SQLite workspace. Repository,
configuration, and serializer failures at this route return the same fixed 503
detail without database URLs, credentials, paths, SQL text, owner identifiers,
or another tenant's values.

## Public export contract

The versioned JSON export contains the authenticated user's profile; base
resumes and versions; jobs; saved searches; applications, active or undone
status history, and engagements; privacy-minimized suppressions; and stored
source diagnostic codes and bounded user-facing counters. Rows use stable ID or
resume/version ordering, dates are ISO serialized, SQLite JSON text and
PostgreSQL JSON values produce the same public fields, and serialization does
not mutate the repository snapshot.

Explicit projections and a second serializer allowlist exclude:

- provider secrets, encrypted values, key IDs, keyrings, and credentials;
- stored-object rows, object IDs, object keys, encryption metadata, and hashes;
- generated-file storage locations and local filesystem paths;
- owner and identity IDs, internal URL fingerprints, headquarters-cache keys
  and values, and internal-only diagnostic material.

Resume, cover-letter, interview-preparation, application-note, engagement, and
profile content are intentionally present because this endpoint is the user's
authenticated portable copy. The response remains an attachment with
`Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

## Data authority and rollback

No records are copied or mutated by export, and no schema, index, policy, grant,
or Alembic head changes. Before final cutover, SQLite remains authoritative and
PostgreSQL/R2 rehearsal targets remain disposable. After an eventual first
authoritative PostgreSQL/R2 write, a code-only rollback to writable SQLite is
forbidden because it would split data authority; retain PostgreSQL/R2 authority
and use a reviewed forward correction.

## Verification gates

- [ ] Synthetic SQLite tests prove complete public coverage, stable ordering,
      deterministic serialization, fixed errors, and exclusion of secrets,
      paths, fingerprints, and cache data.
- [ ] Dispatch tests prove local parity, authenticated shared selection, and no
      shared-mode SQLite fallback.
- [ ] Disposable PostgreSQL 18 tests prove two-owner isolation, explicit public
      fields, stable rows, and omission of owner IDs, fingerprints, cache values,
      object metadata, and unrelated tenant content.
- [ ] Exact P0 tests, compilation, dependency checks, diff hygiene, protected
      path scan, and required GitHub checks pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live database,
      object, service, release, environment, user, or access setting changes.

The final authority, rollback, recovery, and cutover contract remains in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
