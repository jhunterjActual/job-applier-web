# Opt-in Claude caching candidate

This candidate prepares request caching and cost accounting for review. Ordinary app requests stay on the existing path. It does not authorize paid provider calls, a cache warm-up, deployment, a study dispatch, or a change to the historical comparison freeze.

## Intended request boundary

An explicit caller selects the caching path; there is no account, environment, profile or UI switch that silently activates it. The initial candidate supports five-minute ephemeral caching only. Stable instructions and candidate evidence precede the changing job/date suffix. Candidate and job evidence remain user content, with the same factual constraints and response schema. No model, thinking setting, output limit, automatic retry, payer or API tool is added.

Reordering a prompt is a candidate change even when its factual inputs are unchanged. Existing comparison adapters must continue to reject an unfrozen cache request. This branch is not a replacement for the reviewed study candidate until its exact code, settings, request contract and quality evidence are separately frozen.

## Accounting and offline evidence

Record uncached input, cache reads, five-minute writes, one-hour writes when reported, and output distinctly. Missing or conflicting TTL evidence must not be converted to zero or priced as the cheaper write type. Use explicit Decimal rates; conservative reservations must cover a cache miss/write and output, not projected cache hits. The existing no-retry and unknown-liability rules remain in force.

Offline checks must cover the unchanged default request, explicit cache block placement, dynamic suffixes, factual constraints, unsupported policies, sanitized one-dispatch errors, usage normalization, write/read pricing, old receipt compatibility and refusal of incomplete usage. Mocked checks do not prove provider acceptance, cache hits, lower latency or generation quality.

## Small measurement proposal — not dispatched

After exact source and independent review acceptance, prepare a separate approval record for two sequential requests using one public synthetic candidate, the same eligible stable prefix, and two synthetic job suffixes. Keep model/settings and output ceilings fixed. The first real request creates the entry; do not add a hidden warm-up or token-count call. The second request may reuse it only if the provider reports a hit. No retries, extra tool calls, private profile or paid scoring APIs are included.

Before requesting that approval, bind the exact candidate, request digests, current model-specific eligibility, current rates, full two-request cache-miss/write reservation, available-credit evidence, prospective timing window and evidence-retention owner. Provider-reported cache read/write counts, output counts, settled cost and terminal outcomes are the result. A miss or unknown outcome remains in the evidence; do not repeat the experiment automatically. This proposal supplies no fabricated balance, measured hit rate, affordability claim or fixed dollar cap.

## Development skills

Two small skills live under `.claude/skills/`: `jobapplier-claude-api` and `jobapplier-context-optimization`. They contain original project instructions informed by the selected public libraries, not copied upstream skill packs. Claude Code can discover them in this candidate checkout; they are not uploaded API Skills, global account settings or an installation into the scored upstream workflow. Live discovery in the operator's Claude session is separate from file/frontmatter validation.

Sources reviewed 25 October 2026: [prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Claude Code skill locations](https://code.claude.com/docs/en/skills), [Anthropic claude-api skill](https://github.com/anthropics/skills/tree/main/skills/claude-api), and [context-optimization skill](https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering/tree/main/skills/context-optimization). Refresh mutable provider documentation before any paid measurement. No third-party PDF skill, retry framework, code-execution service or global hook is introduced.
