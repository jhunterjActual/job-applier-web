# Common factual calibration controls v2

Preparation controls only; do not include these examples or their key in scored
generation prompts. Two reviewers annotate independently, compare segmentation
and classifications, then settle the common rules before scored output. Record
reviewer IDs, original annotations, disagreement and fact-cited disposition.

Evidence: K01 maintained REST APIs; K02 wrote automated tests; K03 candidate reduced
a reporting job from 40 minutes to 25 minutes; K04 refuses relocation; K05 worked at Example
Cedar Labs January 2022–September 2026. Certification status is not supplied.

| Control | Proposition or passage | Expected treatment |
|---|---|---|
| C01 | Maintained REST APIs. | Supported K01 |
| C02 | Maintained REST APIs and wrote automated tests. | Split two supported actions K01/K02 |
| C03 | Wrote automated tests for the REST APIs. | Unsupported relationship; separate action supports do not prove their association |
| C04 | Developed REST APIs. | Unsupported responsibility; maintenance does not entail development |
| C05 | Reduced the reporting job from 40 minutes to 25 minutes. | Supported K03 |
| C06 | Improved batch performance from 40 minutes to 25 minutes. | Metric present; unsupported batch classification means distorted required fact |
| C07 | Has no cloud certification. | Unsupported negative claim; unknown is not absence |
| C08 | Has a cloud certification. | Unsupported positive claim; unknown is not possession |
| C09 | Will relocate. | Contradicted K04 |
| C10 | Example Cedar Labs, January 2021–September 2026. | Employer supported; start-date atom contradicted K05; end date supported |
| C11 | I would welcome the opportunity to discuss this role. | Nonfactual expression of interest, not an employment claim |
| C12 | I always start by timing the job before changing it. | Unsupported historical method, not merely interest or style |
| C13 | That experience reinforced my focus on careful engineering. | Ambiguous self-assessment; retain competing readings and reason |
| C14 | Cover omits dates; resume supplies correct employer and dates. | Packet-level attribution may be satisfied; omission is not contradiction |
| C15 | Packet omits a case-required reporting metric. | Required-evidence omission; do not add an invented-claim atom |

Expected counts are deliberately not pooled into one rate: C06 combines a metric
and unsupported category, C10 contains three propositions, and C13 remains
ambiguous. Freeze a common proposition inventory after calibration. An automated
string test cannot establish semantic reviewer agreement.
