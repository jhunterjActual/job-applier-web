"""Supervised delete-only consumer for durable account-erasure requests."""

from __future__ import annotations

import argparse
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

import config
from account_erasure_execution import (
    execute_account_erasure_claim,
    runtime_account_erasure_delete_backend,
)
from account_erasure_repository import (
    AccountErasureUnavailable,
    PostgreSQLAccountErasureWorkerRepository,
    claim_next_account_erasure,
)
from data_store import SHARED_DATA_STORE, validate_data_store_configuration
from postgres_database import runtime_postgres_engine
from version import APP_BUILD


STOP_EVENT = Event()


def _request_stop(_signal_number, _frame) -> None:
    STOP_EVENT.set()


def _write_heartbeat(path: Path, status: str) -> None:
    payload = {
        "build": APP_BUILD,
        "pid": os.getpid(),
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(temporary_path, path)


def run(once: bool = False) -> int:
    """Claim bounded work without logging owner, request, object, or error data."""
    summary = config.validate_account_erasure_worker_configuration()
    interval = config.get_account_erasure_poll_seconds()
    batch_size = config.get_account_erasure_batch_size()
    enabled = config.account_erasure_worker_enabled()
    heartbeat_path = config.DATA_DIR / "account-erasure-worker-heartbeat.json"
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    print(
        json.dumps(
            {
                "event": "account_erasure_worker_started",
                "build": APP_BUILD,
                "environment": summary["environment"],
                "queue": "account_erasure" if enabled else "disabled",
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    if not enabled:
        while not STOP_EVENT.is_set():
            _write_heartbeat(heartbeat_path, "idle_disabled")
            if once or STOP_EVENT.wait(interval):
                break
    else:
        if validate_data_store_configuration() != SHARED_DATA_STORE:
            raise config.RuntimeConfigurationError(
                "The account-erasure worker requires PostgreSQL shared storage."
            )
        engine = runtime_postgres_engine()
        repository = PostgreSQLAccountErasureWorkerRepository(engine=engine)
        # Validate delete-only R2 access before claiming; configuration failure
        # must not consume the request's bounded attempt budget.
        backend = runtime_account_erasure_delete_backend()
        worker_id = str(uuid4())
        while not STOP_EVENT.is_set():
            persistence_unavailable = False
            try:
                repository.purge_expired(limit=100)
                claim = claim_next_account_erasure(worker_id, engine=engine)
            except AccountErasureUnavailable:
                claim = None
                persistence_unavailable = True
                _write_heartbeat(heartbeat_path, "persistence_unavailable")
            if claim is None:
                if not persistence_unavailable:
                    _write_heartbeat(heartbeat_path, "idle")
            else:
                _write_heartbeat(heartbeat_path, "working")
                result = execute_account_erasure_claim(
                    claim,
                    repository,
                    backend=backend,
                    batch_size=batch_size,
                    stop_requested=STOP_EVENT.is_set,
                )
                _write_heartbeat(heartbeat_path, result)
            if once or (claim is None and STOP_EVENT.wait(interval)):
                break
    print(
        json.dumps(
            {"event": "account_erasure_worker_stopped", "build": APP_BUILD},
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Validate configuration, process at most one claim, and exit.",
    )
    arguments = parser.parse_args()
    return run(once=arguments.once)


if __name__ == "__main__":
    raise SystemExit(main())
