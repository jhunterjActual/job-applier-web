# Stage 3: PostgreSQL account erasure

This slice adds an authenticated, owner-only, asynchronous erasure boundary for
CareerTrellis application data in selected shared mode. It does **not** delete
the person's WorkOS identity, call a WorkOS administration API, revoke every
identity-provider session, activate shared hosting, or change data authority.
`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false, the erasure worker stays
disabled, and no hosted-authority compatibility declaration is shipped.

## User contract

`POST /api/account/erasure` is available only to the authenticated owner in
selected PostgreSQL mode. The request must contain the exact confirmation text
`DELETE MY CAREERTRELLIS DATA` and an explicit irreversible-action
acknowledgement. Local SQLite mode returns a fixed `409`; local backup and local
workspace deletion behavior remain unchanged.

Acceptance is irreversible and cannot be cancelled. A successful request
returns `202 Accepted`, uses `Cache-Control: no-store`, and clears the local
CareerTrellis session cookie. There is no public polling URL, bearer receipt,
or token that can disclose deletion progress. Authentication fails closed after
acceptance because the local owner mapping is disabled; deleting the separate
WorkOS identity, if desired, remains the user's or operator's independent
identity-provider action.

The API exposes only fixed validation, conflict, unavailable, and accepted
responses. The accepted body contains only status, acceptance time, and the
fixed 30-day receipt-retention period. It never returns an internal receipt or
object identifier, object key, WorkOS identifier, database ID, provider-secret
state, progress detail, or failure detail.

## Atomic acceptance and durable cleanup

One short owner-scoped PostgreSQL acceptance transaction locks the active
`app_users` row and any existing erasure request, then idempotently returns the
prior acceptance or disables that owner mapping and inserts one durable request.
It does not enumerate or lock dependent tenant state, snapshot object keys,
delete tenant rows, or call R2. Disabling the owner prevents new authenticated
owner work while deletion is pending.

The worker later claims the durable request. Under the request lease and the
owner catalog lock, each short object-batch transaction sets tenant context,
tombstones a bounded batch of `stored_objects`, and returns only their opaque
keys after the transaction closes. Ciphertext deletion occurs outside the
database transaction; a separate lease-bound confirmation records each deleted
blob. Finalization takes the same locks, rechecks that every owner object has a
confirmed erasure marker, then deletes `app_users`. Foreign-key cascades remove
the remaining profiles, resume history, jobs, saved searches and durable search
work, applications and histories, suppression/diagnostic/cache data, provider
secrets, and stored-object metadata in that final transaction.

The accepted request and dynamically discovered object-deletion work survive process termination and
lease expiry. Claim, success, retry, and terminal transitions are atomic and
attempt-bounded. Repeated delivery or worker restart is idempotent. Missing R2
objects are treated as already deleted; transient deletion failure keeps only
the bounded retry state needed for another attempt. User cancellation is not a
state transition after acceptance.

After every blob and protected domain row is gone, finalization immediately
deletes the disabled `app_users` row. The receipt then survives ownerless with a
nullable owner reference and no WorkOS identifier. Staging authentication stays
unauthorized after that deletion unless an operator deliberately performs the
separate reviewed owner-seeding procedure; login never recreates the mapping.

The final minimized ownerless operational receipt contains no plaintext profile,
document, job, provider-secret, WorkOS, session, R2 credential, object-key, or
local-path data. It is retained for at most 30 days after completion and is then
purged by the same bounded durable execution path. The receipt is operator-only
and is never exposed through an application status endpoint.

## Dedicated worker boundary

The account-erasure worker is separate from both the web process and durable
search worker:

- Unix user `careertrellis-erasure-worker` has a private writable directory at
  `/var/lib/careertrellis/account-erasure`.
- PostgreSQL login role `careertrellis_erasure_worker` is `NOSUPERUSER`,
  `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, `NOREPLICATION`, and
  `NOBYPASSRLS`. It receives schema usage and only the migration's narrowly
  scoped SECURITY DEFINER broker functions. It receives no table, sequence, or
  general function privileges.
- The broker fixes `search_path`, checks exact `SESSION_USER`, returns only the
  bounded work needed for deletion, and performs cross-owner state transitions
  internally. The worker never selects another tenant's repository rows.
- `/etc/careertrellis/postgres-erasure-worker.env` contains only the dedicated
  database URL. `/etc/careertrellis/account-erasure-object-storage.env`
  contains a separate private-bucket deletion credential. The worker does not
  load the web runtime, WorkOS, trusted-proxy, migration, provider-secret,
  search-worker, object-decryption keyring, or object active-key settings.
- The deletion credential is scoped to the generated-material bucket and is
  used only to delete the opaque keys returned by the broker. The worker cannot
  decrypt, download for user delivery, re-encrypt, or upload material.
- Poll interval, batch size, attempts, leases, and receipt retention are fixed
  or range-bounded. Startup/shutdown logs contain only build, validated
  environment, and fixed queue/lifecycle state. The heartbeat contains only
  build, PID, fixed lifecycle state, and update timestamp—never owner, request,
  lease, object, WorkOS, credential, or exception data.

Provisioning installs the unit and root-owned mode-`0600` example files but
does not enable or start it. This release requires
`CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=false`; a later reviewed hosted
activation must deliberately change that flag, select
`CAREERTRELLIS_DATA_STORE=postgres`, satisfy the one-way authority/cutover
guards, and enable the unit. Web UI or an API request cannot activate the
worker.

## Role, migration, and grant order

The additive role bootstrap is deliberately separate from the original
three-role credential bundle so an existing database host never rotates its
migration, web-runtime, or search-worker passwords:

1. From the exact reviewed release on the database host, run
   `bootstrap-erasure-worker-role.sh`. It creates only
   `careertrellis_erasure_worker`, encrypts its independently generated
   credential to the existing age recipient, and fails closed if role and
   credential state disagree.
2. Transfer only that encrypted bundle and run
   `careertrellis-install-erasure-worker-credential` on the application host.
   The installer parses one exact non-executable credential line and creates
   `/etc/careertrellis/postgres-erasure-worker.env` as `root:root` mode `0600`.
3. With all application workers inactive, apply migration
   `20260814_0009_account_erasure`. The role must exist first because the
   migration revokes broad access and grants its exact broker functions.
4. Reapply `deploy/postgres/grant-runtime.sql` after migration. It revokes all
   table, sequence, and general function privileges from the erasure role, then
   restores only the exact broker EXECUTE grants.
5. Install a distinct delete-only private-bucket credential, keep the worker
   flag false, and run the disposable PostgreSQL/R2 isolation and retry tests.

Never pass a database or R2 secret on a command line, print either environment
file, source an untrusted credential bundle, reuse the web R2 credential, or
grant the erasure worker the object keyring.

## Rollback and no-activation boundary

Migration `20260814_0009` is additive; correct it with a reviewed forward
migration rather than downgrading a shared database. Before the erasure worker
has ever been enabled, code rollback may target a pre-erasure release while
hosted authority remains `sqlite-pre-cutover`. Once the worker is active, deploy
and rollback refuse a target without the erasure entrypoint and unit so durable
work is not stranded under incompatible code.

Import, reconciliation, and the one-way hosted-authority transition require the
web, search worker, and erasure worker all to be inactive. They do not enable,
cancel, repair, or consume an erasure request. This PR does not run a migration,
import a workspace, start or enable a service, change an environment file, use
R2/PostgreSQL/WorkOS, or perform an account deletion.

## Explicit exclusions

This slice does not add WorkOS account deletion, global WorkOS session
revocation, a public status or cancellation endpoint, in-app worker activation,
backup-media rewriting, legal-retention policy, bucket-wide administrator
cleanup, search-worker object credentials, plaintext audit records, or a
hosted-authority compatibility manifest. PostgreSQL/R2 backup retention and
expiry remain operator policy; a CareerTrellis erasure receipt does not claim
that separately retained disaster-recovery media was rewritten in place.

Acceptance requires fixed-response route tests, worker retry/lease/idempotency
tests, two-owner RLS and broker tests on disposable PostgreSQL, synthetic R2
deletion tests, role/grant static checks, P0 and compile checks, shell and
PowerShell parsing, one Alembic head at `20260814_0009`, build
`20260813.19`, no protected-file changes, and confirmation that the erasure
worker, shared cutover, live services, credentials, and external stores remain
untouched.
