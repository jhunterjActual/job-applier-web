# Product Backlog

This backlog consolidates the code-review, job-seeker persona, product-owner, monetization, and resume/PDF-formatting recommendations.

## P0 - Reliability, safety, and document integrity

- [x] Retire automatic browser-based application filling and submission attempts, including headed-mode controls and automation-specific retry states; replace them with a dependable guided-manual flow that opens the verified posting, presents the prepared materials and application checklist, and records progress only when the user confirms it.
- [x] Make every manual-application artifact explicit and downloadable: persist the generated cover letter as a safely named `.txt` file alongside the resume PDF, regenerate it after edits, and expose clearly labeled Resume PDF and Cover Letter TXT downloads from both Search & Match and Application Logs.
- [x] Prevent cover-letter upload files from overwriting tailored resume PDFs.
- [x] Distinguish `form_filled`, unverified `submitted`, and destination-confirmed `applied` states.
- [x] Record lifecycle timestamps and submission evidence without treating tailoring as an application date.
- [x] Require human review for sensitive questions and unmatched dropdown values; never guess fallback answers.
- [x] Add regression coverage for lifecycle mapping, sensitive fields, upload paths, and safe Markdown rendering.
- [x] Enforce a two-page resume contract, with a compact rendering retry and a hard rejection instead of truncation.
- [x] Prevent experience entries and individual bullets from splitting across pages when the entry fits on one page.
- [x] Escape untrusted resume content and correctly handle headings, bullets, bold, italics, and separators.
- [x] Use role-neutral resume-writing instructions with concise summary and bullet limits.

## P1 - Core workflow and security

- [x] Mark a job as manually applied from either matched or tailored state, with an editable application date and method.
- [x] Fix lifecycle date-field accessibility and usability: keep all date segments legible while one segment is selected, preserve visible focus/selection contrast in the dark theme, and provide a date control supporting both calendar selection and validated direct text entry.
- [x] Preview and bulk archive or delete untouched jobs while preserving generated materials and application history.
- [x] Add reversible archive/restore recovery for bulk cleanup.
- [x] Preserve privacy-safe tombstones for manually or permanently deleted job postings so later **Search & Analyze** runs suppress the same canonical posting instead of restoring it; provide a way to review and clear suppressed-posting records when the user wants to rediscover a job.
- [x] Add undo/recovery for other lifecycle corrections.
- [x] Replace frontend `innerHTML` interpolation of job data with safe DOM construction and validated links.
- [x] Stop returning plaintext API keys from profile reads; mask keys and use dedicated secret-update operations.
- [x] Restrict CORS and define the supported local-only deployment threat model.
- [x] Enforce the local browser trust boundary: reject non-loopback Host headers, block cross-site unsafe requests using Origin/Referer and Fetch Metadata, and cap streamed resume uploads at 2 MB.
- [x] Add pipeline statuses with notes, follow-up dates, interviews, offers, rejections, withdrawals, and closed roles.
- [x] Add saved searches, duplicate detection, and on-demand expired-posting checks.
- [x] Add a consistent user-controlled **Stop** action for long-running progress-modal operations, including **Search & Analyze**, **Preview Details**, **Save Job** and its match analysis, **Tailor Materials**, resume parsing/import, listing verification, PDF regeneration, and comparable provider, network, AI, or document-generation work. Propagate cancellation through the frontend and backend; show a responsive **Stopping...** state; define safe commit boundaries; preserve previously saved data/materials plus safely completed partial results and diagnostics; leave a job saved but clearly unscored if post-save analysis is stopped; and report a clear canceled/partial outcome without treating user cancellation as a source failure. Do not offer misleading cancellation once a short atomic database change has committed.
- [x] Add a manual job-import workflow that accepts a posting URL from any site, validates and canonicalizes the URL, detects duplicates, extracts available job details with a preview before saving, and allows the user to complete or correct fields when the source cannot be parsed.
- [x] After a manually added job is saved, refresh the job-postings list, reveal and scroll its results frame to the new row, and briefly focus or highlight that row so the user can confirm the import and continue working. If active filters, sorting, or pagination would hide the posting, reveal it intentionally and explain any temporary view change instead of failing silently.
- [x] Extend manual-import extraction for framed or script-only career portals such as branded iCIMS pages, and distinguish an embedded-content or anti-bot access challenge from a posting that simply lacks structured data while preserving manual field entry as the fallback.
- [x] Add optional scheduled alerts for saved searches.
- [x] Detect job-source format drift using provider health checks, expected content/selector contracts, and rejection-rate baselines; alert the user when a provider appears incompatible instead of silently returning no results, including a safe diagnostic summary and affected provider.
- [x] Make Lever source diagnostics resilient and specific: use the official read-only Lever Postings JSON API as the primary job-detail source; classify missing, removed, or expired postings as stale instead of format drift; reserve format-drift alerts for successful responses with invalid schemas; retain privacy-safe rejection-category counts; and use browser scraping only when the API is unavailable.
- [x] Restrict Playwright job-page redirects and subresources to public HTTP(S) destinations by resolving and blocking loopback, link-local, private-network, and non-web targets without breaking supported ATS providers.
- [x] Add a saved profile toggle to prefer a U.S. headquarters address when an employer has multiple headquarters, incorporate it into address resolution for unemployment reporting, and reject non-U.S. Places matches from the U.S.-preferred lookup before a verified global fallback.
- [x] Add sortable/filterable job results with status and a user-adjustable minimum match-score control.
- [x] Enrich job results with normalized location, work arrangement, compensation, date found, and source fields.
- [x] Add editable resume and cover-letter preview with PDF regeneration before applying.
- [x] Add deterministic section templates before PDF generation.
- [x] Add IT, technical-executive, general-professional, federal, healthcare, education, sales, trades/operations, academic-CV, and cover-letter modes.

## P2 - Broader job-seeker value

- [x] Replace the inherited GCD site icon with product-specific branding, including appropriate favicon, pinned-tab, application-manifest, and mobile home-screen assets.
- [x] Rename the product to CareerTrellis so its market identity reflects a privacy-first, user-controlled search, tailoring, tracking, and preparation workspace rather than automatic application submission.
- [ ] Before public launch, complete professional trademark, corporate-name, app-store, domain, and social-handle clearance for CareerTrellis; secure the selected properties and then decide whether to rename the GitHub repository and legacy compatibility identifiers.
- [x] Show a privacy-safe "Maps API key saved" state and replacement guidance in Profile & Resume, matching the existing Gemini-key experience without revealing either stored key.
- [x] Add an AI-provider abstraction with securely stored bring-your-own keys, model selection, capability validation, and clear provider-specific errors; support providers beyond Gemini without exposing secrets to the browser.
- [x] Add a geocoding/maps-provider abstraction with provider selection, securely stored credentials, validation, rate-limit handling, and support for alternatives to Google Maps.
- [x] Polish the cleanup-preview modal action bar: prevent Restore counts and labels from wrapping awkwardly, use consistent button sizing and icon alignment, establish a clearer primary/secondary/destructive hierarchy, and stack actions cleanly at narrow widths.
- [x] Support multiple named base resumes with safe selection, duplication and deletion; automatic snapshots for meaningful saves; previewable, non-destructive version restore; migration of existing resume content; and source-resume/version attribution on newly tailored materials.
- [x] Import DOCX/PDF resumes with an explicit, provider-backed OCR fallback for scanned pages and export an accessible DOCX alternative with semantic headings, real lists, editable text, and clickable contact links.
- [x] Add deterministic, local job-result filters for compensation and contract rate, employment type, shift/on-call schedule, travel, commute/work arrangement, sponsorship, clearance, professional licenses, and physical/outdoor work conditions; include unspecified details by default and show the detected requirements used by the filters.
- [x] Add per-base-resume, role-specific professional evidence sections for skills, projects, portfolio links, licenses, certifications, and work samples; version and restore them with the resume, provide mode-aware entry guidance, and supply only user-entered evidence to the applicable tailoring sections without inventing missing details.
- [x] Add local application-funnel and response-rate analytics by source, role, location, resume version, and application method, with clear response definitions, outcome counts, timing, and small-sample guidance.
- [x] Add an interview-preparation workspace with role-specific research prompts, likely questions, candidate evidence/STAR-story planning, questions for the hiring team, editable notes, and printable or downloadable output.
- [x] Add a versioned, human-readable local data export covering profile settings, resume history, jobs, applications, saved searches, deletion choices, and privacy-safe diagnostics while excluding stored API keys, host file paths, URL fingerprints, and provider caches.
- [x] Add password-encrypted full backup and safe restore with compatibility checks, explicit conflict handling, and recovery from interrupted imports.
- [x] Add multilingual assistance for profile guidance, search workflows, generated-material review, and user-facing validation without silently translating factual resume content.
- [x] Complete a mobile-responsiveness and accessibility QA pass across keyboard, screen-reader, contrast, focus, motion, zoom, reflow, modal, table, and document workflows.
- [x] Design an opt-in, privacy-preserving provider-failure reporting mechanism for broader releases so repeated format-drift alerts can notify maintainers without transmitting resumes, API keys, job-application data, or unnecessary browsing details.
- [x] Persist a privacy-safe local history of the important diagnostic elements that triggered each **Some job sources may need attention** notice—including timestamp, provider, diagnostic code, and bounded aggregate counters—and provide retrieval or export for troubleshooting without storing job URLs, descriptions, searches, resumes, or credentials.
- [x] Make the **Some job sources may need attention** and **Search notes** box dismissible for the current view while keeping the underlying diagnostic history available for later retrieval.
- [x] Bound remote search response bytes, per-provider candidate counts, and retained job-description sizes; add explicit network timeouts and clear partial-result diagnostics when a source exceeds a budget.
- [x] Add an optional privacy-safe Sentry integration and operator setup guide. Keep local variables, request bodies, profile/resume content, job details, URLs, API keys, and generated materials out of events; document read-only Codex inspection credentials separately from the application DSN.
- [x] Replace broad mutable dependency ranges with a reproducible, reviewed lock or constraints file and an intentional dependency-update workflow.
- [x] Add recruiter, hiring-manager, referral, assessment, and networking tracking.
- [x] Add PDF metadata, accessible document semantics, clickable contact links, and recruiter-friendly filenames.

## P3 - Integrations, growth, and monetization enablement

- [ ] Support secure multi-user operation before public web hosting: authentication and account recovery, strict per-user data and file isolation, authorization checks, server-side encrypted secret storage, privacy controls, export/deletion, abuse protection, and deployment-safe database migrations.
- [ ] Add email and calendar integrations for interviews and follow-ups.
- [ ] Add a browser extension or bookmarklet for saving jobs and assisting applications.
- [ ] Add team administration, billing, usage metering, and institutional reporting.
- [ ] Offer a privacy-first freemium plan, short-duration Pro passes, BYOK Pro, and optional AI credit packs.
- [ ] Evaluate coaching/resume-review marketplace, workforce-development licensing, and white-label institutional editions.
- [ ] Add clearly disclosed ethical affiliate programs without allowing payments to affect job ranking.

## Product principles

- A clicked submit button is not proof that an application was accepted.
- Prefer a transparent guided-manual application workflow over brittle browser automation that job sites actively resist.
- Automation must not invent credentials, experience, sensitive answers, or consent.
- Preserve user history by default; archive before permanent deletion.
- Optimize for application quality and trustworthy outcomes rather than raw submission volume.
- Do not sell candidate data or accept paid employer ranking.
