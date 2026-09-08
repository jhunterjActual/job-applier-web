# Stage 3: Durable PostgreSQL Search Runs

Historical migration instructions below do not apply to admission/0014 candidates.
Use the protected coordinator in `BETA_ADMISSION_OPERATIONS.md`, not the retired
standalone migration/grant wrappers or credential-exporting commands.

This bounded slice replaces the web process's `IS_SEARCHING` and
`LAST_SEARCH_RESULT` memory with durable, owner-scoped search-run state. It
adds cooperative cancellation, bounded retry leases, crash recovery, and a
worker-only cross-owner claim broker. Local mode keeps the same background
search experience through SQLite. This document began as pre-cutover
infrastructure; staging now runs on the authoritative hosted repositories.

## Current hosted-authority boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` is true and staging uses PostgreSQL plus
private object storage as its authoritative data plane. The search worker still
requires its explicit protected-environment enablement and shared-host startup
continues to fail closed on inconsistent authority configuration. A search
progress release must not repeat the permanent SQLite import, alter authority
records, broaden public access, or run backup/restore work as deployment
cleanup.

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

The worker also has no direct `app_users` privilege. The token-gated
`lock_search_run_claim(uuid, bigint, uuid)` definer is the only path used by
heartbeat, progress, and completion to lock the matching live claim and learn
whether its owner has been disabled. It requires the worker login, an exact
transaction-local owner context, and a matching unexpired lease token, and it
returns only the run status plus a disabled-owner boolean. Runtime and erasure
roles cannot execute it.

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

`GET /api/jobs/status` exposes only the active phase, bounded completed/total
counters, worker-claim count, UTC start time, latest productive-progress time, and
latest lease-heartbeat time; it never returns the operation ID, request terms,
location, owner, worker, or lease. Productive progress is deliberately distinct
from worker liveness, so a fresh check-in no longer makes stalled provider work
look productive. The browser recreates its progress modal and poller from that
owner-scoped status after a reload, shows elapsed time and the ages of both
signals in fixed rows with tabular timers, and keeps Stop available. The normal
operation-ID cancellation route remains available to the tab that submitted
the run. `POST /api/jobs/search/cancel` addresses only the authenticated
owner's current active run, so a restarted browser can stop work without the
lost tab-local operation ID. It immediately closes preparing, queued, and
expired-lease work; a live running lease becomes `cancel_requested` until the
worker reaches its next safe checkpoint.

A claim receives a random lease token, bounded expiry, and incremented attempt.
Heartbeats renew only the matching owner/run/token. A dedicated lease heartbeat
runs while bounded provider calls are in progress, so a multi-step search does
not lose its lease merely because one browser or AI step spans the normal
checkpoint interval. It holds no connection between pulses and propagates
durable cancellation, lease loss, or worker stop state to the main execution
path. After a crash, the broker makes the expired row eligible; a new worker
atomically replaces the lease. The stale worker can no longer complete it. At
three attempts the broker moves the run to fixed-error `failed` instead of
retrying forever. Cancellation on an expired worker becomes `cancelled`, not a
fresh provider attempt.

Keyword generation has its own 45-second provider timeout and disables Gemini
thinking for that small extraction task. Other structured AI calls remain
bounded at 90 seconds. A failed match-scoring call retries the same already
inspected five-posting batch up to three total attempts, with cancellation-aware
5- and 15-second waits. The durable `retrying_match_analysis` phase distinguishes
those bounded provider retries from the separately labelled worker-claim count.
Only an allowlisted provider failure classification is logged or retained; raw
provider responses and exceptions remain excluded.

Discovered postings are inspected, scored, and saved in chunks of five instead
of retaining the entire search in memory until one final AI response.
Successfully committed chunks therefore remain useful if a later provider call
fails; the terminal result reports a privacy-safe partial success instead of
discarding the work. If no chunk was saved, the terminal result gives a bounded,
actionable provider classification while preserving source diagnostics. Worker
logs contain fixed event names and allowlisted error codes, never search terms,
locations, posting URLs, resumes, credentials, or provider payloads.

Source discovery still uses bounded public search-result HTML rather than a
supported Yahoo API contract. Yahoo is attempted first for each ATS-domain
query. The first Yahoo request failure opens a run-scoped circuit breaker, so
subsequent queries use Bing instead of repeating the same degraded source. Bing
also serves as the immediate fallback for the failed query. Result links are
accepted only after unwrapping known Yahoo or Bing redirect formats and passing
the existing ATS-domain, specific-posting, canonicalization, and per-provider
candidate limits. A posting that later redirects to a provider's company-wide
board is rejected as stale before matching rather than treating the board title
and contents as one position. Fallback logs contain only the source transition
and a fixed failure class.

Worker SIGTERM and deploy shutdown are infrastructure events, not user
cancellation. At the next safe checkpoint the worker exits claim execution
without writing success, failure, or cancellation and leaves the current lease
to expire. A replacement worker then reclaims it as the next bounded attempt.
Only the authenticated cancellation route records durable user intent.

Unexpected failures expose only `The job search could not be completed.` Queue
or status outages expose fixed 503 messages. Cleanup fails closed when durable
search state cannot be checked.

## Migration and rollback

Migration `20260813_0007` created `search_runs`, forced RLS and owner policies, the
private claim broker, synchronization trigger, locked-path definer functions,
and explicit grants/revokes. Migration `20260902_0011` adds the privacy-safe
progress, heartbeat, and last-attempt fields plus bounded database constraints;
its claim trigger initializes each retry without widening runtime or worker
privileges. Migration `20260903_0012` adds the worker-only claim-lock broker and
explicitly removes direct worker access to authentication rows. Migration
`20260903_0013` adds the durable `retrying_match_analysis` phase without new
columns, role grants, or data-plane access. These changes
do not modify existing authoritative business rows or migrate process memory,
because memory has no durable recovery value.

Disposable PostgreSQL verification must prove repeated upgrade reaches one
head, runtime cannot call the broker, worker cannot read the broker table,
cross-owner status is denied, concurrent workers do not duplicate a claim,
and expiry/cancellation/attempt exhaustion converge to safe terminal states.
For a hosted release, prepare the exact immutable commit first, run that
release's `migrate-postgres.sh` against the approved migration environment,
confirm the database is at the single head, and only then activate that exact
release.

The legacy `20260813_0007` downgrade drops the claim function, broker trigger
and function, broker table, indexes, and `search_runs`. The additive
`20260902_0011` downgrade drops only its progress trigger/function,
constraints, and six progress-related columns. The `20260903_0012` downgrade
drops only its claim-lock function and does not broaden worker privileges. The
`20260903_0013` downgrade first normalizes an active retry phase to
`analyzing_matches`, then restores the prior phase constraint.
All downgrade paths are for disposable rehearsal only. Because PostgreSQL is
authoritative, staging must never downgrade or resume writable SQLite; use a
reviewed forward correction.

## Explicit exclusions

The follow-up documented in `STAGE_3_POSTGRES_SCHEDULED_SEARCH_DISPATCH.md`
adds only bounded due saved-search publication into this existing run path.
The combined boundary still does not add prioritization, multiple general
queue types, per-provider rate-limit coordination, durable source
diagnostic retries beyond the run-scoped Yahoo-to-Bing failover, distributed
cancellation during a single blocking network
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
- [ ] Build strings agree at the candidate build, Alembic has one head at
      `20260903_0013`, and `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains true.

Final authority transition and remaining repository gates stay in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
