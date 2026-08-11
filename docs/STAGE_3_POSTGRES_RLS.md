# Stage 3: PostgreSQL Tenant and Migration Foundation

Stage 3 creates the PostgreSQL schema boundary required before application data can become multi-user. It is intentionally not deployed to the current staging service yet: the running application still uses its single-owner SQLite workspace, and production remains blocked.

## Security contract

- PostgreSQL major version 18 is pinned for this stage.
- Alembic runs only through the dedicated migration role, which explicitly assumes the non-login schema-owner role.
- Runtime and worker roles do not own tables, are not superusers, and do not have `BYPASSRLS`.
- The database and application schema grant no privileges to `PUBLIC`; runtime access is explicit and authentication-controlled authorization fields cannot be selected on insert or changed later.
- Every protected row has a non-null `owner_user_id`.
- Parent/child relationships use composite ownership foreign keys so records from different users cannot be joined accidentally.
- Every protected table has both `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`.
- The application must set `careertrellis.user_id` with transaction-local `set_config(..., true)` after authentication and authorization. Missing context exposes zero protected rows.
- The immutable WorkOS ID is used only for the identity-to-local-user mapping transaction. It is not copied into profile or activity tables.
- Provider secrets have only ciphertext and key identifiers in the new schema. Generated-file metadata uses random object identifiers and wrapped data keys; no local filesystem path is part of the shared schema.

The schema is a clean PostgreSQL target for a later rehearsed import. This PR does not copy the local SQLite database, upload objects, switch application queries, weaken the owner-only staging gate, or accept additional users.

## Before applying to a database host

1. Provision a separate database host; do not install PostgreSQL on the public application Droplet as the production design.
2. Establish WireGuard between the application and database hosts.
3. Bind PostgreSQL only to loopback and the WireGuard address. Block port 5432 on the public interface at both firewalls.
4. Install PostgreSQL 18 from a reviewed repository and record the exact minor version.
5. Create the `careertrellis` database as a cluster administrator.
6. Generate independent random passwords for the migration, runtime, and worker roles. Store them in the password manager and root-owned environment files, not shell history or Git.
7. Take a PostgreSQL-native backup before every migration after the database begins accepting real data.

## Bootstrap roles and migrate

Run the role bootstrap against the new empty database from a protected administrative session. Pass secrets through protected automation or a temporary root-only variable source; the example values below are placeholders.

```bash
psql --host 10.70.0.2 --username postgres --dbname careertrellis \
  --set migration_password='REPLACE' \
  --set runtime_password='REPLACE' \
  --set worker_password='REPLACE' \
  --file deploy/postgres/bootstrap-roles.sql
```

Install `deploy/config/postgres-migration.environment.example` as `/etc/careertrellis/postgres-migration.env`, replace its placeholder, set owner `root:root`, and set mode `0600`. Then apply an exact reviewed release:

```bash
sudo deploy/scripts/migrate-postgres.sh /opt/careertrellis/staging/releases/EXACT_COMMIT
psql --host 10.70.0.2 --username postgres --dbname careertrellis \
  --file deploy/postgres/grant-runtime.sql
```

The migration wrapper uses a host lock, obtains a PostgreSQL advisory lock, verifies PostgreSQL 18+, upgrades to the single Alembic head, and confirms the database is at that head. It never prints the database URL.

Do not run Alembic downgrade against staging or production. Roll application code back only to a revision compatible with the current schema; correct database changes with a reviewed forward migration. A destructive downgrade exists only for disposable development verification of this empty baseline.

## Local disposable verification

From the repository root in PowerShell:

```powershell
docker compose -f deploy/postgres/docker-compose.test.yml up -d --wait
$env:CAREERTRELLIS_POSTGRES_TEST_ADMIN_URL = "postgresql+psycopg://postgres:local-test-admin@127.0.0.1:55432/postgres"
Push-Location backend
./venv/Scripts/python.exe -m unittest -v test_postgres_rls.py
Pop-Location
Remove-Item Env:CAREERTRELLIS_POSTGRES_TEST_ADMIN_URL
docker compose -f deploy/postgres/docker-compose.test.yml down
```

The integration suite creates one disposable database and four fixed test roles inside this isolated server, runs the migration, proves cross-user reads/writes and relationships are denied, verifies transaction context is cleared before pool reuse, verifies forced RLS on every protected table, and removes its database and roles.

## Exit gate for this stage

- The Alembic history has exactly one head.
- A fresh PostgreSQL 18 database upgrades to head twice without drift or failure.
- Runtime and worker connections see no protected rows without context.
- Two synthetic users cannot read, change, relate, export, or delete one another's rows.
- Pooled connections do not retain tenant context.
- Runtime and worker roles neither own protected tables nor bypass RLS.
- No real user data, WorkOS secret, provider API key, or database password appears in tests, logs, source control, or migration SQL.

The next application stage converts repositories and request/worker transactions to this schema, rehearses the SQLite import into Hunter's mapped WorkOS identity, and adds private object-storage authorization. No beta invitations are permitted until those stages and their negative isolation tests pass.
