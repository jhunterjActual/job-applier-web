# Stage 3: PostgreSQL Scheduled Saved-Search Dispatch

This bounded slice connects due PostgreSQL saved searches to the existing
durable search-run worker. It publishes one owner-scoped queued run and advances
the corresponding reminder in the same transaction. It does not add a
general-purpose queue, a provider-rate scheduler, or hosted activation.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and
`CAREERTRELLIS_SEARCH_WORKER_ENABLED` remains false by default. Do not set
either control, apply the migration to a live target, import permanent data,
restart staging, or change data authority for this slice. Local SQLite reminder
listing and manual execution remain unchanged; there is no new standalone
background scheduler.

## Private due broker and owner boundary

Forced RLS deliberately exposes no tenant row before owner context is bound.
Migration `20260813_0008` therefore adds a private non-RLS broker containing
only `saved_search_id`, numeric `owner_user_id`, and `due_at`. Keywords,
locations, names, WorkOS IDs, credentials, provider data, and results never
enter that broker.

A locked-search-path `SECURITY DEFINER` trigger mirrors only enabled daily or
weekly reminders with a due timestamp. PUBLIC and the runtime/worker login
roles have no table access. The trigger function is not directly executable by
either application role. The worker alone can execute the bounded dispatcher;
the web runtime cannot.

For each due broker identity, the dispatcher:

1. verifies `SESSION_USER` is exactly `careertrellis_worker` and requires a
   batch limit from 1 through 100;
2. selects due identities with `FOR UPDATE SKIP LOCKED`;
3. binds transaction-local tenant context from that private identity;
4. locks and rechecks the authoritative forced-RLS saved-search row;
5. defers only the private broker identity for 30 seconds, without advancing
   the saved search, when any search run for that owner is active;
6. validates persisted keywords and location against the exact search-run
   character and byte bounds, quarantining only an invalid broker identity for
   five minutes without advancing the saved search;
7. inserts a normal `queued` search run; and
8. advances `last_run_at` and `next_alert_at` in the same transaction.

Deferral prevents a full bounded batch of active or invalid early identities
from starving later owners. The invalid-row quarantine contains no query text
or error detail. Editing either saved keyword or location refreshes the private
broker from the authoritative schedule, so a repaired due row need not wait for
the quarantine interval.

Concurrent delete or disable wins safely when it already owns the saved-search
row. The dispatcher skips that locked row, and the edit transaction removes or
refreshes the broker identity. This avoids holding the broker lock while
waiting in the opposite order for an authoritative edit.

## Atomic publication and recovery

`search_runs` records nullable `scheduled_search_id` and `scheduled_for` only
for scheduled occurrences. The owner, saved-search ID, and due timestamp have
a unique occurrence index. The operation UUID is deterministically derived
from that same identity. An ambiguous client result can therefore be retried
without publishing the occurrence twice.

Run insertion and schedule advancement commit or roll back together. An insert
failure leaves the reminder due. A concurrent manual run can win the existing
one-active-run constraint; scheduled dispatch then applies the same broker-only
30-second deferral and leaves the reminder due for a later worker iteration. A
completed occurrence advances daily or weekly from its authoritative due time,
so downtime does not silently discard occurrences.

The dispatch transaction closes before the worker calls the existing claim
broker. Claiming then closes before profile, secret, network, browser, or AI
work. Scheduled runs use the existing random lease tokens, heartbeats, bounded
three-attempt recovery, cancellation, stale-worker rejection, minimized result,
and owner scope.

Each enabled worker iteration dispatches first and then attempts a normal
claim. A fixed dispatch persistence failure logs only
`scheduled_search_dispatch_failed` with code `persistence_unavailable`; no
exception, SQL, URL, owner, query, or secret is logged, and existing queued
runs remain claimable. Successful logs contain only an event and bounded count.

## Migration and rollback

Migration `20260813_0008` advances the single head from `20260813_0007`. It
adds scheduled occurrence columns/check/index, the identity-only broker and
sync trigger, and the worker-only dispatch function. Existing eligible rows are
backfilled while RLS stays enabled: FORCE is relaxed only for the schema owner
inside the migration transaction and restored before commit.

The downgrade removes the dispatcher, trigger, broker, occurrence index/check,
and columns. It is destructive to scheduled occurrence identity and is for
disposable rehearsal only. After the first authoritative hosted write, use a
reviewed forward correction rather than downgrading or returning to writable
SQLite.

## Explicit exclusions

This slice does not add provider prioritization, multiple queue classes,
rate-limit coordination, arbitrary cron expressions, catch-up policy controls,
distributed interruption of a blocking provider call, object-store access,
web/WorkOS/migration/R2 credentials in the worker, account deletion, permanent
import, authority cutover, public access, Caddy changes, or additional users.

## Verification gates

- [ ] Unit tests prove bounded calls, transaction closure, fixed errors,
      dispatch-before-claim ordering, and privacy-safe failure continuation.
- [ ] Disposable PostgreSQL 18 tests prove worker-only execution, denied broker
reads, forced-RLS owner binding, two-worker occurrence uniqueness, progress
past more than one batch of active owners, invalid-row quarantine with later
owner progress, disable behavior, atomic rollback, and lease recovery.
- [ ] CI runs focused unit tests on Python 3.10/3.12 and the PostgreSQL tests on
      PostgreSQL 18; Alembic reports the single head `20260813_0008`.
- [ ] Build and launcher strings agree at `20260813.15`; P0, search-run,
      scheduled-dispatch, syntax, dependency, shell, diff, and security checks
      pass.
- [ ] Both activation controls remain false and no live database, service,
      credential, user data, or object store changed.

The final authority transition remains governed by
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
