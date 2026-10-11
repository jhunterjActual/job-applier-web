# Scheduled mirror gap: local design candidate

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.

Owner: CareerTrellis backup maintenance. Issue: CT-BACKUP-LABEL-GAP-20261007.
Status: historical offline policy prototype. The integrated monitor is now
[deployed with an additional last-clear deadline cap](MIRROR_PENDING_DEPLOYED_20261008.md).
This prototype module is not imported by the application,
installed monitor, backup scripts, or alert reporter.

## Problem and proposed rule

The October 7 diagnosis (local-only evidence: `../artifacts/backup-label-gap-20261007.md`) records a
successful backup at 03:19:14 UTC, a label mismatch at 04:01:30, and successful
daily mirroring by 04:44:50. These are historical observations, not current
backup-health evidence.

The [pure classifier](../scripts/mirror_pending_policy.py) proposes a `pending`
classification only when the existing evaluator's sole failure is the mirror
label mismatch. Verified successful mirror evidence must identify the immediate
predecessor backup, precede the new backup, and be less than 48 hours old. Both
receipt evidence and the actual schedule must be verified. An existing active
alert disqualifies pending; this policy cannot acknowledge or clear it.

The proposed schedule contract is daily 04:30 UTC, at most 15 minutes of timer
jitter and 30 minutes to complete. The fixed deadline is the earlier of 05:15
UTC on the backup-completion day and three hours after that backup completed.
At the deadline, the mismatch is overdue. A backup completed at or after 04:30
does not acquire tomorrow's grace period. Repeated evaluations cannot extend
the deadline. These allowances are a proposed policy, not a measured runtime
guarantee or a change to the installed timers.

Any additional failure, including unknown future failure codes, remains under
the current evaluator. Missing/unverified evidence, stale mirrors, unexpected
ordering, unknown predecessors and active alerts cannot become pending.
The classifier retains the original failure codes and never emits healthy
status or clears an alert. It is deliberately unsuitable as a drop-in filter
that simply removes the mismatch code.

## Integration requirements before activation

1. Refresh the installed evaluator, full service/override chain, timer UTC
   semantics, jitter, duration limits and immediate failure-reporting path.
   Reconcile the deployed source with the candidate baseline before editing.
2. Build inputs from canonical manifest-verified receipts and current repository
   inventory. Identify the immediate predecessor using repository chronology,
   not label lexicography. Validate successful generation-bound mirror evidence
   through the existing evaluator. Boolean flags here represent those checks;
   the prototype does not perform them. Unknown evidence fails closed.
3. Introduce an explicit pending status across the closed snapshot/status schemas,
   CLI, reporter, heartbeat and readers. Pending must not publish a healthy
   recovery, acknowledge/reset existing alerts, or refresh an old mirror's age.
   Keep monitoring freshness separate from mirror-success freshness. Older readers
   must reject unsupported states rather than interpreting them as healthy.
4. Retain immediate unit/receipt failures, stale-monitor alerts, and missed-timer
   detection. With an hourly evaluator, an overdue condition is observed on the
   first check at/after the deadline; 05:15 is not a promise of alert delivery at
   that instant. Evaluate whether an additional deadline check is required.
5. Add end-to-end synthetic checks for pending-to-success, pending-to-overdue,
   concurrent backup/mirror locking, invalid manifests, disabled timers, reporter
   rejection and preserved active alerts. The nine classifier tests do not prove
   installed status/reporting behavior or backup recoverability.
6. Review the exact operational patch and rollback plan, obtain deployment
   authorization, then verify the deployed revision and one complete scheduled
   cycle. Keep this operational change separate from the AI application candidate.

## Local verification and retention

[Tests](../scripts/test_mirror_pending_policy.py): nine passed on Windows,
Python 3.12, using synthetic timestamps and labels without network or user data.
Coverage includes exact deadline boundaries, fixed retry deadlines, three-hour
cap, late backup refusal, stale/future/order checks, failure preservation,
unverified inputs, and active-alert preservation. No live health claim is made.
An independent read-only review found no actionable issues in the classifier,
tests or design. Nothing is staged; unrelated working changes were preserved.

The retained application candidate's regression and loopback smoke results are
recorded separately in the receipt (local-only evidence: `../artifacts/mirror-policy-regressions-20261007.json`).
They check that candidate, not integration of this uninstalled policy.

Retain the small source, tests, design and verification receipt as review evidence
until this issue is implemented or superseded; owner CareerTrellis backup
maintenance. No packages, downloads, Docker resources or remote artifacts were
created for this slice. The verification runner removes its temporary database
and bytecode and stops its own loopback process.
Retained source, tests, design, receipt and pending friction note total about
15 KiB. No disposable task artifacts remain and no disk-reclamation claim is made.
