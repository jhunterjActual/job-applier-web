# Protected admission installation and maintenance candidate

2026-09-08. Local implementation, **not installed, deployed, activated or approved
for live use**. CareerTrellis is owned by **J. Hunter Johnson LLC**. This follows
`BETA_SIGNUP_ROLLOUT_REHEARSAL.md`; that earlier receipt remains historical.

## Corrected blockers

`pilot_users.py` creates an empty profile inside the same transaction as a newly
seeded account, using the authorized target's tenant context. A profile-insert
failure rolls back the new account. Repeating `seed` for an active account or
successfully reinviting a disabled account inserts only a missing profile;
`ON CONFLICT DO NOTHING` preserves populated profiles. Existing active-account
repair reports `already_active` with `changed=yes`. Inspection, owner protection,
disabled-seed refusal, pending erasure and cap/lifecycle restrictions remain.
No bulk repair, migration backfill or real account mutation was performed.

Both worker validators now reject the admission database URL and **every**
`CAREERTRELLIS_AUTH_V2_` name by presence, including blank values, `ENABLED=0`,
optional audience and unknown future names. Rejection occurs before startup
logging, heartbeat creation, DB use or work claims, including disabled/once mode.
Legacy forbidden names retain their existing blank-value behavior. Search-owned
provider keys and erasure's dedicated delete-only configuration remain valid.

The security-fix investigation and one independent candidate review covered the
runtime-use and file-delivery boundaries. The review identified a draft bypass
when an older rollback target lacked the new checker. The correction is a
**target-independent operational guard**, exercised against an empty legacy
target in the native test; both deployment and rollback reject contaminated
worker files before proceeding. This is not evidence of a previous live leak.

## Artifacts and ownership

- `backend/admission_operations.py`: root CLI, descriptor-pinned protected file
  reads, no-clobber/compare-and-replace writes, strict parsers, DB installation,
  web delivery and maintenance phases.
- `deploy/scripts/manage-admission-operations.sh`: explicit source-release
  launcher with fresh environment, isolated interpreter and fixed-error output.
- `deploy/scripts/check-worker-admission-environment.sh`: standalone root-only
  delivery guard independent of a deployment/rollback target's feature support.
- Deployment and rollback invoke the adjacent reviewed guard or its fixed
  installed path `/usr/local/libexec/careertrellis/check-worker-admission-environment.sh`.
  The guard is required, root-owned and mode 0750/0755; missing it fails the gate.
  Install/verify the guard and both updated operational scripts together before
  using the new deployment workflow. This turn did not install that bundle.
- Only the web unit optionally reads `/etc/careertrellis/auth-v2.env`. Neither
  worker unit references it. Existing native systemd identity/capability controls
  are unchanged. Verify actual units/drop-ins and installed bytes before live use.

All operation inputs below live under `/etc/careertrellis`, with root ownership,
mode 0600, one link, bounded size and no symlink or writable ancestor. State is
under `admission-operations/`, root-owned mode 0700. Reads reject ambiguous
systemd/shell forms, duplicates and unknown bundle/metadata fields; no shell
evaluation occurs. Atomic writes fsync file and parent and refuse implicit
overwrite. Retained state/ciphertext is not an automatic retry or rotation permit.

## Installation contract

Execute only in the operator's visible secure terminal, from the exact reviewed
root-owned immutable source and dependencies. Never send a password, key, token,
identity or decrypted bundle through chat, argv, environment, logs or receipts.
The tool generates the broker password internally and encrypts it before DB
mutation; no plaintext transfer file or exported password is used.

First perform the project’s live rediscovery/authority/lock/active-user gates and
obtain explicit installation/maintenance approval. Complete the protected initial
0013-to-0014 sequence below before this credential installer. Do not use the
historical credential-exporting migration/grant wrappers as a fallback.
It is not a provisioner for an empty server and does not replace the initial
migration workflow. Stop/drain authorized lifecycle callers separately; no helper
silently stops processes, restarts services, deploys code, or triggers backups.

1. **DB host, `db-install`.** Prepare `admission-install.json` with exactly
   `recipient` (verified age X25519 public recipient), `source_cidr` (one host),
   `database_host` and `transport` (`wireguard` or `tls`). The WireGuard choice is
   restricted to the existing reviewed tunnel coordinates, not arbitrary private
   plaintext routing. The native CLI verifies PostgreSQL 18, the `careertrellis`
   database and main-cluster data directory. It requires the restricted broker
   NOLOGIN with no password, current 0014 head, no direct broker table privileges,
   no public-schema creation privilege and quiescent lifecycle callers.
2. The installer prepends one managed HBA block before broad rules, preserving
   all unrelated bytes. It refuses existing markers/includes, writes the exact
   validated main-cluster HBA file, checks parse errors and requests reload, never
   PostgreSQL restart. TLS uses an exact-source `hostssl` allow; both transports
   explicitly reject other broker TCP and local connections. The original HBA
   bytes and attempt phase are retained privately. The generated credential is
   installed as a SCRAM verifier with **NOLOGIN retained**. Its age-encrypted
   transfer bundle is `admission-credential.age`; no credential hash is printed.
3. **DB host, `db-enable-login`.** Explicitly enable only the already-installed
   broker after inspection of the successful installation phase. Policy must be
   unchanged and lifecycle callers quiescent. This does not enable signup or v2.
   An uncertain partial install is refused, not repaired by rotating credentials.
4. Transfer only the encrypted bundle to the same protected filename on the web
   host through the separately approved secure transfer. The web host's existing
   `credential-transfer.agekey` remains root-only. No transfer was performed here.
5. **Web host, `web-install`.** Prepare `admission-web.json` with explicit
   `issuer`, `generation`, `key_id`, integer `session_seconds` (60–86400), and
   optional nonempty `audience`. The installer validates these against the
   application's provider/session constructors and existing WorkOS settings,
   without calling WorkOS. It requires all three services inactive, rejects web
   names in shared/worker or existing web files, decrypts in process and verifies
   exact runtime/broker target agreement. TLS requires `verify-full` and a CA;
   mandatory broker authentication and closed availability must pass before the
   file is published. Distinct flow/envelope keys are generated locally, with
   `CAREERTRELLIS_AUTH_V2_ENABLED=0`. No existing auth file is overwritten.

HBA parse/reload success is not proof of live source restrictions. Before accepting
installation, verify actual positive login, wrong password, other database,
other source, local socket and non-TLS denial as applicable, plus WireGuard/TLS
identity and effective systemd delivery. Keep ingress closed. Session-v2 policy
arming, forced sign-in and public signup remain separately approved transitions;
the installer never invents a cap/realm/domain or turns on the adapter.

## Protected initial bootstrap and migration

### Bounded transport verifier (approved implementation, not live acceptance)

The protected launcher now supports `web-verify-transport <UUID>` and
`db-verify-transport <same-UUID>`. Bind both to the same reviewed immutable
**operational source commit** and verify installed bytes. This operations-only
addition does not bump the application build or authorize moving release links.
The existing application candidate and initial-migration target retain their
separate exact binding. Do not edit either prepared immutable release in place.

Run only after initial migration and broker/web installation succeed, while
web/search/erasure remain stopped and signup/session-v2 remain disabled. Execute
web verification first, let every connection close, then DB verification; do not
run the two concurrently. Only a UUID is passed as input; no credential is typed,
exported, printed or transferred by these commands.

- Web rereads protected installed configuration, requires the fixed approved
  WireGuard destination, decrypts the retained age ciphertext in process and
  compares it with the installed broker credential. It verifies wg0 routing and
  actual database/source/server identity, successful closed availability before
  and after probes, explicit wrong-password rejection, and explicit HBA rejection
  for the other database (`postgres`, whose existence DB verification checks).
- DB requires completed initial/install phases, enabled restricted broker,
  unchanged default policy, revision0014, quiescent callers and exact installed
  HBA bytes/parse success. Real no-password psql probes must receive explicit HBA
  rejection from localhost TCP and the local Unix socket. This is an unauthorized
  **loopback-source** test, not a claim of testing an independent remote host.
- Missing password, network/timeout/TLS failure, missing database, unexpected
  acceptance, unknown/localized diagnostic text and mixed failure messages all
  fail closed. PG18/libpq may omit SQLSTATE during connection failure; only the
  bounded known English rejection forms are accepted internally. The exact
  `/dev/null` password-file warning is recognized but never counts as rejection.
- Root-only receipts are `admission-operations/transport-web-<UUID>.json` and
  `transport-database-<UUID>.json`. They record operational source, UUID, UTC
  start/finish, elapsed seconds and fixed case names; successful execution prints
  the receipt SHA-256. No credential/configuration fingerprint or raw error is
  retained. Existing attempt files cannot be reused. Failure records incomplete;
  interruption may leave started. Neither is success or automatic retry authority.

Require both receipts `ok`, matching UUID/source, fresh timestamps and matching
digests before accepting this matrix. This is not the entire installation gate:
independent trusted WireGuard peer evidence, effective web-only systemd delivery,
live application verification and the remaining rediscovery gates still apply.
The new verifier is deliberately bounded to the approved WireGuard route. A TLS
deployment needs a separately reviewed certificate/non-TLS-denial procedure;
non-TLS traffic inside approved WireGuard is not itself a failed case.

Local rehearsal: nine verifier tests passed, including real PostgreSQL18 correct
and incorrect-password access, other-database rejection, localhost/socket HBA
rejection, and refusal to pass under a deliberately permissive synthetic HBA.
Composed tests cover protected-input/route/age mismatch, installed phase, CLI
source binding, receipt conflict/incomplete state and network-failure disposal.
The dedicated database is loopback-only, tmpfs, labeled and auto-removed; every
test uses synthetic credentials. Native Linux protected-file/launcher tests:
14 passed, including both new verb dispatches with inherited secrets scrubbed.

Substitutions remain explicit: local DB tests use synthetic minimal availability,
not another full migration rehearsal; host paths, policy/phase and native file
facts around composed tests are substituted. Age/route responses are substituted
in composed web tests. Actual age round-trip, two-host execution, trusted peer and
installed-systemd acceptance remain live gates. No staging operation occurred.

Local candidate added after live preflight found staging on 0013 with no admission
roles. This closes the circular prerequisite in the later-maintenance coordinator;
it does not install credentials, enable v2, restart services or approve an outage.
Run the same reviewed launcher from the same exact root-owned immutable source
release on both hosts. Its fresh environment and isolated Python interpreter strip
inherited database/provider/Python overrides. Operator handoffs remain explicit,
root-only files, not signed remote attestations.

After complete live rediscovery and an approved stop/drain of web, search, erasure
and independent lifecycle callers, use these operations:

1. DB: `db-initial-begin <new-UUID> <exact-source-commit>`. Requires exactly 0013,
   no admission roles/schemas, restricted original caller roles, and no existing
   initial/install/maintenance attempt. Retain `admission-operations/initial.json`
   with original login flags, source/head/commit/attempt and DB-host boot identity.
   Under maintenance and Alembic advisory locks, atomically recheck the snapshot,
   execute the protected bootstrap SQL and set web/search/erasure/broker NOLOGIN.
   Both new roles remain passwordless. No policy function is assumed to exist.
2. NOLOGIN is not session termination. A surviving caller leaves
   `bootstrapped_fenced` and no quiesced receipt. After the actual owned connection
   is closed, `db-initial-quiesce <same-UUID>` explicitly reconciles the same boot,
   source, empty admission schemas and complete fence. It neither kills sessions
   nor silently restarts bootstrap. A prepared/uncertain attempt cannot be replaced
   by a fresh attempt; failed bootstrap rolls back its SQL but retains evidence.
3. Transfer `admission-operations/database-initial-<UUID>.json` to the web host's
   `admission-operations/database-initial-quiesced.json`, preserving protected
   ownership/mode. The initial receipt contains phase `initial_quiesced`, UUID,
   exact commit, source 0013 and head 0014. Regular maintenance receipts cannot
   substitute for it.
4. Web: `web-initial-migrate <same-UUID> <same-source-commit>`. Require inactive
   web/search/erasure units, clean worker files, matching receipt, restricted
   migration principal, original 0013 and all four caller roles fenced. Read both
   protected runtime/migration files in-process; require matching host, port,
   database and TLS options. Query-key overrides for host/hostaddr/dbname/user/
   password/service/passfile/options are rejected; only scalar TLS mode/CA options
   are allowed. Production uses the known WireGuard target or an explicit private
   verify-full/CA target. The test-only loopback seam is unavailable through CLI.
   Normalize one psycopg URL for both Alembic and grants; never export it or pass
   it to a child process. Before migration, retain `web-attempt-<UUID>.json`.
   Run actual Alembic to exactly 0014, runtime grants and complete default-policy
   verification. Only then publish `web-<UUID>.json`, phase `initial_migrated`.
   Failure after migration but before grants retains the fence/start marker and
   cannot be retried simply because head is already 0014.
5. Transfer that success receipt to the DB host's
   `admission-operations/web-initial-migrated.json`. DB:
   `db-initial-finish <same-UUID>`. Require the same boot/source/receipt, exact0014,
   no callers, a restricted passwordless NOLOGIN broker, and the entire untouched
   default policy: pilot, signup closed, adapter false, session floor1, maintenance
   false, cap1, TTL600, generation0, realm unconfigured. Under advisory/lifecycle
   locks restore only the captured original web/search/erasure login flags.
   Services remain stopped; the broker remains NOLOGIN. A lost final acknowledgement
   can reconcile only the same success receipt and exact restored state, without
   repeating SQL mutation. Policy drift, reboot or mismatched receipts fail closed.

An unresolved initial attempt blocks credential installation and ordinary later
maintenance. No initial operation changes service state, HBA, user/profile data,
release links or provider configuration. Once it completes, the separately
approved credential installation sequence above can proceed while callers remain
stopped/drained; it still does not activate signup or sessions.

At this candidate revision, `migrate-postgres.sh` and
`apply-postgres-runtime-grants.sh` are fixed-error refusal entry points. They do
not read credentials or acquire mutation locks, even for an older target. The
legacy cutover coordinator rejects an admission release before credential reads,
pending-evidence writes, daemon reload or service stops. Historical immutable
releases remain unchanged and are not an approved workaround. New SQLite cutover
through this candidate is intentionally unsupported; preserve historical receipts.

## Maintenance coordinator contract

Use the launcher from the exact source release with these non-secret operations:

| Host/operation | Required result |
| --- | --- |
| DB: `db-begin <new-UUID> <target-40-character-commit>` | Signup must already be closed. Persist the original policy, role login state, revision and DB boot identity; set maintenance and fence runtime/search/erasure/broker with NOLOGIN. Require zero surviving lifecycle sessions. |
| DB: `db-quiesce <same-UUID>` | Explicit reconciliation after an owned client was stopped. Require the same boot and exact persisted fence, then recheck quiescence; do not kill sessions. |
| Web: `web-migrate <same-UUID> <same-target-commit>` | Require stopped services and the transferred matching DB receipt, then independently verify the live role/policy fence. Run real preflight, Alembic and grants using the restricted migration principal from a parsed protected file. Check head under schema-owner role and retain the result. |
| DB: `db-finish <same-UUID>` | Require the transferred matching successful web receipt, exact head, same DB boot, unchanged policy except maintenance, restricted roles and zero callers. Restore only original role login flags and clear maintenance. Services remain stopped and signup remains closed. |

The coordinator takes existing maintenance/migration locks plus an operation lock
**on each host**. It does not pretend a file descriptor is a distributed lock and
does not automatically SSH. Durable database NOLOGIN/policy fences bridge the
operator-controlled host handoffs. The migration principal remains the one
approved connection path; independent operator/import clients must be quiesced.
Database lifecycle/advisory locks protect policy comparisons and release, and
release also checks the Alembic lock. Unexpected current state fails closed.

Transfer the root-owned DB receipt
`admission-operations/database-<UUID>.json` to the web host's fixed
`admission-operations/database-quiesced.json`. After successful web migration,
transfer `admission-operations/web-<UUID>.json` to the DB host's fixed
`admission-operations/web-migrated.json`. These are explicit trusted-operator
handoffs, **not signed remote attestations**. They contain only phase, attempt,
source commit and, for success, Alembic head. Verify both hosts/attempts before
transfer; neither the CLI nor a remote path is a substitute for approval.

An unresolved DB attempt blocks a fresh attempt. NOLOGIN never terminates an
existing connection; a blocked begin retains the fence and `db-quiesce` can
continue only after the actual connection is gone. A web migration-start marker
prevents blind reruns after uncertainty. A DB finish whose acknowledgement was
lost can reconcile only the complete expected restored state plus the same
accepted web receipt, without repeating mutation. Reboot, policy drift, mismatch
or an uncertain migration requires protected-state inspection and a separately
reviewed recovery decision. No state file, lock, receipt, account, or backup is
removed to make a gate pass. There is no automatic legacy rollback or session-floor
lowering. Future signup reopening and service restart require separate approval.

## Verification and remaining gate

New tests cover both real worker entrypoints before side effects; synthetic
PostgreSQL atomic profile creation, targeted repair and full profile preservation;
actual lifespan/HTTP first save; real NOLOGIN/reconnect/quiescence, coordinator
preflight/Alembic/grants, receipt mismatch/replay and policy preservation; SCRAM
authentication and mandatory web-installer proof; native root POSIX file
permissions, symlinks/hardlinks, size/parent checks, no-clobber replacement,
partial HBA-install failure and no implicit rotation; and the operational guard
for both current deployment and a legacy rollback target.

Substitutions are explicit: local PG uses synthetic identities/credentials and
loopback, not the live tunnel. Native protected-installer tests use synthetic SQL
and encrypted-byte transport substitutes; the web-installer SQL test substitutes
native files/service inventory and physical DB routing. Real `age` binary transfer,
full two-host CLI execution, main-cluster reload, effective systemd drop-ins and
live privacy/health verification remain **host acceptance gates**, not claims made
by these tests. No real credential installation, profile repair, deployment,
invitation, push, PR, Actions run or backup/restore occurred.

## Final execution receipt

All commands ran locally on the candidate worktree with read-only configuration
mode; PostgreSQL URLs selected only the guarded disposable test server.

| Gate / command | Result |
| --- | --- |
| `python -m unittest test_worker_admission_boundary` (focused trigger and legitimate controls) | 2 tests passed; nested cases cover both workers, enabled/disabled, all current/future names and blank/whitespace/zero/secret values. |
| `python -m unittest test_p0_regressions.py test_object_store.py test_object_import.py test_stored_objects.py test_pilot_acceptance.py test_pilot_acceptance_receipts.py test_account_choice.py test_provider_session_candidate.py test_workos_rehearsal.py test_signup_http.py test_worker_admission_boundary.py test_admission_operations.py` | 524 tests, 34.902 seconds, OK with 17 platform/explicit-fixture skips. Includes native Edge and loopback HTTPS startup/clean shutdown. |
| `python -m unittest test_signup_rollout test_admission_lifecycle test_provider_session_postgres test_signup_integration` | 89 tests, 38.655 seconds, OK with one unchanged native-bootstrap test skipped because its separate container contract was not selected. Eight rollout scenarios ran. |
| `python -m unittest test_postgres_rls` | 57 tests, 5.501 seconds, OK, including legacy environment-based migration/grant compatibility. |
| Native root Linux, network-disabled: `python -B -m unittest test_admission_operations` with the explicit disposable delivery fixture | 10 tests, 0.510 seconds, OK with no skips. Native tests cover the six checks skipped on Windows; no host files were mounted writable. |
| `python -m compileall -q .`; `bash -n` on the new launcher/guard and changed deployment/rollback scripts; `git diff --check` | Passed. |

One earlier combined PG run crossed a real fixed-minute budget boundary and
correctly received a newly available budget. The test now avoids starting in
the final ten seconds of a minute; it still calls the real SQL function and
asserts unchanged production limits. The final combined run above passed.
Fixture corrections also supplied the repository's complete profile-update
shape, used raw SQL for a synthetic SCRAM verifier and selected the existing
schema-owner role for post-migration head inspection. None lowered a live gate.

The worker security outcome is **fixed locally**: the original unexpected setting
is rejected before any worker side effect; alternate/blank/future settings and
file-parser forms also fail. Legitimate worker entrypoints still completed real
synthetic DB claims, and existing/default-off web behavior remained intact.
The source review and native legacy-target guard test close the candidate-review
finding without relying on a newer rollback target.

The final disposable PostgreSQL container identity, label, tmpfs/auto-removal,
loopback binding and absence of host mounts were verified. Residual test-role,
schema, extra-database and HBA-error counts were zero. The exact container was
stopped and auto-removed; only disposable synthetic data was discarded and is
not recoverable or needed. Native test containers auto-removed themselves.
No application data or backup was removed. `.codex/config.toml` remains present
and Git-excluded, existing local work is preserved, and nothing is staged.
No commit, push, PR, deployment, live repair or live credential change occurred.

## Live host preflight — 2026-09-08 14:42–14:44 UTC

Read-only host acceptance discovery, not installation or successful transfer.
The operator signed in to DigitalOcean; its inventory confirmed both staging
host names and public/reserved addresses. SSH used the existing known-host keys
with strict checking, batch mode, no agent forwarding and no forwarded ports.

- Web current release: `ebf372f7988a9b36e90969710ee60a0914d1272b`;
  previous: `f4b08de19947f2200f82cff6cdac3292b027f1d9`.
  The authority helper returned `postgres-r2-authoritative`. The unauthenticated
  loopback `/api/version` request returned 401; no version payload was obtained
  and no authentication boundary was bypassed.
- Web, search and erasure services were active/running. Their loaded
  `EnvironmentFiles`, fragment paths and hosted-authority drop-in paths were
  inventoried without printing environment values. The loaded web configuration
  did not include `auth-v2.env`; neither worker listed that file. This is not a
  complete installed-byte/drop-in or process-environment verification.
- `/usr/bin/age` existed on both hosts. The web transfer identity existed as
  root-owned mode 0600 with one link; its contents were not read. The web
  admission ciphertext, `auth-v2.env`, and fixed installed standalone worker
  guard were absent. Identity pairing and encrypted transfer remain untested.
- PostgreSQL was 18.4, database `careertrellis`, cluster main data/HBA paths as
  expected, revision **`20260903_0013`**. Neither admission role nor the admission
  policy table existed. HBA parse-error count was zero.
- PostgreSQL SSL was enabled. Seven aggregate lifecycle connections all used
  TLS from WireGuard source `10.70.0.1`; the web route to `10.70.0.2` selected
  `wg0`. This proves observed encrypted connections, **not** certificate identity
  validation, broker admission rules or the required negative connection matrix.

Execution stopped before mutation. The installer requires 0014; the coordinator
also requires policy/functions created by 0014 and therefore cannot bootstrap
this live 0013 database. Independent read-only source review and primary-agent
inspection confirmed that the existing initial `migrate-postgres.sh` still
shell-sources/exports the migration credential. It is not an acceptable fallback
under the current no-secret-environment operations contract. A protected initial
bootstrap/migration/grants path must be implemented and verified first.

The candidate also remains uncommitted, so the required immutable source binding
does not yet exist. Before execution, establish an exact reviewed local commit
and installed-byte manifest, finish applicable live rediscovery, and coordinate
approval for stopping/draining web, search and erasure callers. Do not use a
directory named after the old HEAD as a binding for modified candidate bytes.
No service was stopped, no role/schema/HBA/configuration was changed, and no
credential was generated, transferred or installed. No backup, restore, workflow,
commit, push or PR occurred. Existing work and protected configuration remain.

## Protected initial-path local verification — 2026-09-08

Outcome: **implemented and verified locally**, not installed or executed on
staging. This candidate's standalone migration/grant shell entry points no longer
read or export credentials: native execution returns a fixed refusal without
evaluating the supplied environment file. The supported web coordinator reads
protected credentials directly, normalizes one URL and runs Alembic/grants
in-process. Real upgrade tests forbid subprocess creation across that boundary
and assert unchanged `os.environ`. The actual shell launcher was separately
exercised on native Linux with inherited synthetic PG/migration/Python overrides
and an isolated stub runner; none reached the runner. The stub proves launch
environment only, not a database operation.

Fresh independent investigation identified the legacy cutover caller and initial
policy circular dependency. The one fresh candidate review identified libpq query
overrides bypassing superficial URL identity checks. A test using the installed
SQLAlchemy psycopg dialect reproduced the override. The shared protected reader
now rejects those keys in both runtime/migration URLs, requires target/TLS
agreement, and supplies the same normalized psycopg URL to grants and migration.
Legitimate WireGuard, explicit private verify-full/CA forms and the `postgresql`
driver alias remain valid in local tests. Real certificate identity is not proven.
The reviewer reported no additional concrete issue in its inspected snapshot;
it did not independently reread the later target-validation correction. The
primary agent verified that correction directly and through the final focused
and combined tests. This was one candidate-review cycle, not an immutable-commit
or full-repository security scan.

Changed this follow-up: initial phases and shared URL validation in
`backend/admission_operations.py`; launcher verbs; refusal-only migration/grant
wrappers; early admission-target cutover refusal; new initial tests and native
launch/refusal tests; existing maintenance fixture and two static wrapper-contract
tests; this runbook, two historical runbook notices and backlog status. Existing
prototype/runtime edits were preserved, not treated as newly authored work.

| Final gate | Result |
| --- | --- |
| `python -m unittest test_admission_initial` | 11 tests, 7.102 seconds, OK; real initial upgrade and target-override rejection. |
| `python -m unittest test_admission_initial test_signup_rollout test_admission_lifecycle test_provider_session_postgres test_signup_integration` | 100 tests, 46.522 seconds, OK, one unchanged historical native-bootstrap fixture skip. All three test-URL selectors pointed only at the disposable synthetic server. Includes actual application lifespan/readiness/profile flow and worker entrypoints with provider substitutes. |
| P0/object, pilot acceptance/receipts, account choice, provider candidate, WorkOS rehearsal, signup HTTP, worker boundary, admission operations and final-cutover regressions | 554 tests, 29.040 seconds, OK with 30 platform/explicit-native-fixture skips. Native Edge synthetic HTTP flow ran and shut down cleanly. |
| `python -m unittest test_postgres_rls` | 57 tests, 6.747 seconds, OK; tenant isolation and migration/grant API compatibility. |
| Network-disabled root Linux, read-only source: `python -B -m unittest test_admission_operations`, explicit native fixture | 13 tests, 0.526 seconds, OK, no skips; descriptor protection, no-clobber, current/legacy delivery guard, retired entry-point refusal, early cutover refusal and clean launcher environment. |
| `python -m compileall -q .`, individual `bash -n` for launcher/migration/grants/final-cutover scripts, `git diff --check` | Passed. No protected files staged. |

Earlier failures were diagnosed, not waived: the synthetic administrator adapter
needed psycopg literal-percent escaping for bootstrap `format('%I')`; a held test
connection needed a dedicated NullPool instead of disposing its still-checked-out
pool; and two static tests needed to recognize retired wrappers as nonmutating
refusals. A native test module initially imported unavailable SQLAlchemy; its
stdlib-only native cases were separated without substituting PG tests. An early
combined PG invocation omitted two fixture selectors and skipped those suites;
the final 100-test invocation supplied all selectors. No production gate was
relaxed to address these test/setup issues.

Proof boundaries: actual SQL, Alembic and grants ran on PostgreSQL18 with synthetic
profiles/roles. Windows tests substitute root file ownership, service inventory
and DB-superuser transport; native tests stub only the shell launcher payload.
Full two-host root CLI execution, real age transfer, installed-byte/systemd
delivery, HBA/TLS denial matrix and deployed/live behavior remain host acceptance
work. No systemd identity/capability controls or application runtime/UI bytes
changed in this follow-up. No staging service, provider, account, schema or
credential changed; no deployment, commit, push, PR or Actions run occurred.

Cleanup: the exact labeled tmpfs/auto-remove PostgreSQL container and absence of
host mounts were verified. Residual fixture-role, schema, extra-database and
HBA-error counts were all zero. That container was stopped and auto-removed;
only disposable synthetic data was discarded. Native containers auto-removed
themselves. Local `.codex/config.toml` remains present and Git-excluded. No
application data or backup was deleted, and no real secret was read into chat
or tool arguments.

## Release .71 binding and maintenance coordination — 2026-09-08

The operator requested an exact local release commit followed by coordination of
the approved two-host maintenance window. Candidate build is `20260908.71` in
`backend/version.py` and both local launchers, based on hosted `web/main` commit
`184b34b397dc1fb5458a70e387b76658ac479ad6`. The release commit containing this
section is the source binding; obtain its full hash from Git, not this document's
historical live pointers. No push or PR is permitted until that exact candidate
has deployed and passed live verification.

Final preparation corrected two stale launcher build references and added the
admission launcher and worker guard to the release preparation executable list.
The native regression executes that chmod fragment and final permission
normalization, proving both helpers end as mode 0750. Installation still requires
root ownership and installed-byte verification on the actual hosts.

- Final local regressions: 557 tests in 27.706 seconds, OK, 32 platform or explicit
  fixture skips. This run included the prototype entry-point checks but did not
  select its isolated PostgreSQL fixture. Earlier PostgreSQL receipts above
  remain the database evidence; no database implementation changed in this step.
- Native network-disabled Linux: 14 admission tests in 0.563 seconds, all passed.
- Python compileall, Bash release-script syntax and Git whitespace checks passed.
- A separate real Uvicorn loopback smoke used only a fresh temporary SQLite
  database and scrubbed provider configuration. `/api/version` returned .71,
  `/` and `/health/ready` succeeded, signup runtime stayed disabled, and the
  server stopped cleanly. Synthetic temporary data was removed automatically;
  no user data was touched. The native Edge signup-form regression also passed.

### Coordinated window, not an immediate outage

Confirm an operator-attended start time in America/New_York. Reserve a 60-minute
planning allowance after release preparation; this is not a measured duration or
an automatic reopening deadline. Before announcing an outage, prepare and verify
the immutable source/dependencies on both hosts without changing current/previous,
install/verify the reviewed operational guard/deploy/rollback bundle separately,
and resolve explicit installer transport and web metadata in the secure terminal.
`--prepare-only` is not an activation or complete live-readiness check.

At the agreed start, repeat the mandatory trusted-inventory, fingerprint/egress,
release/API version, authority/lease/lock, database/RLS/revision, backup/WAL,
provider restriction, queue/user, capacity/port and receipt-chain discovery.
Use only sanitized aggregate user/work counts, not identities or user content.
If anyone is active or work cannot drain, defer the outage and coordinate again;
do not terminate users or jobs merely to meet the planned start.

1. Publish the agreed maintenance notice through the existing operator helper;
   then stop web production, drain and stop search/erasure and independent
   lifecycle callers. Prove quiescence before beginning the initial sequence.
2. Execute the initial DB fence/bootstrap, protected DB-to-web receipt handoff,
   web 0013-to-0014 migration/grants, protected web-to-DB receipt handoff, and
   initial finish against the same full source commit. Keep services stopped.
3. Execute the protected broker/HBA installer, explicit broker-login enablement,
   age ciphertext transfer and web installer. Keep signup closed and v2 disabled.
4. Verify actual permitted/denied connection cases, selected transport identity,
   web-only systemd delivery and installed bytes. Activate the exact .71 runtime
   only within the agreed stop/start scope; verify default-off pilot sign-in,
   existing profile preservation, worker/readiness/health/privacy surfaces and
   retain sanitized receipts before declaring service restored.

No secret prompt may be started in a background tool terminal. Supply the exact
reviewed command only after preflight and use the operator's visible secure
terminal for any protected input. An unexpected phase, failed migration/grant,
receipt mismatch or lost acknowledgement pauses execution with evidence/fences
retained; inspect the original operation before any retry. Do not automatically
restore a legacy application/schema, restart services with uncertain state, or
open signup/session-v2. No precautionary backup, restore, cleanup cycle, provider
cost change, GitHub workflow, push or PR is part of this coordination.

At local release preparation, staging remained untouched. Actual two-host
installation, encrypted-transfer, HBA/TLS and installed-systemd acceptance gates
remain open, as does confirmation of the window's start time.
