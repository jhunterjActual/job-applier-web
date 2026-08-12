"""Add owner-scoped base-resume soft deletion.

Revision ID: 20260812_0002
Revises: 20260811_0001
Create Date: 2026-08-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_0002"
down_revision: Union[str, Sequence[str], None] = "20260811_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
INDEX_NAME = "idx_base_resumes_owner_deleted_updated"


def upgrade() -> None:
    op.add_column(
        "base_resumes",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        INDEX_NAME,
        "base_resumes",
        ["owner_user_id", "deleted_at", sa.text("updated_at DESC")],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="base_resumes", schema=SCHEMA)
    op.drop_column("base_resumes", "deleted_at", schema=SCHEMA)
