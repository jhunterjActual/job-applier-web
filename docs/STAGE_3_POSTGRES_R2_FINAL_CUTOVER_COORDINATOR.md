# Stage 3: PostgreSQL/R2 final-cutover coordinator

This slice hardens the dormant root-only cutover coordinator without enabling
hosted repositories or changing live state. Build `20260813.29` keeps
`HOSTED_REPOSITORY_CUTOVER_COMPLETE` false, ships no
`deploy/hosted-authority.compatibility`, and performs no migration, import,
authority transition, release activation, credential change, or service action
merely by being installed or prepared.

The permanent object bucket is empty before import, so its recovery generation
cannot be verified before import. The coordinator is therefore split into two
explicit phases with a durable, root-only pending-attempt record between them.
The pause makes post-import PostgreSQL and generated-object backup/restore
evidence a prerequisite to the irreversible authority marker rather than a
post-activation promise.

Do not run either phase until a later exact release separately flips the
repository-cutover guard, carries the exact hosted-authority capability, and
all prerequisites for that exact release have been approved.

## Pre-authority and worker gates

While authority is `sqlite-pre-cutover`, the installed authority helper permits
only an immutable release with exactly one
`HOSTED_REPOSITORY_CUTOVER_COMPLETE = False` assignment and no compatibility
manifest. A final-capable release cannot be started early through ordinary
deploy or rollback. Any pending cutover record also blocks pre-cutover release
starts and runtime checks, so a partially imported workspace cannot silently
resume SQLite writes.

The web environment carries the same exact boolean switches as the dedicated
workers:

- `CAREERTRELLIS_SEARCH_WORKER_ENABLED`
- `CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED`

Deploy, rollback, and the coordinator reject missing, malformed, duplicate, or
mismatched producer/consumer values. Final cutover requires both pairs to be
exactly false. The web therefore rejects or hides both producer operations and
deploy keeps both workers stopped and disabled. Enabling either consumer after
cutover remains a separate reviewed operator action; the coordinator never
implicitly starts the erasure worker.

Before phase one, the coordinator also requires the installed authority helper
and all three systemd start guards to byte-match the exact release. It takes the
maintenance lock, reloads systemd, and proves each unit's loaded `DropInPaths`
contains that exact guard. The no-start provisioner takes the same lock, so it
cannot replace these files concurrently. Preparing a release alone does not
install them; the reviewed no-start provisioning step must have completed.
These checks make the pending pause effective across reboots and manual starts.

## Durable pending attempt

The coordinator records the attempt in:

```text
/etc/careertrellis/authority/pending-cutover
```

The regular, non-symlink file is `root:root` mode `0600` and contains only a
format version, random 128-bit attempt ID, phase, exact 40-character release,
SHA-256 identities for the root-owned migration, object, and provider-keyring
environment files,
and a private digest of the serialized SQLite workspace, owner binding, and
bounded generated-file inventory. It contains no URL, credential, keyring,
tenant identifier, object key, private path, or document hash. Replacement is
atomic and synced.

The accepted phases are `preparing`, `importing`, `imported`,
`recovery-verified`, `authority-marked`, `activating`, `activation-failed`, and
`activated`. A new import is refused while any pending record exists. The
`preparing` phase alone may carry the literal source identity `pending` until
the stopped source is hashed; every later phase requires the exact SHA-256.
The intended phase-one import alone receives the random attempt ID while
holding the inherited maintenance lock; standalone or retry imports remain
denied. A `preparing` record means target preparation did not reach the
importer and can advance only through the reviewed `resume-preparation` action.
An `importing` record means the importer outcome may be ambiguous and requires
operator review and the exact read-only `reconcile-pending` action; do not
retry or delete either record casually.

## Phase one: import and pause

Before phase one, retain approved sanitized evidence for the exact release and
frozen source:

- required GitHub, P0, PostgreSQL/RLS, route-qualification, dependency, syntax,
  and prepare-only checks;
- a recoverable SQLite database and generated-files snapshot;
- an immediately pre-import full pgBackRest backup, encrypted independent
  mirror, and isolated restore with zero bad checksums;
- independently recoverable provider and object keyrings;
- the completed disposable no-omission rehearsal and three-surface
  reconciliation; and
- verified permanent PostgreSQL/R2 identities, empty destinations, role/RLS
  boundaries, and regular root-owned environment files.

The exact later compatible release is invoked as:

```bash
sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/finalize-postgres-r2-cutover.sh \
  import-and-pause \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-migration.env \
  /etc/careertrellis/object-storage.env \
  I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP
```

Under one inherited maintenance lock, phase one:

1. validates the final-capable exact release, environment identities, authority
   state, and matching web/worker switches;
2. durably records `preparing` before service stop, source hashing, or target
   mutation, then stops and proves the web, search, and erasure services
   inactive;
3. records the exact frozen-source digest while remaining `preparing`;
4. applies the exact Alembic head and reviewed runtime grants;
5. runs the read-only SQLite preflight;
6. records `importing` immediately before importing the frozen source to
   permanent PostgreSQL and private R2 with no
   omission switches, including automatic independent reconciliation; and
7. durably records `imported`, releases the lock, and deliberately leaves all
   three services stopped with authority still `sqlite-pre-cutover`.

No release is activated and no authority marker changes in phase one.

If phase one exits while `preparing`, the importer was not entered. Diagnose
the migration, grant, preflight, source, or service-state failure first. After
confirming it is safe to resume the same attempt and exact targets, use:

```bash
sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/finalize-postgres-r2-cutover.sh \
  resume-preparation \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-migration.env \
  /etc/careertrellis/object-storage.env \
  I_HAVE_REVIEWED_PRE_IMPORT_PREPARATION_FAILURE
```

This explicitly revalidates the bound attempt and frozen source, reruns only
the idempotent preparation operations, and writes `importing` immediately
before its single import invocation. It is never an automatic retry.

If phase one exits after writing `importing`, do not rerun or resume the
importer. After reviewing the failure and confirming read-only reconciliation
is the correct next step, use the same exact paths:

```bash
sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/finalize-postgres-r2-cutover.sh \
  reconcile-pending \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-migration.env \
  /etc/careertrellis/object-storage.env \
  I_HAVE_REVIEWED_AMBIGUOUS_IMPORT
```

This action never imports. It promotes `importing` to `imported` only when the
same frozen source and exact target pass preflight and complete independent
reconciliation.

## Operator pause: prove imported-state recovery

While the pending record blocks application restart and all three services
remain inactive:

1. review phase one's sanitized import/reconciliation receipt;
2. take a fresh full pgBackRest backup of the imported permanent database,
   mirror it independently, and pass an isolated restore;
3. create an immutable generated-object ciphertext generation from the now-
   populated permanent bucket, pair it with that backup label, and verify its
   manifest and disposable restore;
4. prove zero PostgreSQL restore checksums are bad, reconcile the isolated
   restored database to the disposable restored object bucket, recover the
   provider and object keyrings independently, and decrypt every restored
   generated object;
5. confirm the permanent PostgreSQL row counts, `stored_objects` references,
   full R2 inventory, restored database, and restored generation agree; and
6. retain all receipts in the encrypted operator evidence store without
   credentials, URLs, tenant values, object keys, or document hashes.

There is intentionally no maintenance lock across this human-reviewed pause.
The durable pending record and service start guards preserve quiescence across
process exits and host sessions.

Record the reviewed non-secret pairing through the exact coordinator before
phase two. The backup label and manifest digest are not credentials or tenant
data, but they must match the retained receipts:

```bash
sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/finalize-postgres-r2-cutover.sh \
  record-recovery-evidence \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-migration.env \
  /etc/careertrellis/object-storage.env \
  EXACT_FULL_BACKUP_LABEL \
  EXACT_GENERATED_OBJECT_MANIFEST_SHA256 \
  I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES
```

This action reruns preflight and reconciliation, writes a root-only evidence
record bound to the attempt/release with exact true attestations for the full
PostgreSQL mirror/restore/checksum, generated-object mirror/restore, paired
restored-surface reconciliation, independent provider/object keyring recovery,
every-object decryption, and final reconciliation gates, and advances the pending phase to
`recovery-verified`. The authority helper itself refuses a direct marker change
without that exact pending phase, release, inherited lock, and evidence record.

## Phase two: reconcile, mark, and activate

Only after the post-import backups and restores pass, invoke:

```bash
sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/finalize-postgres-r2-cutover.sh \
  mark-and-activate \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-migration.env \
  /etc/careertrellis/object-storage.env \
  I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES \
  I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED
```

Under a new inherited maintenance lock, phase two:

1. validates the exact `recovery-verified` attempt, release, source and
   environment identities, plus the root-only paired recovery record;
   authority state, and worker switches;
2. stops/rechecks all three application services;
3. reruns the exact preflight and independent read-only reconciliation without
   retrying the import;
4. durably marks PostgreSQL/R2 authoritative;
5. records `authority-marked` immediately after the durable marker, proves all
   three application units inactive and disabled, and creates a root-only
   transient activation lease bound to the exact attempt, release, live
   coordinator PID, kernel boot identity, process start time, and inherited
   maintenance-lock descriptor;
6. records `activating`, activates only the same immutable release while the
   systemd guards can validate that live lease, verifies web/Caddy active and
   enabled with both workers inactive and disabled, records `activated`, and
   removes the lease.

The cutover path never starts either asynchronous worker. Search and account
erasure remain unavailable until their separately reviewed post-cutover
activation procedures update each matched web/worker switch and service state.

## Failure boundary

Validation failures before the durable attempt begins do not change services
or authority. Once `preparing` is written, any phase-one failure leaves all
three services stopped and the pending record at the last durable state. Use
`resume-preparation` only for a reviewed `preparing` failure. Do not retry an
ambiguous `importing` attempt or remove its record; use the exact read-only
`reconcile-pending` action only after review. Any failure during the pause or
before phase-two authority marking also leaves SQLite authority unchanged and
services stopped.

After the authority marker changes, SQLite import and code-only rollback are
permanently forbidden. A failed activation removes the transient lease, stops
and disables all application services, and records `activation-failed`; a
crash leaves no usable lease because the recorded PID, boot identity, process
start time, and descriptor-10 maintenance-lock ownership must all still match.
A recycled PID cannot revive the lease. Hosted runtime guards permit an
`activating` start only while that exact live lease is valid, permit normal
runtime only at `activated`, and reject every other pending phase. After
diagnosing and correcting the failure, resume only the same exact release and
pending attempt with:

```bash
sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/finalize-postgres-r2-cutover.sh \
  resume-hosted-activation \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-migration.env \
  /etc/careertrellis/object-storage.env \
  I_HAVE_REVIEWED_HOSTED_FORWARD_RECOVERY
```

The resume action requires hosted authority plus the exact `authority-marked`
or `activation-failed` pending attempt. It also safely normalizes the same
attempt when a crash left `activating`, or when the marker committed just
before `recovery-verified` could be advanced. It revalidates the paired
evidence and source/target identities, never imports or marks again, creates a
new transient lease, and performs only the forward activation and final
service-state verification. If authority status is unavailable, all import,
retry, rollback, and activation actions are forbidden until an operator
establishes the durable state. No trap restarts services or downgrades
authority.

## Acceptance for this dormant hardening slice

- [ ] Pre-cutover deploy/runtime rejects a final-capable release and any pending
      cutover attempt.
- [ ] Web producer availability matches each durable consumer switch, and
      disabled workers remain stopped/disabled during deploy and rollback.
- [ ] Phase one records `preparing` before service stop, source hashing, or
      target mutation, supports only an explicit reviewed preparation resume,
      and records `importing` immediately before the importer.
- [ ] Phase two requires the exact recovery-verified attempt and post-import
      recovery evidence record, reruns reconciliation, then marks authority
      before an exact live activation lease permits systemd startup.
- [ ] Failure tests prove no import retry, automatic service restart, rollback,
      authority downgrade, or backward recovery path after the marker.
- [ ] P0, coordinator, account-erasure, import/reconciliation, PostgreSQL/RLS,
      route-qualification, dependency, compilation, and shell checks pass for
      build `20260813.29`.
- [ ] The cutover flag remains false, the compatibility file remains absent,
      and no live release, service, authority, database, object, credential,
      environment, user, DNS, Caddy, or firewall state changes in this slice.

The authority marker remains authoritative in
`STAGE_3_HOSTED_AUTHORITY_BOUNDARY.md`; import ambiguity remains authoritative
in `STAGE_3_SQLITE_POSTGRES_IMPORT_RECONCILIATION.md`; and generated-object
recovery remains authoritative in `STAGE_3_GENERATED_OBJECT_RECOVERY.md`.
