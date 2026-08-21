# Pilot Caddy access

The initial friends-and-family pilot retains Caddy Basic Authentication in
front of WorkOS. The existing outer-gate principal remains unchanged. The one
additional principal is the opaque, fixed username `pilot-01`; it is not a
WorkOS identity, CareerTrellis account, tenant, or role and cannot provision
one.

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
pre-cutover rollback applies the same checks only when its target declares the
pilot principal; hosted authority rejects rollback and requires a reviewed
forward correction.

```bash
sudo CAREERTRELLIS_SOURCE_DIR=/srv/careertrellis/job-applier-web \
  /usr/local/sbin/careertrellis-deploy \
  0123456789abcdef0123456789abcdef01234567
```

## Acceptance and containment

Use separate clean browser profiles for these checks and retain only status
classes and booleans:

1. Missing and incorrect Basic Auth credentials receive `401`.
2. The existing owner credential still passes the Caddy gate.
3. `pilot-01` passes the Caddy gate with the newly shared password.
4. Before any CareerTrellis mapping exists, the pilot's verified WorkOS
   identity still receives the fixed application `403` and no application
   session or local user row.
5. Access logs contain no Authorization, Proxy-Authorization, Cookie, or
   Set-Cookie fields and no authentication callback entry.

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
