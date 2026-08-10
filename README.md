# CareerTrellis

*Find, tailor, track, and prepare—on your terms.*

See [PRODUCT_BACKLOG.md](PRODUCT_BACKLOG.md) for the consolidated implementation and product roadmap.
See [BRAND_NAMING.md](BRAND_NAMING.md) for the working-name rationale, voice guardrails, and pre-launch clearance checklist.
See [PROVIDER_FAILURE_REPORTING.md](PROVIDER_FAILURE_REPORTING.md) for the review-before-sharing diagnostic-report contract and privacy boundary.

A self-contained, privacy-first workspace designed to streamline job discovery, resume tailoring, guided-manual applications, progress tracking, and interview preparation.

The application features a **FastAPI backend** that doubles as a static file server to deliver a **custom, premium dark-mode dashboard** built with clean, native web technologies (no NPM or React build pipeline required).

---

## 🌟 Key Features

1. **Intelligent Multi-Platform Discovery**:
   - Crawls major job boards: **Greenhouse**, **Lever**, **Ashby**, and **SmartRecruiters** using optimized Yahoo Search queries.
   - Runs domain-specific search queries separately to bypass search engine ranking caps, boosting relevant matches by over 300%.
   - Generalized to support Greenhouse's modern `job-boards.greenhouse.io` migration.
   - Uses a modern browser compatibility profile while rejecting job-board fallback and browser-error pages.

2. **Provider-Selectable AI Matching**:
   - Matches candidate resumes against crawled listings and computes match scores (0-100%) with key pros/cons.
   - Supports bring-your-own Google Gemini or OpenAI API keys, a user-selected model, and an explicit key/model/capability test without returning credentials to the browser.
   - **Batch AI Matching**: Groups discovered job listings into a single structured-output request to reduce provider quota usage.
   - **Keyword Caching**: Analyzes your resume to auto-suggest and store search terms, preventing redundant API calls.

3. **Combined Resume & Cover Letter Tailoring (50% Quota Savings)**:
   - Rewrites your resume highlights for specific postings and drafts custom cover letters.
   - Uses a single consolidated, schema-validated provider request returning JSON instead of separate resume and cover-letter calls.
   - Converts Markdown and plain text outputs into polished, print-ready PDF files using Playwright.

4. **Provider-Selectable Headquarters Resolution**:
   - Automatically queries and logs the full street address (including ZIP code/international details) of company headquarters.
   - Supports bring-your-own-key **Google Places** or rate-limited **OpenStreetMap Nominatim**, with successful OpenStreetMap results cached locally and provider attribution retained in application history.
   - Uses the selected AI provider only as a clearly labeled, verify-before-filing fallback when the maps lookup cannot verify an address.

5. **Guided Manual Applications**:
   - Reviews and saves tailored materials before application work begins.
   - Provides explicit, recruiter-friendly Resume PDF and Cover Letter TXT downloads.
   - Opens the verified employer posting in a new tab; you complete the employer's form and then confirm the lifecycle state with **Mark Applied**.

6. **Local Security**:
   - Stored completely on your local machine. Profile details and private API keys stay in local SQLite (`data/jobapplier.db`), while generated PDF and TXT artifacts remain in the ignored local output directory.

7. **Practical Job Filters**:
   - Narrows the saved-job list by pay or contract rate, employment type, remote/hybrid/on-site arrangement, shift and on-call expectations, travel, sponsorship, clearance, professional license, and physical/outdoor work signals.
   - Derives filter signals locally with deterministic text rules. Unspecified details remain included by default, and filtering does not change match scores or send posting details to an AI provider.

8. **Role-Specific Professional Evidence**:
   - Stores skills, projects, portfolio links, licenses, certifications, and work samples with each named base resume, including its non-destructive version history.
   - Provides guidance for each resume mode and sends only the candidate's entered evidence to the selected AI provider during user-triggered tailoring. Missing issuers, dates, outcomes, metrics, or credential status must not be inferred.

9. **Local Application Effectiveness Insights**:
   - Summarizes confirmed applications, employer responses, interviews, offers, rejections, response rates, and recorded response timing directly on the dashboard.
   - Breaks outcomes down by job source, role, location, source resume version, and application method without sending application history to an analytics or AI provider. Percentages from small samples are explicitly presented as directional rather than predictive.

10. **Interview Preparation Workspace**:
   - Opens a local, editable preparation plan from Application Logs with company-research prompts, likely questions, truthful STAR-story planning, questions for the hiring team, logistics, and follow-up notes.
   - Provides a useful local starter without an AI call. An explicit **Generate with AI** action can replace it with a grounded draft using the selected provider and saved job/application context, with a Stop action before the generated draft is committed.
   - Saves reviewed notes locally and supports safely named text downloads and print-friendly output.

11. **People & Application-Step Tracking**:
   - Tracks recruiters, hiring managers, referrals, networking contacts, and assessments beside each saved application without requiring email or calendar access.
   - Records status, contact or reference details, activity and next-action dates, and notes. Records stay local, appear in the human-readable data export, and are preserved by encrypted full backups.

12. **Accessible, Responsive Workspace**:
   - Supports skip navigation, keyboard-operated workspace and material tabs, visible focus, focus-trapped dialogs with opener restoration, screen-reader status announcements, labeled tables, and reduced-motion preferences.
   - Reflows the fixed desktop navigation, forms, filters, action groups, and dialogs for narrow screens and high zoom. Wide result tables remain keyboard-focusable horizontal regions so their columns are not silently removed.

13. **Multilingual Assistance**:
   - Saves an English or Spanish interface preference and translates the principal profile, search, job-action, validation, and generated-material review guidance.
   - Changes guidance and controls only. CareerTrellis never silently translates a saved resume, cover letter, job posting, or other user-entered factual content; review the language and claims in generated materials before use.

---

## 🛠️ Architecture

* **Frontend**: Vanilla HTML5, Vanilla CSS3 (custom dark-mode grid design, glassmorphic cards, transition glows), and Vanilla ES6+ JavaScript.
* **Backend**: FastAPI (Python), SQLite (database), Playwright (scraping and browser control), Cryptography for authenticated local backups, the `google-genai` SDK, and the OpenAI Responses REST API.

---

## 🚀 Getting Started

### Prerequisites
* Python 3.10.2 or higher.
* Google Chrome or Chromium (installed automatically by Playwright).
* A **Gemini API Key** from [Google AI Studio](https://aistudio.google.com/) or an **OpenAI API Key**.
* A Google Maps API Key with the **Places API (New)** enabled if you select Google Places. OpenStreetMap does not require a key.

### Quick Start (Windows)
We have provided automated startup scripts in the root directory:

#### Option A: Using Windows Command Prompt (Recommended)
* Double-click `run.bat` or run it from CMD:
  ```cmd
  run.bat
  ```

#### Option B: Using Windows PowerShell
1. Open PowerShell and navigate to the project directory.
2. Execute the start script:
   ```powershell
   .\run.ps1
   ```
   If your organization blocks local PowerShell scripts, use `run.bat`. A process-only alternative that does not change the system execution policy is:
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
   ```

The startup scripts will automatically set up a Python virtual environment, install the reviewed hash-locked requirements, download the matching Playwright browser binaries, start the FastAPI server, and open the dashboard in your default browser at:
👉 **[http://127.0.0.1:8001/](http://127.0.0.1:8001/)**

Port `8001` is the default. To choose another port, run `.\run.ps1 -Port 9000` in PowerShell or `run.bat 9000` in Command Prompt.

---

### Manual Installation (All OS)
If you prefer setting up manually or are on macOS/Linux:

1. **Set up Virtual Environment**:
   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
2. **Install Dependencies**:
   ```bash
   python -m pip install --require-hashes -r requirements.txt
   ```
3. **Install Playwright Browsers**:
   ```bash
   playwright install chromium
   ```
4. **Start the FastAPI Server**:
   ```bash
   uvicorn app:app --host 127.0.0.1 --port 8001 --reload
   ```
5. Open your browser and navigate to `http://127.0.0.1:8001/`.

---

## ⚙️ Configuration & Usage

1. **Set up Profile**:
   - Go to the **Profile & Resume** tab.
   - Choose **English** or **Spanish / Español** under **Interface language**. The selection is saved with the local profile and does not translate resume or job content.
   - Enter your contact details and personal links, select **Google Gemini** or **OpenAI**, choose a compatible model, and paste that provider's API key.
   - New profiles use **OpenStreetMap Nominatim** for headquarters lookups by default. You can select **Google Places** instead with a saved Places API key.
   - Each key field shows a privacy-safe **Saved** or **Not saved** status. Saved keys are never displayed; leave the field blank to keep the current key or enter a new key to replace it.
   - Save the settings, then use **Test Saved AI Provider** to validate the stored key, selected model, and structured-output capability before starting a long search.
   - Use **Test Saved Maps Provider** for a small, user-triggered capability check. The public Nominatim option identifies the app, sends at most one request per second, caches successful results, and must not be used for bulk or autocomplete traffic. Set `NOMINATIM_BASE_URL` to an approved self-hosted/third-party endpoint when public-service limits do not fit your use.
   - Keep **Prefer a U.S. headquarters address** selected when U.S. unemployment reporting requires a domestic employer address; clear it to prefer the employer's primary global headquarters.
   - Select an existing named base resume or use **New**/**Duplicate** to maintain role-specific starting points. Import a `.txt`, `.md`, `.docx`, or `.pdf` file, or copy-paste content directly. Text, Markdown, DOCX, and text-based PDF extraction stay local.
   - Scanned or image-only PDF pages are never sent to an AI provider by default. To use OCR, select **Allow my selected AI provider to OCR scanned PDF pages** before importing; only pages without readable embedded text are sent as images to the selected provider. Review the transcription before saving it.
   - Click **Save Settings**. Meaningful name, mode, or content changes create a new version; unchanged saves do not duplicate history.
   - Use **History** to preview earlier versions and restore one non-destructively. A restore becomes a new version, and the app prevents deletion of your last base resume.
   - Use **Export My Data** to download a versioned JSON copy of your profile settings, resumes and history, jobs, applications, saved searches, deletion choices, and privacy-safe diagnostics. Stored API keys, generated-file paths, URL fingerprints, and provider caches are excluded. The file still contains personal data and should be kept private.
   - Use **Encrypted Full Backup** when you need a restorable copy of the complete local workspace. It includes the SQLite database, saved API keys, and generated materials inside an authenticated AES-256-GCM archive protected by a password-derived key. CareerTrellis never stores the password; use at least 12 characters and keep the password separately.
   - **Restore Full Backup** validates the password, authenticated archive, file inventory, checksums, database integrity, schema, and minimum compatible build before changing live data. Restore explicitly replaces the current database and generated-materials folder, while retaining the immediately preceding workspace under ignored local `data/restore-recovery/` storage for manual recovery. Finish other work before restoring, and periodically remove recovery copies you no longer need because they contain unencrypted local profile data and saved keys just like the live database.
2. **Search for Jobs**:
   - Go to the **Search & Match** tab.
   - Enter search keywords (e.g. `"Enterprise Architect"`) or leave blank to use keywords auto-suggested from your resume.
   - Enter locations (e.g. `San Francisco, CA; Remote` separated by semicolons) to search multiple markets at once.
   - Click **Search & Analyze**.
3. **Tailor Materials**:
   - Under **Discovered Job Postings**, click **Tailor Materials** next to a matched job.
   - Review your tailored resume and cover letter. The two-page PDF includes recruiter-readable document properties, tagged headings/lists, an outline, and clickable contact details. The editable, accessible DOCX provides semantic headings, real lists, and clickable contact links as an alternative.
4. **Apply**:
   - Click **Apply Manually**, review and save edits, and download the **Resume PDF** or **Accessible DOCX** plus the **Cover Letter TXT**. Download names begin with your saved candidate name when available, followed by the employer, target role, and artifact type so recruiters can identify the files outside CareerTrellis.
   - Click **Open Application Site**, complete the employer's form yourself, and return to use **Mark Applied** with the correct date and method.

### Preview and clean up untouched jobs

1. Open **Search & Match** and click **Clean Up** beside **Refresh**.
2. Review the exact counts for jobs eligible to archive, permanently delete, or restore.
3. Choose **Archive Untouched** for a reversible cleanup. Tailored jobs and every job with application history are protected.
4. Use **Restore Archived** to return archived jobs to the active list, or **Delete Permanently** only after reviewing both confirmation prompts.

The server validates that the candidate set has not changed since the preview. If a search adds or changes jobs, refresh the cleanup preview before continuing.

Permanent deletion also creates a privacy-minimized local suppression record so the same canonical posting does not return during a later search. Open **Clean Up** to review suppressed postings and allow one or all of them to be discovered again; each record stores a URL fingerprint and bounded display labels, not the full posting URL or description.

### P1 workflow controls

- Use **Mark Applied** on a matched or tailored job to record a manually completed application with its date, method, notes, and follow-up date.
- Use **Update Status** to record interviews, offers, rejections, withdrawals, or closed roles. **Undo Last Change** restores the previous recorded state.
- From **Application Logs**, open **People & Steps** to record recruiters, hiring managers, referrals, networking contacts, assessments, and their next actions. These records are deliberately manual and local; CareerTrellis does not contact anyone or access email on your behalf.
- Save frequently used keyword/location combinations above the search form. Use the score, status, and sort controls to focus the active list.
- Saved searches can optionally provide daily or weekly local reminders while the app is running; rerunning the saved search advances its next reminder.
- Use the circular-arrow action on a job row to recheck whether the employer posting remains available.
- Use **Add Job by URL** when search does not find a posting. The app validates the public URL, detects an existing canonical match, previews extracted details from supported, generic, and readable framed job pages, and lets you correct required fields before saving. If a site blocks automated reading or protects an embedded frame, the preview explains that the posting could not be read while preserving manual entry. A configured key for the selected AI provider adds match analysis; otherwise the imported job remains visible as **Unscored** and can still be tailored.
- Provider health diagnostics distinguish stale postings, access challenges, provider failures, and likely ATS URL or data-format changes instead of silently hiding a failing source. Lever details use its public Postings API first and fall back to browser extraction only when that API is unavailable.
- Remote searches use explicit time, download-size, candidate-count, and retained-description budgets. When a provider reaches one of those limits, completed results remain available and **Search notes** explains why that provider's results may be partial.
- Source warnings and search notes can be dismissed for the current view. **Source diagnostics** retains the newest 500 local notices for later review, JSON export, or explicit clearing. History contains only timestamps, allowlisted provider/code values, and bounded aggregate counters; it excludes searches, locations, URLs, job details, resumes, credentials, rendered messages, and raw errors.
- When an actionable source warning may need maintainer attention, **Prepare Maintainer Report** creates a minimized local preview grouped by provider and diagnostic code. Nothing is sent automatically: copying stays on the device, while **Open GitHub Issue Draft** sends exactly the displayed report to `github.com` in a still-editable draft only after the user clicks it. Stale-posting and ordinary partial-result notes are excluded.
- Select the named base resume and its resume mode under **Profile & Resume** before tailoring. The selected resume and version are recorded with newly generated materials for future outcome analysis. Generated resume and cover-letter text can be edited in **View Materials**; **Save Edits** regenerates both downloadable artifacts.
- Each resume mode uses a deterministic section template before PDF generation. Manual application dates accept direct `MM/DD/YYYY` or `YYYY-MM-DD` entry and also provide a calendar picker.

The app is designed for local access at `127.0.0.1`. It rejects non-loopback Host headers and cross-site state-changing browser requests, while still allowing local command-line clients that do not send browser-origin headers. Resume uploads are streamed; text and Markdown are limited to 2 MB, while bounded DOCX/PDF imports are limited to 10 MB, 12 PDF pages, and 50 MB of expanded DOCX content. Job-page browser navigation validates the initial URL, redirects, and subresources against resolved public HTTP(S) addresses so manual imports cannot reach loopback, private, link-local, or reserved network services. Cross-origin browser access is intentionally disabled, and profile reads return only API-key configuration flags—not stored key values. Do not bind the current single-user application to a public or shared network interface; public hosting requires authentication, per-user authorization, isolated files/data, and protected secret storage first.

### Optional anonymous analytics

PostHog analytics is disabled unless `POSTHOG_PROJECT_TOKEN` is present in the process environment. `POSTHOG_HOST` is optional and defaults to the US ingestion endpoint; use the ingestion host for your PostHog region. The variable names are also listed in `backend/.env.example`, but CareerTrellis does not automatically read `.env` files or include any real token in the repository.

When enabled, CareerTrellis creates a random installation UUID in the local ignored `data/` directory. It never uses the profile database ID and does not identify a person. Only `job_search_started`, `resume_tailored`, `manual_application_opened`, `material_downloaded`, and `job_lifecycle_updated` are sent, with allowlisted low-cardinality status, source-category, material-type, lifecycle-transition, duration-bucket, and application-version properties. Names, contact details, résumé or application text, employer and job details, URLs, search terms, filenames, paths, API keys, and database content are never included.

Analytics capture is isolated from request handling and uses short network timeouts. Failures are silently dropped. GeoIP enrichment, session replay, automatic exception capture, and local-variable capture are disabled. To opt out, remove `POSTHOG_PROJECT_TOKEN` and restart CareerTrellis; the local installation UUID may be deleted separately if you do not plan to re-enable analytics.

### Optional Sentry monitoring

Sentry error and performance monitoring is disabled unless `SENTRY_DSN` is present in the process environment. CareerTrellis does not commit or automatically load local `.env` files. Before launching from PowerShell, configure the current process with `$env:SENTRY_DSN="your-project-dsn"`; use `set "SENTRY_DSN=your-project-dsn"` in Command Prompt.

The integration uses an outbound allowlist in addition to disabling Sentry's default PII and local-variable collection. Error events retain only the exception class and bounded application module/function/line locations; exception messages, source context, local variables, request data, URLs, users, breadcrumbs, tags, and extra context are removed. Performance events retain only anonymous timing and bounded trace identifiers under the fixed `http.server` name. Tracing defaults off (`SENTRY_TRACES_SAMPLE_RATE=0.0`) and can be explicitly enabled with a value from 0 through 1; invalid values fall back to zero. Code profiling is not enabled because its payload has a broader data surface. `SENTRY_ENVIRONMENT` can distinguish development and production events. Monitoring initialization failures never prevent the local app from starting.

To verify a new project, temporarily set both `SENTRY_DSN` and `SENTRY_DEBUG_ROUTE=1`, launch CareerTrellis, and open `http://127.0.0.1:8001/sentry-debug`. The intentional error should appear in Sentry after a short delay. Remove `SENTRY_DEBUG_ROUTE` (or set it to `0`) and restart the app after verification; the endpoint otherwise returns 404. Also configure appropriate server-side scrubbing and retention in the Sentry project as defense in depth.

The application DSN only sends events and is separate from inspection credentials. For read-only Codex/Sentry-plugin inspection, create a dedicated `SENTRY_AUTH_TOKEN` with only the scopes needed to read the selected organization, project, and events (normally `org:read`, `project:read`, and `event:read`). Store that token in the Codex/plugin environment—not in CareerTrellis, its `.env` example, the database, or source control—and never substitute the DSN for it. Sentry documents the available scopes in its [API permissions guide](https://docs.sentry.io/api/permissions/).

### Startup troubleshooting

The Windows launchers validate the project virtual environment before opening the dashboard and repair it when a usable Python installation is available. They fingerprint `backend/requirements.in` and the hash-locked `backend/requirements.txt` and compare the installed application package manifest with the lock. A missing or changed fingerprint, wrong version, or unexpected package triggers a clean transactional virtual-environment rebuild, consistency check, and installation of the Chromium revision matching Playwright. The old environment is moved intact before the replacement is built at its final path and is restored automatically if setup fails. If Windows has the old environment locked, stop that copy with Ctrl+C and launch again.

Use `run.bat` when Windows execution policy blocks `.ps1` files. If the dashboard was already open during an unsuccessful launch, close that tab and launch again. The browser opens only after the expected backend build responds, and local UI assets are served without stale caching. If the configured port (default `8001`) is already occupied by an older copy, stop that terminal with Ctrl+C before relaunching.

### Updating dependencies

`backend/requirements.in` is the human-reviewed direct dependency policy. `backend/requirements.txt` is the complete transitive lock and must not be edited by hand. Every locked distribution has an exact version and SHA-256 hashes, so fresh installations fail closed if an artifact does not match the reviewed lock.

On Windows, update a direct version range in `requirements.in`, then regenerate and validate the lock from the repository root:

```powershell
.\scripts\update_dependencies.ps1
```

If local script policy blocks it, use the process-only alternative `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\update_dependencies.ps1`. The updater discovers the project environment or another usable Python 3.10.2+ installation; an explicit interpreter can still be supplied with `-Python`.

The compiler is itself installed from `scripts/dependency-tools.txt`, a complete hash-locked toolchain containing pip 26.1.2, pip-tools 7.6.0, and the Python 3.10 TOML compatibility dependency. The updater uses disposable compiler and validation environments, installs the candidate application lock with `--require-hashes`, runs `pip check`, the P0, analytics, and observability tests, and the syntax check, then atomically replaces the repository lock only after validation succeeds. Review the direct-policy change and both lock files before committing. GitHub installs both reviewed locks and tests the application on clean Windows Python 3.10 and 3.12 environments. Finally run a launcher once so the project environment and Playwright Chromium revision reconcile to the accepted lock.
