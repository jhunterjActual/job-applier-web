SET LOCAL ROLE careertrellis_admission_owner;

CREATE TABLE careertrellis_admission.recovery_credential_sets (
 account_id bigint NOT NULL REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 operation uuid PRIMARY KEY,
 actor jsonb NOT NULL CHECK (jsonb_typeof(actor)='object' AND octet_length(actor::text)<=4096),
 proof jsonb NOT NULL CHECK (jsonb_typeof(proof)='object' AND octet_length(proof::text)<=8192),
 purpose text NOT NULL CHECK (purpose IN ('issue_recovery_credentials','replace_recovery_credentials')),
 generation uuid UNIQUE,
 proposed jsonb NOT NULL CHECK (jsonb_typeof(proposed)='object' AND octet_length(proposed::text)<=16384),
 predecessor_generation uuid,
 state text NOT NULL CHECK (state IN ('pending','active','revoked','consumed')),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
 hold_attempts integer NOT NULL DEFAULT 0 CHECK (hold_attempts BETWEEN 0 AND 5),
 deadline bigint NOT NULL CHECK (deadline>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 confirmed_at timestamptz,
 consumed_operation uuid UNIQUE,
 consumed_epoch bigint,
 consumed_at timestamptz
);
REVOKE ALL ON TABLE careertrellis_admission.recovery_credential_sets FROM PUBLIC,
 careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;

CREATE FUNCTION careertrellis_admission.recovery_credentials_request(
 actor jsonb, session_key text, action text, op uuid DEFAULT NULL,
 expected jsonb DEFAULT NULL, replacement jsonb DEFAULT NULL,
 proposed jsonb DEFAULT NULL, submitted text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE uid bigint; auth jsonb; old jsonb; row careertrellis_admission.recovery_credential_sets%ROWTYPE;
 slot careertrellis_admission.account_security_slots%ROWTYPE; has_slot boolean; has_row boolean;
 predecessor careertrellis_admission.totp_attempts%ROWTYPE;
 disposition careertrellis_admission.enrollment_dispositions%ROWTYPE;
 enrollment_ready boolean:=false; transfer_slot boolean:=false;
 active_generation uuid; now_epoch bigint; result jsonb; candidate text;
 equal boolean; matched boolean := false; i integer;
BEGIN
 IF session_user<>'careertrellis_runtime' THEN RAISE EXCEPTION 'Recovery credential caller denied'; END IF;
 IF actor IS NULL OR jsonb_typeof(actor) IS DISTINCT FROM 'object' OR octet_length(actor::text)>4096
    OR session_key IS NULL OR session_key !~ '^[0-9a-f]{64}$'
    OR action NOT IN ('eligible','factor','get','reserve','cas','propose','pending','confirm') THEN RETURN NULL; END IF;
 IF NOT actor ?& ARRAY['authorization','session_id','browser','authenticated_at']
    OR actor-ARRAY['authorization','session_id','browser','authenticated_at']<>'{}'::jsonb THEN RETURN NULL; END IF;
 auth:=actor->'authorization';
 IF jsonb_typeof(auth) IS DISTINCT FROM 'object'
    OR NOT auth ?& ARRAY['local_id','subject','provider_binding','epoch','incarnation']
    OR auth-ARRAY['local_id','subject','provider_binding','epoch','incarnation']<>'{}'::jsonb
    OR auth->>'local_id' !~ '^[0-9]{1,19}$' OR auth->>'epoch' !~ '^[0-9]{1,19}$'
    OR jsonb_typeof(auth->'subject') IS DISTINCT FROM 'string'
    OR jsonb_typeof(auth->'provider_binding') IS DISTINCT FROM 'string'
    OR jsonb_typeof(auth->'incarnation') IS DISTINCT FROM 'string' THEN RETURN NULL; END IF;
 uid:=(auth->>'local_id')::bigint;
 IF uid IS NULL OR actor->>'session_id' !~ '^session_[A-Za-z0-9_]{6,160}$'
    OR actor->>'browser' !~ '^[a-f0-9]{64}$' OR actor->>'authenticated_at' !~ '^[0-9]{1,19}$' THEN RETURN NULL; END IF;
 IF careertrellis_admission.identity_check(uid,auth->>'provider_binding',auth->>'subject',
     (auth->>'epoch')::bigint,(auth->>'incarnation')::uuid) IS NULL THEN RETURN NULL; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-http-session-v1',0));
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-totp-'||uid::text,0));
 now_epoch:=floor(extract(epoch FROM clock_timestamp()));
 IF (actor->>'authenticated_at')::bigint>now_epoch
    OR (actor->>'authenticated_at')::bigint<=now_epoch-300
    OR EXISTS(SELECT 1 FROM careertrellis_admission.http_revocations r WHERE r.session_key=recovery_credentials_request.session_key)
    OR NOT EXISTS(SELECT 1 FROM careertrellis_admission.policy WHERE NOT maintenance AND adapter_ready AND minimum_session_version=2) THEN RETURN NULL; END IF;
 SELECT generation INTO active_generation FROM careertrellis_admission.recovery_credential_sets
  WHERE account_id=uid AND state='active' FOR UPDATE;
 SELECT * INTO slot FROM careertrellis_admission.account_security_slots
  WHERE account_id=uid FOR UPDATE;
 has_slot:=FOUND;
 SELECT * INTO row FROM careertrellis_admission.recovery_credential_sets
  WHERE operation=op AND account_id=uid FOR UPDATE;
 has_row:=FOUND;
 SELECT * INTO predecessor FROM careertrellis_admission.totp_attempts WHERE account_id=uid FOR UPDATE;
 SELECT * INTO disposition FROM careertrellis_admission.enrollment_dispositions WHERE account_id=uid FOR UPDATE;
 enrollment_ready:=predecessor.account_id IS NOT NULL AND disposition.account_id IS NOT NULL
   AND predecessor.operation IS NOT DISTINCT FROM disposition.operation
   AND predecessor.record IS NOT DISTINCT FROM disposition.record
   AND predecessor.record->>'state' IS NOT DISTINCT FROM 'complete'
   AND predecessor.record->'actor'->'authorization' IS NOT DISTINCT FROM auth
   AND predecessor.record->>'factor' ~ '^auth_factor_[A-Za-z0-9_]{6,160}$'
   AND disposition.consumed_by IS NULL
   AND disposition.evidence_kind='positive_challenge_response_and_complete_owner_readback';
 transfer_slot:=has_slot AND slot.operation_kind='enrollment' AND slot.stage='complete'
   AND slot.authorization_binding IS NOT DISTINCT FROM auth
   AND slot.operation IS NOT DISTINCT FROM predecessor.operation
   AND slot.actor IS NOT DISTINCT FROM predecessor.record->'actor' AND enrollment_ready;
 IF action='eligible' THEN
  RETURN to_jsonb(COALESCE(transfer_slot OR (has_slot AND slot.operation_kind='recovery'
    AND slot.authorization_binding IS NOT DISTINCT FROM auth),false));
 END IF;
 IF action='factor' THEN
  IF NOT COALESCE(enrollment_ready AND (transfer_slot OR (has_slot AND slot.operation_kind='recovery'
    AND slot.authorization_binding IS NOT DISTINCT FROM auth)),false) THEN RETURN NULL; END IF;
  RETURN to_jsonb(predecessor.record->>'factor');
 END IF;
 IF op IS NULL THEN RETURN NULL; END IF;
 IF action='get' THEN
  IF NOT has_row OR row.actor->'authorization' IS DISTINCT FROM auth THEN RETURN NULL; END IF;
  RETURN row.proof;
 END IF;
 IF action='pending' THEN
  IF NOT has_row OR row.actor->'authorization' IS DISTINCT FROM auth OR row.state<>'pending' THEN RETURN NULL; END IF;
  RETURN row.proposed;
 END IF;
 IF action='reserve' THEN
  IF has_row OR NOT COALESCE(enrollment_ready,false) OR NOT has_slot
     OR replacement IS NULL OR replacement->>'operation' IS DISTINCT FROM op::text
     OR replacement->>'factor' IS DISTINCT FROM predecessor.record->>'factor'
     OR replacement->>'purpose' NOT IN ('issue_recovery_credentials','replace_recovery_credentials')
     OR replacement->'actor' IS DISTINCT FROM actor OR replacement->>'deadline' !~ '^[1-9][0-9]{0,18}$'
     OR (replacement->>'deadline')::bigint<=now_epoch OR (replacement->>'deadline')::bigint>now_epoch+300 THEN RETURN 'false'::jsonb; END IF;
  IF (replacement->>'purpose'='issue_recovery_credentials') IS DISTINCT FROM (active_generation IS NULL) THEN RETURN 'false'::jsonb; END IF;
  IF has_slot AND NOT transfer_slot AND (slot.operation_kind<>'recovery' OR slot.stage<>'active'
     OR slot.authorization_binding IS DISTINCT FROM auth
     OR NOT EXISTS(SELECT 1 FROM careertrellis_admission.recovery_credential_sets prior
       WHERE prior.operation=slot.operation AND prior.account_id=uid AND prior.state='active'
         AND prior.generation=active_generation)) THEN RETURN 'false'::jsonb; END IF;
  INSERT INTO careertrellis_admission.recovery_credential_sets(account_id,operation,actor,proof,purpose,generation,proposed,predecessor_generation,state,deadline)
   VALUES(uid,op,recovery_credentials_request.actor,replacement,replacement->>'purpose',NULL,'{}'::jsonb,active_generation,'pending',(replacement->>'deadline')::bigint);
  IF has_slot THEN
   UPDATE careertrellis_admission.account_security_slots
    SET operation_kind='recovery',operation=op,authorization_binding=auth,actor=recovery_credentials_request.actor,stage='pending',updated_at=clock_timestamp()
    WHERE account_id=uid;
  ELSE
   INSERT INTO careertrellis_admission.account_security_slots
     (account_id,operation_kind,operation,authorization_binding,actor,stage)
     VALUES(uid,'recovery',op,auth,recovery_credentials_request.actor,'pending');
  END IF;
  RETURN 'true'::jsonb;
 END IF;
 IF NOT has_row OR row.actor IS DISTINCT FROM actor OR row.state NOT IN ('pending','active') THEN RETURN 'false'::jsonb; END IF;
 IF NOT has_slot OR slot.operation_kind<>'recovery' OR slot.operation IS DISTINCT FROM op
    OR slot.authorization_binding IS DISTINCT FROM auth OR slot.actor IS DISTINCT FROM actor THEN RETURN 'false'::jsonb; END IF;
 IF action='cas' THEN
  IF slot.stage<>'pending' OR expected IS NULL OR expected IS DISTINCT FROM row.proof
     OR replacement IS NULL OR replacement->>'operation' IS DISTINCT FROM op::text THEN RETURN 'false'::jsonb; END IF;
  UPDATE careertrellis_admission.recovery_credential_sets SET proof=replacement,deadline=(replacement->>'deadline')::bigint WHERE operation=op;
  RETURN 'true'::jsonb;
 END IF;
 IF action='propose' THEN
  IF slot.stage<>'pending' OR row.state<>'pending' OR expected IS NULL OR expected IS DISTINCT FROM row.proof
     OR replacement IS NULL OR replacement->>'state' IS DISTINCT FROM 'consumed'
     OR proposed IS NULL OR proposed->>'operation' IS DISTINCT FROM op::text THEN RETURN 'false'::jsonb; END IF;
  UPDATE careertrellis_admission.recovery_credential_sets SET proof=replacement, proposed=recovery_credentials_request.proposed,
    generation=(recovery_credentials_request.proposed->>'generation')::uuid, state='pending' WHERE operation=op;
  UPDATE careertrellis_admission.account_security_slots SET stage='proposed',updated_at=clock_timestamp()
   WHERE account_id=uid AND operation=op AND operation_kind='recovery';
  RETURN 'true'::jsonb;
 END IF;
 IF action='confirm' THEN
  IF slot.stage<>'proposed' OR row.state<>'pending' OR proposed IS NULL OR proposed->>'operation' IS DISTINCT FROM op::text
     OR row.deadline<=now_epoch OR row.attempts>=5 THEN RETURN 'false'::jsonb; END IF;
  UPDATE careertrellis_admission.recovery_credential_sets SET attempts=attempts+1 WHERE operation=op;
  IF submitted IS NULL OR submitted !~ '^[0-9a-f]{64}$' THEN RETURN 'false'::jsonb; END IF;
  FOR candidate IN SELECT jsonb_array_elements_text(row.proposed->'hashes') LOOP
   equal:=true;
   FOR i IN 1..64 LOOP
    equal:=equal AND substr(candidate,i,1)=substr(submitted,i,1);
   END LOOP;
   matched:=matched OR equal;
  END LOOP;
  IF NOT matched THEN RETURN 'false'::jsonb; END IF;
  IF active_generation IS NOT NULL AND row.predecessor_generation IS DISTINCT FROM active_generation THEN RETURN 'false'::jsonb; END IF;
  IF active_generation IS NOT NULL THEN
   UPDATE careertrellis_admission.recovery_credential_sets SET state='revoked',confirmed_at=clock_timestamp()
    WHERE account_id=uid AND generation=active_generation AND state='active';
  END IF;
  UPDATE careertrellis_admission.recovery_credential_sets SET state='active',confirmed_at=clock_timestamp() WHERE operation=op;
  UPDATE careertrellis_admission.account_security_slots SET stage='active',updated_at=clock_timestamp()
   WHERE account_id=uid AND operation=op AND operation_kind='recovery';
  RETURN 'true'::jsonb;
 END IF;
 RETURN 'false'::jsonb;
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.recovery_credentials_request(jsonb,text,text,uuid,jsonb,jsonb,jsonb,text)
 FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;
GRANT EXECUTE ON FUNCTION careertrellis_admission.recovery_credentials_request(jsonb,text,text,uuid,jsonb,jsonb,jsonb,text) TO careertrellis_schema_owner;

SET LOCAL ROLE careertrellis_schema_owner;
CREATE FUNCTION careertrellis.recovery_credentials_request(
 actor jsonb, session_key text, action text, op uuid DEFAULT NULL,
 expected jsonb DEFAULT NULL, replacement jsonb DEFAULT NULL,
 proposed jsonb DEFAULT NULL, submitted text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT careertrellis_admission.recovery_credentials_request(actor,session_key,action,op,expected,replacement,proposed,submitted)
$$;
REVOKE ALL ON FUNCTION careertrellis.recovery_credentials_request(jsonb,text,text,uuid,jsonb,jsonb,jsonb,text)
 FROM PUBLIC,careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;
GRANT EXECUTE ON FUNCTION careertrellis.recovery_credentials_request(jsonb,text,text,uuid,jsonb,jsonb,jsonb,text) TO careertrellis_runtime;

SET LOCAL ROLE careertrellis_admission_owner;
CREATE FUNCTION careertrellis_admission.recovery_credentials_hold_context(
 account_id bigint, generation uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row careertrellis_admission.recovery_credential_sets%ROWTYPE;
BEGIN
 IF session_user<>'careertrellis_migration' OR account_id IS NULL OR account_id<=0 OR generation IS NULL THEN
  RAISE EXCEPTION 'Recovery hold context denied'; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 SELECT * INTO row FROM careertrellis_admission.recovery_credential_sets c
  WHERE c.account_id=recovery_credentials_hold_context.account_id
    AND c.generation=recovery_credentials_hold_context.generation AND c.state='active' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Recovery credential unavailable'; END IF;
 RETURN jsonb_build_object('actor',row.actor,'operation',row.operation,'purpose',row.purpose,'generation',row.generation,
   'hashes',row.proposed->'hashes','deadline',row.deadline,'attempts',row.attempts);
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.recovery_credentials_hold_context(bigint,uuid)
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis_admission.recovery_credentials_hold_context(bigint,uuid)
 TO careertrellis_migration;

CREATE FUNCTION careertrellis_admission.recovery_credentials_consume_hold(
 account_id bigint, generation uuid, recovery_op uuid, target_binding text,
 target_subject text, until_time timestamptz, owner_ok boolean, submitted text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE row careertrellis_admission.recovery_credential_sets%ROWTYPE;
 acc careertrellis_admission.identity_accounts%ROWTYPE; auth jsonb;
 candidate text; equal boolean; matched boolean:=false; i integer;
 transition_state text; hold_epoch bigint;
BEGIN
 IF session_user<>'careertrellis_migration' OR account_id IS NULL OR account_id<=0
  OR generation IS NULL OR recovery_op IS NULL OR target_binding IS NULL OR target_binding !~ '^[0-9a-f]{64}$'
  OR target_subject IS NULL OR target_subject !~ '^user_[A-Za-z0-9_]{6,120}$' OR owner_ok IS NULL
  OR until_time IS NULL OR until_time<=clock_timestamp()
  OR until_time>clock_timestamp()+interval '1 hour'
  THEN RETURN NULL; END IF;
 PERFORM careertrellis_admission.lock_lifecycle();
 SELECT * INTO row FROM careertrellis_admission.recovery_credential_sets c
  WHERE c.account_id=recovery_credentials_consume_hold.account_id
    AND c.generation=recovery_credentials_consume_hold.generation FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 -- Consumed operations require read-only reconciliation, never another hold call.
 IF row.state<>'active' OR row.hold_attempts>=5 THEN RETURN NULL; END IF;
 auth:=row.actor->'authorization';
 IF target_binding IS DISTINCT FROM auth->>'provider_binding'
  OR target_subject IS DISTINCT FROM auth->>'subject' THEN RETURN NULL; END IF;
 SELECT * INTO STRICT acc FROM careertrellis_admission.identity_accounts
  WHERE id=recovery_credentials_consume_hold.account_id FOR UPDATE;
 IF acc.disabled OR acc.held OR careertrellis_admission.identity_erasing(acc.id)
  OR acc.binding IS DISTINCT FROM auth->>'provider_binding'
  OR acc.subject IS DISTINCT FROM auth->>'subject'
  OR acc.epoch IS DISTINCT FROM (auth->>'epoch')::bigint
  OR acc.incarnation IS DISTINCT FROM (auth->>'incarnation')::uuid
  OR (acc.role='owner' AND owner_ok IS DISTINCT FROM true) THEN RETURN NULL; END IF;
 UPDATE careertrellis_admission.recovery_credential_sets SET hold_attempts=hold_attempts+1
  WHERE operation=row.operation;
 IF submitted IS NULL OR submitted !~ '^[0-9a-f]{64}$' THEN
  RETURN jsonb_build_object('outcome','code_not_accepted'); END IF;
 FOR candidate IN SELECT jsonb_array_elements_text(row.proposed->'hashes') LOOP
  equal:=true;
  FOR i IN 1..64 LOOP equal:=equal AND substr(candidate,i,1)=substr(submitted,i,1); END LOOP;
  matched:=matched OR equal;
 END LOOP;
 IF NOT matched THEN RETURN jsonb_build_object('outcome','code_not_accepted'); END IF;
 transition_state:=careertrellis_admission.identity_prepare(recovery_op,account_id,
   auth->>'provider_binding',auth->>'subject',(auth->>'epoch')::bigint,
   (auth->>'incarnation')::uuid,target_binding,target_subject,until_time,owner_ok);
 IF transition_state IS DISTINCT FROM 'held' THEN RAISE EXCEPTION 'Recovery hold unavailable'; END IF;
 SELECT epoch INTO hold_epoch FROM careertrellis_admission.identity_transitions
  WHERE operation=recovery_op;
 UPDATE careertrellis_admission.recovery_credential_sets SET state='consumed',
  consumed_operation=recovery_op,consumed_epoch=hold_epoch,consumed_at=clock_timestamp()
  WHERE operation=row.operation;
 RETURN jsonb_build_object('outcome','account_held','operation',recovery_op,'epoch',hold_epoch);
EXCEPTION WHEN unique_violation OR foreign_key_violation OR check_violation THEN
 RAISE EXCEPTION 'Recovery hold unavailable';
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.recovery_credentials_consume_hold(bigint,uuid,uuid,text,text,timestamptz,boolean,text)
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis_admission.recovery_credentials_consume_hold(bigint,uuid,uuid,text,text,timestamptz,boolean,text)
 TO careertrellis_migration;

-- Reconcile an unknown acknowledgement without consuming or preparing again.
CREATE FUNCTION careertrellis_admission.recovery_credentials_hold_receipt(
 account_id bigint, generation uuid, recovery_op uuid, target_binding text,
 target_subject text, until_time timestamptz, owner_ok boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE receipt jsonb;
BEGIN
 IF session_user<>'careertrellis_migration' THEN RAISE EXCEPTION 'Recovery receipt denied'; END IF;
 SELECT jsonb_build_object('outcome','hold_recorded','operation',t.operation,
   'epoch',t.epoch,'transition_state',t.state) INTO receipt
 FROM careertrellis_admission.recovery_credential_sets c
 JOIN careertrellis_admission.identity_transitions t ON t.operation=c.consumed_operation
 WHERE c.account_id=recovery_credentials_hold_receipt.account_id
   AND c.generation=recovery_credentials_hold_receipt.generation AND c.state='consumed'
   AND t.operation=recovery_op AND t.account_id=c.account_id AND t.epoch=c.consumed_epoch
   AND t.target_binding=recovery_credentials_hold_receipt.target_binding
   AND t.target_subject=recovery_credentials_hold_receipt.target_subject
   AND t.deadline=until_time AND t.owner_authorized=owner_ok;
 RETURN receipt;
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.recovery_credentials_hold_receipt(bigint,uuid,uuid,text,text,timestamptz,boolean)
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis_admission.recovery_credentials_hold_receipt(bigint,uuid,uuid,text,text,timestamptz,boolean)
 TO careertrellis_migration;

SET LOCAL ROLE careertrellis_schema_owner;
