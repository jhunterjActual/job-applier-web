# Bounded self-service admission prototype

CareerTrellis is owned by **J. Hunter Johnson LLC**.

This document records the original reduced-schema experiment. The later
real-schema adapter experiment and its separate acceptance receipt are in
`BETA_SIGNUP_INTEGRATION_PROTOTYPE.md`; its database is `ct_signup_integration`,
not the `ct_signup_prototype` database described below. Neither enables public
signup or changes the current pilot.

## Scope and status

The operator authorized a **local prototype**, not public signup activation.
`backend/signup_prototype/` and `backend/test_signup_prototype.py` are isolated
from application imports, routes, configuration and the Alembic migration chain.
They accept only a loopback PostgreSQL database named `ct_signup_prototype`.
The SQL also rejects other database names. No deployment/build bump, WorkOS,
DNS, billing, staging, live account, Caddy, or backup change is included.

The intended product journey is Sign In or Create Account, independent email
verification and activation, with a capacity restriction during broader beta.
Full release removes the beta capacity restriction and other temporary beta
gates, not verification, account isolation or security controls. Existing login
and recovery must remain available when signup is closed or full. The current
pilot stays operator-preseeded and invitation-only until separate acceptance.

WorkOS without paid add-ons is a conditional candidate. The operator rejected
the $99/month custom-domain add-on. A stable provider-managed domain and an
explicit identity-preserving environment transition remain to be rehearsed;
matching emails must never rebind tenants. The local prototype makes no claim
about provider pricing eligibility, email deliverability, MFA or recovery.

## Implemented experiment

| Concern | Prototype behavior |
|---|---|
| Modes | Database default `closed`; `beta` applies cap; `open` removes only the cap. Missing policy/DB errors return `unavailable`, not `full`. |
| Capacity | All retained synthetic local accounts (including owner, disabled and erasing) plus unexpired, current-generation reservations. No email/identity list or count is returned publicly. |
| Serialization | One policy row lock coordinates reserve, activate, cancellation, policy updates, fixture erasure and expiry purge. Time is sampled **after** lock acquisition. |
| Lease | Default 10 minutes, configurable 30–900 seconds, bounded by a 15-minute trusted flow deadline. Retry never renews. TTL values are experimental, not a final accessibility policy. |
| Activation | A valid reserved flow plus synthetic verified immutable subject creates one ordinary user and consumes the reservation in the same transaction. No owner role input or email matching. |
| Replay | Duplicate activation returns the same active local ID; mismatched browser/subject, missing reservation, cancelled/expired flow, disabled/erasing/deleted account cannot create another account. |
| Failure | Cancellation releases capacity; abandoned attempts expire. A rolled-back activation preserves the reservation and creates no account. A lost response can be retried using durable state. DB/lock failures are sanitized as unavailable. |
| Privileges | Separate reader and broker roles; neither owns schema or directly writes accounts/ledger. Fixed-search-path owner functions expose only bounded operations. Synthetic accounts use forced RLS. PUBLIC/default function execution is revoked. |

The status names are internal results, not finished UI copy or session grants.
Only a future trusted server adapter may call reserve/activate; a browser must
never submit `SyntheticVerifiedIdentity` or choose its verification assertions.
Before first activation, durable final-subject-to-reservation correlation is an
**unimplemented adapter obligation**, not something proven by the prototype's
browser-secret hash or the separate live protocol receipt.
After activation the reservation is tied to the exact local/external mapping.

### Policy changes and erasure

Closing signup prevents new account activation even with an outstanding lease.
An already committed activation remains an idempotent result only while its
account is active; ordinary sign-in stays a separate existing-account path.
Lowering the cap does not evict accounts. Outstanding leases may then exceed
the new cap temporarily; only activations below the new account cap succeed,
and no new leases are issued until occupancy is below that cap. Raising the cap
or reopening signup allows an otherwise still-valid lease to finish.

Fixture erasure advances a **global admission generation** and deletes the
synthetic account atomically. This invalidates all outstanding pre-erasure
registrations, including ones not yet correlated with an external identity.
Consumed callbacks still require the original active local ID. A genuinely
fresh explicit flow can create a new ordinary account, never restore old data.
This intentionally conservative experiment avoids a permanent erased-identity
tombstone, but may require unrelated people to restart signup after an erasure.
That tradeoff and integration at erasure *request* and completion must be reviewed
before adoption; the fixture is not the application erasure implementation.

Reservations retain an opaque request ID, browser-secret digest, deadlines,
generation, state, and (after activation) a local ID; no email or external ID
is retained in this ledger. Owner-only `purge_expired` removes expired rows only
after the original flow deadline has also passed (up to 15 minutes plus purge
scheduling delay). Retaining the receipt until then prevents the same still-
valid flow from renewing an expired shorter lease after cleanup.
It is tested but not scheduled or installed. The eventual adapter must reject
an expired signed flow even after its database row is purged, and must never
reuse an old request ID with a refreshed deadline. A retention/purge schedule
is required before deployment; TTL alone is not physical deletion.

## Local acceptance and repeatability

Use a new disposable PostgreSQL 18 container, loopback-only port, no host data
volumes, and synthetic credentials. The suite refuses pre-existing fixture
roles/schema and removes only the fixtures it created. Never point it at an
application database, recovered backup, or shared PostgreSQL test server.

1. Start a dedicated `postgres:18` container with `POSTGRES_DB=ct_signup_prototype`,
   synthetic test-only password, ephemeral storage, and a `127.0.0.1` port.
2. Set `CAREERTRELLIS_SIGNUP_PROTOTYPE_URL` to that disposable database only.
   This variable must not contain a live or operator credential.
3. From `backend`, run the existing Python environment with
   `-m unittest test_signup_prototype -v`.
4. Run the P0/object suites and `-m compileall -q .` separately.
5. Confirm fixture cleanup and stop only the exact disposable container.

Without the explicit isolated URL, the PostgreSQL class skips; that is not
acceptance. The suite includes real separate-connection last-slot and duplicate-
activation races, an expiry-over-lock-wait case, rollback after insert, a new
pool retry after a committed activation, revoked-role denials, RLS visibility,
closed/full/unavailable states, and consumed/outstanding erasure replays.
It does not simulate an actual WorkOS outage or TCP loss during COMMIT: the
provider-failure branch cancels a synthetic flow, and the lost-response case
discards a committed result before retrying from a new pool.

### Local receipt — 2026-09-07

- Base: merged `web/main` commit `184b34b397dc1fb5458a70e387b76658ac479ad6`;
  local branch `codex/beta-factor-readiness`. Prototype changes are uncommitted.
- PostgreSQL **18.4** in a dedicated, auto-removed local container, loopback-only
  port, tmpfs database storage, no host data mounts and synthetic data only.
- **29 prototype tests passed, no skips** (3.593 seconds on the final run),
  including real PostgreSQL lock-wait observation before expiry and retention
  through the original flow deadline after a shorter lease expires.
- **370 P0/object tests passed with two platform skips** (14.606 seconds):
  POSIX hosted-authority shell and Linux systemd verification are not applicable
  to this Windows run. **11 existing account-choice tests passed** (0.580 seconds).
  Python compileall passed. The existing Starlette/httpx deprecation warning is
  unrelated to this experiment.
- Independent lifecycle acceptance inspection identified the shorter-lease
  purge replay gap and insufficient lock-wait evidence; both were corrected,
  retested and read back as resolved. This is not a full security review or
  live provider acceptance.
- At **15:17:00 UTC**, final fixture inventory reported zero prototype schemas
  and zero prototype roles. The disposable container was then stopped and
  auto-removed; its absence was verified. Only synthetic test data was discarded.
- No application server was started: this experiment has no application imports,
  routes or migrations to exercise. Existing sign-in regression coverage passed;
  no live `/api/version`, dashboard or WorkOS flow was claimed as verified.
- No staging/provider changes, credentials, backup cycles, deployment, push,
  PR, or workflow dispatch. The local `.codex` configuration remains untouched.

## Remaining gates — not closed by this prototype

1. **Provider correlation rehearsal:** prove explicit registration intent,
   PKCE/state/browser binding, final verified immutable subject, retries,
   cancellation, timeout and incomplete activation. Signed Actions require
   separate acceptance only if selected; the bounded integration evaluation
   recommends a callback-only first slice. WorkOS's documented
   registration Action supplies pre-provisioning user data, not the final user
   ID or this app's PKCE state. Do not infer a correlation from an email or Action
   ID. A verified login with no local mapping must still fail closed.
2. **Lifecycle/database integration:** adapt to real tenant schema/RLS, restrictive
   runtime grants and existing operator provisioning/erasure locks. Every source
   of account creation/deletion and relevant disable/erasure transition must
   coordinate; simply copying these functions into production is unsafe. Test
   preservation of real-shaped synthetic tenant data and concurrent lifecycle
   transitions, not just this reduced account fixture.
3. **Public flow:** no-store sign-in/create/full/unavailable UI, server-side cap
   enforcement, clear expired/incomplete/retry instructions, accessibility,
   anti-abuse/rate limits, cancellation, and a reviewed ingress change. Current
   BasicAuth and public-route boundaries are unchanged. Never advertise a
   successful CareerTrellis account solely because provider identity exists.
4. **Operational decisions:** actual beta cap/counting exceptions, lease duration,
   finite receipt retention/purge, generation invalidation UX, and policy controls.
   The sample cap of one is test data, not an operator setting.
5. **Provider/environment acceptance:** no-paid-add-on capabilities/costs,
   verification/recovery email, stable domain, MFA/password/social/passkey and
   lost-factor flows, identity-preserving transition and separate live approval.

The operator subsequently approved the isolated WorkOS rehearsal. Its separate
staging-only project and local callback harness are recorded in
`WORKOS_CORRELATION_REHEARSAL.md`. The operator reported `signup_correlated` on
2026-09-07 first using an existing WorkOS account, then with a new test email
account. The operator confirmed required email verification for the second
attempt, completing the fresh-signup row on operator-observed evidence. A
subsequent login-only control with that new test account returned
`verified_login_only`, also passing on operator-reported evidence. The expiry
control subsequently returned `expired` and passed on the same evidence basis.
A subsequent direct clean-result check in a fresh browser context returned
`no_browser_context`, passing that narrow check on operator-reported evidence.
Real swapped/duplicate callbacks, provider cancellation and Google have not
been live-demonstrated; the rehearsal document separates offline coverage and
remaining live scope from the future reservation-activation adapter.
This does not change pilot staging or close application admission/provisioning
gates.

The bounded integration evaluation in `BETA_SIGNUP_INTEGRATION_EVALUATION.md`
recommends an isolated real-schema adapter slice, durable exchange claims,
atomic account/profile creation, shared lifecycle/cap coordination and
account-incarnation-bound sessions. It records the crash-recovery tradeoff and
does not authorize deployment or close integrated acceptance.

Primary provider references: [Actions](https://workos.com/docs/authkit/actions),
[registration payload](https://github.com/workos/workos-node/blob/main/src/actions/interfaces/action.interface.ts),
[hosted UI](https://workos.com/docs/authkit/hosted-ui),
[email verification](https://workos.com/docs/authkit/email-verification),
[environments](https://workos.com/docs/authkit/environments), and
[passkeys](https://workos.com/docs/authkit/passkeys).
