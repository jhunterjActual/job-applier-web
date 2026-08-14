# Stage 3: PostgreSQL/R2 final-cutover coordinator

This slice adds a dormant, root-only coordinator for a later reviewed
PostgreSQL/private-R2 authority transition. It does **not** authorize or perform
that transition. Build `20260813.23` keeps
`HOSTED_REPOSITORY_CUTOVER_COMPLETE` false, ships no
`deploy/hosted-authority.compatibility` file, changes no live environment or
credential, and does not migrate, import, reconcile, mark authority, move a
release symlink, restart a service, or access PostgreSQL or R2.

The coordinator is an ordering and failure-boundary control. It cannot replace
the operator's review of backup, import, reconciliation, authorization, and
recovery evidence. Do not run it against staging until a later exact release
separately flips the repository-cutover guard, carries the exact reviewed
hosted-authority capability, and every prerequisite below has been approved.

## Manual evidence that automation cannot create

Before the coordinator may acquire the maintenance lock, an operator must
retain and approve all of these sanitized receipts for the exact immutable
release and frozen source:

- the required GitHub checks and `careertrellis-deploy --prepare-only` receipt
  for one exact 40-character merge commit;
- a recoverable, read-only SQLite database and generated-files snapshot with
  bounded inventory counts and hashes;
- an immediately pre-cutover pgBackRest full-backup label, the successful
  independent encrypted-mirror receipt, and an isolated restore result with
  zero bad checksums;
- the permanent private-bucket recovery/versioning and inventory receipt,
  without object keys, document hashes, credentials, or tenant data;
- the earlier disposable no-omission import and independent three-surface
  reconciliation receipts from the same reviewed implementation; and
- confirmation that the provider-secret and object keyrings are independently
  retrievable and every reviewed runtime environment file is a regular,
  non-symlinked `root:root` mode-`0600` file.

The receipts stay outside command arguments. The exact confirmation token
`I_HAVE_VERIFIED_QUIESCENT_SNAPSHOTS_AND_BACKUPS` records the operator's
attestation that they were reviewed; it is not a substitute for the evidence
itself. The coordinator can validate only the exact token, not the operator's
underlying evidence; the operator must not invoke it when a receipt is missing,
stale, malformed, or bound to another release. A receipt must never contain a
database URL, password, R2 key, keyring, WorkOS identifier, object key, local
private path, document hash, profile content, or other tenant value. A missing
or inexact confirmation fails before any migration, import, authority, release,
or service mutation.

## One inherited maintenance lock

`deploy/scripts/finalize-postgres-r2-cutover.sh` uses the shared
`deploy/scripts/maintenance-lock.sh` helper to own the existing exclusive
`/run/lock/careertrellis-maintenance.lock` for the whole operation. Migration,
runtime-grant, preflight, import, reconciliation, authority, and activation
steps run inside that inherited lock rather than dropping it between commands.
A child helper must not trust an arbitrary environment flag as proof of
ownership, and standalone deploy, rollback, migration, import, reconciliation,
or `apply-postgres-runtime-grants.sh` commands must continue to take the same
lock normally.

Keeping one verified lock prevents a deployment, rollback, second import, or
manual reconciliation from entering between the frozen-source checks and the
one-way marker. The coordinator also proves the web, search-worker, and
account-erasure-worker services are inactive before any source or target is
opened and rechecks that quiescent state immediately before the marker changes.

## Exact transition order

After the manual receipts pass review, the later compatible release uses this
exact root-only interface:

```bash
sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/finalize-postgres-r2-cutover.sh \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-migration.env \
  /etc/careertrellis/object-storage.env \
  I_HAVE_VERIFIED_QUIESCENT_SNAPSHOTS_AND_BACKUPS \
  I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED
```

The release, migration-environment, and object-environment paths must satisfy
their existing immutable/root-owned validation. The first confirmation binds
the manual snapshot/backup receipts to the attempt; the second is passed only
to the one-way hosted-authority transition. The coordinator then keeps this
order while holding the inherited lock:

1. Resolve the exact immutable release, validate the still-pre-cutover
   authority state and root-owned inputs, idempotently stop the web,
   search-worker, and account-erasure-worker services, and prove all three are
   inactive. This re-establishes the quiescence asserted by the manual receipt.
2. The exact confirmation attests that the approved SQLite/generated-files
   snapshot, immediately pre-cutover PostgreSQL backup, independent mirror,
   isolated restore, and permanent-object recovery receipts belong to this
   exact attempt. The coordinator does not inspect or regenerate them.
3. Apply the exact reviewed Alembic head with `migrate-postgres.sh`, then invoke
   `apply-postgres-runtime-grants.sh` to reapply the reviewed runtime grants.
   Never run an Alembic downgrade.
4. Run the exact release's read-only SQLite preflight and compare its sanitized
   counts with the approved no-omission rehearsal baseline.
5. Import the frozen workspace into the permanent PostgreSQL database and
   private R2 bucket with no omission switches. The import wrapper must finish
   its independent read-only reconciliation under the same lock before it can
   report success.
6. Require a successful sanitized reconciliation across every supported source
   and owner-scoped target table, encrypted provider-secret plaintext,
   `stored_objects` metadata, application object references, accepted source
   files, and the full permanent-bucket inventory. Any missing, extra,
   duplicate, corrupt, mismatched, or orphan surface stops the transition.
7. Validate that the selected exact release carries only the reviewed
   `postgres-r2-authoritative-v1` capability and that the root-owned web and
   object-store environments select PostgreSQL and encrypted private R2
   exactly.
8. While all three services are still inactive, pass the exact release and
   second confirmation to the authority helper for the one-way
   `postgres-r2-authoritative` marker transition. The marker must be durable
   before the first PostgreSQL/R2 application write can occur.
9. Invoke `deploy-release.sh` for only that same immutable release. Record
   whether the separately controlled erasure worker was active before the
   coordinator stopped it, and start that worker only after successful
   activation when it was previously active; never activate it implicitly.
   Then verify the build, local liveness/readiness, service state, HTTPS path,
   owner flows, negative authorization checks, and minimized logs before
   reopening traffic.
10. Take the post-import PostgreSQL full backup and independent mirror, retain
    the permanent-object inventory receipt, and preserve the frozen SQLite and
    generated-files snapshot for the reviewed recovery window.

No operator may skip forward, substitute a different release, reuse an
omission rehearsal, or mark authority from an unreconciled import. An
ambiguous import outcome requires the read-only reconciliation command with the
same release and inputs before any retry decision.

## Failure and recovery boundary

Before the authority marker changes, any failed prerequisite, migration,
grant, preflight, import, or reconciliation step aborts the attempt and leaves
all three application services stopped. The coordinator does not automatically
restart the prior SQLite release: doing so would invalidate the quiescent
snapshot and blur whether a later retry still refers to the approved evidence.
The operator may investigate, reconcile an ambiguous outcome, or begin a new
reviewed attempt while `sqlite-pre-cutover` remains authoritative.

After the marker changes, SQLite and local generated files must never become
writable authority again. Code-only rollback, SQLite import, and an automatic
restart of an older release are forbidden. A failed activation, readiness
check, smoke check, or later operation leaves services stopped and requires a
reviewed forward code or schema correction that preserves PostgreSQL/private-R2
authority. The authority marker is not downgraded or recreated.

## Acceptance for this dormant slice

- [ ] The coordinator and every nested helper share one verified inherited
      maintenance lock without creating a lock-bypass environment variable.
- [ ] Tests prove the exact ordering, confirmation contract, three-service
      quiescence, no-omission import, automatic reconciliation, and marker-before-
      activation boundary.
- [ ] Injected failures before the marker leave all services stopped and do not
      restart the SQLite release; injected failures after the marker require a
      forward correction and never invoke rollback or import.
- [ ] Receipt output is fixed and sanitized; secrets, private paths, object
      keys, document hashes, owner identifiers, and tenant data are absent.
- [ ] The P0, coordinator, import/reconciliation, PostgreSQL/RLS, route-
      qualification, dependency-lock, compilation, and shell-syntax checks
      pass for build `20260813.23`.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false, the hosted-authority
      compatibility file remains absent, and no live release, service,
      authority marker, database, object, credential, environment, user, DNS,
      Caddy, or firewall state changes in this slice.

The durable marker and restart boundary remain authoritative in
`STAGE_3_HOSTED_AUTHORITY_BOUNDARY.md`; import ambiguity and no-omission
reconciliation remain authoritative in
`STAGE_3_SQLITE_POSTGRES_IMPORT_RECONCILIATION.md`; the complete repository and
operator gate remains in `STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
