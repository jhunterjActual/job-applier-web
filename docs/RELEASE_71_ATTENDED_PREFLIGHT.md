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
