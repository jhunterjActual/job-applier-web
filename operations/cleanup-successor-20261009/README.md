# Reviewed cleanup successor source

This self-contained operational bundle corrects admission and TOTP cleanup compatibility with schema `20261005_0023`. It is derived from installed operational source `a75050df65cc63ef6c25e8ac69b0f4b884d8d01e`, independently reviewed and kept separate from application runtime code.

The successor handoff preserves original bytes and durable success/failure history. It starts both lanes with no successor success credit; real successful cleanup and fresh monitoring are required. Retention rules, batch/time bounds, policy guards and locking remain unchanged. Application pointers, policy and TOTP enablement are outside its scope.

`source-manifest.json` identifies the exact 51 runtime/test/unit/wrapper files. Packaging maps this directory's `backend/` and `deploy/` to archive root, including only manifest-listed regular files; do not archive the entire repository or this README/verification directory. The release identity must be the actual local Git commit containing this subtree. The installer is separately reviewed in `verification/`.

Validation completed October 9, 2026: 31 isolated PostgreSQL18 checks against actual schema0023 migrations; 68 Linux operational tests with zero skips; 10 portable installer tests; nine Linux synthetic installer cases. Linux receipts are retained under `verification/`. The installer fixture exercises real filesystem/locking/chroot semantics but mocks SQL, systemd and its child test process. These checks are not live deployment or cleanup acceptance.

Installation is additive, DB-only, with exact archive/manifest hashes, existing maintenance/migration locks, unchanged database configuration/policy/roles/service baseline and at least 5GiB remaining space. It does not activate the successor. Activation requires holding five predecessor/successor timer families, verifying terminal services without clearing failure state, publishing the reviewed durable handoff, and observing real cleanup/monitor/reporter outcomes. An interrupted unsealed stage must remain held for manual review; never delete it or retry blindly.

No remote installation, activation, cleanup, push or PR has occurred as part of this local package preparation. Preserve the current operational bundle and all recovery history. Owner: CareerTrellis maintenance; retain until deployment and recovery review or explicit supersession.
