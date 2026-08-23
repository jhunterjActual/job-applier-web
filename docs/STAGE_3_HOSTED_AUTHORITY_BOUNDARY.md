# Stage 3 hosted data-authority boundary

The release-independent operational latch protects the PostgreSQL/private-R2
cutover. Build `20260813.31` is the reviewed final-capable release: it preserves
the true repository-cutover flag and exact hosted-authority compatibility
declaration introduced by build `20260813.30`, while repairing the prepared-
release helper modes needed by the coordinator. It does **not** perform the
cutover merely by being merged, installed, or prepared. Do not import the
permanent workspace, mark hosted storage authoritative, or activate it outside
the reviewed two-phase coordinator.

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
service, the installed helper validates the selected immutable target. While
authority is `sqlite-pre-cutover`, it admits only a release with exactly one
false repository-cutover assignment and no hosted compatibility manifest. It
also rejects any start while the root-only final-cutover pending record exists.
This prevents ordinary deployment from starting final-capable code before the
authority marker and prevents a paused/partial permanent import from resuming
SQLite writes. The systemd drop-ins then run the exact root-owned installed
helper with the systemd `+` execution prefix before every web, search-worker,
or account-erasure-worker start. The elevated pre-start process reads only the
root-owned authority control files and never inherits service credentials;
restoring an older unit file does not remove those drop-ins. In
`postgres-r2-authoritative` state, all three services require the current
immutable release to contain exactly this reviewed capability declaration:

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
release capability. When a root-only pending attempt exists after the marker,
runtime is denied unless the phase is `activated`, or it is `activating` and a
root:root mode `0600` transient lease proves that the exact live coordinator
PID, kernel boot identity, and process start time still match and that process
still holds descriptor 10 on the maintenance lock. Binding all three process
identities prevents a recycled PID from reviving a stale lease. A crash,
reboot, stale lease, mismatched attempt/release, or any other pending phase
therefore denies startup.

The exact eight-line `activated` record remains immutable historical evidence
of the release and attempt that completed cutover. It is not rewritten and does
not permanently pin later code releases to that historical revision. A later
release may start only when its own immutable directory still passes the hosted
capability check and the live runtime selections remain PostgreSQL/private R2.
The nonterminal `activating` phase deliberately remains bound to the exact
current revision and live coordinator lease; this distinction permits reviewed
post-cutover forward releases without weakening ambiguous-cutover containment.

Build `20260813.31` preserves that capability file only alongside the true repository
cutover flag and the completed shared-host, owner-isolation, recovery, and
two-phase coordinator gates. Pre-cutover ordinary deploy and runtime checks
continue to reject the final-capable release until the marker transition.

Manual rollback and a post-readiness-failure automatic rollback are allowed
only in `sqlite-pre-cutover`. After hosted authority is marked, the rollback
helper refuses before changing the release symlink. A failed hosted deployment
leaves the application services stopped and requires a reviewed forward
correction. The permanent SQLite import wrapper is also denied after the
transition. The root-only read-only reconciliation wrapper is likewise allowed
only in `sqlite-pre-cutover`; preflight remains available. Neither wrapper can
rewrite the marker.

## Eventual operator transition (not for this PR)

The later dormant coordinator uses two separately locked phases. Phase one
migrates, grants, imports, reconciles, and durably pauses with every application
service stopped and authority unchanged. It writes `preparing` before service
stop, source hashing, or target mutation; a reviewed preparation failure can be
resumed without treating a never-started import as ambiguous. `importing` is
written only immediately before the importer. The root-only pending record
blocks pre-cutover deploy/runtime while the operator creates and restores both
the post-import PostgreSQL backup and generated-object recovery generation.
Phase two revalidates the exact attempt and reconciliation before marking
authority and activating the same release under the transient live-coordinator
lease. Failures before a durable attempt leave prior service state unchanged;
failures after it starts leave services stopped, while every post-marker
failure requires a forward correction.
Its exact receipt and ordering contract is documented in
`STAGE_3_POSTGRES_R2_FINAL_CUTOVER_COORDINATOR.md`; that slice does not add the
capability or perform this transition.

The final cutover must keep the web, search-worker, and account-erasure-worker
services stopped from the phase-one source freeze through the phase-two marker.
The maintenance lock is held within each automated phase; the durable pending
record and loaded start guards preserve quiescence during the operator-reviewed
post-import recovery pause. Only after those receipts pass, and **before any
service can accept the first PostgreSQL/R2 write**, may the exact coordinator
invoke the internal one-way authority command. Do not invoke the authority
helper directly; it requires the coordinator's inherited lock, exact pending
attempt ID, exact release, and paired recovery-evidence record.

The command itself rejects a target outside the immutable 40-character release
path, an absent/symlinked/executable/non-exact capability file, a non-unique or
false repository-cutover assignment, runtime files that do not select
PostgreSQL/private R2 exactly, or the absence of the exact release-bound
`recovery-verified` pending attempt and its exact recovery evidence. The exact reviewed release must pass that
check before the marker changes or any service is started. Check the non-secret
state with:

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
lock checks, a single `20260814_0009` Alembic head, build `20260813.31`, no
protected-file changes, and confirmation that live/external state remains
untouched by the code PR. The reconciliation boundary is documented in
`STAGE_3_SQLITE_POSTGRES_IMPORT_RECONCILIATION.md`.
