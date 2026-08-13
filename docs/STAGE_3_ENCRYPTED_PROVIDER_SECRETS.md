# Stage 3: Encrypted Per-User Provider Secrets

This transition moves Gemini, OpenAI, and Google Maps credentials out of the
standalone profile row for shared-host operation. Local-only installations keep
their existing SQLite behavior. A staging or production process fails startup
unless it is explicitly configured for the encrypted PostgreSQL store.

## Security boundary

- Each credential is stored in the RLS-protected `provider_secrets` row owned
  by the authenticated local user. A missing user context exposes no row.
- Values are encrypted with AES-256-GCM before PostgreSQL receives them. Every
  encryption uses a random 96-bit nonce and authenticated data binding the
  format version, owner ID, provider, and key ID. Copying ciphertext to another
  user, provider, or key identifier therefore fails authentication.
- The database stores only the key ID and authenticated ciphertext. Encryption
  keys live in a separate root-owned systemd environment file and are not
  available to PostgreSQL, Caddy, the idle worker, Git, exports, or API reads.
- Replacing a key overwrites the encrypted value atomically. Revocation
  overwrites the prior ciphertext before retaining a timestamped tombstone.
- Provider settings are resolved from the request's authenticated owner
  context. Global environment API keys are never used as a shared-host
  fallback.

This protects database snapshots and database-host compromise from disclosing
provider keys. It does not make a running application host unable to decrypt
credentials; that host must decrypt a key briefly to call the selected
provider. Application-host hardening, log minimization, and process isolation
remain required.

The bounded consumer routing in
`STAGE_3_POSTGRES_PROVIDER_CONTEXT_READS.md` uses this authenticated secret
boundary for AI/maps capability validation, explicitly consented resume OCR,
and manual-import scoring. Each owner-scoped profile and credential transaction
closes before the external provider call begins; those consumers never fall
back to a global credential in shared-host mode.

## Root-owned keyring

Before activating this release, create
`/etc/careertrellis/provider-secrets.env` as `root:root` mode `0600`, following
`deploy/config/provider-secrets.environment.example`.

The keyring value is URL-safe base64 of compact JSON. Each JSON value is itself
URL-safe base64 of exactly 32 random bytes. Use a non-identifying key ID such as
`kek-2026-08-12-a`. Generate the key in a private password-manager or offline
terminal workflow, record the active ID and complete keyring in the password
manager, and only then install the same values with `sudoedit`. Never paste the
key, decoded JSON, or environment file into chat, a ticket, shell history,
logs, source control, or a PR.

The keyring is independent recovery material. An encrypted PostgreSQL backup
cannot restore provider credentials without the matching keyring. Confirm the
password-manager copy can be retrieved before storing a real credential.

## Rotation

1. Add a newly generated 32-byte key to the keyring while retaining every old
   key, and set its ID as `CAREERTRELLIS_PROVIDER_SECRET_ACTIVE_KEY_ID`.
2. Restart the web service and confirm readiness.
3. Run the reviewed release's bounded rotation wrapper for each owner:

   ```bash
   sudo /opt/careertrellis/staging/current/deploy/scripts/rotate-provider-secrets.sh \
     /opt/careertrellis/staging/current OWNER_USER_ID
   ```

4. Verify credential presence and provider capability through the application.
5. Take a fresh PostgreSQL backup. Remove an old key only after all active rows
   have been rotated, the backup completed, and rollback no longer depends on
   that key.

Rotation locks the selected owner's active rows and commits all replacements in
one transaction. Its output contains only the number of rotated credentials.

## SQLite import

The importer now encrypts non-empty legacy provider fields into PostgreSQL in
the same transaction as the owner's other rows. Plaintext exists only in the
read-only SQLite source and application memory; it is never written to the
target database, output, or logs. `--omit-provider-secrets` remains available
only for a deliberately incomplete disposable rehearsal.

Use a separate root-owned migration environment pointing to a disposable
database for rehearsal:

```bash
sudo /path/to/release/deploy/scripts/import-sqlite-postgres.sh \
  /opt/careertrellis/staging/releases/EXACT_MERGE_COMMIT \
  /etc/careertrellis/postgres-rehearsal.env \
  --omit-generated-files
```

The target must be empty. The wrapper uses fixed root-owned configuration,
locks concurrent imports, and emits sanitized counts only. Generated-file
paths remain an explicit blocker for final cutover until the private encrypted
object-storage stage is complete.

## Activation gate

- Exact reviewed release prepared without activation.
- Fresh PostgreSQL backup and independent encrypted mirror verified.
- WorkOS staging owner configured and read-only SQLite preflight successful.
- Keyring independently escrowed and installed with the required ownership and
  mode.
- Disposable import with encrypted provider secrets succeeds, exact counts
  match, cross-user RLS tests pass, and provider capability tests succeed.
- Owner login, logout, readiness, and code rollback are verified while Caddy's
  access restriction remains enabled.

Generated files remain a separate activation gate. Complete the private,
per-user encrypted object-storage rehearsal in
`STAGE_3_PRIVATE_OBJECT_STORAGE.md` before any final import or repository
cutover, and do not reuse this provider-secret keyring as an object keyring.
