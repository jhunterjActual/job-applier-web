-- Private tables stay outside broad application grants; the public wrapper's
-- restricted execute grants are also preserved by grant-runtime.sql replay.

SET LOCAL ROLE careertrellis_admission_owner;

CREATE TABLE careertrellis_admission.totp_attempts (
 account_id bigint PRIMARY KEY REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 operation uuid NOT NULL UNIQUE,
 record jsonb NOT NULL CHECK(jsonb_typeof(record)='object' AND octet_length(record::text)<=8192),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- One row per local account, including after epoch/provider changes. Only account
-- erasure removes the slot: expiry must not make an uncertain create repeatable.
CREATE TABLE careertrellis_admission.totp_budgets (
 account_id bigint PRIMARY KEY REFERENCES careertrellis.app_users(id) ON DELETE CASCADE,
 window_start timestamptz NOT NULL, used integer NOT NULL CHECK(used BETWEEN 1 AND 60)
);
REVOKE ALL ON TABLE careertrellis_admission.totp_attempts,careertrellis_admission.totp_budgets FROM PUBLIC,
 careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker;

CREATE FUNCTION careertrellis_admission.totp_request(actor jsonb, session_key text, action text,
 op uuid DEFAULT NULL, expected jsonb DEFAULT NULL, replacement jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE uid bigint; auth jsonb; previous jsonb; now_at timestamptz;
 n integer; old_state text; new_state text; old_attempts integer; new_attempts integer;
BEGIN
 IF session_user<>'careertrellis_runtime' THEN RAISE EXCEPTION 'TOTP caller denied'; END IF;
 IF actor IS NULL OR jsonb_typeof(actor)<>'object' OR session_key IS NULL
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
    OR auth->>'local_id' !~ '^[0-9]+$' OR auth->>'epoch' !~ '^[0-9]+$'
    OR jsonb_typeof(auth->'subject') IS DISTINCT FROM 'string'
    OR jsonb_typeof(auth->'provider_binding') IS DISTINCT FROM 'string'
    OR jsonb_typeof(auth->'incarnation') IS DISTINCT FROM 'string' THEN RETURN NULL; END IF;
 uid:=(auth->>'local_id')::bigint;
 IF uid IS NULL OR actor->>'session_id' IS NULL OR actor->>'browser' IS NULL
   OR actor->>'session_id' !~ '^session_[A-Za-z0-9_]{6,160}$'
   OR actor->>'browser' !~ '^[a-f0-9]{64}$'
   OR jsonb_typeof(actor->'authenticated_at') IS DISTINCT FROM 'number'
   OR actor->>'authenticated_at' !~ '^[0-9]+$' THEN RETURN NULL; END IF;
 IF careertrellis_admission.identity_check(uid,auth->>'provider_binding',auth->>'subject',
     (auth->>'epoch')::bigint,(auth->>'incarnation')::uuid) IS NULL THEN RETURN NULL; END IF;
 -- Same lock as provider-session logout guards, held through the write commit.
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-http-session-v1',0));
 PERFORM pg_advisory_xact_lock(hashtextextended('careertrellis-totp-'||uid::text,0));
 now_at:=clock_timestamp();
 IF to_timestamp((actor->>'authenticated_at')::bigint)>now_at
   OR to_timestamp((actor->>'authenticated_at')::bigint)<=now_at-interval '300 seconds'
   OR EXISTS(SELECT 1 FROM careertrellis_admission.http_revocations r WHERE r.session_key=totp_request.session_key)
   OR NOT EXISTS(SELECT 1 FROM careertrellis_admission.policy
     WHERE NOT maintenance AND minimum_session_version=2 AND adapter_ready) THEN RETURN NULL; END IF;
 INSERT INTO careertrellis_admission.totp_budgets AS budgets VALUES(uid,now_at,1)
 ON CONFLICT(account_id) DO UPDATE SET
   window_start=CASE WHEN budgets.window_start<=now_at-interval '60 seconds' THEN now_at ELSE budgets.window_start END,
   used=CASE WHEN budgets.window_start<=now_at-interval '60 seconds' THEN 1 ELSE budgets.used+1 END
 WHERE budgets.window_start<=now_at-interval '60 seconds' OR budgets.used<60;
 GET DIAGNOSTICS n=ROW_COUNT;
 IF n=0 THEN RETURN NULL; END IF;
 IF action='eligible' THEN RETURN 'true'::jsonb; END IF;
 SELECT record INTO previous FROM careertrellis_admission.totp_attempts WHERE account_id=uid FOR UPDATE;
 -- Scrub without changing state, attempts or identity. This also invalidates any
 -- stale CAS writer holding the pre-scrub ciphertext.
 IF previous IS NOT NULL AND previous->>'sealed_secret' IS NOT NULL
   AND (previous->>'deadline')::bigint<=extract(epoch FROM now_at) THEN
   previous:=jsonb_set(previous,'{sealed_secret}','null'::jsonb);
   UPDATE careertrellis_admission.totp_attempts SET record=previous,updated_at=now_at WHERE account_id=uid;
 END IF;
 IF action='get' THEN
   IF previous->'actor'->'authorization' IS DISTINCT FROM auth
      OR (op IS NOT NULL AND (previous->>'operation')::uuid<>op) THEN RETURN NULL; END IF;
   RETURN previous;
 END IF;
 IF replacement IS NULL OR jsonb_typeof(replacement) IS DISTINCT FROM 'object'
    OR NOT replacement ?& ARRAY['operation','actor','state','deadline','factor','challenge','sealed_secret','attempts']
    OR replacement-ARRAY['operation','actor','state','deadline','factor','challenge','sealed_secret','attempts']<>'{}'::jsonb
    OR octet_length(replacement::text)>8192
    OR replacement->>'operation' IS NULL OR (replacement->>'operation')::uuid IS DISTINCT FROM op
    OR replacement->'actor'->'authorization' IS DISTINCT FROM auth
    OR replacement->>'deadline' IS NULL OR replacement->>'attempts' IS NULL
    OR jsonb_typeof(replacement->'deadline') IS DISTINCT FROM 'number'
    OR jsonb_typeof(replacement->'attempts') IS DISTINCT FROM 'number'
    OR jsonb_typeof(replacement->'sealed_secret') NOT IN ('null','string')
    OR jsonb_typeof(replacement->'factor') NOT IN ('null','string')
    OR jsonb_typeof(replacement->'challenge') NOT IN ('null','string')
    OR (replacement->>'deadline') !~ '^[0-9]+$'
    OR (replacement->>'attempts') !~ '^[0-5]$'
    OR replacement->>'state' IS NULL THEN RETURN 'false'::jsonb; END IF;
 new_state:=replacement->>'state'; new_attempts:=(replacement->>'attempts')::integer;
 IF action='reserve' THEN
   IF previous IS NOT NULL OR replacement->'actor' IS DISTINCT FROM actor OR new_state<>'creating'
     OR new_attempts<>0 OR replacement->>'factor' IS NOT NULL OR replacement->>'challenge' IS NOT NULL
     OR replacement->>'sealed_secret' IS NOT NULL
     OR (replacement->>'deadline')::bigint<=extract(epoch FROM now_at)
     OR (replacement->>'deadline')::bigint>extract(epoch FROM now_at)+300 THEN RETURN 'false'::jsonb; END IF;
   INSERT INTO careertrellis_admission.totp_attempts VALUES(uid,op,replacement,now_at);
   RETURN 'true'::jsonb;
 END IF;
 IF previous IS NULL OR previous IS DISTINCT FROM expected
   OR replacement->'actor' IS DISTINCT FROM previous->'actor'
   OR replacement->>'operation' IS DISTINCT FROM previous->>'operation' THEN RETURN 'false'::jsonb; END IF;
 old_state:=previous->>'state'; old_attempts:=(previous->>'attempts')::integer;
 IF old_state='creating' AND new_state='pending' THEN
   IF previous->'actor' IS DISTINCT FROM actor OR new_attempts<>0
      OR replacement->>'factor' IS NULL OR replacement->>'factor' !~ '^auth_factor_[A-Za-z0-9_]{6,160}$'
      OR replacement->>'challenge' IS NULL OR replacement->>'challenge' !~ '^auth_challenge_[A-Za-z0-9_]{6,160}$'
      OR replacement->>'sealed_secret' IS NULL
      OR (replacement->>'deadline')::bigint> (previous->>'deadline')::bigint
      OR (replacement->>'deadline')::bigint<=extract(epoch FROM now_at) THEN RETURN 'false'::jsonb; END IF;
 ELSE
   IF replacement->>'factor' IS DISTINCT FROM previous->>'factor'
      OR replacement->>'challenge' IS DISTINCT FROM previous->>'challenge'
      OR replacement->>'deadline' IS DISTINCT FROM previous->>'deadline' THEN RETURN 'false'::jsonb; END IF;
   IF new_state IN ('closed','complete') AND old_state<>'complete' AND new_attempts=old_attempts THEN
     IF replacement->>'sealed_secret' IS NOT NULL
       OR (new_state='complete' AND previous->>'factor' IS NULL) THEN RETURN 'false'::jsonb; END IF;
   ELSIF old_state='pending' AND new_state='verifying' AND new_attempts=old_attempts+1 AND new_attempts<=5
     OR old_state='verifying' AND new_state='pending' AND new_attempts=old_attempts THEN
     IF previous->'actor' IS DISTINCT FROM actor
       OR replacement->>'sealed_secret' IS DISTINCT FROM previous->>'sealed_secret'
       OR (previous->>'deadline')::bigint<=extract(epoch FROM now_at) THEN RETURN 'false'::jsonb; END IF;
   ELSE RETURN 'false'::jsonb;
   END IF;
 END IF;
 UPDATE careertrellis_admission.totp_attempts SET record=replacement,updated_at=now_at WHERE account_id=uid;
 RETURN 'true'::jsonb;
END $$;

CREATE FUNCTION careertrellis_admission.totp_scrub_expired(maximum integer DEFAULT 100)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE n integer;
BEGIN
 IF session_user<>'careertrellis_migration' OR maximum IS NULL OR maximum NOT BETWEEN 1 AND 1000 THEN
   RAISE EXCEPTION 'TOTP scrub caller denied'; END IF;
 WITH targets AS (SELECT account_id FROM careertrellis_admission.totp_attempts
   WHERE record->>'sealed_secret' IS NOT NULL
    AND (record->>'deadline')::bigint<=extract(epoch FROM clock_timestamp())
   ORDER BY account_id LIMIT maximum FOR UPDATE SKIP LOCKED)
 UPDATE careertrellis_admission.totp_attempts a SET record=jsonb_set(a.record,'{sealed_secret}','null'::jsonb),
   updated_at=clock_timestamp() FROM targets t WHERE a.account_id=t.account_id;
 GET DIAGNOSTICS n=ROW_COUNT;
 RETURN n;
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.totp_request(jsonb,text,text,uuid,jsonb,jsonb),
 careertrellis_admission.totp_scrub_expired(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION careertrellis_admission.totp_request(jsonb,text,text,uuid,jsonb,jsonb) TO careertrellis_schema_owner;
GRANT EXECUTE ON FUNCTION careertrellis_admission.totp_scrub_expired(integer) TO careertrellis_migration;
SET LOCAL ROLE careertrellis_schema_owner;
CREATE FUNCTION careertrellis.totp_request(actor jsonb, session_key text, action text,
 op uuid DEFAULT NULL, expected jsonb DEFAULT NULL, replacement jsonb DEFAULT NULL)
RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT careertrellis_admission.totp_request(actor,session_key,action,op,expected,replacement)
$$;
REVOKE ALL ON FUNCTION careertrellis.totp_request(jsonb,text,text,uuid,jsonb,jsonb)
 FROM PUBLIC,careertrellis_worker,careertrellis_erasure_worker;
GRANT EXECUTE ON FUNCTION careertrellis.totp_request(jsonb,text,text,uuid,jsonb,jsonb) TO careertrellis_runtime;
