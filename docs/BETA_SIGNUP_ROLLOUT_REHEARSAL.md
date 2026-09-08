# Broker, migration and session-v2 rollout rehearsal

2026-09-08, local candidate on `codex/beta-factor-readiness`, base
`184b34b397dc1fb5458a70e387b76658ac479ad6`. CareerTrellis is owned by
**J. Hunter Johnson LLC**. This is a rehearsal receipt and proposed operational
contract, not an installed activation tool or permission to deploy.

## Outcome

Follow-up: the local corrections and protected operations candidate are described
in `BETA_ADMISSION_OPERATIONS.md`. The defects and test results below describe
the earlier rehearsal state; they are retained as historical evidence, not the
current implementation status.

The bounded synthetic sequence passed, but rollout is **not clear**. Two
reproduced defects require correction before accepting the complete path:

1. A fresh `pilot_users.perform_operation(..., 'seed', ...)` creates the account
   without its profile. The actual first `/api/profile` POST returned 404,
   "The profile was not found." The retained diagnostic test reproduces the
   repository exception. Existing pilots with profiles and self-service accounts
   created atomically with profiles both saved successfully. Correct operator
   provisioning and assess safe, additive repair of existing missing profiles;
   never replace populated profiles or reset application data.
2. Both worker environment validators omit the new `CAREERTRELLIS_AUTH_V2_*`
   settings and `CAREERTRELLIS_ADMISSION_DATABASE_URL`. Synthetic unexpected
   broker URL, envelope key and flow key were accepted. Checked-in units do not
   explicitly deliver these new settings, so this does not establish an actual
   leak. Reject them in workers and cover delivery/installer validation before
   installing web credentials. In particular, `provider-secrets.env` is shared
   with search workers and must not receive these web-only secrets.

`backend/test_signup_rollout.py` contains six scenarios. Tests 04 and 05 are
**diagnostic expectations of unresolved defects**, not acceptance of that
behavior. Convert them to corrective regression assertions when implementing
the fixes; a green test count must not conceal these open gates.

## Evidence boundary

The dedicated PostgreSQL 18 Docker container had a loopback-only published
port, tmpfs database storage, no host mounts, auto-removal and an exact rehearsal
label. The fixture refuses nonempty/colliding schemas, roles or other databases.
All passwords, identities, profiles, objects and signed provider replies were
synthetic. Only this disposable database and its exact container were accessed.

| Surface | Observed result |
| --- | --- |
| Bootstrap and migration | Actual administrator bootstrap was repeatable before login; actual migration principal, preflight, Alembic 0013-to-0014 upgrade, repeated head check and runtime grants preserved the existing synthetic profile exactly. |
| Restricted broker | NOLOGIN denied new connections; synthetic SCRAM login allowed only intended functions. Direct policy/account reads, owner-role assumption and public table creation were denied; no elevated role attributes were present. |
| Actual HBA | Exact database/role/source SCRAM rule accepted the intended connection. Wrong password, other database and different TCP source failed. Explicit reject rules preceded broader fixture defaults. Parse errors remained zero; original HBA bytes were restored. |
| Later maintenance | LOGIN broker blocked bootstrap/preflight. NOLOGIN denied new connections but did not terminate an already-open connection; preflight still rejected that connection. After owned pools closed, current-head migration/grant reapplication passed and LOGIN could be restored without resetting minimum-v2 or account data. This was not a fabricated future migration. |
| Real application lifespan | Default-off pilot startup, readiness, `/api/version`, `/` and mapped session passed. Enabled v2 with default policy failed startup. Minimum-v2 with v2 disabled denied readiness/private access. Compatible enabled construction passed using the actual factory, restricted broker and runtime roles. |
| Forced sign-in and closure | Legacy cookie was rejected without invoking legacy authentication after the v2 transition. Existing pilot login/profile save and new signup/first profile save passed. Closing signup and advancing flow generation retained existing v2 access and ordinary mapped login. Disabling the adapter after minimum-v2 failed closed rather than restoring legacy acceptance. |
| Actual worker entrypoints | Dedicated search role claimed and completed a synthetic queued run and emitted idle heartbeat on the next `run(once=True)`. Dedicated erasure role completed a synthetic account/object erasure and emitted completed heartbeat. Owner account survived; minimum-v2 and closed signup remained set. |

The application test runs the real ASGI lifespan and routes in-process. It
substitutes only external provider transport, legacy provider authentication,
analytics and object-store reachability. Shared runtime configuration validation,
broker factory, SQL, tenant mapping, middleware and profile repository are real.
Workers use their real entrypoints/validators/roles/queues/leases/finalization;
external search work and delete-only object transport are substitutes, and
signal registration is suppressed to avoid changing the test runner's handlers.

This does **not** prove real WorkOS credentials, token sizes, MFA/recovery,
real search-provider/R2 access, native systemd credential delivery, protected
file installation, deployed Caddy/privacy, or live end-to-end rollout. The HBA
test uses local TCP SCRAM, not deployed TLS identity or firewall verification.
Docker archive copy could not read the tmpfs; the test instead streams fixture
bytes into the exact validated container file. Changing `hba_file` itself is
startup-only; the successful test changes file contents and reloads them.

## Proposed installation and transition sequence

No live installer exists yet. Implement and rehearse a narrowly scoped,
secret-safe installer/coordinator before operating this sequence on staging.
It must preserve maintenance/migration locking, reject ambiguous ownership or
state, retain sanitized receipts and never print credentials or provider IDs.

1. Rediscover the exact release, authority, active users/queues, locks, service
   states and database prerequisites under the project runbook. Coordinate a
   maintenance window and obtain exact deployment/configuration approval.
   Do not trigger another backup, restore or workflow merely for cleanup.
2. Quiesce web, search and erasure callers, including independent operator/import
   clients. Verify database connection quiescence rather than relying solely on
   systemd status. Bootstrap the NOLOGIN broker/owner under administrator
   authority, then run the real migration preflight, migration and grants as the
   migration principal. Revision 0014 is required even for default-off pilot code.
3. Initially retain pilot policy, closed signup, adapter not ready and minimum
   session version 1. Verify compatible default-off pilot startup and preserved
   existing account/profile behavior. Do not use an empty fixture as proof that
   all real pilot accounts have profiles.
4. Install the reviewed exact database/role/source HBA allow followed by explicit
   broker rejects **before any broader matching allow**, including local/non-TLS
   bypass considerations. Validate deployed TLS/source routing separately.
   Install the broker SCRAM credential through the operator's visible secure
   terminal, with root-only protected delivery to the web process only. The
   owner remains NOLOGIN; grant no owner membership or direct table access to
   the broker. Preserve unrelated HBA rules and prove positive/negative access.
5. Prepare distinct flow/envelope keys, explicit key ID, issuer, realm generation,
   session lifetime, accepted audience if required, and same-target broker/runtime
   URLs. Credentials must never enter chat, argv, receipts or shared worker files.
   Validate exact file ownership/mode/allowed names and native service delivery.
6. During coordinated maintenance, advance the persisted policy to the accepted
   beta cap/realm, adapter ready and minimum session version 2, with signup still
   closed. Policy mutation must be serialized under the lifecycle lock in its
   eventual reviewed helper; the test's administrator SQL is not that helper.
   Enable the explicit v2 application switch, end maintenance at the reviewed
   point and verify startup, readiness and forced fresh sign-in. Verify the
   actual profile/search/erasure paths without widening public access.
7. Keep public signup closed pending all remaining policy/provider/MFA/ingress
   gates and separately authorized live acceptance. A closed-signup rehearsal
   is not permission to admit beta users. Opening the cap later is a separate
   operation and must not remove existing-user login/recovery.

## Subsequent migrations and forward correction

- NOLOGIN is a connection-admission fence, **not a connection terminator**.
  Stop/drain the actual owning services and verify all lifecycle connections
  have gone. Never terminate unrelated processes or sessions by a broad pattern.
- Current bootstrap/preflight requires the broker NOLOGIN even after activation.
  A future coordinator must snapshot the intended login/service/policy state,
  fence and quiesce, run reviewed migration/grant checks, then restore only the
  approved state after success. On an uncertain failure, retain maintenance and
  investigate; do not blindly re-enable services or retry the migration.
- Closing signup while leaving the adapter and minimum-v2 active preserves
  existing sessions and mapped login. Incrementing flow generation invalidates
  in-flight flows and needs explicit operator intent.
- After minimum-v2, neither flipping the environment switch off nor selecting an
  old legacy release is an accepted rollback. Keep v2-compatible forward
  correction; revision 0014 deliberately refuses destructive downgrade. Do not
  lower the session floor or delete admission/account data to recover access.
- Generic legacy database readiness is intentionally false at minimum-v2.
  Check web v2 readiness and actual worker queues independently; the entrypoint
  tests demonstrate that this legacy probe alone does not imply worker outage.

## Remaining acceptance

Correct both reproduced defects, implement/rehearse the protected installer and
later-migration coordinator, then resolve cap/leases, physical retention/purge,
session/key rotation, trusted peer and abuse limits, accessibility and recovery,
stable provider domain/cost/identity transition, MFA and public ingress. Retain
the existing deployment-first PR gate. No staging deployment, provider change,
public signup, commit, push, PR, Actions run or user-data mutation occurred here.

## Final local receipt

- Six rollout scenarios: **OK, no skips, 7.815 seconds**. Two intentionally
  reproduce the unresolved defects described above.
- Required P0/object plus pilot acceptance/receipt, account-choice, provider,
  protocol-rehearsal and signup HTTP regressions: **512 tests in 28.562 seconds,
  OK with 11 platform-related skips** (two existing P0 skips, two unavailable
  symlink checks and seven POSIX descriptor tests). Native browser coverage in
  the HTTP suite ran; no real provider request was made. An earlier invocation
  named a nonexistent `test_pilot_users` module; the corrected full invocation
  above passed. No application change was needed for that command error.
- Python `compileall`, tracked whitespace/diff checks and final status review
  passed. Nothing is staged, including protected files. Only the new test,
  this receipt, the HTTP receipt cross-reference and backlog changed this turn;
  pre-existing local integration work remains intact.
- Independent environment-contract review confirmed the worker validator and
  shared-file hazard against source. Two other evidence assignments did not
  initialize and were cancelled; the primary agent directly inspected and
  exercised the migration and application/worker surfaces instead. No unreturned
  agent result is counted as evidence.
- Before cleanup: exact disposable container identity/label, auto-removal,
  loopback-only binding and absence of host mounts verified; residual fixture
  roles, schemas, extra databases and HBA parse errors were all zero. Original
  HBA content restoration passed in the test. The exact container was stopped
  and auto-removed, discarding only temporary synthetic data; no recoverable
  application data or backups were removed.
- Root-local `.codex/config.toml` remained present and Git-excluded. No commit,
  push, PR, workflow, deployment, live backup/restore or protected configuration
  change occurred.
