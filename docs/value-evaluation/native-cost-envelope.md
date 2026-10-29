# Native comparison cost arithmetic

`scripts/value_evaluation/native_cost_envelope.py` is a pure offline calculator.
It reads no files, credentials, clocks, provider accounts or ledgers and makes no
network calls. Import and `estimate()` cannot authorize or dispatch work.

Its fixed inventory preserves twelve fit cases and six material cases per
product. CareerTrellis uses one fit request with native output limit16,384,
one tailoring request32,768 and one uncached/unknown headquarters fallback4,096.
The material allocation includes both latter requests. Upstream reserves up to
four fit and twenty material requests per case, including its native reviewer
and tool continuations, each with unchanged native output limit32,768. There are
at most192 generation requests and5,922,816 requested output tokens. These are
limits, not predictions or measured usage. No human corrections or scoring API
calls are added.

For every request, retain the reviewed conservative estimate:

`ceil_microdollars((((counted_input * 2 + 4096) * input_price) + max_output * output_price) * 1.25 / 1e6)`

All arithmetic uses an isolated64-digit decimal context, independent of caller
precision/rounding settings, and leaves the caller's context unchanged.
Round each request upward before multiplying. At declared rates$2/M input and
$10/M output and a100,000 counted-input limit per request, complete-batch
reservations total$172.001280 before contingency. Keeping native operation limits
saves$4.608 compared with applying32,768 to every request. Even a hypothetical
zero-input declaration reserves$76.001280 for native output and fixed margins;
that sensitivity case is not a feasible zero-input workflow.

Call `estimate(input_limits, pricing, available_cap_usd, contingency_usd)` with
an exact five-key map: `ct_fit`, `ct_tailor`, `ct_headquarters`, `up_fit`,
`up_materials`. Each value is an integer0–100,000. Pricing contains exactly
`model`, `input_usd_per_million`, `output_usd_per_million`, `source`, and
`verified_utc`; model is `claude-sonnet-5-5`, source is the official pricing page.
Rates, cap and contingency are nonnegative decimal strings with at most six
fraction digits; rates must be positive. There is no account-balance or cap
default. Keep actual account amounts and resulting private reports outside
public repository commits.

The result exposes operation, stage, full-packet and fit-only reservations;
complete assigned totals; contingency; supplied cap; and any arithmetic gap.
`arithmetic_within_supplied_cap` is an arithmetic comparison only. Both
`future_input_bounds_verified` and `funding_or_dispatch_authorized` remain false,
including when the numbers fit. It cannot establish that a future adaptive
request will stay inside a declared input bound. It assumes neither favorable
early settlement nor retries, replacement cases, reduced native output limits
or hidden reviewer allocations.

Count the complete actual input, including system prompts, tool schemas,
reviewer context, thinking/context behavior and nested image blocks from native
PDF visual reads. Raw JSON bytes and scripted token-count responses are not
real provider token evidence. Anthropic's free token-count endpoint supports
base64 image/PDF and client-tool inputs; URL/file image sources, most server
tools and MCP are unsupported. Counting remains an estimate. Any later provider
counting needs its own authorized execution context; this calculator supplies
none. Unsupported billable categories, caching or premium settings require a
separately reviewed policy rather than silently changing the formula.

Sources checked October24,2026: [official pricing](https://platform.claude.com/docs/en/about-claude/pricing),
[token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting),
[vision inputs](https://platform.claude.com/docs/en/build-with-claude/vision).
The supplied price timestamp is a declaration, not live freshness verification.

Run synthetic arithmetic controls from `scripts/value_evaluation`:
`python -B -m unittest -v test_native_cost_envelope`. They check exact native
denominators, per-request rounding, packet/stage/batch agreement, contingency,
cap boundaries and refusal of omitted operations or unsupported declarations.
