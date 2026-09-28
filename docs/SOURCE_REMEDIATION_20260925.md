# Selected source and client-filter continuation — September 25, 2026

User selected University of Dayton, ZipRecruiter and client filtering after the two failed coverage windows. Implementation is local and uncommitted. No source activation, deployment, paid calls, scoring of real jobs, profile writes or new acceptance window occurred.

## Local implementation

- `candidate_filters.py` gives the three beta role names phrase matching, with a reviewed software engineer/developer alternative. Generic multiword searches retain whole-token matching. Dayton does not match Daytona; Ohio/OH and US aliases normalize. These remain deterministic retrieval rules, not a substitute for duty-based acceptance review.
- Employer catalog acquisition prioritizes known matches, then admits at most two uncertain-location candidates per board and five per provider into the existing 25-detail cap. Truncation is explicit. Details must satisfy role/location before scoring; rejected/unknown candidates enter filtered counts rather than source-error counts.
- Public feeds use the same role/detail eligibility filter. Text-inferred work arrangement is distinguished from structured metadata; incidental remote mentions and explicit remote negation cannot establish eligibility. Dayton-or-remote alternatives still allow an onsite Dayton role. The internal provenance flag is removed before matching/storage.
- `additional_sources.py` prepares bounded UD listing and ZipRecruiter MCP adapters. UD pagination is at most five 20-row pages, deduplicated by numeric requisition. Its headquarters is never used as a job location. ZipRecruiter uses the observed `search_jobs` schema, at most six keyword/location calls of five cards each; offset remains zero until deeper pagination is verified. Structured error/unsupported responses fail, while genuine empty responses remain distinct. Later-page failures preserve earlier valid candidates.
- Acquisition has fixed endpoints, redirect refusal, byte/request/deadline bounds, cancellation and no retry. No persistent queries/cards or credentials. ZipRecruiter `jid` remains in canonical URLs, and redirects cannot silently change the posting identity. UD URLs are similarly bound to numeric requisition identity.

## Activation limitations

`ENABLED = ()` is intentionally empty, with no environment activation switch. URL recognition/manual inspection is available, but automated discovery is disabled for both new sources.

UD's careers footer links [terms](https://udayton.edu/compliance/privacy-terms/index.php) limiting nonpersonal reuse without consent. Resolve consent or a supported feed before enabling product acquisition. The public listing was inspected for structure, not collected as beta inventory.

The official [ZipRecruiter MCP documentation](https://api.ziprecruiter.com/mcp/docs) and one successful live `tools/list` response establish the tool's input/output schema. The cards do not include full descriptions. Full-detail retrieval, applicable product reuse/retention/scoring conditions, and operating limits must be resolved before activation. No MCP job-search call was made. Do not infer an integration license from the absence of end-user OAuth.

Both sources currently reach the existing generic browser detail extractor. Neither has a live-verified full discovery/detail/save path. The next source-specific engineering step is validating that path under resolved source access; implement a dedicated detail parser if generic extraction is insufficient. No source is production-ready on this receipt.

## Verification

The isolated regression runner included source tests, feed metadata, search outcomes, public feeds, P0, object store/import/stored-object tests. **467 tests passed, two skipped, 11.204 seconds; syntax passed.** Temporary synthetic data was removed. A temporary loopback app returned HTTP 200 at `/api/version` and `/`; it stopped cleanly. The changed filtering flow was exercised synthetically through scoring/save boundaries, not with user data or live AI calls.

Independent read-only review found and verified fixes for inferred-remote false positives, combined US-remote candidate loss, and mixed city/remote alternatives. An end-to-end test exposed an overwritten filtered counter; corrected before the final passing run. Diff checks passed and no protected files were staged. The pre-existing object-store tamper-test change was preserved separately.

Deployment, exact live validation, source activation, new acceptance windows and beta opening remain outstanding. Historic failed receipts and the six-cell scope/floor remain unchanged. All other remediation alternatives are indexed as COV-01 through COV-15 in the workspace product backlog.
