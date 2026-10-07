-- A durable ownership barrier, not a removal/recovery implementation. No timeout,
-- completion or cleanup frees this slot. Future transfer requires reviewed proof.
SET LOCAL ROLE careertrellis_admission_owner;

CREATE TABLE careertrellis_admission.account_security_slots (
 account_id bigint PRIMARY KEY REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 operation_kind text NOT NULL CHECK(operation_kind IN ('enrollment','removal','recovery')),
 operation uuid NOT NULL UNIQUE,
 authorization_binding jsonb NOT NULL CHECK(jsonb_typeof(authorization_binding)='object' AND octet_length(authorization_binding::text)<=2048),
 actor jsonb NOT NULL CHECK(jsonb_typeof(actor)='object' AND octet_length(actor::text)<=4096),
 stage text NOT NULL CHECK(length(stage) BETWEEN 1 AND 64),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(actor->'authorization' IS NOT DISTINCT FROM authorization_binding),
 CHECK((authorization_binding->>'local_id')::bigint IS NOT DISTINCT FROM account_id)
);
REVOKE ALL ON TABLE careertrellis_admission.account_security_slots FROM PUBLIC,
 careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;

-- Retain every historical slot, including stale-epoch, expired, closed and
-- complete records. Never infer a safe new mutation from state or age.
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM careertrellis_admission.totp_attempts
   WHERE (record->>'operation')::uuid IS DISTINCT FROM operation
    OR (record->'actor'->'authorization'->>'local_id')::bigint IS DISTINCT FROM account_id
    OR jsonb_typeof(record->'actor') IS DISTINCT FROM 'object'
    OR jsonb_typeof(record->'actor'->'authorization') IS DISTINCT FROM 'object'
    OR record->>'state' IS NULL
    OR record->>'state' NOT IN ('creating','pending','verifying','closed','complete')) THEN
  RAISE EXCEPTION 'Enrollment ownership backfill refused';
 END IF;
END $$;
INSERT INTO careertrellis_admission.account_security_slots
 (account_id,operation_kind,operation,authorization_binding,actor,stage)
 SELECT account_id,'enrollment',operation,record->'actor'->'authorization',record->'actor',record->>'state'
 FROM careertrellis_admission.totp_attempts;

ALTER FUNCTION careertrellis_admission.totp_request(jsonb,text,text,uuid,jsonb,jsonb)
 RENAME TO totp_request_0016_internal;
-- Renaming also changes PL/pgSQL's implicit function block label. Preserve the
-- 0016 implementation exactly except its one qualified parameter reference.
DO $migration$
DECLARE definition text; needle text := 'r.session_key=totp_request.session_key';
BEGIN
 SELECT pg_get_functiondef('careertrellis_admission.totp_request_0016_internal(jsonb,text,text,uuid,jsonb,jsonb)'::regprocedure)
  INTO definition;
 IF (length(definition)-length(replace(definition,needle,'')))/length(needle)<>1 THEN
  RAISE EXCEPTION 'Enrollment internal function binding refused';
 END IF;
 EXECUTE replace(definition,needle,'r.session_key=totp_request_0016_internal.session_key');
END $migration$;
REVOKE ALL ON FUNCTION careertrellis_admission.totp_request_0016_internal(jsonb,text,text,uuid,jsonb,jsonb)
 FROM PUBLIC,careertrellis_schema_owner,careertrellis_runtime,careertrellis_worker,
 careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;

CREATE FUNCTION careertrellis_admission.totp_request(actor jsonb, session_key text, action text,
 op uuid DEFAULT NULL, expected jsonb DEFAULT NULL, replacement jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE uid bigint; auth jsonb; result jsonb;
 owned careertrellis_admission.account_security_slots%ROWTYPE;
 previous careertrellis_admission.totp_attempts%ROWTYPE;
 has_slot boolean; has_attempt boolean;
BEGIN
 IF session_user<>'careertrellis_runtime' THEN RAISE EXCEPTION 'TOTP caller denied'; END IF;
 IF actor IS NULL OR jsonb_typeof(actor) IS DISTINCT FROM 'object'
    OR octet_length(actor::text)>4096 OR session_key IS NULL OR session_key !~ '^[0-9a-f]{64}$'
    OR action IS NULL OR action NOT IN ('eligible','get','reserve','cas') THEN RETURN NULL; END IF;
 auth:=actor->'authorization';
 IF jsonb_typeof(auth) IS DISTINCT FROM 'object'
    OR NOT auth ?& ARRAY['local_id','subject','provider_binding','epoch','incarnation']
    OR auth-ARRAY['local_id','subject','provider_binding','epoch','incarnation']<>'{}'::jsonb
    OR jsonb_typeof(auth->'local_id') IS DISTINCT FROM 'number'
    OR jsonb_typeof(auth->'epoch') IS DISTINCT FROM 'number'
    OR auth->>'local_id' !~ '^[0-9]{1,19}$' OR auth->>'epoch' !~ '^[0-9]{1,19}$'
    OR jsonb_typeof(auth->'subject') IS DISTINCT FROM 'string'
    OR jsonb_typeof(auth->'provider_binding') IS DISTINCT FROM 'string'
    OR jsonb_typeof(auth->'incarnation') IS DISTINCT FROM 'string' THEN RETURN NULL; END IF;
 -- Numeric/UUID overflow or malformed casts fail the entire transaction closed.
 uid:=(auth->>'local_id')::bigint;
 IF careertrellis_admission.identity_check(uid,auth->>'provider_binding',auth->>'subject',
     (auth->>'epoch')::bigint,(auth->>'incarnation')::uuid) IS NULL THEN RETURN NULL; END IF;
 -- Match 0016 lock order. The existing per-account TOTP mutex now also guards
 -- security ownership; the inner function reacquires these locks reentrantly.
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-http-session-v1',0));
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-totp-'||uid::text,0));
 SELECT * INTO owned FROM careertrellis_admission.account_security_slots WHERE account_id=uid FOR UPDATE;
 has_slot:=FOUND;
 IF has_slot AND (owned.operation_kind<>'enrollment' OR owned.authorization_binding IS DISTINCT FROM auth
    OR (op IS NOT NULL AND owned.operation<>op)) THEN RETURN NULL; END IF;
 SELECT * INTO previous FROM careertrellis_admission.totp_attempts WHERE account_id=uid FOR UPDATE;
 has_attempt:=FOUND;
 IF has_slot IS DISTINCT FROM has_attempt OR (has_slot AND
    (owned.operation IS DISTINCT FROM previous.operation
     OR owned.actor IS DISTINCT FROM previous.record->'actor'
     OR owned.stage IS DISTINCT FROM previous.record->>'state')) THEN
  RAISE EXCEPTION 'Enrollment security ownership unavailable';
 END IF;
 result:=careertrellis_admission.totp_request_0016_internal(actor,session_key,action,op,expected,replacement);
 IF result='true'::jsonb AND action IN ('reserve','cas') THEN
  SELECT * INTO STRICT previous FROM careertrellis_admission.totp_attempts WHERE account_id=uid;
  IF previous.operation IS DISTINCT FROM op OR previous.record->'actor'->'authorization' IS DISTINCT FROM auth THEN
   RAISE EXCEPTION 'Enrollment security ownership changed';
  END IF;
  IF action='reserve' THEN
   INSERT INTO careertrellis_admission.account_security_slots
    (account_id,operation_kind,operation,authorization_binding,actor,stage)
    VALUES(uid,'enrollment',previous.operation,auth,previous.record->'actor',previous.record->>'state');
  ELSE
   IF NOT has_slot THEN RAISE EXCEPTION 'Enrollment security ownership missing'; END IF;
   UPDATE careertrellis_admission.account_security_slots SET stage=previous.record->>'state',updated_at=clock_timestamp()
    WHERE account_id=uid;
  END IF;
 END IF;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.totp_request(jsonb,text,text,uuid,jsonb,jsonb)
 FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker,
 careertrellis_admission_broker,careertrellis_migration;
GRANT EXECUTE ON FUNCTION careertrellis_admission.totp_request(jsonb,text,text,uuid,jsonb,jsonb)
 TO careertrellis_schema_owner;

SET LOCAL ROLE careertrellis_schema_owner;
CREATE OR REPLACE FUNCTION careertrellis.totp_request(actor jsonb, session_key text, action text,
 op uuid DEFAULT NULL, expected jsonb DEFAULT NULL, replacement jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT careertrellis_admission.totp_request(actor,session_key,action,op,expected,replacement)
$$;
REVOKE ALL ON FUNCTION careertrellis.totp_request(jsonb,text,text,uuid,jsonb,jsonb)
 FROM PUBLIC,careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker,careertrellis_migration;
GRANT EXECUTE ON FUNCTION careertrellis.totp_request(jsonb,text,text,uuid,jsonb,jsonb) TO careertrellis_runtime;
