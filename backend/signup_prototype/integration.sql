-- Isolated overlay on the actual Alembic schema, never a production migration.
DO $$ BEGIN
    IF current_database() <> 'ct_signup_integration' THEN
        RAISE EXCEPTION 'isolated integration database required';
    END IF;
END $$;
CREATE SCHEMA ct_admission AUTHORIZATION ct_admission_owner;
GRANT USAGE ON SCHEMA careertrellis TO ct_admission_owner;
GRANT SELECT ON careertrellis.app_users TO ct_admission_owner;
GRANT INSERT (workos_user_id, role) ON careertrellis.app_users TO ct_admission_owner;
GRANT REFERENCES (id) ON careertrellis.app_users TO ct_admission_owner;
GRANT INSERT (owner_user_id) ON careertrellis.profiles TO ct_admission_owner;
GRANT SELECT (id, owner_user_id, lease_token, lease_expires_at)
    ON careertrellis.account_erasure_requests TO ct_admission_owner;
GRANT USAGE ON SEQUENCE careertrellis.app_users_id_seq, careertrellis.profiles_id_seq
    TO ct_admission_owner;
CREATE POLICY admission_read ON careertrellis.app_users FOR SELECT
    TO ct_admission_owner USING (true);
CREATE POLICY admission_insert ON careertrellis.app_users FOR INSERT
    TO ct_admission_owner WITH CHECK (role = 'user');
GRANT EXECUTE ON FUNCTION careertrellis.request_account_erasure(),
    careertrellis.finalize_account_erasure(uuid, bigint, uuid),
    careertrellis.current_app_user_id(), careertrellis.current_workos_user_id() TO ct_admission_owner;

SET LOCAL ROLE ct_admission_owner;
REVOKE ALL ON SCHEMA ct_admission FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA ct_admission REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
CREATE TABLE ct_admission.policy (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    mode text NOT NULL DEFAULT 'closed' CHECK (mode IN ('closed', 'beta', 'open')),
    cap integer NOT NULL DEFAULT 1 CHECK (cap > 0),
    ttl integer NOT NULL DEFAULT 600 CHECK (ttl BETWEEN 30 AND 900),
    generation bigint NOT NULL DEFAULT 0,
    realm text NOT NULL DEFAULT 'synthetic-realm-v1'
);
INSERT INTO ct_admission.policy DEFAULT VALUES;
CREATE TABLE ct_admission.flows (
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
CREATE INDEX admission_live ON ct_admission.flows(expires)
    WHERE state IN ('reserved', 'claimed');

CREATE FUNCTION ct_admission.lock_lifecycle() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF current_database() <> 'ct_signup_integration' THEN
        RAISE EXCEPTION 'isolated integration database required';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('ct-isolated-admission-v1', 0));
END $$;

CREATE FUNCTION ct_admission.availability() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE p ct_admission.policy%ROWTYPE; n bigint;
BEGIN
    PERFORM ct_admission.lock_lifecycle();
    SELECT * INTO STRICT p FROM ct_admission.policy;
    IF p.mode = 'closed' THEN RETURN jsonb_build_object('status', 'closed'); END IF;
    SELECT (SELECT count(*) FROM careertrellis.app_users) +
        (SELECT count(*) FROM ct_admission.flows WHERE purpose = 'signup'
         AND state IN ('reserved', 'claimed') AND expires > clock_timestamp()
         AND generation = p.generation) INTO n;
    RETURN jsonb_build_object('status', CASE WHEN p.mode = 'beta' AND n >= p.cap
        THEN 'full' ELSE 'available' END);
END $$;

CREATE FUNCTION ct_admission.reserve(rid uuid, proof text, until_time timestamptz,
    intent text, provider_realm text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE p ct_admission.policy%ROWTYPE; f ct_admission.flows%ROWTYPE; t timestamptz; a jsonb;
BEGIN
    PERFORM ct_admission.lock_lifecycle();
    t := clock_timestamp();
    SELECT * INTO STRICT p FROM ct_admission.policy;
    IF rid IS NULL OR proof IS NULL OR proof !~ '^[0-9a-f]{64}$'
       OR until_time IS NULL OR until_time <= t OR until_time > t + interval '15 minutes'
       OR intent IS NULL OR intent NOT IN ('signup', 'login')
       OR provider_realm IS DISTINCT FROM p.realm THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    SELECT * INTO f FROM ct_admission.flows WHERE id = rid;
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
        a := ct_admission.availability();
        IF a->>'status' <> 'available' THEN RETURN a; END IF;
    END IF;
    INSERT INTO ct_admission.flows(id, binding, deadline, expires, generation, purpose)
    VALUES (rid, proof, until_time, least(until_time, t + make_interval(secs => p.ttl)),
        p.generation, intent);
    RETURN jsonb_build_object('status', 'reserved');
END $$;

CREATE FUNCTION ct_admission.claim(rid uuid, proof text, digest text, attempt uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE f ct_admission.flows%ROWTYPE; p ct_admission.policy%ROWTYPE;
BEGIN
    PERFORM ct_admission.lock_lifecycle();
    SELECT * INTO STRICT p FROM ct_admission.policy;
    SELECT * INTO f FROM ct_admission.flows WHERE id = rid;
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
    UPDATE ct_admission.flows SET state = 'claimed', claim = attempt, code_digest = digest WHERE id = rid;
    RETURN jsonb_build_object('status', 'exchange');
END $$;

CREATE FUNCTION ct_admission.finish(rid uuid, proof text, attempt uuid, subject text,
    provider_realm text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE f ct_admission.flows%ROWTYPE; p ct_admission.policy%ROWTYPE;
    u careertrellis.app_users%ROWTYPE; new_id bigint;
BEGIN
    PERFORM ct_admission.lock_lifecycle();
    SELECT * INTO STRICT p FROM ct_admission.policy;
    SELECT * INTO f FROM ct_admission.flows WHERE id = rid;
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
            UPDATE ct_admission.flows SET state = 'ineligible' WHERE id = rid;
            RETURN jsonb_build_object('status', 'ineligible');
        END IF;
        IF f.purpose = 'signup' THEN
            UPDATE ct_admission.flows SET state = 'existing' WHERE id = rid;
            RETURN jsonb_build_object('status', 'existing');
        END IF;
        UPDATE ct_admission.flows SET state = 'login', local_id = u.id WHERE id = rid;
        RETURN jsonb_build_object('status', 'login', 'local_id', u.id, 'role', u.role);
    END IF;
    IF f.purpose = 'login' THEN
        UPDATE ct_admission.flows SET state = 'ineligible' WHERE id = rid;
        RETURN jsonb_build_object('status', 'ineligible');
    END IF;
    IF p.mode = 'closed' THEN RETURN jsonb_build_object('status', 'closed'); END IF;
    IF p.mode = 'beta' AND (SELECT count(*) FROM careertrellis.app_users) >= p.cap THEN
        RETURN jsonb_build_object('status', 'full');
    END IF;
    INSERT INTO careertrellis.app_users(workos_user_id, role) VALUES (subject, 'user') RETURNING id INTO new_id;
    PERFORM set_config('careertrellis.user_id', new_id::text, true);
    INSERT INTO careertrellis.profiles(owner_user_id) VALUES (new_id);
    UPDATE ct_admission.flows SET state = 'activated', local_id = new_id WHERE id = rid;
    RETURN jsonb_build_object('status', 'activated', 'local_id', new_id, 'role', 'user');
END $$;

CREATE FUNCTION ct_admission.result(rid uuid, proof text, cancel_flow boolean DEFAULT false) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE f ct_admission.flows%ROWTYPE; gen bigint;
BEGIN
    PERFORM ct_admission.lock_lifecycle();
    SELECT generation INTO STRICT gen FROM ct_admission.policy;
    SELECT * INTO f FROM ct_admission.flows WHERE id = rid;
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
        UPDATE ct_admission.flows SET state = 'cancelled' WHERE id = rid;
        RETURN jsonb_build_object('status', 'cancelled');
    END IF;
    RETURN jsonb_build_object('status', CASE WHEN f.state = 'claimed' THEN 'incomplete' ELSE f.state END);
END $$;

CREATE FUNCTION ct_admission.purge() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE n integer;
BEGIN
    PERFORM ct_admission.lock_lifecycle();
    DELETE FROM ct_admission.flows WHERE expires <= clock_timestamp() AND deadline <= clock_timestamp();
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;

-- Test-only wrappers: original deployment entry points are NOT rewired by this overlay.
CREATE FUNCTION ct_admission.request_erasure() RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE was_active boolean; status_text text;
BEGIN
    IF session_user <> 'careertrellis_runtime' THEN RAISE EXCEPTION 'caller denied' USING ERRCODE = '42501'; END IF;
    PERFORM ct_admission.lock_lifecycle();
    SELECT disabled_at IS NULL INTO was_active FROM careertrellis.app_users
        WHERE id = careertrellis.current_app_user_id();
    SELECT status INTO status_text FROM careertrellis.request_account_erasure();
    IF was_active AND status_text = 'accepted' THEN
        UPDATE ct_admission.policy SET generation = generation + 1;
    END IF;
    RETURN status_text;
END $$;
CREATE FUNCTION ct_admission.finalize_erasure(rid uuid, uid bigint, token uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE done boolean; lease_deadline timestamptz;
BEGIN
    IF session_user <> 'careertrellis_erasure_worker' THEN RAISE EXCEPTION 'caller denied' USING ERRCODE = '42501'; END IF;
    PERFORM ct_admission.lock_lifecycle();
    SELECT lease_expires_at INTO lease_deadline FROM careertrellis.account_erasure_requests
        WHERE id = rid AND owner_user_id = uid AND lease_token = token;
    IF lease_deadline IS NULL OR lease_deadline <= clock_timestamp() THEN RETURN false; END IF;
    done := careertrellis.finalize_account_erasure(rid, uid, token);
    IF clock_timestamp() >= lease_deadline THEN
        -- Roll back the delegated deletion if a later row/catalog wait outlived its lease.
        RAISE EXCEPTION 'isolated erasure lease expired' USING ERRCODE = '40001';
    END IF;
    IF done THEN UPDATE ct_admission.policy SET generation = generation + 1; END IF;
    RETURN done;
END $$;
CREATE FUNCTION ct_admission.seed_allowed(subject text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF session_user <> 'careertrellis_migration' THEN RAISE EXCEPTION 'caller denied' USING ERRCODE = '42501'; END IF;
    PERFORM ct_admission.lock_lifecycle();
    IF EXISTS (SELECT 1 FROM careertrellis.app_users WHERE workos_user_id = subject) THEN RETURN true; END IF;
    RETURN ct_admission.availability()->>'status' = 'available';
END $$;

CREATE FUNCTION ct_admission.erasure_deadline(rid uuid, uid bigint, token uuid) RETURNS timestamptz
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE deadline timestamptz;
BEGIN
    IF session_user <> 'careertrellis_erasure_worker' THEN RAISE EXCEPTION 'caller denied' USING ERRCODE = '42501'; END IF;
    PERFORM ct_admission.lock_lifecycle();
    SELECT lease_expires_at INTO deadline FROM careertrellis.account_erasure_requests
        WHERE id=rid AND owner_user_id=uid AND lease_token=token;
    IF deadline <= clock_timestamp() THEN RETURN NULL; END IF;
    RETURN deadline;
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA ct_admission FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ct_admission FROM PUBLIC;
GRANT USAGE ON SCHEMA ct_admission TO ct_admission_broker, careertrellis_runtime,
    careertrellis_erasure_worker, careertrellis_migration;
GRANT EXECUTE ON FUNCTION ct_admission.availability(),
    ct_admission.reserve(uuid, text, timestamptz, text, text),
    ct_admission.claim(uuid, text, text, uuid), ct_admission.finish(uuid, text, uuid, text, text),
    ct_admission.result(uuid, text, boolean) TO ct_admission_broker;
GRANT EXECUTE ON FUNCTION ct_admission.request_erasure() TO careertrellis_runtime;
GRANT EXECUTE ON FUNCTION ct_admission.finalize_erasure(uuid, bigint, uuid) TO careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION ct_admission.erasure_deadline(uuid, bigint, uuid) TO careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION ct_admission.lock_lifecycle() TO careertrellis_migration, careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION ct_admission.seed_allowed(text) TO careertrellis_migration;
