# SEARCH-003 web binding and compatible recovery — October 10, 2026

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.

Owner: CareerTrellis maintainer. This is the current release/recovery handoff;
linked earlier receipts remain historical evidence. Full provider acceptance remains open.

<a id="provider-acceptance-current"></a>

## Current status: build 20261010.85 deployed; live acceptance remains partial

Successor `dfbb17d512e835538947c746f4840c17a109686c` / build `20261010.85`
completed coordinated activation on October 10 at 12:05:26 PM EDT (16:05:26 UTC).
The activation receipt (local-only evidence: `../../artifacts/provider-acceptance-20261010/activation.jsonl`)
reports `target_healthy` with configuration unchanged. The
live observation (local-only evidence: `../../artifacts/provider-acceptance-20261010/activation-observation.json`)
binds all three app services to the successor working directory, active with zero
restarts; readiness reports build 20261010.85. Caddy stayed active with its prior PID.
Historical previous remains 4095417. No PR or push occurred.

The user's selected routing is now deployed: Serper is the primary web-discovery
source, with Yahoo/Bing retained as fallback when managed discovery fails. Employer
feeds and existing request/credit limits remain. Tests distinguish unavailable or
failed managed discovery from a successful empty result; bounded results are not
misreported as provider failures. The completed live run supports managed-primary routing, but genuine provider and coverage gaps remain.

The inactive import readback (local-only evidence: `../../artifacts/provider-acceptance-20261010/target-readback-final.json`)
verified source bytes and all 7,947 venv provenance entries before activation. The
WEB import preserved the 640 MiB cgroup cap, zero swap, private network, 600-second
limit and 384 MiB host reserve. Its reported whole-cgroup memory peak reached the
640 MiB cap; this includes file cache and does not establish unconstrained memory
headroom. The original full-build guard was not lowered. The
Linux verification (local-only evidence: `../../artifacts/provider-acceptance-20261010/receipts/verification.json`)
passed: 825 tests run, 4 skipped, plus syntax/shell/systemd checks and isolated
build-85 startup, dashboard and graceful-stop checks. The
Windows verification (local-only evidence: `../../artifacts/provider-acceptance-tests-final-20261010.json`)
ran 483 tests, 2 skipped, with no failures; compile and diff checks passed.

The version-2 audit review (local-only evidence: `../../artifacts/provider-acceptance-20261010/release-tools/terminal-installer-verification.json`)
accounts narrowly for the one terminal envelope created by the earlier approved
search. Exactly one marker, exact-prefix, terminal and exact-approved-envelope row
must agree; saved markers, active work, due/near-due schedules, oversized values
and unknown statuses must remain zero. Nonce, freshness, global visibility, schema
and pinned audit-source checks remain. This does not generally allow existing title
markers. Compatible recovery `40ff10aaa8306c2b840387f24285795626dfb640` / build
20261010.84 is retained and reverified, but was not exercised by this successful
activation. Do not replay either completed coordinator claim.

<!-- provider-acceptance-result:start -->
The single authenticated Edge run completed at 12:11:11 PM EDT (16:11:11 UTC):
terminal UI and persisted counters (local-only evidence: `../../artifacts/provider-acceptance-20261010/live-search-result.json`).
The approved title alternatives and `US remote` were unchanged; save-search was off
and no reminder was created. It found 25 candidates, scored 2, matched 2 at 40%, and
saved 0 new jobs. Failed checks fell from 10 in the prior run to 1; five checks were
empty and all four provider groups retained honest incomplete-coverage notices.
Each provider recorded two shared 10-result pages. No legacy fallback, unusable
response, search-error or false candidate-limit counters appeared in the latest
expanded records. This supports the reviewed managed-primary routing; it is not
an independent provider request trace or proof of exhaustive search coverage.

Greenhouse still recorded one unrecovered source failure, one access challenge
and nine stale postings. Lever and SmartRecruiters recorded employer page limits;
SmartRecruiters also recorded four request/time limit events. Existing caps remain.
Full provider acceptance is therefore open. Next bounded work is to address the
remaining Greenhouse access failure and evaluate feed/page coverage within the
existing caps; no additional paid search was submitted in this turn.

The fresh Edge tab recovered sign-in and a final viewport screenshot was captured
in the conversation; the old detached tab and in-app sign-in cause remain unresolved.
No purchase, top-up, save/schedule or live recovery drill occurred. The completed
version2 coordinator must not be replayed: its exact-one-terminal-envelope guard
was bound to the pre-run snapshot and is not a general future deployment contract.
<!-- provider-acceptance-result:end -->

The current WEB resource inventory (local-only evidence: `../../artifacts/provider-acceptance-20261010/activation-observation.json`)
records exact retained release, import stage, upload, Git bundle and coordinator
paths and logical sizes: 475,767,971 logical bytes in the new remote resources; WEB had 35,082,035,200 bytes free at that observation. Keep
current, prior baseline, compatible recovery and diagnostics under CareerTrellis
maintainer ownership. Review duplicate inputs after acceptance and release/recovery
supersession; remote deletion remains separately scoped. The
Docker cleanup receipt (local-only evidence: `../../artifacts/provider-acceptance-20261010/docker-cleanup-execution/complete.json`)
verifies removal of four task-owned containers, their prepared snapshot image and
the task network, freeing subnet allocation 172.21.0.0/16. The shared Ubuntu base
and unrelated MVOC resources remain; MVOC stayed healthy. Physical Docker bytes
reclaimed were not measured. The local packet register (local-only evidence: `../../artifacts/provider-acceptance-20261010/retained-local-register.json`) records a snapshot of 25,017,041 logical bytes in 146 files. Local source, wheel and evidence files are retained
until accepted release/recovery supersession and maintainer review.

Shared Windows helpers were used in this task for bounded reads and discovery,
Git and authorized SSH/native execution, JSON evidence creation/readback, and these
hash-guarded Markdown edits. The
activation packet receipt (local-only evidence: `../../artifacts/provider-acceptance-20261010/release-tools/activation-packet-verification.json`),
version-2 receipt (local-only evidence: `../../artifacts/provider-acceptance-20261010/release-tools/terminal-installer-verification.json`)
and guarded-edit receipts in the provider acceptance packet document actual use.
The interactive SSH driver remains a necessary exception to the buffered native
helper. No manufactured helper operations or savings/prevention-rate claims.
Task-local friction (local-only evidence: `../../artifacts/provider-acceptance-20261010/friction-pending.json`)
remains pending shared-log consolidation.

## Historical build 20261010.83 activation and first search

The phase entries below are historical. The build-85 status above supersedes their
current-release, pending-authentication and no-search statements.

The single authorized search ran through authenticated Edge on October 10.
UI acceptance receipt (local-only evidence: `../../artifacts/offweb-web-import-20261010/live-search-edge-result.json`):
`Data Engineer; Software Engineer - Data`, alternatives enabled, `US remote`;
exact OR preview confirmed, save unchecked and no reminder. One submission only.
The worker claimed and processed the run; terminal UI reported partial results:
25 candidates, 7 scored, 7 above 40%, and 7 newly saved (59 to 66 total).
Five empty source checks, ten failed checks, eight unusable attempts (possibly
 overlapping failures), and four limited provider groups prevent full provider acceptance.
No purchase, top-up, second run, save/schedule, or live recovery drill occurred.
Evidence is authenticated UI plus the earlier deployment binding, not a direct
per-run database/provider trace. Broader SEARCH-003 acceptance remains open.
Edge authentication works; the in-app authentication cause remains unconfirmed.
Viewport capture timed out; documented tab inventory and ID rebind recovered
terminal readback without repeating submission. No screenshot proof was obtained.
Shared helpers supplied real bounded reads, discovery, Git status, JSON receipts
and guarded document edits. No time-saving or prevention-rate claims are made.

The prior authentication notes below are historical; Edge acceptance supersedes
pending-sign-in and no-search statements. Current/recovery retention is unchanged.

Authentication follow-up at October 10, 10:44 AM EDT: the user returned from AuthKit,
but the in-app callback failed immediately. The UI stops its timer on failure;
00:00 is not an ongoing wait. Protected-root navigation redirected to sign-in.
The explicitly approved sanitized WEB journal check (local-only evidence: `../../artifacts/offweb-web-import-20261010/auth-confirmation-journal-result.json`)
found no matching error markers in a bounded 30-minute / 300-entry window; the
service was active with zero restarts. Auth request access logging is disabled,
so this does not identify the rejection or transport cause. A single attended
sign-in in regular Chrome or Edge is pending; no search or callback replay occurred.
The earlier journal approval block is resolved; the authentication issue remains open.
Shared Windows helpers were used for the approved SSH request and native execution,
receipt creation/readback, bounded source reads, and this guarded document edit.

On October 10 at 14:24:16 UTC, candidate
`7576a8c813d0b0a2ee87668dad1ff093594c76c7` / build `20261010.83` completed the
coordinated activation with `target_healthy` and configuration unchanged. The
activation receipt (local-only evidence: `../../artifacts/title-release-coordinator-canonical-20261010/activation.jsonl`)
contains separate fresh nonce-bound global DB audits before and after stopping all
three producers; both found zero active search/erasure rows and zero due/near-due
schedules. The live binding (local-only evidence: `../../artifacts/title-release-coordinator-canonical-20261010/activation-observation.json`)
confirms all three app process working directories on candidate, active with zero
restarts, readiness build20261010.83, and Caddy unchanged. Historical previous remains
4095417. Generic rollback remains forbidden under PostgreSQL/R2 authority.

Both guarded inactive imports passed; the pair readback (local-only evidence: `../../artifacts/offweb-web-import-20261010/pair-readback.json`)
verified every tracked source and 7,947 venv provenance entries per release. Each
import used its own 640 MiB/no-swap/private-network systemd service, 600-second limit,
control-group termination and 384 MiB host reserve. The original full-build floor
was not changed. No droplet upgrade, shared-browser install or unrelated service stop.

The canonical coordinator (local-only evidence: `../../artifacts/title-release-coordinator-canonical-20261010/coordinator.py`)
is frozen at dc54a03db38519e7ff6e2bdeea1349ab0dd290505c9372bf8e5df5435a97366b.
Its only change from the earlier packet pins full Git object IDs in the binary diff;
local7-character versus WEB8-character abbreviations had caused a safe hash refusal
after candidate object import. State was inspected and only missing recovery objects
were imported. Both exact canonical diffs match (local-only evidence: `../../artifacts/offweb-web-import-20261010/git-objects-result.json`).
Twenty-four coordinator tests passed plus one Windows/POSIX skip; both real SSH-loss
paths then passed against synthetic service state (local-only evidence: `../../artifacts/title-release-coordinator-canonical-20261010/complete-readback.json`).
Both exact rehearsal trees were removed after receipt capture and absent-process checks.

Compatible recovery40ff10a/build20261010.84 is prepared and verified. Caught failures
after switching could select that new decoder-compatible correction, never the old
historical release. Activation succeeded, so no live rollback was triggered. A later
operator-directed recovery drill needs its own reviewed transition; never remove or
replay the completed coordinator claim. Synthetic SSH proof is not a live recovery drill.

WEB producer inventory (local-only evidence: `../../artifacts/offweb-web-import-20261010/producer-web-final-result.json`),
DB inventory (local-only evidence: `../../artifacts/offweb-web-import-20261010/producer-db-final-result.json`),
address/access binding (local-only evidence: `../../artifacts/title-producer-db-scope-result-20261010.json`),
and scheduler closure (local-only evidence: `../../artifacts/offweb-web-import-20261010/producer-db-closure.json`)
exclude the reviewed unrelated cleanup jobs from search/erasure producers. Six installed
cleanup source hashes matched; child-table FKs have no upward cleanup cascade, no
cleanup triggers or DB schedulers were found, and old cleanup timers are disabled.
Fresh installed database/object backup monitors both reported ok before activation.

No live search has been submitted. The user reported an ineffective in-app Sign in
button, reproduced without a visible error. The existing guarded `/auth/initiate`
entry reached the normal AuthKit sign-in screen. User sign-in is pending; the exact
one-run acceptance remains `Data Engineer; Software Engineer - Data`, alternatives on,
location `US remote`, existing credits only, no purchases/top-ups. The button cause
is unconfirmed; recovery evidence (local-only evidence: `../../artifacts/offweb-web-import-20261010/browser-login-recovery.json`)
is a workaround, not an app/browser fix or authenticated acceptance.

Remote resource register (local-only evidence: `../../artifacts/offweb-web-import-20261010/remote-resources-after-activation.json`)
records exact current/recovery releases, stages, incoming bundles, logs and coordinator
receipts. WEB retained about33.1GiB free. Retain current/recovery and diagnostics under
CareerTrellis maintainer ownership; review duplicate inputs after authenticated acceptance
and release/recovery supersession. No physical disk reclamation claimed. New import units
are completed/exited; their configuration is bound by receiver/launch/readback receipts.

Windows helper adoption is evidenced by maintained SSH/native calls, bounded discovery
and reads, structured manifests/receipts and hash-guarded handoff/index/backlog edits.
The persistent interactive SSH driver is a deliberate exception because the buffered
native helper cannot carry challenge/response stdin. No manufactured helper operations,
time-saving or prevention-rate claims. Pending friction (local-only evidence: `../../artifacts/offweb-web-import-20261010/friction-pending.json`)
is indexed for shared consolidation; the original failed comparisons remain preserved.

The following sections retain earlier phase evidence; current status above supersedes
historical pending/no-deployment statements.

## Historical bound running release

The read-only WEB receipt at 12:40 UTC binds current
`ec29b0171eabae27c156a332962a80b3037ee7a9`, previous
`4095417909a8b13ad252be9420e422af3662bef5`, and all three app-process working
directories. Readiness returned build `20261007.82`, status200; unauthenticated
`/api/version` correctly returned401. Authority is PostgreSQL/R2. Caddy and all
three app units were active with zero restarts. No activation/pilot marker existed.

Seven inspected live source-file hashes match the exact local Git object. The
installed deploy helper is intentionally older than the source helper: the source
adds a Serper test, while the prior app-only switch preserved installed operational
configuration. Generic activation/rollback must not replace current Caddy/helpers.

Evidence: WEB binding (local-only evidence: `../../artifacts/title-release-binding-result-20261010.json`),
source comparison (local-only evidence: `../../artifacts/title-release-source-binding-20261010.json`),
database backup status (local-only evidence: `../../artifacts/title-release-db-health-result-20261010.json`),
object backup status (local-only evidence: `../../artifacts/title-release-object-health-result-20261010.json`).
Both backup monitors reported ok at the recorded observations; fresh proof remains
required immediately before activation, not these aging snapshots.

## Immutable local pair

- Candidate `7576a8c813d0b0a2ee87668dad1ff093594c76c7`, build `20261010.83`:
  430 full-suite tests, zero failures/errors, five skips; 16 focused browser tests,
  nine audit tests, 302 compiled Python sources. Final actual loopback app reported
  the new build and verified OR preview/form payload with enqueue intercepted.
- New compatible forward correction `40ff10aaa8306c2b840387f24285795626dfb640`,
  build `20261010.84`: 394 tests, zero failures/errors, two platform skips; 303
  compiled sources. Six new tests exercise actual recovery UI and encrypted
  synthetic SQLite backup/restore preserving saved/queued envelopes.

Recovery hides the new-search UI opt-in while retaining decoder and saved reruns.
It is not an API-wide feature kill switch; cached clients and direct requests retain
compatible admission. It cannot repair arbitrary defects shared by both releases.
Neither release is pushed, activated or presented as live-tested.

Evidence: candidate full verification (local-only evidence: `../../artifacts/role-release-full-verification-20261010.json`),
candidate audit/hash receipt (local-only evidence: `../../artifacts/role-release-audit-and-hashes-20261010.json`),
recovery verification (local-only evidence: `../../artifacts/recovery-title-alternatives-20261010/actual-source-verification.json`),
recovery design (local-only evidence: `../../artifacts/recovery-title-alternatives-20261010/DESIGN.md`).

## Preparation and coordination

PostgreSQL/R2 authority forbids the standard rollback helper. The reviewed local
coordinator packet (local-only evidence: `../../artifacts/title-release-coordinator-20261010/README.md`)
uses a new compatible forward correction after a switch, preserving installed
host configuration. An audit refusal before switching restarts the unchanged
baseline without changing release pointers. A nonce-bound read-only DB-host
aggregate audit is required while producers are stopped. Fake-host tests alone
do not prove the Linux/SSH recovery path or complete producer inventory.
The frozen v4 coordinator passed 23 local tests (one POSIX subprocess case skipped),
then all five isolated Linux tests passed with the unchanged 96 MiB address-space
limit: kernel locks, protected files, nonce/pipe refusals, and real SIGHUP/SIGTERM
handling against synthetic service state. The first native run exposed an eager
64 MiB allocation for tiny files; the corrected read uses observed size plus one,
retains the cap, and rejects length/metadata/path changes. Both exact temporary
trees were removed. This is not an actual SSH transport-loss or live-service drill.
Evidence: v4 local review (local-only evidence: `../../artifacts/title-release-coordinator-20261010/local-verification-v4.json`),
failed native test (local-only evidence: `../../artifacts/title-coordinator-native-20261010/native-result-v2.json`),
passing native test (local-only evidence: `../../artifacts/title-coordinator-native-20261010/native-result-v3.json`).
The due and next-five-minute scheduled-search counts must also be zero before
controlled activation. Worker startup can write queues and invoke providers;
the coordinator performs no direct database writes.

Inactive preparation reuses the maintained deployment helper through a hash-bound
task copy. Identical pinned requirements are required; host dependency and shared
Chromium installation/permission changes are omitted. Existing Chromium must pass
all browser tests. All maintained preparation tests, locking and cleanup remain.
Pip cache and logs belong to the exact task stage; no app activation occurs.

The first authorized preparation was refused in initial guards. Readback found
1,271,271,424 bytes available RAM against the unchanged1,342,177,280-byte floor.
The exact candidate stage, release and systemd preparation unit were absent.
No upload, dependency installation, service stop, pointer change or remote cleanup
occurred. WEB free disk was36,964,982,784 bytes (about34.4GiB); memory is the blocker.
Refusal (local-only evidence: `../../artifacts/title-candidate-prepare-start-20261010.json`) and
guard diagnosis (local-only evidence: `../../artifacts/title-candidate-prepare-diagnosis-20261010.json`).
Do not replay merely because a started/failed command returned; inspect state first.
The final read-only headroom recheck (local-only evidence: `../../artifacts/title-candidate-headroom-recheck-20261010.json`)
found 1,265,528,832 bytes available, still below the unchanged floor. Current remains
`ec29b0171eabae27c156a332962a80b3037ee7a9`; stage, release and preparation unit remain
absent. No preparation retry or unrelated service stop was performed.

## Outside-WEB preparation completed

The approved outside-WEB route prepared the exact immutable pair above on local
Ubuntu 24.04 / CPython 3.12.3 / glibc 2.39, matching the observed WEB runtime.
Candidate passed 818 tests and recovery passed 824, with four skips each; compilation,
shell and systemd checks passed. Full suites ran with Docker networking detached,
synthetic temporary data, and source hashes checked before and after. No provider run.
Evidence: full verification (local-only evidence: `../../artifacts/offweb-preparation-20261010/receipts/verification.json`).

The package receipt (local-only evidence: `../../artifacts/offweb-preparation-20261010/receipts/package.json`)
binds a 132,106,240-byte source/wheel bundle: two Git archives, 58 hash-pinned binary
wheels and a manifest. Every archived member was read back and hash-verified.
The package manifest retains its original importer recipe hash. The explicit
final importer binding (local-only evidence: `../../artifacts/offweb-preparation-20261010/final-importer-binding.json`)
supersedes only that recipe association with reviewed importer
`b676d6034ba4c445167beed80f094101af1aad401eb4b9a51a6d237d99e34a76`.
No source archive or wheel bytes changed. Fifteen importer tests passed natively.
Review found and fixed canonical release-parent/current-target validation before
any WEB use; both complete rehearsals were repeated against the corrected hash.

Candidate (local-only evidence: `../../artifacts/offweb-preparation-20261010/receipts/candidate-import-measurement-v2.json`)
and recovery (local-only evidence: `../../artifacts/offweb-preparation-20261010/receipts/recovery-import-measurement-v2.json`)
offline imports passed in 120.4 and 119.2 seconds under 512 MiB limits, network none,
with no OOM kills. These include permanent-path venv creation, offline installation,
source and runtime provenance, focused tests and service-user Chromium checks.
The cumulative whole-container memory peak reached the cap during fixture work;
this is a conservative bound, not measured unconstrained working-set demand.
A proposed thin-import budget is 640 MiB plus 384 MiB host reserve, subject to fresh
WEB checks and a reviewed bounded launcher. The old 1.25 GiB full-preparation guard
remains unchanged. No WEB upload, install, service stop, pointer change or outage
occurred during this outside-WEB phase; no maintenance window was scheduled.

Remaining sequence: reviewed uploader/launcher and exact execution manifests;
both inactive WEB imports/readbacks; fresh complete producer and backup evidence;
stopped-producer global audit and coordinated activation/recovery; then authenticated
single live search. The earlier sign-in request remains open. The late-night or
early-morning maintenance fallback is available if the guarded import cannot proceed
with healthy services, but timing and exact outage operation are not yet established.

Shared Windows helper adoption continued in actual bounded source reads, Git exports,
native Docker/Python calls, JSON receipts and this guarded handoff edit. Receipts and
hashes support use; no prevention or time-saving claim. The first importer measurement
and failed test-driver smoke remain historical diagnostics, not current acceptance.
Local Docker resources and retention are recorded in the
cleanup receipt (local-only evidence: `../../artifacts/offweb-preparation-20261010/receipts/cleanup-result.json`).
The earlier no-Docker statement below applies only to the preceding WEB-binding phase.

## Live acceptance scope and remaining work

User selected `Data Engineer OR Software Engineer - Data; US remote`. The candidate
UI requires `Data Engineer; Software Engineer - Data` with title alternatives on,
and location `US remote`. One task-owned live run, existing provider credits only;
no purchases or top-ups. No search has been submitted in this work.

The exact canonical site is https://careertrellis.jhunterj.com/. An existing Edge
tab belonged to another task and was left untouched. This task's in-app tab opened
the sign-in page; operator sign-in was requested. Authenticated acceptance remains
pending. Next gates: adequate unmodified preparation headroom, both inactive native
builds/tests and runtime manifests, native coordinator review/checks, fresh complete
host/producer/backup evidence, coordinated activation, then the single live run.
Broader PostgreSQL saved/scheduled/cancellation/restoration acceptance remains open.

## Windows helper adoption and retained evidence

Procedure index and Windows, acceptance and browser procedures were actually read.
Shared bounded-read/discovery, Git/native execution, SSH request, guarded-document
edit and JSON helpers were used in the real task. Strict project SSH options were
added to the request builder through the maintained native wrapper. Source binding
and guarded-edit receipts provide adoption evidence. One direct rg wildcard-path
bypass failed and was recovered with directory/filter arguments; adoption was not
universal. No time-saving or prevention-rate claim, and no synthetic adoption ops.

Friction note (local-only evidence: `../../artifacts/title-release-friction-20261010.md`) is indexed in the
project friction index for shared consolidation. Retain local release worktrees,
bundles, coordinator/test sources and small receipts until accepted release/recovery
supersession. Test temporary databases, browsers and compilation copies were cleaned;
no Docker resources were created. Exact retained sizes are in the release resource
register (local-only evidence: `../../artifacts/title-release-resources-20261010.json`). Both local worktrees are clean and no root paths are staged. Final Markdown validation found 370 valid local links, zero missing/unknown and nine external links not fetched. Root git diff --check reports trailing-whitespace/CRLF diagnostics across the existing AGENTS.md and backlog changes; those broad unrelated changes were preserved. Candidate and recovery verification receipts remain the source of runtime test evidence. No physical disk reclamation is claimed.