# Beta search readiness — September 21, 2026

## Candidate and scope

Branch `codex/beta-search-outcomes`, based on hosted `web/main`
`0e98034` after operations PR #78 merged. Uncommitted local application repair;
not deployed, pushed, or published as a PR. Existing worktrees and root changes
are preserved. SEARCH-001 remains OPEN until reliable discovery and exact-release
live acceptance are established.

The repair rejects unrelated/malformed/challenge responses as unverified results,
retains fallback diagnostics, and distinguishes source failure, confirmed empty
results, no new matches, saved matches, and partial completion. Durable summaries
keep enum values and bounded counts with reconstructed messages. Completion
feedback remains visible after the progress window closes. No domain validation
is loosened and no new provider, credential, or paid service is introduced.

## Live read-only observations

- Application current remains `e0cd2e31cd5ac101c6cecd9a87c5e5c3562ea7bc`.
  Web and search worker active/running, zero restarts.
- Database backup health at evaluation `2026-09-21T16:02:59Z`: alert,
  `pgbackrest_inventory_stale`, inventory age 1,384,068 seconds. Timer has no last
  trigger; next September 24 22:16 UTC. Timer activation and boot were September
  13 around 04:30 UTC. This proves the freshness gap, not its complete cause or
  backup loss. The monotonic seven-day timer needs schedule reconciliation.
- Web backup health at evaluation `2026-09-21T16:01:23Z`: alert,
  `generated_object_mirror_stale`, mirror age 1,365,417 seconds, restore age
  1,365,258 seconds. The hourly evaluator exited 1 and retained its unit alert.
  The validated core status supplied the reason; no alert was cleared.

## Remaining operational gates

September21 follow-up: both stale backup-evidence gates below are now resolved;
see the root workspace `artifacts/BACKUP_FRESHNESS_20260921.md`. They remain in
this historical list to explain the earlier observations. Discovery continuation
is recorded in [Hybrid discovery candidate](HYBRID_DISCOVERY_20260921.md), including
the selected Serper/catalog implementation and remaining live/operational gates.

1. Prove reliable supported posting discovery with bounded public synthetic
   queries. Reporting correctness alone does not meet SEARCH-001 acceptance.
2. Refresh download-verified database recovery-mirror inventory through the
   reviewed `careertrellis-pgbackrest-inventory.service` after verifying installed
   bytes, storage, locks, and no concurrent backup/mirror/restore operation.
   This may run for hours; use owner/phase observation, not duplicate traversals.
   Investigate a durable timer schedule without lowering freshness thresholds.
3. Fresh generated-object capture requires an attended service-stop window under
   the reviewed `mirror-generated-objects-onedrive.sh` procedure. Publish only the
   sealed capture, verify recovery evidence, then re-evaluate health. No outage,
   capture, upload, deletion, or alert clearing is authorized by these observations.
4. Select and verify the final application candidate, then complete protected
   plans, fresh owner/factor/provider proof, transition revocation and attended
   activation with signup closed. Normal factor removal/general recovery remain
   separate application gates. Existing owner credential enrollment is complete;
   do not repeat it. Signup opening remains separately approved.

## Discovery source probes

Eight bounded unauthenticated public requests from WEB, with generic software
engineer queries, 12-second per-request timeout and 1 MiB response cap:

- Yahoo HTTP 500; Bing HTML and RSS HTTP 200, zero direct supported Lever links
  in this bounded probe (the RSS response was also empty of those links).
- DuckDuckGo initially returned nine specific Lever links for a jobs.lever.co
  query, then six for a lever.co query. Greenhouse, Ashby and SmartRecruiters
  queries returned HTTP 202 challenge responses with zero supported links.
- The first generic link-count probe was not a full posting fetch or application
  acceptance test. These observations reject DuckDuckGo as a proven reliable
  replacement; the experimental adapter was removed. No search backend was added.

No personal query, profile, credentials, paid AI, saved job, or database mutation
was used. Raw response bodies were not retained. Reliable discovery remains open;
the next design choice is a supported search API or discovery from a deliberately
selected employer/job-board catalog, with coverage and any cost reviewed first.

The follow-up [discovery investigation](DISCOVERY_REMEDIATION_OPTIONS_20260921.md)
records decoded-link probes, working public-feed samples for all four platforms,
managed-search options, request-planning defects and proposed acceptance gates.
It does not close the reliable-discovery gate.

## Local verification

Required P0/object suites plus search and browser regressions: 412 tests passed,
two existing skips. The actual completion renderer was exercised in Chromium with
synthetic outcomes and all network requests blocked, including persistent feedback,
plain-text rendering and accessible dismissal. Synthetic loopback startup verified
`/api/version`, `/`, closed-default session capability and graceful stop. Syntax
and whitespace checks passed. These are local checks, not hosted search acceptance.
Independent bounded review found candidate-cap and failed-save classification
gaps; both were corrected and covered, with no remaining actionable findings.
After removing the experimental fallback, all 41 focused search/browser checks
passed again, as did syntax and whitespace checks. Nothing is staged.

## Retention

Keep this roughly 6.33 MB checkout as the active repair candidate, owned by the CareerTrellis
operator; review when SEARCH-001 is resolved or the candidate is superseded.
No remote artifacts were created. Temporary synthetic test data and compilation
caches are disposable task-owned artifacts; cleanup is recorded in the final
verification receipt. Preserve the earlier cleanup handoff's retained items.

Cleanup completed: exact temporary directories `ct-beta-search-tests-20260921`
and `ct-beta-search-pycache-20260921` removed after boundary checks; 4,919,907 file
bytes reclaimed and absence verified. No remote storage mutation. Retain the
small root `artifacts/beta-search-friction-20260921.md` until shared-log incorporation.
