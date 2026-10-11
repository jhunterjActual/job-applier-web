# SEARCH-003 hosted aggregate audit — approved execution complete

Evidence portability: paths marked local-only identify retained operator records excluded
from this repository. They are provenance references, not downloadable PR evidence;
dated results below are historical claims and do not establish current live acceptance.

## Approved execution update

Following the explicit /approve response, the exact previously rejected action was
retried once and succeeded at 2026-10-10 02:42:24 UTC. The
hosted receipt (local-only evidence: `../../artifacts/hosted-title-audit-result-20261010.json`) confirms
job-applier-db-staging-01, the expected database, PostgreSQL 18.4, schema revision
20261005_0023, database-wide visibility, read_only=on and repeatable-read isolation.
All two saved-search rows and two search-run rows were counted. Reserved marker,
exact-prefix, oversized and unknown-status counts were zero. No search text or
private user identifiers were returned. The transaction ended with ROLLBACK.

This closes the initial hosted aggregate collision/storage scan for that instant.
It does not establish a coordinated writer-free window, bind the web application's
current release, test the psycopg service adapter, or verify compatible rollback,
provider behavior or deployment. The executed transport used psql with the exact
aggregate SQL generated from candidate source SHA-256
36a5ad08960dd408241ee40f94b243924c1baf0413cc4e50353acddde5ddbf28.
No further remote operation, write, deployment or service change was performed.
Approval was limited to this one audit; it is not authority for later operations.

Real-task helper adoption is now evidenced: New-WorkflowSshPythonRequest prepared
stdin and Invoke-WorkflowNative executed it with stricter project SSH options.
The checked aggregate JSON was written and read back through local tooling. This
is successful bounded use, not a general prevention or time-saving claim.
Only the small local receipt and guarded document snapshots were added; no remote
files or retained processes were created. Owner and retention remain as below.
The preparation/initial rejection record below is preserved as history; its statements
that execution was blocked and no receipt existed are superseded by this update.

## Historical preparation and initial rejection

Prepared the exact database-side script (local-only evidence: `../../artifacts/hosted-title-audit-20261010.py`)
from the reviewed candidate audit's aggregate_sql function using
this local builder (local-only evidence: `../../artifacts/build-hosted-title-audit-20261010.py`).
The script is sent through SSH stdin; it installs no remote files. It asserts the
expected host, uses existing postgres peer access, executes a read-only repeatable-read
transaction with 10-second statement/2-second lock limits, queries only schema/version
metadata and aggregate counts, and rolls back. No jobs, claims, locks, migrations,
configuration, credential reads, services or deployment actions are requested.
Output is withheld unless expected database, global visibility and read-only isolation
are confirmed. Driver diagnostics are captured and replaced with a fixed failure.

Existing project inventory maps hunter@157.245.113.244 to job-applier-db-staging-01.
The local known_hosts ED25519 pin was inspected:
SHA256:80Bt9VIorRznYcqiAuppfSaunTnz9IX+u2vn0ErfO4o.
Prepared SSH options require that key algorithm, StrictHostKeyChecking=yes,
BatchMode=yes, ConnectTimeout=15, ForwardAgent=no and ClearAllForwardings=yes.
No new trust entry was added. Hostname is checked remotely before database queries.
This is retained inventory plus a local trust pin; current remote identity and database
state have NOT been observed in this continuation.

## Result and exact approval boundary

Automatic approval review rejected the exec action before process creation. Reason:
private SSH access and sudo PostgreSQL aggregate inspection require explicit user
authorization; broad Continue intent was judged insufficient. No SSH query ran and
no hosted counts/result receipt exists. Do not route around the rejection.

The concrete pending action is one invocation of the reviewed script over SSH to
hunter@157.245.113.244 using sudo -n python3 -, then sudo -n -u postgres psql against
careertrellis. Approving this action does not approve web-host access, deployment,
rollback, writes, restarts, grants, provider calls or additional queries. The system
allows /approve to authorize one retry of this exact action while auto-review remains
on. Once permitted, inspect the result before deciding any further step.

Local check receipt (local-only evidence: `../../artifacts/hosted-title-audit-local-check-20261010.json`)
confirms Python syntax and the generated SQL's read-only/no-dispatch shape. This is
not PostgreSQL integration or hosted acceptance. The application source is unchanged,
so no application build, runtime preview or repeated P0 suite was warranted. Prior
384-test evidence remains current for unchanged audit code. SEARCH-003 remains open.

## Helper adoption and retained work

PowerShell 7.6.6 FullLanguage and procedure routing were verified. Real work used
bounded reads, file discovery, Git/native wrappers, UTF-8/JSON writes and
New-WorkflowSshPythonRequest. The generic SSH runner lacks the stricter project options,
so its prepared stdin request was passed to Invoke-WorkflowNative with the required
host-key/connect/forwarding arguments added. Remote execution was blocked, not verified
helper reuse on the host. A direct wildcard rg operand failed; directory-plus-glob
recovered. A monolithic JSON search also exceeded the intended output bound; subsequent
inspection used selected structured fields and bounded files. These are adoption gaps,
not measured savings or general prevention evidence. Expected approval rejection is
not logged as operational friction.

Retain the three hosted-title audit preparation/check files, builder, this handoff,
resource receipt and associated small document-edit receipts for the next authorized
attempt. Owner: Hunter / CareerTrellis agent workflow. Review after that audit or
explicit abandonment. No remote artifacts, packages, containers or preview processes
were created. See resource receipt (local-only evidence: `../../artifacts/hosted-title-audit-resources-20261010.json`).