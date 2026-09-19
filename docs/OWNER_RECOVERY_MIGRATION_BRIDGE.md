# Owner credential migration bridge candidate

Status: implementation and additive installation package prepared; live
consumption requires the attended transition. The owner-bootstrap credential remains
active on the web host. It was not read, consumed, rebound or replaced here.

## Exact boundary

The legacy credential binds the original web/database deployment, database OID,
configured source client/subject, owner account ID and creation timestamp. The
historical preimage deliberately retains literal schema0014. After0015/0016,
the bridge reconstructs that same preimage from unchanged source records and
compares it with the enrolled digest; it does not silently change the credential.

The bridge independently checks the full currently configured source provider
binding (including generation), selected approved P plan, target owner proof,
policy generation, closed maintenance and exact held owner context. That context
includes the real incarnation, epoch, source and target identities, operation,
predecessor and deadline. The credential must have been confirmed before the
transition started. No email matching or invented incarnation is accepted.

Private code entry occurs without a database transaction or host-operation lock
across human input. The credential lock serializes attempts. Immediately afterward
the bridge reacquires the source/plan and database lifecycle locks and rejects any
changed selection or context. An attempt is durably charged before verification.
Success commits consumed state and its complete version2 receipt together. Exact
receipt recovery cannot extend time, undo consumption or follow a successor plan.
The receipt expires at the earlier of five minutes or the held operation deadline.

## Evidence sequence and trust

During a separately authorized attended transition:

1. Establish the exact held operation and verify fresh target authentication and
   applicable-factor disposition through their existing independent procedures.
2. Personally consume the existing owner code through the reviewed bridge on the
   web host. This establishes holder evidence only.
3. Complete the required source and target provider-session revocations after
   holder and target verification. Retain their actual times.
4. The separately named `build_with_verified_holder_receipt` builder uses a trusted
   source-verification callback, fixes holder completion to receipt issuance and
   caps manifest expiry by receipt expiry. It keeps separate target authentication,
   factor, revocation and owner-approval prompts and revalidates after input.
5. `owner_recovery_bridge.enter` saves a root-protected outcome manifest on the WEB
   host after a final live check. The bridge-specific `attest` action reads that
   same intake and verifies the consumed receipt inside the exact database
   transaction that records evidence. The account remains held. Existing journal
   and historical reconciliation handle uncertain acknowledgement without
   reconsumption or extending proof validity. Version2 manifests retain operator
   assertion semantics for target authentication/revocation/owner approval.

The trusted callback must verify the protected consumed credential and current
source plan/context, as `verified_for_entry` does on the credential host. Parsing
a JSON file, its digest, or root ownership on another host does not authenticate
its origin. Do not plug in a callback that merely returns imported JSON.

Correction to the previous handoff assessment: the existing identity intake and
attestation launcher both run on the WEB host. Database attestation uses its
existing protected migration connection; no separate manifest file transfer to
the DB host is needed. The bridge guard and attestation share one database
transaction and lifecycle lock. Activation-receipt transfer formats are unrelated.

The new `install-owner-recovery-bridge.py` installs a separate root-only immutable
source tree and `careertrellis-owner-recovery-bridge` launcher using the previously
verified private runtime. Installation compares application/configuration and
credential state before/after, runs native synthetic tests, and checks the active
0014 binding. It never migrates or consumes. The historical credential installer
is used only for its verified prerequisite helpers, not its install function.

Before consuming, both selected plan operations revisions must equal this bridge
artifact revision. The launcher enforces this before code entry and after input;
attestation checks it again in its transaction. Plan/coordinator alignment and
full attended sequence timing remain required. Do not change approved selection
solely to bypass a revision mismatch.

If revocation, handoff or input exceeds the deadline, keep the account held and
the credential consumed. Use reviewed forward recovery; do not extend receipts,
backdate evidence or reset the code. Following migration, a fresh credential must
be enrolled under the resulting identity/incarnation through a reviewed process.
This bridge does not supply normal factor removal or general lost-factor recovery.

## Local acceptance

Submission/package continuation:83 Linux tests passed without skips,49 combined
PostgreSQL tests passed without skips, and required P0371 passed with two existing
skips. Compilation and synthetic startup/default-off/graceful-stop checks passed.
New real-role cases cover successful guarded attestation with the account held,
changed holder time, expired proof and wrong installed source; journal regression
confirms historical reconciliation never revalidates or consumes expired proof.
Independent bounded reviews found no installation/submission blocker. Earlier
bridge-only acceptance follows for historical comparison.

- 73 Linux tests passed without skips: credential state, exact receipt binding,
  source reconstruction, interrupted writes, replay, evidence expiry and real
  hidden-terminal paste/restoration, including the guided identity prompt.
- 45 PostgreSQL18 tests passed without skips: four new real-role bridge checks
  plus existing identity/TOTP enrollment regressions. The bridge queries were
  read-only; source account/hold/policy rows remained unchanged.
- Required P0/object suite:371 tests passed with two existing skips.
- Actual private credential/source account data never entered these fixtures.

See the current installation receipt for deployed status and final test counts.
Live provider/transition acceptance remains open. This is not final beta acceptance.
