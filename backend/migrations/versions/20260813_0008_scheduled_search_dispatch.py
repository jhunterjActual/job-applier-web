"""Add atomic, worker-only scheduled saved-search dispatch.

Revision ID: 20260813_0008
Revises: 20260813_0007
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_0008"
down_revision: Union[str, Sequence[str], None] = "20260813_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"


def upgrade() -> None:
    op.add_column(
        "search_runs",
        sa.Column("scheduled_search_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "search_runs",
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_search_runs_scheduled_identity",
        "search_runs",
        "(scheduled_search_id IS NULL) = (scheduled_for IS NULL)",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_search_runs_owner_scheduled_occurrence",
        "search_runs",
        ["owner_user_id", "scheduled_search_id", "scheduled_for"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("scheduled_search_id IS NOT NULL"),
    )

    # Forced RLS cannot discover an owner before tenant context is known. This
    # private broker therefore contains only opaque identity and due time. The
    # authoritative query text remains exclusively in saved_searches under RLS.
    op.create_table(
        "scheduled_search_dispatch_queue",
        sa.Column("saved_search_id", sa.BigInteger(), primary_key=True),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_scheduled_search_dispatch_due",
        "scheduled_search_dispatch_queue",
        ["due_at", "saved_search_id"],
        schema=SCHEMA,
    )

    # The migration role is intentionally NOBYPASSRLS. Temporarily relaxing
    # FORCE for this transaction lets the schema owner populate opaque broker
    # identities for existing rows; RLS remains enabled throughout and FORCE is
    # restored before the migration commits.
    op.execute(f"ALTER TABLE {SCHEMA}.saved_searches NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.sync_scheduled_search_dispatch_queue()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                DELETE FROM {SCHEMA}.scheduled_search_dispatch_queue
                WHERE saved_search_id = OLD.id;
                RETURN OLD;
            END IF;
            IF NEW.enabled IS TRUE
               AND NEW.schedule_frequency IN ('daily', 'weekly')
               AND NEW.next_alert_at IS NOT NULL THEN
                INSERT INTO {SCHEMA}.scheduled_search_dispatch_queue (
                    saved_search_id, owner_user_id, due_at
                ) VALUES (NEW.id, NEW.owner_user_id, NEW.next_alert_at)
                ON CONFLICT (saved_search_id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    due_at = EXCLUDED.due_at;
            ELSE
                DELETE FROM {SCHEMA}.scheduled_search_dispatch_queue
                WHERE saved_search_id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""CREATE TRIGGER scheduled_search_dispatch_queue_sync
        AFTER INSERT OR UPDATE OF enabled, schedule_frequency, next_alert_at,
            keywords, location OR DELETE
        ON {SCHEMA}.saved_searches
        FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.sync_scheduled_search_dispatch_queue()"""
    )
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.scheduled_search_dispatch_queue (
            saved_search_id, owner_user_id, due_at
        )
        SELECT id, owner_user_id, next_alert_at
        FROM {SCHEMA}.saved_searches
        WHERE enabled IS TRUE
          AND schedule_frequency IN ('daily', 'weekly')
          AND next_alert_at IS NOT NULL
        ON CONFLICT (saved_search_id) DO UPDATE SET
            owner_user_id = EXCLUDED.owner_user_id,
            due_at = EXCLUDED.due_at
        """
    )
    op.execute(f"ALTER TABLE {SCHEMA}.saved_searches FORCE ROW LEVEL SECURITY")

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.dispatch_due_saved_searches(p_limit integer)
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
                SELECT search.* INTO saved
                FROM {SCHEMA}.saved_searches AS search
                WHERE search.id = candidate.saved_search_id
                  AND search.owner_user_id = candidate.owner_user_id
                FOR UPDATE SKIP LOCKED;

                -- A concurrent delete/disable already owns the authoritative
                -- row. Leave its broker entry untouched; that transaction's
                -- trigger will remove or refresh it without a lock inversion.
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
                    -- Keep the authoritative schedule due, but move this
                    -- private identity behind other due owners for a bounded
                    -- period so a full batch of active owners cannot starve
                    -- later candidates.
                    UPDATE {SCHEMA}.scheduled_search_dispatch_queue AS queue
                    SET due_at = CURRENT_TIMESTAMP + INTERVAL '30 seconds'
                    WHERE queue.saved_search_id = candidate.saved_search_id;
                    CONTINUE;
                END IF;

                -- Legacy/imported rows can predate current request validation.
                -- Validate every database bound before insertion so one bad
                -- tenant row cannot abort the entire worker batch. A later edit
                -- to either request field fires the broker sync trigger and
                -- makes a repaired due row eligible immediately.
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
                    -- A manual/concurrent reservation may win after the active
                    -- check. Treat that race like an active-owner deferral so
                    -- it cannot remain at the front of every bounded batch.
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
        f"REVOKE ALL ON {SCHEMA}.scheduled_search_dispatch_queue "
        "FROM PUBLIC, careertrellis_runtime, careertrellis_worker"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {SCHEMA}.sync_scheduled_search_dispatch_queue() "
        "FROM PUBLIC, careertrellis_runtime, careertrellis_worker"
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
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.dispatch_due_saved_searches(integer)"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS scheduled_search_dispatch_queue_sync "
        f"ON {SCHEMA}.saved_searches"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.sync_scheduled_search_dispatch_queue()"
    )
    op.drop_table("scheduled_search_dispatch_queue", schema=SCHEMA)
    op.drop_index(
        "uq_search_runs_owner_scheduled_occurrence",
        table_name="search_runs",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_search_runs_scheduled_identity",
        "search_runs",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("search_runs", "scheduled_for", schema=SCHEMA)
    op.drop_column("search_runs", "scheduled_search_id", schema=SCHEMA)
