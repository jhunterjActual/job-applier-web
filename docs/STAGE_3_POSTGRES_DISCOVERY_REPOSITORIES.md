# Stage 3: PostgreSQL Discovery Repository Slice

This slice extends the dual-mode repository boundary to owner-visible job
discovery data. It preserves the existing SQLite behavior for standalone local
use and adds PostgreSQL implementations for a deliberately limited set of
shared-host reads and low-coupling deletes. It does **not** complete the hosted
repository transition and must not be activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to the live staging environment, import the
permanent SQLite source, change the repository authority marker, or activate
this release on the web Droplet. A shared-host process selecting PostgreSQL
must continue to refuse startup until all remaining data domains and the final
cutover controls are complete.

No live database, R2 object, service, release symlink, environment file, user,
or Caddy access restriction is changed by this slice.

## Converted operations

The runtime discovery repository now covers:

- job-list reads, including application dates and whether generated material
  exists, without returning object keys or local filesystem paths;
- canonical-URL duplicate and deletion-suppression checks used before a manual
  job preview;
- privacy-minimized job-suppression listing and owner-scoped clearing;
- enabled saved-search listing, due-search listing, and owner-scoped deletion;
- allowlisted, bounded source-diagnostic persistence, listing, export input,
  maintainer-report input, and clearing; and
- owner-first PostgreSQL indexes supporting the converted list and due-work
  access patterns.

Hosted repository ownership is derived only from the authenticated server
context. Callers cannot supply an owner ID. Every PostgreSQL operation runs in
a transaction that sets trusted transaction-local tenant context, retains an
explicit owner predicate, and remains protected by forced row-level security.
Response mappings omit `owner_user_id`, URL fingerprints, object identifiers,
and diagnostic data outside the existing privacy allowlist.

SQLite remains the implementation in local mode. It continues to use the
standalone database and existing local output semantics without requiring
PostgreSQL, WorkOS, R2, or server keyrings.

Owner-scoped saved-search creation/upsert and post-run reminder advancement are
added by the next bounded slice documented in
`STAGE_3_POSTGRES_SAVED_SEARCH_WRITES.md`.

Owner-scoped manual/discovery job creation and initial score completion are
added by the bounded job-catalog slice documented in
`STAGE_3_POSTGRES_JOB_CATALOG_WRITES.md`.

Atomic owner-scoped job/application lifecycle transitions and undo are added by
the bounded lifecycle slice documented in
`STAGE_3_POSTGRES_LIFECYCLE_WRITES.md`.

Owner-scoped posting refresh and expiry writes are added by the bounded
verification slice documented in `STAGE_3_POSTGRES_VERIFICATION_WRITES.md`.

Protected owner-scoped untouched-job preview, archive, restore, and permanent
deletion are added by the bounded retention slice documented in
`STAGE_3_POSTGRES_JOB_RETENTION_WRITES.md`.

## Operations intentionally not converted

This slice does not move any of the following hosted paths:

- deletion of jobs with applications, history, generated materials, stored
  objects, or other protected dependent records;
- the process-global search-status/result state or durable background work;
- application operations outside converted lifecycle transitions, engagements,
  interview preparation, and follow-up management;
- generated-material creation, replacement, download integration, or deletion;
- headquarters cache, user export, account deletion, or hosted backup/restore;
  or
- the remaining domain reads and writes that still open SQLite directly.

Consequently, this PR is a reviewed foundation for later slices, not a
deployable mixed-store mode. The next repository work must convert a coherent
write aggregate and its transaction semantics before any route flip.

## Migration and rollback contract

Migration `20260812_0003` adds only owner-first indexes; the following bounded
saved-search slice advances the head to `20260812_0004`; the lifecycle slice
advances it to `20260813_0005` with one active-history lookup index; and the
retention slice advances it to `20260813_0006` with one concurrent
owner/status/id retention-candidate index. None of these migrations copies data,
changes RLS, or changes repository authority. Apply them only to disposable
test databases
while this no-activation gate is in force. Staging schema changes are reserved
for the final exact-release migration window after a fresh verified backup and
isolated restore receipt.

Until final cutover, SQLite and generated files remain authoritative and
PostgreSQL/R2 rehearsal targets remain disposable copies. After the eventual
first PostgreSQL/R2 post-cutover write, a code-only rollback to writable SQLite
is forbidden because it would split data authority. The final cutover wrapper
must enforce that distinction; this slice does not weaken it.

## Verification gates for this slice

- [ ] The standalone P0 suite passes from a clean local SQLite workspace.
- [ ] Syntax compilation and shell-script syntax checks pass.
- [ ] Alembic reports exactly one head at `20260813_0006`.
- [ ] A disposable PostgreSQL 18 database upgrades through every migration and
      passes the full configured RLS suite.
- [ ] Two-tenant tests prove converted job, suppression, saved-search, and
      diagnostic operations cannot read, update, or delete the other tenant's
      rows.
- [ ] Returned rows omit internal owner identifiers, suppression fingerprints,
      raw diagnostic errors, searches, URLs, and credentials.
- [ ] Repository-route dispatch tests prove converted routes use the configured
      repository while local behavior remains compatible.
- [ ] The exact diff receives a clean security review and dependency scan.
- [ ] Loopback runtime smoke tests report the expected build and serve the
      dashboard without exposing a non-loopback local listener.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live staging
      state changes.

The final cutover sequence, data-authority rules, and remaining operator gates
remain in `STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
