"""Execute one claimed search without holding queue transactions over network calls."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from database import get_db_connection
from discovery_repository import runtime_discovery_repository
from operations import OperationCancelled
from provider_secrets import owner_user_scope
from search_run_repository import (
    SearchRunClaim,
    SearchRunLeaseLost,
    SearchRunRepository,
)
from searcher import run_job_search_and_matching


class SearchWorkerStopping(Exception):
    """Leave the current lease retryable when worker infrastructure stops."""


def _save_source_diagnostic_history(search_result: dict) -> None:
    """Best-effort minimized diagnostics must never alter a search outcome."""
    if not search_result.get("provider_alerts"):
        return
    try:
        runtime_discovery_repository(
            sqlite_connection_factory=get_db_connection
        ).persist_source_diagnostics(search_result, datetime.now())
    except Exception:
        print("Source diagnostic history could not be saved; search results remain available.")


def execute_search_claim(
    claim: SearchRunClaim,
    repository: SearchRunRepository,
    *,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    """Run provider work under the claimed owner and persist only a safe summary."""

    def checkpoint() -> None:
        if stop_requested is not None and stop_requested():
            raise SearchWorkerStopping()
        repository.heartbeat(claim)

    try:
        with owner_user_scope(claim.owner_user_id):
            checkpoint()
            result = run_job_search_and_matching(
                claim.keywords,
                claim.location,
                checkpoint,
            )
            _save_source_diagnostic_history(result)
            checkpoint()
        if result.get("success"):
            repository.succeed(claim, result)
        else:
            repository.fail(claim, "provider_search_failed")
    except SearchWorkerStopping:
        # SIGTERM/deploy shutdown is not user intent. Do not complete, fail, or
        # cancel the run; the opaque lease expires and a worker can reclaim it.
        return
    except OperationCancelled:
        try:
            repository.cancel(claim)
        except Exception:
            pass
    except SearchRunLeaseLost:
        # Another worker reclaimed an expired lease. This process must not
        # overwrite the newer attempt's outcome.
        return
    except Exception:
        try:
            repository.fail(claim, "search_failed")
        except Exception:
            pass
