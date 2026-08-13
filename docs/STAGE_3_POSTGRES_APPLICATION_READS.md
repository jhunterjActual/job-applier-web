# Stage 3: PostgreSQL Application Records and Outcome Insights

This slice adds one dual-mode read repository for the Application Logs record
list and the application-outcome insight summary. Standalone local use retains
SQLite. Shared-host reads use PostgreSQL transactions and forced RLS, but the
overall repository transition remains incomplete and this release must not be
activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to live staging, migrate or import the live
database, change data authority, activate a release, restart services, or
change R2, Caddy, users, environment files, or secret files for this slice.

SQLite and generated files remain authoritative. PostgreSQL and R2 rehearsal
targets remain disposable until the final repository, no-omission import,
reconciliation, recovery, and authorization gates pass. This slice has no
manual infrastructure prerequisite and authorizes no live operator action.

## Converted reads and privacy boundary

The application-read repository owns only:

- the authenticated owner's `/api/applications` record list, including the
  owner-scoped engagement count for each application; and
- the authenticated owner's `/api/application-insights` aggregation across
  current application state and active lifecycle history.

Hosted ownership comes only from authenticated server context. Requests cannot
select an owner ID. Every PostgreSQL query runs with transaction-local tenant
context, retains explicit owner predicates on applications and dependent joins,
and remains protected by forced RLS. SQL values are parameterized.

The record response uses an explicit projection. It omits owner and WorkOS IDs,
stored-object IDs and keys, local generated-file paths, tailored-resume source,
cover-letter content, interview-preparation content, submission evidence, and
free-form application notes. It returns bounded lifecycle/display metadata
plus boolean availability fields and a numeric engagement count. Standalone
download availability remains derived from usable local artifacts. Hosted
download availability remains false until
the separately reviewed encrypted-object download integration exists, so the UI
does not offer an action that the selected data store cannot complete.

Insight computation remains private and in-process. Two owner-scoped result
sets provide confirmed applications and their active lifecycle history; no job,
application, resume, or outcome data is sent to an analytics or AI provider.
Response and positive-response definitions retain existing behavior, response
timing ignores undone history and dates before application, and each displayed
dimension remains capped at 50 groups.

Repository or storage failures return fixed generic route errors. Responses,
exceptions, and logs omit database URLs, credentials, local paths, object
identifiers, generated content, and unrelated tenant data.

## Migration, data authority, and rollback

This slice adds no migration. Existing owner indexes, the active lifecycle
history index, forced RLS policies, and application/job ownership constraints
support the converted reads. Alembic must continue to report exactly one head
at `20260813_0006`.

No data is copied and authority does not change. Before final cutover, SQLite
and local generated files remain authoritative. After the eventual first
authoritative PostgreSQL/R2 write, a code-only rollback to writable SQLite is
forbidden because it would split data authority. Keep PostgreSQL/R2
authoritative and use a reviewed forward correction. Alembic downgrade is not
a staging rollback mechanism.

## Explicit exclusions

This slice does not convert or authorize:

- tailoring, generated-material creation or editing, encrypted-object download
  integration, stored-object mutation, deletion, R2 tombstones, retry,
  reconciliation, or orphan cleanup;
- deletion of an application, job, lifecycle history, stored-object row, or
  encrypted R2 object;
- application operations outside the converted lifecycle, engagement,
  interview-preparation, record-list, and insight boundaries;
- headquarters caching, user export, account deletion, or hosted
  backup/restore;
- durable saved-search claims, retries, leases, process-global search state,
  provider execution, or background-worker execution; or
- deployment activation, permanent import, authority transition, R2 changes,
  public signup, or additional users.

Generated-material download and cross-store deletion require later bounded
designs. The current worker remains an idle scaffold and receives no web
keyring or R2 credential in this slice.

## Verification gates

- [ ] Temporary SQLite tests prove record ordering, engagement counts,
      availability parity, response minimization, and insight parity.
- [ ] Route-dispatch tests prove both endpoints use the configured repository,
      fixed errors remain private, and shared mode has no SQLite fallback.
- [ ] Alembic reports exactly one head at `20260813_0006`; this slice adds no
      schema, index, grant, or RLS-policy change.
- [ ] Disposable PostgreSQL 18 tests prove missing-context and cross-owner
      denial for application rows, engagement counts, and lifecycle history.
- [ ] PostgreSQL and SQLite results preserve outcome definitions, active-history
      handling, group limits, date serialization, and explicit response shape.
- [ ] Responses and errors contain no owner IDs, stored-object IDs or keys,
      generated content, local paths, credentials, database URLs, or unrelated
      tenant data.
- [ ] Local tests, compilation, dependency and shell checks, exact-diff security
      review, required GitHub checks, and loopback build/dashboard/listener
      smoke pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live database,
      R2 object, service, release, environment, user, or access restriction
      changes.

The final authority, rollback, recovery, and cutover contract remains in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
