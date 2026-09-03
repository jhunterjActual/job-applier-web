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

-- The erasure worker is intentionally excluded from every broad grant. It may
-- enter the schema only to call the exact broker functions granted below.
GRANT USAGE ON SCHEMA careertrellis TO careertrellis_erasure_worker;
REVOKE ALL ON ALL TABLES IN SCHEMA careertrellis
    FROM careertrellis_erasure_worker;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA careertrellis
    FROM careertrellis_erasure_worker;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA careertrellis
    FROM careertrellis_erasure_worker;

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

-- Scheduled searches expose only opaque IDs and due time through a second
-- private broker. The worker can invoke the bounded atomic dispatcher but
-- neither login role can inspect or mutate the broker directly.
REVOKE ALL ON careertrellis.scheduled_search_dispatch_queue
    FROM careertrellis_runtime, careertrellis_worker;
REVOKE ALL ON FUNCTION careertrellis.sync_scheduled_search_dispatch_queue()
    FROM careertrellis_runtime, careertrellis_worker;
REVOKE ALL ON FUNCTION careertrellis.dispatch_due_saved_searches(integer)
    FROM careertrellis_runtime;
GRANT EXECUTE ON FUNCTION careertrellis.dispatch_due_saved_searches(integer)
    TO careertrellis_worker;

-- Authentication-controlled rows and fields are operator-managed. Login may
-- refresh last_seen_at only after an existing active mapping is authorized.
-- Remove the column ACLs granted by the accepted single-owner release before
-- revoking broad table privileges; PostgreSQL tracks those ACLs independently.
REVOKE INSERT (workos_user_id, last_seen_at), UPDATE (last_seen_at)
    ON careertrellis.app_users
    FROM careertrellis_runtime, careertrellis_worker;
REVOKE INSERT, UPDATE, DELETE ON careertrellis.app_users
    FROM careertrellis_runtime, careertrellis_worker;
REVOKE SELECT ON careertrellis.app_users
    FROM careertrellis_worker;
GRANT UPDATE (last_seen_at) ON careertrellis.app_users
    TO careertrellis_runtime;

-- Search lifecycle operations need an atomic disabled-owner check without
-- exposing authentication rows or columns to the worker login.
REVOKE ALL ON FUNCTION careertrellis.lock_search_run_claim(uuid, bigint, uuid)
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.lock_search_run_claim(uuid, bigint, uuid)
    TO careertrellis_worker;

-- Account erasure is accepted only through the authenticated web function.
-- Cross-owner claim, object acknowledgement, finalization, retry, and receipt
-- purge are available only to the dedicated erasure role. Neither worker gets
-- table access, and the search worker cannot invoke this broker.
REVOKE ALL ON careertrellis.account_erasure_requests,
    careertrellis.account_erasure_claim_queue
    FROM careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
REVOKE ALL ON FUNCTION careertrellis.sync_account_erasure_claim_queue()
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
REVOKE ALL ON FUNCTION careertrellis.request_account_erasure()
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.request_account_erasure()
    TO careertrellis_runtime;
REVOKE ALL ON FUNCTION careertrellis.claim_next_account_erasure(uuid, integer)
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
REVOKE ALL ON FUNCTION careertrellis.account_erasure_object_batch(uuid, bigint, uuid, integer)
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
REVOKE ALL ON FUNCTION careertrellis.confirm_account_erasure_object_deleted(uuid, bigint, uuid, uuid)
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
REVOKE ALL ON FUNCTION careertrellis.renew_account_erasure_lease(uuid, bigint, uuid, integer)
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
REVOKE ALL ON FUNCTION careertrellis.finalize_account_erasure(uuid, bigint, uuid)
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
REVOKE ALL ON FUNCTION careertrellis.retry_account_erasure(uuid, bigint, uuid, text)
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
REVOKE ALL ON FUNCTION careertrellis.purge_expired_account_erasures(integer)
    FROM PUBLIC, careertrellis_runtime, careertrellis_worker,
    careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.claim_next_account_erasure(uuid, integer),
    careertrellis.account_erasure_object_batch(uuid, bigint, uuid, integer),
    careertrellis.confirm_account_erasure_object_deleted(uuid, bigint, uuid, uuid),
    careertrellis.renew_account_erasure_lease(uuid, bigint, uuid, integer),
    careertrellis.finalize_account_erasure(uuid, bigint, uuid),
    careertrellis.retry_account_erasure(uuid, bigint, uuid, text),
    careertrellis.purge_expired_account_erasures(integer)
    TO careertrellis_erasure_worker;

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
ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_schema_owner IN SCHEMA careertrellis
    REVOKE ALL ON TABLES FROM careertrellis_erasure_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_schema_owner IN SCHEMA careertrellis
    REVOKE ALL ON SEQUENCES FROM careertrellis_erasure_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE careertrellis_schema_owner IN SCHEMA careertrellis
    REVOKE EXECUTE ON FUNCTIONS FROM careertrellis_erasure_worker;

RESET ROLE;
