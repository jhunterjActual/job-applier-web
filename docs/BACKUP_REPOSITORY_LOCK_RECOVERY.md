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
