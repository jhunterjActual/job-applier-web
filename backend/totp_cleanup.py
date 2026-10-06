"""Bounded DB-host ciphertext scrub lane; scheduled wrapper is separate.

Use the existing privileged local PostgreSQL transport, never web/migration
credentials. totp_cleanup_operations supplies versioned persisted health state.
"""
import time
import admission_operations as ops

BATCH = 500
ROUNDS = 4


def batch_sql():
    return """BEGIN;
SET LOCAL statement_timeout='4s'; SET LOCAL lock_timeout='500ms';
SET LOCAL transaction_timeout='6s';
SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-admission-maintenance-v1',0));
SELECT careertrellis_admission.lock_lifecycle();
DO $$ BEGIN
 IF session_user<>'postgres'
  OR (SELECT version_num IN ('20260915_0016','20260929_0021','20261005_0023')
      FROM careertrellis.alembic_version) IS DISTINCT FROM true
  OR (SELECT NOT maintenance AND minimum_session_version=2 AND adapter_ready
      FROM careertrellis_admission.policy) IS DISTINCT FROM true
  OR (SELECT binding FROM careertrellis_admission.identity_provider) IS NULL
  OR EXISTS(SELECT 1 FROM careertrellis.app_users u LEFT JOIN careertrellis_admission.identity_accounts a
    ON a.id=u.id WHERE a.id IS NULL) THEN RAISE EXCEPTION 'TOTP cleanup unavailable'; END IF;
END $$;
WITH candidates AS MATERIALIZED (
 SELECT account_id FROM careertrellis_admission.totp_attempts
 WHERE record->>'sealed_secret' IS NOT NULL
  AND (record->>'deadline')::bigint<=extract(epoch FROM statement_timestamp())
 ORDER BY account_id LIMIT 500 FOR UPDATE SKIP LOCKED
), cleared AS (
 UPDATE careertrellis_admission.totp_attempts a
 SET record=jsonb_set(a.record,'{sealed_secret}','null'::jsonb),updated_at=clock_timestamp()
 FROM candidates c WHERE a.account_id=c.account_id RETURNING a.account_id
)
SELECT json_build_object('scrubbed',(SELECT count(*) FROM cleared),'remaining',EXISTS(
 SELECT 1 FROM careertrellis_admission.totp_attempts a
 WHERE a.record->>'sealed_secret' IS NOT NULL
  AND (a.record->>'deadline')::bigint<=extract(epoch FROM statement_timestamp())
  AND a.account_id NOT IN (SELECT account_id FROM cleared)));
COMMIT;"""


def scrub(db, *, monotonic=time.monotonic):
    started, total = monotonic(), 0
    for _ in range(ROUNDS):
        if monotonic()-started>=24:
            break
        result = db.query(batch_sql())
        ops.require(isinstance(result,dict) and set(result)=={'scrubbed','remaining'}
            and type(result['scrubbed']) is int and 0<=result['scrubbed']<=BATCH
            and type(result['remaining']) is bool)
        total += result['scrubbed']
        if not result['remaining']:
            return {'result':'ok','scrubbed':total}
        # A locked remainder is backlog, never success from a short batch.
        if result['scrubbed']<BATCH:
            break
    return {'result':'backlog','scrubbed':total}
