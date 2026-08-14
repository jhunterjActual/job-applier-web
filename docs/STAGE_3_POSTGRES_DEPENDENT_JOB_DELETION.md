# Stage 3: PostgreSQL Dependent Job Deletion

This bounded slice converts owner-requested deletion through the existing
single-job route when PostgreSQL is the selected data store and the job has an
application, lifecycle history, engagements, or generated materials. It joins
the already-converted retention and generated-material boundaries; standalone
local mode retains its current SQLite and local-file behavior.

This is a **hard no-activation** change. `HOSTED_REPOSITORY_CUTOVER_COMPLETE`
remains false. Do not select PostgreSQL on live staging, import the permanent
SQLite workspace, activate a release, restart services, change data authority,
invite another user, or mutate live PostgreSQL or private-object storage for
this slice.

## Owner and authorization boundary

Shared deletion derives the owner only from authenticated server context.
Requests cannot supply or override an owner ID. Forced RLS and explicit owner
predicates protect every selected, locked, updated, and deleted row. A missing
or cross-owner job is indistinguishable from a nonexistent job, and public
responses do not expose owner IDs, WorkOS IDs, object IDs or keys, URLs or
fingerprints, database details, credentials, paths, or another tenant's data.

The single-job route is the only converted entry point. Its existing fixed
not-found and fixed temporary-failure behavior remains the public boundary.

## Atomic PostgreSQL deletion contract

One short owner-scoped transaction:

1. acquires the established owner job-catalog serialization lock and locks the
   selected job;
2. locks its application, captures both generated-object references, and locks
   any same-owner surviving applications that reference either object;
3. locks and revalidates that every referenced metadata row belongs to the
   authenticated owner;
4. upserts the privacy-minimized job suppression so discovery does not silently
   recreate an intentionally deleted posting;
5. marks a referenced `stored_objects` row deleted only when no same-owner
   surviving application references it; shared references remain active and
   unchanged; and
6. deletes the job, allowing existing owner-consistent foreign keys to cascade
   its application, application-status history, and engagements in the same
   commit.

Suppression, exclusive-object tombstones, and dependent-row deletion therefore
either all commit or all roll back. Same-owner objects referenced by a
surviving application remain locked, active, and preserved. The transaction
does not call the object backend and does not perform AI, PDF, provider,
filesystem, or network work. Concurrent tailoring, lifecycle, verification,
retention, and deletion operations must serialize on the same owner/job
boundary; stale work cannot recreate or attach material to a deleted job.

Jobs without protected dependents continue through the existing retention
behavior. The shared implementation never falls back to SQLite.

## Post-commit object deletion and retry

Private blobs are never deleted before the PostgreSQL commit. After a
successful commit, the web process makes a bounded best-effort call to the
existing owner-scoped generated-material reconciler. That reconciler waits its
existing grace period, selects only tombstoned metadata no longer referenced by
an application, deletes each private blob, and removes its metadata only after
backend deletion succeeds.

Object-store unavailability cannot roll back an already committed job deletion
and must not produce metadata that points to missing authoritative content. A
failed or interrupted cleanup leaves the durable tombstone for a later retry.
No request infers ownership from an object key or adopts an unexpected bucket
object.

## Local parity and no fallback

Local mode retains the current SQLite transaction, protected-dependent
refusal, suppression semantics for eligible jobs, and existing local files. It
does not require PostgreSQL, WorkOS, R2, or an object keyring. Shared mode does
not open the SQLite database or resolve an authoritative local material path.

## Migration, rollback, and activation

No schema, RLS policy, grant, index, trigger, or broker change is required.
Alembic retains one head at `20260813_0008`; the slice neither migrates user
data nor changes import behavior. Build strings agree at `20260813.16`.

Before activation, rollback is code-only because PostgreSQL/R2 remain
disposable rehearsal targets. After any future authoritative PostgreSQL/R2
write, do not resume writable SQLite; retain PostgreSQL/R2 authority and apply
a reviewed forward correction.

## Explicit exclusions

This slice does not add or authorize:

- account deletion, identity-provider deletion, session revocation, or
  account-wide cross-store erasure;
- bulk deletion of protected cleanup candidates;
- a separate application-deletion route or generated-material history;
- bucket-wide enumeration, adoption, or deletion of orphan objects;
- a new queue, tombstone schema, migration, import, or reconciliation broker;
- object-store or keyring credentials in the search worker;
- hosted in-app backup/restore, permanent import, authority cutover, staging
  activation, public signup, additional users, Caddy changes, or live service,
  database, bucket, environment, or secret mutation.

## Verification gates

- [ ] Focused local tests prove existing SQLite eligible single-job deletion,
      protected-dependent refusal, and local files remain unchanged.
- [ ] Route tests prove owner-context repository selection, fixed errors, and
      no SQLite or local-path fallback in selected shared mode.
- [ ] Repository tests prove suppression, object tombstones, job deletion, and
      foreign-key cascades are atomic and roll back together on failure.
- [ ] Same-owner reference tests prove an object shared with a surviving
      application remains active and is neither tombstoned nor reconciled.
- [ ] Concurrency tests prove tailoring and lifecycle writes cannot race a
      locked deletion or recreate dependent records after it commits.
- [ ] Object tests prove no blob delete occurs before commit, post-commit
      cleanup is bounded and best effort, failures retain tombstones, and a
      later owner-scoped reconciliation retry succeeds.
- [ ] Disposable PostgreSQL 18 tests prove missing-context and two-owner denial,
      explicit owner predicates, forced RLS, same-owner object references, and
      complete application/history/engagement cascades.
- [ ] P0, focused generated-material and retention tests, PostgreSQL/RLS tests,
      syntax, dependency-lock, launcher, protected-file, and exact-diff review
      pass without network calls or real user data.
- [ ] Alembic reports exactly one head at `20260813_0008`, build strings agree
      at `20260813.16`, `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false, and
      no live import, migration, deployment, or external mutation occurred.
