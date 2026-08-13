"""Constrain saved-search schedule frequencies.

Revision ID: 20260812_0004
Revises: 20260812_0003
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260812_0004"
down_revision: Union[str, Sequence[str], None] = "20260812_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
CONSTRAINT = "ck_saved_searches_schedule_frequency"


def upgrade() -> None:
    # NOT VALID avoids a full-table validation while holding the stronger
    # constraint-install lock. Validation then uses PostgreSQL's lower-impact
    # validation lock and fails closed if imported legacy data is invalid.
    op.execute(
        f"ALTER TABLE {SCHEMA}.saved_searches "
        f"ADD CONSTRAINT {CONSTRAINT} "
        "CHECK (schedule_frequency IN ('none', 'daily', 'weekly')) NOT VALID"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.saved_searches VALIDATE CONSTRAINT {CONSTRAINT}"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.saved_searches DROP CONSTRAINT IF EXISTS {CONSTRAINT}"
    )
