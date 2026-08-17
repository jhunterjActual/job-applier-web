# Stage 3 PostgreSQL/R2 cutover rehearsal

This slice adds one root-only wrapper for the disposable no-omission import and
reconciliation rehearsal. It does not activate a release, change the hosted-
authority marker, add the hosted compatibility capability, stop or start a
service, modify a live database or bucket, clean a rehearsal target, or invite
another user. SQLite and local generated files remain authoritative,
`HOSTED_REPOSITORY_CUTOVER_COMPLETE` remains false, and the Alembic head remains
`20260814_0009_account_erasure`.

## Exact invocation

Prepare the exact reviewed 40-character release without activating it. Create
separate root-owned mode-`0600` rehearsal database and object-storage
environment files that select a disposable PostgreSQL database and a dedicated
private disposable R2 bucket. Before running the wrapper, provision the
required non-`BYPASSRLS` roles on that disposable target, apply this exact
release's Alembic head, and reapply this exact release's
`deploy/postgres/grant-runtime.sql`. Confirm head
`20260814_0009_account_erasure` and confirm the migrated target contains no
tenant data, search runs, erasure requests, or stored-object metadata. In this
contract, "empty" means schema-only and fully migrated; the rehearsal wrapper
does not migrate or grant the target. Keep the live migration, runtime, and
object-storage environment files installed so the wrapper can compare target
identities without printing them.

Manually stop and verify the web, search-worker, and account-erasure-worker
services, then run:

```bash
sudo /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT/deploy/scripts/rehearse-postgres-r2-cutover.sh \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-rehearsal.env \
  /etc/careertrellis/object-storage-rehearsal.env \
  I_UNDERSTAND_THIS_WRITES_ONLY_TO_DISPOSABLE_POSTGRES_AND_R2
```

The explicit phrase confirms that the command writes only to operator-created
disposable targets. The wrapper accepts exactly those four arguments. It never
accepts either import omission switch.

## Fail-closed boundary

Before calling the existing reviewed preflight and import wrappers, the
rehearsal wrapper proves all of the following:

- the caller is root and the release is a real, non-symlinked immutable path
  directly under `/opt/careertrellis/staging/releases/<40-hex-commit>`;
- the exact release contains its private Python environment, preflight wrapper,
  and atomic import/reconciliation wrapper;
- both rehearsal files and all three live identity files are regular,
  non-symlinked `root:root` mode-`0600` files;
- the effective libpq rehearsal PostgreSQL host/port/database identity differs
  from both live migration and runtime identities even when usernames or
  passwords differ; service-file, `hostaddr`, multi-host, and non-default
  target-selection indirection is rejected rather than compared lexically;
- the rehearsal R2 endpoint/bucket identity differs from the live application
  object identity and both object environments select `r2_encrypted`;
- the wrapper acquires descriptor 10 on the exact shared maintenance lock, and
  the installed authority helper confirms exact `sqlite-pre-cutover` with no
  pending final-cutover attempt;
- the web, search-worker, and account-erasure-worker services are all inactive.

Identity comparison occurs inside the exact release's Python process through
environment values, not command arguments. Fixed errors never include a URL,
username, password, endpoint, bucket, tenant identifier, local document path,
or document hash. Only after the normalized target-separation proof does the
wrapper compute the two environment-file digests and pass the exact release,
digests, and live rehearsal coordinator PID through the separate
`assert-rehearsal-import-allowed` boundary. The underlying import wrapper
recomputes those digests, inherits descriptor 10, takes its separate import
lock, and rechecks authority and inactive services before it opens the frozen
source or either disposable target. Direct, missing, partial, mixed, stale, or
final-cutover import contexts are rejected.

The wrapper has no command that marks authority, moves a release symlink,
enables, starts, restarts, or stops a service, performs target cleanup, or
invokes a provider API. Operators remain responsible for keeping the disposable
database and bucket until the receipt has been reviewed and for deciding when
their separate cleanup is safe.

## Sanitized receipt

Standard output contains the existing count-only SQLite preflight receipt, the
atomic import receipt, the independent read-only reconciliation receipt, and a
final fixed envelope containing only:

- the exact 40-character release revision;
- authority `sqlite-pre-cutover`;
- `services_inactive: true`;
- `no_omissions: true`; and
- `targets: disposable`.

Retain that output with the exact release's GitHub checks, PostgreSQL 18/RLS
receipt, pgBackRest backup label, independent mirror receipt, and isolated
restore result. Do not retain environment contents, command tracing, exception
details, database or bucket identities, tenant data, local paths, or document
hashes.

An import error is intentionally ambiguous: the wrapper stops, prints no target
detail, and does not retry or clean anything. Run the exact release's read-only
`reconcile-sqlite-postgres-import.sh` with the same files before an operator
decides whether retry or disposable-target cleanup is safe.

## Not final-cutover evidence

A receipt from this build (`20260813.29`) proves the disposable rehearsal only.
The final cutover release must repeat prepare-only checks and the no-omission
rehearsal after all final compatibility changes are present. Permanent import,
the one-line `postgres-r2-authoritative-v1` capability, the repository cutover
constant, one-way authority mark, release activation, hosted smoke, worker
enablement, and post-import recovery receipts remain a later single reviewed
operation.
