# Pilot Caddy access

The initial friends-and-family pilot retains Caddy Basic Authentication in
front of WorkOS. The existing outer-gate principal remains unchanged. The one
additional principal is the opaque, fixed username `pilot-01`; it is not a
WorkOS identity, CareerTrellis account, tenant, or role and cannot provision
one.

The versioned access log skips every `/auth` route, hashes connection-IP and
User-Agent fields, and removes request Referer and response Location. The
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
manually start an application service. Leave the existing staged verifier in
place, prepare and review a forward release, and use that exact target's deploy
helper once. The forward deploy performs a privacy-safe credential `check` and
starts both enabled consumers before the web producer.

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
5. Using only boolean field-presence results, confirm the versioned access log
   and every retained versioned rotation contain no
   `/auth` entries; no raw connection, forwarded-IP, or User-Agent values; and
   no Authorization, Proxy-Authorization, `X-CareerTrellis-Proxy-Token`,
   Cookie, Set-Cookie, Referer, or Location fields. A representative non-auth
   record must retain only hashed connection-IP and User-Agent fingerprints.

The exact active `.53` release includes a root-only, read-only check for item
5. It accepts no arguments, requires the durable Caddy configuration to match
the current immutable release, reads the privacy-filtered active log plus its
plain or gzip rotations, and emits bounded counts and fixed result codes:

```bash
sudo /opt/careertrellis/staging/current/deploy/scripts/check-caddy-access-log-privacy.sh
```

The check fails closed on malformed records, unsafe file metadata, any `/auth`
record, a raw client fingerprint, a forbidden request or response field, or a
sensitive query/value shape at any bounded percent-decoding layer. The exact
fixed Basic Auth challenge is permitted only in its response-header context.
It never prints a record, path, query, header, matched value, or source
location, and never rewrites a log. It requires representative non-auth
evidence across the aggregate; an empty set is not acceptance evidence.

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
