# Stage 3: Durable PostgreSQL Search Runs

This bounded slice replaces the web process's `IS_SEARCHING` and
`LAST_SEARCH_RESULT` memory with durable, owner-scoped search-run state. It
adds cooperative cancellation, bounded retry leases, crash recovery, and a
worker-only cross-owner claim broker. Local mode keeps the same background
search experience through SQLite. This remains repository infrastructure, not
a hosted activation.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and
`CAREERTRELLIS_SEARCH_WORKER_ENABLED` defaults to false. Shared-host startup
continues to fail closed before final repository cutover, even if the worker
switch is accidentally enabled. Do not set either activation control, import
the permanent SQLite source, change data authority, restart staging for this
slice, or enable public access.

The worker unit loads a dedicated non-web runtime environment, its dedicated
`careertrellis_worker` PostgreSQL credential, and the existing encrypted
provider-secret keyring. It does not receive the common staging environment,
WorkOS or cookie secrets, proxy token, web runtime database
credential, migration credential, object-storage/R2 credential, or object
keyring. Provider keys remain encrypted per owner in PostgreSQL and are loaded
only after the claimed owner is bound in trusted server context.
The worker runs as the separate `careertrellis-worker` Unix account with its
own mode-0700 data directory. It can read the reviewed release and shared
Playwright installation through the non-secret `careertrellis` group, but it
cannot read the web process environment or the web-owned data directory.

## Owner and transaction boundary

`POST /api/jobs/search` first reserves one owner-scoped `preparing` run. The
partial unique index permits only one `preparing`, `queued`, `running`, or
`cancel_requested` run per owner. The claim broker ignores `preparing` rows.
After saved-search bookkeeping succeeds, one short owner transaction updates
the normalized request and publishes the run as `queued`; any preparation
failure deletes only that still-unpublished row. Local mode schedules an
in-process task only after publication; selected shared mode leaves the durable
run for the worker.

Status, cleanup gating, and cancellation derive ownership only from the
authenticated request context. They never accept an owner ID from a header,
path, query, or body. PostgreSQL operations bind transaction-local tenant
context and explicitly repeat the owner predicate under forced RLS. SQLite
retains owner columns for parity and synthetic isolation tests.

A claim transaction closes before profile reads, provider-secret decryption,
browser work, network requests, or AI calls begin. Each durable heartbeat,
cancellation check, result transition, profile/catalog access, diagnostic
write, and accepted-job write uses its own bounded transaction. No database
connection, row lock, or queue lock is retained over external work.

## Least-privilege cross-owner claiming

Neither runtime role has `BYPASSRLS`; both login roles remain non-superuser and
`NOBYPASSRLS`. The non-RLS `search_run_claim_queue` broker contains only an
opaque run UUID, numeric owner ID, and availability timestamp. Runtime and
worker roles have no table privileges on it. A locked-search-path
`SECURITY DEFINER` trigger mirrors only active scheduling metadata.

The `claim_next_search_run(uuid, integer)` definer function:

1. rejects every `SESSION_USER` except `careertrellis_worker`;
2. validates a 5–900 second lease and opaque per-process worker UUID;
3. selects one eligible broker row with `FOR UPDATE SKIP LOCKED`;
4. binds that row's owner with transaction-local `careertrellis.user_id`;
5. locks and updates only the corresponding forced-RLS search row; and
6. returns the request only after an atomic lease-token and attempt increment.

The web runtime cannot execute the claim function. The worker cannot inspect
the broker directly. Two workers can claim different runs concurrently, while
the row lock and status update prevent duplicate claims of one run.

## Bounded state, cancellation, and recovery

Requests retain at most 500 keyword characters and 240 location characters.
Starting a new run deletes that owner's previous terminal run in the same
transaction, so operational query/result history does not accumulate.
The browser-visible result is capped at 16 KiB and reduced to success, bounded
`jobs_added`, cancellation state, or a fixed error. Provider payloads, alerts,
URLs, prompts, posting/resume content, credentials, WorkOS IDs, raw exceptions,
SQL, database URLs, worker IDs, and lease tokens are not stored in that result
or returned by the status route.

Statuses are `preparing`, `queued`, `running`, `cancel_requested`, `succeeded`,
`failed`, and `cancelled`. Preparing or queued cancellation is immediately terminal. Running
cancellation becomes durable and is observed at the next existing safe search
checkpoint. Jobs committed before that checkpoint remain committed.

`GET /api/jobs/status` exposes only the active phase plus UTC start and
last-activity timestamps; it never returns the operation ID, request terms,
location, owner, worker, or lease. The browser recreates its progress modal and
poller from that owner-scoped status after a reload, shows elapsed time and the
age of the latest durable heartbeat, and keeps Stop available. The normal
operation-ID cancellation route remains available to the tab that submitted
the run. `POST /api/jobs/search/cancel` addresses only the authenticated
owner's current active run, so a restarted browser can stop work without the
lost tab-local operation ID. It immediately closes preparing, queued, and
expired-lease work; a live running lease becomes `cancel_requested` until the
worker reaches its next safe checkpoint.

A claim receives a random lease token, bounded expiry, and incremented attempt.
Heartbeats renew only the matching owner/run/token. After a crash, the broker
makes the expired row eligible; a new worker atomically replaces the lease.
The stale worker can no longer complete it. At three attempts the broker moves
the run to fixed-error `failed` instead of retrying forever. Cancellation on an
expired worker becomes `cancelled`, not a fresh provider attempt.

Worker SIGTERM and deploy shutdown are infrastructure events, not user
cancellation. At the next safe checkpoint the worker exits claim execution
without writing success, failure, or cancellation and leaves the current lease
to expire. A replacement worker then reclaims it as the next bounded attempt.
Only the authenticated cancellation route records durable user intent.

Unexpected failures expose only `The job search could not be completed.` Queue
or status outages expose fixed 503 messages. Cleanup fails closed when durable
search state cannot be checked.

## Migration and rollback

Migration `20260813_0007` advances the single Alembic head from
`20260813_0006`. It creates `search_runs`, forced RLS and owner policies, the
private claim broker, synchronization trigger, locked-path definer functions,
and explicit grants/revokes. It changes no existing authoritative rows and
does not migrate process memory, because memory has no durable recovery value.

Disposable PostgreSQL verification must prove repeated upgrade reaches one
head, runtime cannot call the broker, worker cannot read the broker table,
cross-owner status is denied, concurrent workers do not duplicate a claim,
and expiry/cancellation/attempt exhaustion converge to safe terminal states.
No migration was run against a live or unapproved target by this change.

The downgrade drops the claim function first, then its trigger/function,
broker table, indexes, and `search_runs`. It is destructive to queued/history
state and is for disposable rehearsal only. Once PostgreSQL accepts an
authoritative hosted write, never downgrade or resume writable SQLite; keep
PostgreSQL authoritative and use a reviewed forward correction.

## Explicit exclusions

The follow-up documented in `STAGE_3_POSTGRES_SCHEDULED_SEARCH_DISPATCH.md`
adds only bounded due saved-search publication into this existing run path.
The combined boundary still does not add prioritization, multiple general
queue types, per-provider rate-limit coordination, durable source
diagnostic retries, distributed cancellation during a single blocking network
call, job-write transactions spanning a search, object-storage access, web or
R2 credentials in workers, account deletion/export conversion, backup/restore
activation, permanent import, authority cutover, Caddy changes, public signup,
or additional users.

## Verification gates

- [ ] Local tests prove owner isolation, one-active-run enforcement, queued and
      running cancellation, minimized results, stale-lease rejection, crash
      retry, and two-worker single-claim behavior.
- [ ] Route tests prove a preparing reservation precedes saved-search writes,
      publication precedes background scheduling, shared mode
      does not use an in-process worker, status/cancel/cleanup use durable
      owner-scoped state, and outages fail closed with fixed errors.
- [ ] Execution tests prove owner context is set only for one claim, reset on
      every outcome, and no queue transaction remains open across providers.
- [ ] Disposable PostgreSQL 18 tests prove forced RLS, worker-only broker
      execution, denied direct broker reads, `SKIP LOCKED` concurrency,
      lease recovery, attempt exhaustion, and downgrade/upgrade to one head.
- [ ] Worker configuration proves the default is disabled, the cutover guard
      still blocks activation, provider secrets remain owner-scoped, and no
      web runtime, migration, object-storage, or R2 credential is added.
- [ ] P0, focused search-run, syntax, dependency-lock, migration, launcher,
      protected-file, and exact-diff security checks pass.
- [ ] Build strings agree at `20260813.15`, Alembic has one head at
      `20260813_0008`, and `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false.

Final authority transition and remaining repository gates stay in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
