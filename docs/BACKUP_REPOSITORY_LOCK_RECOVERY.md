# Repository lock reboot-persistence correction

2026-09-08. Operational revision only; application release .71 remains bound to
`151f58dddb17c57686de2ba3b6bf4d9c21e4570f`. No application build increment.

Read-only discovery found the DB host booted on September 7 at 04:30:08 UTC.
The required `/run/lock/careertrellis-pgbackrest-repository.lock` was absent,
and no matching tmpfiles.d creation rule existed. The scheduled differential
backup and OneDrive mirror exited immediately. The health wrapper rejected the
missing lock; core health was stale with a mirror-label mismatch. This is not
evidence that provider access or a new backup/mirror run will succeed.

The operator approved the narrow lock-persistence repair and necessary
backup/mirror recovery verification, with staging online. The fix is the plain
nontruncating `f` rule in `deploy/tmpfiles/careertrellis-pgbackrest.conf`.
`backend/backup_repository_lock.py` installs only that rule and an absent lock;
it neither runs global tmpfiles processing nor changes services, credentials,
release pointers, timers, schema, or backup data. Existing locks must be regular,
root:root0600, single-link and inode-stable; busy locks refuse before config
publication. Unsafe existing files are never repaired by overwriting them.
Configuration is published fsynced and no-clobber; a conflicting prior rule is
refused. Failed installation retains the lock and any published state for
inspection. It does not remove/recreate a lock as recovery.

The full backup-health installer now stages and validates the rule and includes
it in installed-target/rollback accounting. Do not run that full installer merely
to repair the already-installed host: it changes unrelated helpers and timers.

Local evidence: all seven synthetic tests passed on existing Ubuntu/systemd255,
including absent-lock creation, simulated volatile `/run` loss and recreation,
nonempty inode/content preservation, held flock exclusion, unsafe file types,
ownership/mode/parent rejection, conflicting config and publication failure.
Docker passed the six applicable tests but lacked systemd-tmpfiles; its one skip
is covered by Ubuntu. Independent bounded implementation review found no
concrete blocker; this is not a full repository security scan.

Live sequence: bind exact ops commit and source hashes in a root-owned bundle;
verify no active repository operation/lock and unchanged installed helpers;
run the synthetic fixture on the target host; execute the narrow installer;
read back rule bytes and lock owner/mode/inode plus native boot prerequisite.
Then run one approved differential backup, retain the exact accepted receipt,
and only after it terminates successfully run one approved OneDrive mirror.
Observe existing operation state, not repeated provider traversals. Refresh
health through the reviewed evaluator only after terminal receipts are valid.
Do not clear an alert by hand, waive stale/mismatched evidence, blindly retry,
start an unrequested restore/inventory, or claim green health before both
surfaces and the full accepted receipt chain pass. An SSH disconnect requires
original invocation/phase reconciliation. Preserve all prior evidence.

The attended application migration window remains September 8 at 10 p.m. EDT.
Any unresolved health failure blocks it. No public signup/session-v2 activation,
GitHub publication or application deployment is authorized by this ops repair.

## Live repair receipt (2026-09-08 UTC)

Installed ops source: `ac03056c6b08ffb9214da4a1fa2ac6ee0d8d8d1e`.
The first target-native test run exposed a synthetic wrong-mode fixture that
depended on the operator umask; it failed before any live lock/config change.
That failed receipt remains retained. The corrected fixture explicitly sets
its mode; all seven tests passed under umask 077 on both local Ubuntu and the
database host (target elapsed 0.053 seconds).

Source archive SHA-256:
`f0c4ff482751e69f674d8d89490f3f635fdac79a7c37bb455dab9e24b2314816`.
Root-owned evidence is retained on the database host under
`/var/backups/careertrellis/ops-bundles/ac03056c6b08ffb9214da4a1fa2ac6ee0d8d8d1e`.

The narrow installer returned `result=ok`, `services_changed=no`. Installed
rule bytes match the immutable bundle, root:root0644, single link. The new
repository lock is root:root0600, single link, inode 6. Native
`systemd-tmpfiles-setup.service` ordering precedes the backup/mirror units.
The real host was not rebooted; boot recreation was tested in a synthetic root.
Install receipt SHA-256:
`226a59e65a4d01df4bb64392c2d93468fedf1b78af1b5f29829dbcf24eef41b6`.
Native-test receipt SHA-256:
`e3207a4b1653f9d5e61868a2eede31aaf7e40d994467905148423eec10a4aa2f`.

The approved differential backup invocation
`566db95e65f4460395b3b68a62c18b9d` completed successfully from
16:19:05 to 16:19:19 UTC (14 seconds). Its manifest-validated receipt is retained;
SHA-256 `bf73faa1154c2350dbffe76d30508bffc411a3f59de0e4e3337b808e7d4706c3`.
Only after that terminal success and lock release was the approved mirror
started, at 16:21:56 UTC, invocation `4476146c3662498381b3b13686b32bf5`.
The same mirror invocation terminated successfully at 16:27:57 UTC, MainPID0,
ExecMainStatus0. Its manifest-validated v2 receipt records wrapper start
16:21:57 UTC (one second after systemd start), 360 seconds elapsed,
13,190 objects and 62,731,536 bytes. Receipt SHA-256:
`9a8047682ca933672debe0b579a63f6b4ae1bcf76b68e592fb0e446e93d8653f`.
Generation SHA-256:
`815a0f3ad23407bb931c4d23a5024fe3039adcbb5fcef3ef1cdcc975ae156b04`.
The receipt is retained root-only beside the other repair evidence.

After lock release, the installed-byte-verified database health evaluator
returned zero; database status was `ok`, no failure codes, evaluated at
16:34:04 UTC. Retained database-health receipt SHA-256:
`4af75c312c5a48d3d9586ad8d7eec44212a5283439afdb05d8b2c7b6614df8c4`.
Web object health readback was also `ok`, no failure codes, evaluated at
16:04:45 UTC; its installed helper matched the active release source. An initial
web SSH handshake timeout executed no command; reconnect succeeded. Both health
surfaces are green at this observation. No alert was manually cleared, no
restore/inventory was started, and no application release was changed.
The temporary mirror monitor was restored to the original September 8,
10 p.m. EDT attended-maintenance reminder. Fresh readiness checks remain
mandatory at that window; this is not an application-deployment receipt.
