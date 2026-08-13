# Stage 3: PostgreSQL Readiness

This bounded slice makes `/health/ready` probe the explicitly selected data
store. Standalone local use retains SQLite. The eventual shared-host runtime
uses PostgreSQL without opening or depending on the frozen SQLite workspace.
The overall repository transition remains incomplete, so this release must not
be activated on staging.

## Hard no-activation boundary

`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false. Do not add
`CAREERTRELLIS_DATA_STORE=postgres` to live staging, migrate or import the live
database, change data authority, activate a release, restart services, or
change PostgreSQL, R2, Caddy, users, environment files, or secret files for
this slice.

SQLite and generated files remain authoritative. PostgreSQL and R2 rehearsal
targets remain disposable until the final repository, no-omission import,
reconciliation, recovery, authorization, and cutover gates pass. A successful
unit or disposable PostgreSQL readiness check is evidence about this code path;
it is not approval to flip the cutover constant or activate shared storage.

## Public, ownerless health boundary

`GET /health/ready` remains a public process-health endpoint. It does not
authenticate a WorkOS session, accept an owner identifier, establish tenant
context, or query an owner's profile, resume, job, application, credential, or
stored-object row. This is intentional: a reverse proxy and the local release
helper must be able to decide whether the process can serve traffic before a
user session exists.

Public does not mean directly exposed. Requests on the configured shared host
must still traverse the existing trusted reverse proxy, which overwrites the
root-owned `X-CareerTrellis-Proxy-Token`; direct requests that lack the token
remain forbidden. Loopback probes from the reviewed deployment helper remain
allowed. `/health/live` keeps its narrower process-only behavior and does not
touch dependencies.

Readiness returns only `{"status":"ready","build":"<build>"}` on success. Any
configuration, filesystem, database, identity-store, provider-secret-store, or
private-object-store failure returns the existing fixed HTTP 503 detail,
`Service is not ready.` The response must not reveal the selected store,
database or R2 location, table name, owner or WorkOS identifier, credential,
key ID, local path, exception text, or failed dependency.

## Selected-store readiness contract

The data-store boundary owns selection and reachability:

- local mode validates the SQLite selection, opens the existing SQLite
  connection, executes one bounded `SELECT 1`, and closes the connection;
- shared mode validates the explicit PostgreSQL selection and runtime database
  URL, opens a short connection through the bounded runtime pool, executes one
  bounded `SELECT 1`, and closes the connection before later checks; and
- neither mode may fall back to the other store after configuration or
  reachability failure.

The shared probe uses only the server's runtime PostgreSQL credential. It does
not use the migration role, set `careertrellis.user_id` or
`careertrellis.workos_user_id`, bypass RLS, or inspect protected rows. Existing
readiness helpers continue to validate the configured identity table and
encrypted provider-secret table without returning owner rows. The private
object-store helper continues to use a metadata-only bucket readiness request;
it does not upload, download, list, or expose an object. Runtime configuration,
authentication configuration, and required local-directory access remain part
of the readiness result.

The cutover guard is part of store validation. While
`HOSTED_REPOSITORY_CUTOVER_COMPLETE` is false, a shared PostgreSQL runtime must
fail closed rather than advertise readiness. The future final cutover must
review that constant separately after every remaining authoritative data path
and recovery gate is complete.

## Migration and real shared-store smoke

This slice adds no migration, schema, index, grant, role, or RLS-policy change.
Alembic must continue to report exactly one head at `20260813_0006`.

A real shared-store smoke belongs to the final reviewed cutover rehearsal, not
this PR. Before it can be run, the disposable or approved target must have:

- the exact reviewed migrations at `20260813_0006` and the reviewed runtime
  grants;
- root-owned runtime database, WorkOS, provider-secret keyring, R2, and trusted
  proxy configuration installed without printing their values;
- the private R2 bucket and required local runtime directories available;
- every remaining repository and no-omission import/reconciliation gate
  complete; and
- an explicitly reviewed build whose cutover constant is allowed to change.

That rehearsal must verify the loopback endpoint and the trusted-proxy path,
the fixed 503 response for each unavailable dependency, the deployment and
Caddy health budgets, absence of SQLite access in shared mode, and absence of
secrets, owner data, database URLs, bucket details, and local paths from
responses and logs. It must not exercise a provider API or read tenant content
merely to prove process readiness.

## Explicit exclusions

This slice does not convert or authorize:

- search execution, durable saved-search claims, retries, leases, or worker
  execution;
- remaining job, application, export, account-deletion, backup, restore,
  tailoring, generated-material, or stored-object application flows;
- any owner-scoped readiness query or representative tenant read/write smoke;
- schema-version enforcement inside the public response, detailed dependency
  diagnostics, a new health payload, or changes to liveness semantics;
- connection-pool, retry, deployment-loop, Caddy, service, or health-timeout
  redesign;
- migrations, permanent import, data-authority transition, R2 mutation, public
  signup, or additional users; or
- staging activation, service restart, environment or key installation, or a
  change to `HOSTED_REPOSITORY_CUTOVER_COMPLETE`.

The idle worker remains outside this web-process readiness boundary and
receives no web database keyring, provider credential, or R2 credential.

## Verification gates

- [ ] Local tests prove SQLite selection, one bounded probe, connection
      closure, and no PostgreSQL access.
- [ ] Shared dispatch tests prove PostgreSQL selection, no SQLite fallback, a
      short closed connection, and no tenant-context or protected-row query.
- [ ] Cutover-incomplete and dependency-failure tests prove fixed HTTP 503
      responses without sensitive detail; liveness remains independent.
- [ ] Endpoint tests prove runtime, authentication, local-directory,
      data-store, identity-store, provider-secret-store, and private-object
      readiness checks run while the success payload remains minimal.
- [ ] Disposable PostgreSQL 18 tests prove the runtime role can perform the
      ownerless reachability probe without gaining tenant visibility.
- [ ] Alembic reports exactly one head at `20260813_0006`; this slice adds no
      migration, schema, grant, role, or RLS change.
- [ ] Local tests, compilation, dependency and shell checks, exact-diff
      security review, required GitHub checks, and loopback build/dashboard
      smoke pass.
- [ ] `HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false and no live database,
      R2 object, service, release, environment, user, or access restriction
      changes.

The final authority, rollback, recovery, and cutover contract remains in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md`.
