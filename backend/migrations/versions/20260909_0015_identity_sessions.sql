-- All private state remains outside the broad application grant namespace.
SET LOCAL ROLE careertrellis_schema_owner;
GRANT UPDATE(workos_user_id) ON careertrellis.app_users TO careertrellis_admission_owner;
CREATE POLICY identity_transition_update ON careertrellis.app_users FOR UPDATE
 TO careertrellis_admission_owner USING(true) WITH CHECK(true);
CREATE POLICY identity_erasure_read ON careertrellis.account_erasure_requests FOR SELECT
 TO careertrellis_admission_owner USING(true);
SET LOCAL ROLE careertrellis_admission_owner;
CREATE TABLE careertrellis_admission.identity_provider (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 binding text CHECK(binding ~ '^[0-9a-f]{64}$')
);
INSERT INTO careertrellis_admission.identity_provider DEFAULT VALUES;
-- Provider lineage survives account erasure; contains no account identities.
CREATE TABLE careertrellis_admission.identity_provider_history(
 binding text PRIMARY KEY CHECK(binding ~ '^[0-9a-f]{64}$'),
 first_used timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE careertrellis_admission.identity_accounts (
 id bigint PRIMARY KEY REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 incarnation uuid NOT NULL DEFAULT gen_random_uuid(),
 epoch bigint NOT NULL DEFAULT 1 CHECK(epoch>0 AND epoch<9223372036854775807),
 binding text CHECK(binding ~ '^[0-9a-f]{64}$'), subject text NOT NULL,
 role text NOT NULL CHECK(role IN ('user','owner')), disabled boolean NOT NULL,
 held boolean NOT NULL DEFAULT false
);
INSERT INTO careertrellis_admission.identity_accounts(id,subject,role,disabled)
 SELECT id,workos_user_id,role,disabled_at IS NOT NULL FROM careertrellis.app_users;
CREATE TABLE careertrellis_admission.identity_history (
 binding text NOT NULL CHECK(binding ~ '^[0-9a-f]{64}$'), subject text NOT NULL,
 account_id bigint NOT NULL REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 state text NOT NULL CHECK(state IN ('active','pending','retired')),
 PRIMARY KEY(binding,subject)
);
CREATE UNIQUE INDEX identity_one_active ON careertrellis_admission.identity_history(account_id) WHERE state='active';
CREATE TABLE careertrellis_admission.identity_transitions (
 operation uuid PRIMARY KEY, account_id bigint NOT NULL REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 incarnation uuid NOT NULL, epoch bigint NOT NULL, old_binding text NOT NULL, old_subject text NOT NULL,
 target_binding text NOT NULL, target_subject text NOT NULL, expected_role text NOT NULL,
 owner_authorized boolean NOT NULL, started timestamptz NOT NULL, deadline timestamptz NOT NULL,
 state text NOT NULL CHECK(state IN ('held','verified','committed','superseded')),
 predecessor uuid UNIQUE REFERENCES careertrellis_admission.identity_transitions(operation),
 evidence_digest text, verified_at timestamptz, evidence_expires timestamptz
);

CREATE FUNCTION careertrellis_admission.identity_sync() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b text;
BEGIN
 PERFORM careertrellis_admission.lock_lifecycle();
 IF TG_OP='INSERT' THEN
  SELECT binding INTO b FROM careertrellis_admission.identity_provider;
  INSERT INTO careertrellis_admission.identity_accounts(id,binding,subject,role,disabled)
   VALUES(NEW.id,b,NEW.workos_user_id,NEW.role,NEW.disabled_at IS NOT NULL);
  IF b IS NOT NULL THEN
   INSERT INTO careertrellis_admission.identity_history VALUES(b,NEW.workos_user_id,NEW.id,'active');
  END IF;
 ELSE
  IF OLD.disabled_at IS DISTINCT FROM NEW.disabled_at OR OLD.role IS DISTINCT FROM NEW.role
   OR OLD.workos_user_id IS DISTINCT FROM NEW.workos_user_id THEN
   PERFORM careertrellis_admission.invalidate_flows();
  END IF;
  UPDATE careertrellis_admission.identity_accounts SET
   epoch=epoch+CASE WHEN OLD.disabled_at IS DISTINCT FROM NEW.disabled_at
      OR role<>NEW.role OR subject<>NEW.workos_user_id THEN 1 ELSE 0 END,
   held=held OR role<>NEW.role OR subject<>NEW.workos_user_id,
   disabled=NEW.disabled_at IS NOT NULL, role=NEW.role WHERE id=NEW.id;
 END IF;
 RETURN NEW;
END $$;
GRANT EXECUTE ON FUNCTION careertrellis_admission.identity_sync() TO careertrellis_schema_owner;
SET LOCAL ROLE careertrellis_schema_owner;
CREATE TRIGGER identity_account_sync AFTER INSERT OR UPDATE OF disabled_at,role,workos_user_id
 ON careertrellis.app_users FOR EACH ROW EXECUTE FUNCTION careertrellis_admission.identity_sync();
SET LOCAL ROLE careertrellis_admission_owner;

CREATE FUNCTION careertrellis_admission.configure_identity_provider(expected text, target text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE b text;
BEGIN
 IF session_user<>'careertrellis_migration' OR target IS NULL OR target !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'identity configuration denied'; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 IF NOT EXISTS(SELECT 1 FROM careertrellis_admission.policy WHERE maintenance AND NOT signup_open) THEN
  RAISE EXCEPTION 'identity configuration requires closed maintenance'; END IF;
 SELECT binding INTO b FROM careertrellis_admission.identity_provider;
 IF b IS DISTINCT FROM expected THEN RAISE EXCEPTION 'identity configuration changed'; END IF;
 IF b IS NULL THEN
  UPDATE careertrellis_admission.identity_accounts SET binding=target;
  INSERT INTO careertrellis_admission.identity_history
   SELECT target,subject,id,'active' FROM careertrellis_admission.identity_accounts;
 END IF;
 UPDATE careertrellis_admission.identity_provider SET binding=target;
 INSERT INTO careertrellis_admission.identity_provider_history(binding) VALUES(target) ON CONFLICT DO NOTHING;
 PERFORM careertrellis_admission.invalidate_flows();
END $$;

-- Operation receipts contain no subjects or credentials. Not admission-purge
-- targets; an exact replay reports history without repeating binding/mappings.
CREATE TABLE careertrellis_admission.identity_configuration_receipts(
 operation uuid PRIMARY KEY,
 plan_digest text NOT NULL CHECK(plan_digest ~ '^[0-9a-f]{64}$'),
 generation bigint NOT NULL CHECK(generation>=0),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION careertrellis_admission.identity_erasing(uid bigint) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM careertrellis.account_erasure_requests WHERE owner_user_id=uid)
$$;
CREATE FUNCTION careertrellis_admission.identity_check(uid bigint,b text,s text,e bigint,i uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a careertrellis_admission.identity_accounts%ROWTYPE;
BEGIN
 -- Shared until the caller commits: containment cannot overtake a protected write.
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'session transaction isolation unsupported'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('careertrellis-admission-lifecycle-v1',0));
 SELECT * INTO a FROM careertrellis_admission.identity_accounts WHERE id=uid;
 IF NOT FOUND OR a.held OR a.disabled OR careertrellis_admission.identity_erasing(uid) OR a.epoch IS DISTINCT FROM e
  OR a.incarnation IS DISTINCT FROM i OR a.binding IS DISTINCT FROM b OR a.subject IS DISTINCT FROM s
  OR b IS NULL OR b IS DISTINCT FROM (SELECT binding FROM careertrellis_admission.identity_provider)
  OR NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_history
   WHERE binding=b AND subject=s AND account_id=uid AND state='active') THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('id',a.id,'role',a.role);
END $$;

CREATE FUNCTION careertrellis_admission.identity_prepare(op uuid,uid bigint,b text,s text,e bigint,i uuid,
 tb text,ts text,until_time timestamptz,owner_ok boolean DEFAULT false)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a careertrellis_admission.identity_accounts%ROWTYPE; p careertrellis_admission.identity_transitions%ROWTYPE;
BEGIN
 IF session_user<>'careertrellis_migration' OR op IS NULL OR tb IS NULL OR tb !~ '^[0-9a-f]{64}$'
  OR ts IS NULL OR ts !~ '^user_[A-Za-z0-9_]{6,120}$' OR owner_ok IS NULL THEN
  RAISE EXCEPTION 'identity preparation denied'; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 SELECT * INTO p FROM careertrellis_admission.identity_transitions WHERE operation=op;
 IF FOUND THEN
  IF (p.account_id,p.old_binding,p.old_subject,p.epoch-1,p.incarnation,p.target_binding,p.target_subject,p.deadline,p.owner_authorized)
   IS DISTINCT FROM (uid,b,s,e,i,tb,ts,until_time,owner_ok) OR p.predecessor IS NOT NULL THEN
   RAISE EXCEPTION 'identity operation conflict'; END IF;
  RETURN p.state;
 END IF;
 IF until_time IS NULL OR until_time<=clock_timestamp() OR until_time>clock_timestamp()+interval '1 hour'
  OR careertrellis_admission.identity_check(uid,b,s,e,i) IS NULL THEN RAISE EXCEPTION 'identity preparation unavailable'; END IF;
 SELECT * INTO STRICT a FROM careertrellis_admission.identity_accounts WHERE id=uid;
 IF a.role='owner' AND NOT owner_ok THEN RAISE EXCEPTION 'owner authorization required'; END IF;
 IF (b,s) IS DISTINCT FROM (tb,ts) THEN
  INSERT INTO careertrellis_admission.identity_history VALUES(tb,ts,uid,'pending');
 END IF;
 UPDATE careertrellis_admission.identity_accounts SET held=true,epoch=epoch+1 WHERE id=uid;
 INSERT INTO careertrellis_admission.identity_transitions(operation,account_id,incarnation,epoch,
  old_binding,old_subject,target_binding,target_subject,expected_role,owner_authorized,started,deadline,state)
 VALUES(op,uid,i,e+1,b,s,tb,ts,a.role,owner_ok,clock_timestamp(),until_time,'held');
 PERFORM careertrellis_admission.invalidate_flows();
 RETURN 'held';
EXCEPTION WHEN unique_violation OR foreign_key_violation OR check_violation THEN
 RAISE EXCEPTION 'identity transition unavailable';
END $$;

-- The lock survives until the intake transaction commits; snapshot comparison and
-- attestation must use this same READ COMMITTED transaction.
CREATE FUNCTION careertrellis_admission.identity_evidence_context(op uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET timezone='UTC' AS $$
DECLARE p careertrellis_admission.identity_transitions%ROWTYPE;
BEGIN
 IF session_user<>'careertrellis_migration' OR current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'identity evidence denied'; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 SELECT * INTO STRICT p FROM careertrellis_admission.identity_transitions WHERE operation=op;
 IF p.state NOT IN ('held','verified') THEN RAISE EXCEPTION 'identity evidence unavailable'; END IF;
 RETURN jsonb_build_object('operation',p.operation,'account_id',p.account_id,'incarnation',p.incarnation,
  'epoch',p.epoch,'old_binding',p.old_binding,'old_subject',p.old_subject,
  'target_binding',p.target_binding,'target_subject',p.target_subject,
  'expected_role',p.expected_role,'owner_authorized',p.owner_authorized,
  'started',p.started,'deadline',p.deadline,'predecessor',p.predecessor);
END $$;

-- Protected operator intake stores only a digest of independently verified evidence.
-- This privileged assertion is not itself an account-holder verification service.
CREATE FUNCTION careertrellis_admission.identity_attest(op uuid,e bigint,digest text,verified timestamptz,
 expires timestamptz,owner_ok boolean DEFAULT false)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p careertrellis_admission.identity_transitions%ROWTYPE; a careertrellis_admission.identity_accounts%ROWTYPE;
BEGIN
 IF session_user<>'careertrellis_migration' OR digest IS NULL OR digest !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'identity attestation denied'; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 SELECT * INTO STRICT p FROM careertrellis_admission.identity_transitions WHERE operation=op;
 SELECT * INTO STRICT a FROM careertrellis_admission.identity_accounts WHERE id=p.account_id;
 IF p.state NOT IN ('held','verified') OR p.epoch IS DISTINCT FROM e OR a.epoch<>p.epoch OR NOT a.held OR a.disabled
  OR careertrellis_admission.identity_erasing(a.id)
  OR a.incarnation<>p.incarnation OR a.role<>p.expected_role OR a.binding<>p.old_binding OR a.subject<>p.old_subject
  OR verified IS NULL OR expires IS NULL OR verified<p.started OR verified>clock_timestamp()
  OR expires<=clock_timestamp() OR expires>p.deadline
  OR (a.role='owner' AND (owner_ok IS DISTINCT FROM true OR NOT p.owner_authorized)) THEN
  RAISE EXCEPTION 'identity attestation unavailable'; END IF;
 IF p.state='verified' AND (p.evidence_digest,p.verified_at,p.evidence_expires) IS DISTINCT FROM (digest,verified,expires) THEN
  RAISE EXCEPTION 'identity evidence conflict'; END IF;
 UPDATE careertrellis_admission.identity_transitions SET state='verified',evidence_digest=digest,
  verified_at=verified,evidence_expires=expires WHERE operation=op;
END $$;

CREATE FUNCTION careertrellis_admission.identity_resume(previous uuid,op uuid,e bigint,until_time timestamptz,owner_ok boolean DEFAULT false)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p careertrellis_admission.identity_transitions%ROWTYPE; n careertrellis_admission.identity_transitions%ROWTYPE;
 a careertrellis_admission.identity_accounts%ROWTYPE;
BEGIN
 IF session_user<>'careertrellis_migration' OR op IS NULL OR op=previous OR owner_ok IS NULL THEN
  RAISE EXCEPTION 'identity resume denied'; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 SELECT * INTO STRICT p FROM careertrellis_admission.identity_transitions WHERE operation=previous;
 SELECT * INTO n FROM careertrellis_admission.identity_transitions WHERE operation=op;
 IF FOUND THEN
  IF (n.predecessor,n.epoch-1,n.deadline,n.owner_authorized) IS DISTINCT FROM (previous,e,until_time,owner_ok) THEN
   RAISE EXCEPTION 'identity resume conflict'; END IF;
  RETURN n.state;
 END IF;
 SELECT * INTO STRICT a FROM careertrellis_admission.identity_accounts WHERE id=p.account_id;
 IF p.state NOT IN ('held','verified') OR p.deadline>clock_timestamp() OR a.epoch IS DISTINCT FROM e OR p.epoch<>e
  OR careertrellis_admission.identity_erasing(a.id)
  OR a.incarnation<>p.incarnation OR NOT a.held OR a.disabled OR a.role<>p.expected_role
  OR a.binding<>p.old_binding OR a.subject<>p.old_subject
  OR until_time IS NULL OR until_time<=clock_timestamp() OR until_time>clock_timestamp()+interval '1 hour'
  OR (a.role='owner' AND NOT owner_ok) THEN RAISE EXCEPTION 'identity resume unavailable'; END IF;
 UPDATE careertrellis_admission.identity_transitions SET state='superseded' WHERE operation=previous;
 UPDATE careertrellis_admission.identity_accounts SET epoch=epoch+1 WHERE id=a.id;
 INSERT INTO careertrellis_admission.identity_transitions(operation,account_id,incarnation,epoch,
  old_binding,old_subject,target_binding,target_subject,expected_role,owner_authorized,started,deadline,state,predecessor)
 VALUES(op,a.id,a.incarnation,e+1,p.old_binding,p.old_subject,p.target_binding,p.target_subject,
  p.expected_role,owner_ok,clock_timestamp(),until_time,'held',previous);
 PERFORM careertrellis_admission.invalidate_flows();
 RETURN 'held';
EXCEPTION WHEN unique_violation OR foreign_key_violation OR check_violation THEN
 RAISE EXCEPTION 'identity transition unavailable';
END $$;

CREATE FUNCTION careertrellis_admission.identity_activate(op uuid,e bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE p careertrellis_admission.identity_transitions%ROWTYPE; a careertrellis_admission.identity_accounts%ROWTYPE;
BEGIN
 IF session_user<>'careertrellis_migration' THEN RAISE EXCEPTION 'identity activation denied'; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 SELECT * INTO STRICT p FROM careertrellis_admission.identity_transitions WHERE operation=op;
 IF p.epoch IS DISTINCT FROM e THEN RAISE EXCEPTION 'identity epoch conflict'; END IF;
 IF p.state='committed' THEN RETURN jsonb_build_object('state','committed','epoch',p.epoch); END IF;
 SELECT * INTO STRICT a FROM careertrellis_admission.identity_accounts WHERE id=p.account_id;
 IF p.state<>'verified' OR p.evidence_expires<=clock_timestamp() OR p.verified_at>clock_timestamp()
  OR careertrellis_admission.identity_erasing(a.id)
  OR a.epoch<>p.epoch OR a.incarnation<>p.incarnation OR NOT a.held OR a.disabled OR a.role<>p.expected_role
  OR a.binding<>p.old_binding OR a.subject<>p.old_subject THEN RAISE EXCEPTION 'identity activation unavailable'; END IF;
 IF (p.old_binding,p.old_subject) IS DISTINCT FROM (p.target_binding,p.target_subject) THEN
  UPDATE careertrellis_admission.identity_history SET state='retired'
   WHERE account_id=a.id AND binding=p.old_binding AND subject=p.old_subject AND state='active';
  IF NOT FOUND THEN RAISE EXCEPTION 'identity source changed'; END IF;
  UPDATE careertrellis_admission.identity_history SET state='active'
   WHERE account_id=a.id AND binding=p.target_binding AND subject=p.target_subject AND state='pending';
  IF NOT FOUND THEN RAISE EXCEPTION 'identity target changed'; END IF;
 END IF;
 UPDATE careertrellis_admission.identity_accounts SET binding=p.target_binding,subject=p.target_subject,held=false WHERE id=a.id;
 -- Compatibility metadata is updated atomically. Authorization uses scoped history.
 UPDATE careertrellis.app_users SET workos_user_id=p.target_subject WHERE id=a.id;
 UPDATE careertrellis_admission.identity_transitions SET state='committed' WHERE operation=op;
 PERFORM careertrellis_admission.invalidate_flows();
 RETURN jsonb_build_object('state','committed','epoch',p.epoch);
EXCEPTION WHEN unique_violation OR foreign_key_violation OR check_violation THEN
 RAISE EXCEPTION 'identity transition unavailable';
END $$;

CREATE FUNCTION careertrellis_admission.finish_session(rid uuid,proof text,attempt uuid,subject text,realm text,b text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; a careertrellis_admission.identity_accounts%ROWTYPE;
BEGIN
 IF session_user<>'careertrellis_admission_broker' THEN RAISE EXCEPTION 'identity finish denied'; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 IF b IS NULL OR b IS DISTINCT FROM (SELECT binding FROM careertrellis_admission.identity_provider) THEN
  RETURN jsonb_build_object('status','ineligible'); END IF;
 IF NOT EXISTS(SELECT 1 FROM careertrellis_admission.flows f,careertrellis_admission.policy p
  WHERE f.id=rid AND f.generation=p.generation) THEN RETURN jsonb_build_object('status','expired'); END IF;
 r:=careertrellis_admission.finish(rid,proof,attempt,subject,realm);
 IF r->>'status' IN ('activated','login') THEN
  SELECT * INTO a FROM careertrellis_admission.identity_accounts WHERE id=(r->>'local_id')::bigint;
  IF careertrellis_admission.identity_check(a.id,b,subject,a.epoch,a.incarnation) IS NULL THEN
   RETURN jsonb_build_object('status','ineligible'); END IF;
  RETURN r||jsonb_build_object('epoch',a.epoch,'incarnation',a.incarnation);
 END IF;
 RETURN r;
EXCEPTION WHEN unique_violation OR foreign_key_violation OR check_violation THEN
 RAISE EXCEPTION 'identity completion unavailable';
END $$;
-- The old bare-subject completion is callable only inside the guarded wrapper.
REVOKE EXECUTE ON FUNCTION careertrellis_admission.finish(uuid,text,uuid,text,text) FROM careertrellis_admission_broker;
REVOKE ALL ON ALL TABLES IN SCHEMA careertrellis_admission FROM PUBLIC,
 careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker;
REVOKE ALL ON FUNCTION careertrellis_admission.identity_check(bigint,text,text,bigint,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION careertrellis_admission.identity_check(bigint,text,text,bigint,uuid) TO careertrellis_schema_owner;
GRANT EXECUTE ON FUNCTION careertrellis_admission.identity_erasing(bigint) TO careertrellis_schema_owner;
GRANT SELECT ON careertrellis_admission.identity_provider,careertrellis_admission.identity_accounts TO careertrellis_schema_owner;
GRANT EXECUTE ON FUNCTION careertrellis_admission.finish_session(uuid,text,uuid,text,text,text) TO careertrellis_admission_broker;
-- Migration login must SET ROLE explicitly; no function is exposed to app callers.
GRANT USAGE ON SCHEMA careertrellis_admission TO careertrellis_migration;
GRANT EXECUTE ON FUNCTION careertrellis_admission.configure_identity_provider(text,text),
 careertrellis_admission.identity_evidence_context(uuid),
 careertrellis_admission.identity_prepare(uuid,bigint,text,text,bigint,uuid,text,text,timestamptz,boolean),
 careertrellis_admission.identity_attest(uuid,bigint,text,timestamptz,timestamptz,boolean),
 careertrellis_admission.identity_resume(uuid,uuid,bigint,timestamptz,boolean),
 careertrellis_admission.identity_activate(uuid,bigint) TO careertrellis_migration;

SET LOCAL ROLE careertrellis_schema_owner;
CREATE FUNCTION careertrellis.identity_session_ready(b text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT session_user='careertrellis_runtime' AND b IS NOT NULL AND
  b IS NOT DISTINCT FROM (SELECT binding FROM careertrellis_admission.identity_provider)
$$;
CREATE FUNCTION careertrellis.identity_owner_ready(b text,s text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT session_user='careertrellis_runtime' AND b IS NOT NULL AND
  b IS NOT DISTINCT FROM (SELECT binding FROM careertrellis_admission.identity_provider)
  AND EXISTS(SELECT 1 FROM careertrellis_admission.identity_accounts
   WHERE binding=b AND subject=s AND role='owner' AND NOT disabled AND NOT held
   AND careertrellis_admission.identity_check(id,b,s,epoch,incarnation) IS NOT NULL)
$$;
CREATE FUNCTION careertrellis.identity_session_check(uid bigint,b text,s text,e bigint,i uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF session_user<>'careertrellis_runtime' THEN RAISE EXCEPTION 'session caller denied'; END IF;
 RETURN careertrellis_admission.identity_check(uid,b,s,e,i);
END $$;
CREATE OR REPLACE FUNCTION careertrellis.current_app_user_id() RETURNS bigint
LANGUAGE plpgsql VOLATILE PARALLEL UNSAFE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE uid bigint; c jsonb; a careertrellis_admission.identity_accounts%ROWTYPE;
BEGIN
 uid:=nullif(current_setting('careertrellis.user_id',true),'')::bigint;
 IF session_user='careertrellis_runtime' THEN
  c:=careertrellis_admission.session_context();
  IF (c->>'minimum_session_version')::integer>=2 THEN
   IF c->>'maintenance'<>'false' OR careertrellis_admission.identity_check(uid,
    nullif(current_setting('careertrellis.provider_binding',true),''),
    nullif(current_setting('careertrellis.workos_user_id',true),''),
    nullif(current_setting('careertrellis.session_epoch',true),'')::bigint,
    nullif(current_setting('careertrellis.account_incarnation',true),'')::uuid) IS NULL THEN RETURN NULL; END IF;
  END IF;
 ELSIF session_user='careertrellis_worker' THEN
  IF current_setting('transaction_isolation')<>'read committed' THEN
   RAISE EXCEPTION 'worker transaction isolation unsupported'; END IF;
  PERFORM pg_advisory_xact_lock_shared(hashtextextended('careertrellis-admission-lifecycle-v1',0));
  SELECT * INTO a FROM careertrellis_admission.identity_accounts WHERE id=uid;
  IF NOT FOUND OR a.disabled OR a.held OR careertrellis_admission.identity_erasing(uid) THEN RETURN NULL; END IF;
 END IF;
 RETURN uid;
END $$;
CREATE POLICY identity_session_restriction ON careertrellis.app_users AS RESTRICTIVE
 TO careertrellis_runtime USING (
  (careertrellis.admission_session_context()->>'minimum_session_version')::integer<2
  OR id=careertrellis.current_app_user_id());
REVOKE ALL ON FUNCTION careertrellis.identity_session_ready(text),
 careertrellis.identity_owner_ready(text,text),
 careertrellis.identity_session_check(bigint,text,text,bigint,uuid) FROM PUBLIC,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.identity_session_ready(text),
 careertrellis.identity_owner_ready(text,text),
 careertrellis.identity_session_check(bigint,text,text,bigint,uuid) TO careertrellis_runtime;
