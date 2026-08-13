"""Add durable owner-scoped search runs and a worker-only claim broker.

Revision ID: 20260813_0007
Revises: 20260813_0006
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260813_0007"
down_revision: Union[str, Sequence[str], None] = "20260813_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"


def upgrade() -> None:
    op.create_table(
        "search_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("owner_user_id", sa.BigInteger(), sa.ForeignKey(f"{SCHEMA}.app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("keywords", sa.String(length=500), nullable=False),
        sa.Column("location", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.SmallInteger(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued','running','cancel_requested','succeeded','failed','cancelled')", name="ck_search_runs_status"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 10 AND attempts <= max_attempts", name="ck_search_runs_attempts"),
        sa.CheckConstraint("octet_length(keywords) <= 2000 AND octet_length(location) <= 960", name="ck_search_runs_request_bytes"),
        sa.CheckConstraint("result_json IS NULL OR octet_length(result_json::text) <= 16384", name="ck_search_runs_result_bytes"),
        sa.CheckConstraint("error_code IS NULL OR error_code IN ('search_failed','provider_search_failed','attempts_exhausted')", name="ck_search_runs_error_code"),
        sa.UniqueConstraint("owner_user_id", "operation_id", name="uq_search_runs_owner_operation"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_search_runs_id_owner"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_search_runs_owner_active",
        "search_runs",
        ["owner_user_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued','running','cancel_requested')"),
    )
    op.create_index(
        "idx_search_runs_owner_recent",
        "search_runs",
        ["owner_user_id", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.execute(f'ALTER TABLE "{SCHEMA}"."search_runs" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{SCHEMA}"."search_runs" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""CREATE POLICY tenant_isolation ON {SCHEMA}.search_runs
        USING (owner_user_id = {SCHEMA}.current_app_user_id())
        WITH CHECK (owner_user_id = {SCHEMA}.current_app_user_id())"""
    )

    # This non-RLS table contains only opaque run/owner IDs and scheduling time.
    # It is inaccessible to both login roles and exists solely so the definer
    # function can select an owner before binding forced-RLS tenant context.
    op.create_table(
        "search_run_claim_queue",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "owner_user_id"],
            [f"{SCHEMA}.search_runs.id", f"{SCHEMA}.search_runs.owner_user_id"],
            ondelete="CASCADE",
            name="fk_search_run_claim_queue_run_owner",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_search_run_claim_queue_available",
        "search_run_claim_queue",
        ["available_at", "run_id"],
        schema=SCHEMA,
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.sync_search_run_claim_queue()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        BEGIN
            IF NEW.status IN ('queued', 'running', 'cancel_requested') THEN
                INSERT INTO {SCHEMA}.search_run_claim_queue (run_id, owner_user_id, available_at)
                VALUES (
                    NEW.id,
                    NEW.owner_user_id,
                    CASE
                        WHEN NEW.status IN ('running', 'cancel_requested')
                            THEN COALESCE(NEW.lease_expires_at, CURRENT_TIMESTAMP)
                        ELSE NEW.available_at
                    END
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    owner_user_id = EXCLUDED.owner_user_id,
                    available_at = EXCLUDED.available_at;
            ELSE
                DELETE FROM {SCHEMA}.search_run_claim_queue WHERE run_id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""CREATE TRIGGER search_runs_claim_queue_sync
        AFTER INSERT OR UPDATE OF status, available_at, lease_expires_at
        ON {SCHEMA}.search_runs
        FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.sync_search_run_claim_queue()"""
    )

    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.claim_next_search_run(p_worker_id uuid, p_lease_seconds integer)
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
                RAISE EXCEPTION 'search worker claim is not authorized' USING ERRCODE = '42501';
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
                    DELETE FROM {SCHEMA}.search_run_claim_queue WHERE run_id = candidate.run_id;
                    CONTINUE;
                END IF;

                IF candidate_status = 'cancel_requested' THEN
                    UPDATE {SCHEMA}.search_runs AS r SET
                        status = 'cancelled', completed_at = CURRENT_TIMESTAMP,
                        lease_expires_at = NULL, lease_token = NULL, worker_id = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE r.id = candidate.run_id AND r.owner_user_id = candidate.owner_user_id;
                    CONTINUE;
                END IF;
                IF candidate_status NOT IN ('queued', 'running') THEN
                    DELETE FROM {SCHEMA}.search_run_claim_queue WHERE run_id = candidate.run_id;
                    CONTINUE;
                END IF;
                IF candidate_attempts >= candidate_max_attempts THEN
                    UPDATE {SCHEMA}.search_runs AS r SET
                        status = 'failed', error_code = 'attempts_exhausted',
                        completed_at = CURRENT_TIMESTAMP, lease_expires_at = NULL,
                        lease_token = NULL, worker_id = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE r.id = candidate.run_id AND r.owner_user_id = candidate.owner_user_id;
                    CONTINUE;
                END IF;

                RETURN QUERY
                UPDATE {SCHEMA}.search_runs AS r SET
                    status = 'running', attempts = r.attempts + 1,
                    worker_id = p_worker_id, lease_token = gen_random_uuid(),
                    lease_expires_at = CURRENT_TIMESTAMP + pg_catalog.make_interval(secs => p_lease_seconds),
                    started_at = COALESCE(r.started_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE r.id = candidate.run_id AND r.owner_user_id = candidate.owner_user_id
                RETURNING r.id, r.owner_user_id, r.operation_id, r.keywords,
                    r.location, r.lease_token, r.attempts;
                RETURN;
            END LOOP;
        END;
        $$
        """
    )

    op.execute(f"REVOKE ALL ON {SCHEMA}.search_run_claim_queue FROM PUBLIC, careertrellis_runtime, careertrellis_worker")
    op.execute(f"REVOKE ALL ON FUNCTION {SCHEMA}.sync_search_run_claim_queue() FROM PUBLIC, careertrellis_runtime, careertrellis_worker")
    op.execute(f"REVOKE ALL ON FUNCTION {SCHEMA}.claim_next_search_run(uuid, integer) FROM PUBLIC, careertrellis_runtime")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SCHEMA}.claim_next_search_run(uuid, integer) TO careertrellis_worker")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.claim_next_search_run(uuid, integer)")
    op.execute(f"DROP TRIGGER IF EXISTS search_runs_claim_queue_sync ON {SCHEMA}.search_runs")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.sync_search_run_claim_queue()")
    op.drop_table("search_run_claim_queue", schema=SCHEMA)
    op.drop_index("idx_search_runs_owner_recent", table_name="search_runs", schema=SCHEMA)
    op.drop_index("idx_search_runs_owner_active", table_name="search_runs", schema=SCHEMA)
    op.drop_table("search_runs", schema=SCHEMA)
