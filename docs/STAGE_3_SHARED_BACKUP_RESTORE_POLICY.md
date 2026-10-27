# Stage 3 shared backup and restore policy

This slice defines the authority boundary for full backup and restore without
activating hosted PostgreSQL repositories or migrating data. The existing
`.ctbackup` format remains a local SQLite workspace feature. It is not a backup
format for an authoritative PostgreSQL database or private object store.

## Runtime behavior

When `CAREERTRELLIS_DATA_STORE=postgres` is selected in shared-host mode:

- unauthenticated requests retain the normal private-route authentication
  response and do not disclose the selected backup policy;
- after authentication, `POST /api/full-backup` returns HTTP `409` with the fixed detail
  `In-app full backup and restore are unavailable in shared mode.`
- after authentication, `POST /api/full-backup/restore` returns the same fixed response;
- the HTTP boundary rejects either authenticated request before FastAPI parses a multipart
  restore upload;
- endpoint-level guards repeat the decision before backup/restore locks,
  operation tokens, `DB_PATH`, `DATA_DIR`, `OUTPUT_DIR`, temporary files,
  archive inspection, SQLite access, migrations, or restore mutation; and
- there is no fallback to SQLite and no payload-selected data-store choice.

The response is deliberately independent of passwords, filenames, paths,
database URLs, provider credentials, archive content, and internal exceptions.
It is marked `Cache-Control: no-store`.

When SQLite is selected in local mode, the existing behavior is preserved:
authenticated AES-256-GCM workspace creation, bounded upload copying, archive
authentication and compatibility checks, database integrity validation,
generated-material replacement, recovery-directory retention, cancellation,
upload closure, and temporary-download cleanup all continue to use the current
local implementation.

## Hosted recovery authority

A hosted backup must cover both authoritative systems independently:

1. Operators take, retain, verify, and periodically restore-test PostgreSQL
   backups using the approved database backup system (the deployment runbook
   currently specifies pgBackRest plus an independent encrypted copy).
2. Operators retain versioned or independently replicated recovery copies for
   the permanent private object store and verify object inventory and integrity
   against database metadata. The manual immutable-generation, paired-backup,
   disposable-restore, and separate retention contract is specified in
   `STAGE_3_GENERATED_OBJECT_RECOVERY.md`.
3. Recovery is performed under a reviewed maintenance procedure that restores
   compatible PostgreSQL and object-store states, then runs readiness and
   reconciliation checks before traffic resumes.

The web application does not claim to create a complete shared-host backup, and
it does not offer an in-app shared restore. In particular, uploading a local
`.ctbackup` must never replace, seed, merge with, or be inferred as ownership
for PostgreSQL/R2 data.

## Verification and non-activation boundary

Synthetic tests prove local encrypted backup/restore parity, local upload and
temporary-file cleanup, the existing unauthenticated private-route response,
fixed authenticated shared responses, pre-parse multipart rejection,
and no calls to local database, filesystem, archive, lock, operation, or
migration helpers in shared mode. Tests use temporary SQLite workspaces and do
not access real profiles, backups, generated materials, PostgreSQL services, or
object storage.

This policy is fail-closed groundwork only. It does not flip
`HOSTED_REPOSITORY_CUTOVER_COMPLETE`, perform a migration, enable another user,
or change the final data-authority cutover and rollback rules. Generated-object
recovery remains an operator-only manual process; it is not a web fallback,
timer, deployment action, or substitute for a reconciled PostgreSQL/R2 cutover.

## Candidate OneDrive operational retention revision

The reviewed operational source adds finite bundles of the encrypted pgBackRest
repository and isolated restore verification for the independent OneDrive recovery copy. It
does not change the application backup endpoints, database schema, application
release pointers or ordinary R2 backup contract. Installation and activation
remain separate from source tests and a draft pull request.

The proposed policy preserves the newest two verified bundles, named holds and
recovery dependencies. A candidate may expire only 48 hours after acceptance of
the second newer verified bundle. Conditional permanent expiry requires the
exact current provider object/eTag and a fresh verified synthetic
capability witness; unknown outcomes block replay. Original INFO-only and legacy
recovery copies do not become verified successors through new metadata.

Before growth, current quota and allocation reads share one three-minute budget;
child closure and fresh facts are mandatory. Guards reserve at least 20% of the
account, account for four times expected peak allocation and enforce the 512 MiB
bundle namespace ceiling without crediting predicted deletion. Holds and stricter
accepted bounds cannot be weakened to make space checks pass.

The candidate calendar is Monday–Saturday 06:00 UTC plus the existing Sunday
paired opportunity. Monitoring checks aggregate upcoming coverage and the actual
pgBackRest recovery point within 36 hours. It cannot substitute restore-test time
for backup time. New native v2 completion receipts bind the successful INFO
timestamp metadata digest through terminal and catalog evidence; historical v1
records remain readable but cannot qualify through a later-added sidecar.
Permanent expiry covers only accepted native bundles in the fixed new database
namespace; legacy recovery trees, generated-object snapshots, R2 and receipts
are outside that deletion scope.

Current-source acceptance on October 27: 901 local cases (872 passes/29 platform
skips), 427 backend cases (423 passes/four skips), syntax checks, independent
reviews, and all 901 synthetic cases on target Python 3.12.3. Target source
manifest: `83382b871a1c376a1d743541e687e9388bd62a8fdd90970f024e90f97f695594`;
sealed terminal: `fb06ee68cc3b9b6e338c18cb7166635cd80a2e60417e16e7f4feaeb9b575323d`.
These results are not physical PostgreSQL restore acceptance. Shared staging
handoff, current preflight, installed-byte verification and two distinct real
replacement restore acceptances are still required before the guarded writer
transition and timer activation. Google Drive migration remains BKP-GDRIVE-001
in the backlog; no Google Drive remote or destination is established.
