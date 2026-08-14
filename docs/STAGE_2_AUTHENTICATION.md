# Stage 2 owner-only authentication boundary

Stage 2 replaces application access based solely on a shared staging password with a WorkOS AuthKit session. It deliberately authorizes exactly one immutable WorkOS user ID because the current SQLite records and files do not yet have per-user ownership keys.

The Caddy Basic Authentication gate remains enabled during this stage as a temporary outer boundary. The app still requires its separate reverse-proxy token, and Uvicorn still listens only on loopback.

## What this stage does

- Uses the WorkOS-hosted AuthKit UI for sign-in and account recovery.
- Uses authorization-code flow with PKCE plus a ten-minute encrypted, replay-resistant state cookie.
- Keeps access and refresh tokens inside a Fernet-encrypted, `HttpOnly`, `Secure`, `SameSite=Lax`, `__Host-` session cookie.
- Validates the WorkOS session on every non-health request, refreshes expired access tokens, and preserves the old cookie when the refresh service has a transient network failure.
- Requires a verified email, rejects impersonated sessions, and compares the immutable WorkOS user ID with the one configured staging owner.
- Creates a privacy-minimized SQLite mapping containing the WorkOS user ID, local role, timestamps, and disablement state. It does not copy the WorkOS email or profile.
- Returns `401` to unauthenticated API clients and redirects unauthenticated browser navigations to AuthKit. Health endpoints remain available to Caddy and deployment checks.
- Excludes the OAuth callback from Caddy access logs, disables the duplicate
  Uvicorn access log, uses `same-origin` on application pages so authenticated
  form posts retain a verifiable origin, and applies `no-referrer` to auth
  responses so callback codes and state are not persisted or forwarded.

This is not general multi-user support. Do not invite beta users or store real private data on the VPS yet. PostgreSQL ownership keys/RLS, private object storage, encrypted provider-secret storage, authorization of every data operation, abuse protection, deletion/export completion, and expand/backfill/contract migrations remain later gates.

## Before the release: WorkOS setup

Use a separate WorkOS staging environment; do not reuse future production credentials.

1. Enable AuthKit and configure registration as invitation-only.
2. Add the exact callback URI `https://staging-app.jhunterj.com/auth/callback`.
3. Add the exact post-logout URI `https://staging-app.jhunterj.com/`.
4. Require email verification and MFA in the staging authentication policy.
5. Invite only the staging owner, complete that account's verification and MFA enrollment, and copy its immutable `user_...` ID from WorkOS.
6. Confirm that password reset/account recovery is enabled in the hosted AuthKit flow and that its email sender is acceptable for staging.
7. Record the staging API key and client ID. Do not paste any secret into GitHub, a PR, chat, application logs, or the repository.

No DNS, firewall, or reserved-IP change is needed. `staging-app.jhunterj.com` remains the only allowed public hostname.

## Before deployment: prepare the Droplet

The existing release ignores these new variables, so it is safe to prepare the root-owned environment file before changing code.

Generate the session-cookie key on the Droplet:

```bash
python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

Use `sudoedit /etc/careertrellis/staging.env` and add:

```dotenv
CAREERTRELLIS_AUTH_MODE=workos
WORKOS_API_KEY=your-staging-api-key
WORKOS_CLIENT_ID=your-staging-client-id
WORKOS_COOKIE_PASSWORD=the-generated-url-safe-base64-value
WORKOS_REDIRECT_URI=https://staging-app.jhunterj.com/auth/callback
WORKOS_LOGOUT_RETURN_TO=https://staging-app.jhunterj.com/
CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID=user_REPLACE_WITH_IMMUTABLE_ID
```

Verify the file remains owned by root with mode `0600`. The cookie key must be retained across releases: replacing it signs out every browser. Never print the completed environment file.

## During deployment

Merge only after CI and review pass. Fetch the reviewed commit into the root-owned source checkout, then deploy the exact 40-character merge SHA with the existing release command:

```bash
sudo CAREERTRELLIS_SOURCE_DIR=/srv/careertrellis/job-applier-web \
  /usr/local/sbin/careertrellis-deploy 0123456789abcdef0123456789abcdef01234567
```

The deployment remains rollback-safe: its readiness check now verifies the WorkOS configuration before activating the release, and the `app_users` migration is additive and idempotent. If readiness fails, the deployment script restores the previous code. The previous Stage 1 code ignores the added environment values.

Do not remove Caddy Basic Authentication in this deployment. It limits exposure while WorkOS behavior is being tested.

## After deployment: acceptance checks

Perform these checks in a private/incognito browser as well as the owner browser:

1. Confirm the outer Basic Authentication challenge still appears.
2. After passing it, confirm an unauthenticated visit redirects to the WorkOS-hosted sign-in screen.
3. Sign in as the configured owner and confirm the dashboard loads and `Sign out` appears.
4. Sign out and confirm the WorkOS session ends and the dashboard requires sign-in again.
5. Exercise `Forgot password`/account recovery and confirm the reset link returns to the staging sign-in flow.
6. Attempt sign-in with a separate verified WorkOS staging user and confirm the app returns `403` without creating another local owner.
7. Confirm `curl --fail http://127.0.0.1:8001/health/ready` succeeds on the Droplet and that no WorkOS keys, tokens, email addresses, callback codes, or cookie values appear in application/Caddy logs.

Keep the Basic Authentication gate until these checks pass and a separate Caddy-removal change is reviewed. Even after its removal, staging stays owner-only. Beta invitations wait for the data-isolation stage.

## Rollback and recovery notes

- Code rollback uses `sudo /usr/local/sbin/careertrellis-rollback` as before.
- A rollback does not remove the additive `app_users` table and does not need to.
- Disabling or deleting the owner in WorkOS terminates future authentication after existing session validation/refresh reflects that state. For immediate containment, restore the outer Basic Authentication gate if it was removed and revoke the WorkOS session in the WorkOS dashboard.
- Changing the configured immutable owner ID does not silently reassign the workspace. The existing owner mapping causes the callback to fail closed; an owner-account migration requires an explicit reviewed database procedure.

The later shared-host account-erasure boundary deletes CareerTrellis application
data only. Its accepted `202` response clears the local CareerTrellis cookie and
the disabled owner mapping rejects subsequent application authorization, but it
does not delete the WorkOS identity or claim global WorkOS session revocation.
See `STAGE_3_POSTGRES_ACCOUNT_ERASURE.md`.
