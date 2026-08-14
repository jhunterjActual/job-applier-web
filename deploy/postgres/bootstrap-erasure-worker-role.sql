\set ON_ERROR_STOP on

-- Run only through bootstrap-erasure-worker-role.sh so an existing role is
-- never rotated implicitly and the generated credential is transferred once.
\if :{?erasure_worker_password}
\else
\echo 'erasure_worker_password is required'
\quit
\endif

SELECT format(
    'CREATE ROLE careertrellis_erasure_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'erasure_worker_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'careertrellis_erasure_worker'
)
\gexec

ALTER ROLE careertrellis_erasure_worker
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT CONNECT ON DATABASE :"DBNAME" TO careertrellis_erasure_worker;
SELECT format(
    'ALTER ROLE careertrellis_erasure_worker IN DATABASE %I SET search_path = careertrellis, pg_catalog',
    current_database()
)
\gexec
