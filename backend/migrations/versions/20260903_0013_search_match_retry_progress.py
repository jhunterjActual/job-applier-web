"""Add durable AI match-retry progress.

Revision ID: 20260903_0013
Revises: 20260903_0012
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0013"
down_revision: Union[str, Sequence[str], None] = "20260903_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "careertrellis"
BASE_PHASES = (
    "preparing",
    "queued",
    "preparing_keywords",
    "searching_sources",
    "inspecting_postings",
    "analyzing_matches",
    "saving_matches",
)
PHASES = (*BASE_PHASES, "retrying_match_analysis")


def _phase_check(phases: tuple[str, ...]) -> str:
    return "progress_phase IN (" + ",".join(f"'{phase}'" for phase in phases) + ")"


def upgrade() -> None:
    op.drop_constraint(
        "ck_search_runs_progress_phase",
        "search_runs",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_search_runs_progress_phase",
        "search_runs",
        _phase_check(PHASES),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(
        f"""UPDATE {SCHEMA}.search_runs
        SET progress_phase='analyzing_matches'
        WHERE progress_phase='retrying_match_analysis'"""
    )
    op.drop_constraint(
        "ck_search_runs_progress_phase",
        "search_runs",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_search_runs_progress_phase",
        "search_runs",
        _phase_check(BASE_PHASES),
        schema=SCHEMA,
    )
