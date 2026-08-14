# Stage 3: PostgreSQL Identity and Import Transition

This transition is intentionally smaller than the final application cutover.
Shared-host authentication resolves an existing PostgreSQL `app_users` row
under the immutable WorkOS identity context. A browser login cannot create an
account, choose a role, restore a disabled account, or promote itself to owner.
Standalone local mode continues to use SQLite.

The running staging application must not be switched to this release until the
exact PR is reviewed and merged. It still stores profile, job, application, and
generated-file data in SQLite; it is not ready for additional users or public
access.

## Configuration boundary

Shared-host web releases require:

```text
CAREERTRELLIS_IDENTITY_STORE=postgres
```

The web service loads `CAREERTRELLIS_DATABASE_URL` from the existing root-owned
`/etc/careertrellis/postgres-runtime.env` file. The idle worker does not receive
its PostgreSQL credential yet; its independent
`/etc/careertrellis/postgres-worker.env` bundle remains reserved for the later
queue transition. Both files must remain `root:root` mode `0600`; never copy
their contents into the shared staging environment, logs, shell history,
tickets, or PR text.

Readiness checks confirm that the runtime role can reach the PostgreSQL identity
table. Liveness remains independent of PostgreSQL so the service supervisor can
distinguish a running process from a ready application.

## Import preflight

After an exact reviewed release exists—but before activation—run the bounded,
read-only preflight against the current SQLite workspace:

```bash
sudo /path/to/release/deploy/scripts/preflight-sqlite-postgres-import.sh \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT
```

The preflight validates the current SQLite schema, owner identity, relationships,
job URLs, and JSON values. Its output contains table counts plus the number of
legacy credential fields and generated-file paths that require separate
migration. It never prints their values, profile content, job data, URLs, or
filesystem paths. Provider-secret counts identify values that the encrypted
import stage must protect; generated-file counts remain a separate object-store
migration gate.

## Rehearsal importer

`backend/sqlite_postgres_import.py` supports a disposable PostgreSQL rehearsal.
It obtains an advisory transaction lock, assumes the non-login schema-owner
role through the migration account, creates or verifies only the configured
owner mapping, sets transaction-local tenant context, requires that owner's
target tables to be empty, remaps all SQLite identifiers, and commits only when
every target row count exactly matches the source.

With the encrypted-provider-secret stage present, the importer encrypts legacy
provider fields before insertion. The omission switches exist for isolated,
deliberately incomplete rehearsals only:

```text
--omit-provider-secrets
--omit-generated-files
```

Do not use an omission rehearsal as the final cutover. Provider credentials
must be encrypted under an independently escrowed server key, and generated
files must first move into authorized private object storage. The SQLite
database and data directories remain the authoritative copy until those
migrations, repository conversion, negative cross-user tests, and rollback
rehearsal are reviewed.

Every successful wrapped import is now followed, under the same maintenance
boundary, by the independent read-only reconciliation implemented by
`reconcile-sqlite-postgres-import.sh`. The reconciler reopens the frozen source,
owner-scoped target, and private-object inventory without repairing either side.
If import exits unsuccessfully or its commit outcome is ambiguous, do not retry:
run that named reconciliation command with the identical release, environment
files, and omission flags first. Its bounded operator contract and sanitized
receipt are documented in
`STAGE_3_SQLITE_POSTGRES_IMPORT_RECONCILIATION.md`.

## Deployment sequence after review

1. Fetch the exact merge commit into the root-owned application-host checkout.
2. Build and test its immutable release directory without activating it:
   `sudo careertrellis-deploy --prepare-only EXACT_MERGE_COMMIT`.
3. Take and record a fresh successful full pgBackRest backup.
4. Apply Alembic through `/etc/careertrellis/postgres-migration.env` and run the
   reviewed runtime grants.
5. Run the SQLite import preflight and retain only its sanitized counts.
6. Seed/import into a disposable database, retain the automatic no-omission
   reconciliation receipt, and repeat the tenant-isolation suite.
7. Activate the identity transition only after PostgreSQL owner authorization,
   readiness, login, logout, and rollback checks pass.

The private generated-object transition and its disposable full-import gate are
documented in `STAGE_3_PRIVATE_OBJECT_STORAGE.md`. The later final data cutover
must stop web and worker writes, take fresh SQLite and PostgreSQL backups,
migrate encrypted secrets and private objects, import in one transaction,
verify per-owner counts and object hashes, switch repository traffic, and
preserve a tested code-and-data rollback point.

The profile and base-resume repository foundation, remaining repository domains,
and the split-brain-safe final cutover contract are documented in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`. That foundation is not approval to
activate a shared-host PostgreSQL data store.
