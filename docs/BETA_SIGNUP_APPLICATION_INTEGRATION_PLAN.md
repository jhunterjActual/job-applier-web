# Default-off signup application integration plan — 2026-09-07

CareerTrellis is owned by **J. Hunter Johnson LLC**.

## Decision and scope

Proceed in local, independently testable slices: database/lifecycle coordination,
real-provider/session adapter, then application UI integration. The accepted
synthetic prototype in `BETA_SIGNUP_INTEGRATION_PROTOTYPE.md` is evidence for the
design, not production code to import or deploy unchanged. Keep its synthetic
provider and exact loopback-database restrictions intact.

This plan's original step changed documentation only. No runtime, migration, role, credential,
provider, ingress or session behavior is changed. Installation, forced sign-in,
real identity persistence and public activation remain separate approval gates.
The application's current prohibition on the `production` runtime remains in
place (`backend/web_auth.py:105`); provider environment selection does not remove it.

Local slice 1 was subsequently implemented and verified on 2026-09-07; see
`BETA_SIGNUP_DATABASE_LIFECYCLE.md` for the current implementation, fresh receipts
and still-open deployment/activation gates. The design receipt below remains a
historical documentation-only receipt, not the status of that later implementation.

The separate provider/session candidate was subsequently implemented and tested
locally; see `BETA_SIGNUP_PROVIDER_SESSION.md`. It remains unwired, with no real
provider call or activation. Its receipt separates local evidence from the still-
open cookie, configuration, application/HTTP and live acceptance gates.

Source base: `184b34b397dc1fb5458a70e387b76658ac479ad6`, local branch
`codex/beta-factor-readiness`, including the uncommitted prototype. File/line
references below identify this inspected source, not current deployed state.

## Separate controls that must not be one feature flag

Proposed names and states here are design contracts, not installed settings.

| Control | Initial compatibility behavior | After explicit activation |
|---|---|---|
| Adapter enabled | False: existing pilot login, no broker connection, no new public mutation route | Enables the reviewed v2 routes/adapter, but does not itself open signup |
| Minimum session format | Legacy until approved transition | Persist v2 requirement; never lower it when signup closes or application flags change |
| Admission phase | `pilot`: public signup prohibited; existing explicit operator provisioning preserved | `beta`: all retained accounts plus live reservations count against cap; `release`: explicitly approved removal of beta cap |
| Public signup open | False | Independent open/closed switch; closure stops new signup activation, not existing login/recovery or beta cap enforcement |
| Provider generation | Explicit binding to approved provider configuration | A provider transition requires its own identity-preservation and session acceptance, never email-based rebinding |
| Flow generation | Invalidate pending admission flows at reviewed lifecycle/policy boundaries | Not the session/provider generation; erasing one account must not silently invalidate every other established session |

Malformed or incomplete enabled configuration fails closed. Missing policy or
database outage is unavailable, not an instruction to revert to legacy sessions
or unlimited admissions. A persisted v2 minimum incompatible with a configured
legacy adapter must block startup/readiness rather than silently weaken it.

Important prototype correction: `integration.sql` requires public availability
for operator seed, so its closed default blocks pilot provisioning. Production
must instead preserve explicit pilot seeding before beta enforcement. Once beta
is activated, closing signup or disabling its UI must never bypass the seed cap.
Count owner, disabled and erasure-pending accounts until deletion; a reinvite of
the same retained account does not allocate another seat. No silent over-cap
operator override. An explicit full-release policy change removes only the cap.

## Slice 1: additive database and actual lifecycle entry points

Bootstrap a dedicated NOLOGIN admission-function owner and restricted broker
separately from Alembic. Neither runtime nor either worker may inherit or SET
ROLE to an owner or migration role. The migration login is already a privileged
maintenance principal; any new explicit SET-role membership must be narrowly
specified, non-inheriting, verified and rehearsed under PostgreSQL 18.

`backend/migrations/env.py:59` runs migrations as `careertrellis_schema_owner`.
It cannot simply assign objects to an unrelated owner. The implementation must
rehearse explicit migration-login role membership and transaction-local switches
to the private owner, then restore the schema-owner context. Do not weaken the
runtime's privileges or change historical migrations to make installation work.
Allocate the next revision from the verified current head at implementation time.

Keep the ledger in a distinct private schema, outside the broad grants in
`deploy/postgres/grant-runtime.sql:11` and its default privileges at `:120`.
Explicit global account-read RLS is granted only to the function owner; grant
only required account/profile columns, sequence usage and reviewed functions.
Broker receives function execution, not direct ledger/account access. Runtime
retains only its current identity-scoped reads and `last_seen_at` update, with
no general account INSERT/DELETE/authorization-field UPDATE (`:51`). Preserve
existing IDs, owner role and profile contents; initialize a new empty profile
only inside the account-admission transaction.

Use a single common lifecycle lock before participating operation/queue/request/
catalog/account locks. Replace actual callable erasure bodies in a new migration,
preserving their signatures and return shapes. Optional Python wrappers with
still-executable uncoordinated originals do not satisfy this gate. Internal
helpers, if introduced, must stay inaccessible after grant-script reapplication.

| Actual source path | Required implementation/test boundary |
|---|---|
| `backend/pilot_users.py:142`, `:190`, `:218`, `:254` | Lock before existing pilot lock and account lookup; enforce phase/cap on new seed; preserve owner protection, idempotency and erasure-pending reinvite denial |
| `backend/account_erasure_repository.py:125`, `:155`, `:190`, `:215`, `:231`, `:248`, `:265`, `:290` | Cover request, batch, acknowledgement, renewal, claim, finalize, retry and purge through their real SQL entry points, not fixture replacements |
| `backend/migrations/versions/20260814_0009_account_erasure.py:218`, `:551` | Disable/generation change and final deletion/generation change in their respective atomic transactions; null linkable ledger IDs without replay eligibility |
| `backend/account_erasure_execution.py:66`, `:90` | Keep object-provider I/O outside coordination transactions; retain acknowledgement prerequisite and truthful lease-loss handling |
| `backend/sqlite_postgres_import.py:528`, `:1053` | Privileged maintenance exception, not a public-admission path; closed/quiesced preflight, occupancy reconciliation and flow invalidation before resumption |
| `backend/local_users.py:111` | Preserve existing active identity lookup/touch; never provision in middleware |

Sample actual database time after lock acquisition, not transaction-start
`CURRENT_TIMESTAMP`. Fence any mutation whose later row/catalog lock wait
outlives its lease; rollback the whole delegated mutation when necessary.
Include the actual worker's receipt purge and claim path. Mixed-workload tests
must cover erasure versus the search worker's run/owner locks and tenant catalog
writers; do not add every search operation to the global lock by default.

Imports already require maintenance locks, stopped services and authority
approval (`deploy/scripts/import-sqlite-postgres.sh:74`, `:81`, `:89`). They do
object I/O inside an existing import transaction (`sqlite_postgres_import.py:1060`).
Do not wrap that network workflow in a global admission lock. Require proven
quiescence, prevent policy reopening until reconciliation, and retain uncertain-
COMMIT handling. Direct importer entry points must not bypass the maintenance
contract. Superuser/migration credentials remain privileged maintenance
authority, not a threat boundary that runtime RLS can constrain. Post-cutover
import prohibitions remain unchanged; this plan authorizes no new import.

Acceptance: upgrade with populated synthetic tenants; repeated head upgrade;
real grant reapplication; negative table/function/role-membership tests; actual
operator and erasure entry-point races; object acknowledgement and lease expiry
through observed lock waits; unchanged pilot behavior with adapter off; import
refusal/reconciliation; no new admission-broker credentials/connections or new
adapter transport when disabled. Existing pilot database/authentication access
continues to use its current credentials and connections.
Deployment must quiesce old uncoordinated transactions before function changes.

## Slice 2: real provider and account-bound sessions

Separate verified exchange results from account activation and cookie issuance.
Use explicit signup/login purpose, browser binding, exact state/PKCE, deadline,
provider generation and durable claim. Only an acknowledged claim permits one
exchange. Only acknowledged activation plus current exact-ID authorization
permits a session. Login never creates a local account. Ambiguous exchange or
COMMIT outcomes require a fresh flow/login, not code replay or compensating delete.

Pinned SDK inspection found a real transport gap:

- `backend/requirements.txt:1201` pins WorkOS **10.1.1**. Current
  `backend/web_auth.py:150` inherits the SDK's 60-second request timeout and
  three retries. Its internal POST retry loop can repeat a consumed-code request.
- PKCE exchange accepts per-request retry/timeout options; the sealed-session
  refresh helper does not pass such options, so client-level defaults also matter.
- SDK clients follow redirects by default. JWKS uses a separate cached
  `PyJWKClient` with a 30-second urllib timeout, unaffected by the SDK timeout.

These observations were read from installed pinned source: WorkOS
`_base_client.py:78`, `:395`, `:451`; `user_management/_resource.py:2390`, `:2433`;
`session.py:181`, `:312`, `:347`; PyJWT `jwks_client.py:26`. The official
[Python SDK documentation](https://workos.com/docs/sdks/python) corroborates the
retry/timeout defaults, but it is not a substitute for the pin-specific tests.

Recommended next provider implementation: a distinct adapter with cancellable
async transport, zero automatic code/refresh retries, no redirects, fixed trusted
endpoints, bounded connect/read/write/pool waits and a monotonic total deadline.
Use supported SDK helpers only where these controls can be enforced; otherwise
isolate a small reviewed async transport for the documented authentication
endpoint. Do not mutate SDK private globals or silently upgrade dependencies.
JWKS needs separately bounded retrieval/cache and strict issuer/client/audience,
algorithm, key and expiry validation consistent with the pinned provider contract.
Verify which claims that contract actually supplies; do not invent a required
`aud` claim from the synthetic model or trust an unverified decoded token.

A timeout around `run_in_threadpool` stops waiting, not the underlying POST.
Per-I/O timeouts alone are also not a total deadline. Cancellation must fence
late activation and never imply that the provider did not consume the code.
Test delayed responses, 429/5xx, redirects, connection loss, JWKS failure/key
rotation, bounded concurrency and cancellation with synthetic local transports;
assert exact request counts and no credential-bearing diagnostics.

The v2 envelope binds local account ID, provider generation, verified subject/
session continuity, sealed provider session and explicit lifetime. Validate the
provider evidence before trusting continuity, and recheck the active exact local
ID on every private request. Preserve binding during refresh. On an approved
transition, legacy cookies and in-flight legacy flows require fresh sign-in;
never upgrade a cookie by resolving its subject to whichever account exists now.
Legacy cookies may support verified provider logout only, not tenant access.

Keep the prototype's conservative cookie ceiling for initial tests, but measure
real-shaped SDK payloads before choosing the final format. Nested real cookies
may exceed browser limits. Oversize must fail without emitting a bare/truncated
cookie or deleting a committed account. If repeated fresh login is also oversized,
do not send the user into a retry loop: report a service limitation and stop
activation until a reviewed storage/chunking alternative is accepted. Server-side
token storage would introduce a separate retention, encryption and backup scope.
The [WorkOS session contract](https://workos.com/docs/authkit/sessions) allows
secure-cookie or backend refresh-token storage; this plan selects neither new
storage nor a provider-account change implicitly.

## Slice 3: application integration, still closed

Update `web_auth.py:154`, `:185`, `:223`, `:251` behind the explicit adapter
boundary. Update `app.py:687` for exact callback cardinality and clean outcomes;
`:492` for account-bound authorization; `:520` for wrapped refresh only; `:481`
and `:750` for verified logout without tenant access; and `:1771` to clear new
auth cookies and suppress refresh on erasure. Database/provider outages preserve
usable session state and show unavailable, not invalid identity.

Keep public endpoints narrowly under `/auth/`, with an exact conditional
allowlist at `app.py:210`. GET availability allocates no seat and exposes no
counts/identities. Explicit signup POST must validate exact non-null Origin,
browser-bound CSRF, bounded body and duplicate/blank-duplicate cardinality.
The generic middleware's optional Origin/Referer check is insufficient by itself.
Use a narrowly scoped same-origin form policy on the landing page or tested
same-origin fetch; retain no-referrer on callbacks/results and no-store on auth.
Existing Caddy `/auth*` log exclusion remains mandatory, not live-verified by
source alone. BasicAuth and trusted-proxy enforcement remain unchanged.

Native browser tests must cover Sign In/Create Account/full/closed/unavailable,
maintenance notice preservation, cancellation, slow verification restart, keyboard
navigation, stable status layout, cookie delivery, refresh/logout and first profile
save. Rate limits must precede reservation/provider work, bound login-flow ledger
growth as well as signup, work across processes, and avoid starving existing users
when signup is full. Final limits and retention/purge scheduling require acceptance.

## Activation and forward-correction gates

1. Complete and review each local slice using real PostgreSQL/SDK-shaped synthetic
   evidence. No routine tests use real identities, paid APIs or application data.
2. Separately approve exact role/credential installation and the maintenance
   migration window. Verify existing owner/pilot tenants, normal worker behavior,
   schema/RLS/ACLs and default-off health before any signup activation.
3. Separately approve session-v2 lifetime/key policy and forced-sign-in notice/
   window. Verify ordinary mapped-user login, refresh, outage and logout first.
4. Accept cap, leases, physical purge, generation-restart UX, abuse/accessibility,
   provider environment/domain/cost/identity transition and MFA/recovery evidence.
   A provider identity count/MAU cap is not guaranteed by the local account cap.
5. Authorize a bounded live integrated rehearsal and then public ingress/signup
   activation separately. Retain only sanitized receipts, never tokens/identities.

Closing signup is an operational safety control, not a schema downgrade or
permission to restore legacy session rebinding. Preserve account IDs, v2 minimum,
cap enforcement and coordinated erasure after activation. Prefer forward correction;
do not remove successful accounts, restore old functions, or initiate a backup/
restore cycle merely to roll back signup presentation.

Successful deployment and live verification of the exact change set still precede
push to a PR branch or creation of a draft PR. None is authorized by this design.

## Design verification

Primary source inspection was reconciled with independent bounded lifecycle and
application/provider reviews, followed by plan readback. Their receipts are
design evidence, not a full security scan or acceptance of an implemented
production adapter. The disabled-path wording was corrected to preserve the
existing pilot's required credentials/connections.

Fresh primary-agent regression receipt: `test_workos_rehearsal`,
`test_account_choice`, `test_p0_regressions.py`, `test_object_store.py`,
`test_object_import.py` and `test_stored_objects.py`: **402 tests in 13.058 seconds,
OK with two existing Windows platform skips** (POSIX hosted-authority and Linux
systemd checks). `python -m compileall -q .` exited zero. Existing Starlette/httpx
deprecation warning was non-fatal. No new application-integration acceptance is
claimed; the prior 44-test prototype receipt remains a separate historical result.

Only this plan, backlog status and its prototype-document crosslink changed in
this step. Prior local work is preserved. Whitespace/diff/status checks passed;
nothing is staged, including protected files. No application server was started:
no runtime/UI code changed. No live provider/host query, database/container setup,
deployment, backup/restore, commit, push, PR or workflow was performed.
