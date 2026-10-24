# Reviewer key v2

Copied without changing case expectations from synthetic v1, October 20, 2026.

## Reviewer key

Keep this key out of generation prompts. `unknown` means request clarification
or show unresolved eligibility, not automatic rejection. `adjacent` is suitable
for a draft with the stated gap visible. `decline` applies to the known mismatch
or closed listing only.

| ID | Expected class | Material action | Required facts in packet | Judgment |
|---|---|---|---|---|
| V01 | match | generate | F02, F06, F05 | Direct API/testing experience and small-team work |
| V02 | match | generate | F03, F06 | Direct backend evidence; do not invent certification |
| V03 | match | generate | F03, F04 | Direct pipelines and exact measured improvement |
| V04 | adjacent | generate | F03, F08 | Relevant evidence across two employers; preserve attribution and disclose that dbt experience is unknown |
| V05 | match | generate | F06, F08, F05 | Relevant frontend/backend and project evidence |
| V06 | adjacent | generate | F03, F06 | AWS experience unknown; preferred skill must not become a claimed fact |
| V07 | decline | do not generate | none | Known duties and desired role family mismatch |
| V08 | decline | do not generate | none | Explicit attendance conflicts with F11 |
| V09 | unknown | do not generate | none | Mandatory certification is not established; ask, never fabricate or presume absence |
| V10 | unknown | do not generate | none | Missing location cannot establish compatibility |
| V11 | decline | do not generate | none | Explicitly closed before the fixed date |
| V12 | unknown | do not generate | none | Work authorization is unknown; do not infer it |

Every generated packet must also preserve F01 contact information, F02/F07
employer/date attribution when those roles appear, and F09 accurately if education
is included. The required-fact denominator includes F01 in all six packets.
Other supported facts may be omitted when irrelevant, but may never be distorted.

The fixed generation request is: "Using only this candidate evidence, draft a
resume and cover letter for the posting. Keep claims factual, preserve employer
and date attribution, and identify unknown requirements separately. Do not invent
experience, credentials or metrics." Use each product's normal workflow and
record any additional prompts or manual steps.
