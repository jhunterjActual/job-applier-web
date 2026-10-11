# Prefix-isolated object restore — October 10, 2026

The live ciphertext rehearsal passed in 29 seconds. Healthchecks recovered to up after the 10:09 PM EDT healthy report. This verifies restoration of the encrypted object inventory; it does not establish key recovery, plaintext decryption, or paired database reconciliation.

## Installed behavior

Each run reserves a unique `rehearsals/YYYYMMDDTHHMMSSZ-<32hex>` prefix within the existing dedicated August disposable recovery bucket. Only that prefix must be empty. Transfers, downloaded-byte comparisons and exact inventory checks address that prefix. The six retained bucket-root objects were preserved: their aggregate inventory hash is unchanged.

The coordinator selects the latest protected canonical capture manifest and seal, verifies its identity, and lets the restore wrapper validate the actual OneDrive bytes before any target writes. The selected capture must be no more than 35 days old. The existing capture/mirror maintenance workflow must continue producing fresh captures and retaining their matching release directories. No application object-store code changed. For a later full recovery rehearsal, explicitly strip the run prefix when interpreting canonical `v1/...` keys; do not point the production reconciler at the whole recovery bucket.

The legacy helper remains unchanged. The new helper, coordinator and units are independently installed; the exact files/hashes are in the installation receipt. The maintained sources and portable tests are included in the October 11 maintenance PR. Publication is authorized without another deployment; no installation or activation is performed by this PR.

## Schedule and bounded retention

Weekly Wednesday at 08:30 UTC, plus up to five minutes of jitter. The observed next run is October 14, 2026 at 08:31:54 UTC (4:31:54 AM EDT). A verified run within 24 hours suppresses catchup duplication. Failed attempts do not qualify.

Stop before exceeding 12 attempted runs or 256 MiB of reserved ciphertext; each snapshot is capped at 32 MiB. Failed/partial attempts consume a reservation. Nothing is automatically deleted. The maintainer must review retained runs before the cap is reached; a cap refusal publishes failed restore health and leaves evidence intact. Review after the first scheduled run and before the twelfth attempt. Service limits: 384 MiB memory, 50% CPU, 64 tasks, bounded journal rate.

## Acceptance evidence

- Installed file hashes and selected snapshot (local-only evidence: `../artifacts/object-prefix-restore-20261010/install-result.json`)
- Final service state and verified run (local-only evidence: `../artifacts/object-prefix-restore-20261010/state-final.json`)
- Timer activation (local-only evidence: `../artifacts/object-prefix-restore-20261010/timer-enabled.json`)
- Before target inventory (local-only evidence: `../artifacts/object-prefix-restore-20261010/target-before.json`) and after inventory (local-only evidence: `../artifacts/object-prefix-restore-20261010/target-after.json`)
- Wrapper tests: five passed (local-only evidence: `../artifacts/object-prefix-restore-20261010/implementation-frozen-receipt.json`)
- Coordinator tests: twelve passed (local-only evidence: `../artifacts/object-prefix-restore-20261010/scheduler-tests-primary.txt`)
- Resource and closeout record (local-only evidence: `../artifacts/object-prefix-restore-20261010/closeout.json`)

The live prefix contains 24 objects totaling 707,762 bytes. The six older objects remain 183,839 bytes with identical inventory hash. The source snapshot is `20261009-143304F`, release `ec29b0171eabae27c156a332962a80b3037ee7a9`. Temporary local restore scratch was removed by the verified helper; ciphertext under the new R2 prefix remains retained.

All three application PIDs and restart counts remained unchanged. The object monitor is healthy and the configured reporter succeeded. The required application P0 command could not start because the existing virtual environment refers to an unavailable WindowsApps Python interpreter; the same interpreter limitation prevents its compileall command. The new coordinator's isolated tests used the discovered bundled Python. Bash syntax and systemd unit validation passed; no application/UI source changed.

## Recovery and ownership

Owner: CareerTrellis backup maintainer. Keep the installed source, manifests, journals and restored ciphertext through review. To suspend recurring execution, stop/disable only `careertrellis-object-prefix-rehearsal.timer`; inspect any active service before further action. Do not interrupt/delete an active restore. The original bucket-root helper remains available. Any removal of installed files or R2 prefixes needs exact ownership/hash/reference checks and the applicable cleanup approval; timer disabling does not delete data.

Shared Windows helpers handled actual discovery, bounded reads, SSH operations, native tests, structured receipts and a guarded index edit. No synthetic helper-adoption operations or time-savings claims were made.
## Maintained source locations

| Repository source | Installed destination |
|---|---|
| `deploy/scripts/test-generated-object-prefix-restore.sh` | `/usr/local/sbin/careertrellis-test-generated-object-prefix-restore` |
| `deploy/scripts/object_prefix_rehearsal.py` | `/usr/local/libexec/careertrellis/object_prefix_rehearsal.py` |
| `deploy/scripts/report_completed_alert.py` | `/usr/local/libexec/careertrellis/report_completed_alert.py` |
| `deploy/systemd/careertrellis-object-prefix-rehearsal.service` and `.timer` | Matching `/etc/systemd/system/` units |
| `deploy/systemd/careertrellis-object-alert-report.service` | Matching `/etc/systemd/system/` unit |
| `deploy/systemd/careertrellis-generated-object-backup-health.service.d/60-completed-alert-report.conf` | Matching `/etc/systemd/system/` drop-in |

Run portable synthetic contracts with `python -m unittest discover -s deploy/tests -p 'test_*.py'`. The shell contracts require Bash and explicitly skip when unavailable. The coordinator currently names the reviewed existing target environment file; moving this tooling to another host requires a reviewed target identity/configuration, preserved source-bucket separation, and the existing operational approval. This PR does not auto-install or enable these tools.

October 11 follow-up: the local Python environment was repaired; all 287 required P0/object tests, compileall, dependency validation and isolated startup checks passed. This supersedes the earlier interpreter blocker recorded above.
