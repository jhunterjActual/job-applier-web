-- Ordinary worker RLS remains unchanged. Only this NOLOGIN definer can
-- terminalize a disabled account's search without reading its payload.
SET LOCAL ROLE careertrellis_schema_owner;
GRANT SELECT(id,owner_user_id,status,lease_token,lease_expires_at)
 ON careertrellis.search_runs TO careertrellis_admission_owner;
GRANT UPDATE(status,result_json,error_code,completed_at,lease_expires_at,lease_token,worker_id,updated_at)
 ON careertrellis.search_runs TO careertrellis_admission_owner;
GRANT SELECT ON careertrellis.search_run_claim_queue TO careertrellis_admission_owner;
-- PostgreSQL requires UPDATE privilege for SELECT FOR UPDATE of queue rows.
GRANT UPDATE(run_id) ON careertrellis.search_run_claim_queue TO careertrellis_admission_owner;
CREATE POLICY disabled_search_cleanup_read ON careertrellis.search_runs FOR SELECT
 TO careertrellis_admission_owner USING (
  EXISTS(SELECT 1 FROM careertrellis_admission.identity_accounts a
         WHERE a.id=owner_user_id AND a.disabled));
CREATE POLICY disabled_search_cleanup_update ON careertrellis.search_runs FOR UPDATE
 TO careertrellis_admission_owner USING (
  EXISTS(SELECT 1 FROM careertrellis_admission.identity_accounts a
         WHERE a.id=owner_user_id AND a.disabled))
 WITH CHECK (status='cancelled' AND error_code='owner_disabled' AND result_json IS NULL
             AND lease_expires_at IS NULL AND lease_token IS NULL AND worker_id IS NULL);

SET LOCAL ROLE careertrellis_admission_owner;
CREATE FUNCTION careertrellis_admission.cancel_disabled_search_run(
 p_run uuid,p_owner bigint,p_lease uuid,p_due_only boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE q careertrellis.search_run_claim_queue%ROWTYPE;
        run_status varchar(24); run_lease uuid; run_deadline timestamptz;
BEGIN
 IF session_user<>'careertrellis_worker' THEN
  RAISE EXCEPTION 'disabled search cleanup caller denied' USING ERRCODE='42501'; END IF;
 IF p_run IS NULL OR p_owner IS NULL OR p_owner<=0 OR p_due_only IS NULL
    OR (p_due_only AND p_lease IS NOT NULL) OR (NOT p_due_only AND p_lease IS NULL)
    OR current_setting('careertrellis.user_id',true) IS DISTINCT FROM p_owner::text THEN
  RAISE EXCEPTION 'disabled search cleanup context invalid' USING ERRCODE='22023'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'disabled search cleanup isolation unsupported'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('careertrellis-admission-lifecycle-v1',0));
 -- Match the claim broker's order: lifecycle, queue, then run. Completion
 -- acquires the queue even for active owners before the repository's
 -- ordinary run-lock path, avoiding a queue/run lock-order inversion.
 SELECT * INTO q FROM careertrellis.search_run_claim_queue
  WHERE run_id=p_run AND owner_user_id=p_owner FOR UPDATE;
 IF NOT FOUND OR (p_due_only AND q.available_at>clock_timestamp()) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_accounts
               WHERE id=p_owner AND disabled) THEN RETURN false; END IF;
 SELECT status,lease_token,lease_expires_at INTO run_status,run_lease,run_deadline
  FROM careertrellis.search_runs WHERE id=p_run AND owner_user_id=p_owner FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 IF p_due_only THEN
  IF run_status NOT IN ('queued','running','cancel_requested')
     OR (run_status IN ('running','cancel_requested')
         AND (run_deadline IS NULL OR run_deadline>clock_timestamp())) THEN RETURN false; END IF;
 ELSE
  IF run_status NOT IN ('running','cancel_requested') OR run_lease IS DISTINCT FROM p_lease
     OR run_deadline IS NULL OR run_deadline<=clock_timestamp() THEN RETURN false; END IF;
 END IF;
 UPDATE careertrellis.search_runs SET status='cancelled',error_code='owner_disabled',
  result_json=NULL,completed_at=clock_timestamp(),lease_expires_at=NULL,lease_token=NULL,
  worker_id=NULL,updated_at=clock_timestamp() WHERE id=p_run AND owner_user_id=p_owner;
 -- Existing queue synchronization removes the terminal row atomically.
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION careertrellis_admission.cancel_disabled_search_run(uuid,bigint,uuid,boolean)
 FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis_admission.cancel_disabled_search_run(uuid,bigint,uuid,boolean)
 TO careertrellis_schema_owner;

SET LOCAL ROLE careertrellis_schema_owner;
CREATE FUNCTION careertrellis.cancel_disabled_search_run(p_run uuid,p_owner bigint,p_lease uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF session_user<>'careertrellis_worker' THEN
  RAISE EXCEPTION 'disabled search cleanup caller denied' USING ERRCODE='42501'; END IF;
 RETURN careertrellis_admission.cancel_disabled_search_run(p_run,p_owner,p_lease,false);
END $$;
REVOKE ALL ON FUNCTION careertrellis.cancel_disabled_search_run(uuid,bigint,uuid)
 FROM PUBLIC,careertrellis_runtime,careertrellis_worker,careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis.cancel_disabled_search_run(uuid,bigint,uuid)
 TO careertrellis_worker;

CREATE OR REPLACE FUNCTION careertrellis.claim_next_search_run(p_worker_id uuid,p_lease_seconds integer)
RETURNS TABLE(id uuid,owner_user_id bigint,operation_id uuid,keywords varchar(500),
 location varchar(240),lease_token uuid,attempts smallint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,careertrellis AS $$
DECLARE candidate careertrellis.search_run_claim_queue%ROWTYPE;
        candidate_status varchar(24); candidate_attempts smallint; candidate_max_attempts smallint;
BEGIN
 IF session_user<>'careertrellis_worker' THEN
  RAISE EXCEPTION 'search worker claim is not authorized' USING ERRCODE='42501'; END IF;
 IF p_worker_id IS NULL OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 5 AND 900 THEN
  RAISE EXCEPTION 'invalid search worker claim' USING ERRCODE='22023'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'search worker claim isolation unsupported'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('careertrellis-admission-lifecycle-v1',0));
 LOOP
  SELECT q.* INTO candidate FROM careertrellis.search_run_claim_queue q
   WHERE q.available_at<=CURRENT_TIMESTAMP ORDER BY q.available_at,q.run_id
   FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN; END IF;
  PERFORM set_config('careertrellis.user_id',candidate.owner_user_id::text,true);
  IF careertrellis_admission.cancel_disabled_search_run(
       candidate.run_id,candidate.owner_user_id,NULL,true) THEN CONTINUE; END IF;
  SELECT r.status,r.attempts,r.max_attempts INTO candidate_status,candidate_attempts,candidate_max_attempts
   FROM careertrellis.search_runs r WHERE r.id=candidate.run_id
    AND r.owner_user_id=candidate.owner_user_id FOR UPDATE;
  IF NOT FOUND THEN
   -- Held/erasing identities are deliberately invisible to ordinary worker
   -- RLS. Their run still exists (the queue has a composite foreign key).
   -- Preserve recoverable work and move it beyond this transaction's scan
   -- cutoff rather than deleting its only queue entry or spinning on it.
   UPDATE careertrellis.search_run_claim_queue AS hidden_queue
    SET available_at=clock_timestamp()+interval '5 minutes'
    WHERE hidden_queue.run_id=candidate.run_id
      AND hidden_queue.owner_user_id=candidate.owner_user_id;
   CONTINUE;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM careertrellis.app_users u
                WHERE u.id=candidate.owner_user_id AND u.disabled_at IS NULL) THEN
   UPDATE careertrellis.search_runs r SET status='cancelled',error_code='owner_disabled',
    result_json=NULL,completed_at=CURRENT_TIMESTAMP,lease_expires_at=NULL,lease_token=NULL,
    worker_id=NULL,updated_at=CURRENT_TIMESTAMP
    WHERE r.id=candidate.run_id AND r.owner_user_id=candidate.owner_user_id;
   CONTINUE;
  END IF;
  IF candidate_status='cancel_requested' THEN
   UPDATE careertrellis.search_runs r SET status='cancelled',completed_at=CURRENT_TIMESTAMP,
    lease_expires_at=NULL,lease_token=NULL,worker_id=NULL,updated_at=CURRENT_TIMESTAMP
    WHERE r.id=candidate.run_id AND r.owner_user_id=candidate.owner_user_id;
   CONTINUE;
  END IF;
  IF candidate_status NOT IN ('queued','running') THEN
   DELETE FROM careertrellis.search_run_claim_queue WHERE run_id=candidate.run_id;
   CONTINUE;
  END IF;
  IF candidate_attempts>=candidate_max_attempts THEN
   UPDATE careertrellis.search_runs r SET status='failed',error_code='attempts_exhausted',
    completed_at=CURRENT_TIMESTAMP,lease_expires_at=NULL,lease_token=NULL,worker_id=NULL,
    updated_at=CURRENT_TIMESTAMP WHERE r.id=candidate.run_id AND r.owner_user_id=candidate.owner_user_id;
   CONTINUE;
  END IF;
  RETURN QUERY UPDATE careertrellis.search_runs r SET status='running',attempts=r.attempts+1,
   worker_id=p_worker_id,lease_token=gen_random_uuid(),
   lease_expires_at=CURRENT_TIMESTAMP+make_interval(secs=>p_lease_seconds),
   started_at=COALESCE(r.started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP
   WHERE r.id=candidate.run_id AND r.owner_user_id=candidate.owner_user_id
   RETURNING r.id,r.owner_user_id,r.operation_id,r.keywords,r.location,r.lease_token,r.attempts;
  RETURN;
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION careertrellis.claim_next_search_run(uuid,integer)
 FROM PUBLIC,careertrellis_runtime,careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis.claim_next_search_run(uuid,integer) TO careertrellis_worker;

-- Heartbeat/progress/attempt paths use the same queue-before-run order as
-- claim and completion. Preserve their original forced-RLS claim checks.
CREATE OR REPLACE FUNCTION careertrellis.lock_search_run_claim(
 p_run_id uuid,p_owner_user_id bigint,p_lease_token uuid)
RETURNS TABLE(run_status varchar(24),owner_disabled boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,careertrellis AS $$
BEGIN
 IF session_user<>'careertrellis_worker' THEN
  RAISE EXCEPTION 'search worker claim lock is not authorized' USING ERRCODE='42501'; END IF;
 IF p_run_id IS NULL OR p_owner_user_id IS NULL OR p_owner_user_id<=0 OR p_lease_token IS NULL THEN
  RAISE EXCEPTION 'invalid search worker claim lock' USING ERRCODE='22023'; END IF;
 IF current_setting('careertrellis.user_id',true) IS DISTINCT FROM p_owner_user_id::text THEN
  RAISE EXCEPTION 'search worker tenant context is invalid' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN
  RAISE EXCEPTION 'search worker claim isolation unsupported'; END IF;
 PERFORM pg_advisory_xact_lock_shared(hashtextextended('careertrellis-admission-lifecycle-v1',0));
 PERFORM 1 FROM careertrellis.search_run_claim_queue q
  WHERE q.run_id=p_run_id AND q.owner_user_id=p_owner_user_id FOR UPDATE;
 IF NOT FOUND THEN RETURN; END IF;
 -- Do not share-lock app_users here: a pending disable already owns that
 -- row before identity_sync requests the exclusive lifecycle lock. Taking
 -- its row lock after our shared lifecycle lock would invert that order.
 -- MVCC reads the committed owner state; the shared lifecycle fence prevents
 -- disable from committing until this transaction finishes, so completion
 -- linearizes before that pending disable without relaxing run locking.
 RETURN QUERY SELECT r.status,u.disabled_at IS NOT NULL
  FROM careertrellis.search_runs r JOIN careertrellis.app_users u ON u.id=r.owner_user_id
  WHERE r.id=p_run_id AND r.owner_user_id=p_owner_user_id AND r.lease_token=p_lease_token
   AND r.lease_expires_at>CURRENT_TIMESTAMP
  FOR UPDATE OF r;
END $$;
REVOKE ALL ON FUNCTION careertrellis.lock_search_run_claim(uuid,bigint,uuid)
 FROM PUBLIC,careertrellis_runtime,careertrellis_erasure_worker,careertrellis_admission_broker;
GRANT EXECUTE ON FUNCTION careertrellis.lock_search_run_claim(uuid,bigint,uuid) TO careertrellis_worker;
