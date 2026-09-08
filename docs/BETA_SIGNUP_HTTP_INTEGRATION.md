# Closed-by-default routes/UI integration

Local candidate, 2026-09-07. This is not deployed, live-verified or publicly
enabled. CareerTrellis is owned by **J. Hunter Johnson LLC**.

## Implemented boundary

`backend/signup_http.py` is constructed only by application lifespan with an
explicit `CAREERTRELLIS_AUTH_V2_ENABLED=1`. Unset or `0` retains pilot sign-in;
other values fail closed. Import/default-off construction reads no candidate
credentials and opens no candidate connections. Enabled-but-missing runtime or
an incompatible database policy never falls back to legacy authentication.

The existing pilot now checks persisted legacy readiness before accepting old
credentials and again before publishing refreshed/callback cookies. Therefore
this candidate requires revision 0014 even when v2 is off. A future session-v2
transition is not reversible by merely changing the environment switch back.

The exact public v2 surfaces are sign-in/login, start, callback/complete, result,
cancel, logout and two static assets. Host/trusted-proxy checks run first. POSTs
require a non-null exact Origin, a browser-bound encrypted CSRF token, bounded
streamed input and exact fields. Ambiguous cookies, duplicate JSON/form members
and duplicate callback fields are rejected. GET availability reserves no seat
and exposes no identities/counts. Start is an explicit POST; full/closed signup
does not remove existing-user sign-in or different-account selection.

Native start POST ends on a same-origin authorization intermediary. Its script
navigates to the fixed provider authorization endpoint; a link works without
JavaScript. This avoids Chromium's form-action CSP redirect restriction without
allowing arbitrary provider origins in form-action. Sign-in and result pages
reject queries and use same-origin referrer policy for native forms; callback
and intermediary use no-referrer. All auth surfaces are no-store. Existing Caddy
auth-log exclusion is still a separate live acceptance requirement.

The callback page never embeds its code/state into HTML. Its external script
clears browser history to `/auth/complete` before POSTing those values; fixed
results live at `/auth/result`. It shows stable tabular elapsed time, an explicit
cancel control and a bounded failure message. No automatic code retry occurs.
JavaScript is required for callback completion; the page states that limitation.

Account-bound authorization sets the existing tenant context and resets it after
the request. The absolute session deadline survives refresh. Only the v2 cookie
can be reissued; ordinary unavailability does not destroy a usable session.
Oversize sessions receive a service limitation, not an endless login loop.
Replacement starts revoke/clear the prior v2/legacy session after valid intent
and budget checks, including closed/unavailable starts. Logout validates CSRF,
records revocation before provider navigation, clears all auth cookies and uses
the configured same-origin return. Erasure clears all new cookies and suppresses
refresh reissuance; exact active-account checks reject late stale cookies.

## Shared request and refresh coordination

The still-unreleased 0014 migration now includes private token-free budget,
refresh-claim and revocation tables/functions. Only the admission broker can
execute these functions; runtime/workers cannot read or write their tables.
No raw addresses, browser IDs, provider IDs, access/refresh tokens or cookies are
stored there. Domain-separated HMACs bind coordination to provider configuration.
The coordination key derives from the envelope key, so key rotation also
invalidates old envelopes instead of silently resetting refresh claims.

Initial fixed-window limits are **proposed implementation values**, not an
accepted public-ingress capacity policy:

| Lane | Global/minute | Browser/minute | Peer/minute |
| --- | ---: | ---: | ---: |
| Signup start | 60 | 5 | 20 |
| Login start; each separate signup/login callback lane; refresh; logout | 120 each | 10 each | 60 each |
| Availability/result/cancel | 240 | 30 | 120 |

All thresholds are checked before counters increment. Global limits bound
browser/peer churn; signup cannot consume login's start or completion pool.
Callback lane selection comes from the authenticated flow, not a posted purpose.
The peer is the ASGI connection peer, never an arbitrary forwarded header. Behind
the current proxy many visitors may share it; trusted client-address handling and
upstream abuse controls require explicit acceptance before activation.

A refresh snapshot must acquire a durably committed one-use claim before the
provider POST. No transaction is held across HTTP. Duplicate/uncertain claims
are not reclaimed; a loser neither clears nor overwrites a winner's cookie.
Re-encryption does not change the claim. Claims and revocations are retained for
the maximum 24-hour envelope horizon from the operation, covering other already
issued envelopes too; duplicates can extend retention but never grant another
refresh. Provider/mapping/policy/revocation
checks run again before issuing the result. A lost winning response may require
fresh sign-in: no token store was introduced to replay it.

Expired coordination rows are removed in bounded batches of at most 500/table
on relevant traffic. This is opportunistic cleanup, **not a physical-retention
schedule**: idle data and the older flow ledger still need an accepted operator
purge policy/job and storage sizing before public activation.

## Configuration and remaining rollout work

No environment file, credential, role login, host or provider setting changed.
The factory requires existing validated WorkOS settings, explicit v2 issuer,
generation, key ID, session lifetime (60–86400 seconds), distinct envelope/flow
keys and the restricted admission database URL. Optional audience must match
the accepted provider contract. Broker/runtime URLs must identify the same
host/port/database and TLS settings; routing overrides/service/options and
unexpected principals are rejected before connecting. Actual session principals
and persisted adapter-ready/minimum-v2/realm/maintenance state are checked.
All owned resources are disposed on failed/cancelled construction and shutdown.

The 2026-09-08 follow-up is recorded in
`docs/BETA_SIGNUP_ROLLOUT_REHEARSAL.md`: actual lifespan/worker/HBA rehearsal
completed locally, with two unresolved defects and protected installation still
open. The original rollout work list below remains context, not activation
approval:

1. Design the broker credential/login/HBA installation and subsequent-migration
   lifecycle. Bootstrap/preflight currently require NOLOGIN, while enabled HTTP
   needs the broker login; no activation installer exists yet.
2. Rehearse maintenance migration, default-off pilot behavior, then the explicit
   v2/forced-sign-in transition with synthetic identities and actual app startup,
   first profile save, search and erasure workers. Web v2 readiness uses its
   context query; generic `check_postgres_ready` remains intentionally legacy-only.
   Workers do not call that generic check at startup, so a worker outage must not
   be inferred solely from minimum-v2; actual queues still need acceptance.
3. Accept cap/leases, physical retention, session lifetime/key rotation, final
   abuse limits/trusted peer policy, accessibility/recovery and real cookie sizes.
4. Resolve provider environment/domain/cost/immutable identity transition and
   MFA/recovery gates. A local account cap does not cap provider identities/MAU.
5. Separately authorize exact deployment and bounded live integrated verification,
   then public ingress/signup activation. Preserve deployment-first PR ordering.

## Local evidence

- 37 actual PostgreSQL/provider/lifecycle tests passed in 18.196 seconds: real
  migration/bootstrap/grants, callback claims, budgets, refresh contention,
  revocation/cleanup, restricted-principal runtime construction and default-policy
  rejection. Runtime construction made zero provider HTTP requests.
- 57 full PostgreSQL/RLS tests passed in 5.305 seconds.
- 16 HTTP/browser tests passed within the final regression suite: default-off/no fallback,
  full/closed login, input/CSRF/rate rejection before reservations, clean callback
  and duplicate handling, account mapping, logout/replacement, refresh contention,
  DSN override rejection and cancelled/failed resource cleanup. Native Edge used
  ephemeral loopback HTTPS, synthetic provider interception, keyboard submission
  at 320px, secure HttpOnly SameSite cookie acceptance and `/api/version`.
- The native HTTP rehearsal injects the tested runtime and disables lifespan;
  the separate PostgreSQL factory test covers construction. Neither proves live
  provider credentials nor an actual deployed lifespan transition.
- Final required P0/object and pilot/provider/rehearsal/HTTP regression run:
  **459 tests in 23.207 seconds, OK with two existing Windows platform skips**.
  Blocking storage/identity readiness checks run off the async event loop.
  The native server and browser stopped cleanly using a separate Selector TLS
  loop; the earlier Windows Proactor shutdown warnings did not recur.
- 44 historical isolated-integration compatibility tests passed in 7.473 seconds.
- `compileall` and whitespace/diff checks passed; nothing is staged. Root-local
  `.codex/config.toml` remains present and ignored. The exact labeled disposable
  PostgreSQL container was stopped/auto-removed after zero residual test-role,
  schema and extra-database counts. Only temporary synthetic data was discarded;
  it is not recoverable or needed. Application data and backups were untouched.

No commit, push, PR, Actions workflow, deployment, real provider request,
application-data mutation or live backup/restore occurred. Existing local work
and ignored `.codex` configuration are retained. Evidence is synthetic and local;
the broader beta and self-service acceptance items remain open.
