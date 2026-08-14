# Stage 3: Private Encrypted Per-User Object Storage

This stage adds the object-storage boundary required for generated resumes and
cover letters before shared-host cutover. The hosted application stores only
encrypted objects in a private Cloudflare R2 bucket and authorizes every read
through the authenticated application. Standalone local installations keep
their existing filesystem behavior.

This PR does **not** activate the release, move the authoritative SQLite
workspace, invite another user, or complete the PostgreSQL repository cutover.
Generated files and SQLite remain authoritative until the full migration and
rollback gate below has been rehearsed against disposable targets and the
subsequent repository transition has been reviewed.

## Security boundary

- The object bucket is private. Do not enable `r2.dev`, attach a public custom
  domain, create public object URLs, or grant browsers direct R2 access. The web
  process retrieves and decrypts an object only after authenticating its owner.
- Object keys are random, opaque, and immutable. They do not contain a user ID,
  name, employer, role, original filename, or local filesystem path.
- Every object has a new random 256-bit data-encryption key and 96-bit nonce.
  AES-256-GCM authenticated data binds the format, immutable owner, object
  identity, media type, plaintext size, and digest. Moving ciphertext or a
  wrapped data key to another owner or metadata row therefore fails closed.
- PostgreSQL stores owner-scoped object metadata and the wrapped data key under
  forced RLS. It does not store the object key-encryption key or R2 secret.
- The object key-encryption keyring is available only to the web process through
  a root-owned environment file. It is independent from the provider-secret
  keyring, the R2 S3 credential, the pgBackRest cipher passphrase, and database
  credentials. Neither PostgreSQL, Caddy, the search worker, nor the account-
  erasure worker receives it. The erasure worker later receives a distinct
  bucket-scoped deletion credential and can delete only opaque ciphertext keys
  supplied by its database broker; it cannot decrypt or serve an object.
- Upload is not considered complete until the service can read the ciphertext
  back, authenticate and decrypt it, and verify the plaintext digest and size.
  Failed metadata insertion is compensated by deleting the just-uploaded
  object; reconciliation still treats an unexpected object as an orphan rather
  than silently assigning it to a user.

Application-layer encryption protects R2 contents and provider-side snapshots
from plaintext disclosure. It does not protect plaintext while the authorized
web process is generating or returning a document. Host hardening, bounded
memory use, minimized logs, authenticated downloads, and process isolation
remain part of the security boundary.

## Separate R2 bucket and least-privilege credential

Create a new staging object bucket. It must not be the existing pgBackRest
repository or its OneDrive mirror, and it must use a new R2 S3 credential.

Configure the credential for object read/write access to only this bucket. It
does not need account administration, bucket creation, or access to any backup
bucket. If Cloudflare's token controls allow a source-IP restriction, restrict
the token to the application Droplet's verified outbound public `/32`; do not
use an administrator workstation IP or an end user's browser IP. When the
actual outbound address has not yet been proven, leave the filter unset only
for the bounded setup test, record the observed egress address without logging
credentials, then apply and retest the `/32` restriction before rehearsal.

Keep public development access, public buckets, custom public domains, and
browser CORS disabled. No client needs to contact R2 directly.

Store the access-key ID and secret access key in the password manager. Never
put either value in source control, a PR, chat, a ticket, a command argument, or
shell history. Revoking this credential stops object I/O but does not decrypt
existing objects.

## Independent object keyring

Before installing object-storage configuration, complete PR #8's provider-
secret gate: its full provider keyring and active key ID must be independently
escrowed and retrievable. The object keyring is additional recovery material,
not a replacement or copy of that provider keyring.

Generate a distinct random 32-byte object key-encryption key in a password
manager or offline workflow. Give it a non-identifying ID such as
`object-kek-2026-08-12-a`. Encode the keyring in the format described by
`deploy/config/object-storage.environment.example`, store both the complete
keyring and active ID in the password manager, and verify retrieval before any
real object is uploaded.

Losing this keyring makes encrypted objects unrecoverable even when the R2
bucket and PostgreSQL backup are intact. The R2 credential alone cannot decrypt
them. Keep old key-encryption keys during a rotation until every data key has
been rewrapped, a fresh backup has completed, and the rollback window no longer
depends on the old key.

## Root-owned web configuration

Create `/etc/careertrellis/object-storage.env` on the application Droplet with
the variable names from `deploy/config/object-storage.environment.example`.
First create the file, if absent, with its final ownership and mode; the guarded
command does not replace an existing file. Enter real values only through an
operator-controlled editor:

```bash
sudo sh -c 'if [ ! -e /etc/careertrellis/object-storage.env ]; then install -o root -g root -m 0600 /dev/null /etc/careertrellis/object-storage.env; fi'
sudo stat -c '%U:%G %a' /etc/careertrellis/object-storage.env
sudoedit /etc/careertrellis/object-storage.env
sudo chown root:root /etc/careertrellis/object-storage.env
sudo chmod 0600 /etc/careertrellis/object-storage.env
sudo stat -c '%U:%G %a' /etc/careertrellis/object-storage.env
```

The final command must report `root:root 600`; it deliberately does not print
the file. The systemd web unit requires this file. Do not add it to
`staging.env`, copy it into a release directory, or give it to the worker.

Hosted configuration uses `CAREERTRELLIS_OBJECT_STORE=r2_encrypted`. Local
standalone launchers do not load this file and continue using the ignored local
generated-materials directory.

## Reviewed-release rehearsal

Perform the following only after the exact PR is reviewed and merged.
`STAGE_3_POSTGRES_R2_CUTOVER_REHEARSAL.md` supplies the bounded wrapper for the
preflight, no-omission import, independent reconciliation, target-separation
checks, and sanitized receipt described below; backup and restore checks remain
separate operator steps.

1. Fetch that exact 40-character merge commit into the root-owned application
   checkout. Prepare the immutable release without activating it:

   ```bash
   sudo careertrellis-deploy --prepare-only EXACT_MERGE_COMMIT
   ```

2. On the database host, take a fresh pgBackRest full backup, copy the encrypted
   repository to OneDrive, inspect the backup record, and run the existing
   isolated restore test:

   ```bash
   sudo -u postgres pgbackrest --stanza=careertrellis --type=full backup
   sudo /usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive
   sudo -u postgres pgbackrest --stanza=careertrellis info
   sudo /usr/local/sbin/careertrellis-test-pgbackrest-restore
   ```

   Record only backup labels, times, sanitized counts, and success receipts.
   Never record the pgBackRest cipher passphrase or repository credentials.

3. Create a disposable PostgreSQL database and a disposable private R2 object
   bucket with the same permissions and public-access controls as the proposed
   staging bucket. Provision the required disposable database roles, migrate
   the database to this exact release's Alembic head, reapply this exact
   release's `deploy/postgres/grant-runtime.sql`, and verify the target is
   schema-only with no tenant, search-run, erasure-request, or stored-object
   rows. The rehearsal wrapper does not perform that preparation. Point
   root-owned rehearsal environment files at those targets. Do not use the
   live staging database or backup bucket.

4. Run the read-only SQLite preflight from the exact release and retain its
   sanitized table, provider-secret, and generated-file counts:

   ```bash
   sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/preflight-sqlite-postgres-import.sh \
     /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT
   ```

5. Import into the disposable targets with **no omission switches**:

   Stop the staging web, search-worker, and erasure-worker services first so the
   SQLite database and generated files are one quiescent source snapshot. Pass
   a separate root-owned mode-0600
   object environment file that names only the disposable rehearsal bucket;
   never overwrite the staging web object's environment file for this test.

   ```bash
   sudo systemctl stop careertrellis-web.service careertrellis-worker.service careertrellis-erasure-worker.service
   sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/import-sqlite-postgres.sh \
     /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
     /etc/careertrellis/postgres-rehearsal.env \
     /etc/careertrellis/object-storage-rehearsal.env
   ```

   A successful wrapper import immediately runs the read-only reconciler with
   those same arguments. For an interrupted or ambiguous import, run it
   explicitly before retrying:

   ```bash
   sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/reconcile-sqlite-postgres-import.sh \
     /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
     /etc/careertrellis/postgres-rehearsal.env \
     /etc/careertrellis/object-storage-rehearsal.env
   ```

   The reviewed deploy, rollback, and import wrappers share an exclusive
   maintenance lock. Do not manually start either application service while an
   import is running; the importer also rechecks that both are inactive after
   acquiring that lock.

   `--omit-generated-files` and `--omit-provider-secrets` are incomplete
   diagnostic modes and cannot clear this gate. The completed rehearsal must
   atomically import encrypted provider secrets and owner-bound generated-
   object metadata, upload every accepted generated file, verify readback and
   hashes, and report exact sanitized source and verified-target counts. Restart
   the prior active release only after the rehearsal receipt and target
   reconciliation have been reviewed.

6. Retain evidence that the exact release's automated and integration checks
   prove all of the following:

   - each imported object's decrypted bytes, plaintext size, and SHA-256 digest
     match its bounded source file;
   - modified ciphertext, metadata, owner ID, object key, or wrapped key fails
     authentication without returning partial plaintext;
   - a second synthetic tenant cannot select metadata or read, replace, delete,
     or download the first tenant's object;
   - missing tenant context exposes no protected object row;
   - upload/readback failure and database rollback leave no falsely committed
     object reference, while unexpected orphan keys are reported;
   - the bucket, database, application logs, test output, and API responses do
     not expose plaintext documents, local paths, R2 secrets, or keyring data;
   - provider capability tests still succeed with the PR #8 keyring; and
   - owner readiness, login, authorized material download, logout, and code
     rollback succeed while Caddy's access restriction remains enabled.

## Rollback and reconciliation

Preparing a release does not change the active symlink or service. If a later
activation fails readiness, the reviewed deployment helper restores the prior
code release. Database schema changes are corrected with a forward migration;
do not run an Alembic downgrade against staging.

Until final cutover is approved, keep the SQLite database and generated-files
directory unchanged and authoritative. Never delete a source file merely
because upload succeeded. A code rollback cannot by itself reverse committed
database rows or remove R2 objects.

For every rehearsal or interrupted migration, reconcile all three surfaces:
the accepted source-file inventory, owner-scoped `stored_objects` metadata, and
private-bucket object keys. Investigate any missing row, missing object, hash
mismatch, duplicate reference, or orphan key. Do not attach an orphan to a user
by inference. Remove disposable database/bucket resources only after the
operator reviews the sanitized reconciliation receipt and explicitly approves
that cleanup; never apply rehearsal cleanup to the pgBackRest bucket or an
authoritative data directory.

The root-only command, its read-only PostgreSQL/object behavior, service and
authority checks, fixed failure boundary, and sanitized receipt are specified
in `STAGE_3_SQLITE_POSTGRES_IMPORT_RECONCILIATION.md`.

## Acceptance checklist

- [ ] PR #8's provider-secret active key ID and complete keyring are independently escrowed and retrievable.
- [ ] A separate private object bucket exists; `r2.dev`, public domains, public URLs, and browser access are disabled.
- [ ] The bucket-scoped R2 credential is distinct from pgBackRest and limited to object read/write on this bucket.
- [ ] The token is restricted to the verified application-host egress `/32`, or a time-bounded setup exception is recorded and closed before rehearsal.
- [ ] The object active key ID and complete 32-byte-key keyring are distinct from all provider, database, backup, and R2 credentials and are independently escrowed.
- [ ] `/etc/careertrellis/object-storage.env` is `root:root` mode `0600`, is required only by the web unit, and contains no placeholder.
- [ ] Standalone local launch and filesystem storage still pass without hosted object configuration.
- [ ] The exact reviewed merge commit is prepared with `--prepare-only`; the active release has not changed.
- [ ] A fresh pgBackRest backup, OneDrive mirror, and isolated restore rehearsal succeeded immediately before migration rehearsal.
- [ ] A disposable import with no omission flags reports exact source/target row and generated-object counts.
- [ ] Readback, plaintext hash/size, tamper rejection, transaction compensation, and orphan reconciliation checks pass.
- [ ] Missing-context and two-tenant negative tests prove metadata and object read/write/delete/download isolation.
- [ ] Provider capability, owner readiness, login, authorized download, logout, and code rollback checks pass without secret or document leakage.
- [ ] SQLite and local generated files remain authoritative; no live cutover or additional user invitation occurred in this PR.
- [ ] The final activation decision is deferred until repository conversion, rollback compatibility, and all remaining multi-user gates are reviewed.

The next profile/base-resume repository slice and its explicit no-activation,
data-authority, and split-brain rollback boundary are documented in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.

The later generated-material repository consumer, including verified upload,
atomic application-reference replacement, tombstoned cleanup, and fixed-error
download behavior, is documented in
`STAGE_3_POSTGRES_GENERATED_MATERIALS.md`.
