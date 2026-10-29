# Complete-batch funding worksheet v3 — native headquarters fallback

Proposed October 24, 2026. This explicitly supersedes the v2 CT request/packet
envelope, expected total and headroom. V2 remains unchanged historical planning
evidence: COST_WORKSHEET_V2.md SHA-256
`39e557124781833256abcbcaacd79d8b737ff55332bbd3c2b743769ddf27f728` at commit
`878aeb968bed75535f084f573a7d2c32ce67e228`. Neither version grants funding or
dispatch authority. Any owner response referring to the earlier two-request/$2
CT packet proposal does not approve this revised three-request/$3 proposal.

## Diagnosed native request omitted from v2

In the reviewed PDF candidate, backend/app.py `_tailor_resume_endpoint` resolves
the profile's actual AI settings, generates tailored materials, then resolves
headquarters before saving the application. With no cached result, it calls
`resolve_headquarters` using the same AI settings. In backend/maps_providers.py,
an unknown or failed maps lookup plus a configured AI key invokes
`generate_structured(..., HeadquartersAIResponse)` once. This is an additional
Claude request even when the final headquarters remains Unknown.

The earlier direct-tailor pilot bypassed this application-route step. Native
workflow accounting must cover it. Six distinct synthetic employers mean the
conservative batch assumes one uncached/unknown headquarters fallback per material
case. Do not seed a cache or bypass the resolver to make the old envelope fit.
Retain actual cache/maps outcomes and all provider requests; an unused fallback
allowance is not permission to regenerate or add critique calls.

## Revised complete-batch expectation

The v2 pricing basis, no-cache/no-premium/no-paid-server-tool boundary and native
upstream assumptions remain unchanged: Sonnet 5.5 at $2/M input and $10/M output,
verified by primary from official Anthropic pricing October 24. Freeze price and
settings again with the final candidate. Amounts are USD.

| Work | Assigned count and assumed unit | Expected |
|---|---|---:|
| CT fit | 12 × $0.008 | $0.096 |
| CT material generation | 6 × $0.020 | $0.120 |
| CT native headquarters AI fallback | 6 × $0.008 | $0.048 |
| Upstream full fit+materials/native review/tool workflow | 6 × $2.25 | $13.500 |
| Upstream fit-only | 6 × $0.25 | $1.500 |
| Total expected provider cash | Complete assigned batch | $15.264 |
| Planning allowance | Round to $20; $4.736 allowance above expectation | $20.000 |

The $0.008 headquarters unit is a planning assumption, not a measured native-run
average. Complete exact-runtime proof and token accounting must verify the call
path and categories. Local compiler/browser/PDF tools have no study API fee;
agent-review subscription cost and human effort remain unmeasured. There is no
separately billed scoring API allocation. Additional paid critique, startup/model
validation or other requests must be inventoried before freeze and repriced;
they cannot be silently taken from headroom.

Maps HTTP operations are distinct from Claude token charges. Freeze the exact
maps provider, synthetic fixture/service boundary and any price/quota implications
before dispatch. This estimate includes no paid Google Maps or other maps-service
fee and grants no external-service activation. An actual provider-priced maps
operation requires a revised explicit allocation. A scripted unknown maps response
proves only the declared mechanical fixture boundary.

## Revised conservative reservation envelope

Preserve v2 per-request bounds of counted input ≤100,000 and requested output
≤32,768 with the reviewed conservative formula and $0.919840 maximum request
reservation. No native request limit is silently reduced. These are conservative
estimates, not mathematically guaranteed provider billing limits.

| Stage allocation | Count | Per-case cap / maximum provider requests | Total |
|---|---:|---|---:|
| CT fit, including the six material cases' fit stages | 12 | $1 / 1 | $12 |
| CT material generation plus native HQ fallback | 6 | $2 / 2 | $12 |
| Upstream fit stages, including material cases | 12 | $4 / 4 | $48 |
| Upstream material/native review/tool stages | 6 | $20 / 20 | $120 |
| Complete assigned stage allocations | 36 | Same shared campaign | $192 |
| Unassigned headroom | — | Cannot finance extra scope/retry without approval | $8 |
| Proposed shared study ceiling | — | New owner funding decision required | $200 |

Shared full-packet caps: CT **$3 / three requests** for each V01–V06 fit+materials
packet; CT V07–V12 **$1 / one request**. Upstream remains **$24 / 24 requests**
for full packets and **$4 / four requests** for fit-only cases. Every request,
including the HQ fallback and native reviewer/tool continuations, consumes the
same stage, packet and shared campaign allocation. Dollar settlement never resets
request counts. The equivalent packet allocation is CT6×$3 + CT6×$1 + UP6×$24 +
UP6×$4 = $192.

Maximum request count is CT12 fit + CT12 materials/HQ + UP48 fit + UP120 materials
= **192**. At the common request bound, summed reservations are
**$176.609280**. A maximum CT full packet reserves $2.759520 < $3; CT material
stage $1.839680 < $2. Upstream bounds are unchanged. Rounded stage/packet caps
sum to $192, leaving $8 within $200 without relying on favorable early settlements.

## Superseding funding decision and readiness

Present this concrete revision to the owner: expected approximately $15.264
provider cash (planning allowance $20), conservative ceiling $200, with revised
CT full-packet cap $3/three requests, CT fit-only $1/one, upstream full $24/24 and
fit-only $4/four, at most 192 provider requests. Funding binds a fresh reviewed
manifest with these exact caps and the final candidate/settings/workflow proofs.
An answer to a previous $2/two-request CT question must be superseded explicitly.

No paid dispatch follows from this document. Unknown outcomes retain the full
request reservation and pause the shared route. Keep every failure, first draft
and native revision; no automatic retries or replacement outputs. Exact native
mechanical proof, estimator category coverage, reviewed candidate/freeze and
explicit matching funding approval remain required. The historical pilot's
unknown charges and prior authorizations remain untouched.
