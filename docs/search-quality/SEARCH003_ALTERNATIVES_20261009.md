# SEARCH-003 explicit title alternatives — local candidate

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.

Implemented the next roadmap slice in the isolated, unpublished worktree
search-role-alternatives-20261009 (local-only evidence: `../../.worktrees/search-role-alternatives-20261009/docs/SEARCH_TITLE_ALTERNATIVES.md`),
based on locally recorded web/main 5a3103a. The dirty root checkout and unrelated
worktrees were preserved. No commit, push, deployment or PR was performed.

The user opts into up to three semicolon-separated role titles, sees the interpreted
OR search, and can save/reload that choice. Blank input retains resume suggestions;
ordinary old semicolon searches retain their previous single-query meaning.
Aliases use execution tokenization. Geography, request/reservation limits,
cancellation checkpoints and title-only matching remain in place. One collector
invocation receives all alternatives. No description classifier was adopted.

## Verification

- Final suite and loopback receipt (local-only evidence: `../../artifacts/role-alternatives-final-20261009.json`):
  430 tests, zero failures/errors, five platform/opt-in skips. Includes required P0,
  object-store/import/stored-object suites, search runs, cancellation browser tests
  and Serper reservation/concurrency tests. No real profiles, database or paid APIs.
- Focused candidate receipt (local-only evidence: `../../artifacts/role-alternatives-candidate-20261009.json`):
  16 tests passed, including explicit-vs-legacy storage round trips through temporary
  SQLite saved/due rows and queue claims, and six browser tests for stale responses,
  preview failures, editing during submission and safe text rendering.
- Compileall receipt (local-only evidence: `../../artifacts/role-alternatives-compileall-20261009.json`):
  all 300 Python files compiled from byte-verified temporary copies, avoiding the
  observed Windows long pycache path. Temporary copies removed. Original-source
  py_compile checks also passed. Node --check passed for app.js.
- Actual isolated app returned /api/version build 20261007.82 and / successfully;
  Chromium 148.0.7778.96 exercised the real form/preview/validation. Only final
  enqueue was intercepted to prevent provider calls. Screenshot (local-only evidence: `../../artifacts/role-alternatives-final-20261009.png`)
  inspected: opt-in and OR preview readable. Build label is inherited; the
  source manifest (local-only evidence: `../../artifacts/role-alternatives-resources-20261009.json`)
  identifies the changed bytes.
- Final diff reviewed, whitespace check passed, no files staged in either checkout.
  Protected root files were not staged. Hosted PostgreSQL dispatch, authentication,
  live recall/cost/latency and production behavior remain unverified.

## Release boundary and next action

SEARCH-003 stays open. Before hosted rollout, audit reserved-prefix collisions,
coordinate web/worker compatibility and rehearse rollback with a compatible
versioned-title decoder. Old workers cannot interpret the new stored envelope.
The candidate's persistence/release contract (local-only evidence: `../../.worktrees/search-role-alternatives-20261009/docs/SEARCH_TITLE_ALTERNATIVES.md`)
contains the exact remaining gates. Raw exports retain the lossless envelope.
Do not claim a live recall gain from this local implementation.

## Resources and Windows helper adoption

The resource register (local-only evidence: `../../artifacts/role-alternatives-resources-20261009.json`)
is authoritative for this slice: 7,452,058 logical bytes in the retained worktree,
plus exact evidence paths/sizes/hashes. Owner: Hunter / CareerTrellis agent workflow.
Retain code, screenshots, receipts and document-edit snapshots until integration
or explicit abandonment; review on the next roadmap/release task. Both task previews
stopped cleanly, listeners closed and temporary data was removed. No Docker resources
were created. Disk reclamation was not measured; final observed C: free space was
1,234,012,672,000 bytes, with concurrent activity not attributed to this task.

Procedure-index routing and PowerShell 7.6.6 FullLanguage were verified. Real work
used Get-WorkflowFiles, Read-WorkflowExcerpt, Invoke-WorkflowGit,
Invoke-WorkflowNative, Write-WorkflowUtf8/Json and Edit-WorkflowDocument. Guarded
index/backlog edits retain before/after hashes and exact-readback receipts in
artifacts/role-alternatives-friction-edit-20261009 and
artifacts/role-alternatives-backlog-edit-20261009 (small document snapshots retained
under the same owner/review condition). Adoption was partial: direct discovery
bypasses required recovery; no prevention rate or time savings is claimed.
The indexed pending recovery note (local-only evidence: `../../artifacts/role-alternatives-friction-20261009.md`)
records source discovery, Windows compiler paths and verifier multiprocessing.
No operations were performed solely to exercise the helpers.