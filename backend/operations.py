"""Cooperative cancellation for user-started long-running operations."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock
import time
from uuid import UUID


class OperationCancelled(Exception):
    """Raised at a safe checkpoint after the user requests cancellation."""


TAILOR_PROGRESS_MESSAGES = {
    "loading_inputs": "Loading reviewed inputs.",
    "generating_materials": "Generating the resume and cover letter.",
    "rendering_resume": "Validating and rendering the resume PDF.",
    "resolving_headquarters": "Resolving the employer headquarters.",
    "saving_materials": "Saving the tailored materials.",
    "completed": "Tailored materials are ready.",
    "completed_with_review": "Tailored materials are ready with a headquarters item to review.",
    "cancelled": "Tailoring stopped before previously saved materials changed.",
    "failed": "Tailoring could not be completed.",
}

_PROGRESS_MESSAGES = {"tailor": TAILOR_PROGRESS_MESSAGES}


def _progress_timestamp(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class OperationToken:
    """Thread-safe cancellation token shared by an endpoint and its worker."""

    operation_id: str | None = None
    owner_id: int = 0
    kind: str | None = None
    _cancelled: Event = field(default_factory=Event)
    _temporary_files: set[Path] = field(default_factory=set)
    _state_lock: Lock = field(default_factory=Lock)
    _started_epoch: float | None = None
    _progress_epoch: float | None = None
    _phase: str | None = None
    _completed: int | None = None
    _total: int | None = None

    @property
    def cancellation_requested(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> bool:
        """Request cancellation unless an atomic commit boundary has begun."""
        with self._state_lock:
            if self.kind == "tailor" and self._phase in {
                "saving_materials",
                "completed",
                "completed_with_review",
                "cancelled",
                "failed",
            }:
                return False
            self._cancelled.set()
            return True

    def checkpoint(self) -> None:
        if self.cancellation_requested:
            raise OperationCancelled("The operation was stopped by the user.")

    def track_temporary_file(self, path: str | Path) -> None:
        self._temporary_files.add(Path(path))

    def commit_temporary_file(self, path: str | Path) -> None:
        self._temporary_files.discard(Path(path))

    def cleanup_temporary_files(self) -> None:
        for path in self._temporary_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._temporary_files.clear()

    def update_progress(
        self,
        phase: str,
        *,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        """Advance a registered operation using only its fixed public schema."""
        messages = _PROGRESS_MESSAGES.get(self.kind or "")
        if messages is None or phase not in messages:
            raise ValueError("The operation progress phase is not allowlisted.")
        if (completed is None) != (total is None):
            raise ValueError("Progress counts must either both be present or both be absent.")
        if completed is not None:
            if (
                isinstance(completed, bool)
                or isinstance(total, bool)
                or completed < 0
                or total is None
                or total < 1
                or completed > total
                or total > 100
            ):
                raise ValueError("Operation progress counts are invalid.")
        now = time.time()
        with self._state_lock:
            if phase == "saving_materials" and self.cancellation_requested:
                raise OperationCancelled("The operation was stopped by the user.")
            if self._started_epoch is None:
                self._started_epoch = now
            if self._progress_epoch is not None:
                now = max(now, self._progress_epoch)
            self._progress_epoch = now
            self._phase = phase
            self._completed = completed
            self._total = total

    def progress_status(self) -> dict[str, object] | None:
        """Return the closed privacy-safe progress view, when configured."""
        with self._state_lock:
            if (
                self.kind not in _PROGRESS_MESSAGES
                or self._phase is None
                or self._started_epoch is None
                or self._progress_epoch is None
            ):
                return None
            return {
                "kind": self.kind,
                "phase": self._phase,
                "message": _PROGRESS_MESSAGES[self.kind][self._phase],
                "started_at": _progress_timestamp(self._started_epoch),
                "last_progress_at": _progress_timestamp(self._progress_epoch),
                "completed": self._completed,
                "total": self._total,
                "stop_requested": self.cancellation_requested,
            }


_operations: dict[tuple[int, str], OperationToken] = {}
_operations_lock = Lock()


def normalized_operation_id(value: object) -> str | None:
    """Accept only canonical UUIDs supplied by the local frontend."""
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


def start_operation(
    operation_id: object = None,
    *,
    owner_id: int = 0,
    kind: str | None = None,
) -> OperationToken:
    """Register a cancellable operation, or return a no-op token for legacy callers."""
    normalized = normalized_operation_id(operation_id)
    token = OperationToken(normalized, owner_id=owner_id, kind=kind)
    if normalized:
        with _operations_lock:
            _operations[(owner_id, normalized)] = token
    return token


def finish_operation(token: OperationToken) -> None:
    """Remove a token only when it is still the active instance for its ID."""
    token.cleanup_temporary_files()
    if not token.operation_id:
        return
    with _operations_lock:
        key = (token.owner_id, token.operation_id)
        if _operations.get(key) is token:
            _operations.pop(key, None)


def request_cancellation(operation_id: object, *, owner_id: int = 0) -> bool:
    """Request cancellation and report whether the operation is still active."""
    normalized = normalized_operation_id(operation_id)
    if not normalized:
        return False
    with _operations_lock:
        token = _operations.get((owner_id, normalized))
    if not token:
        return False
    return token.cancel()


def operation_progress(
    operation_id: object,
    *,
    owner_id: int = 0,
    kind: str | None = None,
) -> dict[str, object] | None:
    """Read one owner-bound progress view without revealing whether another owns it."""
    normalized = normalized_operation_id(operation_id)
    if not normalized:
        return None
    with _operations_lock:
        token = _operations.get((owner_id, normalized))
    if token is None or (kind is not None and token.kind != kind):
        return None
    return token.progress_status()


@contextmanager
def operation_scope(
    operation_id: object = None,
    *,
    owner_id: int = 0,
    kind: str | None = None,
):
    """Register and reliably retire a token around synchronous work."""
    token = start_operation(operation_id, owner_id=owner_id, kind=kind)
    try:
        yield token
    finally:
        finish_operation(token)
