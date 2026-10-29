# Complete-batch funding worksheet v2 — proposal

No spending is authorized by this worksheet. Owner funding and exact readiness
freeze are required. Figures are USD; provider cash and reviewer subscription/
human effort are separate. The historical remaining allocation is not new funding.

## Pricing and historical evidence

Primary PM verified official Anthropic pricing October 24, 2026 at
https://platform.claude.com/docs/en/about-claude/pricing: Sonnet 5.5, $2 per million
input and $10 per million output tokens. Freeze exact model ID and provider
availability before dispatch. Base-only operation requires global inference,
standard speed/tier and zero cache/server-paid-tool activity; otherwise pause and
reprice before dispatch. No OCR, paid server tools, residency premium, fast mode,
cache creation, subscription-credit assumption or model switching is included.
Local browser/TeX/PDF tools have no study provider fee; their machine time remains
measured effort. Output tokens already include thinking; do not add thinking twice.

Retained October 23 parent/child ledgers contain 108 generation calls: 18 CT and
90 upstream. Six upstream fit+materials packets used 12,12,12,5,17,16 calls; the
six fit-only cases used 3,3,2,3,2,3. Those include incomplete/failed native workflows
and differing operational settings, so they are not six accepted packet costs.
Known usage was $9.735768 plus $0.647290 retained for two unknown charges, exposure
$10.383058; actual aggregate billing remains unknown and not invoice reconciled.

## Expected planning scenario — full assigned batch

| Work | Count and assumed unit | Expected |
|---|---|---:|
| CT fit | 12 × $0.008 | $0.096 |
| CT materials | 6 × $0.020 | $0.120 |
| Upstream fit+materials including native reviewer/tool turns | 6 × $2.25 | $13.500 |
| Upstream fit-only | 6 × $0.25 | $1.500 |
| Independent study annotation/adjudication | Existing authorized agent reviewers, no study API dispatch | $0 additional study API |
| Total expected provider usage | Complete assigned batch | $15.216 |
| Planning allowance | Round to $20; $4.784 buffer | $20.000 |

This is an expectation, not a cap or guarantee of native completion. CT material
assumption exceeds the old direct generation average but cannot absorb an added
paid critique feature: add its exact requests before freeze if enabled. Native
upstream review is already included. New CT quality prompts and real PDFs can
change output sizes; old costs are only directional inputs. Agent subscription
cost and human correction time are unmeasured, not economically free. Separately
billed reviewer APIs are excluded and prohibited unless explicitly added with
model, counts and their own approved reserve.

## Conservative reservation envelope — complete assigned batch

Proposed per-request maximum: counted input at most 100,000 tokens and requested
output at most 32,768. Do not lower a native requested limit silently. Any request
outside this envelope blocks readiness or dispatch pending a new binding.
Retain the prior conservative formula:

`ceil_microdollars((((counted_input * 2 + 4096) * 2) + max_output * 10) * 1.25 / 1e6)`

At those limits each reservation is $0.919840. This is a conservative estimate,
not a mathematical provider billing guarantee. Sequential settlement may release
unused reservations, but a complete-batch proposal must not assume favorable
early settlement to finance later assigned cases.

| Assigned work | Envelope | Reserved allocation |
|---|---|---:|
| CT six full fit+materials packets | 6 × $2 cap; at most two provider requests/packet | $12 |
| CT six fit-only cases | 6 × $1 cap; one provider request/case | $6 |
| Upstream six full fit+materials packets | 6 × $24 cap; at most 24 provider requests/packet | $144 |
| Upstream six fit-only cases | 6 × $4 cap; at most four provider requests/case | $24 |
| Unassigned conservative headroom | Cannot fund a retry, new case/model or reviewer API without approval | $14 |
| Proposed full-study ceiling | New funding decision required | $200 |

The corresponding maxima are 186 provider requests: 18 CT + 144 upstream full
packet + 24 upstream fit-only. At the common request ceiling their summed reserve
is $171.090240; case-specific rounded allocations sum to $186 and leave $14 within
$200. Native named reviewers' requests count toward the upstream packet maximum;
do not give subagents or tools a second budget. Enforce aggregate request counts,
packet totals, shared study cap and one active financial execution lease.

At max request limits a full CT packet reserves $1.839680 < $2, upstream full
packet $22.076160 < $24, fit-only upstream $3.679360 < $4. No request-level or
aggregate safety depends on the historical $4 packet cap. Native turns can still
exceed 24 or ten minutes; then the packet is a retained failure, never an automatic
retry. Exact-runtime offline proof must show ordinary full mechanics fit the
chosen limits; simulated response brevity cannot prove live model turn needs.

## Funding choice and readiness

Recommended decision presentation: approximately $15–20 expected provider cash
for the full assigned batch, with a deliberately broad $200 conservative ceiling
under the stated finite request envelope. A smaller owner ceiling may be chosen,
but its complete-batch feasibility must be recomputed from exact native requests;
do not substitute a cheap partial subset or guarantee all cases will complete.
The $200 proposal is not authority and no unused amount is automatically spent.

Any unknown outcome retains the complete request reservation and pauses further
paid work pending reconciliation; no replacement failures. Record request usage,
native review/tool turns, packet totals and study total with terminal receipts.
The final funding request must bind candidate/tooling/input hashes, current price,
concrete workflow proof and reviewer readiness. Missing readiness stays blocked
while unrelated authorized implementation continues.
