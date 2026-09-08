# Isolated provider transport and account-bound session slice — 2026-09-07

Subsequent closed-by-default application wiring is recorded in
[BETA_SIGNUP_HTTP_INTEGRATION.md](BETA_SIGNUP_HTTP_INTEGRATION.md). Statements
below about being unwired describe this earlier slice's receipt, not the current
local tree. Neither slice has been deployed or publicly enabled.

CareerTrellis is owned by **J. Hunter Johnson LLC**.

## Outcome and scope

The local, unwired candidate for application-integration slice 2 is implemented
and verified. The application does not import it; no route, current login/session,
provider setting, credential, production restriction or public-signup state changed.
`build_candidate()` defaults off and does not call its configuration/client/store
factory when disabled. There is no new environment reader or executable launcher.

Base remains merged PR 76, `184b34b397dc1fb5458a70e387b76658ac479ad6`, branch
`codex/beta-factor-readiness`. Prior local changes are preserved. Everything remains
local and uncommitted. This receipt does not authorize installation, forced sign-in,
real identity persistence, deployment, publication or public activation.

## Implemented boundaries

- `workos_transport_candidate.py`: fixed HTTPS authentication and client-scoped
  JWKS endpoints; explicit asynchronous transport; zero automatic POST retries;
  no redirects or environment proxies. Each provider operation has a monotonic
  total deadline covering its POST, body consumption and JWKS work. Defaults are
  10 seconds total, 2/3/3/1-second connect/read/write/pool limits and 8 in-flight
  operations, with excess work rejected instead of queued without a bound.
- Responses are limited to 64 KiB of uncompressed JSON, reject duplicate members,
  and never expose provider error text. Only HTTP 400 with `error=invalid_grant`
  is classified as terminal grant rejection; configuration/unknown errors and
  transport failures remain unavailable. JWKS has a bounded key count, 60-second
  cache and 5-second fetch/unknown-key cooldown, including failed fetches.
- Signed evidence requires RS256, an exact configured issuer, a key from the
  fixed client-specific JWKS, typed required time/user/session claims and an exact
  response-user/JWT-subject match. Unverified email, impersonation, hostile key
  URLs, private/invalid RSA material and unexpected audience claims are rejected.
  An audience is required only when explicitly configured; the documented WorkOS
  token contract does not guarantee one. See [WorkOS sessions](https://workos.com/docs/authkit/sessions).
- `account_session_candidate.py`: a versioned, encrypted envelope binds the exact
  local account ID, provider generation, verified subject/session, sealed provider
  tokens, key identifier and absolute lifetime. A configuration binding over the
  client ID, issuer, optional audience and generation also prevents accidental
  generation reuse from silently accepting a different provider configuration.
  This binding excludes secrets and is not emitted as a diagnostic.
- Every candidate authorization verifies provider evidence and resolves the exact
  active local ID plus subject under tenant identity RLS, including owner-role
  consistency. Refresh preserves the ID, subject, session and original absolute
  expiry; expiry is rechecked after awaited provider/database work. Flow-generation
  changes alone do not expire unrelated established sessions.
- An expired access token returns `refresh_required` unless the caller explicitly
  requests one refresh. There is no retry loop or automatic fresh-login redirect.
  Outages return unavailable without clearing the existing cookie or authorizing
  an expired token. The future HTTP boundary must provide bounded refresh
  coordination/backoff; this is not a cross-process refresh-once ledger. WorkOS
  documents a refresh grace period, but this candidate does not depend on retrying
  within it. See [session resilience](https://workos.com/docs/authkit/session-resilience).
- Legacy cookies cannot authorize or be silently upgraded. A separate explicit
  legacy path can verify provider evidence for logout only. Logout returns a
  verified provider redirect target, not a claim that provider logout occurred;
  HTTP CSRF, cookie clearing, redirect delivery and final navigation are not wired.
- `admission_adapter_candidate.py`: browser/state/PKCE/purpose/generation binding,
  committed durable claim before exchange, and acknowledged account/profile
  activation before cookie issuance. SQL transaction exit is part of success.
  Ambiguous claim/COMMIT results emit no session, repeat no code exchange and do
  not trigger compensating deletion. A fresh explicit login can recover a retained
  account without allocating a second seat.
- Only minimal verified user fields and the needed access/refresh tokens enter
  the pinned SDK-compatible inner seal. Email/profile, upstream OAuth tokens and
  provider organization roles are discarded. Sensitive value types suppress repr;
  no new token database, cache, backup or server-side persistence is introduced.

Pinned local source inspected: WorkOS 10.1.1 code/refresh request builders and
session sealing, HTTPX 0.28.1 and PyJWT 2.13.0. The existing SDK client's behavior
and dependency pins were not altered. Authentication method metadata is not MFA
proof; this work does not close the beta factor/recovery gate.

## Database addition

The still-uncommitted, never-deployed revision `20260907_0014` now also defines a
narrow `careertrellis.admission_session_context()` read for the candidate: adapter
readiness, minimum session version, maintenance and provider realm only. Runtime
can execute it; workers/broker cannot. Both migration defaults and real grant
reapplication are tested. No ledger access, counts, cap, identity or token is
exposed. Existing legacy readiness remains unchanged.

Tests explicitly simulate v2/beta policy and give only the disposable broker a
synthetic login. Shipped bootstrap still creates NOLOGIN roles, installs no
credential and leaves policy pilot/closed/v1. No automatic policy activation or
compatibility fallback was added.

## Cookie budget evidence and remaining acceptance

The candidate caps a cookie value at 3,500 ASCII bytes and checks the complete
Set-Cookie budget against 4,096 bytes. It preflights using the maximum PostgreSQL
account-ID length before activation and checks again after activation/refresh.
Known oversize prevents account creation; unexpected later oversize retains the
account, emits no bare/truncated cookie and returns `service_limited`, not a
fresh-login loop.

Fresh synthetic measurements with minimal documented JWT claims, minimal sealed
user data, nested pinned-SDK sealing and a maximum-length local account ID:

| RSA bits | Refresh bytes | Access bytes | Cookie value bytes | Set-Cookie bytes | Fits candidate |
| --- | --- | --- | --- | --- | --- |
| 2048 | 128 | 591 | 2148 | 2234 | Yes |
| 2048 | 512 | 591 | 2828 | 2914 | Yes |
| 2048 | 2048 | 591 | 5560 | 5646 | No |
| 4096 | 128 | 932 | 2764 | 2850 | Yes |
| 4096 | 512 | 932 | 3448 | 3534 | Yes |
| 4096 | 2048 | 932 | 6180 | 6266 | No |

These are measurements of synthetic shapes, not real provider payload sizes or
browser-delivery acceptance. Extra token claims can exceed the remaining margin.
Before live activation, measure accepted real-shaped payloads and verify native
browser delivery. If necessary, stop for a separately reviewed storage/chunking
decision; do not silently enlarge cookies or add token persistence. Session
lifetime/key policy and the forced-sign-in window also require acceptance. The
one-hour test lifetime is a fixture choice, not an approved live policy.

## Fresh verification

| Gate | Result |
| --- | --- |
| Provider/session + actual PostgreSQL + lifecycle suites | 72 tests in 13.459 s, all passed; 39 transport/session, 8 PostgreSQL adapter and 25 existing lifecycle tests; no skips |
| Full PostgreSQL/RLS suite | 57 tests in 4.165 s, all passed; repeated head `20260907_0014` |
| Historical isolated integration prototype | 44 tests in 7.873 s, all passed |
| WorkOS rehearsal/account-choice/runtime/P0/object suites | 404 tests in 17.359 s, passed with two existing Windows platform skips |
| Compilation | `python -m compileall -q .`, exit zero |
| Native permissions/shell | Existing Linux root-to-PostgreSQL bootstrap and four shell syntax checks passed within the lifecycle suite |

Provider fixtures generate temporary RSA keys and signed tokens, use in-memory
HTTP transports, and never call an external API. A real loopback HTTPS/TLS server
also delivers an incomplete streamed body: the actual HTTPX/httpcore request is
cancelled at the total deadline, its socket closes, and the server stops cleanly.
Injected connect/read/write/pool exceptions prove fixed error mapping; they are
not separate kernel-level tests of every timeout phase. No live Caddy, provider
network, host systemd, DNS or certificate acceptance is claimed.

Actual PostgreSQL tests prove commit-then-lost-acknowledgement behavior, lost claim
acknowledgement without exchange, concurrent duplicate callbacks, account/profile
atomicity, exact-ID authorization, real erasure/recreation, provider-generation
changes, closed-signup login and runtime-only context grants. The disposable
PostgreSQL 18.4 fixture refuses collisions and uses loopback plus tmpfs storage.

The 404-test run starts the ordinary app with temporary synthetic SQLite storage,
checks `/api/version`, `/` and the unchanged sign-in flow, and stops it cleanly.
The two skips remain the existing POSIX hosted-authority/Linux systemd tests.
Existing Starlette/httpx and websockets deprecation warnings are non-fatal.

Independent bounded transport and session/lifecycle readbacks were reconciled
with primary source inspection and execution receipts. Corrections included
actual transport-pool settings, terminal-versus-configuration error handling,
post-wait expiry, exact context-function grants and key/configuration binding.
This is local slice acceptance, not a repository-wide security scan.

Final tracked/untracked whitespace and diff/status checks passed; nothing is
staged. Root `.codex` remains present and ignored. The exact labeled disposable
container was stopped and auto-removed after zero residual fixture roles/schemas
and no RLS test database were verified. Only disposable synthetic contents were
discarded and are not retained; application data and backups were untouched.

## Next step

Implement application routes/UI and session-cookie integration behind the explicit
closed boundary. Include strict callback cardinality and Origin/CSRF checks,
cross-process abuse/refresh controls, stable status layout, accessibility and
native browser flows. Preserve existing sign-in/recovery when signup is full or
closed. Final configuration must reuse the reviewed database/TLS/secret-loading
contracts; the injected candidate engine/configuration seams are not an installer.

Public activation still needs cap/lease/purge policy, provider domain/environment/
cost and immutable-identity transition, MFA/recovery, real cookie sizing, ingress
privacy and exact-change deployment/live verification. No commit, push, draft PR,
deployment, extra backup/restore or workflow was run for this slice.
