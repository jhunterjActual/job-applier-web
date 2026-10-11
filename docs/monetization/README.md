# MON-001/002 baseline proposals

October 7, 2026. Owner: Hunter / CareerTrellis product. Status: **reviewable proposal,
not an approved price, entitlement change, provider budget, or paid pilot**.

The [versioned plan matrix](plan-proposal-v1.json) is a proposed future shared UI
and server input. Runtime code does not load it. The existing free beta, account
cap, and finite Serper budget are unchanged. Existing credits do not justify a
recurring free allowance or a purchase; platform limits always bind first.

## Proposed packaging and allowances

| Monthly allowance | Free with BYOK | Pro, proposed $19/month |
| --- | --- | --- |
| Search runs / discovery provider attempts | 10 / 60 | 50 / 300 |
| Jobs scored | 100 | 500 |
| Tailoring actions | 5 | 30 |
| OCR pages / interview actions | 12 / 5 | 48 / 20 |
| Stored materials | 100 MiB | 1 GiB |
| Included managed AI spend | None | Up to $3, additionally limited by workflow allowances |
| Scheduled searches / concurrent runs | 0 / 1 | 1 / 1 |
| Tracking, manual import, export, deletion, existing downloads | Available | Available |

Free users pay their AI provider directly for optional AI actions. Ordinary text,
DOCX and text-PDF import remains local. Manual job import does not require AI.
Map-backed enrichment must disclose its separate provider setup/limits; the
proposed Pro journey must cover required geocoding without personal credentials.

Pro may explicitly select BYOK without a discount in the initial proposal. AI
requests then use that chosen personal provider/key, while platform discovery,
workflow and storage limits still apply. Do not automatically switch payers,
providers or models when a budget, key or response fails. No managed Free trial,
credit packs, rollover or automatic overage charges are proposed.

Reset counters at calendar-month UTC boundaries, including the displayed reset
date. Reserve logical workflow allowance once per idempotent admitted operation;
count provider attempts, retries and known/possibly-spent cost separately. A
retry must not consume another logical action or receive free provider credit.
Cancellation before dispatch can release its reservation; after dispatch, unknown
usage remains reserved until reconciliation. Job-count limits count the jobs in a
batch, not merely one HTTP call. OCR limits count pages. Scheduled work uses the
same limits and checks plan state again at execution.

Existing beta accounts would remain Free without deletion or retroactive charges.
Provide notice and a transition decision before enforcing new limits; the proposed
matrix is not authority to change today's beta. New accounts and upgrades receive
only the current period's difference, preventing repeated upgrades/downgrades from
resetting allowance. The renewal-period choice and billing proration must be
reconciled before processor selection and MON-006 implementation.

At exhaustion, show the binding allowance, usage and reset time, preserve saved
work, and offer an explicit wait/upgrade or eligible BYOK choice. A spend cap may
bind before a workflow-count cap; do not advertise that every maximum is guaranteed
simultaneously. Before expensive work, show that limitation and reserve the worst
permitted request cost. Do not promise a $3 cap until MON-004 enforces it durably.

Cancellation ends renewal; downgrade takes effect at the paid-through boundary,
with entitlement checked again for queued work. Failed/expired payment cannot
grant further managed spending. A downgrade preserves existing records and files;
over-storage accounts can read/export/delete but cannot add materials. Existing
downloads and personal-data export/deletion cannot require upgrading. Propose a
30-day product-led retention review for idle excess storage, with no automatic
deletion authority. Refund, tax and dispute policies remain decisions for MON-006.

## Reproducible cost worksheet

[Inputs](cost-inputs-v1.json), [baseline](cost-baseline-v1.json), and
[double-variable-cost sensitivity](cost-stress-v1.json) are machine-readable and
reproducible with [the calculator](../../scripts/estimate_unit_cost.py):

```powershell
backend/venv/Scripts/python.exe -B scripts/estimate_unit_cost.py --inputs docs/monetization/cost-inputs-v1.json --output NEW_REPORT.json
backend/venv/Scripts/python.exe -B scripts/estimate_unit_cost.py --inputs docs/monetization/cost-inputs-v1.json --multiplier 2 --output NEW_STRESS_REPORT.json
```

The calculator refuses existing output files. Workload quantities and token
counts are **hypothetical planning inputs**, not measurements. SEARCH-002's filter
replay provides no end-to-end request or token counts. AI-001 can now capture
normalized attempt receipts; an authorized synthetic full-workflow replay must
replace these assumptions before selecting commercial limits.

All scenarios include query preparation, scoring, tailoring, OCR, interview help
and AI retry fractions of 5%, 10% and 25%. Output assumptions include reasoning.
Discovery counts are total provider attempts, including retries, rather than
unique search results. No cache discount, batch discount or free provider tier is
assumed. Image tokens must be measured; the OCR assumption is not a bytes-to-token
conversion. An uncertain/cancelled provider request is not cost-free.

| Model / scenario | AI estimate | Discovery replacement estimate | Known subtotal |
| --- | --- | --- | --- |
| GPT-5 mini / light | $0.1271 | $0.0150 | $0.1421 |
| GPT-5 mini / typical | $0.6677 | $0.0800 | $0.7477 |
| GPT-5 mini / heavy | $2.5413 | $0.3000 | $2.8413 |
| Gemini 2.5 Flash / light | $0.1567 | $0.0150 | $0.1717 |
| Gemini 2.5 Flash / typical | $0.8231 | $0.0800 | $0.9031 |
| Gemini 2.5 Flash / heavy | $3.1341 | $0.3000 | $3.4341 |

These are **not total costs or contribution margins**. Maps/geocoding,
storage/backups, compute/egress, support labor, processor/subscription fees, and
refund/dispute/tax-service costs are unknown; both total cost and margin remain
null in generated reports. Quotes, usage/retention assumptions, and support load
must be supplied rather than treated as zero. No payment processor is selected.

At a proposed $19 price and a proposed 70% contribution-margin target, allowable
total variable cost would be $5.70. The typical scenarios leave $4.95 (OpenAI) or
$4.80 (Gemini) for all missing components. These are cost-budget differences, not
claimed margins. The heavy Gemini scenario already exceeds the proposed $3 AI
cap. Doubling variable costs makes both heavy AI scenarios exceed that cap and
leaves only $0.02 (OpenAI) or negative $1.17 (Gemini) before missing costs. The
heavy-user promise must be reduced or price/cap revised if measurements resemble
these scenarios. The proposed price is not ready to launch.

## Price provenance and model availability

Official pages fetched October 7, 2026:

- [OpenAI GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini):
  $0.25 input and $2.00 output per million tokens; cached input $0.025. The page
  marks the model deprecated. Its price is a comparison for the existing adapter,
  not a recommendation for a new managed pilot.
- [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing): 2.5 Flash
  standard text/image input $0.30 and output including thinking $2.50 per million.
  [The model page](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash)
  restricts availability to previously active users; new-project eligibility is
  unverified. No model migration is performed here.
- [Serper pricing](https://serper.dev/): Starter $50 for 50,000 credits, six-month
  expiry, taxes excluded. The worksheet amortizes this as $0.001/request; actual
  replacement cash needs depend on package purchase and credit expiry. Unused
  expired credits raise realized unit cost. Existing prepaid credits are finite
  and separate from replacement cost. No purchase/top-up is authorized.

These dated quotes require refresh and account eligibility/terms review before a
provider/model/processor commitment. The contract's optional RateCard is supplied
by trusted caller code; no production pricing table or billing settlement is added.

## Next decisions and verification

MON-001 remains open for owner acceptance of the matrix and transition policies.
MON-002 remains open for full-workflow counts, the six missing cost components,
quality-qualified available models and complete margin/downside review. Suggested
future pilot bounds are five participants, $15 aggregate managed AI and 1,500
discovery attempts per month, with a separate $5 maps envelope, all subordinate
to approved platform limits. These caps are proposals only; live participation,
spending and billing require their own bounded approval.

Four offline calculator regressions pass: missing-cost preservation, doubled
variable costs, complete synthetic margins and invalid-number rejection. Files
contain public quotes and fabricated workloads, no secrets or user telemetry.
Retain as proposal/source evidence; owner CareerTrellis product, review when actual
workflow receipts or commercial decisions become available. No Docker resource,
service, purchase, billing integration or entitlement change was created here.
