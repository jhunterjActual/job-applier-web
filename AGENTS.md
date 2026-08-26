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

## Application releases and operational bundles

- Treat immutable application releases and installed operational bundles as separate revision domains. Bind the application to its exact runtime release commit and API version; bind helpers, units, and timers to their separately reviewed source commit plus installed-byte verification. An ops-bundle install must not move the application `current`/`previous` pointers, restart application workers, or change the application build.
- Create or activate an application release only when user-facing behavior, application/schema/API bytes, or an application security boundary changes. Scripts, service units, timers, diagnostics, and operational documentation alone use an ops-bundle revision and do not justify an application build bump.
- Before committing a change to systemd identity, capability, device, syscall, namespace, or `NoNewPrivileges` controls, reproduce the minimum fixed transition on a target-OS host under its native systemd and kernel. Prefer an exit-code-only transient probe with no provider call; local syntax checks are not a substitute. Retain only a sanitized result.
- When a provider failure is hidden by the normal privacy boundary, use only an explicitly authorized, short-lived root diagnostic: disable shell tracing, create a root-owned mode-`0600` file under `/run`, redirect all raw output there, emit only a fixed sanitized classification, inspect it only in the secure terminal, and remove it after terminal state is proved. Keep diagnostics read-only unless the exact mutation is separately authorized; never send raw output to journald, chat, commits, or receipts.
- After the failure cause is isolated, batch all known related operational corrections into one reviewed PR and one deployment. Do not create one PR or deployment per probed directive; split only an independently reviewable security boundary or urgent containment.
- Conserve GitHub Actions: run the applicable local tests and reviews first, produce one locally verified commit, push once, and open one PR. Inspect the specific failing log before changing code; never speculatively rerun unchanged checks. Push a follow-up only for a diagnosed correction.
- For ops-only unit or helper changes, satisfy the completion gate with host-native operational verification of the reviewed artifact; do not start or release the application unless its runtime bytes changed.

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
