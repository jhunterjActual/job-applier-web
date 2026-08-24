\set ON_ERROR_STOP on

DO $$
DECLARE
    migration_role record;
BEGIN
    SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
           rolinherit, rolreplication, rolbypassrls
    INTO migration_role
    FROM pg_catalog.pg_roles
    WHERE rolname = 'careertrellis_migration';

    IF NOT FOUND
       OR migration_role.rolcanlogin IS NOT TRUE
       OR migration_role.rolsuper IS TRUE
       OR migration_role.rolcreatedb IS TRUE
       OR migration_role.rolcreaterole IS TRUE
       OR migration_role.rolinherit IS TRUE
       OR migration_role.rolreplication IS TRUE
       OR migration_role.rolbypassrls IS TRUE THEN
        RAISE EXCEPTION 'pilot operator migration role contract is not satisfied';
    END IF;
END;
$$;

GRANT SET ON PARAMETER log_parameter_max_length TO careertrellis_migration;
GRANT SET ON PARAMETER log_parameter_max_length_on_error TO careertrellis_migration;
