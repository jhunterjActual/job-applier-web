-- Immutable migration artifact; no production prototype imports.
GRANT USAGE ON SCHEMA careertrellis TO careertrellis_admission_owner;
GRANT SELECT ON careertrellis.app_users TO careertrellis_admission_owner;
GRANT INSERT (workos_user_id, role) ON careertrellis.app_users TO careertrellis_admission_owner;
GRANT REFERENCES (id) ON careertrellis.app_users TO careertrellis_admission_owner;
GRANT INSERT (owner_user_id) ON careertrellis.profiles TO careertrellis_admission_owner;
GRANT SELECT (id, owner_user_id, lease_token, lease_expires_at)
    ON careertrellis.account_erasure_requests TO careertrellis_admission_owner;
GRANT USAGE ON SEQUENCE careertrellis.app_users_id_seq, careertrellis.profiles_id_seq
    TO careertrellis_admission_owner;
CREATE POLICY admission_read ON careertrellis.app_users FOR SELECT
    TO careertrellis_admission_owner USING (true);
CREATE POLICY admission_insert ON careertrellis.app_users FOR INSERT
    TO careertrellis_admission_owner WITH CHECK (role = 'user');
GRANT EXECUTE ON FUNCTION careertrellis.current_app_user_id(), careertrellis.current_workos_user_id() TO careertrellis_admission_owner;

SET LOCAL ROLE careertrellis_admission_owner;
REVOKE ALL ON SCHEMA careertrellis_admission FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA careertrellis_admission REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
CREATE TABLE careertrellis_admission.policy (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    phase text NOT NULL DEFAULT 'pilot' CHECK (phase IN ('pilot', 'beta', 'release')),
    signup_open boolean NOT NULL DEFAULT false,
    adapter_ready boolean NOT NULL DEFAULT false,
    minimum_session_version smallint NOT NULL DEFAULT 1 CHECK (minimum_session_version IN (1, 2)),
    maintenance boolean NOT NULL DEFAULT false,
    cap integer NOT NULL DEFAULT 1 CHECK (cap > 0),
    ttl integer NOT NULL DEFAULT 600 CHECK (ttl BETWEEN 30 AND 900),
    generation bigint NOT NULL DEFAULT 0,
    realm text NOT NULL DEFAULT 'unconfigured'
);
INSERT INTO careertrellis_admission.policy DEFAULT VALUES;
CREATE TABLE careertrellis_admission.flows (
    id uuid PRIMARY KEY,
    binding text NOT NULL CHECK (binding ~ '^[0-9a-f]{64}$'),
    deadline timestamptz NOT NULL,
    expires timestamptz NOT NULL,
    generation bigint NOT NULL,
    purpose text NOT NULL CHECK (purpose IN ('signup', 'login')),
    state text NOT NULL DEFAULT 'reserved'
        CHECK (state IN ('reserved', 'claimed', 'activated', 'login', 'existing', 'cancelled', 'ineligible')),
    claim uuid,
    code_digest text CHECK (code_digest ~ '^[0-9a-f]{64}$'),
    local_id bigint REFERENCES careertrellis.app_users(id) ON DELETE SET NULL
);
CREATE INDEX admission_live ON careertrellis_admission.flows(expires)
    WHERE state IN ('reserved', 'claimed');

CREATE FUNCTION careertrellis_admission.lock_lifecycle() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-admission-lifecycle-v1', 0));
END $$;

CREATE FUNCTION careertrellis_admission.availability() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE p careertrellis_admission.policy%ROWTYPE; n bigint;
BEGIN
    PERFORM careertrellis_admission.lock_lifecycle();
    SELECT * INTO STRICT p FROM careertrellis_admission.policy;
    IF (p.maintenance OR NOT p.signup_open OR p.phase = 'pilot' OR NOT p.adapter_ready OR p.minimum_session_version < 2) THEN RETURN jsonb_build_object('status', 'closed'); END IF;
    SELECT (SELECT count(*) FROM careertrellis.app_users) +
        (SELECT count(*) FROM careertrellis_admission.flows WHERE purpose = 'signup'
         AND state IN ('reserved', 'claimed') AND expires > clock_timestamp()
         AND generation = p.generation) INTO n;
    RETURN jsonb_build_object('status', CASE WHEN p.phase = 'beta' AND n >= p.cap
        THEN 'full' ELSE 'available' END);
END $$;

CREATE FUNCTION careertrellis_admission.reserve(rid uuid, proof text, until_time timestamptz,
    intent text, provider_realm text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE p careertrellis_admission.policy%ROWTYPE; f careertrellis_admission.flows%ROWTYPE; t timestamptz; a jsonb;
BEGIN
    PERFORM careertrellis_admission.lock_lifecycle();
    t := clock_timestamp();
    SELECT * INTO STRICT p FROM careertrellis_admission.policy;
    IF p.maintenance OR NOT p.adapter_ready OR p.minimum_session_version < 2 THEN
        RETURN jsonb_build_object('status', 'closed');
    END IF;
    IF rid IS NULL OR proof IS NULL OR proof !~ '^[0-9a-f]{64}$'
       OR until_time IS NULL OR until_time <= t OR until_time > t + interval '15 minutes'
       OR intent IS NULL OR intent NOT IN ('signup', 'login')
       OR provider_realm IS DISTINCT FROM p.realm THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    SELECT * INTO f FROM careertrellis_admission.flows WHERE id = rid;
    IF FOUND THEN
        IF f.binding <> proof OR f.deadline <> until_time OR f.purpose <> intent THEN
            RETURN jsonb_build_object('status', 'invalid');
        END IF;
        IF f.expires <= t OR f.generation <> p.generation THEN
            RETURN jsonb_build_object('status', 'expired');
        END IF;
        RETURN jsonb_build_object('status', f.state);
    END IF;
    IF intent = 'signup' THEN
        a := careertrellis_admission.availability();
        IF a->>'status' <> 'available' THEN RETURN a; END IF;
    END IF;
    INSERT INTO careertrellis_admission.flows(id, binding, deadline, expires, generation, purpose)
    VALUES (rid, proof, until_time, least(until_time, t + make_interval(secs => p.ttl)),
        p.generation, intent);
    RETURN jsonb_build_object('status', 'reserved');
END $$;

CREATE FUNCTION careertrellis_admission.claim(rid uuid, proof text, digest text, attempt uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE f careertrellis_admission.flows%ROWTYPE; p careertrellis_admission.policy%ROWTYPE;
BEGIN
    PERFORM careertrellis_admission.lock_lifecycle();
    SELECT * INTO STRICT p FROM careertrellis_admission.policy;
    IF p.maintenance THEN RETURN jsonb_build_object('status', 'closed'); END IF;
    SELECT * INTO f FROM careertrellis_admission.flows WHERE id = rid;
    IF NOT FOUND OR proof IS NULL OR f.binding <> proof OR attempt IS NULL
       OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    IF f.expires <= clock_timestamp() OR f.generation <> p.generation THEN
        RETURN jsonb_build_object('status', 'expired');
    END IF;
    IF f.code_digest IS NOT NULL AND f.code_digest <> digest THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    IF f.state <> 'reserved' THEN
        -- A duplicate never gets exchange authority, even with the same attempt ID.
        RETURN jsonb_build_object('status', CASE WHEN f.state = 'claimed'
            THEN 'incomplete' ELSE f.state END);
    END IF;
    UPDATE careertrellis_admission.flows SET state = 'claimed', claim = attempt, code_digest = digest WHERE id = rid;
    RETURN jsonb_build_object('status', 'exchange');
END $$;

CREATE FUNCTION careertrellis_admission.finish(rid uuid, proof text, attempt uuid, subject text,
    provider_realm text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE f careertrellis_admission.flows%ROWTYPE; p careertrellis_admission.policy%ROWTYPE;
    u careertrellis.app_users%ROWTYPE; new_id bigint;
BEGIN
    PERFORM careertrellis_admission.lock_lifecycle();
    SELECT * INTO STRICT p FROM careertrellis_admission.policy;
    SELECT * INTO f FROM careertrellis_admission.flows WHERE id = rid;
    IF NOT FOUND OR proof IS NULL OR f.binding <> proof OR attempt IS NULL
       OR f.claim IS DISTINCT FROM attempt OR subject IS NULL
       OR subject !~ '^user_[A-Za-z0-9_]{6,120}$'
       OR provider_realm IS DISTINCT FROM p.realm THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    IF f.expires <= clock_timestamp() THEN RETURN jsonb_build_object('status', 'expired'); END IF;
    IF f.state IN ('activated', 'login') THEN
        SELECT * INTO u FROM careertrellis.app_users WHERE id = f.local_id;
        IF NOT FOUND OR u.workos_user_id <> subject OR u.disabled_at IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ineligible');
        END IF;
        RETURN jsonb_build_object('status', f.state, 'local_id', u.id, 'role', u.role);
    END IF;
    IF f.state <> 'claimed' THEN RETURN jsonb_build_object('status', f.state); END IF;
    IF f.generation <> p.generation THEN RETURN jsonb_build_object('status', 'expired'); END IF;
    SELECT * INTO u FROM careertrellis.app_users WHERE workos_user_id = subject;
    IF FOUND THEN
        IF u.disabled_at IS NOT NULL THEN
            UPDATE careertrellis_admission.flows SET state = 'ineligible' WHERE id = rid;
            RETURN jsonb_build_object('status', 'ineligible');
        END IF;
        IF f.purpose = 'signup' THEN
            UPDATE careertrellis_admission.flows SET state = 'existing' WHERE id = rid;
            RETURN jsonb_build_object('status', 'existing');
        END IF;
        UPDATE careertrellis_admission.flows SET state = 'login', local_id = u.id WHERE id = rid;
        RETURN jsonb_build_object('status', 'login', 'local_id', u.id, 'role', u.role);
    END IF;
    IF f.purpose = 'login' THEN
        UPDATE careertrellis_admission.flows SET state = 'ineligible' WHERE id = rid;
        RETURN jsonb_build_object('status', 'ineligible');
    END IF;
    IF (p.maintenance OR NOT p.signup_open OR p.phase = 'pilot' OR NOT p.adapter_ready OR p.minimum_session_version < 2) THEN RETURN jsonb_build_object('status', 'closed'); END IF;
    IF p.phase = 'beta' AND (SELECT count(*) FROM careertrellis.app_users) >= p.cap THEN
        RETURN jsonb_build_object('status', 'full');
    END IF;
    INSERT INTO careertrellis.app_users(workos_user_id, role) VALUES (subject, 'user') RETURNING id INTO new_id;
    PERFORM set_config('careertrellis.user_id', new_id::text, true);
    INSERT INTO careertrellis.profiles(owner_user_id) VALUES (new_id);
    UPDATE careertrellis_admission.flows SET state = 'activated', local_id = new_id WHERE id = rid;
    RETURN jsonb_build_object('status', 'activated', 'local_id', new_id, 'role', 'user');
END $$;

CREATE FUNCTION careertrellis_admission.result(rid uuid, proof text, cancel_flow boolean DEFAULT false) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE f careertrellis_admission.flows%ROWTYPE; gen bigint;
BEGIN
    PERFORM careertrellis_admission.lock_lifecycle();
    SELECT generation INTO STRICT gen FROM careertrellis_admission.policy;
    SELECT * INTO f FROM careertrellis_admission.flows WHERE id = rid;
    IF NOT FOUND OR proof IS NULL OR f.binding <> proof THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    IF f.expires <= clock_timestamp() OR
       (f.state NOT IN ('activated', 'login') AND f.generation <> gen) THEN
        RETURN jsonb_build_object('status', 'expired');
    END IF;
    IF f.state IN ('activated', 'login') THEN
        IF NOT EXISTS (SELECT 1 FROM careertrellis.app_users WHERE id = f.local_id AND disabled_at IS NULL) THEN
            RETURN jsonb_build_object('status', 'ineligible');
        END IF;
    ELSIF cancel_flow THEN
        UPDATE careertrellis_admission.flows SET state = 'cancelled' WHERE id = rid;
        RETURN jsonb_build_object('status', 'cancelled');
    END IF;
    RETURN jsonb_build_object('status', CASE WHEN f.state = 'claimed' THEN 'incomplete' ELSE f.state END);
END $$;

CREATE FUNCTION careertrellis_admission.purge() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE n integer;
BEGIN
    PERFORM careertrellis_admission.lock_lifecycle();
    DELETE FROM careertrellis_admission.flows WHERE expires <= clock_timestamp() AND deadline <= clock_timestamp();
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;


CREATE FUNCTION careertrellis_admission.invalidate_flows() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM careertrellis_admission.lock_lifecycle();
    UPDATE careertrellis_admission.policy SET generation=generation+1;
END $$;

CREATE FUNCTION careertrellis_admission.require_operational() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF (SELECT maintenance FROM careertrellis_admission.policy) IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'account lifecycle maintenance is active' USING ERRCODE='55000';
    END IF;
END $$;

CREATE FUNCTION careertrellis_admission.legacy_runtime_ready() RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT coalesce((SELECT NOT maintenance AND NOT signup_open AND NOT adapter_ready
        AND minimum_session_version=1 FROM careertrellis_admission.policy),false)
$$;

CREATE FUNCTION careertrellis_admission.operator_allowed(operation text, subject text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE p careertrellis_admission.policy%ROWTYPE; occupied bigint;
BEGIN
    IF session_user <> 'careertrellis_migration' OR operation IS NULL
       OR operation NOT IN ('inspect', 'seed', 'disable', 'reinvite') THEN
        RAISE EXCEPTION 'operator caller denied' USING ERRCODE='42501';
    END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    PERFORM careertrellis_admission.require_operational();
    SELECT * INTO STRICT p FROM careertrellis_admission.policy;
    IF operation <> 'seed' OR EXISTS (SELECT 1 FROM careertrellis.app_users WHERE workos_user_id=subject)
       OR p.phase <> 'beta' THEN RETURN true; END IF;
    SELECT (SELECT count(*) FROM careertrellis.app_users) +
        (SELECT count(*) FROM careertrellis_admission.flows WHERE purpose='signup'
         AND state IN ('reserved','claimed') AND generation=p.generation AND expires>clock_timestamp())
        INTO occupied;
    RETURN occupied < p.cap;
END $$;

-- Maintenance is a durable closed gate, never a long-held admission lock during object I/O.
CREATE FUNCTION careertrellis_admission.set_import_maintenance(enabled boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE p careertrellis_admission.policy%ROWTYPE;
BEGIN
    IF session_user <> 'careertrellis_migration' OR enabled IS NULL THEN
        RAISE EXCEPTION 'maintenance caller denied' USING ERRCODE='42501';
    END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    IF NOT pg_try_advisory_xact_lock(hashtextextended('careertrellis-admission-import-v1', 0)) THEN
        RAISE EXCEPTION 'import is still in progress' USING ERRCODE='55000';
    END IF;
    SELECT * INTO STRICT p FROM careertrellis_admission.policy;
    IF p.signup_open OR p.phase <> 'pilot' THEN
        RAISE EXCEPTION 'import requires closed pilot maintenance' USING ERRCODE='55000';
    END IF;
    IF p.maintenance IS DISTINCT FROM enabled THEN
        UPDATE careertrellis_admission.policy SET maintenance=enabled, generation=generation+1;
    END IF;
END $$;

CREATE FUNCTION careertrellis_admission.assert_import_maintenance() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF session_user <> 'careertrellis_migration' THEN
        RAISE EXCEPTION 'import caller denied' USING ERRCODE='42501';
    END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('careertrellis-admission-import-v1', 0));
    IF NOT EXISTS (SELECT 1 FROM careertrellis_admission.policy
                   WHERE maintenance AND NOT signup_open AND phase='pilot') THEN
        RAISE EXCEPTION 'closed import maintenance is required' USING ERRCODE='55000';
    END IF;
END $$;

-- Public policy activation has deliberately no operator API in this database-only slice.
REVOKE ALL ON ALL TABLES IN SCHEMA careertrellis_admission FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA careertrellis_admission FROM PUBLIC;
GRANT USAGE ON SCHEMA careertrellis_admission TO careertrellis_admission_broker, careertrellis_schema_owner;
GRANT EXECUTE ON FUNCTION careertrellis_admission.availability(),
    careertrellis_admission.reserve(uuid,text,timestamptz,text,text),
    careertrellis_admission.claim(uuid,text,text,uuid),
    careertrellis_admission.finish(uuid,text,uuid,text,text),
    careertrellis_admission.result(uuid,text,boolean) TO careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis_admission.lock_lifecycle(),
    careertrellis_admission.invalidate_flows(), careertrellis_admission.require_operational(),
    careertrellis_admission.legacy_runtime_ready(),
    careertrellis_admission.operator_allowed(text,text),
    careertrellis_admission.set_import_maintenance(boolean),
    careertrellis_admission.assert_import_maintenance() TO careertrellis_schema_owner;
SET LOCAL ROLE careertrellis_schema_owner;
REVOKE ALL ON SCHEMA careertrellis_lifecycle FROM PUBLIC;

CREATE FUNCTION careertrellis.admission_lifecycle_ready() RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT careertrellis_admission.legacy_runtime_ready()
$$;
REVOKE ALL ON FUNCTION careertrellis.admission_lifecycle_ready() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION careertrellis.admission_lifecycle_ready() TO careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker;

ALTER FUNCTION careertrellis.request_account_erasure() SET SCHEMA careertrellis_lifecycle;
CREATE OR REPLACE FUNCTION careertrellis_lifecycle.request_account_erasure()
RETURNS TABLE (status varchar(24), accepted_at timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, careertrellis
AS $$
DECLARE
    selected_owner_id bigint;
    selected_workos_user_id text;
    selected_disabled_at timestamptz;
    existing_request careertrellis.account_erasure_requests%ROWTYPE;
BEGIN
    IF SESSION_USER <> 'careertrellis_runtime' THEN
        RAISE EXCEPTION 'account erasure acceptance is not authorized'
            USING ERRCODE = '42501';
    END IF;
    selected_owner_id := careertrellis.current_app_user_id();
    IF selected_owner_id IS NULL THEN
        RAISE EXCEPTION 'account erasure acceptance is unavailable'
            USING ERRCODE = '42501';
    END IF;

    SELECT u.workos_user_id, u.disabled_at
        INTO selected_workos_user_id, selected_disabled_at
    FROM careertrellis.app_users AS u
    WHERE u.id = selected_owner_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'account erasure acceptance is unavailable'
            USING ERRCODE = '42501';
    END IF;

    SELECT request.* INTO existing_request
    FROM careertrellis.account_erasure_requests AS request
    WHERE request.owner_user_id = selected_owner_id
    FOR UPDATE;
    IF FOUND THEN
        status := 'accepted';
        accepted_at := existing_request.accepted_at;
        RETURN NEXT;
        RETURN;
    END IF;
    IF selected_disabled_at IS NOT NULL THEN
        RAISE EXCEPTION 'account erasure acceptance is unavailable'
            USING ERRCODE = '42501';
    END IF;

    -- The forced app_users policy requires the already-authorized
    -- WorkOS identity on writes. It remains inside this transaction
    -- and is never returned or copied into the receipt/broker.
    PERFORM pg_catalog.set_config(
        'careertrellis.workos_user_id', selected_workos_user_id, true
    );
    UPDATE careertrellis.app_users AS u
    SET disabled_at = clock_timestamp()
    WHERE u.id = selected_owner_id
      AND u.disabled_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'account erasure acceptance is unavailable'
            USING ERRCODE = '40001';
    END IF;

    INSERT INTO careertrellis.account_erasure_requests (owner_user_id)
    VALUES (selected_owner_id)
    RETURNING account_erasure_requests.accepted_at INTO accepted_at;
    status := 'accepted';
    RETURN NEXT;
END;
$$;
REVOKE ALL ON FUNCTION careertrellis_lifecycle.request_account_erasure() FROM PUBLIC, careertrellis_runtime, careertrellis_worker, careertrellis_erasure_worker;

ALTER FUNCTION careertrellis.claim_next_account_erasure(uuid,integer) SET SCHEMA careertrellis_lifecycle;
CREATE OR REPLACE FUNCTION careertrellis_lifecycle.claim_next_account_erasure(
    p_worker_id uuid, p_lease_seconds integer
)
RETURNS TABLE (
    request_id uuid,
    owner_user_id bigint,
    lease_token uuid,
    lease_expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, careertrellis
AS $$
DECLARE
    candidate careertrellis.account_erasure_claim_queue%ROWTYPE;
    request careertrellis.account_erasure_requests%ROWTYPE;
BEGIN
    IF SESSION_USER <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure claim is not authorized'
            USING ERRCODE = '42501';
    END IF;
    IF p_worker_id IS NULL OR p_lease_seconds < 30 OR p_lease_seconds > 900 THEN
        RAISE EXCEPTION 'invalid account erasure claim'
            USING ERRCODE = '22023';
    END IF;

    LOOP
        SELECT queue.* INTO candidate
        FROM careertrellis.account_erasure_claim_queue AS queue
        WHERE queue.available_at <= clock_timestamp()
        ORDER BY queue.available_at, queue.request_id
        FOR UPDATE SKIP LOCKED
        LIMIT 1;
        IF NOT FOUND THEN
            RETURN;
        END IF;

        SELECT selected.* INTO request
        FROM careertrellis.account_erasure_requests AS selected
        WHERE selected.id = candidate.request_id
          AND selected.owner_user_id = candidate.owner_user_id
        FOR UPDATE;
        IF NOT FOUND OR request.status NOT IN ('accepted', 'deleting_objects') THEN
            DELETE FROM careertrellis.account_erasure_claim_queue
            WHERE account_erasure_claim_queue.request_id = candidate.request_id;
            CONTINUE;
        END IF;
        IF request.attempts >= request.max_attempts THEN
            UPDATE careertrellis.account_erasure_requests AS selected SET
                status = 'needs_attention', error_code = 'attempts_exhausted',
                lease_token = NULL, lease_expires_at = NULL, worker_id = NULL,
                updated_at = clock_timestamp()
            WHERE selected.id = candidate.request_id
              AND selected.owner_user_id = candidate.owner_user_id;
            CONTINUE;
        END IF;

        RETURN QUERY
        UPDATE careertrellis.account_erasure_requests AS selected SET
            status = 'deleting_objects', attempts = selected.attempts + 1,
            lease_token = gen_random_uuid(), worker_id = p_worker_id,
            lease_expires_at = clock_timestamp()
                + pg_catalog.make_interval(secs => p_lease_seconds),
            error_code = NULL, updated_at = clock_timestamp()
        WHERE selected.id = candidate.request_id
          AND selected.owner_user_id = candidate.owner_user_id
        RETURNING selected.id, selected.owner_user_id,
            selected.lease_token, selected.lease_expires_at;
        RETURN;
    END LOOP;
END;
$$;
REVOKE ALL ON FUNCTION careertrellis_lifecycle.claim_next_account_erasure(uuid,integer) FROM PUBLIC, careertrellis_runtime, careertrellis_worker, careertrellis_erasure_worker;

ALTER FUNCTION careertrellis.account_erasure_object_batch(uuid,bigint,uuid,integer) SET SCHEMA careertrellis_lifecycle;
CREATE OR REPLACE FUNCTION careertrellis_lifecycle.account_erasure_object_batch(
    p_request_id uuid, p_owner_user_id bigint,
    p_lease_token uuid, p_limit integer
)
RETURNS TABLE (object_id uuid, object_key text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, careertrellis
AS $$
DECLARE
    request careertrellis.account_erasure_requests%ROWTYPE;
BEGIN
    IF SESSION_USER <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure object access is not authorized'
            USING ERRCODE = '42501';
    END IF;
    IF p_request_id IS NULL OR p_owner_user_id IS NULL
       OR p_owner_user_id <= 0 OR p_lease_token IS NULL
       OR p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'invalid account erasure object request'
            USING ERRCODE = '22023';
    END IF;
    SELECT selected.* INTO request
    FROM careertrellis.account_erasure_requests AS selected
    WHERE selected.id = p_request_id
      AND selected.owner_user_id = p_owner_user_id
    FOR UPDATE;
    IF NOT FOUND OR request.status <> 'deleting_objects'
       OR request.lease_token <> p_lease_token
       OR request.lease_expires_at <= clock_timestamp() THEN
        RAISE EXCEPTION 'account erasure lease is unavailable'
            USING ERRCODE = '40001';
    END IF;

    PERFORM pg_catalog.set_config(
        'careertrellis.user_id', p_owner_user_id::text, true
    );
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'careertrellis-job-catalog:' || p_owner_user_id::text, 0
        )
    );
    RETURN QUERY
    WITH targets AS (
        SELECT stored.id
        FROM careertrellis.stored_objects AS stored
        WHERE stored.owner_user_id = p_owner_user_id
          AND stored.erasure_deleted_at IS NULL
        ORDER BY stored.id
        FOR UPDATE
        LIMIT p_limit
    ), tombstoned AS (
        UPDATE careertrellis.stored_objects AS stored
        SET deleted_at = COALESCE(stored.deleted_at, clock_timestamp())
        FROM targets
        WHERE stored.id = targets.id
          AND stored.owner_user_id = p_owner_user_id
        RETURNING stored.id, stored.object_key
    )
    SELECT tombstoned.id, tombstoned.object_key
    FROM tombstoned
    ORDER BY tombstoned.id;
END;
$$;
REVOKE ALL ON FUNCTION careertrellis_lifecycle.account_erasure_object_batch(uuid,bigint,uuid,integer) FROM PUBLIC, careertrellis_runtime, careertrellis_worker, careertrellis_erasure_worker;

ALTER FUNCTION careertrellis.confirm_account_erasure_object_deleted(uuid,bigint,uuid,uuid) SET SCHEMA careertrellis_lifecycle;
CREATE OR REPLACE FUNCTION careertrellis_lifecycle.confirm_account_erasure_object_deleted(
    p_request_id uuid, p_owner_user_id bigint,
    p_lease_token uuid, p_object_id uuid
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, careertrellis
AS $$
DECLARE
    request careertrellis.account_erasure_requests%ROWTYPE;
BEGIN
    IF SESSION_USER <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure confirmation is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT selected.* INTO request
    FROM careertrellis.account_erasure_requests AS selected
    WHERE selected.id = p_request_id
      AND selected.owner_user_id = p_owner_user_id
    FOR UPDATE;
    IF NOT FOUND OR request.status <> 'deleting_objects'
       OR request.lease_token <> p_lease_token
       OR request.lease_expires_at <= clock_timestamp() THEN
        RETURN FALSE;
    END IF;
    PERFORM pg_catalog.set_config(
        'careertrellis.user_id', p_owner_user_id::text, true
    );
    UPDATE careertrellis.stored_objects AS stored
    SET erasure_deleted_at = COALESCE(
        stored.erasure_deleted_at, clock_timestamp()
    )
    WHERE stored.id = p_object_id
      AND stored.owner_user_id = p_owner_user_id
      AND stored.deleted_at IS NOT NULL;
    RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION careertrellis_lifecycle.confirm_account_erasure_object_deleted(uuid,bigint,uuid,uuid) FROM PUBLIC, careertrellis_runtime, careertrellis_worker, careertrellis_erasure_worker;

ALTER FUNCTION careertrellis.renew_account_erasure_lease(uuid,bigint,uuid,integer) SET SCHEMA careertrellis_lifecycle;
CREATE OR REPLACE FUNCTION careertrellis_lifecycle.renew_account_erasure_lease(
    p_request_id uuid, p_owner_user_id bigint,
    p_lease_token uuid, p_lease_seconds integer
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, careertrellis
AS $$
BEGIN
    IF SESSION_USER <> 'careertrellis_erasure_worker'
       OR p_lease_seconds < 30 OR p_lease_seconds > 900 THEN
        RETURN FALSE;
    END IF;
    UPDATE careertrellis.account_erasure_requests AS request SET
        lease_expires_at = clock_timestamp()
            + pg_catalog.make_interval(secs => p_lease_seconds),
        updated_at = clock_timestamp()
    WHERE request.id = p_request_id
      AND request.owner_user_id = p_owner_user_id
      AND request.status = 'deleting_objects'
      AND request.lease_token = p_lease_token
      AND request.lease_expires_at > clock_timestamp();
    RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION careertrellis_lifecycle.renew_account_erasure_lease(uuid,bigint,uuid,integer) FROM PUBLIC, careertrellis_runtime, careertrellis_worker, careertrellis_erasure_worker;

ALTER FUNCTION careertrellis.retry_account_erasure(uuid,bigint,uuid,text) SET SCHEMA careertrellis_lifecycle;
CREATE OR REPLACE FUNCTION careertrellis_lifecycle.retry_account_erasure(
    p_request_id uuid, p_owner_user_id bigint,
    p_lease_token uuid, p_error_code text
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, careertrellis
AS $$
BEGIN
    IF SESSION_USER <> 'careertrellis_erasure_worker'
       OR p_error_code NOT IN ('object_delete_failed', 'persistence_unavailable') THEN
        RETURN FALSE;
    END IF;
    UPDATE careertrellis.account_erasure_requests AS request SET
        status = CASE WHEN request.attempts >= request.max_attempts
                      THEN 'needs_attention' ELSE 'accepted' END,
        error_code = CASE WHEN request.attempts >= request.max_attempts
                          THEN 'attempts_exhausted' ELSE p_error_code END,
        available_at = clock_timestamp() + pg_catalog.make_interval(
            secs => LEAST(900, (2 ^ LEAST(request.attempts, 9))::integer)
        ),
        lease_token = NULL, lease_expires_at = NULL, worker_id = NULL,
        updated_at = clock_timestamp()
    WHERE request.id = p_request_id
      AND request.owner_user_id = p_owner_user_id
      AND request.status = 'deleting_objects'
      AND request.lease_token = p_lease_token
      AND request.lease_expires_at > clock_timestamp();
    RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION careertrellis_lifecycle.retry_account_erasure(uuid,bigint,uuid,text) FROM PUBLIC, careertrellis_runtime, careertrellis_worker, careertrellis_erasure_worker;

ALTER FUNCTION careertrellis.finalize_account_erasure(uuid,bigint,uuid) SET SCHEMA careertrellis_lifecycle;
CREATE OR REPLACE FUNCTION careertrellis_lifecycle.finalize_account_erasure(
    p_request_id uuid, p_owner_user_id bigint, p_lease_token uuid
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, careertrellis
AS $$
DECLARE
    request careertrellis.account_erasure_requests%ROWTYPE;
    completion_time timestamptz;
BEGIN
    IF SESSION_USER <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure finalization is not authorized'
            USING ERRCODE = '42501';
    END IF;
    SELECT selected.* INTO request
    FROM careertrellis.account_erasure_requests AS selected
    WHERE selected.id = p_request_id
      AND selected.owner_user_id = p_owner_user_id
    FOR UPDATE;
    IF NOT FOUND OR request.status <> 'deleting_objects'
       OR request.lease_token <> p_lease_token
       OR request.lease_expires_at <= clock_timestamp() THEN
        RETURN FALSE;
    END IF;
    PERFORM pg_catalog.set_config(
        'careertrellis.user_id', p_owner_user_id::text, true
    );
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'careertrellis-job-catalog:' || p_owner_user_id::text, 0
        )
    );
    PERFORM 1 FROM careertrellis.app_users AS app_user
    WHERE app_user.id = p_owner_user_id
      AND app_user.disabled_at IS NOT NULL
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    IF EXISTS (
        SELECT 1 FROM careertrellis.stored_objects AS stored
        WHERE stored.owner_user_id = p_owner_user_id
          AND stored.erasure_deleted_at IS NULL
    ) THEN
        UPDATE careertrellis.account_erasure_requests AS selected SET
            status = 'accepted', available_at = clock_timestamp(),
            lease_token = NULL, lease_expires_at = NULL, worker_id = NULL,
            updated_at = clock_timestamp()
        WHERE selected.id = p_request_id
          AND selected.owner_user_id = p_owner_user_id;
        RETURN FALSE;
    END IF;

    -- Deleting the identity is the authoritative cascade. It removes
    -- every tenant row, makes stale post-acceptance writers fail their
    -- owner FK, and leaves only this request through ON DELETE SET NULL.
    DELETE FROM careertrellis.app_users AS app_user
    WHERE app_user.id = p_owner_user_id;
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    completion_time := clock_timestamp();
    UPDATE careertrellis.account_erasure_requests AS selected SET
        status = 'completed', completed_at = completion_time,
        receipt_expires_at = completion_time + INTERVAL '30 days',
        error_code = NULL, lease_token = NULL,
        lease_expires_at = NULL, worker_id = NULL,
        updated_at = completion_time
    WHERE selected.id = p_request_id
      AND selected.owner_user_id IS NULL;
    RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION careertrellis_lifecycle.finalize_account_erasure(uuid,bigint,uuid) FROM PUBLIC, careertrellis_runtime, careertrellis_worker, careertrellis_erasure_worker;

ALTER FUNCTION careertrellis.purge_expired_account_erasures(integer) SET SCHEMA careertrellis_lifecycle;
CREATE OR REPLACE FUNCTION careertrellis_lifecycle.purge_expired_account_erasures(p_limit integer)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, careertrellis
AS $$
DECLARE
    purged integer;
BEGIN
    IF SESSION_USER <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure receipt purge is not authorized'
            USING ERRCODE = '42501';
    END IF;
    IF p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'invalid account erasure receipt purge limit'
            USING ERRCODE = '22023';
    END IF;
    WITH expired AS (
        SELECT request.id
        FROM careertrellis.account_erasure_requests AS request
        WHERE request.status = 'completed'
          AND request.receipt_expires_at <= clock_timestamp()
        ORDER BY request.receipt_expires_at, request.id
        FOR UPDATE SKIP LOCKED
        LIMIT p_limit
    )
    DELETE FROM careertrellis.account_erasure_requests AS request
    USING expired
    WHERE request.id = expired.id;
    GET DIAGNOSTICS purged = ROW_COUNT;
    RETURN purged;
END;
$$;
REVOKE ALL ON FUNCTION careertrellis_lifecycle.purge_expired_account_erasures(integer) FROM PUBLIC, careertrellis_runtime, careertrellis_worker, careertrellis_erasure_worker;

CREATE FUNCTION careertrellis_lifecycle.lease_deadline(rid uuid, uid bigint, token uuid)
RETURNS timestamptz LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE deadline timestamptz;
BEGIN
    SELECT lease_expires_at INTO deadline FROM careertrellis.account_erasure_requests
    WHERE id=rid AND owner_user_id=uid AND lease_token=token AND status='deleting_objects' FOR UPDATE;
    IF deadline IS NULL OR deadline <= clock_timestamp() THEN RETURN NULL; END IF;
    RETURN deadline;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA careertrellis_lifecycle FROM PUBLIC,
    careertrellis_runtime, careertrellis_worker, careertrellis_erasure_worker,
    careertrellis_admission_owner, careertrellis_admission_broker;

CREATE FUNCTION careertrellis.request_account_erasure()
RETURNS TABLE(status varchar(24), accepted_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE existed boolean;
BEGIN
    IF session_user <> 'careertrellis_runtime' THEN
        RAISE EXCEPTION 'account erasure caller denied' USING ERRCODE='42501';
    END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    PERFORM careertrellis_admission.require_operational();
    SELECT EXISTS(SELECT 1 FROM careertrellis.account_erasure_requests
                  WHERE owner_user_id=careertrellis.current_app_user_id()) INTO existed;
    RETURN QUERY SELECT * FROM careertrellis_lifecycle.request_account_erasure();
    IF NOT existed THEN PERFORM careertrellis_admission.invalidate_flows(); END IF;
END $$;

CREATE FUNCTION careertrellis.claim_next_account_erasure(p_worker_id uuid, p_lease_seconds integer)
RETURNS TABLE(request_id uuid, owner_user_id bigint, lease_token uuid, lease_expires_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE claimed record;
BEGIN
    IF session_user <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure caller denied' USING ERRCODE='42501';
    END IF;
    IF p_worker_id IS NULL OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 30 AND 900 THEN
        RAISE EXCEPTION 'invalid account erasure claim' USING ERRCODE='22023';
    END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    PERFORM careertrellis_admission.require_operational();
    FOR claimed IN SELECT * FROM careertrellis_lifecycle.claim_next_account_erasure(p_worker_id,p_lease_seconds) LOOP
        IF claimed.lease_expires_at <= clock_timestamp() THEN
            RAISE EXCEPTION 'account erasure lease expired' USING ERRCODE='40001';
        END IF;
        request_id:=claimed.request_id; owner_user_id:=claimed.owner_user_id;
        lease_token:=claimed.lease_token; lease_expires_at:=claimed.lease_expires_at;
        RETURN NEXT;
    END LOOP;
END $$;

CREATE FUNCTION careertrellis.account_erasure_object_batch(p_request_id uuid, p_owner_user_id bigint, p_lease_token uuid, p_limit integer)
RETURNS TABLE(object_id uuid, object_key text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE deadline timestamptz;
BEGIN
    IF session_user <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure caller denied' USING ERRCODE='42501';
    END IF;
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 500 OR p_request_id IS NULL
       OR p_owner_user_id IS NULL OR p_owner_user_id <= 0 OR p_lease_token IS NULL THEN
        RAISE EXCEPTION 'invalid account erasure batch' USING ERRCODE='22023';
    END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    PERFORM careertrellis_admission.require_operational();
    deadline:=careertrellis_lifecycle.lease_deadline(p_request_id,p_owner_user_id,p_lease_token);
    IF deadline IS NULL THEN RAISE EXCEPTION 'account erasure lease expired' USING ERRCODE='40001'; END IF;
    RETURN QUERY SELECT * FROM careertrellis_lifecycle.account_erasure_object_batch(p_request_id,p_owner_user_id,p_lease_token,p_limit);
    IF clock_timestamp() >= deadline THEN RAISE EXCEPTION 'account erasure lease expired' USING ERRCODE='40001'; END IF;
END $$;

CREATE FUNCTION careertrellis.confirm_account_erasure_object_deleted(p_request_id uuid,p_owner_user_id bigint,p_lease_token uuid, p_object_id uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE deadline timestamptz; done boolean;
BEGIN
    IF session_user <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure caller denied' USING ERRCODE='42501';
    END IF;
    IF p_request_id IS NULL OR p_owner_user_id IS NULL OR p_owner_user_id <= 0 OR p_lease_token IS NULL OR p_object_id IS NULL THEN RETURN false; END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    PERFORM careertrellis_admission.require_operational();
    deadline:=careertrellis_lifecycle.lease_deadline(p_request_id,p_owner_user_id,p_lease_token);
    IF deadline IS NULL THEN RETURN false; END IF;
    done:=careertrellis_lifecycle.confirm_account_erasure_object_deleted(p_request_id,p_owner_user_id,p_lease_token, p_object_id);
    IF clock_timestamp() >= deadline THEN RAISE EXCEPTION 'account erasure lease expired' USING ERRCODE='40001'; END IF;

    RETURN done;
END $$;

CREATE FUNCTION careertrellis.renew_account_erasure_lease(p_request_id uuid,p_owner_user_id bigint,p_lease_token uuid, p_lease_seconds integer)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE deadline timestamptz; done boolean;
BEGIN
    IF session_user <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure caller denied' USING ERRCODE='42501';
    END IF;
    IF p_request_id IS NULL OR p_owner_user_id IS NULL OR p_owner_user_id <= 0 OR p_lease_token IS NULL OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 30 AND 900 THEN RETURN false; END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    PERFORM careertrellis_admission.require_operational();
    deadline:=careertrellis_lifecycle.lease_deadline(p_request_id,p_owner_user_id,p_lease_token);
    IF deadline IS NULL THEN RETURN false; END IF;
    done:=careertrellis_lifecycle.renew_account_erasure_lease(p_request_id,p_owner_user_id,p_lease_token, p_lease_seconds);
    IF clock_timestamp() >= deadline THEN RAISE EXCEPTION 'account erasure lease expired' USING ERRCODE='40001'; END IF;

    RETURN done;
END $$;

CREATE FUNCTION careertrellis.retry_account_erasure(p_request_id uuid,p_owner_user_id bigint,p_lease_token uuid, p_error_code text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE deadline timestamptz; done boolean;
BEGIN
    IF session_user <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure caller denied' USING ERRCODE='42501';
    END IF;
    IF p_request_id IS NULL OR p_owner_user_id IS NULL OR p_owner_user_id <= 0 OR p_lease_token IS NULL OR p_error_code IS NULL OR p_error_code NOT IN ('object_delete_failed','persistence_unavailable') THEN RETURN false; END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    PERFORM careertrellis_admission.require_operational();
    deadline:=careertrellis_lifecycle.lease_deadline(p_request_id,p_owner_user_id,p_lease_token);
    IF deadline IS NULL THEN RETURN false; END IF;
    done:=careertrellis_lifecycle.retry_account_erasure(p_request_id,p_owner_user_id,p_lease_token, p_error_code);
    IF clock_timestamp() >= deadline THEN RAISE EXCEPTION 'account erasure lease expired' USING ERRCODE='40001'; END IF;

    RETURN done;
END $$;

CREATE FUNCTION careertrellis.finalize_account_erasure(p_request_id uuid,p_owner_user_id bigint,p_lease_token uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE deadline timestamptz; done boolean;
BEGIN
    IF session_user <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure caller denied' USING ERRCODE='42501';
    END IF;
    IF p_request_id IS NULL OR p_owner_user_id IS NULL OR p_owner_user_id <= 0 OR p_lease_token IS NULL THEN RETURN false; END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    PERFORM careertrellis_admission.require_operational();
    deadline:=careertrellis_lifecycle.lease_deadline(p_request_id,p_owner_user_id,p_lease_token);
    IF deadline IS NULL THEN RETURN false; END IF;
    done:=careertrellis_lifecycle.finalize_account_erasure(p_request_id,p_owner_user_id,p_lease_token);
    IF clock_timestamp() >= deadline THEN RAISE EXCEPTION 'account erasure lease expired' USING ERRCODE='40001'; END IF;
    IF done THEN PERFORM careertrellis_admission.invalidate_flows(); END IF;
    RETURN done;
END $$;

CREATE FUNCTION careertrellis.purge_expired_account_erasures(p_limit integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF session_user <> 'careertrellis_erasure_worker' THEN
        RAISE EXCEPTION 'account erasure caller denied' USING ERRCODE='42501';
    END IF;
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'invalid account erasure purge limit' USING ERRCODE='22023';
    END IF;
    PERFORM careertrellis_admission.lock_lifecycle();
    PERFORM careertrellis_admission.require_operational();
    RETURN careertrellis_lifecycle.purge_expired_account_erasures(p_limit);
END $$;

REVOKE ALL ON FUNCTION careertrellis.request_account_erasure() FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.request_account_erasure() TO careertrellis_runtime;

REVOKE ALL ON FUNCTION careertrellis.claim_next_account_erasure(uuid,integer) FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.claim_next_account_erasure(uuid,integer) TO careertrellis_erasure_worker;

REVOKE ALL ON FUNCTION careertrellis.account_erasure_object_batch(uuid,bigint,uuid,integer) FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.account_erasure_object_batch(uuid,bigint,uuid,integer) TO careertrellis_erasure_worker;

REVOKE ALL ON FUNCTION careertrellis.confirm_account_erasure_object_deleted(uuid,bigint,uuid,uuid) FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.confirm_account_erasure_object_deleted(uuid,bigint,uuid,uuid) TO careertrellis_erasure_worker;

REVOKE ALL ON FUNCTION careertrellis.renew_account_erasure_lease(uuid,bigint,uuid,integer) FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.renew_account_erasure_lease(uuid,bigint,uuid,integer) TO careertrellis_erasure_worker;

REVOKE ALL ON FUNCTION careertrellis.retry_account_erasure(uuid,bigint,uuid,text) FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.retry_account_erasure(uuid,bigint,uuid,text) TO careertrellis_erasure_worker;

REVOKE ALL ON FUNCTION careertrellis.finalize_account_erasure(uuid,bigint,uuid) FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.finalize_account_erasure(uuid,bigint,uuid) TO careertrellis_erasure_worker;

REVOKE ALL ON FUNCTION careertrellis.purge_expired_account_erasures(integer) FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.purge_expired_account_erasures(integer) TO careertrellis_erasure_worker;

-- Narrow trusted context for the still-unwired v2 candidate. No ledger, cap,
-- account count or token access. Legacy readiness remains unchanged.
SET LOCAL ROLE careertrellis_admission_owner;
CREATE FUNCTION careertrellis_admission.session_context() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object('adapter_ready',adapter_ready,
        'minimum_session_version',minimum_session_version,'maintenance',maintenance,'realm',realm)
    FROM careertrellis_admission.policy
$$;
REVOKE ALL ON FUNCTION careertrellis_admission.session_context() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION careertrellis_admission.session_context() TO careertrellis_schema_owner;
SET LOCAL ROLE careertrellis_schema_owner;
CREATE FUNCTION careertrellis.admission_session_context() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT careertrellis_admission.session_context()
$$;
REVOKE ALL ON FUNCTION careertrellis.admission_session_context() FROM PUBLIC,
    careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis.admission_session_context() TO careertrellis_runtime;

-- Token-free, shared HTTP abuse/refresh coordination. Never hold a database
-- transaction across provider I/O. A refresh snapshot is consumed permanently
-- until its original outer session expires, including uncertain/lost responses.
SET LOCAL ROLE careertrellis_admission_owner;
CREATE TABLE careertrellis_admission.http_budgets (
    lane text NOT NULL, bucket text NOT NULL, window_id bigint NOT NULL,
    uses integer NOT NULL CHECK (uses > 0), expires timestamptz NOT NULL,
    PRIMARY KEY (lane,bucket,window_id)
);
CREATE INDEX http_budgets_expiry ON careertrellis_admission.http_budgets(expires);
CREATE TABLE careertrellis_admission.http_refresh_claims (
    snapshot text PRIMARY KEY CHECK (snapshot ~ '^[0-9a-f]{64}$'),
    expires timestamptz NOT NULL
);
CREATE INDEX http_refresh_expiry ON careertrellis_admission.http_refresh_claims(expires);
CREATE TABLE careertrellis_admission.http_revocations (
    session_key text PRIMARY KEY CHECK (session_key ~ '^[0-9a-f]{64}$'),
    expires timestamptz NOT NULL
);
CREATE INDEX http_revocation_expiry ON careertrellis_admission.http_revocations(expires);

CREATE FUNCTION careertrellis_admission.http_budget(p_lane text,p_browser text,p_network text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w bigint; now_at timestamptz; global_limit integer; browser_limit integer;
    network_limit integer; b text; maximum integer;
BEGIN
    IF session_user <> 'careertrellis_admission_broker' THEN
        RAISE EXCEPTION 'admission caller denied' USING ERRCODE='42501';
    END IF;
    IF p_lane IS NULL OR p_lane NOT IN ('signup','login','callback_signup','callback_login','status','refresh','logout')
        OR p_browser IS NULL OR p_browser !~ '^[0-9a-f]{64}$'
        OR p_network IS NULL OR p_network !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-http-budget-v1',0));
    now_at:=clock_timestamp(); w:=floor(extract(epoch FROM now_at)/60)::bigint;
    global_limit:=CASE p_lane WHEN 'signup' THEN 60 WHEN 'status' THEN 240 ELSE 120 END;
    browser_limit:=CASE p_lane WHEN 'signup' THEN 5 WHEN 'status' THEN 30 ELSE 10 END;
    network_limit:=CASE p_lane WHEN 'signup' THEN 20 WHEN 'status' THEN 120 ELSE 60 END;
    -- Global lane checked first: arbitrary browser/network churn cannot allocate
    -- unbounded rows. Login capacity is independent of signup capacity.
    IF coalesce((SELECT uses FROM careertrellis_admission.http_budgets
        WHERE lane=p_lane AND bucket='global' AND window_id=w),0) >= global_limit THEN RETURN false; END IF;
    DELETE FROM careertrellis_admission.http_budgets WHERE ctid IN
        (SELECT ctid FROM careertrellis_admission.http_budgets WHERE expires < now_at LIMIT 500);
    FOREACH b IN ARRAY ARRAY['global','browser:'||p_browser,'network:'||p_network] LOOP
        maximum:=CASE WHEN b='global' THEN global_limit WHEN b LIKE 'browser:%' THEN browser_limit ELSE network_limit END;
        IF coalesce((SELECT uses FROM careertrellis_admission.http_budgets
            WHERE lane=p_lane AND bucket=b AND window_id=w),0) >= maximum THEN RETURN false; END IF;
    END LOOP;
    FOREACH b IN ARRAY ARRAY['global','browser:'||p_browser,'network:'||p_network] LOOP
        INSERT INTO careertrellis_admission.http_budgets VALUES(p_lane,b,w,1,to_timestamp((w+2)*60))
            ON CONFLICT (lane,bucket,window_id) DO UPDATE SET uses=careertrellis_admission.http_budgets.uses+1;
    END LOOP;
    RETURN true;
END $$;

CREATE FUNCTION careertrellis_admission.http_session_guard(p_key text,p_expires timestamptz,
    p_operation text,p_snapshot text DEFAULT NULL)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE now_at timestamptz; retain_until timestamptz; inserted integer;
BEGIN
    IF session_user <> 'careertrellis_admission_broker' THEN
        RAISE EXCEPTION 'admission caller denied' USING ERRCODE='42501';
    END IF;
    IF p_key IS NULL OR p_key !~ '^[0-9a-f]{64}$' OR p_expires IS NULL
        OR p_operation IS NULL OR p_operation NOT IN ('check','claim','revoke') THEN RETURN false; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-http-session-v1',0));
    now_at:=clock_timestamp();
    IF p_expires <= now_at OR p_expires > now_at+interval '24 hours' THEN RETURN false; END IF;
    -- Cover every already-issued envelope for this provider session/snapshot,
    -- not only the particular browser envelope presenting this operation.
    retain_until:=now_at+interval '24 hours';
    DELETE FROM careertrellis_admission.http_refresh_claims WHERE ctid IN
        (SELECT ctid FROM careertrellis_admission.http_refresh_claims WHERE expires < now_at LIMIT 500);
    DELETE FROM careertrellis_admission.http_revocations WHERE ctid IN
        (SELECT ctid FROM careertrellis_admission.http_revocations WHERE expires < now_at LIMIT 500);
    IF p_operation='revoke' THEN
        INSERT INTO careertrellis_admission.http_revocations VALUES(p_key,retain_until)
            ON CONFLICT(session_key) DO UPDATE SET expires=greatest(
                careertrellis_admission.http_revocations.expires,excluded.expires);
        RETURN true;
    END IF;
    IF EXISTS(SELECT 1 FROM careertrellis_admission.http_revocations WHERE session_key=p_key) THEN RETURN false; END IF;
    IF p_operation='check' THEN RETURN true; END IF;
    IF p_snapshot IS NULL OR p_snapshot !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
    UPDATE careertrellis_admission.http_refresh_claims SET expires=greatest(expires,retain_until)
        WHERE snapshot=p_snapshot;
    IF FOUND THEN RETURN false; END IF;
    INSERT INTO careertrellis_admission.http_refresh_claims VALUES(p_snapshot,retain_until)
        ON CONFLICT(snapshot) DO NOTHING;
    GET DIAGNOSTICS inserted=ROW_COUNT;
    RETURN inserted=1;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA careertrellis_admission FROM PUBLIC,
    careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker;
REVOKE ALL ON FUNCTION careertrellis_admission.http_budget(text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION careertrellis_admission.http_session_guard(text,timestamptz,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION careertrellis_admission.http_budget(text,text,text),
    careertrellis_admission.http_session_guard(text,timestamptz,text,text) TO careertrellis_admission_broker;
SET LOCAL ROLE careertrellis_schema_owner;
