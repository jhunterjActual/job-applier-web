# Stage 3 generated-object recovery generations

Build `20260813.25` adds dormant operator tooling for copying the already
encrypted generated-object ciphertext from the permanent private R2 bucket to
an isolated OneDrive recovery location. It does not run a copy, configure a
credential, install a timer, activate a release, migrate data, change the
database schema, mark hosted authority, or delete a recovery generation.

The copy is not a second application object store. PostgreSQL metadata and the
permanently private R2 bucket remain authoritative. A OneDrive generation is
useful only with its paired pgBackRest full backup and an independently
recoverable complete object-wrapping keyring. It is never a runtime fallback.

## Installed boundary and credentials

Provisioning installs these manual helpers:

- `/usr/local/sbin/careertrellis-mirror-generated-objects`
- `/usr/local/sbin/careertrellis-test-generated-object-restore`

Both run as root, acquire the shared maintenance lock and a dedicated recovery
lock, require the web, search-worker, and erasure-worker services to be
inactive, and fail if `/usr/local/sbin/careertrellis-authority status` changes
during the operation. Transfer and manifest work runs as the non-login
`careertrellis-object-recovery` account. Its state and work directories are
mode `0700` under `/var/lib/careertrellis/generated-object-recovery`.

Configure exactly one private `type=onedrive` remote in
`/var/lib/careertrellis/generated-object-recovery/rclone.conf`. The file must
be a regular, non-symlinked file owned by
`careertrellis-object-recovery:careertrellis-object-recovery`, mode `0600`.
The drive and root must be isolated from pgBackRest and every mutable backup
tree.

Install `/etc/careertrellis/generated-object-recovery.env` as a regular,
non-symlinked `root:root` mode `0600` file. It may contain only:

```text
CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ENDPOINT=...
CAREERTRELLIS_GENERATED_OBJECT_SOURCE_BUCKET=...
CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ACCESS_KEY_ID=...
CAREERTRELLIS_GENERATED_OBJECT_SOURCE_SECRET_ACCESS_KEY=...
CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_REMOTE=...
CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_ROOT=...
```

The R2 identity must be dedicated to the generated-object bucket and able to
list/read but not create, overwrite, or delete. It must be distinct from the
web, erasure, pgBackRest, database, and provider identities. The helper has no
object-wrapping keyring, plaintext, database URL, pgBackRest credential, or
pgBackRest cipher passphrase. Use
`deploy/config/generated-object-recovery.environment.example` as the assignment
contract; never commit configured copies.

The reviewed configuration files provide provider identities and credentials.
The command line contains only an immutable release path, public backup label,
expected manifest digest, explicit acknowledgement tokens and, for a restore
test, the path of a root-owned target environment. Receipts omit endpoints,
credentials, local paths, object keys, tenants, owners, and keyring values.

The complete active-and-retired object-wrapping keyring remains outside this
process and both storage providers. Its independently tested escrow is a
separate recovery dependency; retained ciphertext is useless when an old key
is missing.

## Create one quiescent immutable generation

Keep the maintenance boundary and all three services stopped while taking and
verifying the paired full pgBackRest backup and while running this mirror. Use
the exact 40-character deployed release revision and exact full-backup label
(`YYYYMMDD-HHMMSSF`):

```bash
sudo /usr/local/sbin/careertrellis-mirror-generated-objects \
  /opt/careertrellis/staging/releases/EXACT_40_CHARACTER_COMMIT \
  YYYYMMDD-HHMMSSF \
  I_HAVE_VERIFIED_SOURCE_R2_CREDENTIAL_IS_READ_ONLY \
  I_UNDERSTAND_THIS_APPENDS_AN_IMMUTABLE_ONEDRIVE_SNAPSHOT
```

The helper derives `snapshot_id` as `<release>-<backup-label>` and the
destination as `<reviewed-remote>:<reviewed-root>/<backup-label>`. The
destination must be empty. It downloads the source ciphertext with immutable
copy semantics and rejects any key other than `v1/<32 lowercase hexadecimal
characters>`, symlink/non-regular entry, file larger than `10 MiB + 64 bytes`,
inventory over 100,000 objects, or total/manifest bound violation.

`snapshot-manifest.json` is canonical JSON. It binds format/version, release,
backup label, snapshot ID, object count, total ciphertext bytes, and the sorted
`object_key`, `size_bytes`, and SHA-256 of every ciphertext object. Its maximum
serialized size is 32 MiB. The helper performs downloaded byte comparisons of
the destination objects, then copies the manifest last. The manifest
is therefore the completion marker; an interrupted generation without it is
not eligible for restore or retention decisions. Nothing is written to the
live R2 bucket.

The final stdout receipt is one sanitized JSON line with `authority`,
`backup_label`, `manifest_sha256`, `release`, `snapshot_id`, and
`result=onedrive_snapshot_created`. Preserve that receipt with the exact
pgBackRest verification receipt. The helper never runs `sync`, propagates a
live deletion, updates a `latest` pointer, or overwrites a prior generation.

## Automated disposable-R2 restore slice

Create a new empty, private, disposable R2 bucket. Put its identity in a
separate regular, non-symlinked `root:root` mode `0600` file containing only:

```text
CAREERTRELLIS_GENERATED_OBJECT_RESTORE_ENDPOINT=...
CAREERTRELLIS_GENERATED_OBJECT_RESTORE_BUCKET=...
CAREERTRELLIS_GENERATED_OBJECT_RESTORE_ACCESS_KEY_ID=...
CAREERTRELLIS_GENERATED_OBJECT_RESTORE_SECRET_ACCESS_KEY=...
```

The target endpoint/bucket identity and environment file must differ from the
source. Run the test with the mirror receipt's exact release, backup label, and
manifest digest:

```bash
sudo /usr/local/sbin/careertrellis-test-generated-object-restore \
  /opt/careertrellis/staging/releases/EXACT_40_CHARACTER_COMMIT \
  YYYYMMDD-HHMMSSF \
  EXPECTED_64_CHARACTER_LOWERCASE_MANIFEST_SHA256 \
  /root-owned/disposable-r2.env \
  I_HAVE_VERIFIED_THE_R2_TARGET_IS_DISPOSABLE_AND_EMPTY \
  I_UNDERSTAND_THE_DISPOSABLE_R2_TARGET_WILL_BE_LEFT_INTACT
```

The helper requires an empty target, downloads the exact OneDrive generation,
checks all downloaded bytes, verifies the canonical inventory and supplied
digest, and requires the embedded release, backup label, and snapshot ID to
match the requested identities. It then copies and download-checks only the
canonical ciphertext objects into the disposable bucket. It intentionally
leaves that bucket intact and puts no manifest or marker there because the
application reconciler rejects every non-`v1/` key.

A passing receipt has
`result=disposable_r2_restore_verified_and_left_intact` plus the same sanitized
identity fields. It proves the OneDrive ciphertext-and-manifest copy can
recreate an exact empty disposable R2 inventory. It does **not** by itself
prove PostgreSQL restore, database-to-object reconciliation, keyring recovery,
or decryption.

## Complete paired recovery rehearsal

Before production acceptance, retain separate operator evidence for the rest
of the recovery path:

1. Restore the paired pgBackRest label to an isolated PostgreSQL instance with
   no listener available to application traffic.
2. Independently recover the complete active-and-retired object-wrapping
   keyring without copying the live web environment into the workspace.
3. Reconcile restored `stored_objects` metadata, application references, and
   the disposable R2 inventory exactly. Missing, extra, duplicate, reassigned,
   or inferred rows/objects fail the rehearsal.
4. Authenticate and decrypt every restored object against its owner-bound
   metadata, then verify plaintext size and SHA-256. Any missing retired key or
   altered ciphertext, metadata, owner, object key, or wrapped data key fails
   closed.
5. Record sanitized restore evidence and measured RPO/RTO. Destroy disposable
   resources only after an operator reviews and approves their exact targets.

This remains offline operator work. It never enables the web service, changes
the authority marker, writes to production, merges with an existing bucket, or
offers an in-app shared-host restore.

## Retention and purge are a separate blocked gate

Build `20260813.25` implements no OneDrive deletion or purge helper. Mirror and
restore credentials must not be treated as purge authority. Recovery retention
must be finite and aligned with the paired PostgreSQL backup retention, but no
generation may be deleted until a separately reviewed exact-target procedure
exists and its production policy is approved.

That future gate must use a separately controlled destination-delete identity,
the shared and recovery locks, exact complete snapshot identity, explicit
confirmation, and sanitized deletion receipt. It must reject the current,
incomplete, unknown, caller-selected, or still-required generation. Before a
purge, evidence must show a newer completed generation passed the full paired
rehearsal, the target is outside the approved retention window, and its paired
pgBackRest backup is no longer retained. Provider recycle-bin or version
history is defense in depth, not proof of deletion.

Account erasure removes live ciphertext; it does not claim immediate rewriting
of separately retained recovery media. The approved retention disclosure must
state that finite recovery-media window.

## Acceptance checklist

- [ ] The source R2 token is bucket-scoped read/list only and all source,
      destination, pgBackRest, database, erasure, and keyring identities remain
      separate.
- [ ] The dedicated account, `0700` directories, `0600` configs, exact
      allowlists, remote type, locks, three-service quiescence, and unchanged
      authority checks pass.
- [ ] One exact full pgBackRest label, immutable release, derived snapshot ID,
      canonical manifest digest, and successful mirror receipt are retained as
      one recovery generation.
- [ ] The disposable-R2 helper reproduces and download-checks only the exact
      manifest ciphertext keys and leaves the target intact for review.
- [ ] Isolated PostgreSQL restore, exact DB/object reconciliation, independent
      keyring recovery, decrypt-every-object evidence, and measured RPO/RTO are
      recorded separately; the helper receipt is not overclaimed.
- [ ] Retention/purge remains blocked until its separate policy, credential,
      exact-target helper, tests, rehearsal, and receipts are approved.
- [ ] No timer, activation hook, authority capability, migration, schema
      change, runtime fallback, live credential, or external operation is
      introduced by merely deploying this slice.
- [ ] Focused recovery, object-store, import/reconciliation, cutover, P0,
      dependency, compilation, shell-syntax, documentation, and protected-file
      checks pass for build `20260813.31`.

The final authority transition remains governed by
`STAGE_3_POSTGRES_R2_FINAL_CUTOVER_COORDINATOR.md`. These receipts are manual
prerequisites only; they do not activate the dormant coordinator or make it
compatible with hosted authority.
