# Stage 3: PostgreSQL Repository Foundation

This slice begins moving application data access behind explicit repositories.
It adds the PostgreSQL repository foundation for the profile, base-resume, and
base-resume-version domains while preserving SQLite for standalone local use.
It does **not** complete the hosted repository transition and must not be
activated on staging.

## Hard no-activation boundary

Shared-host configuration is intended to use:

```text
CAREERTRELLIS_DATA_STORE=postgres
```

That setting is a fail-closed selection, not permission to cut over. The
application's internal `HOSTED_REPOSITORY_CUTOVER_COMPLETE` constant remains
false until every hosted data domain and operation has been converted and the
final cutover contract has been reviewed. A staging or production process
configured with `CAREERTRELLIS_DATA_STORE=postgres` must therefore refuse to
start while that constant is false.

Do not add the setting to the live `/etc/careertrellis/staging.env`, activate a
release containing only this foundation, import into the authoritative staging
database, or treat a passing subset of repository tests as cutover approval.
The example environment file documents the eventual required setting only.

Local mode remains deliberately separate. An unset data-store setting or
`CAREERTRELLIS_DATA_STORE=sqlite` selects the existing local SQLite workspace;
local launchers must not require PostgreSQL, WorkOS, R2, or server keyrings.

## Repository contract in this slice

The foundation covers:

- one owner-scoped profile repository;
- owner-scoped base-resume and base-resume-version repositories;
- owner-scoped base-resume soft deletion that preserves version history and
  excludes deleted resumes from normal selection;
- profile-to-active-resume and resume-to-version relationships constrained to
  the same owner; and
- explicit transactions that set trusted, transaction-local tenant context
  before accessing a protected PostgreSQL row.

Hosted repository methods must use the authenticated local `app_users.id`, not
an owner ID accepted from a request body, URL, cookie, or form. Every query must
retain an owner predicate even though forced PostgreSQL RLS is also present.
Missing tenant context must expose no protected rows. Repository errors must not
include connection URLs, profile contents, resume text, WorkOS identifiers, or
provider credentials.

SQLite remains the implementation for local mode. This slice must not modify,
reset, seed, or import the user's local `data/jobapplier.db`, and its tests must
use synthetic temporary workspaces and disposable PostgreSQL targets.

## Domains still blocking repository cutover

The discovery slice converts job-list reads, duplicate/suppression checks,
suppression review/clearing, saved-search reads/deletion, and minimized source-
diagnostic history. Its exact scope and no-activation gate are documented in
`STAGE_3_POSTGRES_DISCOVERY_REPOSITORIES.md`.

The saved-search write slice adds owner-scoped create/upsert and atomic
post-run reminder advancement. Its transaction and scheduling contract is in
`STAGE_3_POSTGRES_SAVED_SEARCH_WRITES.md`.

The job-catalog write slice adds owner-scoped manual/discovery job creation and
initial score completion. Its bounded transaction contract is in
`STAGE_3_POSTGRES_JOB_CATALOG_WRITES.md`.

The lifecycle write slice adds atomic owner-scoped job/application lifecycle
transitions and latest-change undo. Its bounded transaction contract is in
`STAGE_3_POSTGRES_LIFECYCLE_WRITES.md`.

The posting-verification slice adds owner-scoped active refresh and expiry
writes, including atomic application closure for pre-application states. Its
bounded transaction contract is in `STAGE_3_POSTGRES_VERIFICATION_WRITES.md`.

The job-retention slice adds owner-scoped bounded preview, archive, restore, and
permanent deletion for untouched jobs, with atomic privacy-minimized
suppression. Its protected transaction contract is in
`STAGE_3_POSTGRES_JOB_RETENTION_WRITES.md`.

The application-operations slice adds owner-scoped engagement tracking and the
interview-preparation workspace, including conflict-safe persistence around an
explicit AI-provider call. Its two-phase transaction contract is in
`STAGE_3_POSTGRES_APPLICATION_OPERATIONS.md`.

The application-read slice adds the owner-scoped Application Logs projection
and private in-process outcome insight aggregation. Its response-minimization
contract is in `STAGE_3_POSTGRES_APPLICATION_READS.md`.

The provider-context slice routes AI/maps capability validation, explicitly
consented resume OCR, and manual-import scoring through the authenticated
owner's existing profile and encrypted-secret boundaries. Its transaction and
data-minimization contract is in
`STAGE_3_POSTGRES_PROVIDER_CONTEXT_READS.md`.

The readiness slice routes the public `/health/ready` database probe through
the explicitly selected local or shared data store without a SQLite fallback.
It remains ownerless, returns only fixed health responses, and does not weaken
the final cutover gate. Its operational and privacy contract is in
`STAGE_3_POSTGRES_READINESS.md`.

The search-execution context slice routes the authenticated owner's profile,
conditional suggested-keyword cache, known-job statuses, suppression
fingerprints, and accepted-job writes through the existing repository
boundaries. Each short database transaction closes before external provider
or AI work begins. Its ownership, minimization, and transaction contract is in
`STAGE_3_POSTGRES_SEARCH_EXECUTION_CONTEXT.md`.

The headquarters-cache slice routes the authenticated owner's privacy-minimized
OpenStreetMap cache lookup and eligible write through a dedicated repository.
Its short cache read closes before maps or AI provider work, and its eligible
write uses a separate transaction after that work. Its storage-policy contract
is in `STAGE_3_POSTGRES_HEADQUARTERS_CACHE.md`.

The durable search-run slice replaces process memory with owner-scoped status,
cancellation, leases, retry/crash recovery, and a worker-only least-privilege
claim broker. The bounded scheduled-dispatch follow-up atomically publishes due
saved searches through that run path and advances their reminders only after
publication succeeds. Their no-activation and rollback contracts are in
`STAGE_3_POSTGRES_SEARCH_RUNS.md` and
`STAGE_3_POSTGRES_SCHEDULED_SEARCH_DISPATCH.md`.

Generated tailoring materials now use the existing owner-scoped application
and stored-object schema in selected shared mode. AI, PDF, and encrypted upload
work occurs outside database transactions; a short stale-safe transaction
atomically swaps both verified object references and tombstones replaced
metadata for retryable reconciliation. Local mode retains SQLite/filesystem
behavior. The boundary is documented in
`STAGE_3_POSTGRES_GENERATED_MATERIALS.md`.

Owner-requested single-job deletion now has a bounded cross-store contract in
selected shared mode. One short owner-scoped transaction locks the job and its
application object references, records the privacy-minimized suppression,
tombstones only object metadata that is not referenced by any same-owner
surviving application, and deletes the job so PostgreSQL foreign keys remove
its application, lifecycle history, and engagements atomically. Shared
same-owner references remain locked, active, and preserved. Private blobs are
never deleted before commit; the existing owner-scoped reconciler performs
best-effort deletion afterward and retains tombstones for retry. Local behavior
is unchanged. See
`STAGE_3_POSTGRES_DEPENDENT_JOB_DELETION.md`.

Selected shared mode now also exposes an explicit dependent bulk-cleanup
action. It is limited to a separately previewed, bounded set of matched or
archived-from-matched jobs with saved application work. Its token binds the
job, dependent-row, target-object, and survivor-reference graph; the apply
transaction locks and revalidates that graph before reusing the single-job
suppression, exclusive tombstone, cascade, and post-commit reconciliation
contract. Existing untouched cleanup and all local SQLite behavior remain
unchanged. See `STAGE_3_POSTGRES_DEPENDENT_BULK_CLEANUP.md`.

The shared backup/restore policy now fails both encrypted-workspace endpoints
closed when PostgreSQL is the selected data store. The HTTP boundary rejects a
restore after normal shared authentication but before multipart parsing, and
the endpoint boundary repeats the guard before local paths, locks, temporary
files, archive helpers, or restore mutation. Local SQLite behavior is
unchanged. Hosted PostgreSQL and private
object-store recovery remain operator-managed; see
`STAGE_3_SHARED_BACKUP_RESTORE_POLICY.md`.

The portable user-data export now selects the configured repository. Local mode
uses one explicit SQLite read transaction. Shared mode uses one PostgreSQL JSON
statement under transaction-local tenant context, with explicit owner
predicates on every protected relation. Both repository scopes close before
serialization or response streaming begins. Its privacy and consistency
contract is in `STAGE_3_POSTGRES_USER_DATA_EXPORT.md`.

The account-erasure slice adds irreversible owner-only acceptance and durable,
idempotent PostgreSQL/private-object cleanup through a dedicated least-
privilege erasure worker. It deletes CareerTrellis application data only; the
WorkOS identity remains outside this boundary. The worker is installed disabled
and final shared activation remains blocked. See
`STAGE_3_POSTGRES_ACCOUNT_ERASURE.md`.

The following hosted operations remain outside these foundations and must be
converted before `HOSTED_REPOSITORY_CUTOVER_COMPLETE` can become true:

- any general-purpose background queue beyond the bounded search-run,
  scheduled saved-search dispatch, and account-erasure brokers;
- any application operation outside the converted lifecycle, engagement,
  interview-preparation, record-list, and insight repository boundaries;
- any unconverted job-domain reads;
- any standalone direct protected application deletion outside the converted
  job-deletion and account-erasure aggregates; and
- final no-omission rehearsal, recovery, activation, and post-cutover evidence.

Provider secrets and stored-object metadata already have PostgreSQL/RLS
boundaries. The four provider-context consumers use the encrypted secret
boundary, but all application flows must be verified again with the final
repository implementation. The bounded search worker described in
`STAGE_3_POSTGRES_SEARCH_RUNS.md` uses only its dedicated database credential
and the owner-scoped provider-secret keyring; do not give it the web runtime,
migration, object-storage, or R2 credentials merely for symmetry.
The account-erasure worker is a separate Unix and PostgreSQL principal. It gets
only its bounded broker functions and a separate delete-only private-bucket
credential; it receives no provider-secret or object-decryption keyring.

## Data authority and rollback contract

Before final cutover, SQLite and the local generated-files directory remain the
authoritative source. PostgreSQL and R2 rehearsal targets are disposable copies.
After the final import is reconciled and the PostgreSQL repository release is
activated, PostgreSQL and the permanent private R2 bucket become authoritative,
and the SQLite source becomes a read-only recovery snapshot.

A code-only rollback cannot reverse committed PostgreSQL rows, remove R2
objects, or copy new hosted writes back to SQLite. Automatically activating an
older SQLite release after PostgreSQL has accepted even one post-cutover write
would create split-brain data and can lose user changes. Therefore:

- before the first post-cutover write, a failed readiness or smoke test may
  restore the frozen SQLite-compatible code and source snapshot;
- after the first post-cutover write, keep PostgreSQL/R2 authoritative and use
  a reviewed forward code or schema correction; and
- never run an Alembic downgrade on staging or infer ownership for an orphaned
  R2 object.

The release-independent helper and systemd guards in
`STAGE_3_HOSTED_AUTHORITY_BOUNDARY.md` encode an operator-confirmed, one-way
data-authority state so automatic rollback and service restart cannot silently
cross that boundary. This foundation release remains incompatible with hosted
authority while `HOSTED_REPOSITORY_CUTOVER_COMPLETE` is false.

## Operator gates before the eventual final cutover

All of these are required; completion of the earlier disposable rehearsal does
not waive the exact-release checks:

1. Review and merge the complete repository-transition PR and fetch its exact
   40-character merge commit into the root-owned deployment checkout.
2. Confirm the exact revision's required `Web foundation` GitHub Actions checks
   passed, retaining its PostgreSQL 18/RLS and negative-authorization receipt.
   Then prepare the same 40-character revision with
   `careertrellis-deploy --prepare-only`; this supplies the immutable-release,
   local automated-test, object-integrity, and syntax receipts but does not
   substitute for the configured PostgreSQL CI job.
3. Confirm `/etc/careertrellis/staging.env`, `search-worker.env`,
   `account-erasure-worker.env`, `postgres-runtime.env`, `postgres-worker.env`,
   `postgres-erasure-worker.env`, `provider-secrets.env`, `object-storage.env`,
   and `account-erasure-object-storage.env` are `root:root` mode `0600`;
   confirm both complete keyrings can be retrieved independently. Confirm each
   worker unit loads only its dedicated files. The erasure worker must have no
   WorkOS, provider-secret, web-cookie, upload, or decryption-keyring value.
4. Confirm WireGuard, the runtime database role, the permanent private R2
   bucket, its bucket-only credential, and its application-egress `/32`
   restriction are ready without printing secrets.
5. Take a fresh full pgBackRest backup, copy its encrypted repository to the
   independent destination, record the backup label, and pass an isolated
   restore with zero bad checksums.
6. Retain the completed disposable no-omission import and three-surface
   reconciliation receipts produced by the exact reviewed release's root-only
   wrapper. Keep the rehearsal database and bucket until an operator explicitly
   approves cleanup. See
   `STAGE_3_SQLITE_POSTGRES_IMPORT_RECONCILIATION.md`.
7. Verify the exact final code can roll back safely before data authority
   changes and fails closed against an incompatible post-cutover rollback.
8. Keep Caddy's access restriction enabled and do not invite another user.

## Eventual final cutover sequence

This sequence is documentation for the later complete transition, not an
instruction to activate this foundation release:

1. Enter maintenance mode, stop the web, search-worker, and erasure-worker
   services, and prove they are inactive so SQLite and generated files form one
   quiescent source snapshot.
2. Create and verify a recoverable SQLite/generated-files snapshot. Record only
   sanitized inventory counts and hashes.
3. Take and verify the immediately pre-import PostgreSQL backup and independent
   encrypted mirror.
4. Apply the exact reviewed Alembic head with the migration role and apply the
   reviewed runtime grants. Use forward migrations only.
5. Run the read-only SQLite preflight from the exact release and compare its
   sanitized counts with the accepted rehearsal baseline.
6. Import to the permanent PostgreSQL database and permanent private R2 bucket
   with no omission flags while the services remain stopped. The wrapper must
   automatically complete its independent read-only reconciliation before it
   can report success.
7. Review the sanitized reconciliation of the accepted source-file inventory,
   every owner-scoped PostgreSQL
   table, `stored_objects` metadata, and all permanent-bucket object keys. Stop
   on any missing row/object, count or hash mismatch, duplicate reference, or
   orphan key.
8. Confirm the exact reviewed release carries the hosted-authority capability,
   mark PostgreSQL/R2 authoritative while all three services remain stopped,
   activate only that release, and verify its build, local liveness/readiness,
   service state, and HTTPS path. The marker must precede the first hosted write.
9. Verify owner login and logout, representative profile/resume/job/application
   reads and writes, provider capability, export/deletion scope, and authorized
   generated-object download. Repeat missing-context and cross-tenant negative
   tests and inspect minimized logs for secret or personal-data leakage.
10. Take a fresh post-import full PostgreSQL backup, mirror it independently,
    record the permanent R2 inventory receipt, and retain the frozen SQLite
    snapshot for the reviewed rollback window.

Do not reopen web traffic if reconciliation, readiness, authorization, or
recovery evidence is incomplete.

## Acceptance checklist for this foundation PR

- [ ] `CAREERTRELLIS_DATA_STORE` accepts SQLite only in local mode and requires
      `postgres` for shared-host mode.
- [ ] Shared-host startup fails closed while
      `HOSTED_REPOSITORY_CUTOVER_COMPLETE` is false.
- [ ] Profile, base-resume, and base-resume-version repositories use explicit
      owner-scoped PostgreSQL transactions with transaction-local tenant context.
- [ ] User-data export uses a single owner-scoped repository snapshot, exposes
      only its explicit public allowlist, and cannot fall back to SQLite in
      shared mode.
- [ ] Base-resume soft deletion cannot select, delete, or reassign another
      owner's resume or expose a deleted resume through normal reads.
- [ ] Missing-context and two-tenant tests deny cross-owner reads, writes, and
      relationship changes for the converted domains.
- [ ] Local SQLite behavior and local launch remain compatible without hosted
      configuration or network services.
- [ ] No route outside the converted foundation is represented as PostgreSQL-
      ready, and no fallback silently uses SQLite in shared-host mode.
- [ ] Documentation identifies every remaining repository domain and the data-
      authority transition.
- [ ] The rollback contract prevents code-only rollback to writable SQLite after
      PostgreSQL has accepted a post-cutover write.
- [ ] No live environment file, database, R2 object, release symlink, service,
      staging user, or access restriction changed for this PR.
- [ ] Final activation remains blocked until the remaining repositories, full
      no-omission import, recovery evidence, and end-to-end owner/isolation gates
      are reviewed together.
- [ ] Import/reconciliation runs only from an immutable reviewed release while
      services are inactive and authority is still `sqlite-pre-cutover`; the
      reconciliation command cannot repair or change authority.
