# Stage 3 hosted data-authority boundary

This slice adds the release-independent operational latch required before the
eventual PostgreSQL/private-R2 cutover. It does **not** perform that cutover.
`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false, the live service and
environment files are unchanged, and this release intentionally has no hosted-
authority compatibility declaration. Do not import the permanent workspace,
mark hosted storage authoritative, activate this release, restart staging, or
change PostgreSQL, R2, DNS, Caddy, credentials, or users for this PR.

## Durable one-way state

Reviewed host provisioning installs the root-owned helper as
`/usr/local/sbin/careertrellis-authority`, installs release-independent systemd
drop-ins for the web and search-worker services, and initializes this file only
when the entire authority directory is absent on a virgin host. The later
account-erasure slice installs the same guard for its separately disabled
worker:

```text
/etc/careertrellis/authority/data-authority
```

The directory is beneath root-owned `/etc` and is `root:root` mode `0755`; the
non-secret marker is `root:root` mode `0644`. The only accepted states are:

- `sqlite-pre-cutover`: SQLite/generated files remain authoritative, so the
  existing pre-cutover code rollback and import rehearsal behavior remains
  available; and
- `postgres-r2-authoritative`: PostgreSQL and the permanent encrypted R2 bucket
  are the sole writable authority. This state has no downgrade command.

Initialization is idempotent only after a valid state exists. An existing valid
state is retained. If the authority directory already exists but the marker is
missing, including on a provisioning rerun, initialization fails closed and
never recreates `sqlite-pre-cutover`; a symlink, invalid content, or unexpected
ownership/mode also fails with the same fixed error. State replacement is atomic
and is synced before the helper returns. The authority directory is outside
every immutable release and survives code-symlink changes.

Deploy and rollback validate but never initialize or rewrite the marker. An
existing host must rerun the reviewed no-start provisioning step once to install
the helper/drop-ins and create the pre-cutover state before deploying this
boundary release.

The state is operational control data, not application or tenant data, so the
authority slice itself needed no Alembic change and its head remained
`20260813_0008`. The later account-erasure slice advances the single head to
`20260814_0009` without changing or activating this marker.

## Restart and rollback enforcement

Before deploy or rollback mutates a release symlink, unit, installed helper, or
service, the installed helper validates the selected immutable target. The
systemd drop-ins then run the installed helper before every web, search-worker,
or account-erasure-worker start; restoring an older unit file does not remove
those drop-ins. In `postgres-r2-authoritative` state, all three services require the current immutable
release to contain exactly this reviewed capability declaration:

```text
deploy/hosted-authority.compatibility: postgres-r2-authoritative-v1
```

The pre-mutation deploy check and web guard also require the already root-owned
runtime environment to select `CAREERTRELLIS_DATA_STORE=postgres` and
`CAREERTRELLIS_OBJECT_STORE=r2_encrypted`. The search worker deliberately
receives no web, WorkOS, migration, or object-store credentials. The separate
account-erasure worker receives only its broker database URL, bounded settings,
and delete-only private-bucket credential—never WorkOS, provider-secret, web,
upload, or object-decryption settings. Both workers are covered by the same
release capability.

This PR does not add that capability file because the repository cutover flag
is still false. A later final cutover review must add it only to a release whose
complete shared-host paths and owner-isolation gates are ready.

Manual rollback and a post-readiness-failure automatic rollback are allowed
only in `sqlite-pre-cutover`. After hosted authority is marked, the rollback
helper refuses before changing the release symlink. A failed hosted deployment
leaves the application services stopped and requires a reviewed forward
correction. The permanent SQLite import wrapper is also denied after the
transition. The root-only read-only reconciliation wrapper is likewise allowed
only in `sqlite-pre-cutover`; preflight remains available. Neither wrapper can
rewrite the marker.

## Eventual operator transition (not for this PR)

The final cutover must keep the maintenance lock held and the web, search-
worker, and account-erasure-worker services stopped through snapshot,
migration, no-omission import, and
reconciliation. Only after those receipts pass, and **before any service can
accept the first PostgreSQL/R2 write**, an operator may run the one-way command:

```bash
sudo /usr/local/sbin/careertrellis-authority \
  mark-postgres-r2-authoritative \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED
```

The command itself rejects a target outside the immutable 40-character release
path, an absent/symlinked/executable/non-exact capability file, or runtime files
that do not select PostgreSQL/private R2 exactly. The exact reviewed release
must pass that check before the marker changes or either service is started. Check
the non-secret state with:

```bash
sudo /usr/local/sbin/careertrellis-authority status
```

After the marker changes, never restore writable SQLite, rerun the SQLite
import, or use code-only rollback. Retain PostgreSQL/R2 authority and apply a
reviewed forward code or schema correction. On host recovery, restore or
recreate the authoritative marker before installing/enabling either service;
an absent marker must not be treated as permission to start.

## Explicit exclusions and acceptance

This bounded authority PR added only the host-local marker helper, persistent
systemd guards, deploy/rollback/import enforcement, synthetic tests,
documentation, and build metadata. The later import-reconciliation and
disposable-rehearsal wrappers run under the same pre-cutover assertion; neither
weakens or rewrites this boundary. See
`STAGE_3_POSTGRES_R2_CUTOVER_REHEARSAL.md`. Those slices did not:

- flip `HOSTED_REPOSITORY_CUTOVER_COMPLETE` or add the compatibility file;
- activate a release, restart a service, import or modify any database/object,
  edit an environment file, or access a secret;
- add a migration, downgrade path, hosted backup/restore UI, account deletion,
  public access, or additional worker credentials; or
- make root-host compromise recoverable. Root remains the trusted operator
  boundary, and deletion or corruption of the marker fails service startup.

The later account-erasure slice adds migration 0009 and an isolated worker but
still does not change authority, add the compatibility file, or activate a
service. Its boundary is documented in
`STAGE_3_POSTGRES_ACCOUNT_ERASURE.md`.

Acceptance for the combined tree requires shell syntax, marker transition/fixed-
error and import-reconciliation tests, the P0 suite, compile checks, dependency-
lock checks, a single `20260814_0009` Alembic head, build `20260813.23`, no
protected-file changes, and confirmation that the cutover flag and live/external
state remain untouched. The reconciliation boundary is documented in
`STAGE_3_SQLITE_POSTGRES_IMPORT_RECONCILIATION.md`.
