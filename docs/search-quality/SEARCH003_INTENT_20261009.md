# SEARCH-003 adversarial intent follow-up

October 9, 2026 (America/New_York). Owner: CareerTrellis engineering.
Authoritative acceptance and retained-resource handoff for this local slice.
Status: offline comparison completed; runtime adoption remains rejected.

This continues the [first description experiment](SEARCH003_20261009.md), preserving
its reports and the original 42-control reference. No production matching or UI
changed. The workspace is the existing dirty root checkout at
`C:/Users/hunte/Documents/Antigravity/jobapplier`; unrelated edits remain intact.

## Change and evidence

The [candidate](../../scripts/search_role_intent.py) recognizes three narrow forms
of explicit current duties. It abstains on recognized negative/conditional words,
double quotes, missing roles and descriptions over 50,000 characters. Matching
whole sentences reduces incidental colleague and past-experience matches.
Abstention is deliberately conservative and can reject valid descriptions.

The [evaluator](../../scripts/search_quality.py) exposes this only through
`--role-scope title_or_affirmative_description`. The existing title-only default,
legacy broad description option and geography filter remain. Reports now include
evaluator and intent-source hashes as well as the filter hash.

The separate [adversarial reference](reference-adversarial-v1.json) freezes 72
fabricated judgments and thresholds before comparison. It covers three roles in
two geographies, with explicit duties, incidental/past/quoted/conditional role
mentions, negation, contradictory prose, wrong/unknown geography and deliberately
unsupported implicit positives. It is not an independent real-world sample.

The new optional `max_precision_all_returned_drop` guard measures unique useful
identities divided by all returned rows. A false positive beyond position ten now
fails this guard. The old `precision_at_returned` remains a top-ten metric; older
references keep their existing threshold behavior. Unknown precision cannot pass
the new guard. No empty denominator is silently replaced with zero or one.

All six cells have the same measured results:

| Candidate | Known-set recall | All-returned precision | Fixed comparator |
| --- | --- | --- | --- |
| [Title baseline](adversarial-title-20261009.json) | 25% | 100% | Baseline |
| [Broad description](adversarial-title_or_description-20261009.json) | 75% | 33.3% | Rejected |
| [Affirmative description](adversarial-title_or_affirmative_description-20261009.json) | 75% | 100% | Passed |

[Original-reference replay](affirmative-original-reference-20261009.json) also
retains the four recovered positives. Original reference and baseline hashes remain
unchanged. The affirmative candidate still misses all six implicit-duty positives.

Independent review found another false positive: `Historical role example:` followed
by a newline and `This position is a data engineer role.` Sentence grammar does
not establish whole-document intent. A dedicated test preserves this counterexample;
it was not added to the frozen reference after measurement. Single-quoted context
and other linguistic forms are also outside the narrow guard. Therefore comparator
`accepted: true` is explicitly not approval for production use.

## Verification and reproduction

[Focused tests](../../scripts/test_search_role_intent.py) cover the adversarial
comparison, original-reference gains, bounded input, context limitations, and
all-returned precision including a false positive beyond the first ten positions.
The [isolated verification receipt](verification-intent-20261009.json) records the
updated runner's search, P0 and object suites, backend/changed-tool compilation,
and temporary test-data cleanup. The runner now includes both new search suites.

From the repository root, use the existing Python runtime and a fresh output path:

```powershell
backend/venv/Scripts/python.exe -B scripts/search_quality.py --reference docs/search-quality/reference-adversarial-v1.json --filters .worktrees/serper-open-beta-20261007/backend/candidate_filters.py --role-scope title_or_affirmative_description --before docs/search-quality/adversarial-title-20261009.json --output NEW_COMPARISON.json
backend/venv/Scripts/python.exe -B scripts/verify_search_quality.py --output NEW_VERIFICATION.json
```

The filter source is intentionally the retained release worktree, whose hash is
recorded in every replay. That checkout is a prerequisite; these tests are not yet
portable to a fresh checkout without it. The fixed reference tests verify its hash.
No provider calls, user data, database migrations or network collection are needed.

Application URL, runtime/UI smoke, build, deployment, browser, Docker and SSH:
not applicable because this changes only offline evaluation tooling. No commit,
push or PR. Hosted acceptance and broader search-quality measurements remain open.

## Helper adoption and retained resources

The procedure index routed to Windows, Markdown and acceptance guidance. PowerShell
7.6.6 / FullLanguage and actual helper signatures were checked before invocation.
Get-WorkflowRepository, Get-WorkflowFiles, Read-WorkflowExcerpt, Invoke-WorkflowGit,
Invoke-WorkflowNative, Write-WorkflowUtf8 and Edit-WorkflowDocument were used for
real work. Independent review also used discovery/excerpt helpers. Markdown links
were checked with the maintained checker and bundled Python. This is evidence of
successful bounded reuse, not measured prevention or time savings. No artificial
SSH, Docker or publication operation was performed. Prior friction notes remain
pending shared consolidation; no new operational incident occurred in this slice.

The source/receipt manifest beside this handoff records exact paths, hashes and
logical sizes. Retain the new matcher/tests/reference/reports and guarded backlog
edit snapshots for the next SEARCH-003 review; owner CareerTrellis engineering.
Temporary verification data is removed by the runner. No new checkout, downloaded
package, preview process or remote resource was created. Available C: space before
verification was 1,235,252,035,584 bytes. Logical sizes do not imply disk reclamation.

Next: separate document-context judgments from positive sentence forms, expand
holdout coverage and quantify false negatives before proposing runtime integration.
SEARCH-003 remains open for those controls, editable synonyms, seniority/exclusion
rules, interpreted-search UX and the commute decision. All full-pipeline SEARCH-002
acceptance gaps remain; the earlier AI-001 and MON baseline work is preserved.