\set ON_ERROR_STOP on

-- Supply passwords only as psql variables or a protected passfile. Example:
-- psql ... -v migration_password='...' -v runtime_password='...' -v worker_password='...' -f bootstrap-roles.sql
\if :{?migration_password}
\else
\echo 'migration_password is required'
\quit
\endif
\if :{?runtime_password}
\else
\echo 'runtime_password is required'
\quit
\endif
\if :{?worker_password}
\else
\echo 'worker_password is required'
\quit
\endif

SELECT 'CREATE ROLE careertrellis_schema_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'careertrellis_schema_owner')
\gexec
ALTER ROLE careertrellis_schema_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

SELECT format(
    'CREATE ROLE careertrellis_migration LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'migration_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'careertrellis_migration')
\gexec
SELECT format('ALTER ROLE careertrellis_migration PASSWORD %L', :'migration_password')
\gexec
ALTER ROLE careertrellis_migration LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

SELECT format(
    'CREATE ROLE careertrellis_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'careertrellis_runtime')
\gexec
SELECT format('ALTER ROLE careertrellis_runtime PASSWORD %L', :'runtime_password')
\gexec
ALTER ROLE careertrellis_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

SELECT format(
    'CREATE ROLE careertrellis_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'worker_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'careertrellis_worker')
\gexec
SELECT format('ALTER ROLE careertrellis_worker PASSWORD %L', :'worker_password')
\gexec
ALTER ROLE careertrellis_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

GRANT careertrellis_schema_owner TO careertrellis_migration;
REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"DBNAME" TO careertrellis_migration, careertrellis_runtime, careertrellis_worker;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS careertrellis AUTHORIZATION careertrellis_schema_owner;
ALTER SCHEMA careertrellis OWNER TO careertrellis_schema_owner;
REVOKE ALL ON SCHEMA careertrellis FROM PUBLIC;

SELECT format(
    'ALTER ROLE %I IN DATABASE %I SET search_path = careertrellis, pg_catalog',
    role_name,
    current_database()
)
FROM unnest(ARRAY['careertrellis_migration', 'careertrellis_runtime', 'careertrellis_worker']) AS role_name
\gexec
