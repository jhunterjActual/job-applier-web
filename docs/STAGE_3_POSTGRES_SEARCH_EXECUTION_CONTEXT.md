# Stage 3: PostgreSQL Search-Execution Persistence Context

This bounded slice removes the remaining direct-SQLite profile and job-catalog
access from the interactive job-search execution path. Standalone local mode
keeps its existing SQLite behavior, while explicitly selected shared mode uses
the existing PostgreSQL profile and discovery repositories. This is repository
work only: it does **not** complete or activate the hosted transition.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to the live staging environment, import the
permanent SQLite source, change the repository authority marker, or activate
this release on the web Droplet. A shared-host process selecting PostgreSQL
must continue to refuse startup until every remaining data domain and the final
cutover controls are complete.

No live database, R2 object, service, release symlink, environment file, user,
or Caddy access restriction is changed by this slice. PostgreSQL and R2 remain
disposable rehearsal targets; SQLite and local generated files remain
authoritative before the final cutover.

## Converted path and owner boundary

This slice is limited to the persistence used by `POST /api/jobs/search` and
`run_job_search_and_matching`:

- the current profile and active base-resume text are loaded through the
  configured profile repository;
- AI-suggested keywords are conditionally cached through
  `cache_suggested_keywords_if_empty`;
- canonical known-job statuses and deletion-suppression fingerprints are read
  once through `search_catalog_context`; and
- accepted matches continue to use the owner-scoped
  `create_job_if_available` job-catalog write.

At completion of this historical slice, the authenticated local
`app_users.id` was captured before the FastAPI background task and installed
in trusted server-side owner context. The follow-up durable execution design
is documented in `STAGE_3_POSTGRES_SEARCH_RUNS.md`; request bodies, query
parameters, job-board responses, and AI output still cannot choose an owner ID.

If the request creates or advances a saved search, that separately converted
owner-scoped repository operation completes before the background task is
scheduled. It does not share a transaction with the search loop.

In PostgreSQL mode, both repositories bind that owner explicitly in their SQL
and run under the existing transaction-local tenant context with forced RLS.
A missing owner context fails closed instead of exposing an unscoped profile,
resume, job, or suppression row. Provider credentials remain in the separate
encrypted, owner-scoped secret repository; no global environment credential is
a shared-host fallback.

## Short transactions before network and AI work

Search execution deliberately alternates bounded repository work with
connection-free external work:

1. One short profile read loads the existing profile projection, including the
   active-resume, provider selection, and search settings. In shared mode, the
   ensuing encrypted-secret lookup uses its own short owner transaction. Both
   transactions and connections close before keyword extraction begins.
2. If user-entered and cached keywords are both empty, keyword extraction runs
   without any database connection or transaction. A separate short write then
   caches at most 500 normalized characters only if the stored cache is still
   empty, so a concurrent value is not overwritten.
3. After job-board discovery, one short catalog read returns the minimized
   known-job and suppression snapshot, then closes before browser scraping,
   provider lookups, or batch AI matching begins.
4. Each accepted match is committed independently through the existing atomic
   job-catalog write. No connection, transaction, advisory lock, or row lock is
   held between candidates or across a network, browser, or AI call.

Cancellation and provider failure therefore keep jobs already committed before
the stopping point. They cannot leave one long-lived search transaction or roll
back unrelated accepted jobs.

## Minimized snapshot and cache semantics

`search_catalog_context` is an internal execution snapshot, not an API
response. It contains only:

- a mapping from canonical job URL to status for the current owner's known
  jobs; and
- the current owner's set of one-way deletion-suppression URL fingerprints.

It omits job IDs, titles, companies, descriptions, match analyses, application
data, owner identifiers, suppression metadata, credentials, object keys, and
local paths. The snapshot is intentionally point-in-time. The final
`create_job_if_available` transaction rechecks owner-scoped canonical identity
and suppression under the existing catalog lock, so stale snapshot data cannot
bypass duplicate or deletion-suppression enforcement.

The suggested-keyword cache stores only the normalized comma-separated keyword
string already used by search execution. Empty writes are ignored, values over
500 characters are rejected, and the conditional update never replaces an
existing nonblank owner value. It does not store prompts, provider responses,
resume text, job content, or credentials.

## Fixed errors and privacy

Unexpected background-search failures expose only the fixed result text
`The job search could not be completed.` A per-job persistence failure writes
only the fixed operational log message
`A discovered job could not be saved; the search remains available.` Raw
exception text, database URLs, SQL, owner or WorkOS IDs, credentials, resume or
posting content, URLs, suppression fingerprints, and cross-tenant data are not
placed in the status response or that log message.

Existing provider-health output remains governed by its allowlist. This slice
does not broaden prompts, provider payloads, analytics payloads, diagnostic
history, or browser-visible repository responses.

## Migration, data authority, and rollback

This slice adds no migration. It reuses the existing `profiles`,
`base_resumes`, `jobs`, and `job_suppressions` tables, their owner
relationships, forced-RLS policies, and repository transaction helpers.
This slice itself added no migration and originally retained head
`20260813_0006`. The later durable search-run slice advances the current head
to `20260813_0007`.

No data is copied and authority does not change. After the eventual first
authoritative PostgreSQL/R2 write, a code-only rollback to writable SQLite is
forbidden because it would split data authority. Keep PostgreSQL/R2
authoritative and use a reviewed forward correction. Alembic downgrade is not
a staging rollback mechanism.

## Explicit exclusions

This slice does not convert or authorize:

- the then-process-global search state and worker execution, which are now
  owned by the bounded follow-up `STAGE_3_POSTGRES_SEARCH_RUNS.md`;
- scheduled saved-search dispatch and general-purpose worker queues;
- any web runtime database, migration, object-storage, or R2 credential for
  the worker;
- provider crawling, browser automation, scraping, keyword-selection,
  headquarters-resolution, scoring, or prompt changes;
- application operations beyond their separately converted repository
  boundaries;
- tailoring, generated-material creation or editing, encrypted-object download
  integration, stored-object mutation, R2 tombstones, retry, reconciliation, or
  orphan cleanup;
- headquarters caching, user export, account deletion, or hosted
  backup/restore; or
- deployment activation, permanent import, authority transition, R2 changes,
  public signup, additional users, Caddy changes, environment changes, or
  secret rotation.

The saved-search create and schedule-advancement writes remain owned by
`STAGE_3_POSTGRES_SAVED_SEARCH_WRITES.md`; this slice does not turn them into a
durable worker. Job creation remains owned by
`STAGE_3_POSTGRES_JOB_CATALOG_WRITES.md`; this slice only supplies its bounded
search-execution inputs.

## Verification gates

- [ ] Standalone search tests prove local SQLite compatibility, conditional
      keyword caching, minimized catalog reads, cancellation, and partial
      committed-job behavior.
- [ ] Route and context tests prove `POST /api/jobs/search` propagates the
      authenticated owner into the background task and resets it on success,
      cancellation, and unexpected failure.
- [ ] Repository tests prove every external provider, browser, and AI call runs
      after the preceding transaction and connection have closed.
- [ ] Two-tenant PostgreSQL tests prove profile/cache, known-job, suppression,
      and accepted-job operations cannot read or mutate another tenant's rows.
- [ ] Conditional-write tests prove keyword caching does not overwrite a
      nonblank value, and catalog-creation tests prove duplicate and
      suppression state is rechecked after the snapshot.
- [ ] Failure tests prove browser-visible status and logs use fixed messages
      without raw exceptions, URLs, resume/posting content, credentials, owner
      IDs, database URLs, SQL, or suppression fingerprints.
- [ ] The standalone P0 suite, syntax compilation, dependency lock check, and
      PowerShell and batch-launcher parser checks pass.
- [ ] The historical slice reported head `20260813_0006`; the current durable
      follow-up reports `20260813_0007`, and the disposable PostgreSQL 18 RLS
      suite and shared-mode search smoke test pass.
- [ ] The exact diff receives a clean security review and dependency scan.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live staging
      state changes.

Final cutover sequencing, data-authority rules, and the remaining operator
gates remain in `STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
