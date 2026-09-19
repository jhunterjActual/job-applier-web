# Protected owner recovery and identity operations

This change packages the protected owner credential, operation-bound migration
bridge and identity coordinator. It adds no application endpoints, enrollment UI,
schema migrations, database grants or signup activation. Existing application
authentication modules and cookie format remain unchanged.

## Review and deployment scope

The operations source is extracted from the previously installed recovery branch.
`session_v2_transition` constructs the existing session codec only to validate
keys/configuration; it never issues or opens cookies. The application candidate's
epoch-enforcement codec changes are deliberately outside this change.

The tracked `install-identity-coordinator.py` is the actual identity-only installer.
It preserves archive/ownership, private-runtime, dependency and service/database
baseline checks; monitoring remains separately installed. The historical broader
monitoring installer is not used. Both host roles run their native acceptance
suites without skips and verify installed source hashes.

The bridge installer supports an explicit `--upgrade` only from its pinned
predecessor. It validates the new immutable target before publication, checks the
exact old launcher and protected credential state, and publishes atomically under
credential/host locks. Selected or legacy transition plans refuse the upgrade.
Pending publication files are retained on failure rather than blindly retried.
The existing credential tool and enrolled code are preserved.

## Evidence and remaining boundaries

Focused source checks:243 native coordinator/bridge/upgrade tests passed without
skips; required P0/object suite371 passed with two existing skips. Compilation and
synthetic loopback version/dashboard/application-feature-absence/graceful-stop
checks passed against the unchanged .71 application. Supplemental older admission
rehearsals passed with12 environment-dependent skips; they are not counted as the
no-skip installer acceptance suite.

Earlier deployed revision `d070512b55ba22a8750d393cdcb37a45e725f4fe` passed WEB239,
DB111 native tests and49 synthetic PostgreSQL identity/enrollment tests. Its
application/configuration and enrolled credential remained unchanged. These are
historical receipts, not acceptance of a new commit.

The focused branch is subject to a fresh exact-archive deployment and readback
before PR publication. Per-host receipts record commit, archive hash, source
manifest, native test results and unchanged service/configuration state under
`handoff-installation/`. The dedicated bridge records `installation.json` and
passes an independent preactivation readback. The PR description identifies the
exact deployed commit and results. No private account IDs, credentials or recovery
codes belong in those public receipts.

Account setup already exists: read-only Production checks confirmed the existing
Google-linked owner, verified email and prior sign-in. That does not replace fresh
attended transition proof. No selected plan, owner remapping, factor mutation,
account release, schema activation or beta opening is performed by this PR.
Normal factor removal, general user recovery and final application acceptance
remain separate work.

Current and predecessor operations/runtime directories remain retained for
evidence and rollback review. Uploaded archives/installers are task-owned but
require explicit retention/cleanup review before remote deletion. Never remove
the protected credential state or reuse a consumed code during installation.
