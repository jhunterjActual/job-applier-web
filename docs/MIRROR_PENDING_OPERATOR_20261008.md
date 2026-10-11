# Historical operator handoff: credential entry completed

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.

**Superseded by [verified deployment and resource handoff](MIRROR_PENDING_DEPLOYED_20261008.md).**
Credential entry, reporter activation, provider timing and evaluator deployment
are complete. Do not repeat the steps below; they are retained as historical evidence.

Operational source: `fcfeb0bba27453db1d7300fb9abaf32497ff28e1` on local branch
`codex/mirror-pending-monitor-20261008`. Not pushed or published as a PR.
The exact public bundle is staged **inactive** at
`/opt/careertrellis/staging/releases/fcfeb0bba27453db1d7300fb9abaf32497ff28e1`.
No application pointer, live evaluator, reporter configuration, timer or backup
was changed. PostgreSQL and the monitor timer remained active.

## Operator step now

Create one NEW check in [Healthchecks](https://healthchecks.io/checks/):

- Name: **CareerTrellis database monitor heartbeat**.
- Period: **1 hour**. Grace: **20 minutes**.
- Connect the existing email integration used by the database backup check.
- Use a distinct check, not the current database backup, object backup, or
  admission check. Leave all existing check settings unchanged for now.

The new check's displayed **Ping URL** is a new credential. No existing saved
Bitwarden item has been identified for it. Enter it only at the hidden prompt
in your visible terminal; do not send it in chat.

From your normal PowerShell terminal, run:

```powershell
ssh -t -o BatchMode=yes -o ConnectTimeout=15 -o ForwardAgent=no -o ClearAllForwardings=yes -o StrictHostKeyChecking=yes hunter@157.245.113.244 "sudo -n /bin/bash /opt/careertrellis/staging/releases/fcfeb0bba27453db1d7300fb9abaf32497ff28e1/deploy/scripts/manage-backup-reporter.sh enter-heartbeat"
```

The helper asks for `VISIBLE`, then `VERIFIED`, then the new Ping URL at a hidden
prompt, then `SAVE`. Ordinary paste and balanced terminal bracketed paste use the
existing verified secure-input implementation. Saving writes only the new
root-only `/etc/careertrellis/backup-heartbeat-healthchecks.json`. It sends no
ping and does not activate the new monitor.

Report only `heartbeat_configuration_saved_not_activated`, or the sanitized
error message. Never send the URL. The repo's interactive-secret instruction
requires operator-started visible terminal entry; deployment approval is already
given and does not need to be repeated.

## What follows

After secure entry, the agent will refresh all installed-byte and service guards,
activate the new reporter first and verify both acceptances. Only after the new
hourly heartbeat is active should the existing database outcome check change
to period **4 hours**, grace **20 minutes**. The independent heartbeat preserves
hourly outage detection; the outcome interval accommodates the bounded pending
window. Then the evaluator/service change can be activated and verified.
No provider settings have yet changed. Backup-to-pending-to-mirror-to-healthy
scheduled-cycle acceptance remains required after activation.

## Evidence and retained resources

- Exact staged bundle receipt (local-only evidence: `../artifacts/mirror-integration-20261008/staged-verification.json`):
  106 native tests passed, shell/systemd checks passed, installed baseline
  unchanged, PostgreSQL and monitor timer active. Source bytes: 340,522.
- Initial native receipt (local-only evidence: `../artifacts/mirror-integration-20261008/native-verification.json`):
  105 tests passed before the final malformed-schema case. Public validation
  directory `/opt/careertrellis/staging/mirror-pending-validation-20261008`,
  333,717 source bytes plus receipt.
- Windows receipt (local-only evidence: `../artifacts/mirror-integration-20261008/local-verification.json`):
  459 checks, no assertion failures, four skips, one existing CLI subprocess
  timeout; syntax passed, temporary data removed. Final focused 18 tests passed.
- Deployment/rollback design (local-only evidence: `../artifacts/mirror-integration-20261008/MIRROR_PENDING_DEPLOYMENT.md`).

Retain both small remote source directories and receipts through activation and
scheduled-cycle acceptance. Owner: CareerTrellis backup maintenance; review then,
not automatic deletion. Host free space after staging: 55,706,128,384 bytes.
No Docker resources, application restarts, paid provider calls or backup-data
mutations occurred. Native transient test units were collected on completion;
test data used their private temporary directories. Local source/receipts are
retained in `artifacts/mirror-integration-20261008` and the attached worktree until
the operational change is accepted or superseded.
The local evidence directory currently holds about 1.6 MiB (20 files), including
source-bound staging payloads, installed-source snapshots and verification scripts.
