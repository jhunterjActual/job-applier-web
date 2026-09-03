"""Add privacy-safe search progress checkpoints.

Revision ID: 20260902_0011
Revises: 20260824_0010
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260902_0011"
down_revision: Union[str, Sequence[str], None] = "20260824_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
PHASES = (
    "preparing",
    "queued",
    "preparing_keywords",
    "searching_sources",
    "inspecting_postings",
    "analyzing_matches",
    "saving_matches",
)


def upgrade() -> None:
    op.add_column(
        "search_runs",
        sa.Column("progress_phase", sa.String(32), nullable=False, server_default="preparing"),
        schema=SCHEMA,
    )
    op.add_column(
        "search_runs",
        sa.Column("progress_completed", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.add_column(
        "search_runs",
        sa.Column("progress_total", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column("search_runs", sa.Column("progress_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column("search_runs", sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column("search_runs", sa.Column("last_attempt_error_code", sa.String(64)), schema=SCHEMA)
    op.create_check_constraint(
        "ck_search_runs_progress_phase",
        "search_runs",
        "progress_phase IN (" + ",".join(f"'{phase}'" for phase in PHASES) + ")",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_search_runs_progress_counts",
        "search_runs",
        "progress_completed >= 0 AND (progress_total IS NULL OR "
        "(progress_total >= 0 AND progress_completed <= progress_total))",
        schema=SCHEMA,
    )
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.initialize_search_run_progress()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        BEGIN
            IF NEW.attempts > OLD.attempts THEN
                NEW.progress_phase = 'preparing_keywords';
                NEW.progress_completed = 0;
                NEW.progress_total = 1;
                NEW.progress_at = CURRENT_TIMESTAMP;
                NEW.lease_heartbeat_at = CURRENT_TIMESTAMP;
                NEW.last_attempt_error_code = NULL;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        f"""CREATE TRIGGER search_runs_progress_on_claim
        BEFORE UPDATE OF attempts ON {SCHEMA}.search_runs
        FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.initialize_search_run_progress()"""
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {SCHEMA}.initialize_search_run_progress() "
        "FROM PUBLIC, careertrellis_runtime, careertrellis_worker"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS search_runs_progress_on_claim ON {SCHEMA}.search_runs")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.initialize_search_run_progress()")
    op.drop_constraint("ck_search_runs_progress_counts", "search_runs", schema=SCHEMA)
    op.drop_constraint("ck_search_runs_progress_phase", "search_runs", schema=SCHEMA)
    for column in (
        "last_attempt_error_code",
        "lease_heartbeat_at",
        "progress_at",
        "progress_total",
        "progress_completed",
        "progress_phase",
    ):
        op.drop_column("search_runs", column, schema=SCHEMA)
