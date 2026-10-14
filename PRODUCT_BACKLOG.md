# Product Backlog

## October 7 — current product priorities and delivery order

Owner: Hunter / CareerTrellis product and engineering. Review after each delivery
cycle using search-quality, provider-reliability and activation/cost evidence.
This section is the current ordering of unfinished work; it supersedes older
P0–P3 placement and launch-blocker wording below, while preserving their evidence,
acceptance limits and explicit deferrals. Items remain unchecked until their full
acceptance is met; completed slices are recorded separately. Planning does not
activate billing, providers or new spending.

Recorded baseline, checked against local source and retained receipts October 7:

- [Open beta launched](docs/BETA_BLOCKERS.md) October 6; the recorded account cap
  is 25. Canonical-host cutover (local-only evidence: `artifacts/HOSTNAME_CUTOVER_RESULT_20261007.md`)
  and the UX deployment below supersede the older staging address. Historical
  "beta remains closed" statements are not current status.
- Serper activation receipt (local-only evidence: `artifacts/serper-activated-readback-20261007.json`)
  records enabled discovery with existing credits and zero requests used at that
  readback. This is not evidence of useful results. No top-ups or budget increases
  follow from this plan; refresh usage before any separately authorized live test.
- [The existing AI adapter](backend/ai_providers.py) already lists Gemini and
  OpenAI. Extend that implementation; do not rebuild the checked abstraction item
  or mistake local source support for hosted acceptance of every workflow/model.
- Broader search coverage remains unproven. The deferred coverage campaign,
  optional MFA/recovery work and extra restore drills remain post-beta work.

### Priority policy and balanced work order

P0 is reserved for an active service, data-isolation, security, backup or uncontrolled
spending defect. Such defects preempt planned work. P1 is the next product tranche;
P2 follows measured results or a dependency; P3 is an expansion option. A paid-pilot
prerequisite is not a new gate on the already-open free beta.

For the next cycle, target roughly 35% search, 25% AI/provider work, 20%
monetization and 20% UX/maintenance to address the now-due post-beta reviews.
Return to 40% search, 25% AI, 25% monetization and 10% UX/maintenance after those
reviews have an owner, concrete next action and recorded disposition. These are planning allocations, not estimates
or parallel-agent instructions. Deliver one bounded slice from each of the first
three tracks before taking a second cycle solely on one track. If source access
or a commercial decision blocks a slice, advance its independent sibling work;
do not let a partner reply stall all three tracks.

| Order | Search slice | AI/provider slice | Free/paid slice | UX/maintenance slice | Exit evidence |
|---|---|---|---|---|---|
| 1 — finish the balanced baseline cycle | Reuse the completed SEARCH-002 offline slice; list missing full-pipeline observations rather than rebuild the evaluator | AI-001 capability/usage contract is the next implementation slice | MON-001 plan matrix, then MON-002 cost worksheet with unknowns explicit | P1 due Ubuntu/RAM review; bounded mirror-alert correction design; owner-recovery custody review | Existing 42-control baseline retained, provider contracts tested, plan/cost proposal ready, maintenance decisions scoped; no paid calls required |
| 2 — improve recall and enforce limits | Bounded SEARCH-003 alternate-title/optional-description slice against the fixed reference; then COV-13 decision and COV-04 employer increment | AI-002 Claude adapter; AI-003 setup UX | MON-003 entitlements and MON-004 metering | UX-001/003/004 remaining acceptance and targeted UX-002; execute maintenance only within its approval | Measured synthetic recall gain without agreed regression, mocked adapter parity, concurrent quota enforcement; hosted claims remain separate |
| 3 — prepare a keyless paid pilot | SEARCH-005 duplicate/stale handling before SEARCH-004 ranking; extend SEARCH-002 full-pipeline observations and progress reporting | AI-004 model quality/cost evaluation and bounded routing | MON-005 managed AI and MON-006 billing sandbox | BETA-ADM-01 residual-boundary reconciliation; review backup/recovery-policy gaps and retained resources | Synthetic no-key journey, measured pipeline stages, subscription lifecycle tests, explicit residual hosted/recovery acceptance |
| 4 — pilot and tune | SEARCH-006; targeted COV-10/11 follow-up; separately scoped COV-15 only after improvements | AI-005 next-provider evaluation after Claude parity | MON-007 paid pilot and MON-008 conversion/retention review | Capacity, support and operational readiness for the exact pilot scope | Separately authorized capped live pilot, reconciled cost/usage, retained search quality and a continue/revise/stop decision |

Within a row the tracks can progress independently except for explicit item
dependencies below. A row is a work-selection guide, not a requirement to finish
every large item before delivering a smaller accepted slice.

### October 7 re-review — effect of the other backlog updates

- **Next selection:** AI-001, then MON-001 and the first MON-002 worksheet slice,
  before another search-only cycle. The [SEARCH-002 receipt](docs/search-quality/README.md)
  establishes a reproducible 42-control offline filter baseline. It does not close
  SEARCH-002, establish live coverage or justify repeating the completed setup.
  MON-002 can begin with labeled assumptions; replace them with measured usage
  as AI-001 and the remaining SEARCH-002 observations become available.
- **Next search change:** test the missed data/architecture alternate-title cases
  through SEARCH-003's explicit optional description scope, preserving title-only
  defaults. Use the existing fixed reference/thresholds for this bounded slice;
  full SEARCH-002 completion is not a prerequisite for that offline comparison.
  Complete discovery, extraction, eligibility, scoring, latency and cost observations
  before making end-to-end improvement claims. Move duplicate/stale work ahead of
  ranking so repeated or expired jobs do not distort that evaluation.
- **P1 maintenance now due:** refresh Ubuntu patch/reboot status and prepare the
  existing RAM-resize decision; reaching beta already triggered both reviews.
  Record exact cost/downtime and recovery prerequisites before any host change.
  Review owner-recovery custody/path now; reserve-code provisioning and optional
  MFA/recovery activation remain their separate deferred work.
- **P1 monitoring follow-up:** CT-BACKUP-LABEL-GAP-20261007 has evidence of a
  scheduled pending-mirror interval followed by healthy reporting, not a failed
  backup. Prioritize bounded design and synthetic timing/failure tests, preserving
  stale/overdue/failure alerts. Escalate to P0 if fresh evidence shows an actual
  backup failure, missed required alert or other active safety defect; the retained
  October 7 receipt alone does not establish current health.
- **P2 evidence/retention follow-up:** reuse the deferred-work reconciliation below
  for BETA-ADM/BKP gaps and POST-BETA-RETAINED-RESOURCES-20261007. Review admission
  gaps before relevant identity changes or paid-pilot acceptance, recovery-policy
  gaps before claiming full-service recoverability, and retained resources at the
  next stabilization/storage review. Additional drills and optional factors remain
  deferred; completed launch, alert delivery and cleanup actions are not new work.

Existing backlog reordering:

| Existing work | Current disposition |
|---|---|
| COV-04, COV-10/11/12/13; optional title-versus-other-fields search | P1, incorporated into SEARCH-002/003/005; reuse their artifacts and IDs rather than duplicate implementation |
| COV-01/02 | P1 source-access follow-up; activation remains blocked on applicable permission/terms, with COV-04 as the independent delivery path |
| COV-05/06/07 and COV-09 alternative vendors | P2, one source at a time, selected by incremental eligible inventory per request/cost after the baseline |
| COV-08 broad partnerships; COV-15 expanded acceptance series | P2 after targeted improvements and a separately bounded collection scope; retain the explicit post-beta coverage deferral |
| COV-14 manual-link improvements | P2; promote a concrete broken import flow to P1, without changing the product to import-only |
| UX-001/003/004 remaining acceptance | P1 bounded closeout of deployed behavior; do not redo the completed deployment or hostname work |
| UX-002; progress reporting; credential help and password-manager behavior | P1 targeted onboarding/search/provider slices alongside rows 2–3; cosmetic spacing and optional confirmation preferences remain P3 |
| Multi-user/security umbrella and BETA-ID/ADM/BKP entries | Use the deferred-work reconciliation below; BETA-ID-01's completed transition is not repeated. BETA-ADM-01 remaining boundaries are reviewed before an identity change/paid pilot; residual BETA-BKP-01/02/03 evidence is P2, except the P1 mirror-alert defect. Optional MFA/recovery and extra drills stay deferred; active failures are P0 |
| CT-BACKUP-LABEL-GAP-20261007 | P1 bounded pending-mirror design and synthetic timing/failure tests in the next maintenance slice; do not suppress overdue/stale/failed alerts or repeat completed channel activation |
| POST-BETA-UBUNTU-MAINTENANCE-20261006; POST-BETA-OWNER-RECOVERY-20261006 | P1 current patch/reboot review and owner custody/path review; scope actual changes separately. Optional factor work is not pulled forward by this review |
| POST-BETA-RETAINED-RESOURCES-20261007 | P2 next stabilization/storage review using existing registers; record owner/retention decisions and exact authorized cleanup, never repeat the completed removals |
| Branding clearance and replaceable naming | P2 review before broader commercial promotion; preserve the existing decision scope without making it a new free-beta gate |
| Individual billing/metering/freemium | Promoted from P3 to MON-001 through MON-007; team administration and institutional reporting remain P3 |
| Pro passes, credit packs, email/calendar, extension, marketplace, licensing, white label and affiliates | P3 after evidence from MON-008; avoid launching multiple billing products at once |
| POST-BETA-STAGING-RAM-4G-20261002 | P1 review now: its beta milestone has been reached. Preserve the existing 4 GiB CPU/RAM-only proposal, reserve/floor restoration, and explicit billing/downtime approval; this is not contingent on inventing a new capacity trigger |
| Cross-host discovery cache | P3 until another application host is planned; retain shared polling/isolation requirements before that expansion |

### Search quality — specific additions

- [ ] **SEARCH-002 (P1): Make search quality measurable.** Extend COV-12 with a
  small versioned, dated reference set of positive and negative examples for the
  existing US technology/Dayton/remote audience, including architect and data
  roles. Measure precision at 10, recall against the known set (not the whole
  job market), unique eligible jobs/employers, stale/duplicate rate, detail success,
  latency and requests per usable result. Reuse COV-10 counters to locate losses
  at discovery, detail extraction, eligibility and scoring. Acceptance: a
  reproducible offline before/after report, explicit missing cells and proposed
  improvement/non-regression thresholds fixed before the next comparison. Keep
  fixtures synthetic or permitted public data, separate from user telemetry;
  expanded live collection remains a separately scoped COV-15 activity.
  October 7 implementation: [offline baseline and evaluator](docs/search-quality/README.md)
  add 42 synthetic controls across six cells, versioned comparisons and proposed
  thresholds. Full-pipeline measurements remain explicitly missing; SEARCH-002
  stays open and no live coverage improvement is claimed.
- [ ] **SEARCH-003 (P1): Improve query intent and role recall.** Add editable,
  bounded role synonyms and seniority/exclusion rules; preserve title matching as
  default and implement the existing optional description/other-fields scope.
  Resolve COV-13 commute radius and remote eligibility explicitly. Show the
  interpreted search before execution; never silently widen geography or treat
  "contract" as "1099". The completed SEARCH-002 offline baseline permits a bounded
  filter comparison now; full-pipeline claims depend on its remaining observations.
  Acceptance: architecture/data-role,
  false-friend titles, US-restricted remote, unknown-location and contract fixtures
  pass, with measured recall gains and no agreed precision regression.
  October 9 experiment: [optional description comparison](docs/search-quality/SEARCH003_20261009.md)
  recovers four fixed-reference positives with title-only defaults preserved.
  Incidental/negated-role false positives prevent runtime adoption; SEARCH-003
  remains open for semantic controls and broader acceptance.
  Follow-up: [72-control adversarial comparison](docs/search-quality/SEARCH003_INTENT_20261009.md)
  adds an all-returned precision guard and affirmative-duty candidate. Synthetic
  recall rises from 25% to 75% at 100% returned precision; a historical-context
  false positive and implicit-duty misses keep runtime adoption unapproved.
  [Independent context holdout](docs/search-quality/SEARCH003_CONTEXT_20261009.md)
  rejects document-wide context exclusions: false positives fall 7 to 2 but recall
  falls 26.7% to 20%. Candidate retained as evidence, prior matcher restored.
  Next candidate needs section/subject attribution and local negation scope.
  [Section-aware follow-up](docs/search-quality/SEARCH003_SECTIONS_20261009.md)
  passes retained regressions but fails a fresh 16-case holdout (50% precision,
  25% recall). Candidate remains offline and unadopted. Stop extending phrase
  rules; next slice is explicit user-controlled role alternatives or a separately
  scoped semantic evaluation, with title-only defaults preserved.
  [Explicit title alternatives candidate](docs/search-quality/SEARCH003_ALTERNATIVES_20261009.md)
  now adds opt-in bounded alternatives, preview and versioned saved/queued execution
  in an isolated unpublished worktree. 430 tests passed with five skips; 16 focused
  tests and local browser checks passed. Existing ordinary semicolon queries keep
  their meaning. Hosted prefix-collision audit, coordinated worker/rollback support
  and PostgreSQL/provider acceptance remain open; no deployment or recall gain claimed.
  [Read-only title-format audit](docs/search-quality/SEARCH003_TITLE_AUDIT_20261010.md)
  now counts collisions and downgrade blockers without returning search text;
  384 tests passed with two platform skips. PostgreSQL visibility checks are mocked,
  so hosted audit, target binding and coordinated rollback remain open.
  [Approved hosted aggregate audit](docs/search-quality/SEARCH003_HOSTED_AUDIT_READY_20261010.md)
  completed October 10 at 02:42 UTC: all two saved searches and two run rows had
  zero reserved markers, oversize or unknown-status findings. PostgreSQL 18.4/schema
  20261005_0023 confirmed global read-only repeatable-read coverage. This snapshot
  closes the initial collision scan; web-release binding, coordinated rollback and
  provider acceptance remain open. No deployment or data changes occurred.
  [WEB binding and compatible recovery](docs/search-quality/SEARCH003_WEB_BINDING_20261010.md#provider-acceptance-current)
  now records build 20261010.85 / dfbb17d deployed October 10 at 12:05:26 PM EDT
  (16:05:26 UTC), `target_healthy`, configuration unchanged, and all three app
  services on the exact successor with zero restarts. Serper is now primary web
  discovery with Yahoo/Bing fallback on managed-discovery failure, as selected by
  the user; employer feeds and existing request/credit limits remain. Guarded import
  verified all 7,947 venv entries. Linux: 825 tests run, 4 skipped; Windows: 483 run,
  2 skipped; suites passed. The 640 MiB/no-swap import cap and original full-build
  guard remain. Version-2 aggregate audits narrowly admit the single known terminal
  title envelope, retaining zero-activity/schedule/unknown-marker guards. Compatible
  recovery 40ff10a/build 20261010.84 remains verified and retained; no live recovery
  was exercised. Earlier build-83 partial results remain historical evidence.
  <!-- provider-acceptance-backlog-result:start -->
  The one authenticated build 85 run completed at 12:11:11 PM EDT with partial results:
  25 candidates, 2 scored, 2 matched, 0 new jobs. Failed source checks fell from 10 to 1; latest
  counters show two shared 10-result pages per provider and no legacy fallback or
  unusable-response counters. Greenhouse retains one failure/access challenge;
  shared-page and employer feed limits keep coverage incomplete. Existing caps
  and credits remain; no purchases/top-ups or additional run. Full provider
  acceptance remains open. Next bounded slice: Greenhouse access diagnosis and
  feed/page coverage within existing caps. See terminal evidence (local-only evidence: `artifacts/provider-acceptance-20261010/live-search-result.json`).
  <!-- provider-acceptance-backlog-result:end -->
  Scoped Docker cleanup (local-only evidence: `artifacts/provider-acceptance-20261010/docker-cleanup-execution/complete.json`)
  removed four owned containers, snapshot image and network; freed subnet allocation
  172.21.0.0/16, with MVOC healthy. Physical disk reclamation is unmeasured. Current,
  baseline, recovery and evidence retention is recorded in the handoff and
  WEB inventory (local-only evidence: `artifacts/provider-acceptance-20261010/activation-observation.json`);
  maintainer review follows acceptance and release/recovery supersession. No PR or push at that acceptance checkpoint; the October 11 closeout below
  records the subsequent publication exception. In-app sign-in remains a scoped follow-up; Edge authentication works.
- [ ] **SEARCH-004 (P1): Rank results with understandable evidence.** Separate
  hard eligibility filters from preferences, support user-controlled weighting
  for role fit, skills, recency and commute, and show factual match reasons plus
  missing requirements. Keep unknowns explicit and distinguish source evidence
  from AI assessment. Depends on SEARCH-002/003 and SEARCH-005 duplicate/stale
  handling for the ranking evaluation. Acceptance: reviewed relevance
  comparisons, stable tie-breaking and explanations grounded in retained fields;
  plan level, affiliate revenue and employer payments never influence ranking.
- [ ] **SEARCH-005 (P1): Consolidate duplicates and handle stale listings.** Extend
  COV-11 with canonical employer/requisition identity, cross-source provenance,
  first/last-seen dates and bounded stale checks before costly scoring. Preserve
  uncertain duplicates, changed/reposted requisitions, user edits and deletion
  tombstones. Depends on SEARCH-002. Acceptance: cross-source duplicates are
  scored once; timeout is not mistaken for closure; history survives merging;
  metadata propagation regressions cover source location and employment type.
  October 13 bounded runtime candidate: post-redirect canonical URL checks avoid
  rescoring the same resolved posting within a run and preserve saved/archived
  jobs and deletion fingerprints. Different URLs/repostings remain distinct;
  timeout is not closure. See docs/search-quality/README.md. Cross-URL requisition
  identity, persistent provenance/history and broader stale checks remain open.
  Local synthetic verification only; no live provider request or deployment.
  Full isolated run: 446 tests, two skips, no failures/errors; syntax, loopback
  version/dashboard and clean shutdown passed. Independent review found no
  blocking issue. Receipt: artifacts/search005-postredirect-20261013/verification.json.
  October 14 bounded stale check: generic browser extraction now rejects a
  JobPosting whose explicit timezone-aware expiration is in the past and whose
  URL matches the resolved posting. Missing, malformed, date-only, timezone-free
  and other-posting timestamps do not establish closure. The partial-import path
  respects the same check. No stored job is closed, merged or deleted by it.
  Coverage remains limited to the selected generic structured posting; named ATS
  adapters and cached-feed expiry need separate work. See the search-quality
  notes and local receipt `artifacts/search005-expiration-20261014/verification.json`.
- [ ] **SEARCH-006 (P2): Add useful feedback and zero-result recovery.** Let users
  mark irrelevant, duplicate or closed results with an undo path and scoped reason
  codes. Show whether zero results came from coverage, filters, known jobs or
  source failure, with previewable query adjustments and manual import. Depends
  on SEARCH-003/004/005. Acceptance: feedback changes only the intended user's
  preferences, never silently trains a shared model, and a rerun requires an
  explicit action; compare useful saves and irrelevant dismissals with opt-in,
  aggregate measurement rather than collecting resumes or search text.

### Additional AI APIs — extend the existing adapters

- [ ] **AI-001 (P1): Define provider capability and usage contracts.** Inventory
  every AI entry point: query preparation, extraction, scoring, resume parsing/OCR,
  tailoring and interview help. Centralize required schema/image capabilities,
  input/output limits, model availability, cancellation, safe error codes and
  normalized usage/estimated-cost receipts, including unknown usage. Acceptance:
  Gemini and OpenAI pass the same synthetic contract suite; unsupported operations
  fail clearly; an unknown provider cannot silently fall back to another provider.
  October 7 implementation: provider/plan baseline handoff (local-only evidence: `artifacts/AI001_MON_BASELINE_20261007.md`)
  records the isolated Gemini/OpenAI contract candidate, synthetic usage/cancellation
  parity and remaining hosted/integration acceptance. Defaults remain unchanged;
  current official model-availability caveats are recorded for AI-003/004.
  October 12 integration: [provider contract and caller compatibility](docs/AI_PROVIDER_CONTRACT.md#october-12-integration)
  brings that candidate into the current workspace on `aedc188`, preserving its
  original worktree. Review corrected prompt byte caps to accommodate accepted
  maximum-size Unicode resumes, job descriptions and interview contexts. Both
  provider adapters pass real-caller synthetic checks; the combined suite ran
  392 tests with two skips, source compilation and loopback version/dashboard
  checks passed, and preview/data cleanup was verified. Local-only evidence:
  `artifacts/ai001-integration-final-20261012.json`. No live provider call,
  deployment, commit or PR; workflow persistence, setup and hosted acceptance
  remain open. Next provider slice can build AI-002 on this integrated contract.
- [ ] **AI-002 (P1): Add an Anthropic Claude adapter.** Build on AI-001 with
  provider-specific authentication, schema handling and model capability checks,
  encrypted BYOK storage and no secret readback. Cover structured output, refusal,
  truncation, malformed responses, rate limits, timeouts and cancellation; test
  image/OCR operations only where the selected model supports the contract.
  Acceptance: mocked workflow parity and migration tests, then a separately
  authorized bounded hosted smoke before claiming live support. Model selection
  and prices must be refreshed at implementation time.
  October 12: [isolated Claude transport candidate](docs/AI_PROVIDER_CONTRACT.md#ai-002-claude-transport-candidate--october-12)
  implements structured output/OCR, safe errors, cancellation and conservative
  usage receipts against refreshed official model/API/pricing documentation.
  Combined regression run: 399 tests, two platform skips; final focused run: 26
  tests passed. Compilation and disposable local preview passed. Not selectable
  in the application; no credentials, migration or live-provider call. Next:
  encrypted BYOK admission and additive profile/schema migration, followed by
  selection/UI and real-caller acceptance. AI-002 remains open.
  Backend follow-up October 12: [encrypted hosted integration](docs/AI_PROVIDER_CONTRACT.md#ai-002-hosted-backend-integration--october-12)
  adds Claude credential/profile admission and explicit workflow dispatch. Existing
  text columns require no migration; no plaintext key column was added. Local
  Claude writes fail before mutation. Final isolated suite: 414 tests with two
  skips; compilation, preview and cleanup passed. UI selection, queued/browser
  flows, rollback and live PostgreSQL/provider acceptance remain open; this
  uncommitted candidate is not release-ready. No real credentials or deployment.
  Application completion October 12: [Claude setup and workflow integration](docs/AI_PROVIDER_CONTRACT.md#ai-002-application-integration-completion--october-12)
  adds hosted-only selection/model/key controls and safe saved-state handling,
  browser persistence/mobile coverage, rendered OCR and real-caller/claim tests.
  Terminal provider failures no longer become retryable search errors. Combined
  suite: 427 tests, two skips; syntax and disposable startup/shutdown passed.
  Local implementation is complete. AI-002 stays open only for exact-release
  encrypted-store/RLS, live queue and bounded provider acceptance, with a
  Claude-compatible forward correction. No paid call, credential or deployment.
  Deployment October 12 (02:30 UTC October 13): build `20261012.88`, commit
  `5094df231a85195ac6e94085b53ade8cd107ceb0`, is active. Compatible correction
  `20261012.89` (`803787bd86a7bf79970b72f679bf5656fd48f164`) is installed inactive
  for recovery. Full installed source/runtime readback, live readiness, both
  workers, authority and zero-work pre-switch audits passed; configuration was
  unchanged. Disposable PostgreSQL 18.4 encryption/RLS checks also passed.
  Authenticated browser verification passed October 13: Claude selection/fixed
  model, masked empty key fields, no-key test guard, setup guidance and Manage
  link passed; original provider remained unchanged after reload. No settings
  or keys were saved. Paid Claude acceptance remains explicitly skipped by the
  operator, so AI-002 retains that live-provider acceptance gap. Evidence:
  `artifacts/claude-deploy-20261012/` and
  `artifacts/claude-browser-verification-20261013/receipt.json`.
- [ ] **AI-003 (P1): Make provider setup understandable.** Combine the existing
  official credential links and password-manager fixes with capability-aware
  model selection, masked saved-key status and actionable quota/authentication
  errors. Explain that a consumer chatbot subscription may not include API access.
  Acceptance: switching providers preserves other saved credentials, validation
  discloses any request cost, and paid managed-AI users are not asked for a key.
  BYOK and managed billing responsibility must remain explicit.
  October 13 bounded setup slice: provider-specific model suggestions, explicit
  API-account test-cost disclosure, structured-output versus OCR guidance,
  actionable authentication/quota/model-access failures and duplicate-test
  prevention are implemented locally. Existing saved credentials are preserved.
  Claude paid live verification remains deferred by the operator; it does not
  block this independent roadmap slice. Full AI-003 acceptance remains open for
  managed billing/keyless setup (MON-003/004/005) and exact-release hosted checks.
  Verification: 435 synthetic tests ran with two skips and no failures/errors;
  Python/JavaScript syntax, loopback version/dashboard and clean shutdown passed.
  Local receipt: artifacts/ai003-setup-20261013/verification-final.json.
  No live provider call, deployment, commit, push or PR in this slice.
- [ ] **AI-004 (P1): Evaluate models and constrain task routing.** Use synthetic
  resumes/jobs to compare factual fidelity, schema success, relevance, latency
  and cost per completed workflow, including retries. Depends on AI-001 and
  SEARCH-002; managed routing also depends on MON-004/005. Acceptance: versioned
  results and model allowlist; choose a lower-cost model only where quality meets
  the agreed floor. Failover is bounded and disclosed; never send data to another
  provider, use a personal key or spend platform funds without the selected policy.
  October 14 local slice: [offline exact-fact evaluator](scripts/evaluate_ai_facts.py)
  and [synthetic controls](docs/ai-facts-synthetic-v1.json) establish a reproducible
  schema/fact-preservation check. The faithful fixture passes 2/2 cases; invented
  and omitted facts fail 0/2. Retry costs/latency count, unknown totals remain null,
  and every candidate must cover the same cases. These are authored controls,
  not outputs from real models or measured provider prices/performance.
  See [scope and reproduction](docs/AI_PROVIDER_CONTRACT.md#ai-004-offline-exact-fact-controls).
  Full resumes/jobs, semantic relevance, real-model comparisons, agreed quality
  floors and routing allowlist decisions remain open. Claude live checks deferred.
- [ ] **AI-005 (P2): Evaluate Mistral as the next adapter, then gateway demand.**
  After AI-002 parity, assess Mistral's incremental value using AI-004; review
  schema, image/OCR support, data handling and total cost before choosing models.
  Evaluate OpenRouter or other compatible gateways only if user demand warrants
  another integration. Acceptance: documented go/no-go and, if selected, the same
  adapter suite. API-shape similarity does not prove parity; arbitrary user-supplied
  base URLs remain out of scope for the hosted service.

Provider references checked October 7: [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
[Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output),
and [Mistral custom structured outputs](https://docs.mistral.ai/studio/conversations/structured-output/custom).
These support adapter investigation, not a model/pricing commitment or proof of
commercial eligibility. Confirm current API terms and supported models per adapter.

### Free and paid service — concrete delivery items

Proposed starting packaging, pending MON-001/002 decisions:

| Plan | Intended value | AI and other API responsibility |
|---|---|---|
| Free | Usable job tracking, manual import, personal-data export and bounded discovery; AI-assisted actions available with supported BYOK | User pays their chosen AI provider; platform search has a separate published allowance, never unlimited because a user supplies an AI key |
| Pro with included AI | Higher bounded workflow/search allowances and keyless scoring, parsing and tailoring | CareerTrellis pays approved providers from a capped included allowance; cover discovery and required maps/geocoding too, so the advertised keyless journey really works |
| Optional Pro BYOK mode | Paid workflow benefits while using the user's chosen supported provider | Explicitly separate platform fees, included credits and personal-provider usage; decide any discount only after cost evidence |

No price, quota, rollover, free AI trial or overage policy is approved here.
Start with Free and one Pro subscription; defer passes and credit packs. Never
block personal-data export/deletion or access to existing materials on downgrade.

- [ ] **MON-001 (P1): Specify the plan and entitlement matrix.** Decide exact free
  and Pro features, search/run/AI/storage allowances, reset periods, treatment of
  existing beta accounts, BYOK coexistence, downgrade behavior and exhausted-limit
  UX. Include whether a tiny managed trial is affordable; default to none until
  bounded and approved. Acceptance: one product matrix shared by UI and server,
  a no-key paid journey and explicit decisions for every allowance; preserve a
  useful free workflow rather than requiring payment merely to retrieve user data.
  October 7 proposal: [versioned Free/Pro matrix](docs/monetization/README.md)
  specifies allowances, payer selection and downgrade/exhaustion behavior. It is
  not loaded by runtime code and awaits owner acceptance; no beta limits changed.
- [ ] **MON-002 (P1): Model unit costs before setting prices.** Use AI-001 receipts
  and SEARCH-002 workload counts to estimate typical and heavy-user discovery,
  AI/OCR, retries, maps, storage, compute, support and payment costs. Separate
  temporary prepaid credits from sustainable replacement cost. Acceptance:
  dated assumptions, light/typical/heavy scenarios, contribution margin and
  proposed price/allowance caps with downside sensitivity. Actual provider and
  payment quotes/terms require fresh official-source verification; no invented
  margin or recurring allowance based on today's finite Serper balance.
  October 7 implementation: [reproducible illustrative cost worksheet](docs/monetization/README.md)
  includes official dated AI/discovery prices, three synthetic scenarios and
  doubled-cost sensitivity. Missing operating/payment costs keep total cost and
  margin unknown; workload measurement and complete economics remain open.
  October 13: the offline receipt summary in docs/monetization/README.md connects
  AI-001 receipt exports to cost review. It includes failed/retried attempt costs,
  explicit unknown-cost coverage, incomplete-capture guards and provider/model/
  operation groups. The reproducible five-attempt fixture is entirely synthetic;
  monthly workload, non-AI costs and margins remain unknown. No provider calls,
  telemetry, billing or plan decisions; Claude live verification remains deferred.
  Verification: 17 offline cost tests passed; 435 existing regressions ran with
  two skips and no failures/errors. Syntax, exact fixture replay, 35 local links
  and disposable loopback startup/shutdown passed. Local evidence:
  artifacts/mon002-usage-summary-20261013/completion.json.
- [ ] **MON-003 (P1): Enforce server-side entitlements.** Depends on MON-001.
  Add versioned plan state and additive migration for existing users; authorize
  features at API admission and worker execution, including scheduled jobs.
  Acceptance: cross-user, stale session, tampered client, expired subscription,
  queued downgrade and concurrent-request tests pass; safe existing-data access
  remains available. A checkout success page cannot grant paid access.
- [ ] **MON-004 (P1): Build a durable usage and spend ledger.** Depends on AI-001
  and MON-001/002/003. Reserve bounded estimated usage before dispatch, settle
  actual usage where known, and reconcile interrupted/ambiguous calls without
  releasing potentially spent credit prematurely. Track AI and discovery
  separately with per-account, per-run, period and platform hard limits; reuse
  applicable Serper safeguards without resetting its existing counters.
  Acceptance: retry/idempotency, concurrency, restart, cancellation, unknown usage,
  renewal/time-boundary and refund tests prevent double charges or overspend;
  users see remaining allowance, reset date and a clear exhaustion action.
- [ ] **MON-005 (P1): Provide managed AI for paying users without personal keys.**
  Depends on AI-001, MON-003/004 and an approved provider/model budget. Resolve
  the payer explicitly for each operation; isolate server-held platform credentials
  from BYOK credentials and tenant data, prohibit browser exposure, and retain
  bounded privacy-safe usage records. Include data-processing disclosures, key
  rotation, emergency disable, concurrency limits and account-abuse controls.
  Acceptance: synthetic empty-key account completes search, scoring, tailoring
  and downloads (plus parsing/maps when used); free/expired/exhausted accounts
  cannot spend platform funds. No automatic BYOK-to-platform fallback or top-up.
- [ ] **MON-006 (P1): Implement subscription billing in a sandbox first.** Depends
  on MON-001/003/004 and selection of a payment processor after fee, hosted checkout,
  customer portal and billing-policy review. Use verified, idempotent webhooks
  with reconciliation for delayed, duplicate, missing and out-of-order events;
  bind accounts server-side. Acceptance: purchase, renewal, failed payment,
  cancellation, refund, dispute and end-of-period downgrade tests keep billing,
  entitlements and usage consistent; expose renewal date and easy cancellation.
  Resolve tax/invoice, privacy and refund obligations before live charging; do
  not store card details or activate automatic overages.
- [ ] **MON-007 (P1, gated): Run a capped keyless paid pilot.** Depends on
  MON-002 through MON-006 and AI-004. Reverify hosted tenant isolation, secret
  boundaries, entitlement enforcement, queue fairness, support and rollback on
  the exact candidate; preserve current admission limits until separately changed.
  Acceptance: approved participant/spend caps and live payment/provider scope,
  complete no-key onboarding-to-download journey, reconciled provider/billing
  receipts, cancellation/downgrade behavior, and an operational stop switch that
  preserves account data. Define success and stop thresholds before starting;
  billing activation and provider spending still need their own authorization.
- [ ] **MON-008 (P2): Measure activation, retention and paid value.** Define an
  opt-in aggregate funnel for signup, first useful save, first tailored download,
  quota exhaustion, upgrade and return usage, segmented only as needed by plan.
  Depends on MON-001; paid conclusions depend on MON-007. Acceptance: bounded
  retention, no resume/job/search text or secrets in events, clear denominators
  and small-sample limitations; review useful outcomes and realized cost together
  before changing prices, adding packs/passes or expanding the account cap.

## October 7 — landing, sign-in UX and public address

Owner: CareerTrellis product/UX. Status: UX-001, UX-003 and UX-004 deployed;
broader acceptance remains open. UX-002 review is pending. Review at the next
UX slice. These items do not add launch gates or override existing deferrals.

- [ ] **UX-001 (P1): Simplify the landing actions.** Hunter reports that **Cancel
  this attempt** appears only to disable **Create account**, and **Use a different
  account** opens the same AuthKit sign-in page as **Sign in**. Default to **Sign
  in** and **Create account** when registration is available. Show cancellation
  only during a real pending attempt, with an explicit result and a working retry;
  do not leave registration silently disabled. Keep account switching in a
  relevant signed-in/recovery context only if it actually changes account
  selection. Confirm the deployed implementation before removing controls;
  identical destinations alone do not establish identical session behavior.
  Acceptance: fresh visit, pending/canceled attempt, retry, browser Back,
  registration unavailable and existing-provider-session cases have clear,
  recoverable outcomes without weakening authentication or registration policy.
  October 7: deployed in build 20261007.82; real pending/completing cancellation
  retained. Public/browser landing verified. Broader edge-case acceptance remains
  open. Deployment result (local-only evidence: `artifacts/LANDING_UX_DEPLOYMENT_20261007.md`).
- [ ] **UX-002 (P2): Review the UI against usability best practices and WCAG 2.2
  AA.** Start with the reported excessively wide buttons; use content-sized desktop
  actions and bounded form widths, allowing full-width mobile actions where
  helpful. Review action hierarchy, labels, spacing, feedback, errors, loading and
  disabled states across landing and workspace flows. Verify keyboard operation,
  visible/unobscured focus, accessible names, contrast, zoom/reflow and mobile
  layouts. Record findings, severity, screenshots/viewports and manual plus
  automated evidence. Wide buttons are a usability concern, not automatically a
  WCAG failure. Target size must meet 24 by 24 CSS pixels or an applicable WCAG
  exception; prefer roughly 44-pixel touch targets where practical. Sources:
  [WCAG 2.2](https://www.w3.org/TR/WCAG22/) and
  [target-size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum).
  October 13 local slice: saved-provider tests restore keyboard focus after
  success, failure and cancellation; model guidance is associated with its input.
  Profile settings stack at widths up to 900 pixels, fixing the narrow two-column
  phone layout. Synthetic browser checks cover 320, 390, 900 and 1280 pixels,
  dialog focus containment and duplicate-request prevention. All 449 tests pass
  with two skips; temporary app startup and cleanup pass. Before/after screenshots
  and receipts are retained locally in `artifacts/ux002-provider-focus-20261013/`.
  Broader UX-002, physical-device/assistive-technology and hosted acceptance remain
  open; this is not a WCAG conformance claim. Claude live verification is deferred.
- [ ] **UX-003 (P1): Enter the workspace automatically after successful sign-in.**
  Remove the routine **Continue to workspace** step. Once the application session
  and authorization are established, navigate directly to the workspace (or a
  validated same-origin intended destination). Preserve required verification,
  onboarding and denied-access states with actionable explanations. Acceptance:
  returning/new user, existing session, expired session, denied access, refresh
  and Back do not create loops, bypass authorization or require the redundant
  confirmation. Add focused redirect/session tests and browser acceptance.
  October 7: deployed in build 20261007.82. Signup/login synthetic browser and
  invalid-session regressions pass; attended live returning-user sign-in now
  verified at the dashboard with no Continue to workspace control.
  Deployment result (local-only evidence: `artifacts/LANDING_UX_DEPLOYMENT_20261007.md`).
- [ ] **UX-004 (P1): Adopt a product-facing canonical hostname.** Recommendation:
  `careertrellis.jhunterj.com`, optionally with `ct.jhunterj.com` as a short redirect.
  Hunter selected `careertrellis.jhunterj.com` and requested work begin on October 7.
  Cutover preparation (local-only evidence: `artifacts/HOSTNAME_CUTOVER_PREPARATION_20261007.md`) retains
  the inspected baseline and rollback rationale. Local hostname-aware privacy
  guard/template is prepared in the isolated `careertrellis-hostname` worktree:
  382 tests passed (two expected skips), syntax passed.
  The approved configuration cutover is live. DNS, TLS, WorkOS defaults, legacy navigation,
  existing-user sign-in, session refresh and sign-out verified. No hosting
  purchase or wrapper used. Result, evidence and remaining acceptance limits (local-only evidence: `artifacts/HOSTNAME_CUTOVER_RESULT_20261007.md`).
  Source-template reconciliation is deployed in build 20261007.82 and published
  in [draft PR #83](https://github.com/jhunterjActual/job-applier-web/pull/83).
  UX-004 remains open for unexercised registration/recovery and identity-provider
  variation acceptance on the canonical hostname; reuse completed sign-in/logout
  evidence. Mobile and assistive-technology acceptance remains with UX-002.
  Evaluation and migration acceptance follow below.

### UX-004 domain evaluation — October 7, 2026

| Option | User experience | Relative implementation cost |
|---|---|---|
| `careertrellis.jhunterj.com` | Recommended canonical address: recognizable full product name, easy to explain; longest to type. | Same hosting/DNS/authentication work as the alternatives. |
| `ct.jhunterj.com` | Easiest to type; initials give little product context. Useful optional short alias. | Same cost as canonical; a redirect alias is a small additional DNS/TLS/routing task. |
| `ctrellis.jhunterj.com` | Shorter but an unfamiliar abbreviation that must be explained or spelled. | No technical or recurring-cost advantage over the full name. |

Planning estimate, not a quote: about half to one engineering day for a single
canonical-host cutover and verification, assuming existing DNS, hosting and
WorkOS access and no environment migration; allow additional time for provider
policy changes, session-transition issues or rollout constraints. All three are
subdomains of an already owned domain, so no additional domain registration is
needed. Expect no incremental hosting charge if the current server is reused.
Cloudflare provides free Universal SSL for first-level subdomains in a full DNS
setup; verify the actual zone, proxy mode, certificate status and origin TLS
before execution. No account billing or current DNS inventory was checked here.
See [Cloudflare certificate coverage](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/).

Serve the existing application directly on the chosen hostname through the
existing reverse proxy. Redirect ordinary visits from `staging-app.jhunterj.com`
to the canonical host; initially use a temporary redirect while validating, then
make it permanent. A redirect from the new address back to staging would leave
the staging name in the address bar. An iframe wrapper adds navigation, focus,
login and cookie complications, and the retained
open-beta Caddy configuration (local-only evidence: `artifacts/Caddyfile.open-beta-20261006`) explicitly
sets `X-Frame-Options: DENY`; do not weaken that protection to disguise the URL.
The tracked staging Caddy template is older and is not proof of live state.

Implementation scope: refresh the active deployment/configuration first; update
DNS, Caddy site/TLS, application allowed hosts and canonical origin, WorkOS exact
callback/logout URLs, and public links/bookmarks/help text. Host-only sessions
may require users to sign in again; do not broaden cookie scope just to share
sessions. Register the new callback before sending users to it. Handle in-flight
old-host callbacks deliberately on their original origin or expire/restart them
clearly; never blanket-redirect authorization codes/state between hosts. Preserve
callback query-log suppression and existing trust checks. Verify new/returning
sign-in, sign-up policy, logout, password recovery, safe deep links, old-host
redirects and rollback before final cutover. WorkOS requires a registered matching
[AuthKit redirect URI](https://workos.com/docs/reference/authkit/authentication/get-authorization-url).

Changing the application hostname does not itself replace the `authkit.app`
hosted sign-in hostname. WorkOS custom authentication domains are a separate
production-environment feature; confirm eligibility and pricing only if that
additional branding is requested. See [WorkOS custom domains](https://workos.com/docs/custom-domains).

Documentation-only update: application tests, runtime/browser acceptance and
Docker/storage cleanup are not applicable to this edit. Implementation and live
acceptance remain unchecked above.

## October 6 — explicitly deferred until after open beta

The operator directed that added safeguards, backstops and plans stop delaying
launch. These are deferred work, not completed acceptance. Older entries below
retain history but do not reinstate these items as launch gates. Open beta launched
October 6. The completed launch checklist is at the top of
[BETA_BLOCKERS](docs/BETA_BLOCKERS.md); October 7 delivery status above takes
precedence over the historical release/address references below.

| Item | Post-beta scope and retained limitation |
|---|---|
| POST-BETA-UBUNTU-MAINTENANCE-20261006 | Operator supplied an SSH banner reporting Ubuntu 24.04.4 LTS, kernel 6.8.0-138, 46 available updates including 3 standard security updates, and a required restart. The hostname confirmed this is staging WEB despite differing ingress/egress IPs. Refresh the package/security and reboot status. Schedule applicable 24.04 updates and a controlled reboot with post-reboot application/backup checks. Separately assess the offered 26.04.1 upgrade for compatibility; the banner alone does not authorize an OS upgrade or paid Ubuntu Pro/ESM enrollment. Counts are an operator-provided snapshot, not a current verified inventory. Owner: CareerTrellis operations; post-beta review is now due. |
| POST-BETA-OWNER-RECOVERY-20261006 | Additional owner bootstrap codes, reserve credentials and recovery redundancy/dependencies remain open. The owner-assisted administrative transition completed for launch; do not repeat it. Establish the intended recovery route and verify custody/usability before declaring recovery accepted. When a new credential is needed, explicitly say it is new, where it is created and the exact Bitwarden item/field to save; neither the existing owner SSH key nor the Recovery R2 credential is an owner recovery code. Preserve existing credential state and evidence. |
| POST-BETA-OPTIONAL-MFA-RECOVERY-20261006 | Optional TOTP enrollment, factor removal and general-user recovery provisioning and hosted acceptance. Keep all three feature flags at0 for launch; do not claim these enabled flows are accepted. |
| POST-BETA-COVERAGE-MATRIX-20261006 | Expanded/repeated coverage matrices and coverage improvement. Existing broad coverage limits remain disclosed; no fabricated pass, marketing-scope rename, new paid search or source authorization follows from this deferral. |
| POST-BETA-ADDITIONAL-RECOVERY-DRILLS-20261006 | Additional paired restore rehearsals and missed-monitor drills. Preserve working backups, scheduled monitoring, alerts and retained recovery evidence; deferral does not establish untested restore/notification behavior. |

Owner: CareerTrellis beta maintenance. Review after open beta; no automatic
execution, deletion or paid-service authorization. Existing post-beta staging
memory/plan work remains deferred separately.

### October 7 — deferred-work reconciliation

This is the checklist of work left behind on the path to beta, reviewed against
the [launch scope and execution history](docs/BETA_PATH_20261003.md),
[explicit scope reduction](docs/BETA_BLOCKERS.md) and
cleanup handoff (local-only evidence: `artifacts/BETA_CLEANUP_20261006.md`). Existing IDs remain the
canonical work items; this index does not duplicate them or authorize execution.
All rows below remain open unless their cited current item records completion.

| Existing item | Remaining work / completion evidence | Owner and next review |
|---|---|---|
| POST-BETA-OWNER-RECOVERY-20261006 | Document and verify owner recovery custody and a usable recovery path without assuming a missing code was saved. Preserve uncertain historical provider acknowledgements; current empty-session observations do not recreate those acknowledgements. | Identity maintenance; next recovery work selection. |
| POST-BETA-OPTIONAL-MFA-RECOVERY-20261006; BETA-ID-02 | Reconcile implemented removal/recovery code with the deployed version; complete only missing native and attended enrollment, removal, lost-factor/assisted recovery, replay/concurrency and session-revocation acceptance. Verify custody, interrupted/ambiguous outcomes and recovery hold/release. Keep the three optional feature flags disabled until their own acceptance/activation decision. | Identity maintenance; before enabling any optional factor/recovery flow. |
| BETA-ADM-01 | Map omitted native fixtures to later passing receipts and list only still-uncovered admission/session/isolation boundaries. Completed owner/new-user dashboard, persistence and logout checks are not a claim that every earlier platform-skipped test passed. Do not redo owner transition or reopen signup. | Identity maintenance; next identity change or paid-pilot acceptance. |
| POST-BETA-COVERAGE-MATRIX-20261006; COV-10 through COV-15; SEARCH-002 onward | Retain the failed broad six-cell evaluation and complete reference-set/recall work and a separately bounded repeated coverage evaluation. The October 7 Serper activation supersedes its former disabled state, but is not proof of useful yield or permission for added spend. | Search product/engineering; current search tranche, expanded matrix after targeted improvements. |
| BETA-BKP-01; BETA-BKP-03; POST-BETA-ADDITIONAL-RECOVERY-DRILLS-20261006 | Reuse recovered receipts; resolve residual paired DB/object reconciliation, independent key recovery and authenticated decryption evidence, full-service RPO/RTO objectives, provider-snapshot applicability, and retention/recovery-policy decisions. Scope additional restore/promotion/checksum drills only for actual gaps. | Backup maintenance; next recovery-policy review, before claiming full-service recovery acceptance. |
| BETA-BKP-02; POST-BETA-ADDITIONAL-RECOVERY-DRILLS-20261006 | Complete residual missed-monitor and notification-policy acceptance, including applicable stale/failure/recovery, deduplication and acknowledgement/escalation behavior. Existing channel activation, repaired mirror verification and confirmed database alert/recovery delivery are complete and must not be repeated merely to close this item. Track the subsequent scheduled mirror-lag defect under CT-BACKUP-LABEL-GAP-20261007. | Backup maintenance; next notification maintenance slice; active failures take P0 precedence. |
| POST-BETA-UBUNTU-MAINTENANCE-20261006 | Refresh package/reboot status, apply selected 24.04 security updates and schedule the required controlled reboot with service/backup checks. Hostname was confirmed as staging WEB; differing ingress/egress addresses alone do not imply a second host. Assess the offered major-version upgrade separately. | Operations; post-beta maintenance review now due. |
| POST-BETA-STAGING-RAM-4G-20261002 | Retain the exact 4 GiB CPU/RAM-only plan change, staging power-down/restart, return to 512 MiB reserve/1.5 GiB floor and post-resize checks in the item below. Current 256 MiB reserve remains until that change; the milestone review is due, not silently replaced by a new prerequisite. | Operations / Hunter for cost and downtime; beta milestone reached. |
| POST-BETA-RETAINED-RESOURCES-20261007 | Review the retained trial account/profile, recovery/repair evidence, old configuration and rollback copies, task verification environment, Git worktrees and audit snapshots against their existing resource registers. Decide exact retirement/retention per resource and verify ownership/references before any cleanup; completed 67-runtime/three-archive removals need no replay. | Beta maintenance; stabilization and next storage review. |

The broader pre-launch branding/replaceable-name and product improvements remain
in their existing backlog entries and the October 7 priority policy above.
Completed launch work (provider session reconciliation, owner binding, release
activation, public signup/shared-password removal and core hosted checks) is not
deferred work. Historical preparation failures and superseded attempts are
evidence, not additional implementation tasks. No new beta gates are introduced.

Latest validation: Source validation completed: the exact R2 object matches its manifest and accepted backup receipt (local-only evidence: `artifacts/ORIGINAL_OUTAGE_SOURCE_VALIDATION_STAGE3_RESULT_20261005.md`). All three approved units completed; cleanup verified and batch exhausted. No provider objects changed. Fresh destination checks and conditional repair require a separately approved scope. The next combined repair batch is prepared for approval (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_REPAIR_READINESS_20261005.md`); its first stage is configuration-only, with later writes gated by fresh evidence and independent review. Earlier entries are historical; beta remains closed.

Latest result: Focused hashes confirm a 272-byte R2 backup payload and an empty OneDrive copy (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_CONFLICT_HOSTED_RESULT_20261005.md`). Metadata remained stable; all six commands and exact cleanup passed. A consolidated source-validation scope (local-only evidence: `artifacts/ORIGINAL_OUTAGE_SOURCE_VALIDATION_SCOPE_20261005.md`) is ready for one approval, covering up to three dependent stages with local implementation/review gates. Source validity and repair authority remain open. Earlier entries are historical; beta remains closed.

Latest evidence: Retained inventories contain 13,039 matching paths and one size disagreement (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_RETAINED_HOSTED_RESULT_20261005.md`). No enrolled source object is missing; content-check completion remains unproven. The focused single-object diagnostic (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_CONFLICT_HOSTED_APPROVAL_20261005.md`) is ready for approval after 13 focused checks, source assembly and independent review, preserving both copies and all resource limits. Earlier entries are historical; beta remains closed.

Current result: Filtered batch returned no accepted inner observation; owned unit/cgroup cleanup verified (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_FILTERED_HOSTED_RESULT_20261005.md`). Stopping cause, enrollment and retained-file inventory remain unknown. Exact retained-file and lifecycle inspection (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_RETAINED_HOSTED_APPROVAL_20261005.md`) is ready for approval after15 checks and independent review, without provider access. Earlier entries are historical; beta remains closed.

Current blocker: Compact destination listing still exceeds the 4 MiB cap (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_COMPACT_HOSTED_RESULT_20261005.md`). Source normalization and exact cleanup passed. A source-filtered comparison (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_FILTERED_HOSTED_APPROVAL_20261005.md`) is ready for approval after19 checks and independent review, with unchanged limits and public enrollment counts. Beta and backup readiness remain open; earlier entries are historical.

Latest finding: Detailed comparison hit the destination listing's 4 MiB capture cap (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_DETAIL_HOSTED_RESULT_20261005.md`). Process, lock and diagnostic-unit cleanup passed. Compact listings and conditional comparison (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_COMPACT_HOSTED_APPROVAL_20261005.md`) are ready for approval after 17 checks and independent review, without raising memory or output limits. Content comparison and backup readiness remain unverified; prior entries are historical.

Latest result: Comparison refused during destination listing; lock released and diagnostic cleanup verified (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_HOSTED_RESULT_20261005.md`). Source listing was retained and validated, but exact refusal cause is unknown. A detailed diagnostic and conditional comparison batch (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_DETAIL_HOSTED_APPROVAL_20261005.md`) is ready for approval after 19 focused checks and independent source review. The consumed attempt will not be replayed; beta remains closed. Prior entries below are historical.

Current finding: Mirror incident inspection completed with verified cleanup (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_INCIDENT_HOSTED_RESULT_20261005.md`). The authenticated wrapper refused an immutable payload change; conflicting objects and cause remain unknown. One bounded provider comparison (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_COMPARE_HOSTED_APPROVAL_20261005.md`) is ready for approval after 21 focused checks and independent source review. It preserves both copies; possible OAuth refresh and retained private evidence are explicit. Backup readiness and beta remain open. Prior entries below are historical.

Current blocker: Current valid status reports mirror failure, stale mirror and label mismatch (local-only evidence: `artifacts/ORIGINAL_OUTAGE_BACKUP_STATUS_REASONS_HOSTED_RESULT_20261005.md`). Diagnostic cleanup verified. Bounded mirror incident inspection (local-only evidence: `artifacts/ORIGINAL_OUTAGE_MIRROR_INCIDENT_HOSTED_APPROVAL_20261005.md`) is ready for approval after 16 checks and source review; no restart, alert reset or backup-readiness claim. Earlier entries below are historical.

Current gate: Corrected backup capture passed; status validation refused (local-only evidence: `artifacts/ORIGINAL_OUTAGE_BACKUP_CORRECTED_HOSTED_RESULT_20261005.md`). Exact diagnostic cleanup verified. Comprehensive status classification (local-only evidence: `artifacts/ORIGINAL_OUTAGE_BACKUP_STATUS_REASONS_HOSTED_APPROVAL_20261005.md`) is prepared for approval after 11 tests and source review; backup readiness remains unproven. Earlier entries below are historical.

Current October 5 finding: Status-file diagnostic completed; collector root mismatch found (local-only evidence: `artifacts/ORIGINAL_OUTAGE_STATUS_CAPTURE_HOSTED_RESULT_20261005.md`). The wrapper uses database/objects subdirectories that the collector omitted. Corrected full DB backup diagnostic (local-only evidence: `artifacts/ORIGINAL_OUTAGE_BACKUP_CORRECTED_HOSTED_APPROVAL_20261005.md`) is ready for approval after 12 focused checks and source review; actual backup status is not yet verified. Previous preparation and results below are historical.

Latest diagnosis: Old unit absent; new diagnostic cleaned up; status capture refused (local-only evidence: `artifacts/ORIGINAL_OUTAGE_BACKUP_DIAGNOSTIC_HOSTED_RESULT_20261004.md`). The fixed status-file read failed before backup validation. Original path and 0600 writer/reader policy match locally; exact read failure remains unknown. The fixed status-file diagnostic (local-only evidence: `artifacts/ORIGINAL_OUTAGE_STATUS_CAPTURE_HOSTED_APPROVAL_20261004.md`) is ready for one approval after 15 focused checks and independent reader/delivery reviews. No new host call ran.

Historical preparation (subsequently executed; latest result above): Exact DB-unit reconciliation and conditional backup diagnostic (local-only evidence: `artifacts/ORIGINAL_OUTAGE_BACKUP_DIAGNOSTIC_HOSTED_APPROVAL_20261004.md`) was prepared with one approval scope. Thirteen focused checks and independent source reviews passed; original validators and failed claims remain unchanged. No further host action has run.

Historical batch result (current unit absence resolved above): Three stages passed; DB backup stopped with uncertainty (local-only evidence: `artifacts/ORIGINAL_OUTAGE_PREFLIGHT_BATCH_HOSTED_RESULT_20261004.md`). Queues are empty, signup closed and schema remains `20260907_0014`; the three accepted units report cleanup. Backup-unit cleanup and backup readiness are unverified, and final WEB comparison did not run. The approved attempt is consumed; local reconciliation/diagnostic preparation continues without a hosted retry.

Historical batch preparation (subsequently executed): One approved execution of the five-stage staging observation batch (local-only evidence: `artifacts/ORIGINAL_OUTAGE_PREFLIGHT_BATCH_HOSTED_APPROVAL_20261004.md`). Preparation and independent source review are complete; no hosted observations or real attempt claims have run. The prior installed Context batch passed.

Latest preflight integration: Read-only observation readers and bounded entry passed local checks (local-only evidence: `artifacts/ORIGINAL_OUTAGE_PREFLIGHT_INTEGRATION_20261004.md`). A separate catalog successor corrects the shared B/P archive identity; the installed package is unchanged. Authenticated metadata and bounded observation composition are in progress before the next concrete hosted scope.

Latest runtime gate: Hosted installed Context verification (local-only evidence: `artifacts/ORIGINAL_OUTAGE_CONTEXT_HOSTED_RESULT_20261004.md`) passed in 120.887 seconds after the approved source installation. The actual operational runtime, interpreter, 149 source records and 472 target files verified; exact scratch cleanup and unchanged application service states/PIDs/restarts passed. Sources and public journals remain retained. Fresh outage preflight, stop-specific authority and hosted lifecycle acceptance remain open. Beta is not open; the 256 MiB reserve and post-beta 4 GiB upgrade remain unchanged.

Latest loader gate: The source-only stop loader (local-only evidence: `artifacts/ORIGINAL_OUTAGE_STOP_SOURCE_LOADER_RESULT_20261004.md`) passed independent review, nine main checks and a supplemental partial-execution regression. The 149-file authentication and fixed 21-module private graph are locally verified over modeled reads. Next: sealed-runtime preparation, preparation/action supervision and native composed acceptance before a hosted stop scope.

Latest source acceptance: Supervisor (local-only evidence: `artifacts/ORIGINAL_OUTAGE_STOP_SUPERVISOR_RESULT_20261004.md`) and child release reader (local-only evidence: `artifacts/ORIGINAL_OUTAGE_STOP_RELEASE_READER_RESULT_20261004.md`) passed independent review and thirteen/seven modeled checks. The next local boundary is authenticated source/runtime integration (local-only evidence: `artifacts/original-outage-runtime-integration-plan-20261004.md`); installed entry, native lifecycle and hosted acceptance remain open.

October 4 local advance: Durable stop-owner binding (local-only evidence: `artifacts/ORIGINAL_OUTAGE_STOP_OWNER_RESULT_20261004.md`), boot identity reading (local-only evidence: `artifacts/ORIGINAL_OUTAGE_BOOT_IDENTITY_RESULT_20261004.md`), and unspecified-TLS input composition (local-only evidence: `artifacts/APPLICATION_OBSERVATION_ORIGINAL_TLS_RESULT_20261004.md`) passed their independent local reviews. The controller's external timeout and atomic release handoff are being completed before sealed-runtime entry integration. Hosted lifecycle and beta acceptance remain open; the approved 256 MiB reserve and post-beta 4 GiB upgrade with restoration of the 512 MiB reserve remain unchanged.

October 4 current gate: The approved runtime/configuration read passed (local-only evidence: `artifacts/RUNTIME_CA_READ_RESULT_20261004.md`): exact runtime verification took 36.407 seconds, with no DB connection or hosted change. The protected URLs omit sslmode; the original WireGuard contract permits that, so this is a new input-builder representation gap, not authority to change TLS settings. Local work now binds stop approval to the same durable publisher owner and preserves unspecified TLS mode explicitly. Hosted lifecycle and beta acceptance remain open.

October 4 recovery advance: Exact approved recovery and ten-case native journal validation (local-only evidence: `artifacts/OPERATIONAL_OUTAGE_READINESS_20261004.md`) now pass; both synthetic roots were removed and independently reviewed. This supersedes the pending cleanup below. Protected receipt native composition is next; no hosted changes or beta opening.

October 4 outage evidence update: The 38-phase contract, receipt validator and protected journal reader (local-only evidence: `artifacts/OPERATIONAL_OUTAGE_READINESS_20261004.md`) pass 26 current source checks. The native journal fixture passed nine cases before an OS error; its exact synthetic root is retained pending explicit cleanup approval after auto-review rejected deletion. Diagnostics are improved without relaxing the reference scanner. Protected receipt retrieval and the actual outage controller remain in progress; beta is not open.

October 4 operational integration update: The native operational TLS run (local-only evidence: `artifacts/APPLICATION_OPERATIONAL_TLS_RESULT_20261004.md`) passed its first attempt and independent review: exact operational parent transport, generated freshness adapter, three brokered TLS sessions, six operator sessions, 189 reads, two windows and full cleanup. The synthetic worker seam excludes production factory inputs and installed runtime. Authenticated input and outage/reconciliation integration remain open; local phase classification supplies no hosted action or replay authority.

October 4 operational source update: Transport and freshness successors (local-only evidence: `artifacts/APPLICATION_OBSERVATION_MATERIALIZED_20261004.md`) have nineteen passing pure checks and independently verified materialized bytes. The endpoint forwards a required trusted callback and enforces original expiry around descriptor installation. Full session binding and native verification of this exact composition remain the next local work; source packaging does not establish hosted acceptance.

October 4 TLS update: All five local PostgreSQL TLS cases (local-only evidence: `artifacts/APPLICATION_POSTGRES_TLS_RESULT_20261004.md`) passed independent provenance and acceptance review, including wrong CA, identity mismatch, no-TLS fallback refusal and injected disposal uncertainty. All seventeen test roots were removed with verified exact recovery; negative runs preserve unknown inner cleanup and verified outer containment. The success case completed three brokered sessions, six operator sessions, 189 facts and two proof windows with unchanged state. Operational transport packaging and downstream freshness enforcement are the next local slice. Installed-runtime/protected-state, hosted TLS/CA and hosted execution remain open; no beta-opening authority follows.

October 4 update: Actual client WEB/DB/WEB convergence (local-only evidence: `artifacts/APPLICATION_POSTGRES_CONVERGENCE_RESULT_20261004.md`) passed in 2.804 seconds with three brokered WEB sessions, six read-only operator sessions, unchanged DB/role digest and complete cleanup of both attempts. Whole-job peak789,950,464 bytes; zero memory events. Installed runtime/protected-state, TLS/CA and hosted acceptance remain open. Earlier entries retain their dated scope.


October 4: Combined real PostgreSQL rehearsal (local-only evidence: `artifacts/APPLICATION_POSTGRES_COMBINED_SCOPE_20261003.md`) passed one case in 2.905 seconds with three migration sessions/disposals, unchanged DB/role digest, and complete cleanup. Both attempt roots were removed after repairing the three-manifest cleanup count. Whole-job peak was 791,830,528 bytes with zero memory events. Full WEB/DB/WEB convergence, authenticated installed runtime/protected state, TLS/CA and hosted acceptance remain open. Earlier entries below describe their dated boundaries.


October 3: DB owner/coordinator integration (local-only evidence: `artifacts/APPLICATION_DB_DISPATCH_READINESS_20261003.md`) passes eight owner checks, eighteen coordinator checks, native fixed-peer echo and three launched endpoint checks with synthetic DB ownership. Constructor and timeout cleanup uncertainty fixes passed independent review. Full native SQL composition remains open. The protected-path inventory (local-only evidence: `artifacts/APPLICATION_PROTECTED_METADATA_SCOPE_20261003.md`) subsequently passed its specifically approved hosted read: 34 metadata records in 2.7558 seconds, no remote changes. Current pre-outage release and absent future B/migration receipts are confirmed. The strict manifest (local-only evidence: `artifacts/APPLICATION_PROTECTED_MANIFEST_READINESS_20261003.md`) now passes eight checks and remains non-installable. All four approved native network composition cases (local-only evidence: `artifacts/APPLICATION_DB_COMPOSITION_SCOPE_20261003.md`) now pass in 5.848 seconds, with three real echo relays, 189 synthetic host reads and verified cleanup. Real SQL/TLS and protected-read integration remain open. The descriptor and combined filesystem helpers (local-only evidence: `artifacts/APPLICATION_PROTECTED_COMBINED_READINESS_20261003.md`) pass 12 and 20 mocked checks plus all four native filesystem/lock categories in 0.609 seconds with verified cleanup. The combined wrapper and sealed configuration (local-only evidence: `artifacts/APPLICATION_COMBINED_ADAPTER_READINESS_20261003.md`) pass ten lifecycle and seven configuration/import-order checks; all four combined native cases (local-only evidence: `artifacts/APPLICATION_COMBINED_NETWORK_SCOPE_20261003.md`) pass in 2.541 seconds with verified cleanup. The historical adapter combined batch (local-only evidence: `artifacts/APPLICATION_HISTORICAL_COMBINED_SCOPE_20261003.md`) now passes all four native cases in 6.006 seconds with actual constructor/handler and bindings, three relays, 189 reads and exact cleanup. Installed runtime, real PostgreSQL/TLS and hosted observation acceptance remain open. Observation eligibility is false.

October 3: observation endpoint and narrow locks (local-only evidence: `artifacts/APPLICATION_OBSERVATION_ENDPOINT_READINESS_20261003.md`) now have three passing native channel/cleanup checks and five passing leaf-only lock checks. Complete synthetic client/channel ordering preserves original deadlines and natural-exit cleanup; O_PATH avoids broad lock-directory grants. Actual database-path/read-set composition and hosted acceptance remain open.

October 3: actual confined runtime READY (local-only evidence: `artifacts/APPLICATION_READY_MOUNTFD_SCOPE_20261003.md`) passed after the socket-path correction and retained read-only mountinfo descriptor change. Zero application requests; 97.397 seconds; 704.13 MiB whole-job peak; no task swap/OOM; exact unit/cgroup/files cleanup verified; all three services active afterward. Independent receipt review passed. Local observation-flow integration is next; hosted observation, lifecycle, coverage and beta-opening gates remain open.

This backlog consolidates the code-review, job-seeker persona, product-owner, monetization, and resume/PDF-formatting recommendations.

## P0 - Reliability, safety, and document integrity

### Independent beta gates — September 28, 2026

October2: autonomous idle expiry and driver preparation (local-only evidence: `artifacts/PREPARED_DISPATCHER_READINESS_20261002.md`) closes the idle-listener expiry gap:11 Linux unit tests and5 combined native cases pass under1GiB/896MiB/zeroSwap.62 focused/375 backend tests(two skips) and syntax pass. Public driver packages import on native Ubuntu with all879 files hash-verified; retained45.8MB for next fixture. Real PostgreSQL/full observation and combined controller fault acceptance remain open. No hosted changes.

October2: multiple-connection ownership and local envelope (local-only evidence: `artifacts/PREPARED_CONNECTIONS_READINESS_20261002.md`) passed3 native cases: sequential queued clients, proof expiry and broker refusal. Queued excess client refused at limit;7 descriptor-transfer negatives and8 sealed handoff cases pass. Combined synthetic run verified1GiB/896MiB/zeroSwap/64tasks and exact cleanup. Real adapter/frozen runtime, autonomous idle-proof closure and combined controller fault acceptance remain open. No hosted changes.

October2: private frontend confinement and sealed delivery (local-only evidence: `artifacts/PREPARED_FRONTEND_READINESS_20261002.md`) passed8 source-handoff cases and native two-network-namespace tests, including4 forbidden syscalls, all capability sets cleared, direct parent/peer verification and timeout cleanup.62 focused/375 baseline tests(two skips) and syntax pass. Single-connection primitive only; full adapter connection lifecycle and aggregate controller/worker acceptance remain open. No hosted changes.

October2: broker owner and frontend identity (local-only evidence: `artifacts/PREPARED_BROKER_OWNER_READINESS_20261002.md`) passed14 owner regressions,3 confined native relays under one original deadline and6 peer-refusal checks; native process/namespace cleanup verified.62 focused/375 baseline checks(two skips) and syntax pass. Direct-child peer ownership is verified locally; separate frontend namespace/capability confinement and combined controller/worker integration remain open. No hosted changes.

October2: broker source package and real-driver fixture (local-only evidence: `artifacts/PREPARED_BROKER_PACKAGE_READINESS_20261002.md`) passed8 sealed-handoff cases, authenticated native TCP handoff,62 focused/375 baseline checks(two skips). Frozen37119byte source package retained. Approved PostgreSQL fixture measured3 connections for3 sessions under disable/prefer and1 TLS-refusal connection; exact containers/network removed. Combined owner/frontend/worker integration and hosted maintenance acceptance remain open.

October2: broker confinement and ownership (local-only evidence: `artifacts/PREPARED_BROKER_READINESS_20261002.md`) passes54 focused,375 baseline(two skips),6 native filter cases,3 cgroup/failure cases and synthetic fixed-peer TCP relay. Exact owned resources cleaned. Approved platform probe passed(x86_64,libseccomp2.5.5/API6). Synthetic CA-verified TLS1.3 relay and cleanup also pass; combined-owner packaging/driver timing and maintenance prerequisites remain open. No hosted changes.

October2: isolated relay preparation (local-only evidence: `artifacts/PREPARED_RELAY_READINESS_20261002.md`) verified approved WEB route/DB listener metadata,42 local regressions,375 baseline tests(two skips), syntax and native private-namespace/read-only Unixsocket transport. Owned temporary paths removed; host routes/mounts unchanged. Fixed-destination broker, combined resource/lifetime owner and hosted observation integration remain open.

October2: observation-only continuation (local-only evidence: `artifacts/PREPARED_OBSERVATION_READINESS_20261002.md`) passes29 focused checks and375 baseline tests(two skips), syntax and temporary cleanup. Separate client and pre-dispatch gate admit ephemeral capture plus read-only WEB/DB/WEB observations only. Not installed; constrained database networking and hosted paired-host timing remain open.

October2: combined authenticated SSH delivery (local-only evidence: `artifacts/PREPARED_SSH_READINESS_20261002.md`)
passed the approved hosted READY/CLOSE rehearsal in103.859seconds, zero requests,
verified remote/local cleanup. Bundled postcheck: all three services active,
transient unit absent, no same-package retained roots. Fourteen delivery/runner
checks pass after correcting the Windows ProgramData environment requirement.
Paired-host observation, action-specific authority and maintenance gates remain.
October2: close-only operational package/controller (local-only evidence: `artifacts/PREPARED_SESSION_READINESS_20261002.md`)
is frozen and independently reviewed;13 policy/controller,16 native bootstrap,
two finalization-budget and43 connection checks pass, plus375 baseline/two skips.
READY/CLOSED traverses real local systemd isolation with synthetic runtime; final
ACK follows owned cleanup. Both endpoint/controller reject action requests. No
hosted installation or300-second-envelope approval inferred. Next: authenticated
binary SSH delivery and one concrete hosted close-only workflow including postcheck.

October2: authenticated interactive bootstrap (local-only evidence: `artifacts/PREPARED_INTERACTIVE_READINESS_20261002.md`)
passed15 native synthetic namespace/runtime/worker/Channel tests,43 connection
regressions and375 baseline tests (two skips), syntax and cleanup. Entry/envelope
pins and exact module closure protect loading; exclusive framed stdout and a
separate bounded receipt prevent import diagnostics from corrupting transport.
Full cleanup requires CLOSED plus independent owner verification. Not installed;
operational policy/source freeze and interactive resource-limited outer controller
remain next, followed by SSH/networking/paired-host action acceptance.

October2 current: hosted import rehearsal (local-only evidence: `artifacts/PREPARED_IMPORT_REHEARSAL_READINESS_20261002.md`)
passed real worker READY, zero requests, 650.063 MiB peak and verified cleanup;
subsequent approved inventory confirmed all three services active. This supersedes
the historical full-runtime/import gap below. The approved 256 MiB reserve remains;
post-beta 4 GiB resize/power-down and restoration of 512 MiB remain deferred.

October2: preparation-aware connection (local-only evidence: `artifacts/PREPARED_CONNECTION_READINESS_20261002.md`)
passed 43 local checks plus the 375-test baseline (two skips) and syntax. Separates
up-to-150-second preparation from unchanged 30-second proof freshness, charging
all protocol time to the original launch budget. No frozen package or remote
configuration changed. Next: authenticated interactive bootstrap with framed
stdout, native synthetic verification and reviewed source closure; operational
networking, paired-host timing and action authority remain open.

October2: namespace/snapshot/worker/Channel composition (local-only evidence: `artifacts/PREPARED_ENDPOINT_READINESS_20261002.md`)
passed all 72 local tests. CLOSED now requires both verified worker and snapshot
cleanup; constructor failures retain cleanup uncertainty. Full synthetic native
startup/request/cleanup, syntax, source integrity and namespace cleanup passed.
Operational source closure, full application runtime/imports and hosted gates remain.

October2: Linux namespace lifetime owner (local-only evidence: `artifacts/PREPARED_NAMESPACE_READINESS_20261002.md`)
passed 61 tests, including verified pidfd ancestry, idle deadline, confirmed
detached-child and abrupt-owner-death cleanup. Source, syntax, parent namespace/
mount and temporary cleanup checks passed. Compose authenticated snapshot/worker
startup next; fixed isolated-network owner is not a hosted bootstrap yet.

October2: Linux real-worker Channel integration (local-only evidence: `artifacts/PREPARED_LINUX_CHANNEL_READINESS_20261002.md`)
passed 50 native tests in private PID/mount/network namespaces, including actual
PreparedWorker headers/windows and cleanup. Fixed fractional-window rejection
caused by subtracting epoch timestamps; deterministic regression passes. Parent
mounts/namespace, source hashes and temporary cleanup verified. Operational
namespace owner, full runtime imports and authenticated hosted bootstrap remain open.

October2: Windows subprocess Channel integration candidate (local-only evidence: `artifacts/PREPARED_WINDOWS_CHANNEL_READINESS_20261002.md`)
adds nine full-protocol/composition cases and fixes buffered-final-frame receive
after child exit. Explicit /approve retry passed all 28 combined Windows tests
with no failures, errors or skips. Current baseline also passed: 375 tests (two
skips), backend/four changed-file syntax and verified temporary cleanup. Windows
local verification is complete; Linux/remote bootstrap and hosted gates remain open.

October2: Windows prepared-process owner candidate (local-only evidence: `artifacts/PREPARED_WINDOWS_PROCESS_READINESS_20261002.md`)
adds suspended Job assignment, exclusive pipe transfer, lifetime watchdog and
bounded cleanup accounting. Corrected native /approve rerun passed all 18 tests
with no failures, errors or skips, including unchanged unrelated Job count and
live response after other-owner cleanup. Completion baseline/syntax remain open.
Full Channel integration and hosted bootstrap remain open.

October2: prepared pipe transport candidate (local-only evidence: `artifacts/PREPARED_PIPE_READINESS_20261002.md`)
adds bounded owned-descriptor I/O and real anonymous-pipe regression cases using
the existing review composition. Explicit /approve retry passed all 34 tests on
Windows, with no skips, failures or errors. Separate /approve retry passed all 27
socket regression tests after the shared-fixture edits. Linux verification remains open.
SSH authentication, process ownership and hosted verification remain open.
Required baseline/syntax passed on exact /approve retry: 375 tests (two skips),
backend and changed-file compileall, and verified temporary cleanup. Next reviewed
local slice is the Windows contained session owner.

October2: prepared channel candidate (local-only evidence: `artifacts/PREPARED_CHANNEL_READINESS_20261002.md`)
connects the operator interface and persistent-worker header/window protocol over
an owned stream. After correcting a reply-parameter collision, a fresh /approve
retry passed all 27 isolated tests, with no failures, errors or skips. Exact-action
approval recovery is verified; saved policy revision has not established automatic
execution or human fallback. Actual SSH/bootstrap, namespace
owner, source freeze and hosted timing remain open. No hosted state changed.

October2 continuation: prepared approval client candidate (local-only evidence: `artifacts/PREPARED_CLIENT_READINESS_20261002.md`)
now composes shared-window WEB/DB/WEB checks, publication and reconciliation.
Independent static review found a cleanup-status propagation defect, now corrected
with a regression case. After execution approval routing changed, the identical
custom command passed17tests with no skips. Required baseline passed375tests
(two skips) and backend syntax. These are local synthetic results.
Authenticated remote channel, operational bootstrap,
full-runtime imports and hosted timing remain open. No installed source changed.

October2UTC: original transition readiness (local-only evidence: `artifacts/ORIGINAL_TRANSITION_READINESS_20261002.md`)
now includes a separately authenticated original-only corrected apply/reconcile
entry and closure/runtime-bound approvals. Forty focused tests and the375-test
baseline pass (two expected baseline platform skips); public source package frozen.
Not installed. Remaining: full evidence-action publication/authorization, durable
outage controller and paired-host/provider/timed acceptance. Reserve stays fallback.

October2UTC: action-specific setup preparation (local-only evidence: `artifacts/SUCCESSOR_ACTION_SETUP_20261002.md`)
defines six separate authorization contracts and empty-state provisioning.
Default/manual permissions resolved execution review: six Linux tests passed and
read-only WEB inspection succeeded. Current original v1 selection and absent reserve
plan-binding slots block successor authorization. No remote setup files were created.

October2UTC: approved source-only staging installation completed (local-only evidence: `artifacts/SUCCESSOR_SOURCE_INSTALLATION_RESULTS_20261001.md`).
R3 sources/runtime guard installed and independently read back;33 protected files
481888bytes retained. Library/runtime verified, .78/services/recovery state preserved,
backup health ok. Windows-generated launcher path defect caught before dispatch
and corrected through native Linux generation; core source pins unchanged.
Approval/state provisioning and real paired-host relay/provider/maintenance gates
remain open. No application activation, credential consumption or database change.

October1: source-only installation proposal (local-only evidence: `artifacts/SUCCESSOR_SOURCE_INSTALLATION_PROPOSAL_20261001.md`)
adds the authenticated additive installer/driver (14native tests) and proves the
complete synthetic reserve consume/receipt/enter/attest sequence on PostgreSQL
(two tests). Read-only WEB preflight verifies library/runtime/.78 and no path
conflicts. Exact delivery packet prepared; live source installation awaits scoped
approval. Approval provisioning, paired-host relay and timed maintenance stay open.

October1: attended reserve entry preparation (local-only evidence: `artifacts/ATTENDED_RESERVE_PREPARATION_20261001.md`)
connects the tested held adapter through separate consume/receipt/enter/attest
commands with exact per-action approvals and relay scopes. 206 native tests pass,
including actual PTY hidden-input checks; baseline375/two expected skips passes.
Source-only R3 package is retained. Additive installer, complete new-entry database
integration and actual hosted relay/maintenance acceptance remain open.

October1: relay and held-reserve preparation (local-only evidence: `artifacts/RELAY_HELD_PREPARATION_20261001.md`)
passes45 native and three real PostgreSQL tests; baseline375/two expected skips.
Dedicated authenticated relay source and a separate held-state timestamp/role
adapter are packaged locally. The attended caller still needs explicit wiring;
installation, exact approval provisioning and actual hosted relay/outage acceptance
remain open. Historical helpers, reserve enrollment and packages remain intact.

October1: successor execution preparation (local-only evidence: `artifacts/SUCCESSOR_EXECUTION_PREPARATION_20261001.md`)
passes76 native/three PostgreSQL tests for separate approval, protected startup,
relay protocol and publication recovery without reapplying committed SQL.
New verified guard fixes equivalent-timestamp spelling rejection. The historical
held-reserve path needs the analogous separately reviewed correction before use;
authenticated operator transport, installation and live outage acceptance remain
open. Source-only package retained; no hosted change or commit.

October1: verified reserve successor acceptance (local-only evidence: `artifacts/VERIFIED_RESERVE_ACCEPTANCE_20261001.md`)
adds a local same-transaction pre-apply guard and separate configuration adapter.
15 native tests and five PostgreSQL tests pass; baseline375/two expected skips.
Authenticated successor-apply relay, exact source/approval closure, attended
publication and timed hosted acceptance remain open. Existing enrollment and
original correction package are preserved; no live change or commit occurred.

September30: user selected preparation of the full schema0021 rollout.
Current rollout preparation (local-only evidence: `artifacts/FULL_ROLLOUT_PREPARATION_20260930.md`)
records the durable barrier, schema21 installer/preparation safeguards, passing local migration tests and a separately qualified .79 fallback. Supplemental hosted verification (local-only evidence: `artifacts/TRANSFER_RECONCILIATION_RESULTS_20260930.md`) passed for main881tests and fallback832tests with a pinned test-only fixture correction; original failure preserved, prepared runtime unchanged. Owner recovery readiness passed. Attended WorkOS/provider/identity inputs and migration/activation remain open. Commits remain unpushed.
PR82 checks pass for86e4f5b; subsequent preparation edits are not covered by that CI.
Hosted staging remains .78/schema0014. Matched backup refresh (local-only evidence: `artifacts/BACKUP_REFRESH_20260930.md`)
completed with strict health passing on both hosts at18:46Z. No alert clearing,
threshold change, identity activation or application activation occurred.

Isolated selection durability rehearsal (local-only evidence: `artifacts/SELECTION_DURABILITY_REHEARSAL_20260929.md`)
adds committed claims before synthetic host CAS, fresh-connection readback,
interruption/concurrency checks and an always-disabled consumer boundary.
This is local SQLite evidence; protected live adapters and actual host acceptance
remain open. Existing selection/configuration/schema safeguards are unchanged.

Two-host selection journal model (local-only evidence: `artifacts/TWO_HOST_SELECTION_MODEL_20260929.md`)
adds immutable per-host publication claims, complete lineage checking and conservative
readback decisions. Convergence does not enable consumers or authorize execution.
Next: isolated durable adapter and consumer-barrier rehearsal before a live caller.

Initial-plan replacement contract and prototype (local-only evidence: `artifacts/INITIAL_PLAN_REPLACEMENT_CONTRACT_20260929.md`)
bind the exact previous selection/archive and target source without producing a
selectable packet. Partial attempts require recovery; absent receipts do not prove
no effects. Existing selector/transfer and schema fences remain unchanged. Next:
coordinated journal and consumer barrier tests for two-host publication, then an
independently reviewed protected caller. Local verification is recorded in
replacement receipt (local-only evidence: `artifacts/IDENTITY_REPLACEMENT_PROTOTYPE_20260929.md`).

Schema0020 release-operation contract (local-only evidence: `artifacts/SCHEMA20_RELEASE_OPERATION_CONTRACT_20260929.md`)
now records the exact packet, migration, configuration and rollback requirements.
A separate local source preflight binds selected source hashes and validates the
whole migration graph; it cannot authorize execution. Historical preparation and
activation fences remain unchanged. Audit identified the next prerequisite:
the installed-plan supersession protocol cannot change application/schema, so
design and test its explicit replacement path before enabling0020 compatibility.
See verification receipt (local-only evidence: `artifacts/FACTOR_RELEASE_PREFLIGHT_20260929.md`).

Runtime and schema readiness verified locally (local-only evidence: `artifacts/FACTOR_RUNTIME_READINESS_20260929.md`):
851 offline tests passed,146 explicit skips; restricted PostgreSQL0020 readiness
passed with five schema/grant rejection cases. Enrollment/removal construction is
wired behind default-off flags. Existing operational .74/0016 fences remain closed.
Next: reconcile and test the exact0020 release-operation packet, including rollback
compatibility, before deployment or the prepared hosted attended acceptance proposal.
General recovery and beta opening remain open; no live identity change.

Evidence and next work: independent gate receipt (local-only evidence: `artifacts/INDEPENDENT_BETA_GATES_20260928.md`).
These gates remain separate from source coverage and permission replies.

Continuation: reconciled identity candidate (local-only evidence: `artifacts/IDENTITY_RECONCILED_CANDIDATE_20260928.md`)
is prepared locally on .78 ancestry; 762 tests passed, 146 native/database skips,
syntax and temporary loopback checks passed. Independent static review found no
blocking integration defect. BETA-ID-01 local reconciliation is complete; native
acceptance and operational preparation reconciliation remain open. Nothing deployed.

Retained backup receipts recovered (local-only evidence: `artifacts/BACKUP_RETAINED_EVIDENCE_20260928.md`):
original DB rehearsal RTO86s, elapsed87s and backup-age RPO50,341s verified;
object rehearsal verified22 ciphertext objects. BETA-BKP-01 original measurement
gap is closed; full paired/decrypted recovery, snapshots and retention remain open.

Further continuation: isolated native identity acceptance (local-only evidence: `artifacts/IDENTITY_NATIVE_ACCEPTANCE_20260928.md`)
passed92 PostgreSQL tests plus the separately bound permission check and26 Linux
owner-recovery/installer tests. BETA-ADM-01 selected local native surfaces are now
verified; target-host operational/attended acceptance remains. The
factor/recovery implementation contract (local-only evidence: `artifacts/FACTOR_RECOVERY_IMPLEMENTATION_PLAN_20260928.md`)
identifies shared enrollment/removal/recovery coordination as the next implementation
prerequisite. No live identity setting changed.

Shared security-slot foundation implemented locally (local-only evidence: `artifacts/SECURITY_SLOTS_IMPLEMENTED_20260928.md`):
additive0017 preserves enrollment uncertainty and blocks competing operation kinds.
15 native tests passed; broad suite762 pass/146 skips, syntax and default-off smoke
passed. Shared ownership is prepared; BETA-ID-02 removal/recovery proof, transfer
and effect coordinators remain open. No migration deployed.

Factor-removal coordinator implemented locally (local-only evidence: `artifacts/FACTOR_REMOVAL_COORDINATOR_20260928.md`):
28 synthetic removal tests passed; broad suite790 pass/146 explicit skips, syntax
and default-off startup checks passed. Exact-factor proof, durable effect claims
and no-replay uncertainty handling are prepared. Next: restricted PostgreSQL
wrappers and authorized slot transfer, reviewed provider transport and routes,
then native/attended acceptance. General recovery remains open; nothing deployed.

Removal PostgreSQL foundation verified locally (local-only evidence: `artifacts/REMOVAL_POSTGRES_IMPLEMENTED_20260929.md`):
additive0018 and restricted store passed23 native tests; broader suite800passed/146
explicit skips. Existing enrollment ownership cannot transfer yet:0016 lacks a trusted
persisted predecessor-disposition receipt. Implement that evidence/transfer boundary
before provider/routes or enrolled-account acceptance. No identity feature enabled.

Future enrollment evidence and guarded transfer implemented (local-only evidence: `artifacts/ENROLLMENT_TRANSFER_IMPLEMENTED_20260929.md`):
additive0019 records acknowledged exact-challenge success plus ownership readback;
receipt consumption and removal ownership transfer are atomic.32 native tests and
815 offline tests passed (146 explicit skips). Historical/list-only completion remains
nontransferable. New coordinator/store remain unwired; next provider/routes and
schema/configuration integration, then hosted acceptance. General recovery stays open.

Removal transport/routes and atomic local sign-out prepared (local-only evidence: `artifacts/REMOVAL_HTTP_IMPLEMENTED_20260929.md`):
841 offline tests passed/146 explicit skips;5 native0020 tests passed. Completion-race
regression and intercepted browser no-retry flow passed. Handler remains disabled;
next reconcile runtime construction and operational/schema readiness through0020,
then bounded attended provider acceptance. Existing removal rows block0020 migration;
never erase history to satisfy it. No live identity change.

- [x] **BETA-ID-01 — Reconcile authentication candidate:** superseded by the reconciled application deployment through schema0023 and completed owner binding/v2 activation on October 6; see [release and launch evidence](docs/BETA_PATH_20261003.md). The earlier candidate inventory and migration0015/0016 instructions are historical, not remaining work. Optional factor/recovery acceptance remains in BETA-ID-02.
- [ ] **BETA-ID-02 — Factor removal and general recovery:** implement separate durable coordinators and synthetic replay, concurrency, binding and ambiguous-outcome tests after reconciliation. Existing owner bootstrap recovery does not satisfy general-user recovery. Keep activation pending native restricted-PostgreSQL and attended acceptance.
  October 5 introduced purpose-bound factor proof, hashes-only credential issuance/custody confirmation, a restricted runtime adapter and migration0022. October 6 implementation/native checks and deployed schema0023 supersede the old blanket implementation/deployment-pending statement. Reconcile exact existing hold/release and assisted-recovery coverage before new implementation; attended optional-factor/general-user recovery acceptance and activation remain open, distinct from the completed owner transition.
- [ ] **BETA-ADM-01 — Native admission/session acceptance:** offline .78 regression run passed 411 tests; 76 tests skipped for explicit PostgreSQL/Linux fixtures. Prepare exact isolated native fixture coverage for those omitted surfaces before asserting admission/recovery acceptance.
- [ ] **BETA-BKP-01 — Recover original evidence:** collect sanitized existing protected restore receipts read-only, including exact backup/object bindings, phase verdicts, original measured RPO/RTO, reconciliation and authenticated decryption. Resolve provider-snapshot applicability and the due retention review. Missing local evidence does not authorize a new restore or deletion.
- [ ] **BETA-BKP-02 — Backup notification lane:** prepare synthetic stale/missed/failed/recovery, deduplication and acknowledgment/escalation handling separately from admission/TOTP alerts. Present exact messages, recipient/channel and timing before delivery activation; require actual receipt and acknowledgment evidence.
  October 5 update: both dedicated backup checks are configured on the existing
  email channel and active hourly reporting is installed. Database alert and
  recovery-email receipt are operator-confirmed. The one-file mirror repair and
  downloaded verification passed (13,043 objects, zero divergence); the final
  database evaluation cleared every failure and its healthy report was accepted.
  Do not repeat activation, repair, inventory or email confirmation. Residual
  missed-monitor observation and policy acceptance stay distinct
  from completed delivery. Current evidence: docs/BETA_PATH_20261003.md.
  The ordinary database report at 23:05Z was subsequently verified healthy and
  provider-accepted without manually triggering it; scheduled database reporting
  is now observed too.
- [ ] **BETA-BKP-03 — Residual paired recovery:** after existing evidence review, scope only missing matched DB/object-generation recovery, independent key recovery, authenticated decryption and plaintext/reference reconciliation. Record full-service RPO/RTO, resource limits and retention/cleanup; DB-only receipts and ciphertext checks do not establish these outcomes. See retained recovery evidence (local-only evidence: `artifacts/BACKUP_RETAINED_EVIDENCE_20260928.md`). Additional drills remain explicitly deferred for separate execution authorization.

- [ ] **Beta-opening blocker SEARCH-001:** repair silent zero-posting searches and unreliable source fallback; show distinct source-failure, valid-empty, partial and successful completion outcomes. Track acceptance in [BETA_BLOCKERS.md](docs/BETA_BLOCKERS.md). Deliver in a separate application PR after operations PR #78.

### SEARCH-001 coverage remediation — September 25, 2026

Source-access follow-up: UD permission email draft (local-only evidence: `artifacts/UD_PERMISSION_EMAIL_DRAFT_20260925.md`)
and ZipRecruiter conditions/questions (local-only evidence: `artifacts/ZIPRECRUITER_CONDITIONS_20260925.md`)
are prepared; the user confirmed sending the ZipRecruiter inquiry September25.
Its reply is pending; UD sending has not been confirmed. MCP versus publisher-program
applicability remains unresolved, so source activation is still pending.

Research and priorities: source evaluation (local-only evidence: `artifacts/DISCOVERY_SOURCE_EVALUATION_20260925.md`). Both failed coverage windows remain unchanged. Checked implementation work is not live acceptance or permission to open beta.

September28: release .78 proposal (local-only evidence: `artifacts/RELEASE78_PROPOSAL_20260928.md`)
prepares client filtering plus aggregate coverage diagnostics. Final local suite:
473 tests run, two skipped, no failures; syntax, shell check and loopback smoke
passed. No deployment or new collection. COV-10 now has local pagination/exclusion/
duplicate/cap counters, with alert-driven persistence limits and incremental cursor
work still open. COV-12 has a historical reference baseline (local-only evidence: `artifacts/COVERAGE_REFERENCE_BASELINE_20260928.md`),
not a completed current benchmark; missing positive cells and duty-based role recall
remain explicit. ZipRecruiter and UD acquisition are still disabled.

Later September28: approved candidate is prepared on staging (local-only evidence: `artifacts/RELEASE78_PREPARED_20260928.md`)
at cafad5b48a579adc80695519f0f8b7d39f621cd5; 775 native tests, one skip,
389 tracked files verified. Activation awaits operator resolution of Edge's
hosted-staging browser block; .77 stays current. Existing deployment approval persists.

Superseding deployment update: .78 is activated (local-only evidence: `artifacts/RELEASE78_DEPLOYED_20260928.md`)
with passing server/backup/queue checks. Manual authenticated postactivation
API/dashboard confirmation remains pending; separate coverage gates stay open.

Final September28: operator confirmed .78 authenticated API and dashboard46; delayed
service/heartbeat check passed. COV-03 is deployed and scoped deployment verification
complete. COV-10 diagnostics are deployed but broader recall/cursor work remains;
coverage acceptance and the independent reference benchmark remain open.

- [ ] **COV-01 — University of Dayton (selected; in progress):** prepare a bounded PageUp discovery/detail adapter, requisition deduplication, pagination and real work-location/clearance/citizenship preservation. Resolve the linked source-reuse terms before enabling automated product collection.
- [ ] **COV-02 — ZipRecruiter (selected; in progress):** implement against the verified official MCP schema, preserving posting identity, limits, cancellation, attribution and partial-source failures. Verify full-detail availability and applicable reuse/retention/scoring access before enabling the product integration.
- [ ] **COV-03 — Client filtering (deployed in .78; broader coverage acceptance open):** use deterministic whole-token role matching with reviewed software-developer alternatives; inspect a bounded set of unknown-location candidates before rejection, require affirmative eligibility after details, and keep unknown work arrangement distinct from onsite. Align future acceptance probes prospectively; preserve past receipts. Local evidence: implementation receipt (local-only evidence: `artifacts/SOURCE_REMEDIATION_IMPLEMENTATION_20260925.md`), 467 tests passed with two skips. The September 28 deployment and authenticated confirmation above supersede the earlier deployment-pending label; SEARCH-003 extends this work.
- [ ] **COV-04 — Audience-driven employer registry:** expand direct employer inventory around Dayton and US technology hiring rather than arbitrary ATS tenants; evaluate Leidos and other local employers against actual work locations and access.
- [ ] **COV-05 — Local public sector:** evaluate JobAps tenant connectors starting with City of Dayton; count current requisitions, never classification specifications.
- [ ] **COV-06 — Federal inventory:** evaluate USAJOBS search API access and incremental coverage, retaining hiring-path, citizenship, clearance, agency identity and remote-versus-telework distinctions.
- [ ] **COV-07 — Aggregated search:** evaluate Adzuna US coverage, credentials, pricing, attribution, reuse and full-detail access; do not treat a snippet as validated details.
- [ ] **COV-08 — Partner sources:** separately evaluate Indeed publisher access and US/backend suitability, LinkedIn supported search access, Handshake early-career fit, and iHire technology inventory/export access. Employer publishing APIs do not establish outbound search access.
- [ ] **COV-09 — Managed web discovery (Serper enabled; comparative value open):** the October 7 activation receipt (local-only evidence: `artifacts/serper-activated-readback-20261007.json`) supersedes the earlier disabled-until-selected instruction for Serper only. Measure its useful yield through SEARCH-002 before comparing Brave/SerpApi under a separately approved cost/request budget. Existing-credit limits remain; no purchase, top-up or alternative-provider activation is authorized by this item.
- [ ] **COV-10 — Truncation and recall diagnostics:** report pages read, advertised totals where available, exclusion reasons and capped sources; evaluate bounded incremental indexing/cursors instead of repeatedly reading first pages or removing caps.
- [ ] **COV-11 — Posting identity and sample accounting:** prefer employer/requisition identity, preserve uncertain duplicates, and propose a bounded larger detail pool to measure valid yield without automatic replacement or reinterpretation of old windows.
- [ ] **COV-12 — Independent reference benchmark:** build a dated known-positive set across all six cells with wrong-role, wrong-location and closed-posting controls; compare incremental valid inventory, employer diversity, duplicate rate, recall, detail success and request cost before another acceptance series.
- [ ] **COV-13 — Geography and sparse-market decision:** explicitly decide Dayton city versus commuting radius including Beavercreek/Wright-Patterson; distinguish retrieval recall from minimum useful inventory for rare roles. Change scope/floors only prospectively with approval.
- [ ] **COV-14 — Manual-link fallback:** improve supported-source handoff/import where needed; an import-first beta is a separate product decision and does not pass automated-discovery acceptance.
- [ ] **COV-15 — Renewed acceptance:** after selected changes pass synthetic tests, independent review and exact deployed discovery/detail/save verification, propose fresh bounded independent coverage windows. Retain cancellation, identity/admission and backup gates separately.

### Existing reliability work

- [x] Retire automatic browser-based application filling and submission attempts, including headed-mode controls and automation-specific retry states; replace them with a dependable guided-manual flow that opens the verified posting, presents the prepared materials and application checklist, and records progress only when the user confirms it.
- [x] Make every manual-application artifact explicit and downloadable: persist the generated cover letter as a safely named `.txt` file alongside the resume PDF, regenerate it after edits, and expose clearly labeled Resume PDF and Cover Letter TXT downloads from both Search & Match and Application Logs.
- [x] Prevent cover-letter upload files from overwriting tailored resume PDFs.
- [x] Distinguish `form_filled`, unverified `submitted`, and destination-confirmed `applied` states.
- [x] Record lifecycle timestamps and submission evidence without treating tailoring as an application date.
- [x] Require human review for sensitive questions and unmatched dropdown values; never guess fallback answers.
- [x] Add regression coverage for lifecycle mapping, sensitive fields, upload paths, and safe Markdown rendering.
- [x] Enforce a two-page resume contract, with a compact rendering retry and a hard rejection instead of truncation.
- [x] Prevent experience entries and individual bullets from splitting across pages when the entry fits on one page.
- [x] Escape untrusted resume content and correctly handle headings, bullets, bold, italics, and separators.
- [x] Use role-neutral resume-writing instructions with concise summary and bullet limits.

## P1 - Core workflow and security

- [x] Mark a job as manually applied from either matched or tailored state, with an editable application date and method.
- [x] Fix lifecycle date-field accessibility and usability: keep all date segments legible while one segment is selected, preserve visible focus/selection contrast in the dark theme, and provide a date control supporting both calendar selection and validated direct text entry.
- [x] Preview and bulk archive or delete untouched jobs while preserving generated materials and application history.
- [x] Add reversible archive/restore recovery for bulk cleanup.
- [x] Preserve privacy-safe tombstones for manually or permanently deleted job postings so later **Search & Analyze** runs suppress the same canonical posting instead of restoring it; provide a way to review and clear suppressed-posting records when the user wants to rediscover a job.
- [x] Add undo/recovery for other lifecycle corrections.
- [x] Replace frontend `innerHTML` interpolation of job data with safe DOM construction and validated links.
- [x] Stop returning plaintext API keys from profile reads; mask keys and use dedicated secret-update operations.
- [x] Restrict CORS and define the supported local-only deployment threat model.
- [x] Enforce the local browser trust boundary: reject non-loopback Host headers, block cross-site unsafe requests using Origin/Referer and Fetch Metadata, and cap streamed resume uploads at 2 MB.
- [x] Add pipeline statuses with notes, follow-up dates, interviews, offers, rejections, withdrawals, and closed roles.
- [x] Add saved searches, duplicate detection, and on-demand expired-posting checks.
- [x] Add a consistent user-controlled **Stop** action for long-running progress-modal operations, including **Search & Analyze**, **Preview Details**, **Save Job** and its match analysis, **Tailor Materials**, resume parsing/import, listing verification, PDF regeneration, and comparable provider, network, AI, or document-generation work. Propagate cancellation through the frontend and backend; show a responsive **Stopping...** state; define safe commit boundaries; preserve previously saved data/materials plus safely completed partial results and diagnostics; leave a job saved but clearly unscored if post-save analysis is stopped; and report a clear canceled/partial outcome without treating user cancellation as a source failure. Do not offer misleading cancellation once a short atomic database change has committed.
- [ ] Add trustworthy progress reporting to the long-running **Search & Analyze** workflow. Track progress by authenticated user and operation rather than a process-global flag; show the current phase, elapsed time, and completed/total counters for keyword preparation, source queries, posting inspection, AI analysis, and saving; use an indeterminate state for opaque provider calls; and preserve the existing accessible **Stop** behavior. Persist only privacy-safe per-phase timing and workload metrics—never search terms, locations, URLs, job details, resumes, or credentials—and show an estimated completion-time range only after enough comparable successful runs exist, using robust recent-duration statistics and updating the estimate as the current run reveals its workload.
- [x] Add a manual job-import workflow that accepts a posting URL from any site, validates and canonicalizes the URL, detects duplicates, extracts available job details with a preview before saving, and allows the user to complete or correct fields when the source cannot be parsed.
- [x] After a manually added job is saved, refresh the job-postings list, reveal and scroll its results frame to the new row, and briefly focus or highlight that row so the user can confirm the import and continue working. If active filters, sorting, or pagination would hide the posting, reveal it intentionally and explain any temporary view change instead of failing silently.
- [x] Extend manual-import extraction for framed or script-only career portals such as branded iCIMS pages, and distinguish an embedded-content or anti-bot access challenge from a posting that simply lacks structured data while preserving manual field entry as the fallback.
- [x] Add optional scheduled alerts for saved searches.
- [x] Detect job-source format drift using provider health checks, expected content/selector contracts, and rejection-rate baselines; alert the user when a provider appears incompatible instead of silently returning no results, including a safe diagnostic summary and affected provider.
- [x] Make Lever source diagnostics resilient and specific: use the official read-only Lever Postings JSON API as the primary job-detail source; classify missing, removed, or expired postings as stale instead of format drift; reserve format-drift alerts for successful responses with invalid schemas; retain privacy-safe rejection-category counts; and use browser scraping only when the API is unavailable.
- [x] Restrict Playwright job-page redirects and subresources to public HTTP(S) destinations by resolving and blocking loopback, link-local, private-network, and non-web targets without breaking supported ATS providers.
- [x] Add a saved profile toggle to prefer a U.S. headquarters address when an employer has multiple headquarters, incorporate it into address resolution for unemployment reporting, and reject non-U.S. Places matches from the U.S.-preferred lookup before a verified global fallback.
- [x] Add sortable/filterable job results with status and a user-adjustable minimum match-score control.
- [x] Enrich job results with normalized location, work arrangement, compensation, date found, and source fields.
- [x] Add editable resume and cover-letter preview with PDF regeneration before applying.
- [x] Add deterministic section templates before PDF generation.
- [x] Add IT, technical-executive, general-professional, federal, healthcare, education, sales, trades/operations, academic-CV, and cover-letter modes.
- [ ] Prevent browsers and password managers from misclassifying profile fields and provider API keys as login credentials. Separate personal/profile inputs from secret-entry forms, apply accurate standardized `autocomplete` semantics plus narrowly scoped password-manager opt-out hints where needed, avoid login-like email-and-secret form structures, and verify the behavior in current Edge, Chrome, Firefox, and Safari without weakening secure secret entry or accessibility.
- [ ] Add operator-visible backup health monitoring before beta: alert on failed or missed PostgreSQL full/differential, WAL archive, R2, OneDrive-copy, and provider-snapshot jobs; detect stale destinations and unexpected inventory divergence without exposing object names or credentials; retain bounded success/failure metadata; and schedule recurring isolated restore, promotion, and checksum rehearsals with documented recovery-time and recovery-point results.

## P2 - Broader job-seeker value

- [x] Replace the inherited GCD site icon with product-specific branding, including appropriate favicon, pinned-tab, application-manifest, and mobile home-screen assets.
- [x] Rename the product to CareerTrellis so its market identity reflects a privacy-first, user-controlled search, tailoring, tracking, and preparation workspace rather than automatic application submission.
- [ ] Before public launch, complete professional trademark, corporate-name, app-store, domain, and social-handle clearance for CareerTrellis; secure the selected properties and then decide whether to rename the GitHub repository and legacy compatibility identifiers.
- [ ] Treat CareerTrellis as a working name and make public-facing branding replaceable before launch. Centralize the product name, title, tagline, support identity, domains, analytics labels, document metadata, user-agent string, manifest metadata, and brand assets instead of adding new hard-coded references. Provide and test a rename/migration path that can change repository, package, service, environment-variable, filesystem, database/export, and backup identifiers while preserving backward compatibility for existing installations and backups.
- [x] Show a privacy-safe "Maps API key saved" state and replacement guidance in Profile & Resume, matching the existing Gemini-key experience without revealing either stored key.
- [ ] Add contextual links beside every API-key entry or saved-key status control that open the provider's current official page for creating, viewing, rotating, or revoking credentials. Show the link for the selected AI or maps provider, distinguish credential management from general documentation or billing, centralize and periodically verify the allowlisted HTTPS destinations so provider URL changes do not leave stale links, open external pages without granting opener access, and never place a credential or other user data in a link, query string, analytics event, or referrer.
- [x] Add an AI-provider abstraction with securely stored bring-your-own keys, model selection, capability validation, and clear provider-specific errors; support providers beyond Gemini without exposing secrets to the browser.
- [x] Add a geocoding/maps-provider abstraction with provider selection, securely stored credentials, validation, rate-limit handling, and support for alternatives to Google Maps.
- [x] Polish the cleanup-preview modal action bar: prevent Restore counts and labels from wrapping awkwardly, use consistent button sizing and icon alignment, establish a clearer primary/secondary/destructive hierarchy, and stack actions cleanly at narrow widths.
- [x] Support multiple named base resumes with safe selection, duplication and deletion; automatic snapshots for meaningful saves; previewable, non-destructive version restore; migration of existing resume content; and source-resume/version attribution on newly tailored materials.
- [x] Import DOCX/PDF resumes with an explicit, provider-backed OCR fallback for scanned pages and export an accessible DOCX alternative with semantic headings, real lists, editable text, and clickable contact links.
- [x] Add deterministic, local job-result filters for compensation and contract rate, employment type, shift/on-call schedule, travel, commute/work arrangement, sponsorship, clearance, professional licenses, and physical/outdoor work conditions; include unspecified details by default and show the detected requirements used by the filters.
- [x] Add per-base-resume, role-specific professional evidence sections for skills, projects, portfolio links, licenses, certifications, and work samples; version and restore them with the resume, provide mode-aware entry guidance, and supply only user-entered evidence to the applicable tailoring sections without inventing missing details.
- [x] Add local application-funnel and response-rate analytics by source, role, location, resume version, and application method, with clear response definitions, outcome counts, timing, and small-sample guidance.
- [x] Add an interview-preparation workspace with role-specific research prompts, likely questions, candidate evidence/STAR-story planning, questions for the hiring team, editable notes, and printable or downloadable output.
- [x] Add a versioned, human-readable local data export covering profile settings, resume history, jobs, applications, saved searches, deletion choices, and privacy-safe diagnostics while excluding stored API keys, host file paths, URL fingerprints, and provider caches.
- [x] Add password-encrypted full backup and safe restore with compatibility checks, explicit conflict handling, and recovery from interrupted imports.
- [x] Add multilingual assistance for profile guidance, search workflows, generated-material review, and user-facing validation without silently translating factual resume content.
- [x] Complete a mobile-responsiveness and accessibility QA pass across keyboard, screen-reader, contrast, focus, motion, zoom, reflow, modal, table, and document workflows.
- [x] Design an opt-in, privacy-preserving provider-failure reporting mechanism for broader releases so repeated format-drift alerts can notify maintainers without transmitting resumes, API keys, job-application data, or unnecessary browsing details.
- [x] Persist a privacy-safe local history of the important diagnostic elements that triggered each **Some job sources may need attention** notice—including timestamp, provider, diagnostic code, and bounded aggregate counters—and provide retrieval or export for troubleshooting without storing job URLs, descriptions, searches, resumes, or credentials.
- [x] Make the **Some job sources may need attention** and **Search notes** box dismissible for the current view while keeping the underlying diagnostic history available for later retrieval.
- [x] Bound remote search response bytes, per-provider candidate counts, and retained job-description sizes; add explicit network timeouts and clear partial-result diagnostics when a source exceeds a budget.
- [x] Add an optional privacy-safe Sentry integration and operator setup guide. Keep local variables, request bodies, profile/resume content, job details, URLs, API keys, and generated materials out of events; document read-only Codex inspection credentials separately from the application DSN.
- [x] Replace broad mutable dependency ranges with a reproducible, reviewed lock or constraints file and an intentional dependency-update workflow.
- [x] Add recruiter, hiring-manager, referral, assessment, and networking tracking.
- [x] Add PDF metadata, accessible document semantics, clickable contact links, and recruiter-friendly filenames.
- [ ] Add a per-user confirmation preference for routine action-result and confirmation modals, including profile saves and recoverable job deletion/archive actions. Keep confirmations enabled by default; let users choose which categories to suppress; replace suppressed dialogs with accessible inline or toast feedback and an Undo path where available; and never allow the preference to bypass authentication, consent, secret replacement, permanent account/data deletion, restore-overwrite, or other irreversible/high-risk confirmations.

- [ ] Investigate perceived word spacing in the search status "Searching job sources." The copied text contains spaces; inspect font choice, kerning and rendering before assuming a missing space. Low-priority visual polish, explicitly deferred from the next work set (September 23, 2026).

## P3 - Integrations, growth, and monetization enablement

- [ ] Support secure multi-user operation before public web hosting: authentication and account recovery, strict per-user data and file isolation, authorization checks, server-side encrypted secret storage, privacy controls, export/deletion, abuse protection, and deployment-safe database migrations.
- [ ] Add email and calendar integrations for interviews and follow-ups.
- [ ] Add a browser extension or bookmarklet for saving jobs and assisting applications.
- [ ] Add team administration and institutional reporting (P3); individual billing and usage metering are promoted to MON-003/004/006 in the current work order above.
- [ ] Deliver privacy-first Free and Pro plans through MON-001 through MON-007 (P1); evaluate BYOK Pro packaging there. Short-duration Pro passes and optional AI credit packs remain P3 pending MON-008 evidence.
- [ ] Evaluate coaching/resume-review marketplace, workforce-development licensing, and white-label institutional editions.
- [ ] Add clearly disclosed ethical affiliate programs without allowing payments to affect job ranking.

## Product principles

- A clicked submit button is not proof that an application was accepted.
- Prefer a transparent guided-manual application workflow over brittle browser automation that job sites actively resist.
- Automation must not invent credentials, experience, sensitive answers, or consent.
- Preserve user history by default; archive before permanent deletion.
- Optimize for application quality and trustworthy outcomes rather than raw submission volume.
- Do not sell candidate data or accept paid employer ranking.

- [ ] Design and implement an optional search scope for other fields or all fields, through a toggle, advanced form, or another interface to be designed. Preserve job-title matching as the default. Define supported fields, matching rules, provider limitations and cost/request bounds; cover searches such as "1099 contract" without assuming every contract is US 1099 work. This supersedes the earlier proposal to change default title matching.
- [ ] Separate legacy Yahoo/Bing failures from direct-feed status, retain catalog limit/per-board error diagnostics, and deduplicate history notices. See artifacts/SEARCH_1099_DIAGNOSIS_20260923.md (confirmed live zero-candidate report).

- [ ] Preserve validated employer-feed location and employment metadata through posting inspection/import, or reliably extract it from structured posting data. Confirmed Bosch Enterprise Architect posting advertises Stuttgart, Germany and is not remote, but the saved job shows Location not listed. Keep remote filtering strict; do not label unknown locations remote. See artifacts/LOCAL_SEARCH_COVERAGE_20260923.md.

## Hosted discovery cache

- [x] Share public feed snapshots across users and between the hosted search worker and web application on one host, independently of private user data. Deployed and verified September 23 in build `20260923.75` (`45d91c0`): worker refreshes the shared cache; web reads without network requests or writable locks and cannot modify snapshots. Successful snapshots serve any user's query for six hours; failed refreshes retain a one-hour cooldown; process locking prevents duplicate refreshes. User queries, resumes and scores remain outside the shared cache. Hosted verification returned 83 We Work Remotely and 200 Jobicy listings. See `artifacts/RELEASE75_DEPLOYED_20260923.md`.
- [ ] Before adding another application host, implement centralized public-feed refresh coordination and shared snapshot access across hosts. Preserve provider-wide polling limits regardless of user count or client IP, atomic publication, freshness/cooldown state across restarts and deployments, worker-write/web-read permissions, and private-data isolation. Test concurrent refresh attempts from separate hosts, failure recovery and rollback without resetting cooldowns. Current filesystem locks coordinate only processes on one host; this future scaling work does not reopen the completed single-host sharing gate or establish broader beta search acceptance.

## PR82 disabled-account search cleanup

- [ ] Publish and live-verify the October1 operations schema-recheck fix. Local candidate restores the verified migration session role before the second setup; PostgreSQL42501 reproduced and fixed without expanding grants. Five real PostgreSQL acceptance tests and14 native save/handoff tests pass; full evidence in artifacts/HELD_EVIDENCE_SEQUENCE_20261001.md. Installed OPS/sealedR2 remain unchanged; update operations/package/plan provenance before attended execution.

- [x] Resolve disabled-owner search cancellation locally without relaxing worker RLS. Forward0021 adds bounded terminal cleanup, exact owner/run/live-lease checks, queue preservation and consistent worker locking. Existing cancellation assertions unchanged;190 native PostgreSQL18 tests passed with21POSIXskips, including14 new boundary/concurrency regressions. See artifacts/PR82_POSTGRES_FIXED_20260929.md. Local implementation only; commit/publication, schema0021 operational preparation, deployment and live verification remain open. PR82's existing failed checks have not been rerun.



October 2 original transition: separate evidence-action approval, state-preserving
installer and evidence launcher publisher passed71 local tests and were frozen
in the local delivery package. Hosted installation, exact approval provisioning,
durable outage control and live timed/provider acceptance remain open. Reserve
remains fallback. Evidence: artifacts/HELD_EVIDENCE_PUBLICATION_20261002.md.

October2: evidence-tool source installation and separate hosted readback succeeded; original transition remains pending. See artifacts/HELD_EVIDENCE_INSTALLATION_20261002.md.

- [ ] **BETA-ORIGINAL-TIMING-20261002:** Move full runtime authentication ahead of
  freshness issuance in original approval and held-evidence flows. One measured
  staging runtime check took24.794s; repeated checks cannot fit the existing30s
  approval round and threaten the300s proof window. Preserve both limits. Require
  authenticated prepared workers, process-bound mutation detection, fresh source
  and state checks, bounded cleanup and no replay on uncertain writes. Local guard
  groundwork is not live integration or end-to-end timing acceptance. A synthetic
  mmap test demonstrates that notifications alone miss writes: independently
  establish an immutable execution scope or retain full authentication. See
  artifacts/PREPARED_RUNTIME_READINESS_20261002.md. Owner:
  CareerTrellis maintenance; review before original-transition execution.

- [x] **BETA-ORIGINAL-TIMING local immutable-copy primitive:** Fresh private tmpfs
  copy, whole-filesystem read-only seal, exact source-path binding and cleanup,
  plus dedicated single-threaded worker mounting-capability reduction passed23
  real isolated Linux tests.375 baseline tests passed(two expected skips), syntax
  passed. Independent review completed for this bounded prototype. Actual runtime
  authentication, authenticated worker initialization, remaining adapters and live
  timing remain open under BETA-ORIGINAL-TIMING-20261002. Evidence:
  artifacts/FROZEN_RUNTIME_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING local prepared worker and approval adapter:** Sealed
  source handoff, final-process runtime guard, preparation-before-READY, shared
  proof windows, persistent authenticated library context and fresh per-request
  probes/engines passed40 local tests. Independent review matched source pins;
  baseline375passed(two expected skips). Actual historical dependency imports,
  operational bootstrap/client convergence and hosted timing remain open. Evidence:
  artifacts/PREPARED_WORKER_READINESS_20261002.md.

October2: original apply/reconcile sources/correction installed and separately verified;71local tests and375baseline tests passed(two expected skips). Evidence grant provisioner prepared locally, original-review bundle restored. No identity transition/approval/recovery-code use. See artifacts/ORIGINAL_SOURCE_INSTALLATION_20261002.md.

- [x] October2 storage/GitHub catch-up: inventoried local worktrees, Docker and both
  staging hosts; removed only three verified redundant release source transports
  totaling25,672logical bytes, retaining exact backups and durable receipts. Draft
  PRs80–82 remain intentional; latest PR82 checks pass. No net physical disk change.
  Evidence: artifacts/STORAGE_GITHUB_CATCHUP_20261002.md.
- [x] Prepared-worker candidate source identity: independently verified canonical
  manifest,26local file pins and139historical Git blobs. Operational bundle/bootstrap
  and hosted timing gates remain open; no activation or execution authority added.
  Evidence: artifacts/BUNDLE_MANIFEST_READINESS_20261002.md.
- [x] **BETA-WAL-ALERT-20261002:** Reviewed the sticky archive-failure incident;
  supported acknowledgment completed once after explicit approval and installed
  source-hash verification. Fresh database backup status16:01:27UTC passes with no
  failure codes. Incident evidence retained; this is not a new restore test.
  Evidence: artifacts/wal-acknowledgment-20261002.json.

- [x] **BETA-ORIGINAL-TIMING public source package:** Deterministic 20-file
  candidate plus unchanged 139-file library map; independent digest required by
  the nonexecuting verifier. Eleven local rejection/roundtrip tests passed and
  independent review found no blocking issue. Runtime bytes, installed bootstrap,
  actual imports and hosted timing remain open. Evidence:
  artifacts/PREPARED_SOURCE_BUNDLE_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING bootstrap input derivation:** Independently pinned
  package bytes now produce exact supervisor/adapter configuration and channel
  identity without executing source. Seven local contract tests and independent
  review passed. Snapshot-path runtime verification, actual runtime sizing,
  installed bootstrap/imports and hosted timing remain open. Evidence:
  artifacts/PREPARED_BOOTSTRAP_INPUTS_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING snapshot verifier:** Exact historical guard algorithms
  now verify a temporary copy without rewriting its installed-path bytes. Fourteen
  native synthetic-tree tests passed and independent review found no blocking
  defect. Sealed-copy integration, actual runtime sizing/imports and hosted gates
  remain open. Evidence: artifacts/PREPARED_SNAPSHOT_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING sealed snapshot integration:** Four native private
  namespace cases passed for real copy/bind verification, bad-pin/provenance
  cleanup and preexisting-writer isolation. Independent review passed. Actual
  runtime sizing/imports and installed/hosted gates remain open. Evidence:
  artifacts/PREPARED_SEALED_SNAPSHOT_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING actual runtime capacity inventory:** Explicitly
  approved read-only staging measurement found 382MiB page-rounded file data and
  13,348 nodes, exceeding both prototype defaults. Candidate limits documented,
  not applied; memory peak/service reserve and imports remain open. Evidence:
  artifacts/PREPARED_RUNTIME_CAPACITY_RESULT_20261002.md.

- [x] **BETA-ORIGINAL-TIMING staging-sized local copy:** Exact measured byte/page/
  node geometry passed native sealed-copy/bind verification at proposed512MiB/
  20,000-node limits; cleanup and independent review passed. Synthetic timing/RSS
  do not establish hosted memory/import safety. Evidence:
  artifacts/PREPARED_SCALED_SNAPSHOT_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING cgroup memory inspection:** Read-only staging cgroup
  ancestry/service usage verified; separate SSH worker would have no finite cap.
  Dedicated pre-allocation budget, full-import peak and failure cleanup remain
  open. No unit/limits changed. Evidence:
  artifacts/PREPARED_MEMORY_BUDGET_REVIEW_20261002.md.

- [x] **BETA-ORIGINAL-TIMING memory-budget recommendation and failure validation:**
  1GiB hard/896MiB high/zero-swap whole-job budget passed staging-sized local copy
  plus mounted OOM/deadline cleanup tests.512MiB additional host margin requires
  1.5GiB available; refreshed staging1,397.64MiB fails that allocation gate. No
  hosted limits changed. Evidence: artifacts/PREPARED_MEMORY_RECOMMENDATION_20261002.md.
  Subsequent user decision: reserve reduced to256MiB until beta; current floor is
  1.25GiB, with the1GiB cap/896MiB high/zero-swap controls unchanged. Above512MiB
  observation is historical, not the current gate.

- [x] **BETA-ORIGINAL-TIMING safe reduction assessment:** Process/cgroup/timer/cache
  inspection found no verified disposable job sufficient to close the140.95MiB
  gap. Recommend4GiB CPU/RAM-only staging resize, subject to account quote,
  recovery checks and explicit cost/downtime approval. No services stopped.
  Evidence: artifacts/STAGING_MEMORY_REDUCTION_ASSESSMENT_20261002.md.

- [ ] **POST-BETA-STAGING-RAM-4G-20261002 — Upgrade staging RAM and restore reserve:**
  Schedule after beta is reached; this is not a beta-opening blocker. Increase the
  staging WEB host plan from2GiB to4GiB RAM using a CPU/RAM-only resize, retaining
  the existing disk. Confirm account-compatible plan and billing change, verify
  backup/recovery readiness, and obtain approval for the paid change and maintenance
  window. Required downtime: drain/pause work, gracefully shut down staging, power
  it off for the resize, then boot and verify services, unchanged release/data,
  backup health and memory capacity. Restore the preparation reserve to512MiB and
  the host MemAvailable floor to1.5GiB; retain the1GiB job cap unless separately
  reviewed. Completion requires updated current policy, fresh capacity evidence
  and successful post-resize health checks. Owner: CareerTrellis maintenance.
  Review trigger: beta milestone reached. Current256MiB reserve/1.25GiB floor is
  explicitly approved for the path to beta; this backlog entry is not authorization
  to resize or power down now. Evidence: artifacts/PREPARED_MEMORY_RECOMMENDATION_20261002.md.

- [x] **BETA-ORIGINAL-TIMING real PostgreSQL confined transport:** Synthetic
  PostgreSQL18.4 traffic passed both confined hops with real SQLAlchemy/psycopg,
  three fresh connections each for TLS disable/prefer and exact TLS-required
  refusal. Before/after runtime/library hashes and aggregate1GiB/896MiB/zero-swap
  envelope passed; measured peak624.8MiB. Full observation/controller and hosted
  gates remain open. Evidence: artifacts/PREPARED_REAL_PG_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING post-connection fault cleanup:** Combined local
  PostgreSQL/namespace/idle-owner parent-death and accelerated deadline tests pass;
  automatic empty/absent cgroups verified before fallback cleanup. OOM remains
  unproven: both pressure probes timed out under the unchanged memory-high limit.
  Active-relay interruption and application/controller acceptance remain open.
  Evidence: artifacts/PREPARED_PG_FAULT_READINESS_20261002.md. Next fixture plan:
  artifacts/PREPARED_APPLICATION_OBSERVATION_PLAN_20261002.md.

- [x] **BETA-ORIGINAL-TIMING schema21 fixture preparation:** Historical source
  export and six copied dependency distributions verified, native imports pass.
  Schema21 fixture reviewed but execution blocked before process creation by the
  Compose-only disposable PostgreSQL policy. Explicit native-fixture exception
  required; no schema/application acceptance claimed. Exact action and resources:
  artifacts/PREPARED_SCHEMA21_APPROVAL_20261002.md.

- [x] **BETA-ORIGINAL-TIMING historical schema21 transport acceptance:** User-approved
  native-fixture policy exception applied and accepted by auto-review. Real
  historical migrations/grants and readonly schema/fence checks across both
  confined hops passed; peak697.9MiB under unchanged1GiB/896MiB/zero-swap limits.
  Owned cleanup/integrity verified. Migration-role ApprovalProbe/application
  acceptance remains open. Evidence: artifacts/PREPARED_SCHEMA21_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING repeated identity setup reproduction:** Expanded
  local fixture approved and passed. Real PostgreSQL confirmed second-setup
  SQLSTATE42501 and the explicit role-reset remedy; cleanup verified,698.3MiB
  peak. Separate uninstalled candidate probe passes15 tests. Real migration
  login and full WEB/DB/WEB acceptance remain open. Evidence:
  artifacts/PREPARED_IDENTITY_SETUP_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING direct synthetic observation:** Approved native
  fixture passed real migration login, historical guards, synthetic baseline/B
  receipt, eight fresh WEB/DB/WEB connections and tracked table/role comparison.
  Peak712.0MiB; cleanup/integrity verified. Host facts remain synthetic; capture,
  authenticated delivery and hosted acceptance remain open. Evidence:
  artifacts/PREPARED_APPLICATION_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING capture composition:** Approved native run passed
  actual capture/client/gate SQL with sequential1+8 owners, nine distinct backend
  PIDs, false authorization/publication and verified cleanup. Peak713.0MiB.
  Evidence: artifacts/PREPARED_CAPTURE_READINESS_20261002.md.

- [x] **BETA-ORIGINAL-TIMING real refusal batch:** Nine fresh invalid-state
  fixtures passed natively with actual scalar-level refusal, one connection each,
  unchanged tracked state and verified cleanup. All 45 archived receipt hashes
  independently audited. Evidence: artifacts/PREPARED_NEGATIVE_READINESS_20261003.md.

- [ ] **BETA-ORIGINAL-TIMING application worker/controller integration:** Local
  capability removal and partial process/namespace denial layer pass native checks.
  Full pinned runtime, filesystem/lock/network policy and authenticated observation
  controller with CLOSED acknowledgment remain open. Evidence and implementation
  sequence: artifacts/APPLICATION_WORKER_BOUNDARY_20261003.md.
  Readonly lock candidate now passes seven native tests and private readonly-bind
  original-inode contention/cleanup. Evidence: artifacts/APPLICATION_LOCK_READINESS_20261003.md.
  Fresh systemctl observations require a bounded trusted host-fact boundary outside
  the process-denied worker: artifacts/APPLICATION_HOST_FACT_BOUNDARY_20261003.md.
  Fixed host-fact reader and exact legacy command seam now pass ten synthetic native
  tests; authenticated transport/worker wiring and cgroup cleanup remain open.
  Session protocol now passes nine state tests and native direct-child Unix peer
  verification/cleanup. Per-dispatch 63-read ceiling is enforced. Source bootstrap,
  phase/aggregate accounting and worker wiring remain open. Evidence:
  artifacts/APPLICATION_HOST_CHANNEL_READINESS_20261003.md.
  Sealed client-source delivery and capture/observe/observe owner now pass six
  methods, including three real peer children totaling 189 synthetic reads and
  endpoint revocation on owner refusal. Frozen 29,962-byte package retained.
  Full application/runtime/confinement wiring remains open. Evidence:
  artifacts/APPLICATION_HOST_OWNER_READINESS_20261003.md.
  Eight-module sealed worker composition now passes with zero capabilities,
  process restrictions, readonly locks and 189 synthetic channel reads. Five
  private-binding tests pass; real historical inventory/capture, full filesystem/
  network/runtime confinement and controller cleanup remain open. Evidence:
  artifacts/APPLICATION_WORKER_COMPOSITION_READINESS_20261003.md.
  Historical capture/inventory and two WEB probes now pass with 189 synthetic
  service reads and five nonnested lock scopes. Cleanup uncertainty survives
  generic adapter errors. Real adapter/runtime and whole-job integration remain
  open. Evidence: artifacts/APPLICATION_HISTORICAL_BINDINGS_READINESS_20261003.md.
  Actual adapter constructor/handle through the copy helper now passes twelve fixture
  tests, including source/context/freshness refusal and disposal uncertainty through
  RequestGate. Authenticated application runtime and complete confinement/controller
  integration remain open: artifacts/APPLICATION_REAL_ADAPTER_READINESS_20261003.md.

  October 3 continuation: native nine-module sealed worker composition passes
  read-only filesystem enforcement, original fixture lock contention and 189
  synthetic host-fact reads. Three Landlock methods cover fresh directory lookup
  and fail-closed exact-leaf replacement. Full application read-set/runtime,
  network and controller integration remain open:
  artifacts/APPLICATION_FILESYSTEM_READINESS_20261003.md.
  Offline outage journal now passes ten synthetic tests, including interrupted
  intent/acknowledgment, fsync and lock-close uncertainty. All reopened journals
  refuse automatic advancement; operational adapters/reconciliation remain open:
  artifacts/ORIGINAL_OUTAGE_STATE_READINESS_20261003.md.
  Consolidated mandatory beta work and actual decisions: docs/BETA_PATH_20261003.md.

  Coverage continuation: narrow data-specialized software-title acquisition plus
  affirmative duty validation is implemented locally in beta-search-outcomes.
  483 tests pass (two platform skips), syntax and temporary loopback smoke pass;
  independent review counterexamples are retained. No provider collection or
  coverage acceptance claimed: artifacts/COVERAGE_RECALL_READINESS_20261003.md.

  Prepared adapter continuation: nine window-mapping checks, nine wrapper checks
  and two actual-adapter integration fixtures pass, including acknowledged full
  read count, shared original deadline and poisoned dispatch after close failure.
  Authenticated launcher/concurrent host-fact/controller integration remains open:
  artifacts/APPLICATION_PREPARED_ADAPTER_READINESS_20261003.md. The exact approved
  October 3 paired-plan read also passed without remote mutation or provider calls.
  Follow-on integration now passes six sealed source-delivery checks, ten
  concurrent owner checks and three actual-adapter fixture checks, including the
  real socket protocol. A delayed owner-abort race was fixed and independently
  reviewed. Next boundary is authenticated control handoff to a real launched
  worker with runtime preparation and independent termination/reaping; the current
  fixtures do not establish that boundary or hosted identity acceptance.
  Further October 3 continuation: actual launched-control fixture passes in a
  private mount/PID namespace, with real peer credentials, capability/process/
  filesystem restrictions and verified local cleanup. The synthetic adapter is
  explicitly excluded from real application acceptance. Source/bootstrap and
  launcher checks pass; actual confined application READY remains open. Eleven
  READY-only composition checks pass, with explicit OS inventory/manifest required
  before candidate generation. The exact approved public OS SSH inventory now
  passes; explicit manifest and candidate are built. Four isolated delivery/helper
  checks pass. Next: one hosted READY-only rehearsal and read-only postcheck,
  separately scoped in artifacts/APPLICATION_READY_REHEARSAL_SCOPE_20261003.md.

  The approved attempt then refused during worker preparation with verified
  cleanup and all three services active in its postcheck. The 119-byte Unix
  socket path exceeds Linux's limit; a one-line revision yields 90 bytes. Seven
  local socket/package checks pass. Corrected hosted attempt awaits specific
  approval: artifacts/APPLICATION_READY_REHEARSAL_RESULT_20261003.md.

  V2 was then approved and also refused cleanly in worker_prepare; its postcheck
  reports all services active. Fixed-stage diagnostics now pass nineteen local
  checks and independent review without changing auth/confinement/resource
  limits. Exact next hosted scope awaits specific authorization:
  artifacts/APPLICATION_READY_DIAGNOSTIC_SCOPE_20261003.md.

  Approved diagnostics then confirmed confined authenticated runtime verification
  passes, with refusal at the remaining post-runtime deadline/topology guard.
  Cleanup and postcheck passed. Refined guard diagnostics preserve all assertions,
  pass23 local checks and independent review, and await exact hosted approval:
  artifacts/APPLICATION_READY_GUARD_SCOPE_20261003.md. READY remains open.

  Approved guard diagnostics reached stage68 (mountinfo read/parse), with cleanup
  and service postcheck passing. A retained read-only metadata FD candidate keeps
  fresh seek/read, original parser and all assertions;31 local checks (including
  real pathname denial and later mount-drift rejection) and review pass. Hosted
  verification awaits approval: artifacts/APPLICATION_READY_MOUNTFD_SCOPE_20261003.md.
# CT-BACKUP-LABEL-GAP-20261007 — scheduled mirror lag alerts

Fix verified through the October 9 scheduled cycle; owner CareerTrellis backup maintenance. The following diagnosis and local preparation are historical. October 7 live evidence shows a
successful 03:19 UTC database backup causing a label mismatch at 04:01 before
the successful daily mirror completed at 04:44. Healthy reporting resumed at
05:01. Design bounded pending-mirror handling or sequencing while preserving
failed, overdue and stale mirror alerts; verify synthetic timing/failure cases
before deployment. Evidence: diagnosis (local-only evidence: `artifacts/backup-label-gap-20261007.md`).

Local follow-up: [bounded pending-mirror design](docs/MIRROR_PENDING_POLICY.md)
and an offline policy classifier now have nine passing synthetic timing/failure
tests. The proposed fixed deadline is 05:15 UTC, capped at three hours after
backup completion; pending never clears an active alert. This is not installed
monitor behavior. Schema/reporter integration, fresh installed-baseline review,
operational approval and full scheduled-cycle verification remain open.

October 8, 12:52 UTC: integration deployed and immediate live verification passed.
Evaluator `1229ed9bce42c66f6d4b613e56abdfeb00da347d` uses an additional fixed
last-clear-evaluation +3h cap to cover early-backup/hourly-jitter timing. Reporter
`fcfeb0bba27453db1d7300fb9abaf32497ff28e1` delivered fresh healthy results to both
checks. The separate heartbeat is hourly/20m; the explicitly approved existing
outcome interval is 4h/20m, email preserved. Final validation:108 native tests and
391 local P0/object/pending checks passed (two local skips); syntax passed.
October 9 normal scheduled-cycle acceptance passed: pending at04:02:47 UTC,
healthy at05:03:30 UTC, with both outcome and heartbeat deliveries accepted.
[Deployment evidence and resource handoff](docs/MIRROR_PENDING_DEPLOYED_20261008.md).

## October 9 separate monitoring incidents

- **CT-OBJECT-MIRROR-STALE-20261009 — stale condition cleared; recurrence follow-up open, P1.** Latest verified object mirror
  is September30; eight-day freshness limit exceeded October8. WEB evaluator
  returns alert exit1, but its unit treats that as execution failure and therefore
  skips the OnSuccess reporter. Prepare guarded mirror refresh/recurrence and
  correct alert delivery without suppressing failures. Owner backup maintenance.
  October9 approved window created full backup `20261009-133950F`, but object
  capture failed because the coordinator could not discover the installed rclone
  through its PATH. Services were restored and full health verified; no capture
  was accepted or published. Recovery-handshake defects also require correction.
  Another paired window remains unapproved. Window outcome and retained resources (local-only evidence: `artifacts/object-mirror-refresh-20261009/WINDOW_OUTCOME.md`).
  A subsequently approved V3 window succeeded with full backup `20261009-143304F`,
  paired24-object capture and verified OneDrive publication at14:42 UTC; services
  restored healthy. The scheduled15:01 UTC evaluator returned ok/zero failures,
  cleared the object alert, and its automatic reporter delivered a healthy update
  with provider acceptance. This clears the current stale-object condition;
  mirror-refresh recurrence remains open. The October 10 guarded completed-alert dispatcher fixed failure delivery; Healthchecks accepted the explicit stale-restore alert at 21:41 EDT. The subsequent prefix restore cleared that separate condition; see [current recovery handoff](docs/OBJECT_PREFIX_RESTORE_20261010.md). V3 live evidence (local-only evidence: `artifacts/object-mirror-refresh-20261009/v3/WINDOW_SUCCESS.md`).
- **CT-ADMISSION-CLEANUP-FAILED-20261009 — resolved, live verified.** The installed
  legacy gates rejected schema0023. Approved DB-only successor `98c58a1` now
  accepts that exact schema and preserves monitoring history through a durable
  handoff. Admission removed21 expired records; TOTP completed with0 ciphertext
  scrubs. Both fresh monitors report ok/no issues; at18:11:30 UTC the normal
  combined reporter delivered an empty classification with provider acceptance.
  All five successor schedules are active and predecessors disabled; prior
  failure history remains. No policy, retention or application change.
  Live evidence and retained resources (local-only evidence: `artifacts/admission-cleanup-fix-20261009/DEPLOYED.md`).
  Owner admission maintenance; review on scheduled failure or next schema change.

Read-only diagnosis and exact evidence (local-only evidence: `artifacts/HEALTHCHECKS_DIAGNOSIS_20261009.md`).
## October 10–11 backup correction and environment closeout

- **CT-DB-RESTORE-SCHEDULE-20261010 — fix verified.** Isolated PostgreSQL restore, promotion, checksums and scratch cleanup passed in 171 seconds; the persistent-timer catchup also passed in 121 seconds. A persistent Wednesday 08:00 UTC timer with up to 15 minutes jitter replaces the activation-relative schedule. Observed next run: October 14 at 04:02:56 EDT. Monitor, reporter and Healthchecks recovered; production database process unchanged. Review retained rollback evidence after the first weekly run and carry the timer source into this maintenance PR. Local evidence: `artifacts/restore-correction-20261010/closeout.json`.
- **CT-OBJECT-PREFIX-RESTORE-20261010 — live correction verified; first scheduled-run observation open.** Restored and download-verified 24 encrypted objects (707,762 bytes) under a unique rehearsal prefix in 29 seconds; six older objects remained unchanged. Healthchecks recovered. Weekly Wednesday 08:30 UTC plus up to five minutes jitter, with 24-hour successful-run deduplication; next observed run October 14 at 04:31:54 EDT. Stop for review at 12 attempts or 256 MiB reserved; no automatic deletion. Seventeen offline tests and live service/hash/inventory checks passed. Maintain fresh mirror captures and matching release directories, review after the first scheduled run and before the cap. Include the installed dispatcher, prefix helper and coordinator as maintained source in this PR. This does not establish paired database/object recovery, independent key recovery or authenticated plaintext reconciliation. [Recovery handoff](docs/OBJECT_PREFIX_RESTORE_20261010.md).
- **CT-LOCAL-VENV-20261010 — fix verified October 11.** Repaired the existing virtual environment from unavailable Microsoft Store Python 3.12.10 to working Python 3.12.14, preserving the exact dependency lock. All 287 P0/object tests, syntax and dependency checks passed; isolated local dashboard startup and clean shutdown passed. This supersedes the earlier restore-task test-environment blocker. Review on the next ordinary launch or base-interpreter update. No application source or remote service changed; the private/generated virtual environment is excluded from version control. Local evidence: `artifacts/python-environment-repair-20261011/receipt.json`.

SEARCH-003 full provider acceptance, recurring mirror capture, complete recovery policy and key/plaintext verification remain open. This closeout does not promote partial search acceptance or ciphertext-only rehearsal into broader completion. The user explicitly authorized commit, push and a draft PR without a new deployment on October 11; preserve the previous live evidence and identify later source-only packaging changes separately.

PR #85 CI follow-up (October 11): both Web foundation jobs caught the same omitted policy inventory entry for POST /api/jobs/search/plan. Classify the pure, authenticated preview explicitly and retain exact route coverage; add HTTP boundary coverage and run the four title/discovery contract modules in Linux CI. This test/CI correction does not change application behavior or require deployment. Local and hosted results remain separate; hosted acceptance requires the corrected commit's checks to pass.

Publication note: local-only evidence paths identify retained operator records; those private/generated artifacts are intentionally excluded from this repository. Maintained source and portable synthetic fixtures are included.
