"""Supervised, owner-bound consumer for durable search runs."""

import argparse
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

import config
from data_store import SHARED_DATA_STORE, validate_data_store_configuration
from provider_secrets import validate_provider_secret_store_configuration
from search_run_execution import execute_search_claim
from search_run_repository import (
    PostgreSQLSearchRunRepository,
    claim_next_postgres_search_run,
)
from postgres_database import runtime_postgres_engine
from version import APP_BUILD


STOP_EVENT = Event()


def _write_heartbeat(path: Path, status: str) -> None:
    payload = {
        "build": APP_BUILD,
        "pid": os.getpid(),
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary_path, path)


def _request_stop(_signal_number, _frame) -> None:
    STOP_EVENT.set()


def run(once: bool = False) -> int:
    """Claim at most one run at a time and never retain tenant context between runs."""
    summary = config.validate_runtime_configuration()
    interval = config.get_worker_heartbeat_seconds()
    enabled = config.search_worker_enabled()
    heartbeat_path = config.DATA_DIR / "worker-heartbeat.json"
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    print(
        json.dumps(
            {
                "event": "worker_started",
                "build": APP_BUILD,
                "environment": summary["environment"],
                "queue": "search_runs" if enabled else "disabled",
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
        selected_store = validate_data_store_configuration()
        if selected_store != SHARED_DATA_STORE:
            raise config.RuntimeConfigurationError(
                "The durable search worker requires PostgreSQL shared storage."
            )
        validate_provider_secret_store_configuration()
        engine = runtime_postgres_engine()
        worker_id = str(uuid4())
        while not STOP_EVENT.is_set():
            claim = claim_next_postgres_search_run(worker_id, engine=engine)
            if claim is None:
                _write_heartbeat(heartbeat_path, "idle")
            else:
                _write_heartbeat(heartbeat_path, "working")
                repository = PostgreSQLSearchRunRepository(
                    claim.owner_user_id, engine=engine
                )
                execute_search_claim(
                    claim,
                    repository,
                    stop_requested=STOP_EVENT.is_set,
                )
            if once or (claim is None and STOP_EVENT.wait(interval)):
                break
    print(
        json.dumps({"event": "worker_stopped", "build": APP_BUILD}, separators=(",", ":")),
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Validate configuration, write one heartbeat, and exit.",
    )
    arguments = parser.parse_args()
    return run(once=arguments.once)


if __name__ == "__main__":
    raise SystemExit(main())
