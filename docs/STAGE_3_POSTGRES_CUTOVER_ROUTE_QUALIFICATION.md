# Stage 3 shared-route cutover qualification

This slice adds evidence for the later PostgreSQL/private-object authority
decision. It does not perform that decision. Build `20260813.22` still leaves
`HOSTED_REPOSITORY_CUTOVER_COMPLETE` false, adds no hosted-authority capability
file, changes no production route behavior, and starts no service.

## Route inventory gate

`backend/test_postgres_cutover_routes.py` assigns every `/api/` method and path
to exactly one reviewed shared-host policy:

- session/build metadata and authenticated private-object download;
- profile, base-resume, provider-context, discovery, lifecycle, verification,
  retention, and durable search-run repositories;
- portable export, account-erasure, and local-only backup/restore policy;
- generated-material, application-workspace, and application-read domains.

The inventory fails when a route is added, removed, duplicated, or left without
an explicit policy. It is not a claim that every destructive or provider-bound
route should be invoked in one test. Those mutations remain covered by their
focused transaction, compensation, retry, and authorization suites.

## Disposable PostgreSQL qualification

When `CAREERTRELLIS_POSTGRES_TEST_ADMIN_URL` points to an isolated PostgreSQL 18
server, the dedicated suite reuses the reviewed migration/runtime-role harness
and creates two synthetic owners. It executes representative routes from every
repository family. Owner-distinct payload assertions cover profile, jobs,
resumes, applications, insights, portable export, generated-material metadata,
workspace, and encrypted-object reads. Stable empty-shape routes such as saved
searches, diagnostics, suppressions, cleanup preview, and idle search status are
dispatch/no-SQLite smoke evidence rather than cross-owner payload evidence. The
combined qualification proves that routes:

- select PostgreSQL under the normal shared-host middleware and authenticated
  owner context;
- return only the authenticated owner's seeded private payloads on the
  owner-distinct surfaces listed above;
- deny cross-owner generated-material, workspace, and private-object reads;
- reject missing sessions before any repository or object-store access;
- never call the supplied SQLite connection factory; and
- keep external AI/maps calls mocked while still loading the owner-scoped
  provider profile from PostgreSQL.

Private-object qualification uses a process-local `InMemoryObjectBackend` with
real envelope encryption and PostgreSQL metadata. No R2 credential, bucket, or
network call is needed. Without the explicit PostgreSQL test URL, only the
static route-policy inventory runs and the disposable integration class reports
an environment-gated skip.

## CI coverage

The Linux regression job now runs the previously orphaned generated-material,
dependent single-job deletion, and dependent bulk-cleanup suites as well as the
new inventory. The PostgreSQL 18 job runs those suites again with its disposable
database so their integration classes and the two-owner route qualification are
not silently skipped.

This is supporting evidence, not activation approval. Final cutover still
requires the exact-release no-omission import/reconciliation, recovery,
authority-marker, service, credential-boundary, and operator smoke receipts in
`STAGE_3_POSTGRES_REPOSITORY_FOUNDATION.md` and
`STAGE_3_HOSTED_AUTHORITY_BOUNDARY.md`.
