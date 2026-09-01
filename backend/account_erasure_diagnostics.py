"""Privacy-safe readiness diagnostics for the delete-only object boundary."""

from __future__ import annotations

from dataclasses import dataclass
import sys

import config
from account_erasure_execution import runtime_account_erasure_delete_backend
from account_erasure_repository import AccountErasureUnavailable
from object_store import ObjectStoreBackendError, ObjectStoreConfigurationError


MAXIMUM_CAUSE_DEPTH = 8


@dataclass(frozen=True)
class DiagnosticResult:
    result: str
    classification: str


def _exception_chain(error: BaseException):
    seen: set[int] = set()
    current: BaseException | None = error
    for _depth in range(MAXIMUM_CAUSE_DEPTH):
        if current is None or id(current) in seen:
            return
        seen.add(id(current))
        yield current
        next_error = current.__cause__ or current.__context__
        current = next_error if isinstance(next_error, BaseException) else None


def _http_status(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return None
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return None
    status = metadata.get("HTTPStatusCode")
    if isinstance(status, bool):
        return None
    try:
        normalized = int(status)
    except (TypeError, ValueError):
        return None
    return normalized if 100 <= normalized <= 599 else None


def classify_failure(error: BaseException) -> DiagnosticResult:
    chain = tuple(_exception_chain(error))
    if any(
        isinstance(item, (ObjectStoreConfigurationError, config.RuntimeConfigurationError))
        for item in chain
    ):
        return DiagnosticResult("blocked", "configuration_invalid")
    statuses = tuple(status for item in chain if (status := _http_status(item)) is not None)
    if 401 in statuses:
        return DiagnosticResult("blocked", "authentication_rejected")
    if 403 in statuses:
        return DiagnosticResult("indeterminate", "authorization_ambiguous")
    if 404 in statuses:
        return DiagnosticResult("blocked", "target_unavailable")
    if any(status == 429 or status >= 500 for status in statuses):
        return DiagnosticResult("blocked", "provider_unavailable")
    network_type_names = {
        "ConnectTimeoutError",
        "ConnectionClosedError",
        "EndpointConnectionError",
        "HTTPClientError",
        "ReadTimeoutError",
    }
    if any(type(item).__name__ in network_type_names for item in chain):
        return DiagnosticResult("blocked", "network_unavailable")
    if any(isinstance(item, AccountErasureUnavailable) for item in chain):
        return DiagnosticResult("blocked", "configuration_invalid")
    if any(isinstance(item, ObjectStoreBackendError) for item in chain):
        return DiagnosticResult("blocked", "provider_unavailable")
    return DiagnosticResult("blocked", "unexpected_failure")


def run_diagnostic() -> DiagnosticResult:
    try:
        backend = runtime_account_erasure_delete_backend()
        readiness = getattr(backend, "check_ready", None)
        if not callable(readiness):
            return DiagnosticResult("blocked", "configuration_invalid")
        readiness()
    except Exception as exc:
        return classify_failure(exc)
    return DiagnosticResult("ok", "ready")


def _safe_report(result: DiagnosticResult) -> str:
    return "\n".join(
        (
            "operation=account-erasure-object-storage-diagnostic",
            f"result={result.result}",
            f"classification={result.classification}",
            "readiness_only=yes",
            "delete_attempted=no",
            "object_read_attempted=no",
            "inventory_attempted=no",
            "identity_disclosed=no",
            "personal_data_disclosed=no",
            "secret_disclosed=no",
        )
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print(_safe_report(DiagnosticResult("blocked", "arguments_invalid")))
        return 1
    result = run_diagnostic()
    print(_safe_report(result))
    return 0 if result.result == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
