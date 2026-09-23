# Fifteen-employer discovery catalog

This candidate expands the approved catalog from three employers to fifteen.
Candidate build: `20260923.74`. Staging remains on `.73`; this expansion awaits
exact-commit deployment and live acceptance before GitHub publication.
It broadens the kinds of work searched; it does not establish comprehensive
market coverage or beta readiness. Serper remains disabled until renewed
explicit approval and a reviewed enabling change. Manual-import employer/site
reporting remains in the hybrid-discovery plan, not this implementation.

| Employer | Public board | Coverage added |
| --- | --- | --- |
| Tekton | Lever `tekton` | Tools, manufacturing and operations |
| CLEAR | Greenhouse `clear` | Identity, customer service and technology |
| Coder | Ashby `coder` | Software and developer tools |
| Spring Health | Greenhouse `springhealth66` | Mental health and care operations |
| Sotheby's | Greenhouse `sothebys` | Arts, auctions and client services |
| GiveDirectly | Greenhouse `givedirectly` | Nonprofit programs and field operations |
| Rocketship Public Schools | Lever `rocketship` | Education and school operations |
| Welo Global | Lever `weloglobal` | Language, localization and freelance work |
| Bosch | SmartRecruiters `BoschGroup` | Industrial engineering and skilled trades |
| Accor | SmartRecruiters `AccorHotel` | Hospitality and food service |
| Eurofins | SmartRecruiters `Eurofins` | Laboratories and life sciences |
| H&M Group | SmartRecruiters `HMGroup` | Retail, fashion and logistics |
| AECOM | SmartRecruiters `AECOM2` | Infrastructure and construction |
| Northwestern Medicine | SmartRecruiters `NorthwesternMedicine` | Hospital and allied health work |
| SpaceX | Greenhouse `spacex` | Aerospace, production and technicians |

## Acquisition and cost boundaries

The feed phase allows at most 45 public requests in 90 seconds, with each
request bounded by 12 seconds and the remaining phase deadline. Employers get
their first page before second pages are requested. Slow or unavailable sources
can still consume the phase deadline; missing pages remain explicitly partial.
Candidate selection alternates between employers so the existing 25-posting
per-provider processing cap does not automatically favor the first employer.

Lever pages contain at most 20 postings, with three pages per employer.
SmartRecruiters requests only PUBLIC listings, using up to three bounded role
terms and two 100-posting pages per term. Location filtering remains local.
The smaller providers retain a 1 MiB response limit; SpaceX alone permits a
4 MiB response and up to 5,000 rows. Reaching a limit is not a no-jobs finding.
Job details still require independent validation before matching/saving.

Only public, unfiltered feed pages are reused in the worker process for up to
five minutes, with a 32-entry / 16 MiB serialized-payload ceiling. Search terms,
resumes, credentials and SmartRecruiters query results are not retained in that
shared cache. Cache hits avoid public feed requests; they do not avoid later
posting validation or AI scoring. Restarting the worker discards the cache.
This is not a durable, independently scheduled employer inventory.

The larger catalog can increase free provider requests and eligible AI-scoring
work. It makes no Serper calls. Existing URL deduplication, known-job filtering,
owner isolation, cancellation and bounded scoring remain in place. The feed
deadline is not a deadline over legacy search plus posting inspection/scoring.

## Acceptance limits

Remote listings can remain country/state restricted. Global employer coverage
does not imply worldwide eligibility. Contract/freelance roles require their
actual posting terms; a sampled public URL does not establish a current vacancy.
Government, universities, banking, agriculture, transport and local employers
remain gaps, as does adequate depth within each included market.

`probe_discovery.py` checks one page per employer, using Engineer for
SmartRecruiters. Its optional detail check inspects at most one posting per
employer. It reports page completeness and never declares beta ready. Repeated
role/location coverage, deployed search/save behavior and attended cancellation
acceptance remain separate gates.

No database migration, grant, recovery binding or provider credential is added.
The active schema remains `20260907_0014`.

## September 23 verification

- Combined source, reporting, browser cancellation and required regression
  suite: 431 tests, two existing skips, no failures. Syntax checks passed.
- Actual loopback version/dashboard checks passed; the temporary server stopped
  and its synthetic database was removed. No real profile or credential used.
- First-page checks from WEB at 13:39:36 UTC: all 15 feeds passed URL/employer
  validation; both impossible-role and impossible-location controls returned
  zero for every feed. Fifteen public feed requests; no Serper or AI calls.
- A separate read-only sample beginning 13:46:33 UTC repeated all 15 first-page
  checks and inspected one job per employer: all 15 detail samples were usable.
  Feed requests: 15. Detail-parser requests are additional and not included in
  that counter. No matching, saves, profile access or activation performed.
- SpaceX supplied 2,550 rows in the first observation and 2,549 in the second;
  this is a changing source snapshot, not a fixed acceptance count. Rocketship,
  Welo, Bosch, Accor, Eurofins and AECOM first pages remained explicitly partial.
- An independent source review found no actionable defect. These short-interval
  observations do not establish day-to-day availability or broad relevance.

SmartRecruiters pagination/search parameters follow the
[public posting-list contract](https://developers.smartrecruiters.com/reference/v1listpostings).
Its [endpoint examples](https://developers.smartrecruiters.com/docs/endpoints)
distinguish the API reference from the human posting URL. The numeric human URL
constructed from the bound employer/posting ID passed the actual detail parser
for all six SmartRecruiters employers.
