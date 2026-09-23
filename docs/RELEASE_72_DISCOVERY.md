# Build 20260922.72: interim discovery release

This release combines the approved Tekton, CLEAR and Coder feeds with existing
key-free discovery attempts using `hybrid_feeds`. It retains truthful completion
outcomes after the progress overlay closes and fixes Lever secondary-location
matching. Serper is disabled in code and configuration; no key is installed.
Manual-import employer/site history remains plan-only.

No database migration is required: the active head remains `20260907_0014` and
recovery binding/identity safeguards are unchanged. The unused Serper quota work
is retained only as a deferred proposal. See the compatibility receipt.

## Release preparation evidence

- Local packaged suite: 690 tests passed, 52 platform/fixture skips. Full backend
  compileall and whitespace checks passed. The release helper now includes the
  source, outcome and offline completion-renderer suites in its native gate.
- Independent bounded reviews covered runtime discovery and deployment readiness.
  Lever secondary-location filtering was corrected and regression-tested.
- Public probe at 2026-09-22 15:28:16 UTC: feed counts 11/38/32; one sampled
  posting per provider had usable title, employer and description. Three feed
  requests plus detail-parser requests, zero Serper and zero AI requests.
  This is bounded sample evidence, not broad recall or sustained reliability.
- Fresh staging baseline: current `e0cd2e31cd5ac101c6cecd9a87c5e5c3562ea7bc`,
  previous `ebf372f7988a9b36e90969710ee60a0914d1272b`. Web and both workers active
  with zero restarts. Hosted authority is PostgreSQL/R2; schema `0014`, signup
  closed, session floor 1, maintenance false, active search count zero.
- Owner-recovery preflight verified the original binding and active credential
  without consuming it. Strict object/database backup status returned ok with
  no failure codes. Installed deployment/object-health helpers match reviewed
  source. No concurrent task lock or pilot acceptance was observed.
- WEB free space: 16,838,549,504 bytes, approximately 15.7 GiB. Existing release
  about 400 MiB; allow 2 GiB peak for this candidate and dependency/browser work.
  Recheck before preparation and activation. DB free space exceeds 52 GiB.

## Activation contract

Prepare the exact committed candidate with the installed reviewed deployment
helper's `--prepare-only` mode, then verify native tests and installed bytes.
Set only `CAREERTRELLIS_DISCOVERY_MODE=hybrid_feeds` in the protected search-worker
configuration, preserving its other values, metadata and enabled state. Do not
install Serper credentials, invoke migration/identity activation, or open signup.
Activate with the exact prepared release's reviewed deployment script.

Hosted authority forbids code rollback. A failed activation must retain the
helper's containment and be repaired with a newly reviewed forward release;
never manually restart Caddy/services or rewrite authority/recovery receipts.
Keep the existing release and all recovery evidence. No PR publication until
the exact commit is deployed and live-verified.

Required post-activation evidence: exact build and health, authenticated dashboard,
hybrid feed/key-free search/save/cancel, persistent truthful partial outcomes,
zero Serper use, unchanged owner binding/schema/signup, current backup health.
Local tests and a public detail sample do not satisfy live application acceptance.
Track exact commit, preparation/deployment receipts, retained release size and
cleanup in the root task artifacts so this source document need not self-reference
its own commit. SEARCH-001 stays open until those acceptance gates are met.
