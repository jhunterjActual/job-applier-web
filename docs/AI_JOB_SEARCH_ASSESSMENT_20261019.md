# CareerTrellis and ai job search assessment

Assessment date: October 19, 2026. Owner: Hunter / CareerTrellis product and engineering.

## Recommendation

Continue CareerTrellis as a separate browser product, with the next development
phase focused on proving useful search and application quality. If the objective
is only the owner's personal job search, trialing ai-job-search is likely a better
use of effort than maintaining a hosted platform. Existing investment alone is
not a reason to continue.

ai-job-search has a more developed application-writing workflow. CareerTrellis has
a more developed application platform. Replacing one with the other would change
the user experience and operating model rather than provide a drop-in upgrade.

## Evidence and limits

This is a source/documentation assessment, not a hands-on output benchmark,
security audit, fresh live deployment check, or proof of market demand. Upstream
master sources were read on the assessment date; no immutable upstream commit was
captured. The links below are mutable and must be pinned before reuse or testing.
Repository popularity and release activity are not outcome-quality measurements.

CareerTrellis evidence includes [features and architecture](../README.md),
[backlog](../PRODUCT_BACKLOG.md), [tailoring](../backend/tailor.py),
[interview preparation](../backend/interview_prep.py), and
[search-quality limitations](search-quality/README.md). The root checkout was at
aedc18869d7b3a72a1a588d932e96325aebcb3aa with substantial pre-existing edits; its
20261010.85 build metadata was older than the retained October 17 release
worktree at 7a4d3f077c4da21f2a315cbbcf4fd926d91151f4, build 20261017.96.
Neither observation establishes today's live version. Recent retained role-filter
records inform the assessment; historical handoffs can describe an earlier phase
than later deployment receipts. Do not treat an unchecked backlog item as proof
that no slice has shipped.

## Product comparison

| Dimension | ai-job-search | CareerTrellis implication |
|---|---|---|
| Interaction | Claude Code-oriented commands and local files | A browser UI serves a different audience and avoids requiring users to manage a coding-agent workspace |
| Application quality | Separate drafting, critique and revision; compiled-document checks | Extend existing structured tailoring and human review with bounded critique and final-output verification |
| Search | Danish portal integrations alongside LinkedIn and a multi-market aggregator | Better coverage for US roles is unproven; compare identical roles and locations before adding sources |
| Tracking | Local tracker and application archives; generated HTML report | Extend existing dashboard, outcomes, contacts and interview workspace rather than create another tracker |
| Operations | Personal workspace with user-managed tooling and privacy | Hosted authentication, tenant isolation, worker execution, private objects and recovery are separate responsibilities |
| Portability | Editable workflow/profile files and configurable document toolchains | Borrow small components and concepts; a whole-repository merge would not preserve hosted contracts |

The [upstream overview](https://github.com/MadsLorentzen/ai-job-search) documents
its setup, commands and portal scope. Its private-workspace guidance matters:
personalization in a public fork can expose candidate information. A personal
trial should use a private workspace and synthetic inputs first.

CareerTrellis already implements professional evidence, resume modes, outcome
insights, interview preparation, people/assessment tracking, and provider selection.
Its current search evaluation explicitly lacks complete real-world coverage,
ranking, latency and cost measurements. Those gaps outrank broad feature expansion.
The README also needs separate local/hosted descriptions rather than universal
local-storage claims; DOC-001 tracks that reconciliation.

## Selective adoption

| Priority and backlog owner | Adaptation | Required difference from upstream |
|---|---|---|
| P1 QUALITY-001 | Independent material critique with factual checks and specific edits | Optional bounded provider work, visible proposed changes, preserved originals, cancellation and measured cost; model agreement is not factual proof |
| P1 QUALITY-002 | PDF text and layout validation | Check final downloadable bytes; calibrate for current templates and profession-specific length rules; preserve skipped-check distinctions |
| P1 SEARCH-004 | Evidence-backed fit dimensions, reasons and explicit unknowns | No speculative culture score or generalized eligibility assumption as an automatic veto |
| P2 QUALITY-003 | Exact submission archives, stage-specific preparation and follow-up drafts | Extend current application records; no automatic messaging, outcome inference or mandatory email integration |
| P2 QUALITY-004 | User-controlled writing preferences and examples | Keep style separate from factual evidence; avoid inferred personality profiling |

The [application workflow](https://github.com/MadsLorentzen/ai-job-search/blob/master/.claude/commands/apply.md)
provides the drafting/review pattern. The
[PDF verifier](https://github.com/MadsLorentzen/ai-job-search/blob/master/tools/verify_pdf.py)
and [layout verifier](https://github.com/MadsLorentzen/ai-job-search/blob/master/tools/verify_layout.py)
are candidates for direct adaptation, subject to dependency review and tests.
The layout thresholds are tuned to upstream templates, not CareerTrellis output.
The [fit framework](https://github.com/MadsLorentzen/ai-job-search/blob/master/.claude/skills/job-application-assistant/04-job-evaluation.md)
and [outcome workflow](https://github.com/MadsLorentzen/ai-job-search/blob/master/.claude/commands/outcome.md)
provide design references, not automatically suitable product rules.

Do not copy the entire Claude command/permission environment, impose its document
toolchain on browser users, or replace hosted persistence with a file tracker.
Defer email synchronization, new portal campaigns, upskilling expansion and broad
provider/billing work pending the value review. Source input remains untrusted;
instruction-level prompt defenses do not replace application security boundaries.

The upstream [MIT license](https://github.com/MadsLorentzen/ai-job-search/blob/master/LICENSE)
permits reuse with its copyright and permission notice. Before importing code or
assets, pin the revision, record original paths and modifications, retain notices,
review individual dependency/font/template licenses, and run applicable tests.
No upstream code or assets were copied in this assessment.

## Comparison protocol for VALUE-001

1. Fix a common synthetic candidate profile and 10–15 representative postings,
   including good matches, adjacent roles, explicit mismatches and unknown facts.
   Reuse SEARCH-002 and AI-004 controls rather than rebuilding their evaluators.
2. Pin both repositories and record model/configuration, template versions and
   available capabilities. Keep the same inputs. Where provider/toolchain parity
   is impossible, label that difference rather than attributing it to the product.
3. Before runs, define factual-error severity, relevance rubric, document failures,
   acceptable human editing effort, total cost/latency bounds and decision rules.
   Record reviewer judgments and denominators; do not choose thresholds after
   seeing results. Factual inventions cannot be offset by a better style score.
4. Compare output accuracy, tailoring usefulness, final-document text/layout,
   editing time, latency and total cost including retries and failures. A text
   extraction check is not a guarantee of acceptance by every ATS.
5. Evaluate discovery separately in matched role/location/date windows. Build an
   independent reference and count unique relevant active jobs, duplicates, stale
   listings and known misses. Recall against that reference is not whole-market
   recall; explain unavailable sources and geography differences.
6. With separately scoped participant consent and applicable provider authority,
   observe a small target-user group completing setup, finding a useful job and
   downloading reviewed materials. Record assistance and abandonment with minimal
   personal data. Do not collect resumes or posting text in aggregate analytics.
7. Record a continue/revise/stop decision. Continue for useful outcomes and a
   materially easier browser experience at acceptable cost. Revise for a bounded
   remediable gap with a new review point. Prefer upstream/pause expansion if the
   benefits do not justify support and development. Small samples are directional,
   not evidence of improved hiring outcomes.

## Implementation sequence

[ROADMAP.md](../ROADMAP.md) gives the delivery sequence;
[PRODUCT_BACKLOG.md](../PRODUCT_BACKLOG.md) owns status and acceptance criteria.
Prepare the protocol first, improve core quality in bounded slices, review product
value, then decide which expansions are justified. Existing operational defects
and safeguards retain priority. No provider spend, deployment, public release,
external message, service retirement or user-data migration is authorized by this
planning record.
