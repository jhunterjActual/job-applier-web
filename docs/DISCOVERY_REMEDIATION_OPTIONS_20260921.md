# Discovery blocker investigation — September 21, 2026

## Recommendation

Preserve the goal: relevant, current, actionable job postings with honest results
and bounded latency/cost. Do not treat increasing the count of arbitrary links as
a repair. Keep the prepared reporting fix, then evaluate a managed search API to
preserve broad keyword discovery. Add direct public ATS feeds as a complementary
source for known employers. A feeds-only or manual-import beta is feasible only
as an explicit narrower product scope.

No runtime code was changed during this investigation. No account was created,
credential accessed, paid request made, service changed, or release deployed.
SEARCH-001 remains OPEN.

## Fresh evidence and diagnosis

Twelve bounded public requests ran from the application WEB host between 16:55
and 16:59 UTC: five search requests, four feed requests, and three feed/detail
requests. Each had a 12-second timeout, 1 MiB response cap and no retry. Exact
canonicalization, provider recognition, posting-pattern and search-parser helpers
were extracted from the local candidate by AST; the application itself was not
imported. Source SHA-256:
`a4d9fd024bd0254837557a9ceb2a54564bcfc676b3fa4aa7f99fb8fb8146a43e`.

| Surface | Observation | What it establishes |
| --- | --- | --- |
| Yahoo | HTTP 500 for generic software-engineer ATS query | Current source request fails before discovery |
| Bing | Broad domain, exact host, quoted title and Greenhouse host variants each returned HTTP 200, ten organic result blocks, zero decoded provider links and zero accepted postings | Not merely missed Base64 redirect links; returned results are unsuitable for the requested ATS discovery |
| Bing result hosts | Repeated computer-science/reference sites, including computerscience.org, geeksforgeeks.org, Coursera and Wikipedia | Observable loss of site-filter relevance; exact upstream cause remains unknown |
| Lever public feed / Tekton | First five rows gave five valid posting URLs; bounded larger page returned 11 rows, including one software-title posting with description | Published company-level discovery works on this sample |
| Greenhouse / CLEAR | 37 rows, 37 accepted posting URLs; 11 software-title rows | Published board feed works on this sample |
| Ashby / Coder | 31 listed rows, 31 accepted posting URLs; seven software-title rows | Published board feed works on this sample |
| SmartRecruiters sample board | Public list returned one row; detail returned active posting, title, description and accepted posting URL | Adapter must follow the bounded API detail contract, not treat list `ref` as the job URL |

The initial three feed samples produced 73 valid candidate URLs, not 73 matches
for a user. These small, selected samples demonstrate feasibility, not geographic
coverage, broad relevance, longevity, or end-to-end scoring/saving acceptance.
All responses fit the byte limit. No raw responses were retained. Earlier
DuckDuckGo probes returned some Lever links followed by HTTP 202 challenges on
three other ATS queries; no new attempts to bypass those challenges were made.

Evidence and reproducible probe are in the root workspace under
`artifacts/discovery-investigation-20260921/`. Only aggregate counts and boolean
results are retained.

Three separate problems should remain distinct:

1. **Acquisition reliability:** Yahoo fails and Bing returns unusable responses.
   Host connectivity is not universally broken: the public ATS APIs work. We have
   not established whether Bing behavior is query relaxation, anti-automation,
   regional behavior or another upstream cause; do not label it a proven IP ban.
2. **Outcome reporting:** the deployed pipeline can call an empty candidate list
   success; the local repair classifies outcomes and preserves safe diagnostics.
3. **Request planning/relevance:** current query count grows with keywords ×
   locations × four ATS domains. The 25-candidate/provider cap does not bound
   unsuccessful requests. Cached keyword count lacks the generated-keyword cap;
   Bing has no circuit breaker. Failed/missing AI keyword extraction silently
   defaults to Software Engineer, which can be irrelevant to the actual user.

## Remediation choices

| Option | Fit | Main work and limitations |
| --- | --- | --- |
| Managed web-search API | Closest replacement for current broad keyword/location search within the four supported ATS platforms | Evaluate actual posting recall, terms, quotas and storage; add protected key configuration, budgets and source-aware errors. Indexed links can still be stale. |
| Public ATS feeds with employer catalog | Strongest live feasibility evidence without a new paid search service | Maintain reviewed employer/board IDs; fetch each board once per refresh, filter title/location locally, handle pagination/removals. Coverage is limited to the selected catalog. |
| Hybrid API + public feeds | Best long-term direction if broader coverage is required | Search finds additional postings/boards; direct feeds refresh known employers. Deduplicate and respect each source's permitted retention. More work than a single adapter. |
| Manual-import beta | Existing URL preview, editable fields and save-without-AI path provide a fallback | Explicitly narrow beta scope; label/disable unavailable automated and scheduled discovery. This does not satisfy the existing automated-discovery gate without a product-scope decision. |
| More anonymous HTML search fallbacks | Low implementation effort | Already showed unreliable responses/challenges. Not recommended as the beta dependency; additional retries add latency without establishing coverage. |

## Candidate services and current constraints

Official pages were checked September 21; prices and terms can change. None of
these authenticated services was live-tested in this investigation.

| Service | Published entry pricing | Assessment |
| --- | --- | --- |
| [Serper](https://serper.dev/) | 2,500 trial queries; $50 top-up for 50,000 credits, expiring after six months, excluding tax | Low-cost candidate for a bounded broad-search trial. Vendor-managed Google result acquisition, not Google's own search API; reliability and permitted saved-result use still need validation. |
| [Brave Search API](https://brave.com/search/api/) | $5 per 1,000 search requests; $5 monthly credit | Independent index and documented structured search. Storage rights are a material open gate for this saved-job product. |
| [SerpApi](https://serpapi.com/pricing) | 250 searches/month free; $25/month for 1,000, $75 for 5,000 | Another managed-result candidate, with higher entry cost per query. Trial results are needed; marketing claims are not acceptance evidence. |
| [Adzuna](https://developer.adzuna.com/overview) | Registration required; default published limit 2,500 requests/month | Job-specific search, but a larger product integration: attribution, usage/retention rules and redirect destinations must fit the workflow. Not a drop-in replacement for four-ATS URL discovery. |

Brave documents `site:`/subdomain queries, but also labels search operators
experimental; keep local URL filtering and validate actual recall.
[Operator documentation](https://api-dashboard.search.brave.com/documentation/resources/search-operators).
Its [terms](https://api-dashboard.search.brave.com/documentation/resources/terms-of-service)
restrict non-transient storage of search results; its product FAQ requires a plan
with explicit storage rights. Confirm coverage for durable job URLs and records
rather than assuming that independently fetching an ATS page resolves it.

Adzuna's [published terms](https://developer.adzuna.com/docs/terms_of_service)
require listing attribution and place conditions on commercial use and sourcing.
Evaluate those conditions against the intended import/matching flow before any
integration; this report does not decide contractual permission.

Do not start a new integration with the retired classic Bing Search APIs:
[Microsoft states retirement on August 11, 2025](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement).
Google's [Custom Search JSON API](https://developers.google.com/custom-search/v1/overview)
is closed to new customers, with existing customers required to transition by
January 1, 2027. Neither is a durable new-service choice here.

### Cost sizing, not a quote

Three keywords × two locations × four ATS domains = 24 search requests before
pagination or retries. Ten users, two runs daily, 30 days = 14,400 requests.
At published rates this is roughly $72 of Brave usage before its $5 credit, or
$14.40 of Serper credits consumed (but the purchase minimum is $50). SerpApi's
15,000-search plan is $150/month. These examples exclude AI matching, storage,
tax, detail fetches and extra pages/retries. Consolidating queries may reduce
cost but needs coverage tests; a compound query is not assumed equivalent.

## Feed implementation contracts

- [Lever](https://github.com/lever/postings-api): published jobs are namespaced by
  employer site; list endpoint supports pagination. Existing code already uses
  its single-posting API. A board registry is still needed.
- [Greenhouse](https://docs.greenhouse.io/job-board.html): public GET endpoints
  need no authentication; list by board token, optionally including descriptions.
  Some boards return custom career-site URLs. Retain current rejection until
  custom-domain support is deliberately specified and tested.
- [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api): enumerate
  one board; retain only `isListed=true`, use supplied location/workplace fields
  and `jobUrl`. Unlisted-but-directly-accessible jobs must not enter discovery.
- [SmartRecruiters](https://developers.smartrecruiters.com/docs/endpoints):
  public company posting list and detail endpoints; pagination/filters available.
  Use `destination=PUBLIC`; never request internal postings. Public unauthenticated
  GETs worked in this sample; validate detail `postingUrl` and `active` separately
  from its API `ref`. See [authentication](https://developers.smartrecruiters.com/docs/authentication)
  and [object schema](https://developers.smartrecruiters.com/docs/objects).

Feeds require caps and expiry just like search APIs. Do not fetch a whole board
again for each user/keyword/location, silently truncate a large feed as complete,
or interpret a temporary fetch failure as removal of all jobs. Employer catalog
selection should reflect intended beta roles and regions, not whichever sample
boards were convenient for this probe.

## Concrete implementation and acceptance plan

1. Keep the local truthful-outcome repair; add request-attempt/time budgets,
   cached/generated keyword and location limits, per-source circuit breakers,
   cancellation between requests, and 429/Retry-After handling. Remove silent
   unrelated keyword substitution. Treat incomplete discovery as incomplete.
2. Add a small acquisition adapter returning normalized candidates and typed
   source outcomes at `backend/searcher.py`'s discovery loop. Preserve downstream
   canonicalization, public-address/redirect checks, suppression, owner-scoped
   storage and AI scoring. Separate search-engine health from ATS-detail health.
3. For hosted beta, prefer one protected service credential with hard tenant/run
   quotas and a global spending ceiling. Per-user search keys are possible but
   require new encrypted-key/profile plumbing; existing keys are not a drop-in
   search setting. Do not put credentials in URLs, user-visible errors or logs.
4. Before vendor commitment, run the same generic matrix against two candidate
   APIs: five role families × three location/remote conditions × four ATS scopes
   (60 requests/provider/window before bounded detail checks). Repeat across
   three separate windows. Record valid unique postings, title/location relevance,
   fetchable descriptions, stale-link rate, failure rate, latency and cost. Include
   known-posting controls from feeds and a genuine no-result control. Do not use
   raw search-result count or HTTP 200 as the release criterion.
5. Proposed beta gate: all known-positive controls produce an eligible posting;
   sampled candidates remain within permitted domains and yield usable details;
   no source outage becomes success; limits and cancellation work; target role/
   geographic cells meet an explicitly agreed coverage floor. This small matrix
   is acceptance evidence, not an uptime guarantee or proof of exhaustive recall.
6. Regression coverage: malformed/oversized/empty/partial data, 401/403/429/5xx,
   credential isolation, private redirects, budgets, pagination, duplicates,
   suppressed/stale jobs, failed saves, worker lease loss, scheduled searches and
   persistent UI outcomes. Then deploy/live-verify the exact selected repair
   before publishing its PR. This investigation does not authorize activation.

Source anchors: `backend/searcher.py` functions `search_yahoo_jobs`,
`extract_search_keywords_from_resume`, `run_job_search_and_matching`,
`inspect_job_posting`; `backend/provider_secrets.py` provider allowlist;
`backend/search_run_execution.py` shared scheduled/manual worker execution;
`backend/app.py` manual job preview/import endpoints.

## Manual-import employer and site evidence

Requested September 21: use successful manual imports to record employers and
sites for reporting and prioritizing new discovery coverage. This requirement
is planned, not implemented. It complements the hybrid employer catalog.

- Record the reviewed employer display name, conservatively normalized employer
  key, normalized source hostname, recognized ATS provider (if any), and a
  verified employer board identifier when safely available. Keep employer and
  hosting platform distinct: two employers on Lever are two employer records;
  one employer using two sites has two source associations. Do not infer a
  corporate domain from an employer name or automatically merge similar names.
- Store first/last successful-import dates and a successful-import count per
  authenticated owner and employer/site association. Record only once when a
  new manual job is committed, in the same transaction. Preview, rejected,
  duplicate and suppressed imports do not increment counts; later AI failure or
  cancellation after saving does not remove the observation. Counts describe
  imports, not unique people, demand or application activity.
- The source record contains no position title, description, posting URL or ID,
  URL query/fragment, resume, score, search terms or credentials. For recognized
  ATS URLs, extract only the validated employer board token, never a job token.
  For other sites, keep the hostname; do not guess a board from arbitrary paths
  or collapse branded subdomains to a shared platform domain.
- Report employers and sites by count and recency, with catalog coverage states
  such as not reviewed, feed candidate, feed enabled and manual-only. Review
  feed availability, relevance, freshness and usage rights before incorporation;
  a manual import must not automatically enable a crawler or feed subscription.
- Keep user records private and owner-scoped. Provide a separate reviewable
  employer/site coverage report for deliberate sharing with the maintainer.
  Existing source-health reports explicitly exclude employers and URLs; retain
  that contract. Central cross-user usage aggregation needs an explicit product
  and privacy design, rather than silently exposing owner records.
- Include these records in user export and account erasure, and offer clearing
  source history. Proposed retention: while the account is active, until cleared
  or erased; explain that aggregate source history survives individual job
  deletion. Confirm this behavior in the UI before rollout. Do not reconstruct
  historical manual-import counts from jobs whose import origin is unverified.

Acceptance: additive SQLite/PostgreSQL migrations; owner isolation and erasure;
atomic save/count behavior; retries and concurrent imports; distinct employers
on a shared ATS; the same employer on multiple hosts; conservative name handling;
safe board-token extraction; report escaping; and proof that position-level or
private data cannot appear in source-history/report payloads. Verify both the
manual save flow and reporting UI before deployment acceptance.

## Verification and retention

Investigation-only artifacts: this report, bounded probe script and aggregate
JSON receipt. Runtime tests/builds not applicable because application bytes are
unchanged from the previous turn; no existing local test receipt is promoted to
live acceptance. No remote files, caches, packages or containers were created.
Keep these small artifacts with the beta candidate; owner CareerTrellis operator,
review when a discovery provider/catalog is selected or SEARCH-001 is closed.
The probe and aggregate receipt total 11,542 bytes; this report is approximately
18 KB. No disposable remote artifacts require cleanup. JSON readback succeeded;
candidate diff whitespace checks passed and both staging areas are empty. Root
whitespace checking reports pre-existing AGENTS.md changes, preserved untouched.
