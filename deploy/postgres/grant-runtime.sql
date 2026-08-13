\set ON_ERROR_STOP on

SET ROLE careertrellis_schema_owner;

REVOKE ALL ON SCHEMA careertrellis FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA careertrellis FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA careertrellis FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA careertrellis FROM PUBLIC;

GRANT USAGE ON SCHEMA careertrellis TO careertrellis_runtime, careertrellis_worker;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA careertrellis
    TO careertrellis_runtime, careertrellis_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA careertrellis
    TO careertrellis_runtime, careertrellis_worker;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA careertrellis
    TO careertrellis_runtime, careertrellis_worker;

-- Cross-owner scheduling is limited to the worker-only, reviewed broker.
-- Neither login role can inspect the broker table, and the web runtime cannot
-- invoke the SECURITY DEFINER claim function.
REVOKE ALL ON careertrellis.search_run_claim_queue
    FROM careertrellis_runtime, careertrellis_worker;
REVOKE ALL ON FUNCTION careertrellis.sync_search_run_claim_queue()
    FROM careertrellis_runtime, careertrellis_worker;
REVOKE ALL ON FUNCTION careertrellis.claim_next_search_run(uuid, integer)
    FROM careertrellis_runtime;
GRANT EXECUTE ON FUNCTION careertrellis.claim_next_search_run(uuid, integer)
    TO careertrellis_worker;

-- Authentication-controlled fields cannot be supplied or changed by a user.
-- A verified identity may create its default-role mapping and refresh last_seen_at.
REVOKE INSERT, UPDATE ON careertrellis.app_users
    FROM careertrellis_runtime, careertrellis_worker;
GRANT INSERT (workos_user_id, last_seen_at) ON careertrellis.app_users
    TO careertrellis_runtime, careertrellis_worker;
GRANT UPDATE (last_seen_at) ON careertrellis.app_users
    TO careertrellis_runtime, careertrellis_worker;

ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_schema_owner IN SCHEMA careertrellis
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_schema_owner IN SCHEMA careertrellis
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_schema_owner IN SCHEMA careertrellis
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_schema_owner IN SCHEMA careertrellis
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO careertrellis_runtime, careertrellis_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_schema_owner IN SCHEMA careertrellis
    GRANT USAGE, SELECT ON SEQUENCES TO careertrellis_runtime, careertrellis_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_schema_owner IN SCHEMA careertrellis
    GRANT EXECUTE ON FUNCTIONS TO careertrellis_runtime, careertrellis_worker;

RESET ROLE;
