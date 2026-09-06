# Planned-maintenance announcements

CareerTrellis is owned by J. Hunter Johnson LLC. This feature publishes an
operator-approved, public, plain-text notice; it is **not** an outage switch.
It never stops services, blocks searches, discards forms, or invokes a provider.
The first installation is part of an application release. Subsequent notice
changes require no release, deployment, application restart, or database change.

## Operator boundary

Only an authorized root operator on the web host can change the notice. There
is no HTTP write endpoint and no tenant/owner dashboard control. Use the reviewed
`backend/maintenance_notice.py` from the exact deployed immutable release with
its trusted Python interpreter. Do not run root Python from a writable checkout,
accept a caller-supplied destination, or modify environment/credential files.

The fixed state is `/etc/careertrellis/announcements/current.json`, root-owned
mode `0644` in a root-owned, non-writable-to-others directory. It contains only
public notice text and privacy-safe audit metadata. No credentials, personal
details, identities, internal URLs, resume text, or operational diagnostics
belong in a notice. The web process can read this file but cannot change it.
Ancestors must be root-owned real directories, not symlinks, without group or
other write permission. A missing notice is normal; malformed, unsafe or
unreadable existing state yields a fixed API error without blocking the app.

Updates hold an exclusive nonblocking `.operator.lock`, validate the previous
snapshot, and atomically replace the notice and audit history together, with
file and parent-directory fsync. Each audit entry records only an opaque
revision, UTC timestamp and action (`set` or `disable`). OS SSH/sudo auditing
remains the operator-attribution layer. There is no free-text or identity field
in the audit. History is not silently pruned: at 1,000 changes or 256 KiB the
command refuses a further update. Any future archival/retention change requires
its own reviewed procedure. Do not remove history as routine cleanup.

## Commands and payload

First rediscover the host, resolve `current`, verify its full commit and build,
and compare the deployed source bytes to the reviewed candidate. The command
below uses placeholders intentionally; substitute only the exact verified
40-character release commit. Use the same release path for both interpreter
and script. Do not resolve two mutable `current` paths independently.

```sh
sudo -n env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  /opt/careertrellis/staging/releases/<verified-commit>/backend/venv/bin/python -I \
  /opt/careertrellis/staging/releases/<verified-commit>/backend/maintenance_notice.py inspect
```

`inspect` is read-only and prints configured state, revision and audit count,
never notice content. It does not create directories or acquire a lock. To
create, replace, reschedule or update status, invoke the same command with
`set`, supplying **one** approved JSON object on stdin (maximum 4,096 bytes).
To remove the announcement, use `disable` with no payload; it retains audit
history and clears the current notice atomically. Both successful writes return
an opaque revision. All failures return one fixed error and nonzero status.
If a command disconnects or fails after replacement may have begun, inspect the
revision and public result before retrying. Do not start a second writer or
delete a lock file to bypass contention.

Payload example (replace every date with the approved future window):

```json
{
  "message": "Please save your changes before planned maintenance.",
  "impact": "Search and tailoring may be unavailable during this window.",
  "status": "planned",
  "time_zone": "America/New_York",
  "publish_at": "2026-09-10T12:00:00Z",
  "starts_at": "2026-09-11T04:00:00Z",
  "ends_at": "2026-09-11T05:00:00Z",
  "expires_at": "2026-09-11T05:15:00Z"
}
```

- `publish_at` schedules when the notice first becomes visible, independently
  of the maintenance window. The current single notice is replaced immediately;
  this is not a queue of multiple future notices.
- All times require seconds and an explicit UTC offset or `Z`. Values normalize
  to UTC. `time_zone` must be a valid IANA zone (for example `America/New_York`),
  used for the displayed dates and daylight-saving abbreviation.
- Require `publish_at <= starts_at < ends_at <= expires_at`. The maintenance
  window is at most seven days; notice lead time is at most 90 days; expiry is
  at most 24 hours after the window ends. New settings must not already be expired.
- `status` is explicitly operator-controlled: `planned`, `in_progress`,
  `completed`, or `cancelled`. Time passing does not assert that services stopped
  or that maintenance succeeded. Update status after observing the real state.
- Message and impact are bounded plain text (400 and 240 characters). Unknown
  fields, duplicate JSON keys, control/bidi characters, markup and URL-like
  prompts are rejected. The browser uses `textContent`, never HTML interpolation.

## User experience and acceptance

The dashboard and app-owned `/auth/sign-in` page show the notice, window, time
zone, expected impact and status. `/auth/login` still begins the original WorkOS
PKCE flow; the new landing page does not authorize a user, change invitation
policy or implement MFA. Existing Caddy protection remains in front of both.
The external WorkOS-hosted form itself is not modified.

Only the notice JSON, new landing route and two specific CSS/JS assets become
public through the application authentication boundary. Other static files and
tenant APIs remain private. Notices, landing HTML and assets use `no-store`.
Open visible pages poll every 30 seconds, with an eight-second request timeout,
and refresh on focus/visibility restoration. Polling only changes banner nodes,
never application state, modal state, focus, or unsaved fields. A dedicated
polite live region outside the inert app container announces only changed
content, not every poll. No manual screen-reader certification is implied.

Known notices survive transient polling failures but expire locally using a
server-relative monotonic deadline anchored before response transit. The API
also uses no client clock to determine publication. A second browser wall-clock
deadline expires a notice if a mobile monotonic clock pauses during device sleep;
neither clock can extend the other deadline. The API
also omits unpublished and expired notices. An offline/stopped application cannot
serve new sign-in pages or announcements; a separate static outage response is
future work, not supplied by this feature.

Before acceptance, verify the exact deployed candidate and run the protected-
environment compatibility probe described in `PILOT_AUTHORIZATION.md`. Then,
with separately approved public test wording/window, exercise set/update,
scheduled publication, disable and expiry on sign-in and a signed-in page;
retain the opaque revisions and sanitized outcomes. Verify an unsaved form and
open materials modal stay intact, ordinary login still succeeds, and audit
counts advance. No service stop, backup/restore cycle or provider action is
required. Mark backlog items complete only after successful live verification.

Local regression commands include `test_maintenance_notice.py`,
`test_maintenance_notice_browser.py` (installed Chromium; all network intercepted),
and `test_maintenance_notice_cli.py` (Linux root with disposable synthetic paths),
plus the normal P0/object suites and shared-route inventory check.
