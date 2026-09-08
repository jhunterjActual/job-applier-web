-- ISOLATED EXPERIMENT ONLY: requires the test-only roles and database below.
-- Never install in careertrellis or add to the Alembic migration chain.
DO $$ BEGIN
    IF current_database() <> 'ct_signup_prototype' THEN
        RAISE EXCEPTION 'prototype database required';
    END IF;
END $$;

CREATE SCHEMA admission_prototype AUTHORIZATION ct_signup_owner;
SET LOCAL ROLE ct_signup_owner;
REVOKE ALL ON SCHEMA admission_prototype FROM PUBLIC;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Synthetic stand-in, not the application's app_users table.
CREATE TABLE admission_prototype.app_users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workos_user_id text UNIQUE NOT NULL,
    role text NOT NULL DEFAULT 'user' CHECK (role IN ('owner', 'user')),
    disabled boolean NOT NULL DEFAULT false,
    erasing boolean NOT NULL DEFAULT false
);
ALTER TABLE admission_prototype.app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE admission_prototype.app_users FORCE ROW LEVEL SECURITY;
CREATE POLICY broker_owner ON admission_prototype.app_users
    TO ct_signup_owner USING (true) WITH CHECK (true);
CREATE POLICY own_identity ON admission_prototype.app_users
    TO ct_signup_reader USING (
        workos_user_id = current_setting('careertrellis.workos_user_id', true)
    );

CREATE TABLE admission_prototype.policy (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    mode text NOT NULL DEFAULT 'closed' CHECK (mode IN ('closed', 'beta', 'open')),
    account_cap integer NOT NULL DEFAULT 1 CHECK (account_cap > 0),
    reservation_seconds integer NOT NULL DEFAULT 600
        CHECK (reservation_seconds BETWEEN 30 AND 900),
    erasure_generation bigint NOT NULL DEFAULT 0
);
INSERT INTO admission_prototype.policy DEFAULT VALUES;

CREATE TABLE admission_prototype.reservations (
    request_id uuid PRIMARY KEY,
    browser_digest text NOT NULL CHECK (browser_digest ~ '^[0-9a-f]{64}$'),
    flow_deadline timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    erasure_generation bigint NOT NULL,
    state text NOT NULL DEFAULT 'reserved'
        CHECK (state IN ('reserved', 'activated', 'cancelled')),
    -- Intentionally no FK: deletion must not turn a consumed callback into new admission.
    local_user_id bigint,
    CHECK ((state = 'activated') = (local_user_id IS NOT NULL))
);
CREATE INDEX live_reservations ON admission_prototype.reservations (expires_at)
    WHERE state = 'reserved';

CREATE FUNCTION admission_prototype.availability() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE p admission_prototype.policy%ROWTYPE; n bigint; t timestamptz;
BEGIN
    SELECT * INTO STRICT p FROM admission_prototype.policy FOR UPDATE;
    t := clock_timestamp();
    IF p.mode = 'closed' THEN RETURN jsonb_build_object('status', 'closed'); END IF;
    SELECT (SELECT count(*) FROM admission_prototype.app_users) +
        (SELECT count(*) FROM admission_prototype.reservations
         WHERE state = 'reserved' AND expires_at > t
           AND erasure_generation = p.erasure_generation) INTO n;
    IF p.mode = 'beta' AND n >= p.account_cap THEN
        RETURN jsonb_build_object('status', 'full');
    END IF;
    RETURN jsonb_build_object('status', 'available');
END $$;

CREATE FUNCTION admission_prototype.reserve(
    request uuid, proof text, deadline timestamptz
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE p admission_prototype.policy%ROWTYPE;
    r admission_prototype.reservations%ROWTYPE; n bigint; t timestamptz;
BEGIN
    SELECT * INTO STRICT p FROM admission_prototype.policy FOR UPDATE;
    t := clock_timestamp();
    IF request IS NULL OR proof IS NULL OR proof !~ '^[0-9a-f]{64}$'
       OR deadline IS NULL OR deadline <= t OR deadline > t + interval '15 minutes' THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    SELECT * INTO r FROM admission_prototype.reservations WHERE request_id = request;
    IF FOUND THEN
        IF r.browser_digest <> proof OR r.flow_deadline <> deadline THEN
            RETURN jsonb_build_object('status', 'invalid');
        END IF;
        IF r.expires_at <= t OR r.erasure_generation <> p.erasure_generation THEN
            RETURN jsonb_build_object('status', 'expired');
        END IF;
        -- Retry never extends the lease or returns a local user ID.
        RETURN jsonb_build_object('status', r.state);
    END IF;
    IF p.mode = 'closed' THEN RETURN jsonb_build_object('status', 'closed'); END IF;
    SELECT (SELECT count(*) FROM admission_prototype.app_users) +
        (SELECT count(*) FROM admission_prototype.reservations
         WHERE state = 'reserved' AND expires_at > t
           AND erasure_generation = p.erasure_generation) INTO n;
    IF p.mode = 'beta' AND n >= p.account_cap THEN
        RETURN jsonb_build_object('status', 'full');
    END IF;
    INSERT INTO admission_prototype.reservations
        (request_id, browser_digest, flow_deadline, expires_at, erasure_generation)
    VALUES (request, proof, deadline,
        least(deadline, t + make_interval(secs => p.reservation_seconds)),
        p.erasure_generation);
    RETURN jsonb_build_object('status', 'reserved');
END $$;

CREATE FUNCTION admission_prototype.activate(
    request uuid, proof text, verified_subject text
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE p admission_prototype.policy%ROWTYPE;
    r admission_prototype.reservations%ROWTYPE;
    u admission_prototype.app_users%ROWTYPE; t timestamptz; n bigint;
BEGIN
    SELECT * INTO STRICT p FROM admission_prototype.policy FOR UPDATE;
    t := clock_timestamp();
    IF request IS NULL OR proof IS NULL OR verified_subject IS NULL
       OR verified_subject !~ '^user_[A-Za-z0-9_]{6,120}$' THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    SELECT * INTO r FROM admission_prototype.reservations WHERE request_id = request;
    IF NOT FOUND OR r.browser_digest <> proof THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    IF r.expires_at <= t THEN RETURN jsonb_build_object('status', 'expired'); END IF;
    IF r.state = 'activated' THEN
        SELECT * INTO u FROM admission_prototype.app_users WHERE id = r.local_user_id;
        IF NOT FOUND OR u.workos_user_id <> verified_subject OR u.disabled OR u.erasing THEN
            RETURN jsonb_build_object('status', 'ineligible');
        END IF;
        RETURN jsonb_build_object('status', 'activated', 'local_user_id', u.id);
    END IF;
    IF r.state <> 'reserved' THEN RETURN jsonb_build_object('status', 'cancelled'); END IF;
    IF r.erasure_generation <> p.erasure_generation THEN
        RETURN jsonb_build_object('status', 'expired');
    END IF;
    IF p.mode = 'closed' THEN RETURN jsonb_build_object('status', 'closed'); END IF;
    SELECT * INTO u FROM admission_prototype.app_users WHERE workos_user_id = verified_subject;
    IF FOUND THEN
        UPDATE admission_prototype.reservations SET state = 'cancelled' WHERE request_id = request;
        IF u.disabled OR u.erasing THEN RETURN jsonb_build_object('status', 'ineligible'); END IF;
        -- No linking, rebinding, session grant, or role change. Use ordinary sign-in.
        RETURN jsonb_build_object('status', 'existing_account');
    END IF;
    SELECT count(*) INTO n FROM admission_prototype.app_users;
    IF p.mode = 'beta' AND n >= p.account_cap THEN
        RETURN jsonb_build_object('status', 'full');
    END IF;
    INSERT INTO admission_prototype.app_users (workos_user_id) VALUES (verified_subject)
        RETURNING * INTO u;
    UPDATE admission_prototype.reservations SET state = 'activated', local_user_id = u.id
        WHERE request_id = request;
    RETURN jsonb_build_object('status', 'activated', 'local_user_id', u.id);
END $$;

CREATE FUNCTION admission_prototype.cancel(request uuid, proof text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r admission_prototype.reservations%ROWTYPE;
BEGIN
    PERFORM singleton FROM admission_prototype.policy FOR UPDATE;
    SELECT * INTO r FROM admission_prototype.reservations WHERE request_id = request;
    IF NOT FOUND OR proof IS NULL OR r.browser_digest <> proof THEN
        RETURN jsonb_build_object('status', 'invalid');
    END IF;
    IF r.state = 'activated' THEN RETURN jsonb_build_object('status', 'activated'); END IF;
    UPDATE admission_prototype.reservations SET state = 'cancelled' WHERE request_id = request;
    RETURN jsonb_build_object('status', 'cancelled');
END $$;

-- Fixture-only lifecycle seam; NOT a replacement for application erasure.
-- Invalidate all outstanding flows without retaining erased external identities.
CREATE FUNCTION admission_prototype.erase_fixture(target bigint) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    UPDATE admission_prototype.policy SET erasure_generation = erasure_generation + 1;
    DELETE FROM admission_prototype.app_users WHERE id = target;
END $$;

CREATE FUNCTION admission_prototype.purge_expired() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    PERFORM singleton FROM admission_prototype.policy FOR UPDATE;
    -- Keep terminal/expired receipts until their original signed flow is unusable.
    -- Purging at the shorter lease deadline would allow that same flow to reserve again.
    DELETE FROM admission_prototype.reservations
        WHERE expires_at <= clock_timestamp() AND flow_deadline <= clock_timestamp();
END $$;

REVOKE ALL ON ALL TABLES IN SCHEMA admission_prototype FROM PUBLIC, ct_signup_broker, ct_signup_reader;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA admission_prototype FROM PUBLIC, ct_signup_broker, ct_signup_reader;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA admission_prototype FROM PUBLIC, ct_signup_broker, ct_signup_reader;
GRANT USAGE ON SCHEMA admission_prototype TO ct_signup_broker, ct_signup_reader;
GRANT SELECT ON admission_prototype.app_users TO ct_signup_reader;
GRANT EXECUTE ON FUNCTION admission_prototype.availability() TO ct_signup_reader, ct_signup_broker;
GRANT EXECUTE ON FUNCTION admission_prototype.reserve(uuid, text, timestamptz),
    admission_prototype.activate(uuid, text, text), admission_prototype.cancel(uuid, text)
    TO ct_signup_broker;
