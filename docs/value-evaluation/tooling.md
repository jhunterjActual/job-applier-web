# Offline evaluation spending controls

The evaluation helpers in `scripts/value_evaluation/` reserve a caller-reviewed
charge before one request. They are research tooling, not application metering,
provider account limits, or authorization to run a paid evaluation. They neither
read credentials nor initiate requests on import. All tests use fabricated
requests and in-memory fake openers.

## Freeze and create

`budget_guard.RunConfig` requires a run ID, exact frozen manifest SHA-256,
total ceiling, per-packet-attempt ceiling, accounting basis and sanitized approval
reference. There are no default spending limits or approval references. The run
manifest must separately identify exact product and harness source revisions,
model, prompts, input fixtures, selected price evidence and any approved risk.
A newer harness revision does not silently replace the product revision under test.

Select `verified_upper_bound` only with independent support for that claim.
`approved_conservative_estimate` records an explicitly accepted estimation policy;
it is not a guaranteed billing maximum. The code cannot establish that approval
or validate a price source for the caller. Amounts use decimal strings with at
most six fractional places, converted to exact integer microdollars. Round
reservation estimates upward before supplying them.

Create with `Ledger.create(path, config)` once. Reopen with `Ledger(path, config)`.
Creation refuses an existing ledger. A missing, truncated, corrupt or differently
configured ledger fails closed on the next operation; do not create a replacement
to resume a run. Preserve it and reconcile retained requests and provider evidence.

Each reservation has a unique call ID and a packet-attempt ID. Multiple model/tool
calls needed for the same packet attempt share the latter ID, so splitting a
request cannot evade its cap. A separately permitted recovery attempt gets its
own attempt ID and remains in the same total-budget ledger. This primitive does
not authorize retries or enforce the protocol's maximum number of attempts.

## Attach to the pinned adapter

`ct_budget_transport.BudgetedOpener` wraps the caller's original no-redirect,
no-retry opener. Its only allowed request is POST to the exact Anthropic Messages
endpoint with model `claude-sonnet-5-5`, positive `max_tokens` at most 32,768,
and the pinned adapter's exact content type/API version plus a nonempty API key.
Unknown headers, redirects represented as a different endpoint, duplicate JSON
keys and other models are rejected. The wrapper does not make a generic opener
safe: the caller must retain the pinned adapter's redirect and retry policy.

The wrapper detaches the request from its caller, preserving method, URL, body
bytes and header values. Later mutation of the original request cannot affect the
reservation or outgoing request. The resolver receives immutable body bytes and
method/URL, never credential headers. It returns a `ChargeReservation` whose
accounting basis must match the run. The ledger must successfully persist the
reservation before the original opener receives the detached request.

The resolver must explicitly validate and account for every billable request
feature. Endpoint/model/output-limit validation alone is insufficient: nested
tool, image, cache, service-tier or other pricing modifiers must be refused unless
the approved policy covers them. The pinned adapter's schema and thinking fields
must not be silently removed to make an estimator accept the request.

The wrapper never retries, logs exceptions, reads/rewrites responses or settles
charges. A returned response is not proof of final complete billing. Obtain exact
usage and applicable rates before explicitly calling `Ledger.settle`; otherwise
retain the full reservation. A settlement exceeding the reservation is recorded
and halts future reservations. It cannot undo an already incurred charge.

## Recovery and evidence

Exclusive lock files contain only run ID, PID and UTC acquisition time. A lock
left by an interrupted process is a recovery stop, not permission to remove it.
Verify the owner and terminal state before any separately reviewed recovery.
Concurrent writers refuse the lock rather than retry automatically. Ledger data
is flushed before atomic replacement; filesystem/power-loss durability beyond
the host's guarantees is not asserted. Protect the evidence directory from other
writers and never alter a ledger while this process owns it.

Keep original outputs, attempted failures, reservations and explicit unknowns.
A pre-dispatch refusal is a blocked operation, not a generated packet, and cannot
pass a usefulness criterion. This package does not certify material quality or
complete VALUE-001's discovery, PDF, editing-effort or participant measurements.

## Verification

From the repository root, use an existing Python environment:

```powershell
python -B -m unittest discover -s scripts/value_evaluation -p 'test_*.py' -v
```

On the affected Windows sandbox, use the scoped approved execution context for
temporary-file tests from the outset; do not change ACLs or interpreters to work
around permission failures. Suites use disposable synthetic temporary directories.
No packages, provider access, credentials, application database or server are
needed. Existing repository P0/object and syntax checks remain applicable.

Retain the source and tests with the review branch. The library creates only an
explicit caller-owned ledger, transient replacement file and lock. Run outputs
belong in the run's approved evidence directory, with owner/size/retention recorded
separately. No Docker resources or deployment artifacts are created by this slice.
