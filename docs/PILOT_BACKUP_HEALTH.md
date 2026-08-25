# Pilot backup-health boundary

This document defines the smallest backup-health boundary permitted for the
invitation-only friends-and-family pilot. It does not broaden the pilot, make
registration public, change data authority, migrate a recovery provider, or
replace the complete pre-beta backup program in `PRODUCT_BACKLOG.md`.

The pilot boundary is deliberately local and operator-driven. A systemd timer
evaluates existing immutable operation receipts, writes bounded root-only
status, and records a privacy-safe terminal line in the local journal. The
reviewed operations—not the evaluator—publish their own receipts. The root-only
status command makes a persistent alert visible to the operator. There is no email,
SMS, webhook, or other push notification in this initial scope. Consequently,
the operator **must run the status command at least once every UTC day and
immediately before every invitation**. A missing, stale, unavailable, or
non-passing status blocks the invitation.

## Authoritative interface and storage

The operator runs the same host-bound interface on both authoritative hosts:

```text
sudo -n /usr/local/sbin/careertrellis-backup-health status
```

On the database host the wrapper is fixed to the `database` surface. The
required web-host integration is separately fixed to the `objects` surface; the
database-host installer does not install or prove that integration. Neither
operator interface may accept an operator-selected surface or path override.
Internally, a release-bound wrapper invokes
`backup_health.py status --surface database` or
`backup_health.py status --surface objects`, respectively, with fixed protected
roots. A daily or pre-invitation check is complete only after both strict
surface commands exit zero. Until the reviewed web-host objects integration is
installed and verified, the objects result is unavailable and invitations are
blocked.

The evaluator is `careertrellis-backup-health.service`, scheduled by
`careertrellis-backup-health.timer`. Both the helper and unit definitions are
release-bound installed artifacts. The fixed local alert target is
`careertrellis-backup-health-alert@.service`; it records a local alert only and
does not contact a notification provider. Database evaluator status and its WAL
baseline live below `/var/lib/careertrellis/backup-health/database`, persistent
aggregate alerts below its `alerts` child, and unit alerts below its
`unit-alerts` child. The web-host integration uses the corresponding isolated
`/var/lib/careertrellis/backup-health/objects` surface root and never shares a
mutable status file with the database surface. Compatible operation receipt
pairs live below `/var/backups/careertrellis/backup-health/operations` on the
host that owns the operation. All roots must be canonical, non-symlink,
root-owned directories with mode `0700`; terminal receipt files must be
root-owned with mode `0600`.

The database evaluator serializes itself with
`/run/lock/careertrellis-backup-health.lock`; the objects evaluator uses its
separate fixed objects-health lock. Each observes existing terminal
evidence only. It must not traverse either repository, take a backup, copy a
mirror, perform an inventory check, start a restore, or clean a disposable
restore. Operations that access a PostgreSQL repository or its mirror must
serialize through `/run/lock/careertrellis-pgbackrest-repository.lock` and must
not overlap backup, archive maintenance, check, expire, mirror, or restore work.
Generated-object mirror and restore operations retain their own reviewed
non-overlap contracts.

## Required passing gates

A status is `ok` only when every applicable item below is proven from compatible
terminal evidence. Ages are calculated in UTC from an authenticated terminal
success timestamp, never from a file modification time. An unreadable,
unverifiable, incompatible, future-dated, or incomplete receipt is absent
evidence and fails closed.

| Surface | Required pilot evidence |
|---|---|
| PostgreSQL full backup | Latest successful full backup age is no more than 8 days. |
| PostgreSQL any backup | Latest successful full, differential, or incremental backup age is no more than 2 days. |
| WAL archive | The PostgreSQL archive failure counter has not increased since the preceding accepted evaluation, segment lag is no more than 1, and the repository's maximum WAL segment is not behind PostgreSQL's last archived segment. Comparison is internal; segment names are never emitted. |
| Independent encrypted PostgreSQL mirror | Latest successful mirror age is no more than 2 days, and its receipt is bound to the latest accepted backup label. The label is compared internally and never emitted. |
| Full-download repository inventory | Latest successful full-download one-way comparison age is no more than 8 days and reports no missing or changed source ciphertext. The append-only destination may contain additional retained history, so its aggregate count and bytes may exceed—but never fall below—the source. Metadata-only comparisons and provider hashes do not satisfy this gate. |
| Isolated PostgreSQL recovery | Within 35 days, the exact accepted label was restored in isolation, promoted, checksum-verified, stopped, and cleaned up successfully. The terminal evidence must cover every phase. |
| Generated-object mirror | Latest successful immutable generated-object mirror age is no more than 8 days. |
| Generated-object recovery | Within 35 days, an immutable generation was restored to the reviewed disposable target, its manifest and ciphertext checks passed, and the target was deliberately retained for separate exact-target review. This receipt does not claim database reconciliation, decryption, or target cleanup. |

The accepted `pgbackrest_restore` operation receipt includes bounded,
non-negative `rpo_seconds`, `rto_seconds`, and `elapsed_seconds` fields. Their
measurement boundaries are:

- `rpo_seconds`: whole seconds from the selected manifest-verified backup's
  completion to the disposable restore attempt's start, measuring the recovery
  point's age when the rehearsal began;
- `rto_seconds`: whole seconds from restore-attempt start through the terminal
  promotion, readiness, and checksum verdict, whether that verdict passes or
  fails; and
- `elapsed_seconds`: whole wall-clock seconds from wrapper start through the
  required cleanup verdict.

`rto_seconds` must not exceed `elapsed_seconds`. Missing or contradictory values
make the receipt invalid and the status non-passing. A failed rehearsal still
records its bounded terminal-verdict time and false verification fields. The
pilot sets no misleading recovery promise from a single rehearsal; these
observations establish a measured baseline only.

## Failure, scheduling, and alert semantics

The scheduled evaluator checks both freshness thresholds and terminal unit or
receipt results. A scheduled operation that fails, is cancelled, times out, has
no terminal receipt, or does not produce passing evidence before its freshness
limit is a failed or missed operation. A failed systemd result is an alert even
when older evidence is still within its age limit. The evaluator timer's own
stale state or failed last result is also non-passing when the operator runs the
status command. In particular, a surface status older than 3 hours cannot be
returned as `ok` by the host-bound status interface. A status exactly 3 hours
old or up to 5 minutes ahead for bounded clock skew is accepted; older or
further-future state produces a synthesized canonical alert with
`health_status_stale` and exits nonzero. Status and alert-state filenames are
surface-qualified so a database evaluation cannot overwrite object state or
vice versa.

The manual generated-object mirror and restore helpers attempt local cleanup
and unchanged-authority verification before publishing terminal evidence. Any
nonzero result then attempts a fixed failed operation receipt even when either
safety check also failed; that receipt claims neither cleanup nor unchanged
authority, so it cannot convert an unsafe outcome into success. This makes the
failure immediately non-passing without recording remote names or retrying the
operation. If even the root-only failed-receipt publication cannot be retained,
the helper emits a fixed privacy-safe error and preserves its nonzero result;
the operation remains unresolved and blocks invitations pending explicit
operator investigation.

Alerts are persistent state, not a transient journal line. Once any scheduled
unit fails, `status` continues to return nonzero and report the fixed alert code
until that exact systemd unit completes successfully again and its reviewed
`ExecStartPost` validates newer compatible terminal evidence. Running the wrapper
directly may inspect current evidence, but it cannot clear a scheduled-unit alert
or prove that the timer/service path recovered. A later unit success may clear
the active alert, but it must not alter or erase the earlier failure receipt or
journal history. A partial or out-of-service evaluation cannot clear an alert.
Each `status` command also checks the exact fixed timer allowlist locally and
fails with the privacy-safe `monitor_schedule_unavailable` reason when a required
timer is not loaded, enabled, and active. That check never starts a unit or
traverses a backup repository.

Neither the evaluator nor a scheduled recovery operation automatically retries
an ambiguous or failed mutation. systemd restart policies and timer catch-up
must not create duplicate backups, mirrors, inventory traversals, restores, or
cleanup attempts. After a failure, an operator first identifies the original
PID, lock owner, phase, unit result, and terminal receipt. Any retry is a new,
explicitly authorized operation after terminal state is proven.

The PostgreSQL archive failure counter is monotonic, so a detected increase is
intentionally sticky and is never auto-baselined away. After the incident is
contained and reviewed, the operator may run the interactive, root-only
`careertrellis-backup-health acknowledge-wal` procedure. It requires its exact
confirmation phrase, rechecks that the repository is healthy, segment lag is
within policy, the repository is not behind, and no unit alert remains, then
atomically advances only the protected counter baseline and immediately runs a
complete evaluation. It emits no counter, segment, label, or provider value and
cannot erase the original alert, journal history, or operation receipts.

The operation receipt directory retains the newest 32 complete terminal receipt
pairs for each fixed operation.
Each pair consists of a canonical JSON document and its commit manifest/digest;
the manifest is published last. An unpaired document, temporary file, invalid
digest, or non-canonical path is not terminal evidence. Pruning occurs only
under the operation's reviewed non-overlap lock and only after a newer pair is
durably committed.
Pruning never touches recovery-operation receipts, backup data, repository
metadata, object generations, authority markers, or evidence outside this
dedicated health receipt root.

## Privacy-safe status and receipts

Status, receipts, and this evaluator's journal records use closed schemas:

- `careertrellis.backup-health-operation/v1` receipts have only `schema`,
  `operation`, `result`, `started_at`, `completed_at`, and the operation's
  reviewed fields listed below;
- `careertrellis.backup-health-snapshot/v1` evaluator input has only `schema`,
  `surface`, `observed_at`, `units`, and the reviewed database-only backup/WAL
  comparison values; the objects surface accepts no database fields;
- `careertrellis.backup-health-status/v1` has exactly `schema`, `surface`,
  `evaluated_at`, `result`, `failure_codes`, `failure_count`, and
  `ages_seconds`; and
- `careertrellis.backup-health-alert/v1` and
  `careertrellis.backup-health-publish-result/v1` contain only their reviewed
  fixed alert/publish result fields.

Every unit entry contains exactly `last_result` and `missed`. A database
snapshot additionally contains `latest_backup_label` and a `wal` object with
exactly `failed_count`, `previous_failed_count`, `segment_lag`, and
`repository_archive_max_behind_database`. An alert document contains exactly
`schema`, `surface`, `state`, `first_detected_at`, `last_evaluated_at`,
`consecutive_alert_evaluations`, and `failure_codes`. A publish result contains
exactly `schema`, `operation`, `result`, and the local `manifest_name` of its
newly committed receipt. That local manifest name is never a remote or object
name.

Database `ages_seconds` keys are exactly `full_backup`, `latest_backup`,
`pgbackrest_mirror`, `pgbackrest_inventory`, and `pgbackrest_restore`. Object
keys are exactly `generated_object_mirror` and `generated_object_restore`.
Every age is a bounded non-negative integer or `null` when evidence is missing;
`null` is non-passing.

The operation-specific receipt fields are fixed as follows:

| Operation | Additional fields |
|---|---|
| `full_backup` | `backup_label`, `elapsed_seconds` |
| `differential_backup` | `backup_label`, `elapsed_seconds` |
| `pgbackrest_mirror` | `backup_label`, `mirrored_backup_label`, `object_count`, `byte_count`, `elapsed_seconds` |
| `pgbackrest_inventory` | `backup_label`, `source_object_count`, `source_byte_count`, `destination_object_count`, `destination_byte_count`, `divergence_count`, `elapsed_seconds` |
| `pgbackrest_restore` | `backup_label`, `elapsed_seconds`, `rpo_seconds`, `rto_seconds`, `promotion_verified`, `checksum_verified`, `cleanup_verified` |
| `generated_object_mirror` | `object_count`, `byte_count`, `divergence_count`, `elapsed_seconds` |
| `generated_object_restore` | `object_count`, `byte_count`, `elapsed_seconds`, `manifest_verified`, `ciphertext_verified`, `target_left_intact` |

Backup labels in these protected database-operation receipts are required only
for exact-label recovery and mirror binding. They remain root-only and are never
relayed by the health evaluator. They are not remote names, object names, or
provider identifiers.

Failure codes are a closed set. Applicable scheduled-operation unit codes end
in `_unit_failed`, `_unit_missed`, or `_unit_never`; receipt codes end in
`_receipt_invalid`, `_failed`, or `_receipt_future`. The cross-surface evaluator
code is `health_status_stale`. Database gate codes are
`full_backup_receipt_missing`, `latest_backup_receipt_missing`,
`pgbackrest_mirror_receipt_missing`, `pgbackrest_inventory_receipt_missing`,
`pgbackrest_restore_receipt_missing`, `full_backup_stale`,
`latest_backup_stale`, `wal_failed_count_growth`,
`wal_segment_lag_exceeded`, `wal_repository_archive_behind`,
`latest_backup_label_mismatch`, `pgbackrest_mirror_stale`,
`pgbackrest_mirror_label_mismatch`,
`pgbackrest_inventory_stale`, `pgbackrest_inventory_divergence`,
`pgbackrest_restore_stale`, `pgbackrest_restore_promotion_failed`,
`pgbackrest_restore_checksum_failed`, and
`pgbackrest_restore_cleanup_failed`. Object gate codes are
`generated_object_mirror_receipt_missing`,
`generated_object_restore_receipt_missing`,
`generated_object_mirror_stale`, `generated_object_mirror_divergence`,
`generated_object_restore_stale`,
`generated_object_restore_manifest_failed`,
`generated_object_restore_ciphertext_failed`, and
`generated_object_restore_target_not_retained`. Unknown fields, operations,
surfaces, results, or codes are rejected rather than copied through.

No schema accepts a WorkOS identity, email, database URL, password, token,
cookie, provider key, remote or repository name, bucket, endpoint, object key or
object name, generation identifier, host address, user data, or
source/destination path. Exact database backup labels needed for receipt binding
remain confined to protected database-operation receipts and evaluator input;
WAL segment names are reduced to bounded lag and comparison fields before the
evaluator. Neither is copied to status, alerts, journals, or operator output.
Underlying command stdout and stderr are not relayed to the journal or health
receipt; only closed-schema aggregate state is recorded.

Each daily command emits one canonical JSON line. Values below are illustrative,
not live evidence:

```json
{"ages_seconds":{"full_backup":0,"latest_backup":0,"pgbackrest_inventory":0,"pgbackrest_mirror":0,"pgbackrest_restore":0},"evaluated_at":"<UTC timestamp>","failure_codes":[],"failure_count":0,"result":"ok","schema":"careertrellis.backup-health-status/v1","surface":"database"}
{"ages_seconds":{"generated_object_mirror":0,"generated_object_restore":0},"evaluated_at":"<UTC timestamp>","failure_codes":[],"failure_count":0,"result":"ok","schema":"careertrellis.backup-health-status/v1","surface":"objects"}
```

The zeros are placeholders for non-negative ages, not expected live values. Any
alert preserves the exact field set, uses only fixed alert codes, sets
`result=alert`, and exits nonzero. The operator must not paste underlying logs
or protected receipts into chat, an issue, a pull request, or an invitation
record.

## Activation, rollback, and evidence reuse

Release preparation verifies the helper and unit syntax without changing live
timers. Web release activation and rollback first stop the local objects-health
timer and prove its service is inactive before replacing or removing its
privileged bundle; a successful compatible activation starts the reviewed timer
again. General host reprovisioning refuses to modify that bundle while an
existing objects-health timer or service is active. Database-host activation
installs the release-bound collector, wrapper,
and units; validates their owner and modes; requires the established full,
differential, and independent-mirror timers to remain enabled and active; and
reloads systemd without changing those established timer states. It enables and
starts only the new hourly evaluator, inventory, and restore timer units. The
inventory and restore timers have monotonic first delays of 7 and 30 days
respectively; activation never starts their service units. Activation
therefore does not immediately start a remote backup, mirror, inventory, or
restore operation, and it preserves the existing backup and mirror timer state.
The database-host installer does not claim web-host coverage.
Before invitations, a separate reviewed web-host activation must install and
verify the objects-surface evaluator and host-bound status interface without
changing object authority or launching a mirror/restore. Neither activation
restarts the CareerTrellis web or worker services merely to activate health
monitoring, changes PostgreSQL/R2 authority, alters provider configuration, or
starts a precautionary operation.

The `.39` deployer predates the web-host objects-health artifact class. Its
running shell cannot acquire new activation logic merely because it copies the
`.40` deployer over the installed helper path. The first `.39` to `.40` upgrade
therefore uses a reviewed two-phase handoff: the installed `.39` helper prepares
the exact immutable `.40` release, then the operator verifies and invokes the
deployer inside that prepared release to perform activation. Re-running the
installed `.39` helper for activation is forbidden because it can report the app
ready without installing the new objects-health helper, units, and timer.

```text
revision=<exact reviewed 40-character .40 commit>
sudo -n /usr/local/sbin/careertrellis-deploy --prepare-only "${revision}"
target="/opt/careertrellis/staging/releases/${revision}/deploy/scripts/deploy-release.sh"
sudo -n stat -c '%U:%G %a' "${target}"   # must be root:careertrellis 750
sudo -n "${target}" "${revision}"
```

The database-host installer likewise consumes only an operator-selected,
root-owned reviewed source. It binds the repository directory, rejects symlinked
or writable source components, copies every required artifact once into a
root-only staging tree, validates those exact staged bytes, and installs only
from that tree. Its forward migration changes the disposable restore parent from
`0700 postgres:postgres` to `0710 root:postgres`; PostgreSQL may traverse into
its random `0700 postgres:postgres` child, but cannot rename or replace that
child. Installation rollback restores the legacy parent metadata when it restores
a preceding helper set, or removes a newly created empty parent when the prior
helper set had no restore root. A successful scheduled database health unit
clears its own persistent unit alert only from `ExecStartPost`, after systemd has
recorded the new successful completion.

Activation first reuses compatible retained evidence. It must not take a new
backup, copy a repository, or repeat a restore merely because the monitor is
new. Only when a named required gate cannot be validated from compatible
retained evidence may the operator authorize **one** fresh check. That check
must respect the applicable non-overlap lock and reach a proven terminal state
before health is evaluated again or any other fresh operation is considered.

If activation fails, invitations remain blocked and the preceding application
release remains authoritative. Rollback restores the preceding reviewed
monitor, wrapper, helper, and unit set, reloads systemd, and validates the
resulting unit state without starting a remote operation. Rollback preserves all
health state, alerts, receipt pairs, operation receipts, backups, mirrors,
object generations, and authority files. It never compensates by deleting
evidence or launching recovery work.

## Invitation decision

Before each additional friend is preseeded, the operator runs the daily status
command and retains its sanitized terminal receipt. An invitation is permitted
only when the command exits zero with `result=ok`, the state was evaluated that
day, every gate above is `ok`, no repository/recovery operation is active, and
the current immutable application release has otherwise passed pilot
acceptance. A warning, unknown field, stale state, missing receipt, active lock,
or unavailable command blocks that invitation.

Google Drive migration remains backlog work. The initial pilot continues to
evaluate the currently reviewed independent encrypted mirror without creating a
Google Drive remote, moving data, changing credentials, or retiring OneDrive.
