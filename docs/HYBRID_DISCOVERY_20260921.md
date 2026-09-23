# Hybrid discovery candidate — September 21, 2026

**Superseded implementation record:** the September 22 interim candidate now
uses `hybrid_feeds` (direct feeds plus legacy search), keeps schema `0014`, and
blocks Serper in code. Its quota migration and related runtime changes are
deferred outside the active migration chain. See
[current compatibility receipt](DISCOVERY_COMPATIBILITY_20260922.md).
The sections below preserve the earlier design, tests, trial receipt and
suspension history; they are not current activation instructions or authority
to restore the deferred quota migration. The former 12-request/90-second managed
acquisition budget is not a total budget for the new feeds-plus-legacy flow.

Status: implemented locally and regression-tested; not deployed or activated.
SEARCH-001 remains open. The operator selected Serper and the initial employer
catalog Tekton (Lever), CLEAR (Greenhouse), and Coder (Ashby). Manual-import
employer/site history remains plan-only as requested.

## Behavior and limits

`CAREERTRELLIS_DISCOVERY_MODE=hybrid_serper` selects direct feeds plus Serper.
The default remains `legacy` until the reviewed deployment/configuration step.
Use `CAREERTRELLIS_SERPER_API_KEY` only in the protected search-worker environment;
never in a URL, checked-in file, browser, command argument or user result.
The erasure worker rejects that credential. Existing user AI keys still generate
keywords and score verified postings; they are not used to invent live job URLs.

Each feed is fetched once per run, with Lever pagination capped at three pages.
Malformed, oversized, redirected, unlisted, wrong-board or incomplete responses
are rejected. Feed candidates are filtered by title and location before the
existing canonicalization, public-network inspection, suppression, owner-scoped
matching and save flow. There is no cross-run feed cache yet.

Planning deduplicates/caps keywords at three and locations at two, each120chars.
No failed keyword extraction silently substitutes Software Engineer. The hybrid
acquisition phase permits12HTTP attempts total, at most6Serper attempts,1MiB per
response and a90-second deadline with at most12seconds per socket operation.
Checkpoints run between reads/requests; a blocked read can overrun the deadline
by its socket timeout. Serper redirects are forbidden, including same-origin
redirects. A source error opens the managed-source circuit for that run;429 is
not retried. Partial coverage is reported separately from a valid empty result.

One Serper query combines the four ATS domains per keyword/location pair. For
three keywords and two locations, that is6managed requests instead of24separate
domain requests. Equivalent recall has NOT been established; ten-result pages
are marked incomplete. Adding direct feeds alone does not reduce those six
requests in this candidate. Shared refresh caching and feed-aware request
suppression remain further optimizations requiring coverage tests.

## Durable spending boundary

Migration `20260921_0015` adds private forced-RLS quota tables and a worker-only
reservation function. Global and per-owner daily limits both default to zero.
The administrator-controlled policy is bounded at10,000global and1,000owner
requests/day; these are validation ceilings, not enabled budgets or approval to
spend. Choose explicit conservative limits before activation.

Reservations validate tenant, live claim, owner state and lease after lock waits,
increment both counters atomically and commit before HTTP. Failed/ambiguous
requests consume their reservation. Resets, refunds and retries do not bypass
the boundary. Only UTC day/count/owner are stored, never query, employer, URL or
key. Owner rows cascade on account deletion; global totals remain anonymous.
Successful reservations prune rows older than35days; without new activity, older
rows remain until a separately approved maintenance/erasure operation. SQLite
local mode uses the corresponding short immediate transaction.

## Verification

- Required P0/object suites plus search/UI regressions:428tests passed,2existing
  skips. Later57focused tests passed after the hybrid save-flow addition;
  final16adapter/cancellation checks passed after the final correction.
- PostgreSQL18 disposable integration:58tests passed, including fresh migration,
  repeated upgrade, forced RLS, grants reapplication, role denial, owner/global
  concurrent limits, stale leases and the existing account-erasure suite.
- Python compileall and candidate diff whitespace checks passed.
- Actual loopback server startup returned HTTP200 at `/api/version` and `/`,
  then shut down gracefully. Offline Chromium tested persistent completion UI;
  synthetic hybrid acquisition exercised existing matching and owner-scoped save.
- Live public adapter probe at21:02:14UTC: Tekton11eligible URLs, CLEAR37,
  Coder32;3requests,0Serper requests. No profile, credential or stored job read.
  These are source URLs, not confirmed user matches or longitudinal reliability.

`backend/probe_discovery.py` runs public feed checks. Its `--serper` option uses
the existing protected controlling-terminal reader and makes at most6generic
requests, including positive and negative URL controls. Run it only from the
operator's visible secure Linux terminal, never an agent/background prompt.
Its receipt explicitly says `beta_ready=false`: it does not prove description
quality, geographic relevance, longitudinal reliability or full end-to-end use.
Do not print the key. The key remains in process memory only for this trial.

## Remaining release gates

1. The operator-supplied September 22 receipt passes the bounded generic Serper
   trial (see below). Complete the agreed repeated role/location matrix and
   detail checks. Preserve aggregate
   evidence and decide a useful coverage floor; source HTTP200 is insufficient.
2. Review quota schema compatibility before deployment. Current protected
   noninitial migration requires an identity activation plan, whose schema
   allowlist excludes the quota-only head. Do not add this head to that allowlist:
   it does not supply identity capabilities. Prepare an exact-source/exact-target
   additive maintenance path, or reconcile lineage behind the selected identity
   release, preserving all maintenance fences and accepted receipts.
3. Existing owner-recovery and reusable transport/coordinator checks require
   schema0014. Any additive-schema compatibility change must retain the original
   enrolled credential's0014binding preimage. Do not rotate/re-enroll the owner,
   alter historical initial-install receipts or silently widen identity schemas.
4. Select daily budgets, configure only the worker credential, deploy the exact
   reviewed candidate and prove the live search/save/cancel flows before PR
   publication. No paid calls, provider account changes, staging restart,
   migration, commit, push or PR occurred in this continuation.

Provider contracts checked: [Serper](https://serper.dev/) and its
[terms](https://serper.dev/terms), [Lever](https://github.com/lever/postings-api),
[Greenhouse](https://docs.greenhouse.io/job-board.html), and
[Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api).
The authenticated Serper transport trial passed in the operator-supplied receipt
below; sustained reliability and useful coverage remain unproved.

## September 22 operator trial receipt

The operator supplied [the aggregate receipt](SERPER_TRIAL_20260922.json), observed
at 2026-09-22 14:11:47 UTC. All three feeds passed again with 11, 37 and 32 eligible
URLs respectively. All five positive Serper controls passed, returning 10, 6, 10,
9 and 10 eligible URLs; the negative control returned zero. These counts are per
case and must not be interpreted as 45 unique or relevant jobs. Six Serper calls
and three feed calls were reported, within the trial bound. No credential or raw
response was supplied or retained in this record.

This closes only the authenticated transport and URL-control trial gate. It is
operator-reported evidence, not an independently witnessed execution. The receipt
explicitly retains `beta_ready=false`. Repeated coverage/detail validation,
protected migration and recovery compatibility, explicit operating budgets,
worker credential setup, deployment and live application acceptance remain open.
Do not repeat the paid trial solely to reproduce this receipt. The secure command
below is retained as the procedure used, not a request to run it again.

## Serper use suspended by operator — September 22

Following the successful trial, the operator explicitly withdrew authorization
for further Serper API use until they approve it again. Do not run additional
Serper probes, install its key, enable managed discovery or spend Serper requests
without that renewed explicit approval. Readiness or a previously successful
trial is not approval. The completed trial used the key in process memory only;
it did not install it in application or worker configuration. The candidate
defaults to `legacy` discovery and its durable paid-request budgets default to
zero. No provider-side key revocation is claimed or requested.

Use the approved Tekton, CLEAR and Coder direct feeds and attempts through the
prior key-free discovery mechanism during interim work. Feed integration remains
a local candidate, not a deployed capability; the prior mechanism's known
reliability limitations remain open. Any interim implementation or deployment
must keep Serper inactive. Retain the successful receipt without repeating it.

## Historical secure trial procedure — suspended, do not rerun

The reviewed scripts are staged at
`/run/careertrellis-discovery-trial-20260921` on the existing WEB host. Root-only
directory0700, four source files0500,44,955bytes; installed bytes were read back.
The application current pointer still matched release
`e0cd2e31cd5ac101c6cecd9a87c5e5c3562ea7bc`; no application service was changed.
Five native Linux terminal regressions passed: ordinary/balanced paste, rejected
malformed/multiline/oversized input, no echo and signal/terminal restoration.

The completed trial used the following command in the operator's visible SSH
terminal on `hunter@129.212.153.245`. It must not be rerun without renewed explicit
operator approval for Serper use:

```sh
sudo env CAREERTRELLIS_CONFIG_READ_ONLY=1 PYTHONPATH=/opt/careertrellis/staging/releases/e0cd2e31cd5ac101c6cecd9a87c5e5c3562ea7bc/backend /opt/careertrellis/staging/releases/e0cd2e31cd5ac101c6cecd9a87c5e5c3562ea7bc/backend/venv/bin/python -B /run/careertrellis-discovery-trial-20260921/probe_discovery.py --serper
```

Enter the Serper key only at its hidden prompt. This explicitly runs at most six
requests against that account, without installing/storing the key or changing
the worker. Share only the final JSON receipt. A nonzero exit or failed control
is evidence to investigate, not a reason to automatically repeat paid requests.
The agent has not started this credential prompt. If the ephemeral directory is
gone after a reboot, have the reviewed bundle prepared again rather than using
an unrelated path or pasting a key into chat.

Retain the small key-free bundle until this trial completes, or review on
September28, owned by the CareerTrellis operator. Remote removal needs exact-target
review; do not delete other recovery/staging artifacts. Local source remains the
durable copy. Disposable PostgreSQL container
`careertrellis-discovery-quota-20260921` was stopped and removed after verification;
its pre-existing shared image was preserved. Five task-created cache/runtime
directories were removed, and their absence verified. Two failed-test temporary
databases were also removed after exact-content checks:319,488file bytes reclaimed.
No other disk-reclamation claim is made.

Owner: CareerTrellis operator. Retain the source candidate and aggregate evidence
until SEARCH-001 is resolved or superseded; review by September28. Disposable
test database/container and compile caches are task-owned cleanup items. Existing
worktrees, protected application data and remote recovery evidence are preserved.
