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

## Subagent tasking for gate-clearing work

- When a request is to review, merge, deploy, proceed to the next stage, or otherwise clear multiple gates, use subagents by default when two or more evidence-gathering tasks can run independently.
- Divide work at evidence boundaries, with one owner per bounded artifact or verification surface. Good boundaries include changed-file security review, automated-test and syntax results, GitHub check/review state, deployment-runbook readiness, migration and rollback evidence, and post-deployment health checks.
- Give each subagent a concrete scope, the exact evidence it must inspect, the artifact or concise receipt it must return, and an explicit stopping condition. Prefer tasks such as “review these four changed files and return candidate findings with file/line evidence” over broad tasks such as “review the PR.”
- Keep assignments non-overlapping unless an independent second opinion is deliberately required. Do not ask multiple subagents to edit the same files, and account for every assigned artifact before declaring the gate clear.
- Subagents should gather and assess evidence but must not approve or merge PRs, deploy releases, change external state, handle or reveal secrets, or make the final gate decision unless the user explicitly delegates that authority. The primary agent owns synthesis, unresolved conflicts, final verification, and all consequential external actions.
- A subagent result is evidence, not proof by itself. The primary agent must inspect the returned receipt against the gate, reconcile contradictions or missing coverage, and report any check that remains incomplete.

## Completion criteria

Before declaring work complete, inspect `git diff` and `git status`, run the P0 tests and syntax check above, and confirm no protected files are staged. For runtime or UI changes, start the app, verify `/api/version` and `/`, exercise the changed flow, then stop it cleanly. Report any check that could not be run and why.
