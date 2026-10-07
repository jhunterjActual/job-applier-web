# Public feed discovery

Build 20260923.75 adds We Work Remotely RSS and Jobicy's public, no-key API
to `hybrid_feeds`, alongside the employer catalog and existing general searches.
Serper remains disabled. This removes no existing AI matching charges.

Sources:
- https://weworkremotely.com/remote-job-rss-feed
- https://jobicy.com/jobs-rss-feed

Only fixed public endpoints are fetched; search terms, profile data, scores and
credentials are never sent to them. Titles use the existing keyword filter.
Remote work does not imply worldwide eligibility: source geography is retained.
Listings retain the provider name and canonical provider link for attribution.
Descriptions come from structured feed data, without a browser scrape per job.
Each source has the existing 25-candidate per-search limit. These are finite
recent-listing snapshots, not comprehensive searches of every remote vacancy.

## Cache and request limits

Local DATA_DIR has a `public-job-feeds` directory containing one JSON snapshot
and one lock file per provider. An OS lock prevents simultaneous refreshes.
Successful snapshots last six hours. Failed or cancelled attempts impose a
one-hour retry cooldown, recorded before transport. Jobicy must not be polled
more than hourly; normal successful operation is at most four fetches daily.
Web requests have a 20-second socket timeout and a 30-second read deadline
(a blocking read may extend beyond the deadline until its socket timeout).
Responses are capped at four MiB; cache reads are capped at 32 MiB each.
Atomic replacement retains only the current snapshot. Expired snapshots are
not used after a failed refresh. The directory is outside generated `output`.

Only public feed payloads and refresh timestamps are cached. No user identity,
resume, search terms or scores belong here. Cache loss is recoverable by fetching
again, but deleting a cooldown record can break polling limits; do not clear the
cache as a retry mechanism. Treat snapshots as disposable after this feature is
retired, once no process references them and the request cooldown has passed.

## Inspection and verification

Matching and manual import use a fresh cached listing, or fail explicitly when
the listing is absent. Manual import does not trigger a separate feed refresh.
Explicit Verify additionally requires HTTP 200 from a bounded, redirect-free
HEAD request; 404/410 means stale, and other failures mean unverified. Cached
presence alone is not live verification. Some providers may reject HEAD; such
listings remain unverified rather than being falsely marked active.

## Deployment gate

Hosted staging/production use `/var/lib/careertrellis/public-job-feeds`, independent
of private DATA_DIR. Provisioning and deployment create or validate this directory
as worker-owned, careertrellis-group-readable, mode0750. Atomic snapshots are0640;
worker locks remain private. Only the worker has a writable systemd path; web
reads snapshots without creating a directory, writing a lock or fetching a feed.
Private data permissions and service umasks remain unchanged. Missing hosted cache
fails explicitly instead of silently creating independent caches. Rollback must
retain this directory and timestamps so it does not reset provider cooldowns.

This coordinates processes on one host, the current deployment model. Multiple
independent hosts require a centralized refresh service/store before scaling out.
Actual hosted service permissions and live web/worker inspection must still pass
after deployment; local tests alone do not close that gate.

The local implementation and synthetic tests do not establish broad repeatable
coverage or personalized live matching acceptance. Keep beta activation closed
until those distinct gates pass.
