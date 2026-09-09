# Default-closed signup database/lifecycle slice — 2026-09-07

CareerTrellis is owned by **J. Hunter Johnson LLC**.

## Status and boundary

Local implementation and synthetic verification of slice 1 in
`BETA_SIGNUP_APPLICATION_INTEGRATION_PLAN.md` are complete. This is not deployed
acceptance, provider integration, or permission to open signup. The outer
self-service beta backlog item remains open.

Source base: `184b34b397dc1fb5458a70e387b76658ac479ad6` (merged PR 76), branch
`codex/beta-factor-readiness`, plus the preserved local prototype/design work.
The changes remain uncommitted and unpublished. No staging host, real identity,
provider configuration, credential, backup repository or GitHub workflow was
changed by this slice.

Subsequent local provider/session work adds a narrow runtime-only policy-context
function to this still-uncommitted, never-deployed revision. See
`BETA_SIGNUP_PROVIDER_SESSION.md` for the addition and fresh full lifecycle/RLS
receipts. The original receipt below is retained as historical slice-1 evidence.

## Implemented contract

- Additive Alembic revision `20260907_0014` installs an admission ledger in a
  private schema. A separate reviewed administrator bootstrap creates dedicated
  NOLOGIN owner and broker roles; it installs no broker credential. Unexpected
  memberships, schema grants and admission-owner default privileges fail closed.
- Initial policy is pilot, signup closed, adapter not ready, legacy session
  minimum, maintenance off and provider realm unconfigured. The cap/lease values
  are inactive defaults, not accepted beta policy. Existing explicit pilot seed,
  disable and reinvite behavior is preserved; beta seeding counts retained
  accounts and live reservations even when public signup is closed. Disabled and
  erasure-pending accounts keep their seats until actual deletion.
- Durable reservation/claim/finish functions support one acknowledged exchange
  claim and atomic ordinary-account plus empty-profile creation. They do no
  provider I/O. The broker cannot read or mutate the underlying ledger directly.
  This is a database trust boundary for a future verified adapter, not proof that
  a caller-supplied identity was verified by WorkOS.
- The actual pilot operator and all eight canonical erasure SQL entry points
  acquire the common lifecycle lock before participating inner locks. Original
  erasure implementations are private and remain inaccessible after the real
  runtime-grant script is reapplied. Existing signatures and worker authorization
  are preserved; no production module imports the prototype.
- Erasure acceptance and final deletion invalidate pending flow generations in
  their respective transactions. Final deletion nulls ledger account links.
  Object acknowledgement, other-tenant preservation and the 30-day terminal
  receipt remain intact. Current database time is sampled after lock waits;
  mutations that overrun a lease roll back, including delegated mutations that
  would otherwise return false after changing state.
- Direct imports now require durable closed-pilot maintenance before object
  backend construction or inventory access. A separate shared import lock blocks
  maintenance exit while the transaction runs, without holding the common
  lifecycle lock across object-provider I/O. Existing empty-target checks,
  authoritative inventory recheck and uncertain-COMMIT handling remain.
- Legacy PostgreSQL readiness fails when persisted policy requires an adapter or
  session format that this application does not implement. This is a narrow
  policy-compatibility check, not continuous ACL attestation or session-v2
  enforcement on private requests.
- Destructive downgrade is deliberately refused. Any correction must preserve
  accounts, incarnation boundaries and coordinated erasure; review a forward
  correction instead of restoring the old uncoordinated functions.

## Local verification receipts

All database tests used synthetic tenants and an explicitly scoped disposable
PostgreSQL 18 container, published only on loopback, with tmpfs database storage.
Fixtures refuse pre-existing database/role collisions. No application database,
resume, real provider token or paid API was used.

| Verification surface | Fresh result |
| --- | --- |
| `test_admission_lifecycle` | 25 tests, 6.918 s, all passed; no skips (final SQL cleanup rerun) |
| `test_postgres_rls` | 57 tests, 4.447 s, all passed; repeated migration head `20260907_0014` |
| Historical `test_signup_integration` | 44 tests, 7.257 s, all passed |
| WorkOS rehearsal, account-choice, loopback runtime, P0 and object suites | 404 tests, 18.943 s, passed with two existing Windows platform skips |
| `python -m compileall -q .` | Exit zero |
| Four changed shell scripts | Native Linux `bash -n` passed |

The 25-test lifecycle suite covers populated-tenant preservation, repeat upgrade,
real operator/erasure paths, seed versus final-slot reservation races, closed-beta
cap enforcement, failed-profile rollback, lease expiry across observed database
lock waits, actual search-owner and catalog-write contention, acknowledgement and
generation boundaries, real grants, private-path denials, direct-import refusal,
maintenance exit exclusion, purge deadlines and bootstrap contamination refusal.

The Linux permission test creates a root-only release-shaped directory inside
that container, proves the PostgreSQL OS user cannot read its SQL path, and runs
the actual root bootstrap successfully by streaming root-opened SQL to `psql`
stdin. This checks the restricted release-directory boundary, not the live host's
systemd units, installed credentials, filesystem or migration window.

The 404-test run starts a real Uvicorn server on an ephemeral loopback port with
temporary synthetic SQLite storage, checks `/api/version`, `/` and the unchanged
account-choice flow, then stops it cleanly. The changed PostgreSQL lifecycle paths
are exercised separately against the real disposable PostgreSQL engine. Existing
Starlette/httpx and websockets deprecation warnings are non-fatal. The two Windows
skips are the existing POSIX hosted-authority and Linux systemd checks; they are
not counted as passed native host acceptance.

The historical 44-test integration fixture is now frozen at predecessor revision
`20260903_0013`, with a fixture-only compatibility shim for its old coordinated
operator wrapper. Its synthetic/loopback restrictions remain. Those 44 results
are compatibility evidence, not evidence for production lifecycle enforcement;
the new 25-test suite exercises the actual new migration and entry points. The
old head-to-search-schema downgrade test now asserts the deliberate forward-only
refusal and preservation; it no longer claims an executable destructive downgrade.

Bounded independent lifecycle and deployment-helper readbacks were reconciled
with primary inspection and test receipts. Review prompted fail-closed privilege
checks, stronger current-clock evidence and the root-to-PostgreSQL stdin fix.
This was not a repository-wide security scan or a live deployment gate.

Final tracked and untracked whitespace checks passed; the diff/status readback
confirmed nothing staged. The root `.codex` configuration remains present and
ignored. After confirming zero fixture roles/schemas and no residual RLS test
database, the exact labeled disposable container was stopped and auto-removed.
Only temporary synthetic database and container test files were discarded; those
disposable contents are not retained. Application data and backups were untouched.

## Installation and maintenance remain separately approved

1. Prepare and review the exact candidate, including the SQL artifact, restricted
   role bootstrap and forward-correction plan. Do not install a broker credential
   or change signup/session/provider policy as part of this slice.
2. Obtain the maintenance window and exact role-installation approval. Quiesce
   web, search and erasure callers and keep them stopped. Existing transactions
   and cached calls to old function OIDs must be drained before migration.
3. Run the reviewed root bootstrap against the explicitly selected database.
   The migration helper refuses active services and uses a sanitized, read-only
   principal/namespace/connection preflight. Its connection census is a snapshot,
   not a lease that prevents a caller from reconnecting. The migration independently
   validates its ownership and privilege prerequisites in its transaction.
4. Upgrade and reapply reviewed grants; verify the closed policy, legacy pilot
   compatibility, retained tenants, readiness and normal worker behavior before
   reopening the pilot. Native host permissions and service behavior still need
   exact-release deployment/live verification. No implicit deployment is approved.

The existing host authority and post-cutover import prohibitions still apply.
No import is authorized here. If a future separately approved import is permitted,
the migration principal must commit `set_import_maintenance(true)` with the
schema-owner role before the importer starts. Keep host callers quiesced, perform
the approved import, resolve any uncertain COMMIT outcome, and reconcile account
occupancy and object/database results before committing
`set_import_maintenance(false)` and resuming service. That exit advances the flow
generation and refuses any still-running import. Never clear the durable gate
automatically in failure cleanup. No root import orchestration helper or automated
reconciliation policy is delivered by this slice.

## Next work and outstanding gates

Next is the isolated real-provider transport and account-bound session slice:
zero automatic code/refresh retries, cancellable total deadlines, bounded JWKS,
exact local-account incarnation checks, refresh continuity and realistic cookie
sizing. Then wire application routes/UI while signup remains closed.

Before activation, separately accept broker credential handling, session-v2
lifetime/key policy and forced-sign-in transition, actual cap/lease settings,
physical ledger-purge scheduling, policy-change tooling, abuse controls,
accessibility, stable provider domain/environment/cost and MFA/recovery. The
current application prohibition on production runtime is unchanged. A local
account cap does not cap provider identities or guarantee provider billing limits.

No commit, push, draft PR, deployment, extra backup/restore or workflow was run.
Successful exact-change deployment and live verification still precede publishing
commits to a PR branch or opening a draft PR.
