"""Add owner-first discovery repository indexes.

Revision ID: 20260812_0003
Revises: 20260812_0002
Create Date: 2026-08-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_0003"
down_revision: Union[str, Sequence[str], None] = "20260812_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
INDEXES = (
    "idx_jobs_owner_score_active",
    "idx_saved_searches_owner_due",
    "idx_job_suppressions_owner_deleted",
)


def _drop_indexes_concurrently() -> None:
    for index_name in reversed(INDEXES):
        op.execute(
            sa.text(f'DROP INDEX CONCURRENTLY IF EXISTS "{SCHEMA}"."{index_name}"')
        )


def upgrade() -> None:
    # Concurrent builds avoid blocking writes if this additive migration is
    # later applied to a populated database.  Dropping first makes a retry after
    # an interrupted concurrent build recover from PostgreSQL's invalid-index
    # artifact without an operator guessing which object is safe to remove.
    with op.get_context().autocommit_block():
        op.execute(sa.text("SET lock_timeout = '5s'"))
        op.execute(sa.text("SET statement_timeout = '30min'"))
        _drop_indexes_concurrently()
        op.create_index(
            "idx_jobs_owner_score_active",
            "jobs",
            [
                "owner_user_id",
                sa.text("match_score DESC NULLS LAST"),
                sa.text("id DESC"),
            ],
            schema=SCHEMA,
            postgresql_where=sa.text("status <> 'archived'"),
            postgresql_concurrently=True,
        )
        op.create_index(
            "idx_saved_searches_owner_due",
            "saved_searches",
            ["owner_user_id", "enabled", "next_alert_at"],
            schema=SCHEMA,
            postgresql_concurrently=True,
        )
        op.create_index(
            "idx_job_suppressions_owner_deleted",
            "job_suppressions",
            ["owner_user_id", sa.text("deleted_at DESC"), sa.text("id DESC")],
            schema=SCHEMA,
            postgresql_concurrently=True,
        )
        op.execute(sa.text("RESET statement_timeout"))
        op.execute(sa.text("RESET lock_timeout"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("SET lock_timeout = '5s'"))
        _drop_indexes_concurrently()
        op.execute(sa.text("RESET lock_timeout"))
