# Factor removal coordinator contract

`backend/factor_removal.py` is an inert, injected protocol implementation. Protected
HTTP handlers and a bounded provider transport now exist, but normal runtime
construction leaves removal disabled. The local candidate includes restricted
PostgreSQL store/wrappers through0020 and synthetic/native verification. None of
this candidate has been deployed.

The original account, incarnation, epoch, provider, subject, session, browser,
operation, factor and challenge remain bound throughout mutation. Signed freshness
is under 300 seconds, checked before and after eligibility awaits. Possession
proof lasts less than 30 seconds; five explicit invalid-code attempts are allowed.
Codes and raw responses are not persisted. Verification success is consumed into
the deletion claim without a reusable intermediate proof endpoint.

Each provider mutation requires an acknowledged durable claim first. Uncertain
commit, cancellation, timeout or malformed response never permits replay. Only
an explicit bound invalid-code result permits another charged verification.
Completion requires a durable exact-operation deletion acknowledgment followed by
complete authoritative absence. Absence alone cannot attribute a lost response.
A fresh session with the same immutable authorization may inspect/reconcile;
it cannot resume a mutation. Completion does not release security ownership.

## Required production integration

- Restricted PostgreSQL reserve/CAS wrappers now check current authority, exact
  actor, legal transition, deadline, budget, slot and proof in one transaction.
  Native tests cover these checks and private grants. Preserve lifecycle/session/
  account mutex/slot lock ordering in future changes.
- Transfer a predecessor slot only through independently verified terminal effect
  disposition. Expiry, enrollment completion or a missing response is not transfer
  authority. Preserve enrollment/recovery exclusion and historical evidence.
  Additive0019 now permits one enrollment-to-removal transfer only for future exact
  completed enrollment carrying an unconsumed application-observed disposition
  receipt. The new inert AttributableEnrollmentCoordinator records it only after
  a literal positive bound challenge response and complete subject/factor ownership
  readback. Receipt and enrollment completion commit together; receipt consumption,
  removal claim and slot transfer commit together. Historical complete records,
  list membership alone and uncertain responses never acquire a receipt. Prior0018
  remains unchanged and refuses all enrollment slots. No release or TTL takeover
  exists, and removal completion still retains the slot.
- Bind the fixed provider transport to authoritative factor ownership, challenge,
  verification and deletion semantics. No redirects, automatic retries, alternate
  factor sampling or HTTP-status-only success. Dataclasses are trusted internal
  transport results, never request-body evidence.
- Add authenticated routes, same-origin CSRF and purpose-bound form handling only
  after durable wrappers and transport are reviewed. Local transport and protected
  removal routes are now implemented and synthetically verified, but runtime factory
  construction leaves them disabled.0020 records initiating session-key hashes and
  atomically revokes initiating/completing local sessions on completion; fresh callers
  finalizing an already-complete operation also end their local session. Only the
  SessionEndingRemovalCoordinator satisfies that HTTP success contract. No provider
  or global session revocation is implied.0020 refuses existing removal rows because
  missing initiating hashes cannot be safely reconstructed.
  Existing app/routes still use the older enrollment coordinator. The new coordinator
  and disposition store are intentionally unwired; selecting them requires the
  operational/schema preparation and integration checks. Do not treat the legacy
  is_verified method name or a legacy complete state as a verification receipt.
  WorkOS deletion status/body acceptance still needs attended live validation: the
  inert transport currently accepts the official SDK fixture's200 whitespace-only
  response, treats other statuses as uncertain, and never retries them.
- Test actual PostgreSQL role/grant denial, concurrent enrollment/removal/recovery,
  crash and acknowledgment boundaries; then perform separately scoped hosted and
  attended acceptance. General lost-factor recovery remains separate work.

At-most-one application invocation does not imply exactly-once provider delivery.
This module alone does not clear beta identity or recovery acceptance.
