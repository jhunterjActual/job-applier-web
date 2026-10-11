# SEARCH-003 context holdout decision

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.

October 9, 2026 (America/New_York). Owner: CareerTrellis engineering.
Authoritative decision/resource record for this continuation. **No-go:** whole-document
context exclusions are rejected. The previous experimental matcher is restored;
production behavior is unchanged.

## Independent evidence

A separate reviewer authored and froze [30 synthetic judgments](context-holdout-v1.json)
(15 positives) before candidate evaluation. SHA-256:
`c7f3a50142f683f63537668a9ec3a397007b8eea96b193b7712f2f8fb9de1ef3`.
Labels describe desired current-role interpretation, including benign negatives,
conditional accommodation, quoted current duties, mixed past/current sections and
implicit duties. Missing description means insufficient evidence, not proof of an
irrelevant job. One 53,338-character positive tests the processing boundary.
The sample is small and synthetic, not representative market coverage.

The candidate added conservative whole-document abstention for historical/example,
customer/client and related context words plus single quotes. [The comparison](context-comparison-20261009.json)
records exact matcher, evaluator and reference hashes, predictions and missed IDs.
The description-only screen fixed zero false positives, no recall decrease and an
80% recall floor before comparison. These are proposed offline criteria, not an
approved production search SLA. Neither candidate passes.

| Description matcher | True positives / 15 | False positives | Precision | Recall |
| --- | --- | --- | --- | --- |
| Previous affirmative matcher | 4 | 7 | 36.4% | 26.7% |
| Context-exclusion candidate | 3 | 2 | 60% | 20% |

The guard fixes the historical newline example and several third-party contexts,
but still accepts the archived-description and later-correction controls. It loses
the legitimate mixed-past/current positive. Both variants miss benign negatives,
implicit duties and other valid forms. An improved precision percentage does not
justify that recall loss. Do not compare these description-only percentages directly
with the earlier title-plus-description six-cell replay.

## Implementation disposition

- [Rejected candidate](fixtures/intent-context-candidate.py)
  is retained solely as evidence, SHA-256
  `bd1d14aa9db5577f91b30805b6ecb1188df335b90816faef85a18ecc0f71c0bf`.
- Previous source snapshot (local-only evidence: `../../artifacts/search003-context-20261009/intent-before.py`)
  and active `scripts/search_role_intent.py` match byte-for-byte at SHA-256
  `4818453ae994e24c75f87b0edf7381135cb4198c52e30c41601f467335294c04`.
  The previous experimental mode remains unapproved for runtime use.
- [Reusable context evaluator](../../scripts/search_context_quality.py) and
  [tests](../../scripts/test_search_context_quality.py) remain. Tests verify receipt
  reproduction, frozen inputs, source restoration, duplicate/invalid judgments,
  unknown denominators and recall-loss rejection. The normal isolated verification
  runner includes this suite.

The [verification receipt](verification-context-20261009.json) records the final
P0/object/search suites, syntax result and temporary-data removal. No runtime/UI
change, server smoke, deployment, network, paid request, secret or user database
operation was needed. No commit, push or PR. Unrelated workspace edits were preserved.

Reproduce from the repository root using the existing runtime and a new output:

```powershell
backend/venv/Scripts/python.exe -B scripts/search_context_quality.py --reference docs/search-quality/context-holdout-v1.json --before artifacts/search003-context-20261009/intent-before.py --candidate artifacts/search003-context-20261009/intent-context-candidate.py --output NEW_CONTEXT_REPORT.json
backend/venv/Scripts/python.exe -B scripts/verify_search_quality.py --output NEW_VERIFY_REPORT.json
```

The context evaluator requires only the standard library. The full search suite
still requires the retained filter worktree identified in the
[earlier handoff](SEARCH003_INTENT_20261009.md). No fresh-checkout portability claim.

## Next decision and adoption

Stop expanding a document-wide blacklist. The next description candidate needs
explicit section/subject attribution (current opening versus past/customer/example)
and local scope for negation, followed by a newly authored untouched holdout. Keep
these 30 cases as regressions; once inspected they are no longer an unseen holdout.
Implicit-duty interpretation and processing-bound tradeoffs remain separate gaps.
No model/provider call or runtime classifier has been selected or authorized by
this experiment. SEARCH-003 and full-pipeline SEARCH-002 remain open.

Shared Windows helper adoption: PowerShell 7.6.6 / FullLanguage and actual signatures
were checked before use. Repository/discovery, bounded reads, Git, native execution,
new-file/JSON writing and guarded document editing used the maintained helpers;
the independent author used Write-WorkflowJson and readback. Maintained Markdown
validation checks the handoff links. No manufactured SSH/Docker/browser operations,
new helper failure or operational incident occurred. Prior pending friction remains
in its existing index; no global reliability or time-savings claim follows.

Retain the fixture, two small matcher snapshots, evaluator/tests, reports and
backlog edit journal until the next SEARCH-003 decision; owner CareerTrellis
engineering. The locally retained, uncommitted context-manifest-20261009.json records historical paths, hashes
and logical bytes. No new checkout/download/container/remote artifact or preview
was created. The runner removes its temporary data/bytecode. Free C: space before
work was 1,235,086,675,968 bytes; no reclaimed-disk claim. Snapshots and the rejected
candidate are deliberate evidence, not disposable deployment files.