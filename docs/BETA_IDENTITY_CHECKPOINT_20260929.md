# Beta identity checkpoint — September 29, 2026

This candidate reconciles authentication/session and authenticator work onto
deployed application ancestry cafad5b48a579adc80695519f0f8b7d39f621cd5 (.78).
It is not deployed, enabled or accepted for beta. The unchanged build number
does not imply that these new bytes are live. Older dated readiness documents
remain historical evidence; this note describes the current checkpoint.

The candidate includes session/admission boundaries, additive migrations0015–0020,
durable security-operation slots, acknowledged enrollment disposition and guarded
transfer into exact-factor removal. Removal uses bounded provider transport,
durable claims, uncertainty handling, protected HTTP routes and atomic local-session
invalidation. Runtime construction is opt-in and requires exact0020 structural
readiness. Enrollment/removal remain disabled by default.

Local release-source, initial-plan-replacement and two-host journal models preserve
the existing activation fences. A temporary three-database SQLite adapter rehearses
claim-before-pointer-write, concurrency, lost acknowledgment and disagreement.
It is not a production adapter, and consumers remain disabled even at convergence.

## Verification

The final isolated offline run executed1056 tests:910 passed,146 explicitly skipped
for platform/integration prerequisites, zero failures/errors. This includes required
P0/object suites and12 SQLite durability/concurrency/cleanup tests. AST checked246
Python files; compileall passed with disposable bytecode removed. Prior local
loopback version/dashboard/default-off checks passed; no live acceptance is claimed.
Earlier dedicated native PostgreSQL receipts cover identity acceptance, slots,
removal, disposition transfer, local-session invalidation and runtime readiness.
These are separate runs, not coverage of all skipped tests in the final suite.

Independent reviews resolved completion-race, schema ownership, journal-lineage,
predecessor-binding and delayed-publication issues, with regression coverage.
Task-owned temporary databases and Docker fixtures were cleaned; earlier sanitized
receipts/manifests remain in the workspace artifacts collection. No private profile,
database, environment, credentials or generated user materials belong in this commit.

## Remaining gates

- Existing operational preparation still targets .74/schema0016; activation/owner
  compatibility and selected-plan replacement require a cohesive reviewed successor.
- Migrations0017–0020 require closed maintenance and quiesced callers;0020 rejects
  existing removal attempts. Never remove history to satisfy that prerequisite.
- No schema downgrade is supported. Verify a compatible application fallback and
  forward correction; a database restore cannot undo provider-side factor changes.
- Protected durable host adapters, coordinated consumer barriers, exact host/source
  evidence, distinct release build, deployment and attended provider/owner acceptance
  remain open. General recovery and repeat enrollment remain incomplete.
- Search coverage, acquisition permissions, backups and beta opening are separate.

At checkpoint time web/main is936b0fee15cfae014946f4c55b29e2dc8bfd7caf.
The candidate's ancestry also includes five earlier search commits through .78;
any main-based PR must disclose that unmerged dependency. Do not treat this checkpoint
as authority to deploy, activate, invite users or bypass deployment-first PR ordering.
