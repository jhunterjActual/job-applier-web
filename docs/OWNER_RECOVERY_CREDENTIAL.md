# Owner bootstrap recovery credential

This separate administrator tool enrolls a single-use owner credential before
the application transition. It does not implement public account recovery,
remove factors, revoke sessions, change owner mappings, release holds or activate
the application. It accepts only the existing enabled source owner on schema0014
with signup and session-v2 disabled. Source matching uses protected configuration
and a read-only database transaction; no personal identifiers are displayed.

The operator confirmed ownership and personally used the existing SSH keys on
both droplets without a passphrase. They then backed up these keys in their
password manager. This is an administrative bootstrap basis, not proof from a
previously enrolled recovery credential. The SSH keys remain infrastructure
credentials and are never imported into CareerTrellis.

## Private enrollment

In a personal, unrecorded SSH terminal on the web droplet, run:

```sh
sudo /usr/local/sbin/careertrellis-owner-recovery enroll
```

Enter OWNER at the hidden confirmation prompt. The utility generates 256 random
bits, displays the code only to the controlling terminal, and requires hidden
re-entry from the saved copy within five minutes. Save it as a separate password
manager item named “CareerTrellis owner bootstrap recovery — staging source”.
Do not paste the code into chat, an agent terminal, screenshots or support logs.
Terminal scrollback contains the displayed code; close/clear that terminal after
saving it. Confirmation proves custody; do not consume the code merely to test it.

If interrupted after saving but before confirmation, `confirm` can complete the
same pending generation within its five-minute window. Never run enrollment
again to overwrite a pending or active record. `status` returns only the state.

The hidden prompts accept ordinary and balanced terminal bracketed paste. Paste
only the single-line value; multiline input or other terminal controls refuse.
After an expired unconfirmed issuance, `replace-pending` is a separate attended
administrative procedure: personally authenticate through existing SSH, type
OWNER and REPLACE-PENDING, then save and confirm the newly generated code. It
requires the unchanged owner binding, archives the old unconfirmed verifier as
revoked, and durably creates a new random generation. It never extends the old
deadline or replaces an active/consumed credential. Update the password-manager
item with the NEW code; the previous unconfirmed code is invalid.

Only the salted, account-bound hash is stored under the root-only directory
`/var/lib/careertrellis-owner-recovery`; runtime and worker users cannot read it.
Both valid and invalid verification attempts are charged durably before checking
the secret. Five attempts exhaust the generation; attempts are at least 30 seconds
apart after the first failure. Restarting does not reset the budget.

## Single-use evidence, not automatic recovery

`consume` is for a separately reviewed attended recovery operation. It privately
asks for the exact operation UUID and code, rechecks the source binding, and
atomically consumes the credential before making a five-minute protected receipt
available. `receipt` retrieves that same unexpired operation's receipt without
another effect. It does not expose identifiers or raw proof. A different operation
cannot reuse it. No downstream failure restores a consumed code.

The protected receipt is bootstrap evidence only. Existing identity intake does
not automatically trust it. A reviewed coordinator must compare its exact source
binding and requested operation; do not manually substitute it for all holder,
target-authentication, owner-authorization or revocation checks.

Schema0015/0016, provider changes, disabled/erasing owner, maintenance, changed
database/account creation identity or session-v2 activation cause refusal. There
is no invented incarnation in0014. A later explicit source-to-incarnation bridge
or freshly authorized enrollment is required before use after migration.

## Lifecycle and backup boundaries

There is deliberately no automatic reset/rotation command. The narrowly scoped
expired-pending replacement above requires explicit personal confirmation.
Exhausted active attempts, lost active codes or consumed credentials require a
separately reviewed replacement/revocation procedure with independently verified
administrator control. Do not edit counters, delete state or restore old state
to get another attempt. Do not claim this completes lost-authenticator recovery.

Restoring an old sidecar backup can resurrect consumed codes. After any host or
credential-state restore, treat all restored credentials as invalid and require
fresh authorized enrollment, unless an independent current consumption ledger
proves their status. No automatic off-host backup or non-rollbackable ledger is
provided by this utility. Keep the owner's secret separate from server backups.

The wrapper pins the existing private operations interpreter at revision
45e33567bc9e23746ea5ba8f929e14b180288576. The installer checks its protected
installation receipt, complete runtime ownership/write permissions and resolved
interpreter before root execution. That operations release/runtime is a
live dependency of this tool and must not be removed by retention cleanup. The
small immutable tool source and root-owned state are retained until a reviewed
replacement, migration bridge or decommissioning. No server service/timer is added.
