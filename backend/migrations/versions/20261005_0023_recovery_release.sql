SET LOCAL ROLE careertrellis_admission_owner;
-- Preserve ordinary transition entry points while preventing a consumed
-- recovery operation from bypassing the effect and fresh-proof checks below.
ALTER FUNCTION careertrellis_admission.identity_attest(uuid,bigint,text,timestamptz,timestamptz,boolean)
 RENAME TO identity_attest_nonrecovery_base;
ALTER FUNCTION careertrellis_admission.identity_activate(uuid,bigint)
 RENAME TO identity_activate_nonrecovery_base;
ALTER FUNCTION careertrellis_admission.identity_resume(uuid,uuid,bigint,timestamptz,boolean)
 RENAME TO identity_resume_nonrecovery_base;
REVOKE ALL ON FUNCTION careertrellis_admission.identity_attest_nonrecovery_base(uuid,bigint,text,timestamptz,timestamptz,boolean),
 careertrellis_admission.identity_activate_nonrecovery_base(uuid,bigint),
 careertrellis_admission.identity_resume_nonrecovery_base(uuid,uuid,bigint,timestamptz,boolean)
 FROM PUBLIC,careertrellis_migration,careertrellis_schema_owner,careertrellis_runtime,
 careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker;
CREATE FUNCTION careertrellis_admission.identity_attest(op uuid,e bigint,digest text,verified timestamptz,
 expires timestamptz,owner_ok boolean DEFAULT false)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM careertrellis_admission.lock_lifecycle();
 IF EXISTS(SELECT 1 FROM careertrellis_admission.recovery_credential_sets WHERE consumed_operation=op)
 THEN RAISE EXCEPTION 'Recovery completion wrapper required'; END IF;
 PERFORM careertrellis_admission.identity_attest_nonrecovery_base(op,e,digest,verified,expires,owner_ok);
END $$;
CREATE FUNCTION careertrellis_admission.identity_activate(op uuid,e bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM careertrellis_admission.lock_lifecycle();
 IF EXISTS(SELECT 1 FROM careertrellis_admission.recovery_credential_sets WHERE consumed_operation=op)
 THEN RAISE EXCEPTION 'Recovery completion wrapper required'; END IF;
 RETURN careertrellis_admission.identity_activate_nonrecovery_base(op,e);
END $$;
CREATE FUNCTION careertrellis_admission.identity_resume(previous uuid,op uuid,e bigint,until_time timestamptz,owner_ok boolean DEFAULT false)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM careertrellis_admission.lock_lifecycle();
 IF EXISTS(WITH RECURSIVE lineage AS (
    SELECT operation,predecessor FROM careertrellis_admission.identity_transitions WHERE operation=previous
    UNION ALL SELECT t.operation,t.predecessor FROM careertrellis_admission.identity_transitions t
      JOIN lineage l ON t.operation=l.predecessor)
   SELECT 1 FROM lineage l JOIN careertrellis_admission.recovery_credential_sets c ON c.consumed_operation=l.operation)
 THEN RAISE EXCEPTION 'Recovery forward resume requires independent recovery authority'; END IF;
 RETURN careertrellis_admission.identity_resume_nonrecovery_base(previous,op,e,until_time,owner_ok);
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.identity_attest(uuid,bigint,text,timestamptz,timestamptz,boolean),
 careertrellis_admission.identity_activate(uuid,bigint),
 careertrellis_admission.identity_resume(uuid,uuid,bigint,timestamptz,boolean)
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis_admission.identity_attest(uuid,bigint,text,timestamptz,timestamptz,boolean),
 careertrellis_admission.identity_activate(uuid,bigint),
 careertrellis_admission.identity_resume(uuid,uuid,bigint,timestamptz,boolean) TO careertrellis_migration;
CREATE TABLE careertrellis_admission.recovery_release_plans (
 operation uuid PRIMARY KEY REFERENCES careertrellis_admission.identity_transitions(operation) ON DELETE CASCADE,
 plan_digest text NOT NULL CHECK(plan_digest ~ '^[0-9a-f]{64}$'),
 effects jsonb NOT NULL CHECK(jsonb_typeof(effects)='array' AND jsonb_array_length(effects)<=101),
 reserved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 released_at timestamptz, evidence_digest text
);
CREATE TABLE careertrellis_admission.recovery_release_effects (
 operation uuid NOT NULL REFERENCES careertrellis_admission.recovery_release_plans(operation) ON DELETE CASCADE,
 effect_id uuid NOT NULL, kind text NOT NULL CHECK(kind IN ('delete_factor','revoke_session')),
 target text NOT NULL CHECK(length(target) BETWEEN 1 AND 200),
 state text NOT NULL DEFAULT 'ready' CHECK(state IN ('ready','claimed','succeeded','failed','uncertain')),
 claimed_at timestamptz, finished_at timestamptz, receipt_digest text,
 PRIMARY KEY(operation,effect_id), UNIQUE(operation,kind,target)
);
REVOKE ALL ON careertrellis_admission.recovery_release_plans,careertrellis_admission.recovery_release_effects
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;

-- Trusted operator boundary. Evidence is supplied only by the protected
-- verifier/coordinator after complete provider reads and signed token checking.
-- JSON input is not a public proof API and never grants runtime this authority.
CREATE FUNCTION careertrellis_admission.recovery_release_request(
 op uuid, action text, payload jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET timezone='UTC' AS $$
DECLARE t careertrellis_admission.identity_transitions%ROWTYPE;
 a careertrellis_admission.identity_accounts%ROWTYPE;
 c careertrellis_admission.recovery_credential_sets%ROWTYPE;
 p careertrellis_admission.recovery_release_plans%ROWTYPE;
 slot careertrellis_admission.account_security_slots%ROWTYPE;
 item jsonb; effects_json jsonb; v_auth jsonb; v_actor jsonb; boundary timestamptz;
 authenticated timestamptz; observed timestamptz; expires timestamptz; result jsonb;
BEGIN
 IF session_user<>'careertrellis_migration' OR op IS NULL OR payload IS NULL
  OR jsonb_typeof(payload)<>'object' OR octet_length(payload::text)>65536 THEN
  RAISE EXCEPTION 'Recovery release denied'; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 SELECT * INTO STRICT t FROM careertrellis_admission.identity_transitions WHERE operation=op;
 SELECT * INTO STRICT c FROM careertrellis_admission.recovery_credential_sets
  WHERE consumed_operation=op AND state='consumed' AND account_id=t.account_id AND consumed_epoch=t.epoch;
 SELECT * INTO STRICT a FROM careertrellis_admission.identity_accounts WHERE id=t.account_id;
 IF t.old_binding IS DISTINCT FROM t.target_binding OR t.old_subject IS DISTINCT FROM t.target_subject
  OR a.incarnation IS DISTINCT FROM t.incarnation OR a.epoch IS DISTINCT FROM t.epoch
  OR a.binding IS DISTINCT FROM t.old_binding OR a.subject IS DISTINCT FROM t.old_subject
  OR a.disabled OR a.role<>t.expected_role OR careertrellis_admission.identity_erasing(a.id)
  OR t.old_binding IS DISTINCT FROM (SELECT binding FROM careertrellis_admission.identity_provider)
  THEN RAISE EXCEPTION 'Recovery context changed'; END IF;
 SELECT * INTO p FROM careertrellis_admission.recovery_release_plans WHERE operation=op;
 IF action='context' THEN
  SELECT COALESCE(jsonb_agg(jsonb_build_object('effect_id',effect_id,'kind',kind,'target',target,
    'state',state,'finished_at',finished_at) ORDER BY effect_id),'[]'::jsonb) INTO effects_json
    FROM careertrellis_admission.recovery_release_effects WHERE operation=op;
  RETURN jsonb_build_object('operation',op,'account_id',a.id,'incarnation',t.incarnation,'epoch',t.epoch,
   'binding',t.old_binding,'subject',t.old_subject,'started',t.started,'deadline',t.deadline,
   'plan_digest',p.plan_digest,'reserved_at',p.reserved_at,'effects',effects_json,'state',t.state);
 END IF;
 IF NOT a.held OR t.state<>'held' OR t.deadline<=clock_timestamp() THEN
  RAISE EXCEPTION 'Recovery hold unavailable'; END IF;
 SELECT * INTO slot FROM careertrellis_admission.account_security_slots WHERE account_id=a.id FOR UPDATE;
 IF action='reserve' THEN
  IF (payload->>'epoch')::bigint IS DISTINCT FROM t.epoch
   OR (payload->>'incarnation')::uuid IS DISTINCT FROM t.incarnation
   OR payload->>'plan_digest' IS NULL OR payload->>'plan_digest' !~ '^[0-9a-f]{64}$'
   OR jsonb_typeof(payload->'effects') IS DISTINCT FROM 'array'
   OR jsonb_array_length(payload->'effects')>101 THEN RAISE EXCEPTION 'Recovery plan invalid'; END IF;
  IF p.operation IS NOT NULL THEN
   IF p.plan_digest IS DISTINCT FROM payload->>'plan_digest' OR p.effects IS DISTINCT FROM payload->'effects'
    THEN RAISE EXCEPTION 'Recovery plan conflict'; END IF;
   RETURN 'true'::jsonb;
  END IF;
  IF slot.account_id IS NOT NULL AND (slot.operation_kind<>'recovery'
    OR slot.operation<>c.operation OR slot.stage<>'active') THEN
   RAISE EXCEPTION 'Prior security effect unresolved'; END IF;
  INSERT INTO careertrellis_admission.recovery_release_plans(operation,plan_digest,effects)
   VALUES(op,payload->>'plan_digest',payload->'effects');
  FOR item IN SELECT value FROM jsonb_array_elements(payload->'effects') LOOP
   IF jsonb_typeof(item)<>'object' OR item->>'effect_id' IS NULL
    OR item->>'kind' IS NULL OR item->>'target' IS NULL
    OR (item->>'kind'='revoke_session' AND item->>'target' !~ '^session_[A-Za-z0-9_]{1,180}$')
    OR (item->>'kind'='delete_factor' AND item->>'target' !~ '^auth_factor_[A-Za-z0-9_]{1,180}$')
    THEN RAISE EXCEPTION 'Recovery effect invalid'; END IF;
   INSERT INTO careertrellis_admission.recovery_release_effects(operation,effect_id,kind,target)
    VALUES(op,(item->>'effect_id')::uuid,item->>'kind',item->>'target');
  END LOOP;
  IF (SELECT count(*) FROM careertrellis_admission.recovery_release_effects WHERE operation=op AND kind='delete_factor')>1
   THEN RAISE EXCEPTION 'Recovery factor scope invalid'; END IF;
  v_auth:=jsonb_set(c.actor->'authorization','{epoch}',to_jsonb(t.epoch));
  v_actor:=jsonb_set(c.actor,'{authorization}',v_auth);
  INSERT INTO careertrellis_admission.account_security_slots(account_id,operation_kind,operation,authorization_binding,actor,stage)
   VALUES(a.id,'recovery',op,v_auth,v_actor,'recovering') ON CONFLICT(account_id) DO UPDATE
    SET operation=op,authorization_binding=v_auth,actor=v_actor,stage='recovering',updated_at=clock_timestamp();
  RETURN 'true'::jsonb;
 END IF;
 IF p.operation IS NULL OR slot.operation IS DISTINCT FROM op OR slot.stage<>'recovering'
  THEN RAISE EXCEPTION 'Recovery plan unavailable'; END IF;
 IF action='claim' THEN
  UPDATE careertrellis_admission.recovery_release_effects SET state='claimed',claimed_at=clock_timestamp()
   WHERE operation=op AND effect_id=(payload->>'effect_id')::uuid AND state='ready';
  RETURN to_jsonb(FOUND);
 ELSIF action='record' THEN
  IF payload->>'outcome' IS NULL OR payload->>'outcome' NOT IN ('succeeded','failed','uncertain')
   OR payload->>'receipt_digest' IS NULL OR payload->>'receipt_digest' !~ '^[0-9a-f]{64}$'
   THEN RAISE EXCEPTION 'Recovery result invalid'; END IF;
  UPDATE careertrellis_admission.recovery_release_effects SET state=payload->>'outcome',
   receipt_digest=payload->>'receipt_digest',finished_at=clock_timestamp()
   WHERE operation=op AND effect_id=(payload->>'effect_id')::uuid AND state='claimed';
  RETURN to_jsonb(FOUND);
 ELSIF action='complete' THEN
  IF (payload->>'epoch')::bigint IS DISTINCT FROM t.epoch
   OR (payload->>'incarnation')::uuid IS DISTINCT FROM t.incarnation
   OR payload->>'binding' IS DISTINCT FROM t.old_binding OR payload->>'subject' IS DISTINCT FROM t.old_subject
   OR payload->>'proof_digest' IS NULL OR payload->>'proof_digest' !~ '^[0-9a-f]{64}$'
   OR payload->>'session_id' IS NULL OR payload->>'session_id' !~ '^session_[A-Za-z0-9_]{1,180}$'
   OR payload->'remaining_sessions' IS DISTINCT FROM jsonb_build_array(payload->>'session_id')
   OR jsonb_typeof(payload->'remaining_factors') IS DISTINCT FROM 'array'
   -- This initial optional-MFA recovery lane has no verifier for remaining
   -- enrolled factors. Match the coordinator's fail-closed zero-factor policy.
   OR jsonb_array_length(payload->'remaining_factors')<>0
   OR EXISTS(SELECT 1 FROM careertrellis_admission.recovery_release_effects WHERE operation=op AND state<>'succeeded')
   OR EXISTS(SELECT 1 FROM careertrellis_admission.recovery_release_effects e WHERE e.operation=op
     AND ((e.kind='delete_factor' AND payload->'remaining_factors' ? e.target)
      OR (e.kind='revoke_session' AND e.target=payload->>'session_id')))
   THEN RAISE EXCEPTION 'Recovery evidence incomplete'; END IF;
  SELECT greatest(p.reserved_at,COALESCE(max(finished_at),p.reserved_at)) INTO boundary
   FROM careertrellis_admission.recovery_release_effects WHERE operation=op;
  authenticated:=(payload->>'authenticated_at')::timestamptz;
  observed:=(payload->>'observed_at')::timestamptz; expires:=(payload->>'expires_at')::timestamptz;
  IF authenticated IS NULL OR observed IS NULL OR expires IS NULL OR authenticated<=boundary
   OR authenticated>observed OR observed>clock_timestamp() OR authenticated<clock_timestamp()-interval '300 seconds'
   OR expires<=clock_timestamp() OR expires>t.deadline OR expires>authenticated+interval '300 seconds'
   THEN RAISE EXCEPTION 'Recovery proof stale'; END IF;
  PERFORM careertrellis_admission.identity_attest_nonrecovery_base(op,t.epoch,payload->>'proof_digest',authenticated,expires,t.owner_authorized);
  result:=careertrellis_admission.identity_activate_nonrecovery_base(op,t.epoch);
  UPDATE careertrellis_admission.recovery_release_plans SET released_at=clock_timestamp(),evidence_digest=payload->>'proof_digest'
   WHERE operation=op;
  UPDATE careertrellis_admission.account_security_slots SET stage='released',updated_at=clock_timestamp() WHERE account_id=a.id;
  RETURN result;
 END IF;
 RAISE EXCEPTION 'Recovery action invalid';
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.recovery_release_request(uuid,text,jsonb)
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis_admission.recovery_release_request(uuid,text,jsonb) TO careertrellis_migration;

-- Preserve attributable completed enrollment when its singleton is retired
-- after an acknowledged recovery release. No uncertain enrollment is archived.
CREATE TABLE careertrellis_admission.recovery_enrollment_history (
 operation uuid PRIMARY KEY,
 account_id bigint NOT NULL REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 release_operation uuid NOT NULL REFERENCES careertrellis_admission.recovery_release_plans(operation),
 attempt jsonb NOT NULL, disposition jsonb NOT NULL, slot jsonb NOT NULL,
 archived_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON careertrellis_admission.recovery_enrollment_history FROM PUBLIC,
 careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;

ALTER FUNCTION careertrellis_admission.totp_request(jsonb,text,text,uuid,jsonb,jsonb)
 RENAME TO totp_request_prerecovery;
ALTER FUNCTION careertrellis_admission.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb)
 RENAME TO factor_removal_request_prerecovery;
DO $migration$
DECLARE definition text;
BEGIN
 SELECT pg_get_functiondef('careertrellis_admission.factor_removal_request_prerecovery(jsonb,text,text,uuid,jsonb,jsonb)'::regprocedure)
 INTO definition;
 IF position('factor_removal_request.session_key' IN definition)=0 THEN
  RAISE EXCEPTION 'Removal function binding unavailable'; END IF;
 EXECUTE replace(definition,'factor_removal_request.','factor_removal_request_prerecovery.');
END $migration$;
REVOKE ALL ON FUNCTION careertrellis_admission.totp_request_prerecovery(jsonb,text,text,uuid,jsonb,jsonb),
 careertrellis_admission.factor_removal_request_prerecovery(jsonb,text,text,uuid,jsonb,jsonb)
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;

CREATE FUNCTION careertrellis_admission.recovery_factor_request(actor jsonb, session_key text, action text,
 op uuid, expected jsonb, replacement jsonb, mode text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE uid bigint; auth jsonb; result jsonb;
 owned careertrellis_admission.account_security_slots%ROWTYPE;
 previous careertrellis_admission.totp_attempts%ROWTYPE;
 disposition careertrellis_admission.enrollment_dispositions%ROWTYPE;
 credentials careertrellis_admission.recovery_credential_sets%ROWTYPE;
BEGIN
 IF session_user<>'careertrellis_runtime' OR mode NOT IN ('enrollment','removal') THEN
  RAISE EXCEPTION 'Recovery factor caller denied'; END IF;
 auth:=actor->'authorization'; uid:=(auth->>'local_id')::bigint;
 PERFORM careertrellis_admission.lock_lifecycle();
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-http-session-v1',0));
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-totp-'||uid::text,0));
 SELECT * INTO owned FROM careertrellis_admission.account_security_slots WHERE account_id=uid FOR UPDATE;
 IF NOT FOUND OR owned.operation_kind<>'recovery' THEN
  IF mode='enrollment' THEN
   RETURN careertrellis_admission.totp_request_prerecovery(actor,session_key,action,op,expected,replacement);
  END IF;
  RETURN careertrellis_admission.factor_removal_request_prerecovery(actor,session_key,action,op,expected,replacement);
 END IF;
 -- Includes full actor shape, current identity/epoch, fresh auth, local-session
 -- revocation, maintenance and v2 policy checks before any handoff/read.
 IF careertrellis_admission.recovery_credentials_request(actor,session_key,'eligible') IS DISTINCT FROM 'true'::jsonb
  OR owned.authorization_binding IS DISTINCT FROM auth THEN RETURN NULL; END IF;
 SELECT * INTO previous FROM careertrellis_admission.totp_attempts WHERE account_id=uid FOR UPDATE;
 SELECT * INTO disposition FROM careertrellis_admission.enrollment_dispositions WHERE account_id=uid FOR UPDATE;
 IF previous.account_id IS NULL OR disposition.account_id IS NULL
  OR previous.operation IS DISTINCT FROM disposition.operation
  OR previous.record IS DISTINCT FROM disposition.record
  OR previous.record->>'state' IS DISTINCT FROM 'complete'
  OR previous.record->'sealed_secret' IS DISTINCT FROM 'null'::jsonb
  OR disposition.consumed_by IS NOT NULL THEN RETURN NULL; END IF;
 IF owned.stage='active' THEN
  SELECT * INTO credentials FROM careertrellis_admission.recovery_credential_sets
   WHERE operation=owned.operation AND account_id=uid AND state='active';
  IF NOT FOUND OR credentials.actor IS DISTINCT FROM owned.actor
   OR previous.record->'actor'->'authorization' IS DISTINCT FROM auth THEN RETURN NULL; END IF;
  IF mode='enrollment' THEN
   IF action IN ('eligible','get') THEN
    RETURN careertrellis_admission.totp_request_0016_internal(actor,session_key,action,op,expected,replacement);
   END IF;
   RETURN 'false'::jsonb;
  END IF;
  IF action='eligible' THEN RETURN 'true'::jsonb; END IF;
  IF action='get' THEN RETURN NULL; END IF;
  IF action<>'reserve' OR replacement->>'factor' IS DISTINCT FROM previous.record->>'factor'
   OR EXISTS(SELECT 1 FROM careertrellis_admission.factor_removal_attempts WHERE account_id=uid)
   THEN RETURN 'false'::jsonb; END IF;
  -- Existing removal reserve consumes the exact attributable disposition.
  UPDATE careertrellis_admission.account_security_slots SET operation_kind='enrollment',
   operation=previous.operation,actor=previous.record->'actor',stage='complete',updated_at=clock_timestamp()
   WHERE account_id=uid;
  result:=careertrellis_admission.factor_removal_request_prerecovery(actor,session_key,action,op,expected,replacement);
  IF result IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'Recovery removal handoff refused'; END IF;
  RETURN result;
 END IF;
 IF owned.stage<>'released' OR mode<>'enrollment' THEN RETURN NULL; END IF;
 SELECT * INTO credentials FROM careertrellis_admission.recovery_credential_sets
  WHERE consumed_operation=owned.operation AND account_id=uid AND state='consumed';
 IF NOT FOUND OR credentials.consumed_epoch IS DISTINCT FROM (auth->>'epoch')::bigint
  OR NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_transitions t
    JOIN careertrellis_admission.recovery_release_plans p ON p.operation=t.operation
    WHERE t.operation=owned.operation AND t.account_id=uid AND t.state='committed'
     AND t.epoch=(auth->>'epoch')::bigint AND t.incarnation=(auth->>'incarnation')::uuid
     AND p.released_at IS NOT NULL AND p.evidence_digest IS NOT NULL)
  OR EXISTS(SELECT 1 FROM careertrellis_admission.recovery_release_effects
    WHERE operation=owned.operation AND state<>'succeeded')
  OR NOT EXISTS(SELECT 1 FROM careertrellis_admission.recovery_release_effects
    WHERE operation=owned.operation AND kind='delete_factor' AND state='succeeded'
     AND target=previous.record->>'factor')
  OR previous.record->'actor'->'authorization' IS DISTINCT FROM credentials.actor->'authorization'
  OR EXISTS(SELECT 1 FROM careertrellis_admission.factor_removal_attempts WHERE account_id=uid)
  THEN RETURN NULL; END IF;
 IF action='eligible' THEN RETURN 'true'::jsonb; END IF;
 IF action='get' THEN RETURN NULL; END IF;
 IF action<>'reserve' OR op IS NULL OR op=previous.operation OR op=owned.operation THEN RETURN 'false'::jsonb; END IF;
 INSERT INTO careertrellis_admission.recovery_enrollment_history(operation,account_id,release_operation,attempt,disposition,slot)
  VALUES(previous.operation,uid,owned.operation,to_jsonb(previous),to_jsonb(disposition),to_jsonb(owned));
 DELETE FROM careertrellis_admission.enrollment_dispositions WHERE account_id=uid AND operation=previous.operation;
 DELETE FROM careertrellis_admission.totp_attempts WHERE account_id=uid AND operation=previous.operation;
 result:=careertrellis_admission.totp_request_0016_internal(actor,session_key,action,op,expected,replacement);
 IF result IS DISTINCT FROM 'true'::jsonb THEN RAISE EXCEPTION 'Recovery enrollment handoff refused'; END IF;
 UPDATE careertrellis_admission.account_security_slots SET operation_kind='enrollment',operation=op,
  authorization_binding=auth,actor=recovery_factor_request.actor,stage='creating',updated_at=clock_timestamp()
  WHERE account_id=uid;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.recovery_factor_request(jsonb,text,text,uuid,jsonb,jsonb,text)
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;
CREATE FUNCTION careertrellis_admission.totp_request(actor jsonb,session_key text,action text,
 op uuid DEFAULT NULL,expected jsonb DEFAULT NULL,replacement jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT careertrellis_admission.recovery_factor_request(actor,session_key,action,op,expected,replacement,'enrollment')
$$;
CREATE FUNCTION careertrellis_admission.factor_removal_request(actor jsonb,session_key text,action text,
 op uuid DEFAULT NULL,expected jsonb DEFAULT NULL,replacement jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT careertrellis_admission.recovery_factor_request(actor,session_key,action,op,expected,replacement,'removal')
$$;
REVOKE ALL ON FUNCTION careertrellis_admission.totp_request(jsonb,text,text,uuid,jsonb,jsonb),
 careertrellis_admission.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb)
 FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker,
 careertrellis_admission_broker,careertrellis_migration;
GRANT EXECUTE ON FUNCTION careertrellis_admission.totp_request(jsonb,text,text,uuid,jsonb,jsonb),
 careertrellis_admission.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb) TO careertrellis_schema_owner;
SET LOCAL ROLE careertrellis_schema_owner;
