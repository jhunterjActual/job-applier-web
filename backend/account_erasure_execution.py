"""Delete-only execution for one durable account-erasure claim."""

from __future__ import annotations

from collections.abc import Callable
import os
from typing import Literal, Protocol

import config
from account_erasure_repository import (
    AccountErasureClaim,
    AccountErasureLeaseLost,
    AccountErasureUnavailable,
    DEFAULT_ACCOUNT_ERASURE_LEASE_SECONDS,
    PostgreSQLAccountErasureWorkerRepository,
)
from object_store import (
    OBJECT_STORE_ENVIRONMENT_VARIABLE,
    R2ObjectBackend,
    SHARED_OBJECT_STORE,
    r2_configuration_from_environment,
)


class DeleteOnlyObjectBackend(Protocol):
    """The worker needs deletion only; encryption keys are never loaded."""

    def delete(self, object_key: str) -> None: ...


AccountErasureExecutionResult = Literal[
    "completed", "continued", "retry_scheduled", "lease_lost", "stopped"
]
MAX_ACCOUNT_ERASURE_OBJECTS_PER_CLAIM = 100_000


def runtime_account_erasure_delete_backend() -> DeleteOnlyObjectBackend:
    """Build R2 deletion access without parsing the encryption keyring."""
    if config.get_runtime_environment() == "local":
        raise AccountErasureUnavailable()
    if (
        os.environ.get(OBJECT_STORE_ENVIRONMENT_VARIABLE, "").strip().lower()
        != SHARED_OBJECT_STORE
    ):
        raise AccountErasureUnavailable()
    try:
        return R2ObjectBackend(r2_configuration_from_environment())
    except Exception as exc:
        raise AccountErasureUnavailable() from exc


def _retry_without_leaking(
    repository: PostgreSQLAccountErasureWorkerRepository,
    claim: AccountErasureClaim,
    error_code: str,
) -> AccountErasureExecutionResult:
    try:
        repository.retry(claim, error_code)
        return "retry_scheduled"
    except Exception:
        # An expired lease or database outage is recovered by the broker's
        # lease expiry. Never log the claim or the underlying exception.
        return "lease_lost"


def execute_account_erasure_claim(
    claim: AccountErasureClaim,
    repository: PostgreSQLAccountErasureWorkerRepository,
    *,
    backend: DeleteOnlyObjectBackend | None = None,
    batch_size: int = 10,
    lease_seconds: int = DEFAULT_ACCOUNT_ERASURE_LEASE_SECONDS,
    stop_requested: Callable[[], bool] | None = None,
) -> AccountErasureExecutionResult:
    """Delete one bounded blob batch outside DB transactions, then finalize."""
    try:
        selected_backend = backend or runtime_account_erasure_delete_backend()
        repository.renew(claim, lease_seconds=lease_seconds)
        targets = repository.object_batch(claim, limit=batch_size)
    except AccountErasureLeaseLost:
        return "lease_lost"
    except Exception:
        return _retry_without_leaking(repository, claim, "persistence_unavailable")

    processed = 0
    while True:
        for target in targets:
            if stop_requested is not None and stop_requested():
                return "stopped"
            try:
                repository.renew(claim, lease_seconds=lease_seconds)
                # No database transaction remains open over this R2 call.
                selected_backend.delete(target.object_key)
                repository.confirm_object_deleted(claim, target)
            except AccountErasureLeaseLost:
                return "lease_lost"
            except Exception:
                return _retry_without_leaking(
                    repository, claim, "object_delete_failed"
                )
        processed += len(targets)
        if len(targets) < batch_size:
            break
        if processed >= MAX_ACCOUNT_ERASURE_OBJECTS_PER_CLAIM:
            return _retry_without_leaking(
                repository, claim, "persistence_unavailable"
            )
        try:
            repository.renew(claim, lease_seconds=lease_seconds)
            targets = repository.object_batch(claim, limit=batch_size)
        except AccountErasureLeaseLost:
            return "lease_lost"
        except Exception:
            return _retry_without_leaking(
                repository, claim, "persistence_unavailable"
            )

    if stop_requested is not None and stop_requested():
        return "stopped"
    try:
        repository.renew(claim, lease_seconds=lease_seconds)
        return "completed" if repository.finalize(claim) else "continued"
    except AccountErasureLeaseLost:
        return "lease_lost"
    except Exception:
        return _retry_without_leaking(repository, claim, "persistence_unavailable")
