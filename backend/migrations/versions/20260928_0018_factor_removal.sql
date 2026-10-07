-- Durable exact-factor claims only. Enrollment transfer is deliberately refused:
-- 0016 completion has no independent persisted provider disposition receipt.
SET LOCAL ROLE careertrellis_admission_owner;
CREATE TABLE careertrellis_admission.factor_removal_attempts (
 account_id bigint PRIMARY KEY REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 operation uuid NOT NULL UNIQUE,
 record jsonb NOT NULL CHECK(jsonb_typeof(record)='object' AND octet_length(record::text)<=8192),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE careertrellis_admission.factor_removal_budgets (
 account_id bigint PRIMARY KEY REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 window_start timestamptz NOT NULL, used integer NOT NULL CHECK(used BETWEEN 1 AND 60)
);
REVOKE ALL ON TABLE careertrellis_admission.factor_removal_attempts,careertrellis_admission.factor_removal_budgets
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;
CREATE FUNCTION careertrellis_admission.factor_removal_request(actor jsonb, session_key text, action text,
 op uuid DEFAULT NULL, expected jsonb DEFAULT NULL, replacement jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE uid bigint; auth jsonb; previous jsonb; now_at timestamptz;
 n integer; old_state text; new_state text; old_attempts integer; new_attempts integer; owned careertrellis_admission.account_security_slots%ROWTYPE; has_slot boolean; has_attempt boolean;
BEGIN
 IF session_user<>'careertrellis_runtime' THEN RAISE EXCEPTION 'Factor removal caller denied'; END IF;
 IF actor IS NULL OR jsonb_typeof(actor) IS DISTINCT FROM 'object' OR octet_length(actor::text)>4096 OR session_key IS NULL
    OR session_key !~ '^[0-9a-f]{64}$' OR action IS NULL
    OR action NOT IN ('eligible','get','reserve','cas') THEN RETURN NULL; END IF;
 IF NOT actor ?& ARRAY['authorization','session_id','browser','authenticated_at']
    OR actor-ARRAY['authorization','session_id','browser','authenticated_at']<>'{}'::jsonb THEN RETURN NULL; END IF;
 auth:=actor->'authorization';
 IF jsonb_typeof(auth) IS DISTINCT FROM 'object'
    OR NOT auth ?& ARRAY['local_id','subject','provider_binding','epoch','incarnation']
    OR auth-ARRAY['local_id','subject','provider_binding','epoch','incarnation']<>'{}'::jsonb
    OR jsonb_typeof(auth->'local_id') IS DISTINCT FROM 'number'
    OR jsonb_typeof(auth->'epoch') IS DISTINCT FROM 'number'
    OR octet_length(auth::text)>2048
    OR auth->>'local_id' !~ '^[0-9]{1,19}$' OR auth->>'epoch' !~ '^[0-9]{1,19}$'
    OR jsonb_typeof(auth->'subject') IS DISTINCT FROM 'string'
    OR jsonb_typeof(auth->'provider_binding') IS DISTINCT FROM 'string'
    OR jsonb_typeof(auth->'incarnation') IS DISTINCT FROM 'string' THEN RETURN NULL; END IF;
 uid:=(auth->>'local_id')::bigint;
 IF uid IS NULL OR jsonb_typeof(actor->'session_id') IS DISTINCT FROM 'string'
   OR jsonb_typeof(actor->'browser') IS DISTINCT FROM 'string'
   OR actor->>'session_id' !~ '^session_[A-Za-z0-9_]{6,160}$'
   OR actor->>'browser' !~ '^[a-f0-9]{64}$'
   OR jsonb_typeof(actor->'authenticated_at') IS DISTINCT FROM 'number'
   OR actor->>'authenticated_at' !~ '^[0-9]{1,19}$' THEN RETURN NULL; END IF;
 IF careertrellis_admission.identity_check(uid,auth->>'provider_binding',auth->>'subject',
     (auth->>'epoch')::bigint,(auth->>'incarnation')::uuid) IS NULL THEN RETURN NULL; END IF;
 -- Same lock as provider-session logout guards, held through the write commit.
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-http-session-v1',0));
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-totp-'||uid::text,0));
 now_at:=clock_timestamp();
 IF to_timestamp((actor->>'authenticated_at')::bigint)>now_at
   OR to_timestamp((actor->>'authenticated_at')::bigint)<=now_at-interval '300 seconds'
   OR EXISTS(SELECT 1 FROM careertrellis_admission.http_revocations r WHERE r.session_key=factor_removal_request.session_key)
   OR NOT EXISTS(SELECT 1 FROM careertrellis_admission.policy
     WHERE NOT maintenance AND minimum_session_version=2 AND adapter_ready) THEN RETURN NULL; END IF;
 INSERT INTO careertrellis_admission.factor_removal_budgets AS budgets VALUES(uid,now_at,1)
 ON CONFLICT(account_id) DO UPDATE SET
   window_start=CASE WHEN budgets.window_start<=now_at-interval '60 seconds' THEN now_at ELSE budgets.window_start END,
   used=CASE WHEN budgets.window_start<=now_at-interval '60 seconds' THEN 1 ELSE budgets.used+1 END
 WHERE budgets.window_start<=now_at-interval '60 seconds' OR budgets.used<60;
 GET DIAGNOSTICS n=ROW_COUNT;
 IF n=0 THEN RETURN NULL; END IF;

 SELECT * INTO owned FROM careertrellis_admission.account_security_slots WHERE account_id=uid FOR UPDATE;
 has_slot:=FOUND;
 SELECT record INTO previous FROM careertrellis_admission.factor_removal_attempts WHERE account_id=uid FOR UPDATE;
 has_attempt:=FOUND;
 IF NOT has_slot AND (has_attempt OR EXISTS(SELECT 1 FROM careertrellis_admission.totp_attempts WHERE account_id=uid)) THEN
  RAISE EXCEPTION 'Factor removal security ownership unavailable';
 END IF;
 IF has_slot AND owned.operation_kind<>'removal' THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM careertrellis_admission.factor_removal_attempts a WHERE a.account_id=uid AND a.operation IS DISTINCT FROM (previous->>'operation')::uuid) OR has_slot IS DISTINCT FROM has_attempt OR (has_slot AND
    (owned.operation IS DISTINCT FROM (previous->>'operation')::uuid
     OR owned.actor IS DISTINCT FROM previous->'actor'
     OR owned.authorization_binding IS DISTINCT FROM previous->'actor'->'authorization'
     OR owned.stage IS DISTINCT FROM previous->>'state')) THEN
  RAISE EXCEPTION 'Factor removal security ownership unavailable';
 END IF;
 IF has_slot AND owned.authorization_binding IS DISTINCT FROM auth THEN RETURN NULL; END IF;
 IF action='eligible' THEN RETURN 'true'::jsonb; END IF;
 IF action='get' THEN
  IF op IS NULL OR previous IS NULL OR (previous->>'operation')::uuid IS DISTINCT FROM op THEN RETURN NULL; END IF;
  RETURN previous;
 END IF;
 IF op IS NULL OR replacement IS NULL OR jsonb_typeof(replacement) IS DISTINCT FROM 'object'
    OR NOT replacement ?& ARRAY['operation','actor','factor','state','deadline','challenge','attempts','verified_at','delete_acknowledged']
    OR replacement-ARRAY['operation','actor','factor','state','deadline','challenge','attempts','verified_at','delete_acknowledged']<>'{}'::jsonb
    OR octet_length(replacement::text)>8192
    OR jsonb_typeof(replacement->'operation') IS DISTINCT FROM 'string'
    OR replacement->>'operation' IS DISTINCT FROM op::text
    OR replacement->'actor'->'authorization' IS DISTINCT FROM auth
    OR jsonb_typeof(replacement->'factor') IS DISTINCT FROM 'string'
    OR replacement->>'factor' !~ '^auth_factor_[A-Za-z0-9_]{6,160}$'
    OR jsonb_typeof(replacement->'state') IS DISTINCT FROM 'string'
    OR replacement->>'state' NOT IN ('challenge_claimed','challenge_ready','verification_claimed','delete_claimed','delete_recorded','complete')
    OR jsonb_typeof(replacement->'deadline') IS DISTINCT FROM 'number'
    OR replacement->>'deadline' !~ '^[1-9][0-9]{0,18}$'
    OR jsonb_typeof(replacement->'attempts') IS DISTINCT FROM 'number'
    OR replacement->>'attempts' !~ '^[0-5]$'
    OR jsonb_typeof(replacement->'delete_acknowledged') IS DISTINCT FROM 'boolean'
    OR jsonb_typeof(replacement->'challenge') NOT IN ('null','string')
    OR jsonb_typeof(replacement->'verified_at') NOT IN ('null','number') THEN RETURN 'false'::jsonb; END IF;
 new_state:=replacement->>'state'; new_attempts:=(replacement->>'attempts')::integer;
 IF (replacement->>'delete_acknowledged')::boolean IS DISTINCT FROM (new_state IN ('delete_recorded','complete')) THEN RETURN 'false'::jsonb; END IF;
 IF new_state='challenge_claimed' THEN
  IF replacement->>'challenge' IS NOT NULL OR new_attempts<>0 THEN RETURN 'false'::jsonb; END IF;
 ELSE
  IF replacement->>'challenge' IS NULL OR replacement->>'challenge' !~ '^auth_challenge_[A-Za-z0-9_]{6,160}$' THEN RETURN 'false'::jsonb; END IF;
 END IF;
 IF new_state IN ('verification_claimed','delete_claimed','delete_recorded','complete') AND new_attempts=0 THEN RETURN 'false'::jsonb; END IF;
 IF new_state IN ('delete_claimed','delete_recorded','complete') THEN
  IF replacement->>'verified_at' IS NULL OR replacement->>'verified_at' !~ '^[1-9][0-9]{0,18}$'
    OR (replacement->>'verified_at')::bigint >= (replacement->>'deadline')::bigint THEN RETURN 'false'::jsonb; END IF;
 ELSIF replacement->>'verified_at' IS NOT NULL THEN RETURN 'false'::jsonb;
 END IF;
 IF action='reserve' THEN
  IF has_slot OR has_attempt OR expected IS NOT NULL OR replacement->'actor' IS DISTINCT FROM actor
    OR new_state<>'challenge_claimed'
    OR (replacement->>'deadline')::bigint<=extract(epoch FROM now_at)
    OR (replacement->>'deadline')::bigint>extract(epoch FROM now_at)+300 THEN RETURN 'false'::jsonb; END IF;
  INSERT INTO careertrellis_admission.factor_removal_attempts VALUES(uid,op,replacement,now_at);
  INSERT INTO careertrellis_admission.account_security_slots(account_id,operation_kind,operation,authorization_binding,actor,stage)
   VALUES(uid,'removal',op,auth,actor,new_state);
  RETURN 'true'::jsonb;
 END IF;
 IF previous IS NULL OR previous IS DISTINCT FROM expected
    OR replacement->'actor' IS DISTINCT FROM previous->'actor'
    OR replacement->>'operation' IS DISTINCT FROM previous->>'operation'
    OR replacement->>'factor' IS DISTINCT FROM previous->>'factor' THEN RETURN 'false'::jsonb; END IF;
 old_state:=previous->>'state'; old_attempts:=(previous->>'attempts')::integer;
 -- A fresh session can finish reconciliation only; all mutation uses original actor.
 IF NOT (old_state='delete_recorded' AND new_state='complete') AND previous->'actor' IS DISTINCT FROM actor THEN RETURN 'false'::jsonb; END IF;
 IF old_state='challenge_claimed' AND new_state='challenge_ready' THEN
  IF new_attempts<>0 OR (replacement->>'deadline')::bigint>(previous->>'deadline')::bigint
    OR (replacement->>'deadline')::bigint<=extract(epoch FROM now_at) THEN RETURN 'false'::jsonb; END IF;
 ELSE
  IF replacement->'deadline' IS DISTINCT FROM previous->'deadline'
    OR replacement->'challenge' IS DISTINCT FROM previous->'challenge' THEN RETURN 'false'::jsonb; END IF;
  IF old_state='challenge_ready' AND new_state='verification_claimed' THEN
   IF new_attempts<>old_attempts+1 OR new_attempts>5
     OR (previous->>'deadline')::bigint<=extract(epoch FROM now_at) THEN RETURN 'false'::jsonb; END IF;
  ELSIF old_state='verification_claimed' AND new_state='challenge_ready' THEN
   IF new_attempts<>old_attempts OR (previous->>'deadline')::bigint<=extract(epoch FROM now_at) THEN RETURN 'false'::jsonb; END IF;
  ELSIF old_state='verification_claimed' AND new_state='delete_claimed' THEN
   IF new_attempts<>old_attempts OR (previous->>'deadline')::bigint<=extract(epoch FROM now_at)
     OR (replacement->>'verified_at')::bigint>extract(epoch FROM now_at)
     OR (replacement->>'verified_at')::bigint<=extract(epoch FROM now_at)-30 THEN RETURN 'false'::jsonb; END IF;
  ELSIF (old_state='delete_claimed' AND new_state='delete_recorded')
     OR (old_state='delete_recorded' AND new_state='complete') THEN
   IF new_attempts<>old_attempts OR replacement->'verified_at' IS DISTINCT FROM previous->'verified_at' THEN RETURN 'false'::jsonb; END IF;
  ELSE RETURN 'false'::jsonb;
  END IF;
 END IF;
 UPDATE careertrellis_admission.factor_removal_attempts SET record=replacement,updated_at=now_at WHERE account_id=uid;
 UPDATE careertrellis_admission.account_security_slots SET stage=new_state,updated_at=now_at WHERE account_id=uid;
 RETURN 'true'::jsonb;
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb)
 FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker,
 careertrellis_admission_broker,careertrellis_migration;
GRANT EXECUTE ON FUNCTION careertrellis_admission.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb) TO careertrellis_schema_owner;
SET LOCAL ROLE careertrellis_schema_owner;
CREATE FUNCTION careertrellis.factor_removal_request(actor jsonb,session_key text,action text,
 op uuid DEFAULT NULL,expected jsonb DEFAULT NULL,replacement jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT careertrellis_admission.factor_removal_request(actor,session_key,action,op,expected,replacement)
$$;
REVOKE ALL ON FUNCTION careertrellis.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb)
 FROM PUBLIC,careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;
GRANT EXECUTE ON FUNCTION careertrellis.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb) TO careertrellis_runtime;
