"""Worker-only bridge to atomic PostgreSQL scheduled-search dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from postgres_database import runtime_postgres_engine


MIN_DISPATCH_LIMIT = 1
MAX_DISPATCH_LIMIT = 100
DEFAULT_DISPATCH_LIMIT = 10


class ScheduledSearchDispatchUnavailable(RuntimeError):
    """Fixed persistence error that never exposes connection details."""

    def __init__(self):
        super().__init__("Scheduled search dispatch is temporarily unavailable.")


@dataclass(frozen=True)
class ScheduledSearchDispatch:
    run_id: str
    owner_user_id: int
    saved_search_id: int
    scheduled_for: datetime


def dispatch_due_postgres_saved_searches(
    *,
    engine: Engine | None = None,
    limit: int = DEFAULT_DISPATCH_LIMIT,
) -> list[ScheduledSearchDispatch]:
    """Atomically publish a bounded set of due searches through the private broker."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Scheduled search dispatch limit must be an integer.")
    if limit < MIN_DISPATCH_LIMIT or limit > MAX_DISPATCH_LIMIT:
        raise ValueError(
            f"Scheduled search dispatch limit must be {MIN_DISPATCH_LIMIT} through "
            f"{MAX_DISPATCH_LIMIT}."
        )
    selected = engine or runtime_postgres_engine()
    try:
        with selected.begin() as connection:
            rows = connection.execute(
                text(
                    "SELECT * FROM careertrellis.dispatch_due_saved_searches(:limit)"
                ),
                {"limit": limit},
            ).mappings().all()
        return [
            ScheduledSearchDispatch(
                run_id=str(row["run_id"]),
                owner_user_id=int(row["owner_user_id"]),
                saved_search_id=int(row["saved_search_id"]),
                scheduled_for=row["scheduled_for"],
            )
            for row in rows
        ]
    except SQLAlchemyError as exc:
        raise ScheduledSearchDispatchUnavailable() from exc
