# Deferred Serper quota proposal

Not part of the active migration chain or deployment. The operator suspended
Serper on September 22, 2026. Renewed explicit approval is required before any
paid request, credential installation or activation.

`20260921_0015_search_api_quota.py.proposal` preserves the tested quota migration.
`quota-and-outcomes-reference.txt` preserves the earlier tracked changes as
reference. It includes search outcome changes that remain active: do not apply
this patch wholesale. Neither file is an executable migration plan.

The interim release uses schema `20260907_0014`, unchanged runtime grants and
unchanged owner-recovery binding. It requires no quota migration, re-enrollment,
credential rotation, identity activation or relaxed schema guard. The
`hybrid_feeds` discovery mode combines approved direct feeds with legacy search;
the default remains `legacy` until deployment/configuration is reviewed.

Future Serper work must reconcile its schema lineage and protected migration
receipts before restoring this proposal. A separate exact-source/exact-target
maintenance path would need source/target/operation/release-bound receipts,
validation under the Alembic lock, NOLOGIN/quiescence fences, failure retention,
exact policy/login restoration and recovery-binding compatibility. Do not add
the quota-only schema to the identity activation allowlist. No paid capability
can be activated through environment configuration in the interim candidate.

Retain these small source-only artifacts with the candidate until the proposal
is explicitly resumed or superseded. Owner: CareerTrellis operator; review by
September 28, 2026. No credentials, private data or raw provider responses included.
