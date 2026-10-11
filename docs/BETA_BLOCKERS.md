# Beta-opening blockers

## Open beta launched — October 6, 2026, 8:59 PM Eastern

The finite launch checklist below is complete. Public account entry is available
at https://staging-app.jhunterj.com/ without the temporary shared password.
Owner/new-user access, empty-workspace separation, profile persistence and logout
passed; application health and existing backup monitors are healthy. The25-account
cap remains. Optional recovery/factor work, expanded search coverage, OS maintenance
and the4GiB plan upgrade/512MiB reserve remain post-beta backlog work.
See [current launch evidence](BETA_PATH_20261003.md). Older blocked statements
below are historical and do not describe current launch status.

## October 6 — explicit launch scope reduction

The operator explicitly deferred added safeguards, backstops and plans until
after open beta. This supersedes older requirements below that made additional
owner bootstrap/reserve provisioning, optional factor recovery, expanded coverage
matrices or further recovery rehearsals prerequisites. It does not mark those
checks passed or authorize bypassing an installed guard. **Beta is not open.**

The remaining launch checklist is finite:

1. Complete the owner-assisted administrative identity transition and activate
   the prepared application through the existing controlled release path.
2. Verify hosted sign-in, owner access, a new user's account isolation, sessions
   and logout on the activated version.
3. Verify basic search, save and persistence using existing approved sources and
   task scope. Reuse applicable historical functional evidence with current-version
   verification; do not introduce paid searches or another coverage campaign.
4. Verify core application/worker health and continued operation of existing
   backups and alerts, then complete the explicit signup-opening step.

Keep optional TOTP enrollment, factor removal and general-user recovery disabled:
`CAREERTRELLIS_AUTH_V2_TOTP_ENABLED=0`,
`CAREERTRELLIS_AUTH_V2_REMOVAL_ENABLED=0` and
`CAREERTRELLIS_AUTH_V2_RECOVERY_ENABLED=0`. Their additional provisioning and hosted
acceptance are post-beta work. Preserve existing credentials, receipts, backups
and alerts; do not reset counters or silently discard an existing protection.

Broad search coverage remains limited: the recorded six-cell evaluation did not
pass overall. Deferring the expanded matrix is a known launch limitation, not a
coverage pass or a renamed/narrowed marketing scope. Additional paired restore
rehearsals and missed-monitor drills are likewise deferred limitations; retain
the working backup/alert program. Follow-up items are recorded at the top of
[the product backlog](../PRODUCT_BACKLOG.md).

## October 3 — consolidated execution path

After explicit approval, the paired WEB/DB plan refresh passed: authenticated
source/protected-file checks, worksheet authorization and WEB/DB/WEB consistency.
See paired-plan receipt (local-only evidence: `../artifacts/PAIRED_PLAN_REFRESH_20261003.md`).
This was read-only; provider acceptance and service/backup health are separate.

Use [the current beta path](BETA_PATH_20261003.md) for the next work order and
remaining decisions. Historical entries below retain their original evidence;
later passing receipts supersede earlier pending statements only within scope.
The mandatory tracks are identity/admission and recovery acceptance, useful search
coverage, and the backup program. Local adapter tests do not close these tracks.
Continue authorized local preparation and verification without another generic
proceed request; batch actual decisions when the concrete operation is reviewable.

## October 2 — authenticated close-only SSH rehearsal passed

READY/CLOSE completed in103.859seconds with zero application requests and verified
remote/controller and local Windows Job cleanup. Bundled postcheck at21:07UTC:
all three application services active, configured limits unchanged, temporary
unit absent, no same-package retained roots. MemAvailable1,519,583,232bytes;
disk free12,114,063,360bytes. See artifacts/PREPARED_SSH_READINESS_20261002.md.
This closes the close-only transport gate; paired-host networking/observation,
action authorization, maintenance/provider acceptance and coverage remain open.

## October 2 — hosted preparation/import rehearsal passed

The bounded hosted rehearsal reached real worker READY in 118.859 seconds with
zero application requests. Whole-job peak was 681,639,936 bytes (650.063 MiB),
zero swap and zero memory high/max/OOM events. The 1 GiB cap and 256 MiB reserve
were sufficient for this run; this does not establish a worst-case bound.
Runtime copy, historical verification, imports and owned cleanup passed.
Receipt: `artifacts/prepared-import-rehearsal-hosted-v2-20261002.txt`.

An initial early refusal was repaired by querying systemd policy from the parent
before entering the PID namespace; direct child kernel-limit checks remain.
Both attempts verified cleanup. After separate user approval, the post-run
read-only inventory at 2026-10-02 20:04:04 UTC confirmed WEB, worker and erasure
worker units active, with their configured memory limits unchanged. MemAvailable
was 1,530,392 KiB (1,494.5 MiB), above the 1,280 MiB preparation floor by 214.5 MiB.
Receipt: `artifacts/prepared-memory-post-rehearsal-20261002.json`. This verifies
service state and memory, not authenticated application behavior or restart counts.
Identity/action authorization, full operational transport/timing, coverage and
other beta gates remain open. No beta opening or signup activation occurred.

## October 2 — revised preparation memory reserve

User approved reducing the host preflight reserve to256MiB until beta. With the
unchanged1GiB whole-job cap, require at least1.25GiB MemAvailable immediately before
allocation. Keep896MiB MemoryHigh, zero swap, group termination, timeouts and cleanup
checks. Historical failures against the former1.5GiB floor do not remain blockers
solely on that basis; a fresh launch check is still required. Actual runtime
imports were subsequently verified above; other beta gates remain open.

The4GiB staging plan upgrade, required staging power-down and restoration of the
512MiB reserve/1.5GiB floor are deferred until after beta under
POST-BETA-STAGING-RAM-4G-20261002 in PRODUCT_BACKLOG.md. Current policy:
artifacts/PREPARED_MEMORY_RECOMMENDATION_20261002.md.

## September 28 — .78 deployment verified

Operator confirmed authenticated build20260928.78 and dashboard46 Jobs Matched.
Delayed173.4second check passed unchanged service PIDs/zero restarts and fresh
worker heartbeats. This completes scoped .78 deployment verification; see
`artifacts/RELEASE78_DEPLOYED_20260928.md`. Coverage, source consent and separate
beta gates remain open. Automated navigation is investigated by another chat.

## September 28 — .78 activated, authenticated postcheck pending

Exact cafad5b48a579adc80695519f0f8b7d39f621cd5 is active; live/readiness, stable
services, WEB/DB backups, empty queues and unchanged schema/admission guards pass.
UD/ZipRecruiter/Serper remain disabled. Operator manual .78 API/dashboard confirmation
is pending because another chat is investigating automated browser navigation.
See `artifacts/RELEASE78_DEPLOYED_20260928.md`. No new coverage pass or beta opening.

## September 28 — approved .78 preparation completed

Exact candidate cafad5b48a579adc80695519f0f8b7d39f621cd5 is committed locally and
prepared on staging: 775 native tests (one skip), 389 tracked files byte-verified.
Activation has not started: existing Edge hosted-staging tab is blocked by
ERR_BLOCKED_BY_CLIENT, awaiting operator resolution for authenticated verification.
.77 remains live/ready with healthy backups. Existing deployment approval persists.
See `artifacts/RELEASE78_PREPARED_20260928.md`; no push/PR, new coverage or beta opening.

## September 28 — filtering release preparation and diagnostics

Build .78 is a local candidate only. Aggregate pagination, exclusion, duplicate and
cap diagnostics were added without increasing collection limits. Final isolated
verification ran 473 tests (two skips), syntax, shell syntax and loopback smoke;
independent diagnostics review found no blocker. See
`artifacts/RELEASE78_PROPOSAL_20260928.md` for exact scope and deployment gates.
Both new source adapters remain disabled. Historical reference work exposes missing
positive cells and a duty-based data-engineer title-recall limitation; it is not a
new coverage pass. Beta remains closed; no new window or deployment occurred.

## September 25 — source research and remediation proposal

Continuation: all alternatives are tracked as COV-01 through COV-15 in the product
backlog. Client filtering is implemented locally; UD and ZipRecruiter adapters are
prepared but disabled pending source access and complete detail-path validation.
467 isolated tests passed (two skips), syntax and temporary loopback smoke passed.
See `artifacts/SOURCE_REMEDIATION_IMPLEMENTATION_20260925.md`. No deployment or new
coverage pass is established.

The requested UD, JobAps, ZipRecruiter, Indeed, Handshake, LinkedIn and iHire
evaluation is recorded in `artifacts/DISCOVERY_SOURCE_EVALUATION_20260925.md`.
Prioritize a Dayton employer catalog, a feasibility check of ZipRecruiter's
documented search MCP, and candidate-recall corrections. USAJOBS and Adzuna are
additional API candidates. These are researched proposals, not enabled sources
or acceptance evidence; the failed two-window decision remains unchanged.

## September24 — two-window coverage decision

The scheduled second window completed. Only US-remote software engineering met
the three-posting/two-employer floor in both windows. Remote data engineering
had2 then1 qualifying postings; enterprise architecture and all Dayton cells had0.
Series total101 public HTTP attempts, zero AI calls/user-data writes. The two-window
coverage gate remains unmet and beta stays closed. No further attempts scheduled.
See `artifacts/US_TECH_COVERAGE_WINDOW2_20260924.md` for the completed evidence.

## September24 — approved coverage window one

The first US technology coverage window did not meet the approved gate. Remote
software engineering reached3 postings/2 employers; data engineering2/2; enterprise
architecture0/0. All three Dayton cells were0/0. Public feeds refreshed successfully;
51 public HTTP requests total, zero AI calls or user-data writes. Source page limits
remain visible. Beta stays closed. See
`artifacts/US_TECH_COVERAGE_WINDOW1_20260924.md` for reviewed evidence, limitations,
retention and the second-window allowance (earliest September24 2:14p.m. Eastern).
The remaining window cannot retrospectively repair the first window's shortfall.

## Current continuation status — September 23, release .77

Latest cancellation acceptance: .77 attended Stop passed after durable worker
start, during searching_sources. Same run cancelled, lease/worker cleared,
active0/queue0; saved count46 remained after reload. This closes the active-source
cancellation check, without claiming interruption of scoring or partial-batch
commit races. See `artifacts/RELEASE77_ACTIVE_CANCELLATION_20260923.md`.
Representative coverage acceptance and separate identity/admission gates remain.

Coverage follow-up at23:41 UTC: cache-only .77 matrix replay passed controls but
hashes match prior .75 snapshots, so no independent refresh window is established.
The user selected **US technology roles, including remote** on September23.
The exact sampling matrix and minimum useful coverage remain proposed in
`artifacts/BETA_NEXT_GATES_20260923.md`; the older vendor matrix is not an agreed
floor. Customer-support case remains0/0 in the historical replay, outside the
proposed technology matrix. See `artifacts/RELEASE77_COVERAGE_REPLAY_20260923.md`.
Concrete approval proposal: `artifacts/BETA_US_TECH_COVERAGE_PROPOSAL_20260923.md`.
The user approved that exact six-cell, two-window public coverage check September24.
Collector preparation/verification and first-window execution are underway; no
coverage pass is yet established. The166-request total allowance excludes AI calls.

Latest functional acceptance: one approved .77 Enterprise Architect/Stuttgart
search succeeded with partial_results:1 discovered, scored, qualified and saved.
Bosch posting744000148970273 retains source-verified Stuttgart/full-time metadata
after reload; saved count45 ->46; durable active/queue0 and lease/worker cleared.
This narrow save/persistence gate passes. Source coverage remains incomplete;
other beta gates remain open. Earlier scoring failure cause remains unknown. See
`artifacts/RELEASE77_FUNCTIONAL_ACCEPTANCE_20260923.md`.

Latest: the approved Gemini classification correction is deployed as .77, exact
commit acfed3e535244cb0a6b0ff6003dc19947a3d91d6. It passed419 local tests (two skips),
752 native staging tests (one skip), syntax, byte verification and live dashboard
checks. Services/backups were healthy and queues empty at deployment verification;
signup remains closed in the recorded release. The later acceptance receipts above
supersede the deployment's saved count45 with46. No GitHub publication of .76/.77
has occurred. See `artifacts/RELEASE77_DEPLOYMENT_20260923.md`.

Existing source diagnostics for the successful search confirm five failed source
checks (Greenhouse2, Lever1, Ashby1, SmartRecruiters1), four unusable checks and
page limits in Lever and SmartRecruiters. Repeated diagnostic rows carry the same
provider counters and must not be summed. No timeout was recorded. Failure causes
are not established by these counters. See
`artifacts/RELEASE77_SOURCE_DIAGNOSTICS_20260923.md`.

The settled identity policy is optional MFA, 3600-second sessions, a managed
Production hostname and owner-only migration. Older required-MFA wording in the
search checkout is superseded by the September17 authentication readiness document
and factor-readiness acceptance documents. The six legacy pilots start fresh;
account deletion is not authorized. Owner recovery credential enrollment is now
recorded, but does not establish general user lost-factor recovery acceptance.

Remaining identity/admission work: reconcile the candidate migration chain beyond
live0014; implement/verify normal factor removal and general lost-factor recovery;
complete attended provider/factor and enabled-session acceptance; verify application
delivery and admission controls. Broader backup-program readiness also remains
distinct from the passing health receipts. No live identity, signup, factor or
backup mutation is authorized by this status document. See
`artifacts/BETA_NEXT_GATES_20260923.md` for evidence and the next bounded work.

Everything below is historical, not the current gate decision.

### Earlier .76 observations

**Functional acceptance update:** the approved .76 Enterprise Architect/Stuttgart
run discovered and accepted one posting but failed during scoring after three
bounded provider_error attempts. No match was saved; UI reported failure clearly,
45 existing saved jobs remained, and durable run/queue state is idle. New-save
metadata acceptance is still open. Hosted attended Stop is now confirmed for a
queued run: cancelled, lease/worker cleared, active0, queue0, and UI saved count45
unchanged. The worker had not started; active-processing cancellation remains open.
See `artifacts/RELEASE76_CANCELLATION_20260923.md`.
See `artifacts/RELEASE76_SEARCH_ACCEPTANCE_20260923.md`. No additional paid retry.

**Superseding status:** explicitly approved forward-only activation succeeded.
Build `.76`, commit `7f9dbf178fa29295c56569e80b2ce726349edd78`, is deployed and
healthy. WEB/DB backups pass, signup remains closed, Serper disabled. Authenticated
dashboard reload passed; personalized search/save and active-processing cancellation
remain unverified. Clean implementation branch has one local unpublished commit. See
`artifacts/RELEASE76_DEPLOYED_20260923.md` for final evidence and retained storage.

### Earlier preparation observations

Latest: `.76` is now committed as `7f9dbf178fa29295c56569e80b2ce726349edd78`
and prepared on staging (748 native tests, one skip; 383 exact tracked files).
Activation stopped before any release/service mutation because hosted authority
prohibits code rollback. `.75` remains healthy and current. A reviewed forward-only
activation decision is required; see `artifacts/RELEASE76_PREPARED_20260923.md`
for evidence, proposed action and retained storage. This supersedes the local-only
candidate status below. No push or PR update occurred.

Release preparation update: the local candidate is now build `.76`, including
the metadata regression in the deployment test list. Independent review found
no blocker; final build revalidation was rejected by automatic approval review
as outside the earlier .75 execution approval. No new tests, commit, deployment
or push occurred in that attempt. Exact five-file scope and next execution gates:
`artifacts/RELEASE76_CANDIDATE_20260923.md`.

The latest recorded hosted release is **20260923.75**, exact commit
`45d91c047622c60c2b363dfc6bc905b5811e13d1`, published to draft PR #80.
`artifacts/RELEASE75_DEPLOYED_20260923.md` and
`artifacts/PUBLISH_CLEANUP_20260923.md` supersede the older release/candidate
statements below. The approved read-only observation at 20:53:05 UTC reconfirmed
this exact release, healthy live/readiness endpoints, and WEB backup health ok.

Source review confirmed the previously observed SmartRecruiters metadata loss:
validated feed geography was discarded before detail extraction. A local,
uncommitted repair now carries bounded, URL-bound location, explicit remote/hybrid
and recognized employment metadata through independent detail validation, filling
only missing detail fields. SmartRecruiters title-slug redirects must retain the
same employer and numeric posting ID. It does not infer on-site work from remote=false.
New synthetic regressions cover persistence, existing-field precedence, rejected
details and mismatched URLs. The approved isolated suite ran 446 tests with two
skips and no failures; syntax and temporary loopback smoke passed. This repair
is locally verified but not committed, deployed, or published.

Immediate gates remain repeated representative coverage, personalized hosted
search/save/reload acceptance on the final revision, and attended Stop with a
durable cancelled outcome and drained queue. Existing .75 transport/cache proof
does not close these. Session/admission, factor/recovery and backup-program gates
also require their own evidence before separately authorized signup activation.
Serper remains disabled and beta remains closed.

The prepared read-only cache probe is
`artifacts/beta-cache-coverage-20260923.py`; it forbids feed fetch/lock/write calls,
checks snapshot hashes unchanged, and makes no AI requests. After explicit user
approval it completed successfully: WWR/Jobicy had 83/200 rows, with 6/12 software
engineer, 3/12 architect and 0/5 data engineer remote title matches. Negative
controls passed. These were existing snapshots, not independent refreshes or
geographic eligibility proof. No remote mutation occurred.

Verification status: static second review found no remaining blocker;
`git diff --check` passed and nothing is staged. Required P0/targeted tests,
syntax and temporary loopback smoke passed; synthetic databases were removed
and the temporary server stopped cleanly. No commit, push, deployment or existing
app restart occurred. Full evidence and retained-storage handoff:
`artifacts/BETA_METADATA_VERIFICATION_20260923.md`. Friction handoff:
`artifacts/beta-continuation-friction-pending-20260923.md`.

## Historical evidence

September 23 catalog continuation: a local `.74` candidate now implements the
approved fifteen employers, bounded fair pagination, public feed-page caching
and explicit partial coverage. All fifteen first pages and one detail per
employer passed read-only staging-network samples; 431 local tests ran with two
skips and no failures. Candidate remains uncommitted/undeployed; staging remains
`.73`. See `artifacts/CATALOG_IMPLEMENTATION_20260923.md`. Broad repeated
coverage, deployed search/save acceptance and attended cancellation remain open.
Yahoo/Bing follow-up found query-specific upstream failures without proof of a
blanket IP/automation ban; bounded daily canaries are recommended, not scheduled.
Serper remains disabled. Full diagnosis: `artifacts/YAHOO_BING_DIAGNOSIS_20260923.md`.

## SEARCH-001 — Search silently completes with no usable postings

- Status: **OPEN — blocks beta opening**. Reported and diagnosed September 21, 2026.
- Delivery: separate next application PR; do not add to recovery/identity PR #78.
- Symptom: Search & Analyze briefly shows "Waiting for the search worker", then
  closes with no error or new matches.
- Evidence: the September 21 run completed with zero postings inspected and zero
  jobs added, recorded as success. Worker services were healthy. A generic live
  probe found Yahoo HTTP 500 and unrelated Bing fallback results. The precise
  earlier response bodies were not retained, so the probe supports source
  degradation without proving every earlier response was identical.
- Reporting defects: empty eligible candidates become success; source fallback
  degradation is not retained in the completion result; generic success wording
  implies useful work even when nothing was found.
- Evidence record: ../artifacts/search-diagnosis-20260921.md (local operational record).

Acceptance before closing:

1. Verify reliable discovery of eligible postings from supported sources using
   bounded live checks; preserve URL/domain/security validation.
2. Distinguish source outage or unusable source responses, a valid zero-result
   search, all-known/filtered postings, partial success, and newly saved matches.
3. Persist only bounded, privacy-safe diagnostics and display a clear completion
   outcome after the progress window closes. Do not label source failure success.
4. Cover fallback failure, unrelated/malformed results, genuine zero results,
   partial results and normal success with regression tests; verify the UI flow.
5. Deploy and live-verify the exact repair commit before publishing its PR,
   following the repository deployment-first workflow.

September 21 continuation: an isolated, uncommitted reporting/classification repair
is prepared on `codex/beta-search-outcomes`, based on hosted main after PR #78.
See `.worktrees/beta-search-outcomes/docs/BETA_SEARCH_READINESS_20260921.md` for
verification and live source probes. Reliable discovery is still unproved; no
repair deployment or beta activation has occurred. Separate backup freshness
remediation is documented below.

Backup freshness remediation is tracked separately in
`artifacts/BACKUP_FRESHNESS_20260921.md`: one full-download database inventory is
complete with zero divergence, and its recurring timer has been corrected. The
approved paired full backup and generated-object capture/publication succeeded;
staging was restored on the unchanged release. The matching database mirror
completed at 20:28:02 UTC. Both strict backup health surfaces return ok with no
failure codes; these two backup freshness blockers are resolved. Existing restore
evidence remains within policy; no new restore rehearsal or beta activation is
claimed. SEARCH-001 remains open.

Further investigation: see
`.worktrees/beta-search-outcomes/docs/DISCOVERY_REMEDIATION_OPTIONS_20260921.md`
and `artifacts/discovery-investigation-20260921/PROBE_RESULTS.json`. Decoded Bing
results remained unrelated across four query variants. Public employer feeds
worked on selected samples across all four supported ATS platforms; this proves
feasibility, not broad coverage or release acceptance. Recommended next step is
a bounded managed-search API evaluation, with direct feeds for known employers.
No provider was selected, paid service activated, or runtime change deployed.

Later September21: the operator selected Serper and the initial Tekton/CLEAR/Coder
catalog. A local hybrid implementation and default-closed durable request quotas
are prepared and tested. See
`.worktrees/beta-search-outcomes/docs/HYBRID_DISCOVERY_20260921.md` for evidence and
remaining secure-key, coverage and quota-migration/owner-recovery compatibility
gates. Public feeds passed a fresh three-request adapter check; Serper has not
been live-tested and SEARCH-001 remains open. No staging change or beta activation.

September 22 update: the operator supplied a successful live Serper trial receipt
at 14:11:47 UTC: all five positive URL controls and the zero-result negative
control passed, using six managed requests; all three direct feeds also passed.
The receipt is preserved at
`.worktrees/beta-search-outcomes/docs/SERPER_TRIAL_20260922.json`.
This supersedes the earlier untested-Serper status, but proves only transport and
URL controls. Repeated relevance/coverage checks, protected quota migration and
recovery compatibility, operating budgets, worker credential setup and deployed
application acceptance remain open. SEARCH-001 remains open; no beta activation.

September 22 authorization update: Serper use is suspended until the operator
explicitly approves it again. No additional paid probes, credential installation
or managed-search activation is authorized. Interim discovery work relies on
the approved direct feeds and attempts through the prior key-free mechanism.
The feeds remain a local candidate; this does not assert an interim deployment
or resolve the prior mechanism's reliability problems. The trial did not persist
the Serper key, and no provider-side revocation is claimed.

September 22 compatibility continuation: the interim local candidate now keeps
the exact `0014` schema and recovery safeguards by moving the unused Serper quota
migration outside the active chain. `hybrid_feeds` combines direct feeds with
legacy attempts; Serper is blocked in code. PostgreSQL upgrade/grants (58 tests)
and protected initial migration (11 tests) passed. See
`.worktrees/beta-search-outcomes/docs/DISCOVERY_COMPATIBILITY_20260922.md`.
The interim schema incompatibility is resolved locally; future Serper migration
compatibility is deferred. Deployment and useful live coverage remain open.

September 22 release 72 superseding update: exact commit
`7a46b40023a24a93937cef35b4d062f2ff63167e` is deployed and healthy on staging,
using `hybrid_feeds`, schema 0014, and no Serper key. Recovery and separate WEB/DB
backup-health gates pass. The authorized live search saved seven new matches,
retained after reload, and correctly disclosed partial source failures. Live
cancellation remains unverified: two short attempts completed before Stop could
be confirmed. The final queue is empty. Repeated coverage and cancellation remain
open; SEARCH-001 and beta readiness are not closed. No push/PR or signup activation.
See `artifacts/RELEASE72_DISCOVERY_20260922.md` for exact evidence and retention.

September 23 superseding update: release `20260923.73`, exact commit
`c3ae922b5d41ed359916c2f9d98dacd35545bf23`, is deployed and healthy. It fixes a
lost early-Stop request and resets the Stop control when an already-active search
is recovered. Native tests and exact-file checks pass; schema, recovery, backup
health and Serper suspension are unchanged. One live attempt completed before
browser automation could confirm Stop; cancellation still needs attended live
verification. A six-case public catalog matrix shows remote engineering results
only from Coder, with geographic restrictions. Broad SEARCH-001 remains open;
no limited-scope beta acceptance or public activation is inferred. Full evidence:
`artifacts/DISCOVERY_GATES_20260923.md`.

## September 23 deployment follow-up

Build 20260923.74 (33c214a) is deployed and healthy; backup ops revision db48b7c
is installed and verified. Draft PRs #80 (catalog) and #81 (backup) now record
the exact revisions after deployment. See artifacts/RELEASE74_DEPLOYMENT_20260923.md
for the two-part public probe, acceptance limits and retained storage. Earlier
candidate/unpublished statements in this file are historical. Serper stays
disabled; beta/signup remain closed pending coverage and attended cancellation.

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.
