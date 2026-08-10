"""Supervised worker-process scaffold for the future durable job queue."""

import argparse
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

import config
from version import APP_BUILD


STOP_EVENT = Event()


def _write_heartbeat(path: Path) -> None:
    payload = {
        "build": APP_BUILD,
        "pid": os.getpid(),
        "status": "idle_no_queue",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary_path, path)


def _request_stop(_signal_number, _frame) -> None:
    STOP_EVENT.set()


def run(once: bool = False) -> int:
    """Validate the worker runtime and publish a bounded lifecycle heartbeat."""
    summary = config.validate_runtime_configuration()
    interval = config.get_worker_heartbeat_seconds()
    heartbeat_path = config.DATA_DIR / "worker-heartbeat.json"
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    print(
        json.dumps(
            {
                "event": "worker_started",
                "build": APP_BUILD,
                "environment": summary["environment"],
                "queue": "not_configured",
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    while not STOP_EVENT.is_set():
        _write_heartbeat(heartbeat_path)
        if once or STOP_EVENT.wait(interval):
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
