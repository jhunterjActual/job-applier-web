# Stage 3: PostgreSQL Headquarters Cache

This bounded slice routes headquarters-cache persistence through the selected
local or shared repository while preserving the existing lookup policy used by
resume tailoring. Standalone local mode keeps its SQLite behavior. Explicitly
selected shared mode uses the existing PostgreSQL `headquarters_cache` table
under tenant isolation. This slice does **not** convert the surrounding
tailoring aggregate or activate the hosted transition.

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

## Converted operations and owner boundary

This slice converts only the two persistence operations behind the
headquarters lookup used by `POST /api/jobs/{job_id}/tailor`:

- load one cached result by its bounded cache key; and
- cache one eligible OpenStreetMap result for later requests.

Shared-host ownership comes only from the authenticated server context. A
request body, path parameter, company name, job location, posting URL, maps
response, or AI response cannot supply an owner ID. The PostgreSQL repository
sets trusted transaction-local tenant context, retains explicit
`owner_user_id` predicates, and remains protected by forced RLS. Missing owner
context fails closed instead of exposing or updating an unscoped cache row.

The cache key remains a SHA-256 fingerprint of bounded context: normalized maps
provider, normalized company, U.S.-preferred or global scope, normalized job
location, and posting hostname. The database stores that key rather than the
raw company, job location, or complete posting URL. The internal cache result
contains only provider, address, country code, and attribution. It omits owner
and WorkOS IDs, credentials, job or application content, resume content, object
keys, and local paths.

## Transaction and provider-call boundary

The lookup sequence has three deliberately separate phases:

1. One short owner-scoped cache-read transaction loads an exact key match and
   closes its transaction and connection.
2. On a miss, OpenStreetMap, Google Places, or the existing AI fallback runs
   without a database connection, transaction, row lock, or advisory lock.
3. Only an eligible OpenStreetMap result is written afterward through a new
   short owner-scoped transaction. The write is an owner/key upsert and never
   spans another provider call.

A cache read or write must use only the explicitly selected repository. A
shared-mode failure must never fall back to SQLite. Cache failure must not
expose raw exception text, database URLs, SQL, credentials, complete posting
URLs, or another tenant's data in browser responses or logs.

## Storage eligibility and cache semantics

The cache is an optimization, not the authority for an application or a
generated material. Existing provider-policy constraints remain unchanged:

- cache only results whose selected provider and result source are both
  `openstreetmap`;
- do not cache `Unknown` or otherwise empty/unusable results;
- do not cache Google Places content, including a result obtained while Google
  is the selected provider;
- do not cache an AI-assisted fallback; and
- retain the provider attribution and country code required to display the
  cached OpenStreetMap result correctly.

The PostgreSQL `(owner_user_id, cache_key)` uniqueness constraint makes a
repeated eligible write an owner-scoped replacement. Two owners may hold the
same cache-key fingerprint without sharing, overwriting, or observing one
another's result.

## Migration, data authority, and rollback

This slice adds no migration. It reuses the existing `headquarters_cache`
table, `(owner_user_id, cache_key)` uniqueness constraint, owner index,
forced-RLS policy, and SQLite-import mapping. Alembic must continue to report
exactly one head at `20260813_0006`.

No data is copied and authority does not change. After the eventual first
authoritative PostgreSQL/R2 write, a code-only rollback to writable SQLite is
forbidden because it would split data authority. Keep PostgreSQL/R2
authoritative and use a reviewed forward correction. Alembic downgrade is not
a staging rollback mechanism.

## Explicit exclusions

This slice does not convert or authorize:

- the surrounding tailoring job/profile/base-resume reads, application
  existence check, generated-material write, or job-status write;
- maps-provider selection, headquarters resolution, provider request policy,
  AI fallback, prompts, response interpretation, or provider credentials;
- caching of Google Places content, AI-assisted results, unknown results,
  credentials, raw provider payloads, full job URLs, resumes, or postings;
- tailored-material read, edit, download, manual-application, or local
  filesystem paths;
- generated-material creation, replacement, deletion, encrypted-object
  integration, stored-object mutation, R2 tombstones, retry, reconciliation,
  or orphan cleanup;
- deletion of jobs with protected dependent records, user export, account
  deletion, or hosted backup/restore;
- durable saved-search claims, process-global search status/results, worker
  queues, retries, leases, scheduling, or background-worker execution; or
- deployment activation, permanent import, authority transition, R2 changes,
  public signup, additional users, Caddy changes, environment changes, or
  secret rotation.

The tailoring route therefore remains outside the complete shared-host
repository boundary even though this one cache dependency is converted. The
current worker remains an idle scaffold and receives no database credential,
provider credential, web keyring, or R2 credential.

## Verification gates

- [ ] Temporary SQLite tests prove exact-key read/upsert parity, input bounds,
      connection closure, OpenStreetMap eligibility, and Google/AI/unknown
      non-caching.
- [ ] Dispatch tests prove the cache uses the explicitly selected repository
      and shared mode cannot call a SQLite connection factory.
- [ ] Ordering tests prove the cache-read transaction closes before maps or AI
      work and the eligible cache write starts only after provider work ends.
- [ ] Failure tests prove cache errors never expose raw exceptions, database
      URLs, SQL, credentials, full posting URLs, or tenant data and never cause
      a shared-to-SQLite fallback.
- [ ] Disposable PostgreSQL 18 tests prove missing-context denial, two-owner
      read/write isolation, owner-local replacement, and forced-RLS denial when
      tenant context is absent.
- [ ] The standalone P0 suite, syntax compilation, dependency lock check, and
      PowerShell and batch-launcher parser checks pass.
- [ ] Alembic reports exactly one head at `20260813_0006`; this slice adds no
      schema, index, grant, or RLS-policy change.
- [ ] The exact diff receives a clean security review and dependency scan.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live staging
      state changes.

Final cutover sequencing, data-authority rules, and the remaining operator
gates remain in `STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
