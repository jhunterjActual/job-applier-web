# Deferred Serper quota proposal

## October 7: single-host open-beta implementation

The operator renewed approval for Serper using existing credits only, with no
purchases or top-ups. The account dashboard showed 2,494 credits; auto top-ups
were unchecked and disabled, with no payment card shown. The implementation now
uses `backend/serper_budget.py` as a single-host operational budget. This
supersedes the migration proposal below for this beta deployment; the proposal
is not applied, and application/identity schema remains 20261005_0023.

The root-owned `/etc/careertrellis/serper-budget.json` binds one fixed allocation
to the machine and existing provider key. The private worker-owned ledger at
`/var/lib/careertrellis/worker/serper/budget.sqlite3` retains lifetime, UTC-day,
owner-day and run counters. There is no automatic creation, refill, refund,
pruning or reset in runtime. Limits are no more than the freshly confirmed
balance, 300 requests/day globally, 60/day per owner, and six per claimed run
across retries. All counters commit before HTTP; exhausted/unavailable budgets
stop managed requests while direct sources remain available. Serper precedes
catalog acquisition within the same existing 45-request/90-second budget.

Only the search-worker unit loads `/etc/careertrellis/serper-worker.env`.
`backend/serper_provision.py prepare` is an operator-started, hidden-terminal
installer that leaves `CAREERTRELLIS_SERPER_ENABLED=0`; no service is restarted.
Use the existing key from Serper's **API keys** page. This is a new protected
worker configuration file, not a new provider key or a new Bitwarden item.
Never paste the key into chat or pass it through command arguments.

This ledger is a trusted-worker spending control, not a PostgreSQL-enforced
security boundary against a compromised worker. It is for one host and one
credential allocation only. Do not run clones or additional hosts against
independent copies. Application deployment and rollback must retain the ledger
outside release directories. Restoration or host/key migration must keep
Serper disabled until provider remaining credits and retained consumption are
reconciled; restoring an older ledger must never replenish the allowance.
Protect this state as accounting/recovery data, not disposable cache.

Activation still requires exact release preparation, installed-unit and file
verification, attended key provisioning, service/recovery/backup preflight and
bounded live acceptance. Local tests are not evidence of activation.
Install and verify the reviewed worker admission environment guard before the
new file is loaded. It permits absence, but strictly checks ownership, mode,
names and values when `serper-worker.env` exists, including dangling symlink
rejection. Use an application-only release switch: the historical full deploy
helper would replace the live open-beta Caddy configuration and operations.

## Historical migration proposal (not applied)

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
