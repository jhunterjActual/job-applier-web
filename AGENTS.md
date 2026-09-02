# Repository Guide

## Run locally

- Start (from the repository root): `./run.ps1` in PowerShell, or `run.bat` in Command Prompt. The launcher creates/repairs `backend/venv`, installs Chromium, and runs the app. The port defaults to `8001`; override it with `./run.ps1 -Port 9000` or `run.bat 9000`.
- Stop: press `Ctrl+C` in the launcher terminal. Do not kill unrelated Python processes.
- The only expected listener is `127.0.0.1:8001` by default. Check readiness with `Invoke-RestMethod http://127.0.0.1:8001/api/version`; also verify `http://127.0.0.1:8001/` returns the dashboard. Use the configured port when overridden, and do not expose the server on `0.0.0.0`.

## Checks

Run these commands from the repository root:

- Tests: `Push-Location backend; ./venv/Scripts/python.exe -m unittest test_p0_regressions.py test_object_store.py test_object_import.py test_stored_objects.py; Pop-Location`
- Syntax/lint check: `Push-Location backend; ./venv/Scripts/python.exe -m compileall -q .; Pop-Location`
- Formatting: no formatter is configured; preserve the existing Python, JavaScript, HTML, and CSS style and avoid unrelated reformatting.
- `backend/test_match_api.py` is a manual integration script that uses the local profile, database, and Gemini API; do not run it as part of routine tests.

## Database and fixtures

- SQLite lives at `data/jobapplier.db`. Schema creation and migrations run on import/startup in `backend/database.py`; migrations must be idempotent and safe for existing user data.
- Prefer additive migrations. Never reset, replace, seed, or destructively edit the user's database. Cover schema changes with temporary SQLite databases and mocked services, following `backend/test_p0_regressions.py`.
- There is no shared fixture set. Keep test data synthetic and temporary; tests must not depend on `data/`, real resumes, credentials, network calls, or paid APIs.

## Protected files

- Do not modify or commit local/private/generated content: `data/`, database files, `.env*`, `baseresume.*`, `resume.*`, PDFs, `backend/venv/`, caches, IDE settings, or OS metadata. Follow `.gitignore`, and never print or expose API keys or resume/profile data.
- Do not regenerate brand assets or icons unless the task explicitly requests it.

## Repository identity and PR destination

- CareerTrellis, this repository's hosted project, is owned by `J. Hunter Johnson LLC`. Use that entity in future ownership, policy, legal, and provider-account work unless the operator says otherwise.
- This workspace has two GitHub remotes with similar names. `web` is the hosted CareerTrellis repository `job-applier-web`; `origin` is the older standalone repository `job-applier`. Hosted releases, staging operations, multi-user work, and their pull requests must use `web`, never `origin`.
- Before creating a branch, committing release work, or pushing, inspect the remote bindings and current base. For hosted work, require the branch to descend from `web/main`, push explicitly with `git push --set-upstream web <branch>`, and verify the resulting upstream is `web/<branch>`. Never infer the destination from the conventional name `origin`, an existing upstream, or a browser tab.
- Create hosted pull requests only in `job-applier-web`, with base `main` and the exact pushed `web` branch as compare. Before submission, verify the GitHub repository breadcrumb, expected commit count, expected changed-file count, and branch names. An unexpectedly large comparison or a `job-applier` breadcrumb is a hard stop: do not submit the PR or allow checks to run.
- If a branch is mistakenly pushed to `origin`, do not create a PR there. First verify that the exact commit remains locally and has been pushed to `web`, confirm no legacy-repository PR was created, then remove only the exact mistaken remote branch under separately confirmed cleanup authority. Do not delete or rewrite any other branch.

## Interactive secret prompts

- Treat a Codex-managed exec PTY and the operator's visible secure terminal as different terminals. A background session ID, queued/open-panel result, or tool-side prompt readback is not evidence that the operator can see or type into that prompt.
- When an operation requires a WorkOS identity, passphrase, recovery key, token, or other non-echoed input, do not start it in a background exec session. Complete all secret-safe preflight first, give the operator the exact command to run in their already-visible secure terminal, and wait for the operator's sanitized result.
- Never claim that a secret prompt is present or waiting unless the operator has explicitly confirmed seeing it. Never transmit the secret through chat, tool input, `write_stdin`, command arguments, environment variables, clipboard automation, logs, or receipts.
- If a prompt was mistakenly started in an inaccessible session, first prove the exact PID/session is still waiting for input and no mutation has begun, then interrupt only that process. Prove it exited, any transient state it created is absent or restored to the preflight baseline, every lock acquired by that attempt is released, and all pre-existing unrelated locks remain unchanged before offering the operator-started command. Do not count the inaccessible attempt as authority to retry an ambiguous mutation.

## Application releases and operational bundles

- Treat immutable application releases and installed operational bundles as separate revision domains. Bind the application to its exact runtime release commit and API version; bind helpers, units, and timers to their separately reviewed source commit plus installed-byte verification. An ops-bundle install must not move the application `current`/`previous` pointers, restart application workers, or change the application build.
- Create or activate an application release only when user-facing behavior, application/schema/API bytes, or an application security boundary changes. Scripts, service units, timers, diagnostics, and operational documentation alone use an ops-bundle revision and do not justify an application build bump.
- Before committing a change to systemd identity, capability, device, syscall, namespace, or `NoNewPrivileges` controls, reproduce the minimum fixed transition on a target-OS host under its native systemd and kernel. Prefer an exit-code-only transient probe with no provider call; local syntax checks are not a substitute. Retain only a sanitized result.
- When a provider failure is hidden by the normal privacy boundary, use only an explicitly authorized, short-lived root diagnostic: disable shell tracing, create a root-owned mode-`0600` file under `/run`, redirect all raw output there, emit only a fixed sanitized classification, inspect it only in the secure terminal, and remove it after terminal state is proved. Keep diagnostics read-only unless the exact mutation is separately authorized; never send raw output to journald, chat, commits, or receipts.
- After the failure cause is isolated, batch all known related operational corrections into one reviewed PR and one deployment. Do not create one PR or deployment per probed directive; split only an independently reviewable security boundary or urgent containment.
- Conserve GitHub Actions: run the applicable local tests and reviews first, produce one locally verified commit, push once, and open one PR. Inspect the specific failing log before changing code; never speculatively rerun unchanged checks. Push a follow-up only for a diagnosed correction.
- For ops-only unit or helper changes, satisfy the completion gate with host-native operational verification of the reviewed artifact; do not start or release the application unless its runtime bytes changed.

## Shared staging operational inventory

This section gives a new task enough non-secret coordinates to find the staging systems. An inventory entry identifies a target; it does not authorize a write, restart, deployment, provider change, or destructive operation. Treat public addresses and all live state as mutable, and perform a read-only, secret-safe preflight before consequential work.

### Hosts, endpoints, and network roles

| Role | Stable identity | Last-known operator target | Private role |
|---|---|---|---|
| Staging web/application host | `careertrellis-staging-web-1` | `hunter@129.212.153.245` | DigitalOcean VPC `10.108.0.2`; WireGuard `10.70.0.1` |
| Staging database/backup host | `job-applier-db-staging-01` | `hunter@157.245.113.244` | DigitalOcean VPC `10.108.0.3`; WireGuard `10.70.0.2` |

- The public SSH addresses above are last-known observations, not repository contracts. Verify the destination name, role, and fingerprint against the trusted provider inventory before use. Connect as `hunter`, never as root, with `BatchMode=yes`, `ConnectTimeout=15`, `ForwardAgent=no`, and `ClearAllForwardings=yes`; use `sudo -n` only for bounded privileged work.
- The public application origin is `https://staging-app.jhunterj.com/`. The WorkOS callback is `https://staging-app.jhunterj.com/auth/callback`, and the configured logout return is `https://staging-app.jhunterj.com/`. The last-observed provider-managed AuthKit host was `rapid-bud-68-staging.authkit.app`; rediscover it from the actual redirect rather than treating it as configuration authority.
- Cloudflare fronts the public origin. Caddy is the only public application ingress on TCP 80/443 and proxies to Uvicorn on `127.0.0.1:8001`. Never expose port 8001 publicly.
- WireGuard uses UDP 51820 over the VPC. Application database traffic is web `10.70.0.1` to database `10.70.0.2:5432`; PostgreSQL must not be public. Port 55432 is the standard disposable restore-test port, and 55433 has been used by reviewed post-activation restore wrappers. Both are temporary resources, not permanent listeners.
- Provider source-IP filters must use the current outbound public IPv4 `/32` of the host that actually calls the provider. The web host's last verified R2 egress observation was `134.209.165.107/32`, but query it again immediately before configuring or testing a restriction. Do not substitute an SSH destination, operator ingress address, VPC address, or WireGuard address.

### Release, database, and runtime contracts

- The default source checkout is `/srv/careertrellis/job-applier-web`; the release root is `/opt/careertrellis/staging`; immutable releases are `/opt/careertrellis/staging/releases/<exact-40-character-commit>`. The `current` and `previous` symlinks are mutable and must be resolved live.
- Local `.worktrees/*` directories may contain reviewed release or operational material that is newer than the main checkout, but no local worktree is deployment authority. Identify the intended branch/commit and compare it with the live exact release before using scripts or documentation from it.
- The last accepted activated `.31` generation was commit `e7ae1d6c0e8b5bf39e5babe3ef871f1414f5c3bd`, build `20260813.31`, with Alembic head `20260814_0009`. This is a historical binding, not proof of the live release. Bind operations to the full live commit, `/api/version`, database revision, authority state, and matching receipts; a build number alone is insufficient.
- The accepted database contract is PostgreSQL 18, cluster `main`, database and pgBackRest stanza `careertrellis`, and schema `careertrellis`. Verify the live major/minor version, cluster, listener, HBA, ownership, RLS, and Alembic revision before database work.
- Database roles are `careertrellis_schema_owner` (NOLOGIN), plus restricted login roles `careertrellis_migration`, `careertrellis_runtime`, `careertrellis_worker`, and `careertrellis_erasure_worker`. Do not confuse database roles with application user roles.
- Runtime data roots are `/var/lib/careertrellis/staging`, `/var/lib/careertrellis/worker`, `/var/lib/careertrellis/account-erasure`, and `/var/lib/careertrellis/generated-object-recovery`. The PostgreSQL production default is `/var/lib/postgresql/18/main`; disposable restore roots are `/var/lib/postgresql/careertrellis-restore-tests` and `/var/lib/postgresql/careertrellis-onedrive-restore-tests`.
- The durable Caddy configuration is `/etc/caddy/Caddyfile`, with root-only environment at `/etc/caddy/careertrellis-staging.env`. Older release material referred to `/etc/caddy/Caddyfile.careertrellis-staging`; inspect the live unit/drop-in and do not restore that older path by assumption.

### Units and installed helpers

On the web host, inventory these units:

- `caddy.service`
- `careertrellis-web.service`
- `careertrellis-worker.service`
- `careertrellis-erasure-worker.service`
- `wg-quick@wg0.service`

On the database host, inventory these units:

- `postgresql.service` and the concrete cluster unit, last known as `postgresql@18-main.service`
- `wg-quick@wg0.service`
- `careertrellis-pgbackrest-full.service` and `.timer`
- `careertrellis-pgbackrest-differential.service` and `.timer`
- `careertrellis-pgbackrest-onedrive.service` and `.timer`

The checked-in backup schedules are Sunday full at 03:15, Monday-Saturday differential at 03:15, and daily OneDrive mirror at 04:30, each with its configured jitter. Active/enabled state, PID, restart count, fragment/drop-ins, heartbeat age, last result, and next trigger are live facts and must be queried.

The search and erasure heartbeat files are `/var/lib/careertrellis/worker/worker-heartbeat.json` and `/var/lib/careertrellis/account-erasure/account-erasure-worker-heartbeat.json`. Treat them as mutable liveness evidence, validate their schema/owner/age, and do not use a heartbeat alone as proof that queues or provider calls are healthy.

Installed reviewed helpers live under `/usr/local/sbin/`, including `careertrellis-deploy`, `careertrellis-rollback`, `careertrellis-authority`, `careertrellis-finalize-cutover`, `careertrellis-mirror-generated-objects`, `careertrellis-test-generated-object-restore`, `careertrellis-mirror-pgbackrest-onedrive`, and `careertrellis-test-pgbackrest-restore`. Not every historical one-off wrapper is an installed product helper. Verify the chosen helper path and hash against the exact release or reviewed evidence before privileged use.

### Authority, locks, and evidence

- The installed authority helper is `/usr/local/sbin/careertrellis-authority`; obtain state with `sudo -n /usr/local/sbin/careertrellis-authority status`. Authority files are `/etc/careertrellis/authority/data-authority`, `pending-cutover`, and `post-import-recovery`. Known authority values are `sqlite-pre-cutover` and `postgres-r2-authoritative`; the hosted capability is `postgres-r2-authoritative-v1`.
- The accepted post-cutover observation was authority `postgres-r2-authoritative`, pending phase `activated`, and no activation lease. Rediscover those facts. Never infer authority from service state, environment files, or the `current` symlink, and never rewrite historical recovery evidence to make a gate pass.
- Canonical coordination paths include `/run/lock/careertrellis-maintenance.lock`, `/run/lock/careertrellis-postgres-migration.lock`, `/run/lock/careertrellis-sqlite-import.lock`, `/run/lock/careertrellis-provider-secret-rotation.lock`, `/run/lock/careertrellis-generated-object-recovery.lock`, and `/run/careertrellis-hosted-activation.lease`. One-off post-activation wrappers have used additional exact operation locks. Never remove a lock or lease merely because it exists; establish its owner PID, boot identity, phase, and receipt first.
- The protected receipt parent is `/var/backups/careertrellis`, with operational roots `final-cutover-receipts`, `paired-restore-receipts`, `post-activation-db-backup`, `post-activation-db-mirror`, `post-activation-db-restore`, `post-activation-db-restore-cleanup`, `post-activation-object-inventory`, `worker-activation`, and `worker-activation-db-preflight`. Preserve these evidence roots unless the user authorizes an exact deletion.
- Timestamped attempts, backup labels, object manifests, and receipt SHA-256 values are evidence bindings, not rolling defaults. Discover candidate receipts under the correct fixed root, then validate ownership, mode, schema, operation, release, label, result, and digest before relying on one.

### Provider and storage coordinates

- The pgBackRest R2 repository contract uses bucket `job-applier-staging-db-backups`, prefix `job-applier/staging/postgresql`, and stanza `careertrellis`. Its root-only configuration is `/etc/pgbackrest/pgbackrest.conf`; the root-only rclone configuration is `/etc/careertrellis/rclone.conf`.
- Persistent rclone remote names are `r2:` and `onedrive:`. The PostgreSQL mirror destination is `onedrive:job-applier-staging/postgresql` and is append-only under the reviewed helper. Do not mirror, restore, check, expire, or traverse it concurrently with another repository operation.
- Generated-object recovery uses the OneDrive remote name `generated-object-onedrive` and root `careertrellis/generated-objects`, with immutable generations shaped as `generated-object-onedrive:careertrellis/generated-objects/<YYYYMMDD-HHMMSSF>` containing `objects/` and `snapshot-manifest.json`. Internal helper remote names such as `generatedobjectsource:` and `generatedobjectrestore:` are ephemeral, not persistent provider identities.
- The authoritative live generated-object R2 bucket, provider endpoints, credential identities, and object keys must be read through the correct root-only environment/helper without printing them. Do not infer a deployed bucket from a test fixture.
- Google Drive replacement of the OneDrive mirrors is backlog work only. No Google Drive remote, destination, credential identity, timer, helper, or receipt is established. Do not invent `gdrive:` or remove OneDrive data until a separately authorized migration is verified and rollback retention is satisfied.
- Sanitized receipts and their SHA-256 manifest have also been read back from an operator-controlled encrypted evidence store. That independent store is not the live R2 bucket, the disposable R2 bucket, or the OneDrive recovery mirror. Its BitLocker/device/provider path and access method are intentionally not recorded here; ask the operator only when a gate actually requires access to that separately retained copy.
- Cloudflare DNS/proxy/TLS state, R2 token scopes and IP filters, WorkOS staging environment/capabilities, OneDrive OAuth identity, and DigitalOcean firewall/inventory state are external control-plane facts. Inspect them through their trusted control plane when a gate depends on them; repository examples are not proof of live state.

### Protected configuration locations

Useful root-only configuration paths on the web host are:

- `/etc/careertrellis/staging.env`
- `/etc/careertrellis/postgres-runtime.env`
- `/etc/careertrellis/postgres-worker.env`
- `/etc/careertrellis/postgres-migration.env`
- `/etc/careertrellis/postgres-erasure-worker.env`
- `/etc/careertrellis/provider-secrets.env`
- `/etc/careertrellis/object-storage.env`
- `/etc/careertrellis/search-worker.env`
- `/etc/careertrellis/account-erasure-worker.env`
- `/etc/careertrellis/account-erasure-object-storage.env`
- `/etc/careertrellis/generated-object-recovery.env`
- `/var/lib/careertrellis/generated-object-recovery/rclone.conf`

Application, worker, and Caddy runtime logs are available through their systemd journals. Caddy's default runtime logger must delete complete request URI/header and response-header maps and pseudonymize client addresses before journald receives them. The current privacy-filtered Caddy access log is `/var/log/caddy/careertrellis-staging-access-v4.log`; retained rotations share the `careertrellis-staging-access-v4-` prefix. The superseded unsuffixed, `v2`, and `v3` logs are preserved evidence, not active sinks; do not rename, truncate, or delete them as part of a release. All `/auth` and `/auth/*` access records must remain skipped, IP and User-Agent fields pseudonymized, and request Referer, Chromium client hints, Next-Action, From, Range, Sec-Purpose, X-Openai-Host-Hash, and response Location omitted.

Paths may be inventoried, but never place or print their values, hashes that fingerprint rotating secrets, database URLs/passwords, WorkOS user IDs or emails, Cloudflare account/zone/token identifiers, R2 endpoints or access keys, OAuth tokens, cookie/session data, provider keyrings, pgBackRest passphrases, or SSH/WireGuard private keys in `AGENTS.md`, commands, logs, or receipts. Transient recovery keyrings and passphrase files must use the exact reviewed wrapper path, owner, mode, and cleanup contract; do not guess a path from a prior attempt.

### Mandatory rediscovery before action

Before a consequential staging operation, rediscover the public SSH target/fingerprint, outbound egress `/32`, current/previous release targets, `/api/version`, authority/pending/recovery/lease state, operation locks, unit state, PostgreSQL/Alembic/RLS state, pgBackRest health/latest label/WAL state, provider target and token restrictions, queue counts, disk/inode capacity, disposable ports/sockets, and the exact accepted receipt chain needed by that operation.

If SSH disconnects during a one-shot operation, reconnect and identify the original PID, marker, lock, phase, and receipt. A disconnect is not a failure signal. Do not retry until terminal state is proven. When sending reviewed multiline shell from Windows, preserve LF bytes and avoid PowerShell interpolation or CRLF conversion.

## Subagent tasking for gate-clearing work

- When a request is to review, merge, deploy, proceed to the next stage, or otherwise clear multiple gates, use subagents by default when two or more evidence-gathering tasks can run independently.
- Divide work at evidence boundaries, with one owner per bounded artifact or verification surface. Good boundaries include changed-file security review, automated-test and syntax results, GitHub check/review state, deployment-runbook readiness, migration and rollback evidence, and post-deployment health checks.
- Give each subagent a concrete scope, the exact evidence it must inspect, the artifact or concise receipt it must return, and an explicit stopping condition. Prefer tasks such as “review these four changed files and return candidate findings with file/line evidence” over broad tasks such as “review the PR.”
- Keep assignments non-overlapping unless an independent second opinion is deliberately required. Do not ask multiple subagents to edit the same files, and account for every assigned artifact before declaring the gate clear.
- Subagents should gather and assess evidence but must not approve or merge PRs, deploy releases, change external state, handle or reveal secrets, or make the final gate decision unless the user explicitly delegates that authority. The primary agent owns synthesis, unresolved conflicts, final verification, and all consequential external actions.
- A subagent result is evidence, not proof by itself. The primary agent must inspect the returned receipt against the gate, reconcile contradictions or missing coverage, and report any check that remains incomplete.

## Long-running operation observation

Treat these as staging observations and polling defaults, not deadlines or SLAs. Record UTC start and stop times, elapsed seconds, sanitized object or byte counts, exact release or backup label, result, and receipt SHA-256. Revise estimates only after several comparable successful runs.

A progress poll may inspect only the already-running PID or unit, a privacy-safe phase field, append-only root-owned aggregate statistics, or the final receipt. It must not acquire the operation lock, start another helper, signal or restart the owner, traverse the same remote repository, query mutable application state, or write, delete, rename, retry, or clean either side. An SSH disconnect is not a failure signal: reconnect, identify the original PID, marker, lock, phase, and receipt, and never rerun until terminal state is proven.

| Operation | Current staging evidence | Default polling frequency | Escalation rule |
|---|---:|---:|---|
| OneDrive whole-repository hydration copy | about 2 hours 37 minutes for 26,856 objects / 108,445,872 bytes | owning PID and wrapper phase every 10 minutes; use the wrapper's root-only aggregate stats | after 30 minutes without counter movement, take one read-only PID/I/O liveness sample; do not run `rclone size`, `lsf`, `copy`, or `check` as a poll |
| Whole-repository `rclone check --download` | about 3 hours 13 minutes for the same repository | owning PID and wrapper phase every 10–15 minutes | after 30 minutes without visible progress, take one read-only PID/I/O liveness sample; do not launch a concurrent rclone traversal |
| pgBackRest full backup | measured baseline not yet retained in policy | PID, unit, and privacy-safe log every 2–5 minutes | use `pgbackrest info` before and after, not in a tight loop; never overlap backup, check, expire, restore, or mirror |
| pgBackRest OneDrive mirror | measured baseline not yet retained in policy | PID, unit, and root-only aggregate stats every 5–10 minutes | do not start a duplicate mirror or perform a full destination listing as a poll |
| Isolated PostgreSQL restore and checksum | retained-hydration redress expected in 4–10 minutes; investigate after 20 minutes | every 30 seconds during restore/start/stop and every 60 seconds during quiet checksum work | inspect only PID, phase, port, and receipt; never start the restored cluster manually or touch the live cluster |
| Generated-object mirror or disposable restore | measured baseline not yet retained in policy | owning PID, phase, and receipt every 1–2 minutes | never start a second helper, list the target repeatedly, retry, or clean up as a poll |
| Migration, import, and reconciliation | measured baseline not yet retained in policy | coordinator PID, durable pending phase, and root-only log every 30–60 seconds | never rerun the importer or issue mutation or live row-count queries as progress checks |
| Worker activation | wrapper includes bounded heartbeat/readiness loops and a required 20-second stability window | normally wait for the wrapper; after 60 seconds inspect only PID, phase, heartbeat age, and receipt | never duplicate start/enable/restart, shorten the stability window, or invoke provider/queue work |

Prefer reviewed wrapper-native output such as `--stats 5m --stats-one-line-date` in a root-only log over external polling. Never expose remote paths, object names, identifiers, URLs, or credentials in sanitized progress receipts.

## Completion criteria

Before declaring work complete, inspect `git diff` and `git status`, run the P0 tests and syntax check above, and confirm no protected files are staged. For runtime or UI changes, start the app, verify `/api/version` and `/`, exercise the changed flow, then stop it cleanly. Report any check that could not be run and why.
