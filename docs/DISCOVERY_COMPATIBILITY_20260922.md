# Interim discovery and recovery compatibility — September 22, 2026

The local candidate resolves the database/recovery incompatibility for interim
direct-feed and key-free discovery by deferring the unused Serper quota migration.
It does not establish future Serper migration compatibility or beta readiness.
No staging migration, deployment, restart, key installation, paid request,
owner re-enrollment, credential rotation, commit, push or PR was performed.

## Current release contract

- Active Alembic head stays exactly `20260907_0014`. Existing schema, SQLite
  initialization, runtime grants and protected recovery/identity guards are
  unchanged. Search workers no longer reference the deferred quota function.
- The quota migration and reference patch are retained under
  [deferred-serper](deferred-serper/README.md), outside the active migration path.
  The reference patch includes outcome changes still in use; do not apply it
  wholesale. This future proposal is not deployable as-is.
- `CAREERTRELLIS_DISCOVERY_MODE=hybrid_feeds` combines Tekton/CLEAR/Coder feeds
  with existing Yahoo/Bing attempts, using common URL validation, deduplication,
  per-provider candidate caps, matching, cancellation and owner-scoped save.
  Default remains `legacy`; this is an undeployed candidate, not live behavior.
- A failed source preserves usable results from another and yields partial
  reporting. An intentionally disabled Serper source adds no failure diagnostics.
  Feeds run once per search with their own request/time limits; subsequent legacy
  calls retain their existing bounds. There is no new 12-request/90-second cap
  over the combined flow and no shared feed cache.
- `hybrid_serper` configuration is rejected, the credential accessor returns
  empty, Serper transport is blocked before quota reservation/network, and the
  local probe rejects `--serper` before a credential prompt. An environment key
  cannot reactivate Serper. Renewed explicit approval and reviewed code are needed.

## Evidence

- PostgreSQL 18: 58 integration tests passed, including actual upgrade/repeated
  upgrade and grants reapplication. Head is `0014`; quota tables/function are
  absent. Tenant isolation and existing account-erasure tests passed.
- Protected initial migration: 11 tests passed on the disposable database,
  including failure/recovery paths. No historical receipt or binding widened.
- Initial regression run: 439 tests passed with two existing skips. Later
  source tests additionally prove production default suspension, rejection of
  configured Serper use, feeds-only diagnostics, cross-source preservation,
  partial outcomes and deduplicated saving. Final results recorded below.
- Actual loopback app in `hybrid_feeds` mode returned HTTP 200 for `/api/version`
  and `/`, then stopped cleanly. Provider requests: zero. Changed search/save
  flow exercised with synthetic sources; completion UI exercised offline.
- Independent source review found no blocking issue after deferral. This is
  local evidence; live relevance/repeated coverage and deployed acceptance remain.

## Remaining gates and retained artifacts

SEARCH-001 remains open. Review the exact interim release, then deploy and verify
its live feed/key-free search/save/cancel flow before publication. Keep Serper
disabled throughout. The prior search mechanism's reliability limits remain.
No database migration is needed for this interim release; future quota work needs
the separately reviewed migration/recovery plan in the deferred proposal.

The old root-only `/run/careertrellis-discovery-trial-20260921` bundle on WEB
(44,955 bytes) was not changed this turn. It contains the historical enabled
prompt, but no stored key; this task configured no automated invocation. Its use remains
explicitly prohibited pending renewed approval. Local code changes do not alter
those remote bytes. Owner: CareerTrellis operator; retain/review by September 28
or reconcile exact-target removal during approved remote cleanup.

Retain this candidate, its small source-only deferred proposal, historical trial
receipt and aggregate verification evidence until SEARCH-001 is resolved or
superseded. Owner: CareerTrellis operator; review by September 28. Task-owned
temporary database/cache cleanup and final check results are recorded below.

Final verification: 444 regression tests passed with two existing skips; all 23
source/suspension tests passed. Full backend compileall passed using ordinary
cache paths, and candidate whitespace checks passed. Nothing is staged; no
protected file is staged. Earlier fixture-only counter errors were corrected
before the successful full rerun. The initial redirected compile cache hit a
Windows path-length problem; the normal-path rerun passed.

Cleanup: verified task-owned container
`careertrellis-compatibility-20260922` (ID
`b6a4be012ff958227042838eaca3a6df306b48d8727059fab13e510d7a1a0e5c`)
had no volume mounts and only a 512 MiB tmpfs. It was stopped and removed after
tests; absence was verified and the existing PostgreSQL image preserved.
Seven exact task-created local runtime/cache directories were removed after
workspace/link checks; 10,043,350 file bytes reclaimed. No remote storage changed.
Operational friction is recorded in the root
`artifacts/beta-search-friction-20260921.md` pending shared-log incorporation.
