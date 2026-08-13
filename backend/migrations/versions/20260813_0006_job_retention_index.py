"""Add the owner-first job-retention candidate index.

Revision ID: 20260813_0006
Revises: 20260813_0005
Create Date: 2026-08-13
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_0006"
down_revision: Union[str, Sequence[str], None] = "20260813_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
INDEX_NAME = "idx_jobs_owner_retention_candidates"


def _drop_index_concurrently() -> None:
    op.execute(sa.text(f'DROP INDEX CONCURRENTLY IF EXISTS "{SCHEMA}"."{INDEX_NAME}"'))


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("SET lock_timeout = '5s'"))
        op.execute(sa.text("SET statement_timeout = '30min'"))
        _drop_index_concurrently()
        op.create_index(
            INDEX_NAME,
            "jobs",
            ["owner_user_id", "status", "id"],
            schema=SCHEMA,
            postgresql_include=["archived_from_status"],
            postgresql_where=sa.text("status IN ('matched', 'archived')"),
            postgresql_concurrently=True,
        )
        op.execute(sa.text("RESET statement_timeout"))
        op.execute(sa.text("RESET lock_timeout"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text("SET lock_timeout = '5s'"))
        _drop_index_concurrently()
        op.execute(sa.text("RESET lock_timeout"))
