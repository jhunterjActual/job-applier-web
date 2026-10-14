# AI-001 provider contract baseline

Original candidate: October 7, 2026, based on application commit
`1ab61bb99df55db08aa6043aaf071d447e6912cb`, recorded build `20261007.82`.
No release, model default, credential, provider budget or billing change is activated.

## AI-004 offline exact-fact controls

October 14, 2026: the [evaluator](../scripts/evaluate_ai_facts.py),
[authored synthetic inputs](ai-facts-synthetic-v1.json) and
[expected report](ai-facts-result-v1.json) test evaluation mechanics without
calling providers. Run from the repository root with a new output path:

```powershell
backend/venv/Scripts/python.exe -B scripts/evaluate_ai_facts.py --input docs/ai-facts-synthetic-v1.json --output NEW_REPORT.json
backend/venv/Scripts/python.exe -B -m unittest discover -s scripts -p test_ai_fact_evaluation.py
```

The fixture uses two small fact sets, not full resume/job workflows or real model
responses. A final successful output must contain only a unique list of exact
facts, include every required fact and invent none. Paraphrases fail this exact
comparison; semantic factuality and relevance require a separate reviewed rubric.
Every candidate covers the same cases. Earlier attempts contribute to total
cost and summed attempt latency, including errors; only the final output is
scored. Summed latency is not wall-clock workflow time. The cost denominator is
passing cases, not all calls; zero passing cases gives no per-pass cost claim.
Any unknown attempt measurement leaves its total null and exposes the known
subtotal. Inputs must describe complete attempt histories: omitted attempts
cannot be detected by this evaluator. Costs and timing here are invented controls,
not current prices, observed performance or billing evidence.

Twelve tests cover scoring, malformed schemas, cancellation, complete coverage,
retry/unknown metrics, invalid values, duplicate JSON fields and output retention.
The report never grants routing approval. No runtime code loads this benchmark;
model allowlist, agreed quality floors, full workflow evaluation and any provider
calls remain future AI-004 work. Claude live verification stays deferred.

## Entry-point inventory

| Workflow | Current source | AI operation / representation |
| --- | --- | --- |
| Query preparation | searcher.extract_search_keywords_from_resume | SearchKeywordResponse, query_preparation |
| Job discovery/detail extraction | searcher, public_job_feeds, discovery_sources | Existing deterministic HTTP/DOM/JSON-LD paths; not an AI call |
| Job scoring | tailor.analyze_job_match and batch matching | JobMatchResponse / BatchMatchResponse, scoring |
| Resume text/DOCX/text-PDF import | resume_documents | Local extraction, not an AI call |
| Scanned resume pages | app resume import callback -> extract_text_from_images | resume_ocr, one PNG page per attempt |
| Resume/cover-letter tailoring | tailor | TailoringResponse, tailoring |
| Interview help | interview_prep | InterviewPrepAIResponse, interview_help |
| Headquarters fallback | maps_providers | HeadquartersAIResponse, headquarters; separate maps calls are not AI usage |
| Key/model capability check | validate_provider_capability | CapabilityResponse, capability_check; still a potentially paid request |

[ai_contracts.py](../backend/ai_contracts.py) centralizes the operation limits,
schema routing, reviewed capability declarations, usage normalization, optional
rate-card estimates and caller-owned receipt scopes. Existing application schemas
remain at their current call sites; the adapter validates them as before.

## Dispatch and limits

- Unknown nonempty provider names raise `unsupported_provider` before credential
  lookup or network dispatch. Legacy omitted provider selection still uses Gemini.
  No cross-provider/payer fallback or automatic retry is introduced.
- Existing dictionary/list return values are preserved. Structured calls gain
  optional `operation`, `cancel_check` and `rate_card` arguments; OCR gains an
  optional rate card. Existing callers need no signature changes.
- Input caps are application UTF-8 byte limits, not model context-token claims:
  1 MiB query, 2 MiB scoring/tailoring, 512 KiB interview/generic, 128 KiB headquarters,
  4 KiB capability probe. Request output caps are 4,096 query/headquarters,
  16,384 scoring/interview, 32,768 tailoring/generic, and 1,024 probe tokens.
- OCR permits at most 12 pages and 12 MiB per PNG, matching the existing document
  importer, with 8,192 output tokens per page. Validate the whole page batch before
  its first dispatch. Existing response byte caps remain 2 MiB; Gemini text/schema
  outputs now also receive that size check.
- Structured timeout is an integer from 1 through 90 seconds; keyword calls retain
  their existing 45-second timeout and thinking setting. Synchronous in-flight
  requests cannot be force-cancelled: callbacks run before dispatch and after the
  response, and prevent the next OCR page. A response received after cancellation
  still retains its usage. Cancellation does not imply a provider refund.
- Existing Gemini 2.5 Flash and GPT-5 mini declarations cover schema and image
  input. Custom model IDs retain remote validation behavior; their capabilities
  and account availability are unknown locally. Local declarations do not prove
  hosted/account access. Capability checks still require an explicitly requested
  provider call. Capability-aware setup/catalog refresh remains AI-003 work.
- Refusals and truncation are checked before accepting output, including valid
  partial JSON. Safe error codes retain authentication, permission, rate_limit,
  model_unavailable, unsupported_request, network/provider_error, invalid/empty/
  oversized_response and add unsupported_provider/operation, invalid_input,
  truncated, invalid_rate and receipt_limit. Raw responses/errors are not receipts.

## Receipt and cost semantics

Use `with ai_contracts.capture_receipts() as records:` around a caller's work.
It is an opt-in, bounded in-memory scope, not telemetry or a durable spending
ledger. It neither reads other accounts nor emits logs. Nested scopes and
independent threads isolate records; inherited contexts share a lock and reserve
one of at most 1,000 slots before dispatch, preventing concurrent cap overrun.
While an attempt is running its slot is `in_progress`, never a completed receipt.

Each dispatched attempt records selected provider/model, operation, outcome,
dispatch state, normalized usage, and optional estimated USD/rate version. No key,
prompt, resume, job text, provider response ID or provider error body is retained.
Credentials are excluded from the settings object's repr. Existing callers that
do not capture receipts retain no new usage history.

OpenAI output includes reasoning; Gemini output normalizes visible plus thought
usage from reported totals/components. Missing, malformed, out-of-range or
contradictory metadata stays unknown. Known zero and missing usage are different.
Receipts preserve usage even when validation/refusal/truncation fails. Preflight
validation failures never dispatch; callback cancellation before dispatch gets a
non-dispatched receipt with unknown usage. Post-dispatch failure/cancellation must
not be treated as a free request by a future ledger.

An optional immutable RateCard must match the exact selected provider/model and
have finite nonnegative Decimal rates. No default prices are installed. Cost is
null without adequate usage and a rate card; a missing cache split also leaves
cost unknown if cached and uncached rates differ. Estimates exclude cache storage,
provider tools, taxes, payment fees and other services. They are not bills or
reservation authority. Durable reservation, restart/retry reconciliation and
entitlement/payer enforcement belong to MON-003/004/005.

## Official API evidence and availability

Reviewed October 7, 2026:

- [OpenAI structured output](https://developers.openai.com/api/docs/guides/structured-outputs)
  documents refusal and incomplete-output handling.
- [Gemini generateContent](https://ai.google.dev/api/generate-content) documents
  prompt, visible, thought and total usage, plus finish/block reasons.
- [GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini) supports
  image input and structured output but is marked deprecated.
- [Gemini 2.5 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash)
  supports image input and structured output; its page restricts availability to
  previously active users. No new-account support or model migration is asserted.

These caveats must inform AI-003/004 before selecting a managed pilot model.
Defaults are unchanged in this candidate, and no hosted/provider smoke was made.

## Verification scope

The same synthetic suite exercises both adapters' actual public entry points with
mocked transports: schema output, normalized usage, rate estimates, missing usage,
refusal/truncation, malformed content, timeout, cancellation, OCR and preflight
limits. Additional checks cover SDK enums, contradictory usage, secret-free repr,
nested/thread scopes and overlapping inherited-context receipt admission.

Required P0/object regressions and backend compileall use temporary local SQLite
data and bytecode. A disposable loopback preview checks `/api/version` and `/`,
then shuts down cooperatively and verifies the listener closes. The changed AI
flow is exercised through mocked providers; these tests cannot prove live model
quality, hosted acceptance or durable cost enforcement. No schema migration occurs.

AI-001's bounded contract baseline is implemented locally. Remaining acceptance:
workflow callers adopting scoped receipt collection, measured workflow estimates,
capability-aware setup, provider/account model availability and separately approved
hosted acceptance. Preserve existing deployment-before-PR ordering.

## October 12 integration

The existing unpublished candidate is integrated into the root workspace based on
`aedc188` (recorded `web/main`). The provider adapter was unchanged between its
original `1ab61bb` base and this baseline; its imported bytes matched the retained
October 7 source manifest. The original worktree is preserved.

Review found that the original prompt caps rejected otherwise accepted resumes.
The revised byte caps accommodate the existing 200,000-character resume,
50,000-character job description and 40,000-character supplemental evidence,
including four-byte UTF-8 characters and prompt overhead. Interview preparation
covers four 30,000-character contexts. These are application safety bounds, not
claims about a selected model's context window or account availability.

The synthetic contract suite now exercises actual query, single/batch scoring,
tailoring and interview callers under scoped receipt capture for both providers.
It checks maximum-size Unicode input, unchanged return shapes, normalized usage
and receipts for errors that workflow callers handle. This proves local caller
compatibility; production receipt persistence and durable spend control remain
separate work. Run `scripts/verify_ai_contracts.py --output <new-receipt.json>`
with the project Python environment for isolated contract/P0/object tests, source
compilation and a disposable loopback preview. `--focused` runs contracts only.
The verifier excludes dependency venv files from source compilation and removes
its temporary data and bytecode. It requires a runtime permitted to create and
remove temporary directories. All provider responses are mocked.

No live model, provider, account capability or October 7 price/availability claim
was refreshed in this integration. Model defaults and budgets remain unchanged.
Deployment and hosted acceptance remain open before PR publication.
## AI-002 Claude transport candidate — October 12

[claude_adapter.py](../backend/claude_adapter.py) is an isolated candidate with
explicit caller-supplied settings. It is not registered in the application provider
list, profile API, credential store or UI. No live account/key was created or read.
Existing Gemini/OpenAI defaults and behavior remain covered by their contract suite.

The candidate uses a fixed Anthropic Messages endpoint, rejects redirects and
unknown models, validates original Pydantic schemas after adapting unsupported
numeric/string/array bounds for the remote schema, and preserves refusal/truncation
usage receipts. OCR sends one PNG page per attempt, preflights the entire batch,
and stops before the next page after cancellation. At most 12 pages and 10,000,000
base64 bytes per image are admitted. Decoder/dimension integration remains part
of the document-import acceptance slice. Socket timeouts are bounded to 90 seconds;
this is not an overall wall-clock deadline or in-flight cancellation guarantee.
There are no retries, tools, alternate endpoints or provider/payer fallback.

Usage adds noncached input, cache reads and cache writes, retaining cache creation
separately. Missing or malformed counters remain unknown. The candidate does not
accept rate cards or claim bill reconciliation; the shared estimator returns an
unknown estimate for positive cache creation rather than pricing it as ordinary
input. Receipts contain no prompt, response body, key or provider request ID.

Official references refreshed October 12:

- [Sonnet 5.5 model](https://platform.claude.com/docs/en/models/sonnet-5-5/overview):
  candidate ID `claude-sonnet-5-5`, text/image input. This is an allowlist choice,
  not a quality comparison, account-access claim or change to the app default.
- [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs):
  use `output_config.format`; validate original constraints locally and reject
  refusal or truncated output even if its text parses.
- [Sonnet 5.5 changes](https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5):
  this no-tool adapter uses `thinking.type=between_tools` to avoid up-front
  thinking; `disabled` is not accepted by this model.
- [Pricing](https://platform.claude.com/docs/en/about-claude/pricing): standard
  input/output $2/$10 per million tokens; cache read $0.10, five-minute write
  $2.50 and one-hour write $4. These are dated reference prices only; no price
  table, budget or spending authorization is installed.
- [Cache usage](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
  defines separate input/read/write counters.
- [Vision](https://platform.claude.com/docs/en/build-with-claude/vision) documents
  base64 image input and the direct API's 10 MB encoded-image limit.

Verification: the combined AI/P0/object suite ran 399 tests with two platform
skips; source compilation, loopback version/dashboard and cooperative preview
shutdown passed. Two added schema/envelope tests and the encoded-image correction
then passed the final 26-test focused suite with source compilation. Final
local-only receipts are `artifacts/ai002-transport-verification-20261012.json` and
`artifacts/ai002-transport-final-focused-20261012.json`. All transports were mocked;
workflow schema compatibility is narrower than an end-to-end user flow. Independent
static review found no blocking issue; its timeout limitation is recorded above.

Next bounded AI-002 work: implement encrypted BYOK provider admission and an
additive, idempotent profile/schema migration; ensure provider switching preserves
other keys and secrets never appear in readback. Add profile/UI selection only
with that storage boundary and test saved/queued execution, downgrade/rollback,
real workflow callers and rendered PNG limits. Then obtain separately scoped
hosted smoke acceptance before claiming live Claude support. No migration,
provider call, deployment, commit, push or PR occurred in this transport slice.
## AI-002 hosted backend integration — October 12

This supersedes the transport-only registration status above. Claude is now
registered in backend dispatch and hosted profile API admission. The existing UI
has not been extended yet; this candidate must not be released as a complete
Claude user journey until that follow-up is ready.

`anthropic_api_key` is a new API request field, not a provisioned credential or
new local configuration item. No real key was created, read, requested or stored.
Hosted writes use the existing encrypted `provider_secrets` table and owner-bound
cryptographic context. GET returns only `anthropic_api_key_configured`; plaintext
is removed defensively. Explicit blank revokes only Claude, omitted fields leave
other providers alone, and unauthenticated writes fail. Standalone profile and
key writes reject Claude before any SQLite mutation, including mixed-key updates.
No plaintext Claude column or environment-variable fallback was introduced.

Source inspection found `profiles.ai_provider` and `provider_secrets.provider`
already use unrestricted text, so no schema migration is necessary. Tests inspect
that existing schema and preserve forced RLS. This is source/mocked evidence,
not a live PostgreSQL migration or tenant-isolation acceptance result.

The shared provider dispatcher now routes structured requests and OCR explicitly
to Claude. Query and scoring callers use only the encrypted Claude key when no
explicit key is supplied, never another provider's configured key. Hosted profile
admission accepts only the reviewed model. Existing Gemini/OpenAI settings and
other saved credentials retain their prior behavior. Cost estimates remain
unavailable for Claude in this slice.

The isolated combined verification ran 414 tests with two platform skips and no
failures/errors; backend compilation, loopback version/dashboard checks, preview
shutdown and temporary-data cleanup passed. Receipt:
`artifacts/ai002-hosted-integration-20261012.json`. New tests cover hosted profile
admission, mixed local-write rejection, secret status readback, explicit workflow
dispatch, encryption/tamper detection, tenant/provider binding, revocation and
rotation. PostgreSQL and provider traffic are mocked. The independent initial
storage-only test run did not set a temporary data directory; its import graph
has no SQLite/database initialization, but config may ensure directory existence.
The final combined run isolates all tests with a temporary data directory.
Independent static review found no blocking issues in this scoped integration.

Next: hosted-only UI selection/key controls and capability-aware status; prevent
the older UI from resetting a Claude selection to Gemini. Complete browser,
saved/queued execution, rendered-image limits and rollback acceptance before
release; old application code cannot consume profiles selecting Claude. Reconfirm
live encrypted-store/RLS behavior and separately authorize the provider smoke.
No deployment, commit, push, PR, provider call or real credential change occurred.

## AI-002 application integration completion — October 12

The remaining application wiring is implemented locally. Profile setup now offers
Anthropic Claude with the reviewed Sonnet 5.5 model, a separate password field,
saved-key status, an official key-management link, and explicit save/test controls.
The backend exposes only the boolean `anthropic_available` capability; standalone
storage disables Claude. A saved Claude or unknown provider is preserved visibly
instead of silently switching to Gemini. Failed profile loads revoke cached
availability. Key saves clear and remask the entered value; other saved providers
remain unchanged. The updated script has a new cache version.

No credential was provisioned. The new visible field is **Anthropic Claude API Key**
(`anthropic_api_key` request field), stored through the existing encrypted hosted
provider store. There is no environment fallback or plaintext SQLite key column.
No password-manager entry or vault mapping has been created or assumed.

Terminal contract failures now retain safe error classifications through search
and persisted run results. Refusals, truncation, invalid input and rejected
redirects dispatch once; transient rate limits, overload and provider failures
retain the existing bounded retry policy. No cross-provider fallback was added.

Verification includes actual browser profile selection/save/reload, separate key
save failure/retry, clearing and masking, pending test blocking, stale/unknown
provider handling, failed availability reload and 390px keyboard-accessible key
controls. Backend tests cover actual tailoring, interview, batch scoring,
headquarters and rendered PDF-to-PNG OCR with mocked Claude responses. Claim
execution proves owner-scoped encrypted key resolution and restoration; the full
search regression proves terminal single dispatch and safe persistence. These
are synthetic tests, not live PostgreSQL RLS or provider-account acceptance.
The combined suite passed 427 tests with two platform skips, source compilation,
loopback version/dashboard readiness, cooperative stop and temporary-data cleanup.
Evidence: `artifacts/claude-complete-accepted-20261012.json` (final source including script cache version).

### Release and recovery requirements

The implementation is complete locally; AI-002 hosted acceptance remains open.
Before release, verify the exact deployed encrypted-store/RLS contract, prepare a
new forward-correction release that still understands Claude profiles and keys,
and perform a separately authorized bounded Claude smoke using an existing key
and budget. An old Gemini/OpenAI-only release is not a compatible correction once
Claude profiles exist. Preserve encrypted Claude rows and user provider selection;
never bulk-reset profiles, revoke keys, migrate plaintext or silently choose a
replacement provider as recovery. If Claude must be temporarily unavailable, keep
its selection and encrypted keys while refusing new Claude work clearly. Live
scheduler/queue and owner-isolation checks remain deployment acceptance, separate
from the mocked claim tests. No deployment or real provider call occurred here.

The release candidate includes a source-controlled Claude availability switch.
A compatible forward correction sets CLAUDE_ENABLED to False: profile readback
preserves Claude selection and configured-key status, while profile/key mutations
and all Claude transports refuse before dispatch. Existing encrypted rows remain
untouched. test_claude_recovery covers this disabled mode with synthetic data;
its results do not substitute for live hosted acceptance.