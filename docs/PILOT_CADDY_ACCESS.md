# Pilot Caddy access

The initial friends-and-family pilot retains Caddy Basic Authentication in
front of WorkOS. The existing outer-gate principal remains unchanged. The one
additional principal is the opaque, fixed username `pilot-01`; it is not a
WorkOS identity, CareerTrellis account, tenant, or role and cannot provision
one.

The versioned access log skips every `/auth` route, hashes connection-IP fields,
and deletes whole request and response header maps, including User-Agent. The
default Caddy runtime logger separately removes complete request URI/header and
response-header maps and hashes client addresses before systemd records it.
This keeps both sinks useful without retaining identity-provider paths or raw
client fingerprints. The superseded unsuffixed file and historical journal
records remain preserved evidence; they are not rewritten or treated as new
acceptance evidence.

The password and its Argon2id verifier must never appear in a command argument,
shell history, chat, receipt, commit, or access log. Store the distinct
high-entropy password only in the operator's password manager and share it with
the intended pilot through that manager's secure sharing mechanism.

## Prepare and install

After the exact release commit is reviewed, merged, and fetched on the staging
host, prepare it without activation:

```bash
sudo CAREERTRELLIS_SOURCE_DIR=/srv/careertrellis/job-applier-web \
  /usr/local/sbin/careertrellis-deploy --prepare-only \
  0123456789abcdef0123456789abcdef01234567
```

Create a distinct password in the password manager. Run the reviewed helper
from that exact prepared release and paste the password only into Caddy's
non-echoing interactive prompt:

```bash
sudo /opt/careertrellis/staging/releases/0123456789abcdef0123456789abcdef01234567/deploy/scripts/manage-pilot-caddy-credential.sh install
```

The helper accepts no username, password, or verifier argument. It preserves
the owner entry, rejects ambiguous environment state and unsafe file metadata,
refuses mutation outside an exact prepared release, validates that release's
Caddyfile, atomically adds only the pilot verifier,
and prints a privacy-safe staged receipt. It does not reload Caddy or enable the
new principal. Repeating `install` after a valid installation is a no-op.

Inspect the boundary without printing credentials:

```bash
sudo /opt/careertrellis/staging/releases/0123456789abcdef0123456789abcdef01234567/deploy/scripts/manage-pilot-caddy-credential.sh check
```

Then activate only that exact commit through the normal deployment helper. That
activation performs the single enabling Caddy reload. The deploy path rechecks
the root-owned mode-`0600` Caddy environment, the unique active pilot entry,
and the target release's Caddyfile before changing the release symlink. A
normal activation installs that reviewed file at the durable
`/etc/caddy/Caddyfile` path; the installed credential helper validates that same
effective path for later inspection or rotation. Historical suffixed Caddyfile
paths are inert and must not be restored by assumption. A
pre-cutover rollback applies the same checks only when its target declares the
pilot principal; hosted authority rejects rollback and requires a reviewed
forward correction.

If a hosted activation fails, do not rerun that release, invoke rollback,
rotate or reinstall the pilot credential, rewrite cutover/recovery evidence, or
manually start Caddy or an application service. Leave the existing staged verifier in
place, prepare and review a forward release, and use that exact target's deploy
helper once. The forward deploy performs a privacy-safe credential `check` and
starts both enabled consumers before the web producer. Failed activation
containment disables and stops Caddy as well as stopping the application
services; only a reviewed forward activation may re-enable the public edge.

```bash
sudo CAREERTRELLIS_SOURCE_DIR=/srv/careertrellis/job-applier-web \
  /usr/local/sbin/careertrellis-deploy \
  0123456789abcdef0123456789abcdef01234567
```

## Acceptance and containment

Use separate clean browser profiles for these checks and retain only status
classes and booleans:

Password-only WorkOS authentication is accepted for this bounded pilot.
Environment-wide MFA stays disabled so TOTP enrollment is not required;
optional passkeys may be offered separately. Factor enrollment state must not be recorded.
Invitation-only registration, verified email, and the reviewed CareerTrellis
authorization boundary remain mandatory.

1. Missing and incorrect Basic Auth credentials receive `401`.
2. The existing owner credential still passes the Caddy gate.
3. `pilot-01` passes the Caddy gate with the newly shared password.
4. Before any CareerTrellis mapping exists, the pilot's verified WorkOS
   identity still receives the fixed application `403` and no application
   session or local user row.
5. Using only boolean field-presence results, confirm the v7 access log and
   every retained v7 rotation contain no `/auth` entries, raw client addresses,
   `user_id`, request `headers`, or `resp_headers` fields. Even empty/null
   header maps fail. A representative non-auth record has both hashed
   `remote_ip` and `client_ip`, a method, and an integer HTTP status.

The v7 forward correction deletes complete request and response header maps.
Unknown names, alternate casing, and sensitive values in otherwise ordinary
headers therefore cannot survive the encoder. This deliberately drops
User-Agent fingerprints and header diagnostics; request method, status,
duration, size, and hashed connection addresses remain available.
Authentication, response headers sent to clients, and upstream forwarding
are unchanged. The default runtime logger remains independently filtered.

The root-only, read-only check accepts no arguments, requires the durable
configuration to match the current immutable release, reads the active v7 log
plus every plain/gzip v7 rotation, validates ownership/modes, and refuses an
inventory that changes during the scan:

```bash
sudo /opt/careertrellis/staging/current/deploy/scripts/check-caddy-access-log-privacy.sh
```

It emits only bounded counts and fixed result codes, never source records,
values, or locations. Missing representative evidence is a failure; do not use
`--allow-no-representative` for acceptance. Existing URI, encoded sensitive-value,
malformed-record, and raw-client-address rejection remains in force.

Preserve all v6 and earlier files, rotations, and failed receipts unchanged.
The v6 audit recorded 168 historical `unexpected_request_header` failures;
a passing v7 audit does not make those records compliant. The explicit
`scan_legacy_v6_json_lines` API retains the v6 allowlist and IP-plus-User-Agent
representative semantics for historical analysis; the active CLI never uses it.
After activation, verify v7 grows and the entire v6 inventory is unchanged.
Retirement of historical evidence needs separately authorized exact-target
retention work. Deploy and rollback gates reject the finite-list contract
through the new forward-only floor; use a reviewed forward correction.

Before release, run the synthetic native suite with `CADDY_TEST_BINARY` pointing
to Caddy 2.11.4: `python3 -m unittest test_caddy_native.py` from `backend`.
It exercises both outer-gate principals, incorrect/missing authentication,
proxy-token overwrite, Origin preservation, auth-route exclusion, arbitrary
and formerly allowed header names, runtime error logging, and native rotation.
No staging credentials, user data, provider calls, or real jobs are used.

To rotate the pilot password, create and store a new distinct password and run:

```bash
sudo /usr/local/sbin/careertrellis-caddy-pilot-credential rotate
```

Requested revocation uses the same rotation command with a new high-entropy
password that is not shared. Revocation is complete only after the command
succeeds and the prior password receives `401`.

If the reload result is ambiguous, the helper retains the candidate environment
and makes one fail-closed attempt to stop Caddy; it does not retry the reload.
Treat the pilot boundary as unavailable and revocation as unproven. Do not run
`rotate` again. Confirm that Caddy is stopped, inspect the service failure without
printing request data, and obtain review before validating the retained
configuration and starting Caddy once. If the stop result is also ambiguous,
contain access at the host/network boundary before recovery. Keep both passwords
until the prior one is proven to receive `401`; only then destroy an unshared
revocation password.

The helper refuses rotation if a rollback target no longer declares the pilot
principal. Removing the principal from the Caddyfile requires a reviewed forward
release. No PostgreSQL, R2, WorkOS, Cloudflare, backup, or restore action is part
of this outer-gate change.
