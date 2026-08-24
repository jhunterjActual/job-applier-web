"""Contain disabled pilot search work.

Revision ID: 20260824_0010
Revises: 20260814_0009
"""

from alembic import op


revision: str = "20260824_0010"
down_revision: str | None = "20260814_0009"
branch_labels = None
depends_on = None

SCHEMA = "careertrellis"


def upgrade() -> None:
    op.drop_constraint(
        "ck_search_runs_error_code", "search_runs", schema=SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "ck_search_runs_error_code",
        "search_runs",
        "error_code IS NULL OR error_code IN ("
        "'search_failed','provider_search_failed','attempts_exhausted','owner_disabled')",
        schema=SCHEMA,
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.claim_next_search_run(
            p_worker_id uuid,
            p_lease_seconds integer
        )
        RETURNS TABLE (
            id uuid,
            owner_user_id bigint,
            operation_id uuid,
            keywords varchar(500),
            location varchar(240),
            lease_token uuid,
            attempts smallint
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        DECLARE
            candidate {SCHEMA}.search_run_claim_queue%ROWTYPE;
            candidate_status varchar(24);
            candidate_attempts smallint;
            candidate_max_attempts smallint;
        BEGIN
            IF SESSION_USER <> 'careertrellis_worker' THEN
                RAISE EXCEPTION 'search worker claim is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_worker_id IS NULL OR p_lease_seconds < 5 OR p_lease_seconds > 900 THEN
                RAISE EXCEPTION 'invalid search worker claim' USING ERRCODE = '22023';
            END IF;

            LOOP
                SELECT q.* INTO candidate
                FROM {SCHEMA}.search_run_claim_queue AS q
                WHERE q.available_at <= CURRENT_TIMESTAMP
                ORDER BY q.available_at, q.run_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1;
                IF NOT FOUND THEN
                    RETURN;
                END IF;

                PERFORM pg_catalog.set_config(
                    'careertrellis.user_id', candidate.owner_user_id::text, true
                );
                SELECT r.status, r.attempts, r.max_attempts
                    INTO candidate_status, candidate_attempts, candidate_max_attempts
                FROM {SCHEMA}.search_runs AS r
                WHERE r.id = candidate.run_id
                  AND r.owner_user_id = candidate.owner_user_id
                FOR UPDATE;
                IF NOT FOUND THEN
                    DELETE FROM {SCHEMA}.search_run_claim_queue
                    WHERE run_id = candidate.run_id;
                    CONTINUE;
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM {SCHEMA}.app_users AS app_user
                    WHERE app_user.id = candidate.owner_user_id
                      AND app_user.disabled_at IS NULL
                ) THEN
                    UPDATE {SCHEMA}.search_runs AS r SET
                        status = 'cancelled', error_code = 'owner_disabled',
                        completed_at = CURRENT_TIMESTAMP,
                        lease_expires_at = NULL, lease_token = NULL,
                        worker_id = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE r.id = candidate.run_id
                      AND r.owner_user_id = candidate.owner_user_id;
                    CONTINUE;
                END IF;

                IF candidate_status = 'cancel_requested' THEN
                    UPDATE {SCHEMA}.search_runs AS r SET
                        status = 'cancelled', completed_at = CURRENT_TIMESTAMP,
                        lease_expires_at = NULL, lease_token = NULL,
                        worker_id = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE r.id = candidate.run_id
                      AND r.owner_user_id = candidate.owner_user_id;
                    CONTINUE;
                END IF;
                IF candidate_status NOT IN ('queued', 'running') THEN
                    DELETE FROM {SCHEMA}.search_run_claim_queue
                    WHERE run_id = candidate.run_id;
                    CONTINUE;
                END IF;
                IF candidate_attempts >= candidate_max_attempts THEN
                    UPDATE {SCHEMA}.search_runs AS r SET
                        status = 'failed', error_code = 'attempts_exhausted',
                        completed_at = CURRENT_TIMESTAMP,
                        lease_expires_at = NULL, lease_token = NULL,
                        worker_id = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE r.id = candidate.run_id
                      AND r.owner_user_id = candidate.owner_user_id;
                    CONTINUE;
                END IF;

                RETURN QUERY
                UPDATE {SCHEMA}.search_runs AS r SET
                    status = 'running', attempts = r.attempts + 1,
                    worker_id = p_worker_id, lease_token = gen_random_uuid(),
                    lease_expires_at = CURRENT_TIMESTAMP
                        + pg_catalog.make_interval(secs => p_lease_seconds),
                    started_at = COALESCE(r.started_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE r.id = candidate.run_id
                  AND r.owner_user_id = candidate.owner_user_id
                RETURNING r.id, r.owner_user_id, r.operation_id, r.keywords,
                    r.location, r.lease_token, r.attempts;
                RETURN;
            END LOOP;
        END;
        $$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {SCHEMA}.claim_next_search_run(uuid, integer) "
        "FROM PUBLIC, careertrellis_runtime"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {SCHEMA}.claim_next_search_run(uuid, integer) "
        "TO careertrellis_worker"
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.dispatch_due_saved_searches(
            p_limit integer
        )
        RETURNS TABLE (
            run_id uuid,
            owner_user_id bigint,
            saved_search_id bigint,
            scheduled_for timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        DECLARE
            candidate {SCHEMA}.scheduled_search_dispatch_queue%ROWTYPE;
            saved {SCHEMA}.saved_searches%ROWTYPE;
            created_run_id uuid;
            occurrence_operation_id uuid;
        BEGIN
            IF SESSION_USER <> 'careertrellis_worker' THEN
                RAISE EXCEPTION 'scheduled search dispatch is not authorized'
                    USING ERRCODE = '42501';
            END IF;
            IF p_limit IS NULL OR p_limit < 1 OR p_limit > 100 THEN
                RAISE EXCEPTION 'invalid scheduled search dispatch limit'
                    USING ERRCODE = '22023';
            END IF;

            FOR candidate IN
                SELECT queue.*
                FROM {SCHEMA}.scheduled_search_dispatch_queue AS queue
                WHERE queue.due_at <= CURRENT_TIMESTAMP
                ORDER BY queue.due_at, queue.saved_search_id
                FOR UPDATE SKIP LOCKED
                LIMIT p_limit
            LOOP
                PERFORM pg_catalog.set_config(
                    'careertrellis.user_id', candidate.owner_user_id::text, true
                );

                IF NOT EXISTS (
                    SELECT 1
                    FROM {SCHEMA}.app_users AS app_user
                    WHERE app_user.id = candidate.owner_user_id
                      AND app_user.disabled_at IS NULL
                ) THEN
                    -- Keep the authoritative schedule unchanged so explicit
                    -- operator reactivation can make it eligible again.
                    UPDATE {SCHEMA}.scheduled_search_dispatch_queue AS queue
                    SET due_at = CURRENT_TIMESTAMP + INTERVAL '5 minutes'
                    WHERE queue.saved_search_id = candidate.saved_search_id;
                    CONTINUE;
                END IF;

                SELECT search.* INTO saved
                FROM {SCHEMA}.saved_searches AS search
                WHERE search.id = candidate.saved_search_id
                  AND search.owner_user_id = candidate.owner_user_id
                FOR UPDATE SKIP LOCKED;
                IF NOT FOUND THEN
                    CONTINUE;
                END IF;
                IF saved.enabled IS NOT TRUE
                   OR saved.schedule_frequency NOT IN ('daily', 'weekly')
                   OR saved.next_alert_at IS NULL THEN
                    DELETE FROM {SCHEMA}.scheduled_search_dispatch_queue AS queue
                    WHERE queue.saved_search_id = candidate.saved_search_id;
                    CONTINUE;
                END IF;
                IF saved.next_alert_at > CURRENT_TIMESTAMP THEN
                    UPDATE {SCHEMA}.scheduled_search_dispatch_queue AS queue
                    SET due_at = saved.next_alert_at
                    WHERE queue.saved_search_id = candidate.saved_search_id;
                    CONTINUE;
                END IF;

                IF EXISTS (
                    SELECT 1 FROM {SCHEMA}.search_runs AS active
                    WHERE active.owner_user_id = candidate.owner_user_id
                      AND active.status IN (
                          'preparing', 'queued', 'running', 'cancel_requested'
                      )
                ) THEN
                    UPDATE {SCHEMA}.scheduled_search_dispatch_queue AS queue
                    SET due_at = CURRENT_TIMESTAMP + INTERVAL '30 seconds'
                    WHERE queue.saved_search_id = candidate.saved_search_id;
                    CONTINUE;
                END IF;

                IF pg_catalog.char_length(saved.keywords) > 500
                   OR pg_catalog.char_length(saved.location) > 240
                   OR pg_catalog.octet_length(saved.keywords) > 2000
                   OR pg_catalog.octet_length(saved.location) > 960 THEN
                    UPDATE {SCHEMA}.scheduled_search_dispatch_queue AS queue
                    SET due_at = CURRENT_TIMESTAMP + INTERVAL '5 minutes'
                    WHERE queue.saved_search_id = candidate.saved_search_id;
                    CONTINUE;
                END IF;

                occurrence_operation_id := pg_catalog.md5(
                    'careertrellis-scheduled-search-v1:'
                    || candidate.owner_user_id::text || ':'
                    || candidate.saved_search_id::text || ':'
                    || pg_catalog.to_char(
                        saved.next_alert_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US'
                    )
                )::uuid;

                BEGIN
                    DELETE FROM {SCHEMA}.search_runs AS prior
                    WHERE prior.owner_user_id = candidate.owner_user_id
                      AND prior.status IN ('succeeded', 'failed', 'cancelled');

                    INSERT INTO {SCHEMA}.search_runs (
                        owner_user_id, operation_id, keywords, location, status,
                        scheduled_search_id, scheduled_for
                    ) VALUES (
                        candidate.owner_user_id, occurrence_operation_id,
                        saved.keywords, saved.location, 'queued',
                        candidate.saved_search_id, saved.next_alert_at
                    )
                    RETURNING id INTO created_run_id;
                EXCEPTION WHEN unique_violation THEN
                    UPDATE {SCHEMA}.scheduled_search_dispatch_queue AS queue
                    SET due_at = CURRENT_TIMESTAMP + INTERVAL '30 seconds'
                    WHERE queue.saved_search_id = candidate.saved_search_id;
                    CONTINUE;
                END;

                UPDATE {SCHEMA}.saved_searches AS search SET
                    last_run_at = CURRENT_TIMESTAMP,
                    next_alert_at = CASE saved.schedule_frequency
                        WHEN 'daily' THEN saved.next_alert_at + INTERVAL '1 day'
                        WHEN 'weekly' THEN saved.next_alert_at + INTERVAL '7 days'
                    END
                WHERE search.id = candidate.saved_search_id
                  AND search.owner_user_id = candidate.owner_user_id;

                run_id := created_run_id;
                owner_user_id := candidate.owner_user_id;
                saved_search_id := candidate.saved_search_id;
                scheduled_for := saved.next_alert_at;
                RETURN NEXT;
            END LOOP;
        END;
        $$
        """
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {SCHEMA}.dispatch_due_saved_searches(integer) "
        "FROM PUBLIC, careertrellis_runtime"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {SCHEMA}.dispatch_due_saved_searches(integer) "
        "TO careertrellis_worker"
    )


def downgrade() -> None:
    # This security boundary is monotonic. Application rollback remains
    # compatible with the unchanged function signatures, while a later
    # downgrade that removes the search subsystem will drop these functions.
    # Do not silently re-enable provider work for a disabled pilot.
    return None
