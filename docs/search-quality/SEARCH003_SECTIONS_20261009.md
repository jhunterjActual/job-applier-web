# SEARCH-003 section-aware parser decision

October 9, 2026 (America/New_York). Owner: CareerTrellis engineering.
Authoritative handoff for this continuation. **No-go:** do not adopt this parser
in the active evaluator or runtime. Earlier matcher and title-only defaults remain
unchanged. Retain the candidate as experimental evidence.

## What was implemented

[Section-aware candidate](../../scripts/search_role_sections.py) adds explicit
current/past/customer section state, attributed quotations, local role negation,
withdrawal/correction handling and narrow assigned-duty bundles for the three
reference roles. It returns a boolean or a bounded reason code without echoing
source descriptions. It is used only by offline tests and explicit evaluator calls.
It is not imported by the application or the ordinary search replay.

The implementation fixes concrete earlier examples: a current opening after history,
benign degree/travel exclusions, attributed current duties, and corrections that
withdraw a draft role. Quote removal now retains a boundary instead of joining
attribution text to later duties. Normal development tests caught and verified
those changes. Those successes do not establish general semantic matching.

## Evidence and selection discipline

The independent author froze the [32-case section set](section-holdout-v1.json)
before candidate evaluation. It initially exposed four false positives and five
misses. After inspection and fixes, this became regression data. A separately
frozen [16-case final holdout](section-final-holdout-v1.json) was authored before
the reviewer inspected candidate code. Its judgments were not adjusted to output.

| Evaluation | Correct positive matches | False positives | Precision | Recall | Disposition |
| --- | --- | --- | --- | --- | --- |
| [Earlier 30 cases, final candidate](sections-context-final-20261009.json) | 14 / 15 | 0 | 100% | 93.3% | Regression pass; oversized positive abstains |
| [32 cases, first candidate](sections-section-comparison-20261009.json) | 11 / 16 | 4 | 73.3% | 68.8% | Failed before fixes |
| [32 cases, final candidate](sections-section-final-20261009.json) | 16 / 16 | 0 | 100% | 100% | Regression pass after inspecting cases |
| [Fresh final 16 cases](sections-final-holdout-20261009.json) | 2 / 8 | 2 | 50% | 25% | Rejected |

All evaluations use the existing proposed description-only screen: zero false
positives, no recall decrease and at least 80% recall. Runtime approval is always
false. The final holdout fails both the precision and recall requirements.
These are fabricated controls, not market coverage, provider quality or a production
SLA. Do not combine these percentages with title-plus-description search metrics.

Static review also identified concrete unresolved defects:

- Stripping question punctuation can convert a role question into an affirmation.
- Unrecognized third-party/history headings can retain default current-role scope.
- Repeated occurrences of an action verb can inflate the duty-bundle threshold;
  ordinary software use can consequently look like software engineering work.
- Role input has a token-count bound but lacks a separate character bound.

These findings reinforce the no-go; the rejected prototype is not hardened for
untrusted service input. Initial/intermediate receipts retain their source hashes
as historical observations. Only final-source receipts are reproducible from the
current retained candidate; intermediate candidate source snapshots were not saved.

## Verification and reproduction

[Section tests](../../scripts/test_search_role_sections.py) cover section resets,
local negatives, quotes, corrections, bounds, both regression sets and exact final
holdout receipt reproduction. A passing test that reproduces the rejected result
is evidence integrity, not passing product acceptance.

[Final verification](verification-sections-20261009.json) records the required
isolated P0/object suites, search tests, backend/tool syntax and temporary test-data
removal. The historical candidate hash and retained file inventory remain in the locally retained, uncommitted
sections-manifest-20261009.json. Existing unrelated workspace changes are preserved.

Reproduce the final holdout with a fresh output filename:

```powershell
backend/venv/Scripts/python.exe -B scripts/search_context_quality.py --reference docs/search-quality/section-final-holdout-v1.json --before scripts/search_role_intent.py --candidate scripts/search_role_sections.py --output NEW_SECTION_REPORT.json
backend/venv/Scripts/python.exe -B scripts/verify_search_quality.py --output NEW_VERIFY_REPORT.json
```

The description-only comparison needs the Python standard library. Full verification
uses the existing project environment and retained filter worktree documented in
[the previous handoff](SEARCH003_CONTEXT_20261009.md). No paid/API/network requests,
private database changes or credentials are involved. Runtime/UI smoke, build,
deployment, Docker and browser work are not applicable to this offline candidate.
No commit, push or PR; nothing staged.

## Next roadmap action

Stop expanding this phrase-rule classifier. Preserve the now-seen sets as regressions.
The next bounded implementation should use explicit user-controlled role alternatives
while preserving title-only defaults, or evaluate a materially different semantic
approach against newly authored evidence before selecting it. A provider-backed
experiment would require its own data/cost scope; this decision authorizes no calls.
Do not add this candidate as a production description toggle. SEARCH-003 remains
open, together with interpreted-search UX, seniority/exclusion rules and commute
semantics. Earlier AI-001/MON work and full-pipeline SEARCH-002 gaps are preserved.

## Windows helper adoption and resources

PowerShell 7.6.6 / FullLanguage and actual helper signatures were checked. Maintained
repository discovery, bounded reads, Git, native execution, new-file/JSON writing
and guarded document edits were used for the real task. Both independent fixture
authorships used the shared JSON writer with saved-content/hash readback. Markdown
validation uses the maintained checker after the verification receipt exists.
No SSH/Docker/publication activity was manufactured. Ordinary failing development
tests were repaired; no new operational tool incident occurred.

Retain the candidate, tests, two new references, comparisons, verification, handoff,
manifest and guarded backlog edit journal until the next SEARCH-003 decision;
owner CareerTrellis engineering. Sizes are logical bytes, not reclaimed disk.
The verification runner removes temporary data/bytecode. No downloads, extra
checkout, server/preview, container or remote artifact was created. Existing prior
reports, fixture sets, worktrees and pending friction records remain intact.