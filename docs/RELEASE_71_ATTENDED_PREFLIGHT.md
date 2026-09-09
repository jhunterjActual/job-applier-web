# September 8 attended preparation — deployment blocked

The operator attended at 10 p.m. EDT and approved online preparation using the
existing WireGuard route and a one-hour future v2 session lifetime. Signup and
v2 remain disabled. No outage, migration, broker credential installation or
application activation occurred.

Both health surfaces were green at the initial 01:59 UTC checks. The exact
application source remains `151f58dddb17c57686de2ba3b6bf4d9c21e4570f`, tree
`9e91194c7842ca63c3541fd0c98bed91fe848f62`. The hash-verified source archive is
retained root-only on both hosts under the matching ops-bundle directory.
The DB source-only release was prepared; no DB operation was invoked. The web
Git object was imported from the verified local bundle without moving checkout
HEAD or pushing to GitHub.

The reviewed web prepare-only helper ran as
`careertrellis-prepare-71-20260909.service`, invocation
`ba38230d37bc49e1b28576477c107371`. It terminated with MainPID0, exit1.
Its 648-test suite ran in 50.539 seconds: one failure and one skip.
`test_account_erasure.AccountErasureRouteTests.test_authenticated_middleware_suppresses_refresh_and_resets_owner_context`
expected HTTP202 but received503. The cause is not yet established; no retry
or weakened gate is authorized by this receipt. OS dependency installation
reported zero upgraded, newly installed or removed packages.

Root-only `prepare.log` SHA-256:
`be12d8bd9bc08ac99f1989320edf122d1b79c2a4572e6bc66f7b660551089ce2`.
The final web candidate directory is absent after helper cleanup, and no
CareerTrellis lock remains held. The live current pointer remains
`ebf372f7988a9b36e90969710ee60a0914d1272b`; web PID316879 was unchanged.

A separate concrete installer blocker was reproduced locally and independently
reviewed: `ProviderSettings` rejects issuer paths other than empty or slash,
while WorkOS documents an AuthKit issuer with `/user_management/<client_id>`.
Correct metadata cannot pass the installer. Substituting a bare API origin
would instead fail exact JWT issuer verification. Existing synthetic tests
configure and mint the same incorrect bare-origin issuer, hiding this mismatch.
Sources: https://workos.com/docs/cli/emulate and
https://workos.com/docs/integrations/supabase-authkit . No real provider token
was read or retained to reproduce this constructor failure.

Correct and verify the issuer contract and diagnose the host regression before
establishing a new exact release binding or resuming the deployment gate.
Installer metadata and installed guard/deploy/rollback acceptance remain open.
The public planned-maintenance notice has not been changed by this preparation.

## Approved correction

The operator approved correcting both blockers and rerunning preparation.
The provider constructor now requires exactly the fixed WorkOS API origin plus
`/user_management/` and its configured client ID. Bare origins, other clients,
other hosts, trailing slashes, query/fragment, credentials and port aliases are
rejected. JWT signature and exact issuer checks remain unchanged. Synthetic
tokens now use the documented client-scoped shape, with explicit signed
mismatch rejection tests. Custom AuthKit issuer domains are not enabled by
this fixed-endpoint candidate; any such configuration needs separate evaluation.

The erasure failure was reproduced without connecting to any database: the
route fixture had omitted its readiness stub, allowing an attempted synthetic
database-engine lookup. The fixture now supplies readiness explicitly, traps
unintended DB access and verifies both success and fail-closed denial. No
production authentication, readiness or erasure guard was changed.

Focused provider/signup tests: 56 passed, including native browser flow.
Erasure module: 38 tests passed with four POSIX-only skips on Windows.
Python compileall passed. Full preparation-suite and target-host results are
required separately before declaring the new candidate prepared.
