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

The next discovery slice converts job-list reads, duplicate/suppression checks,
suppression review/clearing, saved-search reads/deletion, and minimized source-
diagnostic history. Its exact scope and no-activation gate are documented in
`STAGE_3_POSTGRES_DISCOVERY_REPOSITORIES.md`.

The following hosted operations remain outside these foundations and must be
converted before `HOSTED_REPOSITORY_CUTOVER_COMPLETE` can become true:

- job/search-result creation, saved-search creation/update/scheduling, and job-
  status transitions, verification, cleanup, and permanent deletion;
- suppression creation coupled to permanent job deletion;
- applications, engagements, interview preparation, and follow-up data;
- headquarters cache entries and any unconverted job-domain reads;
- all user export and account-deletion reads and writes;
- generated-material creation, replacement, and deletion through encrypted
  private object storage rather than authoritative local filesystem paths;
- hosted backup/restore behavior, which must not replace the frozen SQLite
  workspace after PostgreSQL becomes authoritative; and
- any background-worker transaction that later consumes the worker database
  credential.

Provider secrets and stored-object metadata already have PostgreSQL/RLS
boundaries, but their application flows must be verified again with the final
repository implementation. The current worker is an idle heartbeat scaffold;
do not give it the web keyrings or R2 credential merely for symmetry.

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

The final deployment helper must encode or require an operator-confirmed data-
authority state so its automatic code rollback cannot silently cross that
boundary.

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
3. Confirm `/etc/careertrellis/staging.env`, `postgres-runtime.env`,
   `provider-secrets.env`, and `object-storage.env` are `root:root` mode `0600`;
   confirm both complete keyrings can be retrieved independently.
4. Confirm WireGuard, the runtime database role, the permanent private R2
   bucket, its bucket-only credential, and its application-egress `/32`
   restriction are ready without printing secrets.
5. Take a fresh full pgBackRest backup, copy its encrypted repository to the
   independent destination, record the backup label, and pass an isolated
   restore with zero bad checksums.
6. Retain the completed disposable no-omission import and three-surface
   reconciliation receipts. Keep the rehearsal database and bucket until an
   operator explicitly approves cleanup.
7. Verify the exact final code can roll back safely before data authority
   changes and fails closed against an incompatible post-cutover rollback.
8. Keep Caddy's access restriction enabled and do not invite another user.

## Eventual final cutover sequence

This sequence is documentation for the later complete transition, not an
instruction to activate this foundation release:

1. Enter maintenance mode, stop both web and worker services, and prove they are
   inactive so SQLite and generated files form one quiescent source snapshot.
2. Create and verify a recoverable SQLite/generated-files snapshot. Record only
   sanitized inventory counts and hashes.
3. Take and verify the immediately pre-import PostgreSQL backup and independent
   encrypted mirror.
4. Apply the exact reviewed Alembic head with the migration role and apply the
   reviewed runtime grants. Use forward migrations only.
5. Run the read-only SQLite preflight from the exact release and compare its
   sanitized counts with the accepted rehearsal baseline.
6. Import to the permanent PostgreSQL database and permanent private R2 bucket
   with no omission flags while the services remain stopped.
7. Reconcile the accepted source-file inventory, every owner-scoped PostgreSQL
   table, `stored_objects` metadata, and all permanent-bucket object keys. Stop
   on any missing row/object, count or hash mismatch, duplicate reference, or
   orphan key.
8. Mark PostgreSQL/R2 authoritative, activate only the exact reviewed release,
   and verify its build, local liveness/readiness, service state, and HTTPS path.
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
