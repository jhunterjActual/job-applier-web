# Stage 3: SQLite/PostgreSQL import reconciliation

This slice adds the fail-closed reconciliation gate for a reviewed SQLite-to-
PostgreSQL/private-object import. It does **not** import, repair, delete, activate,
or change data authority. `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false,
no hosted-authority compatibility manifest is shipped, and the single Alembic
head remains `20260814_0009`.

## Read-only boundary

Run reconciliation only as root from the same immutable 40-character reviewed
release used for the import. The wrapper accepts the same target migration and,
unless generated files were deliberately omitted, target object-storage
environment files:

```bash
sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/reconcile-sqlite-postgres-import.sh \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-rehearsal.env \
  /etc/careertrellis/object-storage-rehearsal.env
```

The wrapper also loads the root-owned staging owner and provider-secret files.
Every environment file must be a regular, non-symlinked `root:root` mode `0600`
file. Their contents, database URLs, owner identifiers, object keys, local
paths, document hashes, and tenant data must never be printed or retained with
the receipt.

Before opening either source or target, the command:

- takes the shared non-blocking maintenance lock and SQLite-import lock;
- proves the web, search-worker, and erasure-worker services are inactive;
- requires the installed hosted-authority helper to confirm the exact
  `sqlite-pre-cutover` state; and
- confines execution to the reviewed release and configured SQLite data root.

The reconciler opens SQLite read-only and PostgreSQL in a read-only transaction.
It resolves only the configured imported owner, sets transaction-local owner
context before protected reads, and never creates an owner, inserts or updates a
row, repairs a reference, deletes an object, or invokes an application cleanup
reconciler. Object access is limited to inventory/read/head operations needed to
authenticate the imported encrypted objects and compare them with the accepted
source-file inventory. No public object URL is created.

## What must agree

A successful sanitized receipt means that the frozen SQLite snapshot and the
selected owner scope agree field-for-field across every table supported by the
exact release's importer; encrypted provider-secret plaintext agrees after
authenticated decryption; PostgreSQL relationships and application object
references are valid; and each accepted generated file has exactly one matching
active `stored_objects` record and one authenticated private object with the
expected plaintext size and SHA-256 digest. Scheduled-search configuration in
`saved_searches` is included in that comparison.

Search execution history is deliberately nonportable. Import fails while any
local search run is active, the selected PostgreSQL owner must have no existing
`search_runs`, and the receipt reports only the count of completed local runs
left in the frozen SQLite snapshot. It does not copy request/result/error text,
leases, cancellation state, attempts, or dispatch claims.

The dedicated import bucket inventory must contain no missing, duplicate, or
orphan key. Deleted/tombstoned application objects are not a valid substitute
for imported active material. Any count or field mismatch, reference mismatch,
unexpected target search run, missing object, authentication failure, hash/size
mismatch, or orphan makes reconciliation fail with one fixed message. Reads are
explicitly limited to the configured imported owner; unrelated tenant scopes
are neither exposed in the receipt nor treated as part of this single-owner
comparison. The operator investigates using private host evidence; the command
never infers ownership or performs cleanup.

`--omit-provider-secrets` and `--omit-generated-files` remain available only to
reconcile an explicitly incomplete diagnostic rehearsal made with the identical
import option. A receipt containing either omission cannot clear the disposable
no-omission rehearsal or final cutover gate.

## Import and ambiguous outcomes

The root import wrapper now runs reconciliation immediately after a successful
import while its maintenance/import locks remain held and the web, search-
worker, and account-erasure-worker services remain inactive. Success therefore
requires both the atomic import receipt and a fresh independent read-only
reconciliation receipt.

If the importer exits unsuccessfully—including an indeterminate PostgreSQL
commit or object-compensation result—the wrapper does not retry. It names
`reconcile-sqlite-postgres-import.sh` as the required next command. Run that
command with the exact same release, source, target environment files, and
omission options before deciding whether a retry or disposable-target cleanup is
safe. Reconciliation remains read-only even when it detects partial state.

## Rehearsal and eventual cutover

First use this command only against a disposable empty PostgreSQL target and a
dedicated disposable private object bucket. Keep both targets until an operator
has reviewed the sanitized receipt. A later permanent import must repeat the
same no-omission command against its frozen source while the web, search-worker,
and account-erasure-worker services remain stopped, then retain the receipt with
backup, restore, RLS, object-integrity, readiness, and owner-flow evidence.

This PR does not authorize either run. It does not apply a migration, change a
database or bucket, edit an environment file, restart a service, move a release
symlink, mark PostgreSQL/R2 authoritative, expose staging, or invite another
user. Build strings agree at `20260813.21`; local SQLite behavior is unchanged.

## Acceptance checklist

- [ ] The exact immutable release passed the full web-foundation suite,
      including focused import-reconciliation tests and shell syntax.
- [ ] All environment files are regular `root:root` mode `0600` files and no
      secret or tenant value appears in the retained receipt.
- [ ] Both services stayed inactive and authority remained
      `sqlite-pre-cutover` for the whole read-only run.
- [ ] A no-omission disposable import automatically produced a successful
      sanitized reconciliation receipt for every supported table and all three
      generated-object surfaces.
- [ ] Missing/extra/corrupt imported-owner data, ambiguous commit outcomes,
      active local search work, unexpected target search runs, and orphan
      objects fail closed without repair.
- [ ] The Alembic head remains `20260814_0009`, cutover remains false, no
      compatibility manifest exists, and no live/external state changed.
