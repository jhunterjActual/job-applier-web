# Bounded signup integration evaluation — 2026-09-07

CareerTrellis is owned by **J. Hunter Johnson LLC**.

Follow-up on 2026-09-07: the subsequently authorized isolated real-schema
prototype passed. Its implementation, fresh 44-test integration receipt and
remaining production/live-provider limits are recorded in
`BETA_SIGNUP_INTEGRATION_PROTOTYPE.md`. The evaluation below is the earlier
design decision, not a claim that application activation has now occurred.

## Decision and scope

**Proceed to an isolated integration prototype, not application activation.**
The callback-based approach is viable for the requested CareerTrellis account
cap. It does not need a registration Action to join an email to a final subject.
Keep ordinary sign-in separate and fail closed for unmapped identities.

This evaluation changes documentation only. It does not implement an adapter,
migration, public signup route, session change or deployment. Pilot staging,
provider configuration, billing, ingress and existing account mappings stay
unchanged. WorkOS without paid add-ons remains conditional on the separate
environment/domain, cost, identity-transition and MFA/recovery gates.

Source base: `184b34b397dc1fb5458a70e387b76658ac479ad6`, with the local uncommitted
admission prototype and protocol harness. The rehearsal records operator-reported
fresh signup with required email verification, existing-identity signup intent,
login-only, expiry and missing context on the clean result endpoint. Real swapped
or duplicate callbacks, cancellation and Google are not live-proven. The 29-test
PostgreSQL prototype receipt is historical synthetic evidence, not integrated
application acceptance. See `WORKOS_CORRELATION_REHEARSAL.md` and
`BETA_SIGNUP_PROTOTYPE.md`.

## What can be reused, and what cannot

| Surface | Source evidence | Integration consequence |
|---|---|---|
| Verified identity | `backend/web_auth.py:199`, `:223` validates email/subject and rejects impersonation, then seals a session | Separate provider verification from admission and cookie issuance. Recheck the active mapping before returning a session. |
| Existing login and tenant access | `backend/app.py:686`, `:492`; `backend/local_users.py:87`, `:119` resolve pre-existing active mappings | Never provision in private-request middleware or ordinary login. Preserve configured-owner consistency and per-request mapping checks. |
| Durable capacity experiment | `backend/signup_prototype/schema.sql:73`, `:113` serialize reserve/activate; `service.py:58` requires an isolated DB | Reuse semantics, not the fixture schema or synthetic identity assertions as a public API. |
| Protocol experiment | `backend/signup_prototype/workos_rehearsal.py:58`, `:84` uses a process-local lock/map and discards subject/tokens | Multi-worker claims, restart recovery and session delivery are still missing. Do not copy the global in-memory lock into the app. |
| Real account model | `backend/migrations/versions/20260811_0001_tenant_rls_foundation.py:97` uses bigint local ID, unique WorkOS ID, role and `disabled_at` | Preserve local tenant IDs; new admission creates only an ordinary user. Do not translate fixture booleans mechanically. |
| Global visibility/grants | Foundation migration `:109`; `deploy/postgres/grant-runtime.sql:11`, `:59`, `:120` | Forced RLS makes a normal count identity-scoped. Broad grant reapplication must not expose a private admission ledger. |
| Empty onboarding | `backend/profile_repository.py:219`, `:418`, `:443` does not create missing profiles; update rejects an absent row | Create an empty profile atomically with a new account. Account insertion alone would leave a broken first-use path. |
| Public browser boundary | `backend/app.py:211`, `:323`; `deploy/caddy/Caddyfile.staging:25` | New routes require exact public allowlisting and reviewed CSRF/privacy behavior. BasicAuth still prevents anonymous public signup. |

## Recommended adapter contract

1. **Show availability without allocating a seat.** GET returns available,
   full, closed or unavailable, without counts or identities. When full, show
   the cap message and no create action; always retain sign-in/recovery. GET
   is advisory: only the subsequent transaction can reserve the last seat.
2. **Explicit signup POST reserves before redirect.** Validate exact origin,
   browser-bound CSRF, bounded input and abuse limits. Bind server-created
   purpose, random request ID, state, original deadline, browser-secret digest
   and provider configuration generation. No caller-selected subject, local ID,
   role, verified flag or renewable deadline. Ordinary login allocates no seat.
3. **Claim the callback durably before code exchange.** Validate original
   browser/state/PKCE and query cardinality, then atomically transition the
   reservation to an exchange claim with an attempt/fencing ID. Commit that
   short transaction before HTTP. Only one worker exchanges; duplicates return
   a safe status. Never hold the global admission lock during provider calls.
4. **Verify and activate.** Exchange once with bounded timeout and no automatic
   code retry. Accept only the configured provider realm, verified immutable
   subject, no impersonation, and the separately required authentication policy.
   Reacquire the admission lock, sample database time after acquisition, and
   check claim ID/state, generation, lease, signup mode and capacity again.
   Atomically insert an ordinary `app_users` row and empty `profiles` row and
   consume the reservation. Initialize no resume, personal fields, provider
   keys or copied tenant data. Validate profile defaults through the normal
   first-use path. Roll back everything if either insert fails.
5. **Only then issue an account-bound session.** Confirm the exact active
   local/subject mapping and configured-owner rule. Preserve Secure, HttpOnly,
   SameSite cookie controls, but bind the authenticated envelope to the newly
   allocated local account ID and provider configuration generation, not only
   the WorkOS subject. Send it only after acknowledged activation. Redirect to
   a clean result/onboarding URL. Private requests must check that the verified
   provider subject still maps to that exact active local ID. A terminal
   admission receipt is not itself a session credential.

An existing active local account reached through signup consumes no additional
account slot, never changes role or identity, and releases its unused reservation.
Use the ordinary sign-in path. Disabled/erasure-pending mappings cannot be
reactivated this way. A WorkOS identity without a local mapping may be admitted
only through a genuinely fresh explicit signup and valid reservation, even if
the provider identity predates this attempt. Neither an old provider session nor
an unmapped login callback is implicit registration authority.

**Account recreation also needs a session-incarnation boundary.** Current
`app.py:492` resolves each provider session to whichever local ID currently owns
that subject. After deliberate re-registration of an erased identity, this
would allow a surviving old cookie to resolve to the new account. Admission
generation checks alone do not fix this. The proposed authenticated outer
envelope binds local ID, provider configuration generation and the sealed
provider session together; refresh preserves that binding and verifies subject
continuity. Local IDs must never be reused. An old envelope cannot access a
newly allocated account, even for the same verified provider subject. Legacy
cookies must require fresh sign-in at a separately approved rollout, not be
silently rebound. Prove cookie size limits, refresh/logout and migration behavior
before adopting the new envelope; no session format changes occur now. Every
refresh branch must rewrap with the original binding, never emit the bare
provider cookie currently assigned at `app.py:523`. Preserve the verified
logout exception at `app.py:481`: disabled/erased users may terminate their
verified provider session without gaining tenant access.

## Failure and recovery contract

The recommended first slice prioritizes correctness without storing provider
credentials in the admission database. It does not promise seamless recovery
across every crash window.

| Failure boundary | Required outcome |
|---|---|
| DB unavailable before reservation/claim | No provider exchange or admission. Show unavailable, not full. |
| Crash/timeout after durable exchange claim | The code may be consumed. Never reclaim it for another exchange; show incomplete/restart. Lease expires without automatic renewal. Fence any late result against a terminal/cancelled/expired claim. |
| Provider success, definite activation rollback | No account or profile persists. Clear transient credentials; release/cancel safely or let the bounded lease expire. Start a new explicit attempt. |
| COMMIT acknowledgement uncertain | Do not cancel, delete, compensate or retry as a new account. A browser-bound durable-status read may establish the outcome but cannot grant a session. Fresh authenticated login resolves an existing active mapping; if absent, require fresh explicit signup. |
| Activation committed, response/cookie lost | Preserve the account and occupied slot. Fresh sign-in reaches that same mapping without a new reservation. Never re-exchange the old code to recover a cookie. |
| Expiry/closure/deletion races the exchange | The final database check wins; no activation from expired/cancelled/old-generation claims. Closing signup does not revoke existing login. |
| Late cancellation or duplicate callback | Never overwrite committed success. Do not issue tokens or resurrect a deleted/disabled mapping from a cached receipt. |

Raw codes, PKCE verifiers, email and provider tokens stay out of ledger rows,
logs and receipts. Keep the verifier only in the bounded encrypted browser-flow
envelope, with schema version, trusted timestamps and purpose validated by the
adapter. The ledger retains opaque bindings/claim state/deadlines/generation and
the committed local ID, not a durable pre-activation provider identity. Purge
only after both the lease and original usable-flow deadline expire; reject old
envelopes even after purge. Erasure must remove/null linkable local-ID references
without making an old activated receipt eligible to create an account again.

The hosted app already retains access/refresh material inside a sealed session
cookie (`web_auth.py:240`). An integrated session test therefore cannot reuse
the rehearsal's `provider_tokens_retained:false` assertion to describe the
entire app. Prefer `provider_tokens_persisted_in_admission_store:false`, plus
separate session-cookie security evidence. No token values enter receipts.

## Database and lifecycle requirements

- Use a private admission schema, a NOLOGIN function owner with narrowly scoped
  account/profile privileges and explicit RLS policies, and a restricted broker
  role able to execute only reviewed functions. No application membership in
  the function-owner role, migration credential in the web process, global
  runtime account visibility or general runtime account INSERT. Required
  profile initialization must use only the newly allocated tenant context.
- Count every retained account, including owner, disabled and erasure-pending,
  plus current-generation live reservations/claims. Operator seed must join
  the same serialization and occupancy rules; the existing unrelated advisory
  lock/direct INSERT in `backend/pilot_users.py:142`, `:190` is insufficient.
  Imports (`backend/sqlite_postgres_import.py:540`) require closed admission,
  maintenance coordination and post-import count validation. No silent override.
- Put one shared admission/lifecycle coordination lock before participating
  per-user/request/catalog locks. Real erasure request currently locks user
  then request; finalization locks request, catalog then user (migration
  `20260814_0009_account_erasure.py:187`, `:510`, `:522`, `:527`). A mid-function
  lock or delete trigger alone is not an adequate lock-order design. Inventory
  and test the affected paths together; keep provider/object I/O outside it.
- Preserve immediate denial at erasure acceptance and capacity until final
  deletion. Prototype the conservative global generation invalidation at
  request acceptance and final deletion, atomically with those transitions.
  Completion invalidation also catches unrelated flows begun during erasure.
  Replays require the original extant active mapping. No permanent external-ID
  tombstone, restored tenant data or automatic provider-user deletion is added.
- Global invalidation can force unrelated registrants to restart; this is a
  deliberate prototype tradeoff awaiting product acceptance, not invisible UX.
  Disable/reinvite must coordinate with admission and preserve erasure denial.
- Install additive migrations with closed default. Verify ACLs after both
  migrations and actual grant-script reapplication. Application runtime/search
  worker/erasure worker/PUBLIC must not read the ledger, choose identities via
  a public route, edit caps, promote roles or execute unrelated broker functions.

## Product and provider boundaries

The normal visitor journey remains Sign In or Create Account → reserve → hosted
verification → account activation → onboarding. Beta mode adds capacity; open
mode removes capacity, not verification or authorization. Existing users can
sign in while full/closed. A provider identity may exist after an abandoned or
expired attempt, or direct provider registration; the app must still deny
admission without a reservation. This is an application-account cap, not a
guarantee that provider users/MAUs are capped. Cost and abuse review must account
for that distinction before public beta.

Recommend no registration Action in the first adapter slice. WorkOS documents
Actions as synchronous pre-provisioning policy hooks, while callback state and
PKCE bind the completed authentication. Adding an Action would introduce another
public signed endpoint without replacing the database activation transaction.
If strict provider-user creation control becomes required, reevaluate it as a
separate contract; do not infer a final identity from email/Action ID.

The experimental 10-minute lease/15-minute envelope is not the final enrollment
accessibility policy. Do not time-box MFA enrollment as a product requirement.
An expired reservation must offer a clear new admission attempt without asking
the user to recreate or reverify an already verified provider account. Rehearse
slow email/factor enrollment and restart behavior before choosing final durations.

New public mutation routes also need the native-browser regression already
learned from the harness: `app.py:334` currently forces no-referrer on all auth
pages. Choose and test a narrow same-origin form policy or an explicit same-origin
fetch submission; never accept null Origin to work around it. Keep callback
responses no-referrer and every auth path excluded from access logs. Review the
BasicAuth removal separately; the integration prototype does not remove it.

Official references checked for this evaluation:
[callback/state/PKCE](https://workos.com/docs/reference/authkit/authentication/get-authorization-url),
[Actions](https://workos.com/docs/authkit/actions), and
[session token handling](https://workos.com/docs/authkit/sessions).
These support the provider contracts, not a claim of account-specific pricing
eligibility or acceptance of this proposed adapter.

## Next bounded implementation and acceptance

Recommend a synthetic, isolated adapter slice using the actual Alembic schema
and grant scripts in disposable PostgreSQL, a fake provider and browser tests.
Keep it out of application imports/routes until acceptance. Do not change the
existing operator-run live harness to persist real identities as an incidental
part of this work.

Required tests include final-slot races with operator seed; same subject across
two reservations; duplicate multi-process claims; restart at each exchange/commit
boundary; lost COMMIT acknowledgement; expired claim fencing; closed/full login;
unmapped login denial; default-profile first save; no access before activation;
owner/tenant data preservation; old-session denial after explicit re-registration
and unchanged incarnation during refresh; forced-RLS count accuracy and negative grants
after reapplication; disable/reinvite/erasure/purge races with deterministic lock
wait evidence; old callbacks after erasure; finite physical retention; slow
verification recovery; browser CSRF, duplicate query, clean error URLs and no
credential logging. Reuse synthetic provider cancellation/replay tests, without
relabeling them live evidence. A later approved live integrated rehearsal needs
its own privacy/retention and session receipts.

Deployment decisions still needed: actual cap, lease/retention policy and purge
schedule, conservative generation UX, abuse controls, stable provider environment
and costs, identity-preserving transition, MFA/recovery and public ingress.
None blocks evaluating the isolated integration, but they block activation.

## Evaluation verification

Independent lifecycle/RLS inspection was reconciled against the real migrations,
operator helper, grant script and profile repository. This is an integration
evaluation, not a complete security scan. Review also confirmed preservation of
account binding through refresh and verified logout without tenant access.

The initial delegated regression check produced no receipt and was interrupted;
it supplies no acceptance evidence. A bounded process check found no matching
regression process before the primary agent ran the suite directly. The Windows
Python launcher required execution outside the sandbox.

Fresh primary-agent receipt: `test_workos_rehearsal`, `test_account_choice`,
`test_p0_regressions.py`, `test_object_store.py`, `test_object_import.py` and
`test_stored_objects.py`: **402 tests run in 13.418 seconds, OK with two existing
platform skips**. The skips cover POSIX hosted-authority and Linux systemd
checks on Windows. `python -m compileall -q .` exited zero. Whitespace checks
passed; nothing is staged, including protected files.

These are existing regression tests, not new adapter, live provider or real-schema
integration acceptance. No application server or live dashboard was exercised:
this turn changed documentation only. No provider/host configuration, deployment,
backup/restore, commit, push, PR or workflow action was performed.
