# September 8 attended preparation — deployment blocked

The operator attended at 10 p.m. EDT and approved online preparation using the
existing WireGuard route and a one-hour future v2 session lifetime. Signup and
v2 remain disabled. No outage, migration, broker credential installation or
application activation occurred.

Both health surfaces were green at the initial 01:59 UTC checks. The exact
application source remains `151f58dddb17c57686de2ba3b6bf4d9c21e4570f`, tree
`9e91194c7842ca63c3541fd0c98bed91fe848f62`. The hash-verified source archive is
retained root-only on both hosts under the matching ops-bundle directory.
The DB source-only release was prepared; no DB operation was invoked. The web
Git object was imported from the verified local bundle without moving checkout
HEAD or pushing to GitHub.

The reviewed web prepare-only helper ran as
`careertrellis-prepare-71-20260909.service`, invocation
`ba38230d37bc49e1b28576477c107371`. It terminated with MainPID0, exit1.
Its 648-test suite ran in 50.539 seconds: one failure and one skip.
`test_account_erasure.AccountErasureRouteTests.test_authenticated_middleware_suppresses_refresh_and_resets_owner_context`
expected HTTP202 but received503. The cause is not yet established; no retry
or weakened gate is authorized by this receipt. OS dependency installation
reported zero upgraded, newly installed or removed packages.

Root-only `prepare.log` SHA-256:
`be12d8bd9bc08ac99f1989320edf122d1b79c2a4572e6bc66f7b660551089ce2`.
The final web candidate directory is absent after helper cleanup, and no
CareerTrellis lock remains held. The live current pointer remains
`ebf372f7988a9b36e90969710ee60a0914d1272b`; web PID316879 was unchanged.

A separate concrete installer blocker was reproduced locally and independently
reviewed: `ProviderSettings` rejects issuer paths other than empty or slash,
while WorkOS documents an AuthKit issuer with `/user_management/<client_id>`.
Correct metadata cannot pass the installer. Substituting a bare API origin
would instead fail exact JWT issuer verification. Existing synthetic tests
configure and mint the same incorrect bare-origin issuer, hiding this mismatch.
Sources: https://workos.com/docs/cli/emulate and
https://workos.com/docs/integrations/supabase-authkit . No real provider token
was read or retained to reproduce this constructor failure.

Correct and verify the issuer contract and diagnose the host regression before
establishing a new exact release binding or resuming the deployment gate.
Installer metadata and installed guard/deploy/rollback acceptance remain open.
The public planned-maintenance notice has not been changed by this preparation.

## Approved correction

The operator approved correcting both blockers and rerunning preparation.
The provider constructor now requires exactly the fixed WorkOS API origin plus
`/user_management/` and its configured client ID. Bare origins, other clients,
other hosts, trailing slashes, query/fragment, credentials and port aliases are
rejected. JWT signature and exact issuer checks remain unchanged. Synthetic
tokens now use the documented client-scoped shape, with explicit signed
mismatch rejection tests. Custom AuthKit issuer domains are not enabled by
this fixed-endpoint candidate; any such configuration needs separate evaluation.

The erasure failure was reproduced without connecting to any database: the
route fixture had omitted its readiness stub, allowing an attempted synthetic
database-engine lookup. The fixture now supplies readiness explicitly, traps
unintended DB access and verifies both success and fail-closed denial. No
production authentication, readiness or erasure guard was changed.

Focused provider/signup tests: 56 passed, including native browser flow.
Erasure module: 38 tests passed with four POSIX-only skips on Windows.
Python compileall passed. Full preparation-suite and target-host results are
required separately before declaring the new candidate prepared.

## Corrected candidate preparation accepted

Exact corrected commit: `e0cd2e31cd5ac101c6cecd9a87c5e5c3562ea7bc`, still
undeployed build `.71`. The exact source was staged on both hosts; web preparation
invocation `25940040ad354a28a76f2a2a6b79e170` terminated successfully, MainPID0,
ExecMainStatus0. Local full suite: 649 tests, 33.266 seconds, 52 platform/fixture
skips. Web preparation suite: 649 tests, 47.299 seconds, one skip. Exact source
bytes and 0750 root-owned release/launcher/guard permissions were verified.
Preparation log SHA-256:
`7305e3ec50056dca2ca36cfc173d13a3e0153e6422df2f2b1bc9b72ae12de856`.

Additional Linux provider-only verification: 40 tests, 3.313 seconds, passed;
receipt SHA-256 `f3641a84f998f91f232fe00446fdc484b3df625fe0522487bbb37555bdb4d83b`.
The earlier additional provider/signup run had one platform error: its explicitly
Microsoft Edge browser case cannot launch on this host, where Edge is absent.
That same browser test passed locally in the 56-test provider/signup suite.
The failed-platform receipt is preserved, not represented as a Linux pass.
No extra browser was installed. Independent bounded review found no concrete
regression in the issuer correction.

Live current/previous pointers and web/search/erasure PIDs remained unchanged;
all three services were active with zero restarts. No shutdown, migration,
broker credential installation, v2 activation, push or PR occurred.
Preparation acceptance does not clear the outstanding installer metadata,
installed operational bundle, live transport matrix or deployment gates.

## Installer metadata and operational helpers

The operator approved `staging-v2-initial` generation, `staging-v2-k1` key ID,
3600-second sessions, no audience and the existing WireGuard route. Public
WorkOS discovery was fetched internally from the configured client endpoint;
issuer and client-scoped JWKS exactly matched the candidate contract. No client
identifier, provider credential or discovery response was displayed or retained
in this document. Root-only `admission-web.json` was created without overwrite.

The existing web age identity remained on its host. Its derived public recipient
was used in a protected plan transferred to DB `admission-install.json`, with
`10.70.0.1/32` source, `10.70.0.2` database host and `wireguard` transport. Both
metadata files read back root:root0600, single-link. No broker password, encrypted
credential bundle or `auth-v2.env` was created. Age encryption/decryption proof
and actual broker source-denial checks remain later installation gates.

Installed the candidate guard first, then deploy/rollback helpers, using atomic
file replacements under the existing maintenance lock. Previous helper bytes
were retained root-only in the candidate ops bundle's `helper-install` directory.
The first preflight incorrectly required mode0600 on the existing maintenance
lock and stopped before any install writes. Its actual root:root0644 state is
non-writable by others; the retry preserved the lock inode and permissions.
No lock was removed. Adjacent helper overrides were absent. The existing fixed
maintenance-lock helper already matched the candidate and was not replaced.

All three installed helpers read back root:root0755, single-link, byte-identical
to candidate source. Bash syntax and standalone worker-file guard passed before
and after installation, without displaying configuration values. Installed
manifest SHA-256:
`6f12c185ee40130ae0f3b94331645c0cf5dea51f45e9be1920654afe257c41d5`.
The manifest and prior helper bytes remain under the exact candidate ops bundle.
Rollback was not executed; PostgreSQL/R2 authority does not permit legacy code
rollback, and this installation is not rollback authorization.

Final route observation remained wg0, source10.70.0.1 to10.70.0.2. Live release
pointers and all three service PIDs/restart counts remained unchanged; the
maintenance lock was released. No daemon reload, service stop/start, database
mutation, signup/session activation, push or PR occurred. The future web-only
auth environment reference is not yet loaded into systemd; that remains part of
the attended application activation and installed-delivery verification.

## Final online checks — outage not started

On September 9 around 02:23–02:30 UTC, the provider inventory again matched both
staging hosts. Strict-known-host SSH checks found PostgreSQL 18.4 at 0013,
zero users seen in the preceding 15 minutes, zero active search runs and zero
unfinished erasures. Seven lifecycle connections all used SSL from the approved
WireGuard source. HBA parse errors and admission-role counts were zero.
These aggregate counts do not independently prove the absence of in-memory
tailoring operations or complete every remaining readiness check.

Current/previous release links and all three application service PIDs remained
unchanged, active with zero restarts. Installed helper manifest and worker-file
guard passed; no CareerTrellis file lock was observed held. Web routing remained
wg0, source10.70.0.1 to10.70.0.2. Capacity was available on both hosts; disposable
web ports55432/55433 had no listener. The signed-in browser displayed the existing
dashboard and planned-maintenance notice, but an authenticated API-version
receipt has not yet been obtained.

Both health documents returned result `ok`, with no failure codes: objects
evaluated at 02:02:41 UTC and database at 02:03:27 UTC. An initial projection
requested the nonexistent `status` field and returned null; reading the actual
`result` field confirmed both results. No health evaluation or backup was rerun.

Final procedure review found no verified live negative-transport execution
artifact in the candidate or installed admission-helper inventory. The installer
implements positive authentication/closed-availability proof; the required
wrong-password, other-database, other-source and local-socket rejection matrix
is still prose plus isolated-test evidence, not an executable live acceptance
receipt. A protected, bounded verifier and its reviewed execution procedure must
be ready before beginning an outage that depends on that gate. For approved
WireGuard transport, non-TLS inside the tunnel is not inherently a failed case.

No service stop, migration, role/HBA mutation, credential installation, release
activation, push or PR occurred. Staging remains online; all prior evidence,
configuration and the planned notice were preserved. Deployment is not complete.

## Approved verifier implementation and isolated rehearsal

The operator approved closing the procedure gap without beginning the outage.
Added protected web/database transport-verification verbs, fixed-error rejection
classification and no-clobber, source/UUID-bound sanitized receipts. The complete
execution order and evidence limits are in `BETA_ADMISSION_OPERATIONS.md`.
This is operations-only; application build `.71` and the prepared runtime binding
have not changed. The new operational source is still local and uncommitted.

Final isolated verifier suite: nine tests, 3.645 seconds, all passed. Real PG18
probes exercised password/database/source/socket rejection and confirmed that
permissive HBA authentication cannot produce a pass. Native network-disabled
Linux protected-file/launcher suite: 14 tests, 0.674 seconds, all passed. Final
P0/object/admission regression set: 405 tests, 8.568 seconds, OK with24 platform
or explicitly unselected database-fixture skips. The separately selected real
transport suite above ran without skips. Compileall, Bash syntax and Git
whitespace checks passed. No protected files are staged.

Rehearsal failures were diagnosed before reruns: a test function needed static
binding; PG inet text included `/32`, requiring `host(inet_client_addr())`;
psycopg prefixes and the exact empty-device passfile warning needed explicit
classification; the Windows CLI fixture needed absolute-path normalization.
No unknown error was accepted merely to make a negative test pass.

Independent bounded review found no remaining concrete blocker after composed
entry-point and failure/receipt tests were added. Real age execution, trusted
WireGuard peer and installed systemd delivery remain live acceptance gates,
not claims made by mocked host facts. Synthetic Docker databases were labeled,
loopback-only, tmpfs and auto-removed; final inventory found none remaining.
Only disposable synthetic data was discarded. No staging state, application
pointer, credential, backup, release build, GitHub branch or PR changed.

## Attended execution — migration complete, broker installation blocked

Verifier ops commit `e2e1b03a7888c843dfd7088912481e2f5059bedb` was committed locally
and prepared separately on both hosts. Application target remains
`e0cd2e31cd5ac101c6cecd9a87c5e5c3562ea7bc`; neither immutable directory was edited
in place, and the ops-only directory was not activated. Source archive SHA-256:
`357fd9cf24175eaa872bd221177da7919281bc9098da91434bb6a287c463aa85`.
Both source-file manifests matched:
`f1be56c5c0822d68b19e86f5fb5b7d17efc32a9723221566bc1324cd4d46532b`.
All316 application and317 operations archive files matched their installed bytes.
Web used a private copy of the prepared application venv;59 dependency metadata
files matched and interpreter prefix/import checks passed. The initial metadata
glob selected no files; the corrected nonempty manifest check is the accepted
evidence. Host-native verifier/protected-file tests:20,0.514s,OK,one Docker-only
skip; root-only test receipt SHA-256:
`7a5e05dac69af1962f608c5dd0ff91cfb6275309d82d71f1d6de6cf7810ce461`.

Fresh provider inventory/fingerprints, web egress, authority activated/recovery
evidence with no lease, source manifests, queues, independent migration callers,
RLS metadata, capacity, ports and locks were checked. Both backup-health results
were green. Latest pgBackRest label was `20260906-031528F_20260908-161908D`, status0;
no backup/restore lock was held and no archive failure was recorded. Next
differential was03:22:50UTC. The two trusted hosts' WireGuard public identities
matched each other's exact allowed peer addresses without displaying keys.
The operator confirmed no search/tailoring/unsaved work and authenticated live
API build `20260906.70`. The operator explicitly approved reusing previously
accepted WorkOS/R2 provider-settings evidence for this bounded deployment,
without changing those settings and with live checks required before reopening.

Outage began **2026-09-09T02:43:51Z** (September8,10:43:51p.m.EDT). The existing
notice helper published in-progress revision `6cf755a0232b4ef780366a84012332e9`,
with an approximately one-hour estimate, not an automatic reopening deadline.
Web stopped first, then idle search/erasure; all three reported inactive,PID0.
Database lifecycle connections, active searches and unfinished erasures were0.

Initial operation UUID: `7fb419fd-3a10-4ade-be8a-1856b8b0d39d`, bound to application
commit e0cd2e3 above. `db-initial-begin`, protected receipt handoff,
`web-initial-migrate`, reverse handoff and `db-initial-finish` all succeeded.
DB-to-web receipt SHA-256:
`611a807d69c78ed52cf1969aed6340c4eb59fa5ae7b213f3186f5704fce3de88`.
Web-to-DB receipt SHA-256:
`a0bd9c67b0034524e69a19c7bd39fbc78c5820b7291e5732fd3411bc167c2482`.
Head is0014, default policy was verified unchanged, captured caller login flags
restored, and initial phase is `released_services_still_stopped`. Broker remains
passwordless NOLOGIN. No service was restarted.

The first `db-install` invocation returned its fixed guarded failure. Inspection
found no `install.json`, no ciphertext, no managed broker HBA rows and no HBA parse
errors. All installer checks before its HBA read passed in a read-only diagnosis.
Actual HBA is **root:postgres0640**, single-link, below postgres-owned0755 config
directories. The installer expects a postgres-owned file; its protected read
rejects that existing ownership. A diagnostic root-owner read also rejected the
postgres-owned ancestors, confirming that simply substituting uid0 is not a fix.
No HBA, credential, file-owner or permission change was made and no installer
retry occurred. Fix and rehearse the explicit HBA file/ancestor ownership contract
while preserving existing ownership, then establish a new ops binding before any
retry. Retain the successful initial migration and all failed-attempt evidence.

Application services remain stopped; current/previous still point to the prior
releases. Do not rerun initial bootstrap/migration, restore a legacy schema/code,
change HBA ownership to evade the guard, or restart under uncertain state. No
application activation, push, PR, cleanup backup/restore or Actions run occurred.

### Approved ownership correction

The operator approved preserving existing HBA ownership, implementing/rehearsing
the correction, and retrying only broker installation. Initial migration is not
to be rerun. The HBA reader now supports root:postgres0640 under postgres-owned
ancestors, with descriptor-bound content/metadata checks. Existing postgres-owned
0600/0640 remains supported; foreign ownership/group, unsafe modes, links and
writable ancestors fail closed. Generic protected-file defaults are unchanged.
Replacement preserves UID/GID/mode, and subsequent transport verification binds
to those recorded values. No ownership normalization or permission weakening is
part of the correction.

Native network-disabled Linux:16 tests,1.015s,all passed, including the complete
installer path against synthetic root-owned HBA and no-clobber replay refusal.
P0/object/admission plus explicitly enabled real PG18 transport rehearsal:
396 tests,19.623s,OK,14 platform/fixture skips. Compileall and Git whitespace
checks passed. Bounded independent review found no concrete blocker. A new exact
ops binding and host-native checks are required before the single approved retry;
these local results alone do not claim a successful live installation.

### Ownership-preserving broker retry accepted

Corrected ops binding: `3ce2c05770b848e22be9105e3e6b9b9006e6a885`, local-only.
Archive SHA-256 `b762419d77a8284c6190035231bf1879e4304a042ab6b6fcecb524d233ad9adc`.
Both installed source manifests matched
`00dff8226c045a2dd14aaa9f560aea24584b9823ff108459ee23a868ff3c3d88`.
The new web ops interpreter uses a separately copied, dependency-checked venv;
source imports/prefix passed and services remained inactive/PID0.

Actual DB-host native tests:16,0.624s,OK,one Docker-only skip. Protected test
receipt SHA-256 `7c614d3299d4963dc99054a996bc57150deb546e703b480d6d9879a91c32ca28`.
The corrected HBA reader and candidate generator passed against the live path.
Before retry, initial migration was finished and both install marker and
ciphertext were still absent. Current HBA bytes were retained root-only as
`hba-before-retry` in this ops bundle; no ownership or permission changed.

The single approved `db-install` retry from this exact ops source succeeded.
Readback confirmed phase `installed_nologin`, a protected age ciphertext, broker
password present with NOLOGIN retained, zero HBA parse errors and unchanged
default-off policy. The install marker's original HBA bytes exactly matched the
pre-retry copy. Installed HBA bytes exactly matched the generated prefix plus
original content, and actual/recorded ownership remained root:postgres0640.
No original bytes were deleted. No migration was rerun and no service restarted.

Broker login enablement, encrypted transfer/web installation, paired transport
verification and application activation are still subsequent steps. Use corrected
ops source3ce2c05 for the metadata-aware verifier on both hosts; do not use its
superseded predecessor for DB verification. Application target remains e0cd2e3.
Staging remains offline at this boundary; no push or PR occurred.

### Broker enabled and ciphertext transferred; web install blocked

On the operator's next approval to proceed, `db-enable-login` from corrected ops
3ce2c05 succeeded. The installed ciphertext was copied into an operator-private
transfer directory and transferred DB-to-web over strict-known-host SCP; no
plaintext or private age identity was transferred. The web copy was validated
as a bounded regular mode0600 file and published into the root-only no-clobber
`admission-credential.age` path. Original and transfer ciphertexts were retained.

The subsequent `web-install` returned a guarded failure; `auth-v2.env` is absent,
and all three application services remain inactive/PID0. No installer retry or
credential regeneration occurred. Read-only constructor checks passed for the
existing cookie key, provider metadata and session settings. A phased diagnostic
passed age decryption, bundle shape and exact runtime-target agreement, then
failed at broker connection. Database readback confirmed broker LOGIN=true but
`CONNECT` on `careertrellis`=false, with PUBLIC CONNECT=false and HBA errors0.

The live database is deliberately stricter than the isolated transport fixture,
which retained default PUBLIC CONNECT. Bootstrap/installer code omitted an
explicit broker database grant. Correct this narrowly for the broker and only
the application database, add fixture coverage with PUBLIC CONNECT revoked,
and preserve the existing credential/install attempt. Do not restore PUBLIC
CONNECT, grant table access, rerun migration, rotate credentials, or restart
services merely to bypass this gate. Paired transport verification and application
activation have not occurred. Staging remains offline.

### Approved broker-only CONNECT correction

The operator approved adding the broker-only database grant, testing with PUBLIC
access revoked, and retrying web installation without credential rotation or
migration. A separate immutable `db-grant-connect <attempt-UUID>` operation now
requires the existing login-enabled installation, completed initial migration,
default-off policy, exact head, restricted role, preserved HBA and native negative
transport checks. Host/advisory/lifecycle locks and transactional snapshot checks
guard the single grant. PUBLIC CONNECT and excess broker database privileges
cause refusal. A protected attempt receipt precedes the mutation; no install
marker, password, HBA, application pointer or service is changed. Future login
enablement grants explicit CONNECT atomically with LOGIN.

The isolated PostgreSQL18 fixture now revokes ALL database privileges from PUBLIC.
It reproduced the correct-password CONNECT denial and verified the guarded grant,
idempotent grant semantics, successful broker connection, unchanged password and
revoked PUBLIC access. Local regression suite:398 tests,13.735s,OK,14 skips.
Native network-disabled Linux:16 tests,0.728s,all passed. Compileall passed.
These are preparation receipts only; live correction and web retry remain pending.

### Broker CONNECT, web installation and paired transport accepted

Reviewed ops binding `4a7bf3c46d3b401616c7bfaefacb22c7c7ff30ab` was prepared on
both hosts without activation. Archive SHA-256
`32485151967a66e2e0c3303f37630b53c4c8fa2206a80832194cdc5ecba6dad5`;
matching source-manifest SHA-256
`08ff8c27d2fccbe17c7a7035696a0f0a30d224c608ecc861c349a5cdaf8039aa`.
Actual DB native test receipt SHA-256
`47002b40bd24985de6a5f45c83cca1cbef07c8d088ded4aabadc1228a61a73a1`.
Independent bounded review found no concrete blocker. The further isolated PG18
test passed with explicit refusal of PUBLIC CONNECT or excess broker TEMP.

Approved recovery attempt `20b5e577-cd96-488d-a2ce-4282789c850e` succeeded;
protected receipt SHA-256
`dbf6bb4578bdec91589afe97842f528b1608644618973cdd6f5b52bfea2c0c9c`.
Web installation then succeeded using the existing encrypted credential. No
migration, credential rotation, HBA rewrite or PUBLIC grant occurred.

Paired transport attempt `0ad67c4e-3c98-4e58-9b7c-47d029f9dd06` succeeded on
both hosts from this ops binding. Web receipt SHA-256
`6208b44452dbbf5d6a4c1b38efae07df1eed46c0813c6d01ff6b133fc825c566`;
DB receipt SHA-256
`76a59c56c5fb6d7ba3c28870cae14c43c932e03ffbd374a248cd4ab364022271`.
Checks proved correct broker transport and rejected wrong password, other
database, loopback source and local socket. Policy and v2 remained default-off.

Exact application and ops source bytes were reread against retained archives.
An initial read-only check used an incorrect parser name and failed without
mutation; the corrected check passed. The exact e0 application web unit was
installed while services remained stopped; its prior bytes were retained.
Loaded EnvironmentFiles were verified as web5/search3/erasure3, with auth-v2.env
only on web, no PassEnvironment or unexpected inline assignments, and matching
reviewed drop-ins. All three services remained enabled but inactive/PID0.

At `2026-09-09T03:14:48Z`, activation of application
`e0cd2e31cd5ac101c6cecd9a87c5e5c3562ea7bc` was dispatched as
`careertrellis-activate-71-20260909.service`. Its root-only activation log remains
under the new ops bundle. Terminal activation and live verification were still
pending at this entry; no commit was pushed and no PR opened.

### Application .71 activated; operator verification accepted

Activation terminated successfully at `2026-09-09T03:15:08Z`, exit0. Current is
exact application e0cd2e3; previous is ebf372f7988a9b36e90969710ee60a0914d1272b.
Outage lasted approximately31m17s from the recorded02:43:51Z stop to readiness.
Activation log SHA-256
`5eca77503838624681f4802787786eaebf105aa7cf22bc093c93cae0a9ebbea2`.

At03:15:52Z both loopback liveness/readiness returned200 with build20260908.71.
Web/search/erasure processes ran from the exact application release; all had zero
restarts. Both workers were idle with matching-build/PID heartbeats5.1s/12.0s old.
Process environments proved the intended distinct PostgreSQL principals, v2=0
on web, and no admission URL or v2 settings in either worker. Protected runtime
receipt SHA-256
`07a8fdb888b30c0ff01a28c1e47ec0e14ccfaa470f805bd7b94d581d1d1ceb3c`.
The subsequent stability readback again showed all three services and Caddy
active with zero restarts. Database readback retained revision0014/default-off
policy, restricted broker CONNECT, revoked PUBLIC CONNECT and zero HBA errors.

Deployed Caddy privacy checker passed:170 records,31 query records,170
representative records, no source/personal/secret disclosure. The maintenance
notice was disabled after successful health checks; readback configured=false,
revisionccd866a05cf74550b85bdbeb8b0ced9b, all8 audit events retained.

After deployment, the operator confirmed the signed-in API reports `.71`, the
existing profile loads, and sign-out followed by sign-in succeeds. This closes
the manual verification gate for the deployed default-off application and
installed operations bundle. It does not prove the disabled v2 sign-in or
self-service registration flows and is not broader-beta approval.

No push or PR occurred; the post-install/activation and operator-verification
closeout remains local documentation pending handoff. No further deployment,
backup/restore operation or provider configuration change was needed to record
this acceptance.
