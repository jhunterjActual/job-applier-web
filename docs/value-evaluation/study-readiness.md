# Offline study-freeze readiness checks

`scripts/study_readiness.py` audits an explicitly supplied synthetic application
study v2 manifest. It reads declared local files, checks hashes, compares native
proof bindings and computes complete-batch reservations. It never discovers
evidence or credentials, contacts providers, alters a ledger or starts a run.
Use a dedicated evidence root containing only synthetic study material; explicitly
named files are read, so do not point it at private profile or configuration data.
A complete declaration is **not** proof of authenticity, human approval, current
pricing, clinical/statistical validity or a guaranteed provider billing bound.
The operator and independent reviewers still decide readiness and authorization.

Run from the repository root with an existing Python environment:

```powershell
python -B scripts/study_readiness.py <evidence-root> <relative-manifest.json>
python -B -m unittest discover -s scripts -p test_study_readiness.py
```

The command emits only a sanitized JSON result. Exit 0 means the declarations
are complete; 1 means valid declarations have blockers; 2 means malformed,
changed, missing or unreadable evidence. No exit code grants dispatch authority.
Temporary-file tests on the affected Windows sandbox require the scoped approved
execution context; do not change ACLs or trust settings.

## Manifest contract

The schema is `application-study-freeze-v2`. Unknown fields are refused so a
misspelled gate cannot silently disappear. All evidence bindings contain exactly
`path` (relative forward-slash local path) and `sha256` (64 lowercase hexadecimal
characters). Files are limited to 2 MiB. Traversal, absolute paths, symbolic links
and junctions are refused. Keep the evidence root private and unchanged during
audit; this read-only checker does not implement an adversarial filesystem lock
or certify the truth of signed-looking receipts.

Required top-level fields:

| Field | Contract |
|---|---|
| `study_id` | Nonempty safe identifier |
| `protocol`, `inputs`, `expected_key`, `codebook`, `order`, `runtime` | Evidence bindings; input, answer key and codebook are separate files |
| `controls` | Evaluation date `2026-10-20`; material cases V01–V06; no-material cases V07–V12; scored stage `first_native_completed_pre_human`; resume pages `[1,2]`; separate native page compliance; no automatic retries; pause unknown outcomes; retain first drafts |
| `products` | Exactly two distinct IDs, each with exact 40-character commit, settings binding, native proof binding and `full_packet_cap_usd` |
| `reviewers` | Two distinct independent IDs, distinct adjudicator ID, identity-withheld flag and calibration binding |
| `budget` | Pricing binding, four product/stage rows, paid-scoring reservation, contingency, total cap and accounting basis |
| `funding` | Explicit pending/approved/declined state, approval reference and approved frozen-content digest |

Identifiers allow letters, digits, periods, underscores and hyphens, at most 120
characters. They must never contain email addresses, credentials or personal IDs.

Native proof schema `native-mechanics-proof-v2` binds `product_id`,
`product_commit`, `settings_sha256` and `runtime_sha256`, with integer
`provider_calls: 0`, `terminal: true`, and unique `completed_steps` containing
review, resume_pdf_delivery, cover_pdf_delivery, extraction, layout,
every_page_visual, native_recordkeeping and unknown_outcome. A product with no
native recordkeeping must document that disposition in its reviewed proof; do
not invent an application feature. Raw logs/download hashes and page inspection
receipts remain review evidence behind these declarations, not self-certified
by this schema. A scripted provider proves mechanics only, never output quality.

Calibration schema `claim-calibration-v2` binds `codebook_sha256`, ordered
`reviewer_ids` and `passed: true`. The checker cannot judge whether the reviewers
actually calibrated independently. Keep calibration originals and adjudication.

Pricing schema `verified-study-pricing-v2` records model, positive decimal
input/output prices per million, `verified_utc` ending in Z, and HTTPS `source`.
The checker validates structure only. Reverify provider/model/settings, cache,
thinking and tool categories before approving the actual request estimator.

Each budget row contains `product_id`, `stage` (fit/materials), `count` (12/6),
positive decimal `expected_each_usd`, `reservation_each_usd` at least expectation,
`full_native_workflow: true`, and positive integer `max_requests_each` at most 100.
These are whole-stage estimates including all native turns; they are not
per-request estimates. Six fit stages share the same packet budget as their
material stage. Per-product full-packet caps must cover fit plus material reserve.
The sum of all 36 stage reserves plus paid scoring and contingency must fit the
shared total. Native review cannot get a second uncounted budget. Financial
execution tooling must independently enforce the declared request and packet
limits; this checker performs no enforcement or provider token accounting.

Amounts use decimal strings with at most six fractional places. Accounting basis
is `approved_conservative_estimate` or `verified_upper_bound`; either remains a
claim requiring evidence and operator review, not something the checker proves.

## Approval binding

`frozen_content_sha256` is SHA-256 of the complete manifest **excluding funding**,
serialized with Python `json.dumps(sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` as UTF-8. This avoids a circular self-hash. A pending audit
returns that digest. Record the real approval reference and that exact digest
only after authorized approval. Changing any other manifest field makes the
funding binding stale. Hashes bind bytes, not a human identity or permission.

The tests deliberately use fabricated proof and pricing data to exercise failure
controls. They are not study readiness receipts and must never be used for an
actual candidate freeze. No paid, deployment, merge or participant action follows
from either tests or this document.
