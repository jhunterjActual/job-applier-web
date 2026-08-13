# Stage 3: PostgreSQL Engagement and Interview-Prep Operations

This slice adds one dual-mode repository boundary for owner-scoped application
engagement tracking and the interview-preparation workspace. Standalone local
use retains SQLite. Shared-host operations use PostgreSQL transactions and
forced RLS, but the overall repository transition remains incomplete and this
release must not be activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to live staging, migrate or import the live
database, change data authority, activate a release, restart services, or
change R2, Caddy, users, environment files, or secret files for this slice.

SQLite and generated files remain authoritative. PostgreSQL and R2 rehearsal
targets remain disposable until the final repository, no-omission import,
reconciliation, recovery, and authorization gates pass. This slice has no
manual infrastructure prerequisite and authorizes no live operator action.

## Converted operations and transaction boundaries

The application-operations repository owns only:

- list, create, update, and delete for the authenticated owner's bounded
  recruiter, hiring-manager, referral, networking, and assessment records;
- read of the authenticated owner's saved interview preparation or the local
  starter context;
- atomic persistence of user-reviewed interview preparation; and
- owner-scoped context read and conflict-safe persistence around an explicitly
  requested AI-assisted interview-preparation draft.

Hosted ownership comes only from authenticated server context. Requests cannot
select an owner ID. Every PostgreSQL query runs with transaction-local tenant
context, retains an explicit `owner_user_id` predicate, and remains protected
by forced RLS. A missing job, application, or engagement and a foreign one use
the same non-disclosing not-found result. SQL values are parameterized.

Engagement mutations lock the authenticated owner's job before counting or
changing dependent rows. This serializes concurrent creates so the existing
limit of 250 engagement records per job cannot be exceeded. Updates and deletes
cannot move an engagement to another job or owner. PostgreSQL mutations use a
five-second lock timeout and a 30-second statement timeout. Existing field
limits remain in force: 160 characters for names and organizations, 320 for
contact details, 4,000 for engagement notes, and 40,000 for interview
preparation.

Interview-preparation generation uses a two-phase boundary. The repository
first reads a bounded owner-scoped context and returns a per-process HMAC
optimistic-concurrency token that binds the owner, job, application, and
relevant saved-context revision. The provider call runs with no database lock
or transaction held. After the provider returns, the repository locks and
revalidates the owner-scoped application and saves only when the token still
matches. A concurrent user edit, changed source context, wrong owner or job,
or process restart produces a bounded conflict and preserves the newer data.
The token is concurrency state, not authorization.

Only an explicit user request sends the already bounded job description,
match analysis, reviewed resume text, and application/interview notes to the
selected AI provider. Engagement names, organizations, contact details,
activity dates, next actions, and notes are never included in that provider
context. Responses and errors omit owner and WorkOS IDs, complete HMAC input,
object identifiers or keys, local paths, database URLs, credentials, raw
provider errors, and unrelated tenant data.

## Migration, data authority, and rollback

This slice adds no migration. The existing tenant foundation already contains
owner-scoped `applications` and `application_engagements`, forced RLS, the
unique owner/job application constraint, and
`idx_application_engagements_owner_job_updated`. Alembic must continue to
report exactly one head at `20260813_0006`.

No data is copied and authority does not change. Before final cutover, SQLite
and local generated files remain authoritative. After the eventual first
authoritative PostgreSQL/R2 write, a code-only rollback to writable SQLite is
forbidden because it would split data authority. Keep PostgreSQL/R2
authoritative and use a reviewed forward correction. Alembic downgrade is not
a staging rollback mechanism.

## Explicit exclusions

This slice does not convert or authorize:

- application operations outside the separately converted record-list,
  insight, lifecycle, engagement, and interview-preparation boundaries;
- lifecycle operations beyond the already converted lifecycle repository;
- tailoring, generated-material creation or editing, stored-object mutation,
  generated-material download integration, object deletion, R2 tombstones,
  retry, reconciliation, or orphan cleanup;
- deletion of an application, job, lifecycle history, stored-object row, or
  encrypted R2 object;
- engagement contact data in any AI-provider request;
- headquarters caching, user export, account deletion, or hosted
  backup/restore;
- durable saved-search claims, retries, leases, process-global search state,
  or background-worker execution; or
- deployment activation, permanent import, authority transition, R2 changes,
  public signup, or additional users.

Generated-material and cross-store deletion require a later design with object
tombstones, bounded retry, and reconciliation. The current worker remains an
idle scaffold and receives no web keyring or R2 credential in this slice.

## Verification gates

- [ ] Temporary SQLite tests prove engagement list/create/update/delete parity,
      validation and record limits, interview-prep starter/read/manual-save
      parity, rollback, cancellation, and preservation on generation conflict.
- [ ] Route-dispatch tests prove all converted routes use the configured
      repository and shared mode has no SQLite fallback.
- [ ] Alembic reports exactly one head at `20260813_0006`; this slice adds no
      schema, index, grant, or RLS-policy change.
- [ ] Disposable PostgreSQL 18 tests prove missing-context and cross-owner
      denial for every converted read and mutation, foreign engagement/job
      denial, and privacy-safe missing-row equivalence.
- [ ] Concurrent PostgreSQL tests prove the 250-record limit cannot be raced,
      updates cannot move records between jobs, and generated preparation
      cannot overwrite a concurrent manual edit or changed context.
- [ ] Provider-boundary tests prove no database transaction or row lock remains
      open during the external call, prior-process/wrong-owner/wrong-job/stale
      HMAC state cannot write, and no engagement contact data enters the prompt.
- [ ] Responses, exceptions, logs, and provider payloads satisfy the privacy
      exclusions above and generic storage/provider failures are bounded.
- [ ] Local tests, compilation, dependency and shell checks, exact-diff security
      review, required GitHub checks, and loopback build/dashboard/listener
      smoke pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live database,
      R2 object, service, release, environment, user, or access restriction
      changes.

The final authority, rollback, recovery, and cutover contract remains in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
