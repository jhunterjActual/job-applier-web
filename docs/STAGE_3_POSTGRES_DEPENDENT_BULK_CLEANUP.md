# Stage 3: PostgreSQL Dependent Bulk Cleanup

This bounded slice adds one explicit shared-host cleanup action for jobs that
the existing untouched deletion deliberately protects. Standalone SQLite keeps
its original preview, archive, restore, untouched-delete, and protected-job
refusal semantics. The ordinary `delete` action is not widened.

This is a **hard no-activation** change. Build strings agree at
`20260813.17`, Alembic remains at the single `20260813_0008` head, and
`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not select PostgreSQL on
live staging, import the permanent workspace, activate a release, restart
services, or mutate live PostgreSQL or private-object storage for this slice.

## Explicit action and bounded preview

PostgreSQL cleanup previews include `delete_with_dependents` with
`available: true`. It selects only owner-visible jobs whose status is `matched`
or `archived` from `matched` and that have at least one application,
application-status-history row, or engagement. Active lifecycle jobs outside
that cleanup status set are not silently included. Local previews return the
same named action with `available: false` and count zero; a forged local request
is rejected before opening SQLite.

The browser renders the action only when the server marks it available. It
shows a separate count and requires confirmations that explicitly name saved
application work and permanent deletion. Existing untouched buttons and
messages retain their prior behavior.

The preview token is owner- and action-bound and covers the exact bounded set
of job IDs and states, privacy-minimized URL fingerprints, application IDs,
lifecycle-history IDs, engagement IDs, application object-reference pairs,
target object IDs, and same-owner survivor references. None of those internal
identifiers are returned separately to the browser. Any changed surface makes
the token stale before mutation.

At most 500 jobs and 5,000 aggregate application, history, engagement, target
object, and survivor-reference items may enter one transaction. Both limits are
checked before catalog or row locks. An oversized or stale request fails with
the existing fixed cleanup response.

## Transaction and object safety

One owner-scoped PostgreSQL transaction:

1. takes the owner job-catalog advisory lock;
2. locks every previewed job in ID order;
3. locks their applications in job/ID order;
4. locks same-owner surviving applications that reference any candidate object;
5. locks all referenced `stored_objects` rows in ID order;
6. recomputes and validates the complete preview graph;
7. upserts one privacy-minimized suppression per job;
8. tombstones each object exactly once only when no application outside the
   selected batch references it; and
9. deletes the jobs, allowing existing composite owner foreign keys to cascade
   applications, lifecycle history, and engagements.

Suppression, exclusive tombstones, and dependent deletion commit or roll back
together. An object shared only by two selected applications is tombstoned once;
an object referenced by a surviving same-owner application remains active. No
object backend, provider, browser, AI, PDF, or filesystem call occurs inside
the transaction.

After commit only, the repository invokes the ordinary owner-scoped generated-
material reconciler once when it created tombstones. The existing 30-second
grace period remains intact. Construction, configuration, or backend failures
are swallowed, leaving durable tombstones for a later retry without changing a
successful response. No object key, owner ID, URL, fingerprint, database detail,
path, credential, or raw exception is returned or logged.

## Explicit exclusions and gates

This slice does not add account erasure, direct application deletion, a new
queue or worker, a schema/grant/RLS change, bucket enumeration or orphan
adoption, in-app shared backup/restore, permanent import, authority transition,
public signup, or additional users.

Verification requires temporary SQLite parity and route tests; database-free
lock, cap, token, rollback, and post-commit ordering tests; disposable
PostgreSQL 18 owner-isolation, shared-reference, target-target sharing,
dependency-change, cascade, rollback, and concurrency tests; the exact P0,
syntax, dependency, launcher, diff, and protected-file gates; one Alembic head
at `20260813_0008`; and a loopback synthetic runtime smoke. No live service,
database, object store, provider, secret, or user workspace may be used.
