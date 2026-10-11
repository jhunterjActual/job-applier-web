# Database mirror monitor deployment: October 8, 2026

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.

Activated and live-verified at **12:52:05 UTC / 8:52:05 AM EDT** on the existing
database host. **October 9 follow-up:** the scheduled cycle is now verified in
host journals and accepted deliveries: pending at04:02:47 UTC, then healthy at
05:03:30 UTC, with heartbeat accepted in both states. See the
overnight evidence and separate alert diagnosis (local-only evidence: `../artifacts/HEALTHCHECKS_DIAGNOSIS_20261009.md`).

## Installed source and verified behavior

- Evaluator, wrapper and service: `1229ed9bce42c66f6d4b613e56abdfeb00da347d`.
- Reporter and its two protected credential bindings:
  `fcfeb0bba27453db1d7300fb9abaf32497ff28e1`.
- Both revisions are local commits on `codex/mirror-pending-monitor-20261008`;
  neither was pushed or published as a PR.
- Activation receipt (local-only evidence: `../artifacts/mirror-integration-20261008/evaluator-activation.json`)
  binds installed hashes to fresh evaluator and reporter invocation IDs.
  Evaluation at 12:52:02 UTC was healthy; both provider deliveries were accepted.
  PostgreSQL and the monitor timer were active; backup timer definitions,
  credential values and WAL baseline were preserved.
- Provider readback (local-only evidence: `../artifacts/mirror-integration-20261008/provider-settings.json`):
  **CareerTrellis staging database backup** uses 4 hours plus 20 minutes;
  **CareerTrellis database monitor heartbeat** uses 1 hour plus 20 minutes.
  Both retain email alerts and received the fresh live result.

Pending requires the verified predecessor-generation mirror gap, otherwise
healthy evidence and prior clear alert history. Its fixed deadline is the earliest
of 05:15 UTC, backup completion plus three hours, or last clear evaluation plus
three hours. Pending never refreshes that anchor, clears a failure, resets WAL
history, or reports a successful backup. It sends a neutral log; the independent
heartbeat reports observation availability. The last-clear cap closes the
early-backup/hourly-jitter edge found during final review.

The application release and WEB/object monitor were outside this deployment.
No application restart, backup run, backup-data mutation, paid API request,
or Docker resource creation occurred.

## Credential and provider steps completed

The user created the new heartbeat check, entered its new **Ping URL** through
the attended hidden terminal prompt, and reported
`heartbeat_configuration_saved_not_activated`. The agent verified root-only
configuration and distinct capabilities, then activated the reporter first.
Do not repeat credential entry. The new configuration is
`/etc/careertrellis/backup-heartbeat-healthchecks.json`; the existing outcome
configuration is `/etc/careertrellis/backup-healthchecks.json`. No credential
values are recorded here, and no Bitwarden item name has been invented.

After heartbeat activation, automatic approval review required explicit authority
for changing the existing outcome interval. The user approved the exact 4-hour
period with unchanged 20-minute grace and email alerts. UI readback verified the
save before evaluator activation. That approval issue is resolved.

## Verification and remaining acceptance

- Final native validation (local-only evidence: `../artifacts/mirror-integration-20261008/staged-verification-v2.json`):
  108 tests passed in 13.283 seconds on Ubuntu/systemd with test networking
  disabled, including terminal paste, CLI, pending and reporting cases.
  Shell syntax and systemd unit verification passed.
- Final local validation (local-only evidence: `../artifacts/mirror-integration-20261008/local-verification-v2.json`):
  391 P0/object/pending checks passed, two skips, zero failures/errors; backend
  syntax passed and temporary data/bytecode were removed.
- Earlier 106-test native and Windows 459-check receipts remain historical.
  The earlier Windows run had one existing CLI timeout; final native validation
  passed those cases without relaxed timeout limits.
- Reporter activation (local-only evidence: `../artifacts/mirror-integration-20261008/reporter-activation.json`)
  and evaluator activation (local-only evidence: `../artifacts/mirror-integration-20261008/evaluator-activation.json`)
  verify actual delivery rather than only mocked transport.

Historical acceptance plan, completed for the normal October 9 cycle above.
Owner: CareerTrellis backup maintenance. At the next overnight scheduled cycle,
verify the new backup creates only bounded pending, a neutral outcome log does
not reset backup success, the heartbeat remains fresh, and a verified mirror
returns the outcome to healthy. Review unexpected alerts or missed deadlines.
Synthetic overdue/failure cases passed; failures were not manufactured in
production. No future automation was created by this task.

## Retained resources and rollback

This is the authoritative resource handoff for this monitor deployment.
Measured inventory (local-only evidence: `../artifacts/mirror-integration-20261008/activation-progress.json`)
at 12:52:50 UTC reports 55,705,800,704 free bytes (about 51.9 GiB). Sizes below
are logical file bytes, not inferred reclaimed disk space.

| Exact database-host path | Bytes | Purpose and retention |
| --- | ---: | --- |
| `/opt/careertrellis/staging/releases/1229ed9bce42c66f6d4b613e56abdfeb00da347d` | 346,566 | Current evaluator source and receipt; retain while installed and through rollback window. |
| `/opt/careertrellis/staging/releases/fcfeb0bba27453db1d7300fb9abaf32497ff28e1` | 343,695 | Active reporter source; required by its systemd unit. |
| `/opt/careertrellis/staging/mirror-pending-validation-20261008` | 336,544 | Initial native validation evidence; review after cycle acceptance. |
| `/opt/careertrellis/staging/mirror-pending-activation-20261008` | 741 | Root-only original outcome configuration/drop-in and transaction. |
| `/opt/careertrellis/staging/mirror-pending-evaluator-20261008` | 93,975 | Root-only original evaluator files, WAL baseline copy, rollback manifest and transaction. |

Owner: CareerTrellis backup maintenance. Review after scheduled-cycle acceptance
or supersession; review is not deletion authority. Protected original configuration
remains only on the host. No remote files were deleted and no disk reclamation is
claimed. Transient test units were collected; private test directories removed.
Existing service journals remain under existing host retention. No Docker
inventory or cleanup is applicable because this deployment created none.

Local sources, scripts, receipts and the credential-free screenshot remain in
`artifacts/mirror-integration-20261008`; the isolated worktree retains both local
commits. The local evidence directory measured 2,699,422 bytes across 38 files
at closeout. Retain through cycle acceptance. For rollback, reconcile transaction
journals first, restore exact originals using the normal repository lock, and
reevaluate rather than relabeling pending as healthy. Restore hourly outcome
timing only after the old reporter is verified. Preserve the new heartbeat until
its retirement is explicitly reviewed.

The operational worktree is clean, and neither checkout has staged protected
files. Unrelated main-checkout changes remain untouched. The main checkout's
default whitespace check treats its preexisting Windows CRLF endings as trailing
whitespace; the scoped backlog check passes with CRLF recognized. No repository
configuration or unrelated line endings were changed. Local evidence validation
passed all 13 links; the external provider link was not part of that checker.
No application preview was launched for this host-monitor-only deployment; the
actual changed service flow and provider delivery were verified on the host.

!Approved outcome timing (local-only evidence: `../artifacts/mirror-integration-20261008/provider-outcome-timing.jpg`)
