# Durable study admission state

`scripts/value_evaluation/campaign_state.py` is an offline accounting/controller
primitive. It reads no credentials, imports no application, starts no processes,
and performs no provider or network request. Creating a `paid` declaration does
not establish human approval or authorize dispatch. Runtime adapters, native
mechanical proof, reviewed pricing and an explicit funding decision remain
separate gates. No paid runner is supplied by this change.

It leaves existing `budget_guard` ledger v2 and all historical campaign files
unchanged. Use a new explicit private evidence directory and a new path for each
study. No automatic discovery, migration, retry, reset or lock removal exists.

## Policy and API

The policy has exactly these fields:

| Field | Meaning |
|---|---|
| `schema` | `study-campaign-policy-v1` |
| `run_id` | Safe synthetic identifier |
| `frozen_content_sha256` | Exact lowercase SHA-256 from the reviewed freeze; controller treats this as an opaque byte binding |
| `mode` | Explicit `offline` or `paid`; immutable for the campaign |
| `approval_reference` | `null` for offline proof, sanitized explicit reference for paid declarations |
| `accounting_basis` | `approved_conservative_estimate` or `verified_upper_bound`; declarations require supporting evidence |
| `total_limit_usd` | Shared campaign ceiling as a decimal string with at most six places |
| `packet_limits_usd` | Exactly two product IDs mapped to positive packet dollar caps |
| `stage_limits_usd` | Each product maps `fit` and `materials` to positive per-case stage dollar caps |
| `request_limits` | Each product maps `fit`, `materials` and `packet` to positive integer request caps, at most 100 each |
| `order` | Complete 36-stage frozen order: 12 fit and 6 materials cases per product; V01–V06 have materials, V07–V12 fit only |

Product IDs/call IDs use letters, digits, underscores and hyphens, at most 80
characters. Evidence references use only bounded sanitized letters, digits,
underscores, periods, slashes, colons and hyphens. Never pass raw errors, provider
headers, credentials, profile text or personal identifiers. Amounts use exact
integer microdollars internally. Boolean request limits, unknown fields, duplicate
JSON keys, nonfinite data, duplicate/missing stages and materials-before-fit
ordering are refused.

Run from `scripts/value_evaluation` or import with that directory on Python's
module path:

```python
from campaign_state import Campaign

campaign = Campaign.create(new_journal_path, reviewed_policy)
# Reopen only with the identical policy; never create on missing/corrupt resume.
campaign = Campaign.open(existing_journal_path, reviewed_policy)
campaign.begin_stage("CT", "V01", "fit")
campaign.reserve("CT_V01_fit_call1", "0.100000", "counts/call1")
# An external adapter may dispatch only after reserve returns successfully.
# This module neither dispatches nor proves that such dispatch is authorized.
campaign.settle("CT_V01_fit_call1", "0.020000", "usage/call1")
campaign.finish_stage("succeeded", "terminal/CT_V01_fit")
summary = campaign.snapshot()
```

All native child-reviewer/tool-continuation requests must use the active stage and
the same controller. Fit and materials share the product/case packet dollar and
request ceilings. Both products share the total dollar ceiling. Stage caps cannot
borrow another stage's allocation. Settlement releases unused dollar reservation
only when supported final charge evidence exists; it never resets admitted request
counts or permits a reused call ID. Failed calls with known zero charge still
consume their admitted count. Packet and stage caps may bind earlier than the
declared complete-batch envelope; this primitive does not estimate feasibility.

Only one stage is active. It follows the exact frozen order. A failed fit allows
its materials stage to be recorded as skipped, but material dispatch requires a
successful fit. `succeeded`, `failed` and `skipped` are execution declarations,
never quality scores or evidence of complete product acceptance.

## Persistence and interruption

The append-only JSONL journal hash-links each canonical event to its predecessor.
One reserve event admits its count and money together. The writer flushes and
fsyncs it before updating a separately fsynced head binding and returning.
Settlement and completion append further events; original admissions remain.
The head detects removal of a complete journal tail, and any mismatched,
partial, missing, malformed or excessive state refuses reopening.

Companion files are `<journal>.head`, transient `<journal>.head.pending`, and an
exclusive `<journal>.lock` containing process/owner identity. The operation lock
uses exclusive create on Windows and POSIX; its identity and bytes are checked
before releasing it. A lock or pending head left by interruption is preserved.
Inspect the exact owner, journal/head and terminal state; never clear a lock by
age or infer failure from an SSH disconnect.

`mark_unknown(call_id, evidence)` retains the full reservation and halts both
products. A reservation left after a crash likewise blocks new admission and
stage completion. An unknown call cannot be silently settled or replayed; a
separately reviewed reconciliation mechanism is deliberately outside this
primitive. Actual charge above reservation is recorded and halts the campaign.
No exception response is permission to retry.

Use a private regular filesystem directory with cooperating writers. Symbolic
links, junctions and hard-linked journal/head/lock files are refused. This is not
an adversarial filesystem or anti-rollback security boundary: changing journal
and head together can forge a consistent history, and root-directory races or
storage devices ignoring fsync are outside the guarantee. Directory-entry
durability across sudden power loss is filesystem/platform dependent. Refusal
preserves evidence for inspection; it does not repair state.

## Verification and resources

```powershell
python -B -m unittest -v test_budget_guard test_ct_budget_transport test_campaign_state
```

Tests use fabricated accounting and temporary files only. They include shared
limits, stage/packet request limits, known failures, unknown outcomes, crashes
before settlement, complete-line truncation, interrupted head update, concurrent
ownership, preserved stale/replaced locks and a complete synthetic 186-request
envelope. That envelope is a test fixture, not an approved allocation or real
usage estimate. Windows temporary files require the normal scoped approved
execution context when the managed sandbox denies writes; do not alter ACLs.

Retain journal/head, original outputs, locks/pending files and supporting receipts
through the study review/reconciliation window. The controller deletes only its
verified live-operation lock and atomically replaces its own head; it never
deletes outputs, old journals, directories or historical evidence. Tests remove
only their owned temporary directories after verification. No Docker resources
or external services are used.
