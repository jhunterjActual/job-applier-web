# Stage 3: PostgreSQL Generated-Material Persistence

This bounded slice converts tailoring material creation, replacement, detail
reads, and authenticated downloads when PostgreSQL is the selected data store.
Standalone local mode keeps its existing SQLite rows and local generated files.

This is a **no-activation** change. `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains
false. Do not set `CAREERTRELLIS_DATA_STORE=postgres`, move the authoritative
SQLite workspace, restart staging for this slice, enable public access, or
invite another user. PostgreSQL and private R2 remain disposable rehearsal
targets until every remaining repository and cutover gate is reviewed.

## Existing schema and ownership boundary

No migration is required. Alembic retains one head at `20260813_0007`.
The existing `applications.tailored_resume_object_id` and
`applications.cover_letter_object_id` composite foreign keys bind each object
reference to the same `owner_user_id`. `applications`, `jobs`, `profiles`,
`base_resumes`, and `stored_objects` remain under forced RLS. Every shared query
also repeats the authenticated owner predicate; no route, object ID, header,
query, or request body may select an owner.

The shared repository reads one consistent job/profile/base-resume/application
context in a short owner-scoped transaction and derives an opaque revision
token. That transaction closes before provider-secret decryption, AI, maps,
PDF generation, ephemeral scratch-file work, encrypted-object upload, or any
other network call.

## Upload and atomic reference swap

The shared web process generates the PDF and UTF-8 cover letter outside a
database transaction. Each artifact is envelope-encrypted under its owner,
uploaded to the private object backend, read back, authenticated, decrypted,
and checked for exact size and SHA-256 digest before a database write begins.

One short owner-scoped transaction then:

1. locks and rereads the job, profile, active base resume, and application;
2. rejects a stale revision token before changing authority;
3. inserts both `stored_objects` metadata records;
4. inserts or updates the application so both object references change
   together;
5. preserves all advanced job/application lifecycle states; and
6. tombstones the replaced metadata only after the new references exist.

Old content is never deleted first. An upload/readback failure leaves database
metadata unchanged. A known database rollback compensates by deleting the two
new staged blobs. If commit outcome cannot be proven after a connection error,
the service rereads the owner-scoped application: it treats matching new IDs as
committed, deletes staged content only when it proves those IDs are
unreferenced, and otherwise retains a possible orphan for reconciliation. This
prefers an orphan over authoritative metadata that points to missing content.

Tombstoned replaced metadata is durable retry state. Bounded reconciliation
selects only owner-scoped tombstones that are no longer referenced, waits a
short grace period for already-authorized downloads, deletes the private blob,
and removes metadata only after deletion succeeds. A backend failure leaves
the tombstone for a later retry.

## Retrieval and response minimization

Tailored detail, DOCX generation, manual-application readiness, and application
record availability derive from owner-scoped PostgreSQL application references.
The API never returns an R2 key, direct URL, wrapped data key, keyring value,
credential, local filesystem path, or owner ID.

PDF and cover-letter downloads first authorize the application reference, then
load active object metadata in a separate short owner transaction. All database
transactions close before the private backend fetch and decryption. Responses
are attachment-only with `private, no-store` caching. Missing/cross-owner
objects return the same fixed not-found response; backend or integrity failures
return a fixed temporary-unavailability response without raw exception detail.

## Local parity and no fallback

Local mode retains the existing SQLite/filesystem generation, editing,
download, backup, and restore behavior. Shared mode never calls the SQLite
connection factory or resolves an authoritative local material path. Temporary
PDF files use the host's ephemeral temporary directory, are operation-tracked
scratch files only, and are removed after the shared commit or failure.

## Explicit exclusions

This slice does not add generated-material history, browser/R2 access, public
object URLs, worker object credentials, scheduled dispatch, provider rate-limit
coordination, account deletion, application/job deletion with dependent
materials, bucket-wide orphan enumeration, permanent import, backup/restore
activation, authority cutover, Caddy changes, public signup, or additional
users. Existing retention policy continues to reject deletion of jobs that
have application/material records; account erasure and cross-store deletion
orchestration require a later independently reviewed slice.

## Verification gates

- [ ] Focused tests prove both encrypted objects are verified before commit,
      upload failure leaves metadata unchanged, known rollback compensates,
      ambiguous commit never deletes possibly authoritative content, stale
      generation cannot overwrite newer state, and shared mode has no SQLite
      fallback.
- [ ] Disposable PostgreSQL 18 tests prove forced-RLS owner isolation,
      composite owner foreign keys, atomic reference replacement, old metadata
      tombstones, retry-safe reconciliation, cross-owner denial, and advanced
      lifecycle preservation.
- [ ] Download tests prove metadata authorization and transaction closure occur
      before object fetch/decryption, and responses contain only fixed errors
      and private attachments.
- [ ] P0, object-store/import/stored-object, focused material, syntax,
      dependency-lock, migration-head, launcher, protected-file, and diff checks
      pass.
- [ ] Build strings agree at `20260813.14`, Alembic remains at the single
      `20260813_0007` head, and `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains
      false.

Rollback before activation is code-only because this slice has no migration and
does not change authority. After any future authoritative PostgreSQL/R2 write,
do not resume writable SQLite; keep PostgreSQL/R2 authoritative and apply a
reviewed forward correction.
