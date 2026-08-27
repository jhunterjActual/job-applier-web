# Friends-and-family pilot authorization

This document defines the smallest reviewed multi-user boundary for the
invitation-only staging pilot. The activated `.38` release implements the
operator-preseeded user boundary. The `.39` release adds the root-only live
acceptance and privacy-safe receipt boundary without changing authentication or
the PostgreSQL schema. The `.47` release adds a pre-prompt, offline
runtime/storage compatibility guard to that acceptance boundary. The `.48`
release binds the acceptance interpreter to the already-provisioned staging
runtime directories and PostgreSQL/R2 mode before application-module import,
preventing fallback writes inside an immutable release. Caddy Basic
Authentication remains in front of WorkOS through initial pilot acceptance.
The `.49` release makes acceptance provider-namespace inspection explicitly
tenant-scoped under forced RLS and preserves an unrelated real provider secret
when recovering a crash before the synthetic fixture insert.

## Authentication invariants

- WorkOS registration stays invitation-only and email verification stays
  required. Password-only authentication is accepted. Environment-wide MFA
  stays disabled so TOTP enrollment is not required; optional passkeys may be
  offered separately. CareerTrellis neither requires nor records factor state.
- AuthKit uses authorization code with PKCE, the encrypted login-flow cookie,
  encrypted `HttpOnly`, `Secure`, `SameSite=Lax`, `__Host-` sessions, and the
  existing anti-impersonation check.
- WorkOS authentication never inserts an `app_users` row, assigns or changes a
  role, clears `disabled_at`, or promotes a user. An identity must already have
  exactly one active local mapping before CareerTrellis issues a session.
- The configured owner WorkOS identity must resolve to the single active
  `role=owner` row. Every other accepted identity must resolve to an active
  `role=user` row. Any mismatch, missing row, disabled row, malformed identity,
  or impersonated session fails closed without a CareerTrellis session.
- Disabling or erasing a CareerTrellis user does not delete, disable, or modify
  the separate WorkOS identity. WorkOS recovery remains usable for the same
  immutable identity.

## Capability model

The owner role is immutable, but it is not a cross-tenant superuser. Root host
access is a separate operator authority and is never granted through the web
application.

| Capability | `owner` | Active pilot `user` |
|---|---|---|
| Sign in, sign out, and use WorkOS recovery | Yes, for the configured immutable owner identity | Yes, for an operator-preseeded immutable identity |
| Profile, resumes, jobs, applications, engagements, and saved searches | Own tenant only | Own tenant only |
| Search and worker results | Own queues and results only | Own queues and results only |
| Provider-secret presence/update and provider calls | Own encrypted secrets only | Own encrypted secrets only; no fallback to owner credentials |
| Generated-object create/download/delete | Own encrypted objects only | Own encrypted objects only |
| Portable export | Own tenant snapshot only | Own tenant snapshot only |
| Account erasure | Own account and tenant only | Own account and tenant only |
| Read or mutate another tenant | Never | Never |
| Provision, inspect, disable, recover, or assign roles | Never through the app | Never through the app |
| Become owner, demote the owner, or change the configured owner | Not through this boundary | Never |
| Host, database, backup, restore, Cloudflare, Caddy, or WorkOS administration | Root/operator control only | Never |

The application must use the authenticated local `app_users.id` as tenant
context for every repository, worker, export, erasure, provider-secret, and
object operation. Missing context exposes nothing, and pooled database
connections must clear context before reuse.

## Root-only provisioning contract

The pilot release includes the reviewed root-only
`/usr/local/sbin/careertrellis-pilot-user` tool with exactly `seed`, `inspect`,
`disable`, and `reinvite` operations. It uses the root-owned migration
credential, accepts the immutable WorkOS user ID only through a non-echoing
interactive prompt, refuses command-line or environment identity values, and
never calls WorkOS. Its stdout is limited to `operation`, `result`, `present`,
`role`, `active`, `changed`, and `identity_disclosed=no`; the identity and email
never appear. The runtime and worker database roles remain unable to insert
users, assign roles, or clear disablement.

Before installing the helper, apply the reviewed
`deploy/postgres/grant-pilot-operator.sql` artifact as the local PostgreSQL
operator. It first verifies the restricted migration-role contract, then grants
that role only `SET` on `log_parameter_max_length` and
`log_parameter_max_length_on_error`, allowing the helper transaction to
suppress bind values in statement and error logs. No runtime or worker role
receives either parameter privilege.

Invoke only the operation name and respond to the non-echoing prompt:

```bash
sudo /usr/local/sbin/careertrellis-pilot-user inspect
sudo /usr/local/sbin/careertrellis-pilot-user seed
sudo /usr/local/sbin/careertrellis-pilot-user disable
sudo /usr/local/sbin/careertrellis-pilot-user reinvite
```

### Seed

1. In the WorkOS staging dashboard, the operator confirms only that the
   invitation is accepted, the email is verified, the identity is not an
   impersonated session, and the immutable identity is the intended invitee.
   Do not copy the email or identity into a receipt, chat, shell history, or log.
2. The tool verifies that the separately configured owner identity has an
   active `role=owner` mapping. It protects that identity and rejects any target
   mapping that unexpectedly has `role=owner`. It does not currently emit a
   global owner-row count; the migration/grant review and PostgreSQL tests must
   separately prove that a pilot cannot create or promote an owner.
3. In one transaction, the tool inserts only an absent identity as
   `role=user`, with `disabled_at` unset. It must refuse an owner identity, an
   existing different role, a disabled row, malformed state, or any request to
   choose a role.
4. Repeating seed for the same active `role=user` row is a no-op. Seed never
   updates `last_seen_at`, clears disablement, changes ownership, or creates a
   second row.
5. Inspect immediately, then perform the live login and two-tenant acceptance
   before permitting real pilot data.

### Inspect

Inspect is read-only and idempotent. It reports only the fixed status fields
listed above: mapping presence, role as owner/user/none, active state, changed
state, and a bounded result enum. It never prints an identity, email, timestamp,
database URL, row ID, session, or credential. A malformed identity uses the
same fixed error shape as another operator-boundary failure.

### Disable and containment

Disable is limited to `role=user`, is idempotent, and changes only
`disabled_at` for the selected mapping. It must refuse the owner. On the next
request, local-user resolution rejects the mapping and clears the CareerTrellis
session; the WorkOS identity remains intact.

Before this operation can be treated as incident containment, tests must also
prove that queued or leased work for a newly disabled user cannot begin or
continue a provider call. If the release does not atomically cancel/reject that
work, the operator must stop the affected worker through a separately reviewed
containment procedure. A successful row update alone is not proof of worker
containment.

### Reinvite, recovery, and explicit reactivation

- An expired or unaccepted invitation is resent in WorkOS to the same intended
  person. CareerTrellis is not seeded until invitation acceptance and verified
  email are confirmed.
- Password reset, passkey enrollment, or optional MFA recovery occurs in
  WorkOS. For the same immutable identity and an active mapping, no
  CareerTrellis database action is required.
- Resending a WorkOS invitation or logging in never reactivates a disabled
  mapping. After the operator separately verifies recovery of the same immutable
  identity, the root-only CareerTrellis `reinvite` operation is the explicit
  reactivation boundary. It accepts only `role=user`, changes only
  `disabled_at`, is idempotent for an already-active user, and refuses the owner,
  an absent row, a changed immutable identity, or any mapping with an accepted
  account-erasure request. It locks the mapping before that check so it cannot
  race the irreversible erasure boundary. The operation name does not send or
  accept a WorkOS invitation.
- If WorkOS recovery produces a different immutable identity, treat it as a new
  account. Disable the old mapping. Do not attach the new identity to the old
  tenant or move data without a separate reviewed migration.
- Owner recovery is outside the pilot-user tool. Loss or replacement of the
  configured owner identity requires a separate owner-migration review.

## Root-only live acceptance contract

The reviewed `/usr/local/sbin/careertrellis-pilot-acceptance` helper has exactly
`prepare`, `verify-active`, `verify-disabled`, `verify-recovered`, `inspect`,
`record-browser`, and `cleanup` operations. Every operation requires root and an
interactive terminal. The wrapper acquires the maintenance and acceptance
locks, binds its shell and Python files to the exact active immutable release,
checks hosted authority, activation evidence, units, process working
directories, Caddy bytes, readiness, and build, then starts the runner with an
empty environment. The runner reads only the owner identity and fixed runtime
mode from the protected staging configuration and requires that mode to be
exactly `staging`. Before any identity prompt, it parses both protected
encryption keyrings and lazily constructs the configured R2 backend without
creating a client or making an object-storage or paid-provider network call.
`inspect` performs the same offline compatibility check, so a configuration or
runtime-mode mismatch fails before `prepare` can create transient state. It
accepts no identity, URL, object key, provider value, or arbitrary path through
argv or the process environment.

The transient cleanup state is
`/var/lib/careertrellis/pilot-auth-acceptance/active.json`, root-owned mode
`0600` under a root-owned mode-`0700` directory. It contains only the release,
build, random attempt marker, one allowlisted provider-name enum, local numeric
tenant IDs, and exact synthetic fixture/object IDs needed for cleanup. It never
contains a WorkOS identity, email, credential, cookie, provider plaintext,
database URL, user content, or raw response. It is not a receipt and must be
removed only by a terminal `cleanup` operation.

Use the operations in this order:

1. `prepare` reads the one pilot WorkOS identity from a non-echoing prompt. It
   requires the configured owner mapping and target pilot mapping to be active
   with their expected roles, the pilot tenant to be empty, the worker brokers
   to be empty, and no pilot erasure request. It creates two small synthetic
   tenant fixtures, one synthetic encrypted provider value per tenant that is
   never sent to a provider, and one tiny encrypted R2 object per tenant. The
   provider namespace must be one supported namespace proven absent for both
   tenants under the protected database lock; preparation fails if no such
   namespace exists and never overwrites an existing credential. The two R2
   objects are uploaded, read back, authenticated, and retained only for the
   bounded acceptance interval. No listing or paid/provider call occurs. While
   the synthetic provider rows exist, the only allowed browser actions are the
   required login, dashboard-isolation, logout, and recovery checks; do not
   start any provider-backed application action.
2. `verify-active` proves missing-context emptiness, one-slot pool reset,
   symmetric profile/resume/job/application/write/relate/provider-secret
   isolation, disjoint owner/pilot IDs across every exported collection,
   rollback-only erasure acceptance for both tenants, authorized object
   download, denied cross-tenant download without an R2 read, and fresh idle
   search and erasure heartbeats bound to the exact checked systemd MainPIDs,
   file owners, groups, mode, and active build. The immutable release
   preparation separately runs the complete two-tenant HTTP, RLS, broker,
   disabled-work, object, export, and erasure suites against a disposable
   PostgreSQL database. The live runner never publishes a search run, claims
   worker work, calls a paid provider, or commits account erasure.
3. After the operator runs `careertrellis-pilot-user disable`, the pilot confirms
   the existing session and a fresh callback fail closed. `verify-disabled`
   confirms the selected local mapping is still `role=user` and disabled. After
   explicit `reinvite`, `verify-recovered` confirms that the same mapping is
   active again; it never assigns or changes a role.
4. `record-browser` asks only fixed yes/no questions for owner and pilot
   login/logout/recovery, pilot dashboard isolation, unlisted denial, disabled
   denial, reinvite recovery, and the privacy-log review. A `no` answer prevents
   acceptance. This step writes no receipt.
5. `cleanup` locks and deletes only rows whose exact stored IDs, owners, and
   random marker still match the active state; decrypts and value-binds both
   synthetic provider rows before deleting them; removes only the two
   predeclared R2 keys; and proves both
   objects absent. Only after all automated/browser checks and cleanup pass does
   it durably publish the sanitized receipt, then publish the adjacent digest
   manifest as the final commit marker, and finally remove transient state. A
   JSON receipt without its verified adjacent manifest is incomplete and must
   never be accepted. Cleanup before a complete acceptance is allowed as an
   abort path and writes no success receipt.

If an operation fails or the connection drops, do not rerun it immediately.
Run `inspect`, establish whether transient state and a terminal receipt exist,
and resume only the exact reviewed phase. Never repair the state, delete a
receipt, or remove an object with an ad hoc command.

## Rollback, forward correction, and containment

- The owner remains active throughout preparation and acceptance. Seed only one
  preverified pilot after the exact release is prepared and reviewed.
- Before any pilot row exists, the release may follow the normal authority-aware
  rollback policy. After a pilot row or pilot data exists, an older owner-only
  release will deny the pilot but must not delete or rewrite that row or data.
- Hosted authority, an active erasure worker, or another compatibility marker
  may prohibit rollback. Never downgrade the database or rewrite authority or
  recovery evidence to make rollback appear permissible.
- If activation or a later mutation is ambiguous, do not retry it. Reconnect,
  identify the original operation and receipt, and prove terminal state first.
  Use a reviewed forward release when rollback is unavailable.
- Forward activation and rollback must refuse to change `current` or the
  installed acceptance helper while the protected `active.json` exists or is a
  symlink. Complete or abort cleanup with the exact originating release first;
  release preparation may still run because it does not change the active
  release.
- Immediate pilot containment is: disable the exact pilot mapping, contain its
  queued work, and rotate or revoke the separate `pilot-01` Caddy credential if
  outer-gate access must be removed. Keep the owner credential and Caddy Basic
  Authentication active.
- WorkOS invitation policy, email-verification policy, password recovery, and
  optional factor settings are external state. Code rollback does not change
  them.

## Bounded pilot abuse controls

`Code-enforced` below means the current repository contains a direct bound. A
`manual target` is an operator policy, not an application guarantee. A `gap`
must never be reported as enforced in an invitation or acceptance receipt.

| Surface | Pilot limit | Code-enforced boundary | Manual WorkOS/Cloudflare/operator task or gap |
|---|---|---|---|
| Login attempts | Target: at most 5 failed attempts in 15 minutes followed by a 30-minute cooldown | The PKCE login-flow cookie expires after 10 minutes; there is no CareerTrellis or Caddy attempt counter | **Manual provider tasks:** review available WorkOS AuthKit sign-in protections; separately prepare a Cloudflare rule for the CareerTrellis origin and Basic Auth challenge traffic. Cloudflare cannot rate-limit attempts made directly on the hosted WorkOS sign-in origin. Do not change either provider without explicit approval. Until configured, the numeric target is not enforced. |
| Searches | One active search per user; target no more than 10 submitted runs per user per 24 hours | PostgreSQL permits one active run per user, a run has at most 3 attempts, requests are bounded to 500 keyword and 240 location characters, and retained candidates are capped at 25 per provider | The daily submission target and a pilot-wide queue ceiling are not metered. A privacy-safe aggregate run/status monitor is not implemented; until reviewed, any inspection must be count-only and manual. Repeated excess is a disable/containment event. |
| Concurrent work | One provider-backed operation per user and 4 pilot-wide | One active search per user; Uvicorn is bounded to 32 requests with backlog 64 | There is no unified per-user or pilot-wide semaphore for tailoring, validation, OCR, maps, export, and other work. The stated limits are manual targets until code enforcement exists. |
| Provider calls | Zero calls against operator-owned paid credentials for a pilot; routine tests make zero paid/network calls | Provider response sizes and timeouts are bounded, search retention is capped, and shared-host secret resolution is tenant-scoped | Each pilot uses its own provider credential and provider-account quota. CareerTrellis has no per-user call meter or circuit breaker; verify provider budget/quota controls manually and never share the owner's key. |
| Stored objects | 10 MiB per object; manual target 100 objects or 250 MiB total per user, whichever comes first | Plaintext objects larger than 10 MiB are rejected and object access is owner-bound | Per-user count and aggregate-byte quotas are not enforced. Use only privacy-safe aggregate count/byte monitoring; no object names or keys. |
| Exports | Target one concurrent export, at most 3 per user per hour, and 25 MiB output | Export is a single owner-scoped snapshot and is returned `no-store` | There is no export rate, concurrency, record-count, or output-byte limiter. These targets are gaps until tested code enforcement exists; invitation-only scope is not evidence of enforcement. |
| Account-erasure requests | One accepted irreversible request per account | The request is owner-scoped, idempotent, disables the mapping, uses one durable request, clears the CareerTrellis session, and is consumed by a single leased worker | There is no separate HTTP attempt throttle. Prove disabled-user queue/provider containment before treating the first accepted request as a complete abuse boundary. |

Cloudflare and WorkOS tasks are review inputs only. The operator must record the
available control, intended match scope, privacy impact, failure behavior, and
rollback before authorizing any external change. Monitoring must remain
aggregate-only and must not retain identity-provider paths, email, immutable
identity, raw IP/User-Agent, search terms, object keys, or tenant content.

## Privacy-safe receipts

Pilot authorization receipts are operational evidence, not repository files.
The fixed root required by this contract is
`/var/backups/careertrellis/pilot-auth-acceptance`, owned by `root:root` with
mode `0700`. A reviewed installer or receipt writer must create and validate
that root before acceptance; do not improvise it with an ad hoc shell capture.
Receipt and digest-manifest files are `root:root` mode `0600`, are created
without following symlinks, and each is published without overwriting an
earlier attempt. The pair is not a single atomic filesystem operation: the
receipt is durably published first and the manifest is durably published last
as its commit marker. The filename contains only UTC time, an allowed operation
name, and a random attempt identifier; it contains no person, email, WorkOS
identity, database row ID, or tenant identifier.

Each JSON receipt uses schema `careertrellis.pilot-auth-acceptance/v1` and only
these fields:

- `schema`, `operation`, `result`, exact 40-character `release`, `build`,
  `started_at`, and `completed_at`;
- `subject_kind`, limited to `owner`, `pilot`, `unlisted`, `disabled`, or
  `missing_context`, never a unique subject reference;
- `http_status_class`, when applicable, never a URL, path, code, or header;
- a `checks` object containing only named booleans such as
  `invitation_only`, `email_verification_required`, `password_login_allowed`,
  `mfa_required`, `mapping_present`, `role_expected`, `active`,
  `session_issued`, `local_row_created`, `cross_tenant_exposed`,
  `pooled_context_cleared`, `auth_routes_logged`, and
  `forbidden_field_present`;
- an optional `counts` object whose keys are a closed aggregate-only allowlist
  and whose values are bounded nonnegative integers; and
- optional `notes` containing only reviewed enum values such as
  `basic_auth_retained`, `mfa_optional`, and `synthetic_data_only`, never free
  text.

Unknown checks are omitted, never converted to a passing `false`. Receipts must
not contain identities, emails, timestamps from user records, database URLs,
tokens, cookies, callback codes/state, provider keys, object keys, search terms,
profile data, raw logs, exception text, IP addresses, User-Agent values, or
hashes/fingerprints derived from any of them.

Maintain an adjacent SHA-256 manifest over immutable receipt filenames and
bytes. Consumers must start from the manifest, verify the exact adjacent
canonical JSON filename and digest, and ignore any JSON that has no valid
manifest. After acceptance, copy the sanitized receipt and manifest to the
operator-controlled encrypted evidence store and perform a readback. The
independent store's path and access method remain outside the repository. Do
not paste receipt contents or digests into chat, a commit, or a public CI log.

## Invitation gate

No real pilot data is permitted until one synthetic two-user acceptance run has
proved owner and pilot login/logout/recovery, unlisted and disabled `403`,
missing-context and pool-reset negatives, symmetric live data/object/export/
provider-secret/erasure isolation, exact worker-process liveness, and the full
privacy-log forbidden-field check. The manifest-backed live receipt must be
combined with the same immutable release's passing HTTP, PostgreSQL/RLS,
broker/worker, disabled-work, owner-role, object, export, and erasure tests; the
receipt must not claim those separate test properties as live observations.
Invite additional friends one at a time only after the preceding user's seed,
login, isolation smoke, and sanitized receipt are accepted.
