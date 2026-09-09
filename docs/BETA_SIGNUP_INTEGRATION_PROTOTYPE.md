# Isolated real-schema signup integration prototype — 2026-09-07

CareerTrellis is owned by **J. Hunter Johnson LLC**.

## Outcome and boundary

**The isolated synthetic integration slice passed. Public signup is not enabled.**
This implements the bounded follow-up recommended in
`BETA_SIGNUP_INTEGRATION_EVALUATION.md`. It exercises the actual PostgreSQL
account/profile/erasure schema and grants with a fake provider. It does not
install application routes, alter production session formats, add an Alembic
migration, persist real identities, or change pilot staging or WorkOS settings.

The source base is merged `web/main`
`184b34b397dc1fb5458a70e387b76658ac479ad6`, on local branch
`codex/beta-factor-readiness`. These changes remain local and uncommitted.

## Implemented surfaces

| Artifact | Bounded responsibility |
|---|---|
| `backend/signup_prototype/integration.sql` | Private admission ledger, restricted broker/functions, durable exchange claim, atomic ordinary account plus empty profile, retention and coordinated lifecycle wrappers. Refuses any database other than `ct_signup_integration`. |
| `backend/signup_prototype/integration.py` | Encrypted browser-flow and account-bound session envelopes, exact synthetic-provider restriction, once-only exchange authorization, sanitized database failures, fresh-login recovery, refresh/logout semantics. |
| `backend/signup_prototype/integration_fixture.py` | Collision-refusing disposable PostgreSQL setup; actual Alembic head `20260903_0013` applied twice; actual runtime grant script reapplied; real operator and erasure functions exercised through isolated coordination wrappers. |
| `backend/signup_prototype/integration_http.py` | Test-only HTTPS facade: availability, native signup/sign-in form, synthetic redirect/callback, clean result, private-session check. No launcher or main-application import. |
| `backend/test_signup_integration.py` | Database, process, lifecycle, failure, role, HTTP and native Edge acceptance tests. |

Availability counts every retained application account, including owner, disabled
and erasure-pending accounts, plus live current-generation signup reservations.
Login does not reserve capacity and remains available when full or closed.
Unmapped login cannot provision. New signup creates only an ordinary account and
empty profile in one transaction; the real profile repository can perform its
first save. Existing identities do not consume a second account or change roles.

One short database coordination lock serializes participating admission and
lifecycle operations. No provider exchange holds that lock. A durable claim
grants exchange authority once, including across processes; duplicate callbacks
never obtain a new session from a terminal status alone. Late provider results
are fenced by the original claim, expiry, cancellation, generation and policy.

Sessions bind the verified synthetic provider session to the exact local account
ID and realm. Deletion followed by explicit re-registration creates a different
local ID: old and refreshed-old cookies cannot access it. Refresh preserves the
binding and checks subject/session continuity. Verified logout remains possible
after disablement or erasure without tenant access. Mapping outages return
unavailable, not a false claim that the session is invalid.

Erasure acceptance denies access immediately but retains capacity until actual
final deletion. The isolated worker wrappers coordinate claim, object batch,
acknowledgement, renewal, retry and finalization before existing request/catalog
locks. Actual database time fences a lease after lock waits; a finalization that
outlives its lease rolls back. Real generated-object queue tests require deletion
acknowledgements, preserve another tenant's object, and retain the actual erasure
receipt. No real object-store operation occurs.

## Fresh verification receipt

- Dedicated PostgreSQL **18.4**, loopback-only published port, tmpfs database
  storage, no host mounts, synthetic credentials/data only.
- **44 integration tests passed, no skips**, in **8.408 seconds**. Coverage
  includes last-slot races against real operator seeding, parallel reservations,
  same-subject activation, multiprocess claims, failed profile insertion,
  lost response/COMMIT acknowledgement, adapter restart, cancellation, closure,
  cap reduction, observed advisory/catalog lock waits, erasure lifecycle and
  object acknowledgements, session recreation/refresh/logout, retention,
  grant reapplication, negative privileges and forced-RLS isolation.
- Native **Edge over actual loopback HTTPS** completed the form and redirect
  chain, reached the clean result and authenticated using the browser-stored
  secure session cookie. The test exercised an envelope near the 3,500-byte
  value limit and verified the complete Set-Cookie header stayed below 4,096
  bytes. Exact Origin and duplicate/blank-duplicate form/query checks passed.
  Non-loopback browser requests were blocked. The temporary synthetic TLS
  certificate was trusted only by that test context; no OS trust change occurred.
- The isolated facade's `/api/version` and `/` were exercised. The temporary
  HTTPS server stopped cleanly; no listener remained on `127.0.0.1:8766`.
  The production dashboard/server was not started or claimed as live-verified.
- **402 regression tests ran in 29.572 seconds: OK, two existing platform
  skips**. Suites: `test_workos_rehearsal`, `test_account_choice`,
  `test_p0_regressions.py`, `test_object_store.py`, `test_object_import.py`,
  `test_stored_objects.py`. The skips concern POSIX hosted-authority and Linux
  systemd checks on Windows. `python -m compileall -q .` exited zero.
  Existing Starlette/httpx and websockets deprecation warnings were non-fatal.
- Independent bounded lifecycle and application-boundary source reviews were
  reconciled with the implementation. Review corrections included lease expiry
  across lock waits, outage classification, cookie size allowance and blank
  duplicate form parsing. These were retested/read back. This is not a full
  security scan or independent live-provider acceptance.
- Final database inventory showed **zero fixture schemas and zero fixture
  roles**. Exact container `careertrellis-signup-integration-20260907` was stopped
  and its automatic removal verified. Only disposable synthetic data was
  discarded; other containers were not stopped or modified.

## Evidence limits and remaining gates

1. **Production lifecycle wiring is not implemented.** Existing operator,
   erasure and import entry points remain unchanged; original functions remain
   callable without these prototype wrappers. The experiment proves the
   coordinated paths, not production-wide bypass prevention. A later additive
   implementation must cover every writer, enforce lock ordering and privileges,
   and rehearse upgrade/forward-correction with existing tenant data. Import
   maintenance coordination is still a design requirement. The isolated seed
   wrapper requires available admission for a new account, including denial
   while closed; this is not a change to the current pilot operator policy.
2. **No real provider adapter or session integration.** Only the exact built-in
   fake provider is accepted. There is no real WorkOS transport, JWT validation,
   MFA/recovery, email-delivery or Google acceptance here. Bounded blocking test
   hooks demonstrate fencing and responsiveness, not real transport timeouts.
   Real SDK/network deadlines, token-size behavior and cookie transition still
   require their own implementation and acceptance.
3. **Crash tests have precise limits.** The COMMIT-acknowledgement test commits
   successfully and then injects an exception; it does not drop TCP packets
   during COMMIT. Adapter/new-pool restart tests retain the synthetic provider
   instance as external-provider state, not a provider-process restart. Separate
   processes prove durable claim authority, not a complete multi-host service.
   Ambiguous outcomes issue no cookie and never replay the consumed code;
   fresh authenticated login recovers an existing committed account.
4. **Privacy/retention scope is narrower than the protocol-only rehearsal.**
   The ledger contains opaque bindings, deadlines, claim/code digests and a
   nullable local ID, not raw codes, email, PKCE verifiers or provider tokens.
   Synthetic provider state is ephemeral memory; synthetic provider sessions
   are inside encrypted browser cookies. The appropriate receipt is
   `provider_tokens_persisted_in_admission_store:false`, not a claim that the
   whole integrated application retains no session material. Owner-only physical
   purge is tested after both deadlines, but no purge schedule is installed.
5. **Public beta policy and ingress remain pending.** Final cap, reservation
   duration, physical retention schedule, conservative global-generation restart
   UX, abuse/rate limits, accessibility and finished status/recovery copy need
   acceptance. The 10-minute lease, 15-minute flow and sample caps are experiments.
   BasicAuth/public-route boundaries, final provider environment/domain and
   account-specific cost/identity-transition/MFA gates remain unchanged.

## Repeatability

Use a fresh dedicated PostgreSQL 18 container, loopback-only port, tmpfs storage,
no host data mounts and a synthetic password. The database must be named
`ct_signup_integration`; never use a shared server, application database or
recovered backup. The fixture refuses existing schemas/roles and cleans only
objects it created, including partial-setup failure cases.

Set `CAREERTRELLIS_SIGNUP_INTEGRATION_URL` to that synthetic
`postgresql+psycopg://...@127.0.0.1:<port>/ct_signup_integration` target and
`CAREERTRELLIS_CONFIG_READ_ONLY=1`. From `backend`, run the existing Python
environment with `-m unittest test_signup_integration`. Without the explicit URL,
the PostgreSQL integration class skips; that is not acceptance. The native browser
test requires Edge and free loopback port 8766 and refuses a collision rather
than stopping another listener. Run the regression/syntax checks separately,
verify fixture cleanup, and stop only the exact disposable container.

The subsequent default-off integration design is recorded in
`BETA_SIGNUP_APPLICATION_INTEGRATION_PLAN.md`. It separates database/lifecycle,
real-provider/session and closed UI implementation slices. These remain pending;
design completion does not authorize deployment or public activation.
No deployment, provider change, backup/restore cycle, commit, push, PR or workflow
dispatch was performed for this prototype. Local `.codex` configuration and
protected user files remain untouched; no files are staged.
