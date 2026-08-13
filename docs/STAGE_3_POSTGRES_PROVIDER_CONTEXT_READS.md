# Stage 3: PostgreSQL Provider-Context Reads

This slice routes four provider-dependent consumers through the existing
dual-mode profile and encrypted-provider-secret boundaries. Standalone local
use retains SQLite. Shared-host reads use owner-scoped PostgreSQL transactions
and forced RLS, but the overall repository transition remains incomplete and
this release must not be activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to live staging, migrate or import the live
database, change data authority, activate a release, restart services, or
change R2, Caddy, users, environment files, or secret files for this slice.

SQLite and generated files remain authoritative. PostgreSQL and R2 rehearsal
targets remain disposable until the final repository, no-omission import,
reconciliation, recovery, and authorization gates pass. This slice has no
manual infrastructure prerequisite and authorizes no live operator action.

## Converted consumers and ownership boundary

The provider-context slice converts only the profile and credential reads for:

- `POST /api/profile/ai-provider/validate`, which validates the authenticated
  owner's selected AI provider, model, credential, and structured-output
  capability;
- `POST /api/profile/maps-provider/validate`, which validates the
  authenticated owner's selected maps provider and credential;
- the explicitly consented AI OCR callback within
  `POST /api/profile/upload-resume`; and
- the optional initial match analysis after `POST /api/jobs/import` has saved
  the authenticated owner's reviewed posting through the existing job-catalog
  repository.

Requests cannot select an owner ID. Shared-host profile context comes from the
authenticated local `app_users.id` through the existing profile repository.
Its PostgreSQL query runs with transaction-local tenant context, an explicit
`owner_user_id` predicate, and forced RLS. The active base-resume join is
constrained to the same owner. A missing authenticated context exposes no
profile or resume row.

Provider credentials continue to come from the separately encrypted,
owner-scoped `provider_secrets` boundary. The selected Gemini, OpenAI, or
Google Maps credential is decrypted only for the authenticated owner. Global
environment credentials are not a shared-host fallback. Plaintext credentials
are not returned to the browser, persisted in profile rows, included in route
errors, or written to application logs.

Each profile read and credential lookup completes its short database
transaction before any AI, OCR, maps, or posting-match network request begins.
No database connection, row lock, or transaction remains open while waiting
for an external provider. Provider validation and OCR do not write database
state. Manual import preserves its existing two-phase behavior: the reviewed
job is saved first through the owner-scoped job-catalog repository, provider
analysis runs without a database transaction, and a successful bounded result
is committed later through a separate owner-scoped match-completion write.
Cancellation or provider failure preserves the saved job as unscored.

Only an explicit user action invokes these providers. Resume OCR sends only
the bounded image pages that lack readable embedded text after the user opts
in. Manual-import scoring sends the active reviewed resume and the reviewed
posting fields required for match analysis. Provider capability checks send
only the bounded inputs required by the selected provider. Responses and
errors omit owner and WorkOS IDs, credentials, complete resume or posting
content, database URLs, local paths, object identifiers or keys, and unrelated
tenant data.

## Migration, data authority, and rollback

This slice adds no migration. The existing `profiles`, `base_resumes`, and
`provider_secrets` tables, owner relationships, forced-RLS policies, and
repository methods already provide the required storage boundaries. Alembic
must continue to report exactly one head at `20260813_0006`.

No data is copied and authority does not change. Before final cutover, SQLite
and local generated files remain authoritative. After the eventual first
authoritative PostgreSQL/R2 write, a code-only rollback to writable SQLite is
forbidden because it would split data authority. Keep PostgreSQL/R2
authoritative and use a reviewed forward correction. Alembic downgrade is not
a staging rollback mechanism.

## Explicit exclusions

This slice does not convert or authorize:

- search execution's remaining profile keyword-cache write, job/suppression
  reads, process-global status, durable claims, retries, leases, or background
  worker execution;
- tailoring, generated-material creation or editing, encrypted-object download
  integration, stored-object mutation, deletion, R2 tombstones, retry,
  reconciliation, or orphan cleanup;
- headquarters caching or provider-backed headquarters resolution during
  tailoring;
- application operations beyond their separately converted lifecycle,
  engagement, interview-preparation, record-list, and insight boundaries;
- user export, account deletion, hosted backup/restore, or shared-host
  readiness changes;
- replacement of the standalone SQLite secret-column behavior used only in
  local mode; or
- deployment activation, permanent import, authority transition, R2 changes,
  public signup, or additional users.

The existing interview-preparation provider boundary remains owned by the
application-operations slice. This provider-context slice neither expands its
prompt nor changes its optimistic-concurrency write contract. The current
worker remains an idle scaffold and receives no web keyring, provider
credential, or R2 credential.

The later search-execution context slice reuses the authenticated owner's
profile, active-resume, and encrypted-provider-secret boundaries and adds the
conditional suggested-keyword cache operation. Its separate transaction and
catalog-snapshot contract is documented in
`STAGE_3_POSTGRES_SEARCH_EXECUTION_CONTEXT.md`; the exclusions above remain the
historical boundary of this provider-context slice.

## Verification gates

- [ ] Temporary SQLite tests prove provider/model/maps selection, opt-in OCR,
      manual-import scoring, cancellation, provider-failure preservation, and
      no-key unscored parity.
- [ ] Route-dispatch tests prove all four consumers use the configured profile
      repository and shared mode has no SQLite fallback.
- [ ] Alembic reports exactly one head at `20260813_0006`; this slice adds no
      schema, index, grant, or RLS-policy change.
- [ ] Disposable PostgreSQL 18 tests prove missing-context denial and two-owner
      isolation for profiles, active resumes, provider selection, and encrypted
      credentials.
- [ ] Provider-boundary tests prove every database transaction closes before
      external work begins, each provider receives only the authenticated
      owner's bounded context, and global credentials cannot become a hosted
      fallback.
- [ ] Responses, exceptions, logs, and provider payloads omit internal owner or
      auth identifiers, credentials, unrelated tenant data, local paths,
      object metadata, and content outside the documented request boundary.
- [ ] Local tests, compilation, dependency and shell checks, exact-diff security
      review, required GitHub checks, and loopback build/dashboard/listener
      smoke pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live database,
      R2 object, service, release, environment, user, or access restriction
      changes.

The final authority, rollback, recovery, and cutover contract remains in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
