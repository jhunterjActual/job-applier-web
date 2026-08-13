# Stage 1 web deployment foundation

The access-restricted staging hostname is `staging-app.jhunterj.com`. This stage makes the current application repeatably deployable on Linux without representing the existing single-user SQLite application as ready for beta users.

## Security boundary

The staging path has three independent boundaries:

1. Uvicorn listens only on `127.0.0.1:8001`.
2. Caddy requires TLS and temporary Argon2id-backed HTTP Basic Authentication.
3. The application accepts the public hostname only when Caddy overwrites `X-CareerTrellis-Proxy-Token` with a separate 64-character origin secret.

Local launchers retain their loopback-only behavior. Shared-host mode fails startup when its hostname or proxy token is missing. Do not store real profile, resume, credential, or application data in staging before WorkOS authentication, tenant isolation, encrypted secret storage, and private object storage are complete.

The worker service remains disabled by default. The later bounded search-run
slice can consume only durable owner-scoped searches after both its explicit
worker flag and the separately reviewed repository cutover permit activation;
see `STAGE_3_POSTGRES_SEARCH_RUNS.md`.

## Host prerequisites

- A dedicated DigitalOcean 2 GB Ubuntu application VPS with SSH-key administration and a reserved IP address.
- Python 3, `python3-venv`, Git, curl, tar, and the official Caddy package/service.
- A root-owned checkout of the private `job-applier-web` repository at `/srv/careertrellis/job-applier-web`.
- Inbound ports 80 and 443 only; Uvicorn port 8001 must not be opened in the host or provider firewall.

The database VPS, WireGuard link, PostgreSQL, R2, and production DNS are later stages.

## One-time provisioning

From the repository checkout:

```bash
sudo bash ./deploy/scripts/provision-staging-host.sh /srv/careertrellis/job-applier-web
```

Generate the origin token without saving it in shell history:

```bash
openssl rand -hex 32
```

Put that same value in the two root-only files created by provisioning:

- `/etc/careertrellis/staging.env`
- `/etc/caddy/careertrellis-staging.env`

Generate the temporary staging password hash interactively; never put the plaintext password in either file:

```bash
caddy hash-password --algorithm argon2id
```

Replace all placeholder values, then validate the proxy configuration:

```bash
sudo caddy validate \
  --config /etc/caddy/Caddyfile.careertrellis-staging \
  --envfile /etc/caddy/careertrellis-staging.env
```

## Deploy an exact reviewed commit

Fetch the reviewed web repository commit into the root-owned source checkout, then deploy its full 40-character SHA:

```bash
sudo CAREERTRELLIS_SOURCE_DIR=/srv/careertrellis/job-applier-web \
  /usr/local/sbin/careertrellis-deploy 0123456789abcdef0123456789abcdef01234567
```

The deployment creates an immutable release-specific virtual environment, installs the hash-locked dependencies and Chromium, runs every regression suite and syntax check, atomically changes the `current` symlink, restarts the supervised web and worker services, and rolls back the code symlink automatically if readiness fails. It does not fetch an unreviewed branch or delete older releases.

Inspect the local origin before changing DNS:

```bash
curl --fail http://127.0.0.1:8001/health/live
curl --fail http://127.0.0.1:8001/health/ready
sudo systemctl status careertrellis-web careertrellis-worker caddy
```

After the services are locally healthy, create the Cloudflare `A` record for `staging-app.jhunterj.com` against the reserved VPS IP with proxying temporarily disabled (DNS only). Wait for Caddy to obtain its public certificate and verify HTTPS directly. Then select Cloudflare Full (strict) TLS and enable proxying. An unauthenticated external request must receive `401`; the dashboard should load only after entering the staging Basic Auth credentials.

## Rollback

Roll back to the recorded previous code release:

```bash
sudo /usr/local/sbin/careertrellis-rollback
```

Or name an already-installed exact release SHA. Stage 1 rollback changes application code only. It does not roll back SQLite data; only deploy schema-compatible code until the later Alembic expand/backfill/contract process is in place.
