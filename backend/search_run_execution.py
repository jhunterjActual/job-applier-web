"""Execute one claimed search without holding queue transactions over network calls."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import Event, Lock, Thread

from database import get_db_connection
from discovery_repository import runtime_discovery_repository
from operations import OperationCancelled
from provider_secrets import owner_user_scope
from search_run_repository import (
    DEFAULT_LEASE_SECONDS,
    SearchRunClaim,
    SearchRunLeaseLost,
    SearchRunRepository,
)
from searcher import run_job_search_and_matching


class SearchWorkerStopping(Exception):
    """Leave the current lease retryable when worker infrastructure stops."""


LEASE_HEARTBEAT_SECONDS = min(60.0, DEFAULT_LEASE_SECONDS / 3)


class _LeaseHeartbeat:
    """Renew one claim while bounded provider calls run outside transactions."""

    def __init__(
        self,
        claim: SearchRunClaim,
        repository: SearchRunRepository,
        stop_requested: Callable[[], bool] | None,
    ) -> None:
        self._claim = claim
        self._repository = repository
        self._stop_requested = stop_requested
        self._stop = Event()
        self._repository_lock = Lock()
        self._state_lock = Lock()
        self._failure: BaseException | None = None
        self._thread = Thread(
            target=self._run,
            name="search-run-lease-heartbeat",
            daemon=True,
        )

    def _record_failure(self, failure: BaseException) -> None:
        with self._state_lock:
            if self._failure is None:
                self._failure = failure

    def _raise_recorded_failure(self) -> None:
        with self._state_lock:
            failure = self._failure
        if failure is not None:
            raise failure

    def checkpoint(self) -> None:
        self._raise_recorded_failure()
        if self._stop_requested is not None and self._stop_requested():
            failure = SearchWorkerStopping()
            self._record_failure(failure)
            raise failure
        try:
            with self._repository_lock:
                self._repository.heartbeat(self._claim)
        except Exception as exc:
            self._record_failure(exc)
            raise

    def _run(self) -> None:
        while not self._stop.wait(LEASE_HEARTBEAT_SECONDS):
            try:
                self.checkpoint()
            except Exception:
                return

    def __enter__(self) -> "_LeaseHeartbeat":
        self.checkpoint()
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()


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
    lease = _LeaseHeartbeat(claim, repository, stop_requested)
    try:
        with owner_user_scope(claim.owner_user_id):
            with lease:
                result = run_job_search_and_matching(
                    claim.keywords,
                    claim.location,
                    lease.checkpoint,
                )
                lease.checkpoint()
                _save_source_diagnostic_history(result)
                lease.checkpoint()
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
