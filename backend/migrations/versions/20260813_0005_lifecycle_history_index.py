"""Add the owner/job active lifecycle-history index.

Revision ID: 20260813_0005
Revises: 20260812_0004
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_0005"
down_revision: Union[str, Sequence[str], None] = "20260812_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
INDEX_NAME = "idx_application_status_history_owner_job_active"


def _drop_index_concurrently() -> None:
    op.execute(sa.text(f'DROP INDEX CONCURRENTLY IF EXISTS "{SCHEMA}"."{INDEX_NAME}"'))


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("SET lock_timeout = '5s'"))
        op.execute(sa.text("SET statement_timeout = '30min'"))
        _drop_index_concurrently()
        op.create_index(
            INDEX_NAME,
            "application_status_history",
            ["owner_user_id", "job_id", sa.text("id DESC")],
            schema=SCHEMA,
            postgresql_where=sa.text("undone_at IS NULL"),
            postgresql_concurrently=True,
        )
        op.execute(sa.text("RESET statement_timeout"))
        op.execute(sa.text("RESET lock_timeout"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("SET lock_timeout = '5s'"))
        _drop_index_concurrently()
        op.execute(sa.text("RESET lock_timeout"))
