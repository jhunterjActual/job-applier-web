"""Fail-closed factor feature construction; no environment or database work on import."""
import os


READINESS_SQL = """
WITH required(signature) AS (VALUES
 ('careertrellis.totp_request(jsonb,text,text,uuid,jsonb,jsonb)'),
 ('careertrellis.totp_finish_verified(jsonb,text,jsonb,jsonb)'),
 ('careertrellis.factor_removal_request(jsonb,text,text,uuid,jsonb,jsonb)')),
functions AS (SELECT p.oid,p.prosecdef,p.proconfig,r.rolname FROM required q
 LEFT JOIN pg_proc p ON p.oid=to_regprocedure(q.signature)
 LEFT JOIN pg_roles r ON r.oid=p.proowner)
SELECT (SELECT CASE WHEN count(*)=1 THEN min(version_num) END
 FROM careertrellis.alembic_version) AS schema,
 (session_user='careertrellis_runtime' AND current_user='careertrellis_runtime'
 AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
   JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
   JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='careertrellis_admission' AND c.relname='factor_removal_attempts'
   AND a.attname='initiating_session_key' AND a.attnum>0 AND NOT a.attisdropped
   AND a.attnotnull AND a.atttypid='text'::regtype)
 AND (SELECT bool_and(oid IS NOT NULL AND prosecdef AND rolname='careertrellis_schema_owner'
   AND COALESCE(proconfig @> ARRAY['search_path=pg_catalog'],false)) FROM functions)) AS functions_ready,
 (SELECT bool_and(oid IS NOT NULL
   AND COALESCE(has_function_privilege('careertrellis_runtime',oid,'EXECUTE'),false)
   AND NOT COALESCE(has_function_privilege('careertrellis_worker',oid,'EXECUTE'),true)
   AND NOT COALESCE(has_function_privilege('careertrellis_erasure_worker',oid,'EXECUTE'),true))
 FROM functions) AS grants_ready
"""


def removal_enabled(enrollment_enabled):
    flag=os.environ.get('CAREERTRELLIS_AUTH_V2_REMOVAL_ENABLED','0')
    if flag not in ('0','1') or (flag=='1' and not enrollment_enabled):
        raise ValueError('Invalid authenticator removal configuration.')
    return flag=='1'


async def factor_readiness(store,engine):
    evidence=await store._query(engine,READINESS_SQL,mapping=True)
    if (not isinstance(evidence,dict) or set(evidence)!= {'schema','functions_ready','grants_ready'}
            or evidence['schema'] not in ('20260929_0020','20260929_0021')
            or evidence['functions_ready'] is not True or evidence['grants_ready'] is not True):
        raise ValueError('Factor feature schema is not ready.')
