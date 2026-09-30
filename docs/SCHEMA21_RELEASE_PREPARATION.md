# Schema21 release preparation

Build20260930.80 targets the forward migration from20260907_0014 to20260929_0021.
The immutable operational source isbbbc4298b2ca80df107e64d2673d6c1f243c5e41.
The application commit containing this binding must have a distinct release
directory. Preparation never changes the current application pointer or schema.

Use install-schema21-coordinator.py for the operations package, then the separate
prepare-schema21-application.py and verify-prepared-schema21-application.py for
the application. Both hosts must attest the same selected plan and exact source
bindings before any maintenance, migration or identity activation. Existing
historical preparation helpers retain their historical contracts.

The qualified fallback runtime is8540990edd9e661aac67aabf81c1cd22dd7e5e6d,
build20260930.79. Its preparation archive SHA256 is
6546d3cb0901fba1bf07ed1c98aa16464cbcc4a3a652999b0c89cae0b17bf5c2,6666240bytes.
It was produced with git tar.umask=0022; payload content matches the separately
tested original archive. Use the fallback79 preparation/verification helpers.
Current .78 is not a qualified schema21 fallback. Operations stay pinned; a
runtime fallback does not downgrade the database or undo provider changes.

Local evidence includes1230offline passes,256isolated PostgreSQL18 passes,
149native database-installer passes and51immutable fallback runtime passes.
Explicit platform/database skips remain separate host prerequisites. Final
preparation-contract tests cover both builds and the exact current .78 baseline.
These results do not establish hosted application preparation or live acceptance.

Execution still requires healthy matched backups, protected attended owner/provider
verification, fresh initial-plan authoring and paired selection, fenced callers,
exact migration receipts and live acceptance. Keep signup and both authenticator
feature switches closed. No invitations or beta activation are implied.

Publish new commits to the PR only after exact deployment and live verification.
Retain current/approved rollback packages, paired recovery evidence and sanitized
attempt receipts. Reconcile ambiguous outcomes; never replay a claimed operation.
