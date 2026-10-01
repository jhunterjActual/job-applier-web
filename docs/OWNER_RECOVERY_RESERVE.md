# One reserve credential for the owner migration

This is a separate, single-use credential enrolled by the owner before the
original migration starts. It can authenticate only the immediate successor of
the original expired, uncommitted transition. It does not renew the primary
credential, provide another authentication factor, or support an arbitrary
account recovery. The optional-MFA policy remains unchanged.

## Deployment boundary

The reserve coordinator is a separate operations artifact. Existing application
releases, selected B/P operations revision, original credential and installed
bridge remain unchanged. Local implementation and tests do not mean that the
helper is installed or a reserve exists. Install only the independently reviewed
source closure, with its exact source manifest and protected launcher. Do not
patch an existing immutable release or reuse its old manifest for new bytes.

The fixed reserve namespace is
`/var/lib/careertrellis-owner-recovery-reserve`, root-owned mode 0700; its files
are root-owned, single-link mode 0600. There is no command-line storage override.
Keep the primary namespace `/var/lib/careertrellis-owner-recovery` intact.

The additive installer accepts `install|inspect COMMIT ARCHIVE_SHA MANIFEST_SHA
LAUNCHER_SHA RUNTIME_TREE_SHA`. These are public source pins, not credentials.
Its exact reviewed source must be staged under a root-protected directory;
the fixed archive upload is `/var/tmp/careertrellis-owner-reserve-<commit>.tar`,
root-owned mode 0600. Never run an unverified installer from a writable directory.
Before using the installed Python runtime, it compares that runtime with the
retained approved `4a7bf3c46d3b401616c7bfaefacb22c7c7ff30ab` source runtime,
using the original relocation rules. The runtime hash supplements that check.

Installation requires the original active primary, original P selection,
`.78`/schema0014 and protected existing locks. It records an exclusive attempt
before writing, compares primary/configuration/service state, creates only new
paths, verifies source-only loading and publishes the launcher last. It does
not enroll a credential. Failed attempts remain for read-only `inspect`;
inspection never resumes or authorizes a replay. A recorded successful install
is historical evidence and still requires current live verification.

## Before the original attempt

1. While the exact approved `.78` application and schema `20260907_0014` remain
   active, with maintenance off, closed signup and the original owner binding,
   enroll and confirm the reserve in the owner's visible, unrecorded SSH terminal
   on **the staging WEB host**. Do not enter a credential in chat or an automated
   terminal. Store the displayed code privately and separately; the host stores
   only a salted verifier. Enrollment records the original selected P packet and
   primary credential identity. An interrupted issuance burns the reserve slot;
   the helper cannot generate a replacement.
2. Complete the established migration/B/P preparation gates. Selecting B and then
   P advances the selection sequence, so the enrollment selection alone is not
   the selection that the primary proof will use.
3. Immediately before consuming the primary, use the one-time `arm-primary`
   action to freeze the exact current selected P bytes. This requires the primary
   still active, the reserve confirmed, and the same originally enrolled packet.
   The attended approval identifies that exact selection. Preserve its immutable
   `primary-plan.json`. A later changed selection needs review, not replacement
   of this record.

## If primary proof expires before identity commit

Preserve the primary's consumed record, receipt, original packet, intake and
journal. Reconcile a lost attestation acknowledgement using the existing
historical status/reconciliation actions before attempting recovery. An expired
proof is not evidence that an earlier transaction failed.

The original transition's own deadline must also expire before the existing
resume operation is available. Its normal hold is 55 minutes; expiration of the
five-minute credential proof does not shorten that hold. Use the approved
transition resume and plan supersession procedure, re-enter the encrypted
candidate, and converge the selected P plan on both hosts. The reserve accepts
only one immediate successor: identical owner, incarnation, source and target,
with epoch increased by exactly one and the original packet as the direct parent.
An already committed or ambiguous original result blocks reserve use.

Approve the exact successor selection once. The helper verifies its frozen
approval, source files, stopped services, fenced database roles, migration
evidence, current owner hold, candidate and original acknowledgement. Its
database-host plan check is read-only. A pinned relay on the operator's computer
uses the existing authenticated SSH connection to each host separately. The WEB
host makes no SSH connection to the database host. Host identity, source closure,
independently approved scope and execution permissions must all match.

Each before-and-after check creates a fresh, single-use challenge under the
protected `convergence` subdirectory. The relay reads the actual DB selection and
activation through verified source, rechecks the challenge, then publishes an
exact matching response through the narrow WEB helper. Root ownership or JSON
syntax alone is not database provenance: the pinned operator relay is the trusted
issuer. Responses cannot be reused for another check or become holder proof.

The machine exchange has a 30-second bound, and the operator relay runs for at
most 15 minutes. Neither is a new timed prompt for the owner. They do not extend
the five-minute holder proof. A failed exchange stops; lost acknowledgement is
inspected without republishing. These are sampled before-and-after observations,
not a distributed lock on the DB selection for the entire attended action.

Run the operator driver directly from its verified source package with isolated
Python; do not import an unverified cached module. Independently approve the exact
manifest and scope-file hashes. Its dedicated local journal stores only request
and observation digests; keep that directory under the operator's control and
verify its permissions before operational use. Its filesystem checks do not
constitute a Windows ACL audit. No new SSH key, forwarding rule, peer access or
firewall exception is needed by this design.

Authenticate freshly in the successor context, then personally consume the
reserve. Enter and attest fresh target authentication, authentication-method and
factor disposition, complete source/target session revocation and exact owner
approval through the attended procedure. Finish P identity/configuration commit
before the reserve proof expires: at most five minutes after consumption, capped
by the successor deadline. This deadline applies to activation, not just entry
or attestation. The existing database expiry checks remain authoritative.

The coordinator keeps lock order primary credential, reserve credential, host,
then database. It rechecks the predecessor and reserve proof during consumption,
after attended entry, and inside the actual attestation transaction. It does not
hold host/database locks while waiting for terminal input. Historical status and
reconciliation can inspect an old exact acknowledgement after expiry, but cannot
submit new evidence or renew activation authority.

## Exhaustion, interruptions and retention

There is one reserve slot, with the existing bounded attempt count and cooldown.
There is no third credential, automatic retry, replacement, timer reset or
multi-hop recovery. After a lost response, inspect the exact durable state before
any further action. Missing or ambiguous evidence, another expiry, or exhausted
attempts leaves maintenance and the hold intact for a new reviewed decision.

Retain all primary and reserve metadata, receipts, approvals and journals as
recovery evidence. An unused reserve also requires an explicit retirement and
retention decision after successful migration; no automatic deletion is allowed.
Owner: CareerTrellis operator. Review when the migration is committed or the
attempt is abandoned. The implementation itself creates no Docker resources.
