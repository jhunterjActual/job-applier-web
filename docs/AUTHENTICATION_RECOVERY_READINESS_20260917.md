# Authentication and recovery readiness — September 17, 2026

This is an application candidate and recovery implementation plan, not beta
activation acceptance. Signup/session-v2/authenticator enrollment remain disabled
in the live .71 application. Database monitoring was separately activated from
operations revision a75050d; it does not supply an application release receipt.

## Established decisions and unresolved evidence

The operator clarified that **no recovery credential has been established**.
The earlier answer described a desired future user-recovery method, not existing
owner evidence. Current owner migration proof and future user recovery are
separate work. Neither is accepted by that earlier answer.

Recommendation for future recovery: application-issued single-use random recovery
codes, enrolled while the exact user can still authenticate with any existing
factor enforced. Store only account-bound hashes server-side, use bounded attempts
and atomic single-use consumption, notify on issuance/replacement/use, and keep
the saved codes offline or in an independently accessible password manager.
Use at least 128 random bits per code. Do not place codes in chat, logs, receipts,
source files or the affected account's email as the sole retained copy.

This facility is not implemented and no code has been generated. Creating a code
now cannot retroactively establish independent ownership for the current migration.
The first owner enrollment needs a separately reviewed bootstrap that binds the
current account and owner authorization using already trusted evidence, then
confirms custody privately. The actual bootstrap evidence remains undecided.
See [NIST saved recovery codes](https://pages.nist.gov/800-63-4/sp800-63b.html)
and [OWASP MFA recovery](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html).

Once this facility exists, before recovery privately establish that the credential predates the incident and
belongs to the exact local account/incarnation. Verification must occur through
its established trusted mechanism, with bounded attempts and operation-bound
proof. Single-use credentials require atomic consumption/replay protection;
reusable credentials require their documented revocation/rotation handling.
An unrecognized, newly supplied, expired or revoked credential leaves recovery
blocked. Do not invent a verifier or issue a substitute to satisfy the gate.

Optional MFA, the 3600-second session lifetime, managed Production hostname and
owner-only migration remain settled. The six legacy pilots start fresh; export
confirmation remains waived/unverified and account deletion is not authorized.

## Implemented boundaries

- Signed recent authentication, exact account/session/browser binding, epoch and
  incarnation checks, CSRF and restricted PostgreSQL authorization for enrollment.
- Durable enrollment claims before provider calls, bounded explicit-failure
  verification retries, encrypted temporary setup material and retained uncertainty.
- Identity holds and epoch increments, exact protected outcome attestation,
  forward-only recovery after expiry, and old-session denial across release.
- Separate bounded ciphertext cleanup and versioned monitoring are installed on
  the DB host. Healthy cleanup still requires compatible 0016/released-v2 state.

Identity evidence intake records assertions made by an authorized operator; it
does not itself verify a recovery credential or execute provider recovery.
The existing synthetic same-identity hold/recovery test covers application
containment and data preservation, not lost-factor removal or real holder proof.

## Implementation still required before enabling enrollment for beta

Normal removal needs its own durable operation, separate from enrollment records:
exact account/incarnation/epoch/provider/factor/session/browser binding; verified
factor ownership; an immediately preceding challenge of that same factor; bounded
attempts; and a committed mutation claim before one DELETE. Reject stale/replayed
challenges and unknown ownership. An uncertain response remains pending for
read-only reconciliation, never automatic repeated deletion or blanket removal.

Assisted lost-factor recovery needs a separate protected coordinator:

1. Validate exact recovery scope and independent credential through the established
   verifier. Include separate owner authorization where applicable.
2. Hold the account and increment its epoch before any provider mutation, preserving
   profiles and all retained enrollment/identity operation evidence.
3. Persist the exact-factor mutation claim. Execute only the separately authorized
   provider change and scoped provider-session revocation. Keep ambiguous outcomes
   held and reconcile from authoritative evidence without blind retries.
4. Require fresh authentication, verified resulting factor disposition, complete
   scoped revocation and current protected evidence before releasing the hold.
5. Consume/rotate the recovery credential according to its established contract.
   If evidence expires, retain the hold and use the reviewed forward-resume path.

Do not repurpose completed enrollment rows, clear uncertain slots, lower the
session floor, restore a previous epoch or expose a public Google-only recovery
bypass. Separate durable records and restricted grants must be tested for
cross-account access, worker denial, concurrent enrollment/removal/recovery,
revocation races, expiry, wrong/replayed proof, crashes and ambiguous responses.

WorkOS documents exact-factor challenge and irreversible deletion separately:
[challenge API](https://workos.com/docs/reference/mfa/challenge) and
[factor API](https://workos.com/docs/reference/mfa/factor). The accepted provider
support receipt requires exact-factor possession immediately before normal
removal; REST deletion does not perform that end-user challenge automatically.
No provider call or factor mutation was performed during this preparation.

## Concrete advance acceptance appointment

After local removal/recovery implementation and native acceptance, privately
establish owner bootstrap proof, enroll recovery codes through the implemented
trusted process and confirm actual owner pending-factor disposition.
Use the approved exact environment/account to observe signed Google freshness,
voluntary enrollment, wrong-code handling, subsequent Google plus verified-TOTP
enforcement, normal removal and assisted recovery. Retain sanitized results only.
Complete bounded callback/session cleanup under that appointment's authority.

Protected web-only TOTP key delivery, worker exclusion, exact application host
preparation, protected B/P plans and enabled-v2 acceptance remain distinct gates.
No outage date is agreed and no live migration or signup opening is authorized by
this document. The application artifact can be frozen for verification but cannot
be described as final beta-ready while these gates remain open.
