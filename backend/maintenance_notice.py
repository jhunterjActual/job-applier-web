"""Public maintenance notices with root-only, atomic operator updates.

No database, provider, runtime configuration, or application imports: the CLI
can be used while the application is running, without preparing runtime data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from uuid import uuid4
from zoneinfo import ZoneInfo


NOTICE_ROOT = Path("/etc/careertrellis/announcements")
NOTICE_FILE = NOTICE_ROOT / "current.json"
MAX_BYTES = 256 * 1024
MAX_EVENTS = 1000
ERROR = "The maintenance announcement operation did not complete. Inspect before retrying."
FIELDS = {"message", "impact", "status", "time_zone", "publish_at", "starts_at", "ends_at", "expires_at"}
STATUSES = {"planned", "in_progress", "completed", "cancelled"}


class NoticeError(ValueError):
    pass


def require(condition: bool) -> None:
    if not condition:
        raise NoticeError(ERROR)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: object) -> datetime:
    require(isinstance(value, str) and len(value) <= 32)
    require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", value)))
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        raise NoticeError(ERROR) from None


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_notice(value: object) -> dict:
    require(isinstance(value, dict) and set(value) == FIELDS)
    for name, limit in (("message", 400), ("impact", 240)):
        text = value[name]
        require(isinstance(text, str) and 1 <= len(text) <= limit and text == text.strip())
        # Plain text only. Reject controls (including bidi overrides), markup and
        # URL-like content; announcements must never become login/link prompts.
        import unicodedata
        require(not any(unicodedata.category(char).startswith("C") for char in text))
        require(not any(char in text for char in "<>"))
        require(not re.search(r"(?i)(https?://|www\.|mailto:|javascript:)", text))
    require(isinstance(value["status"], str) and value["status"] in STATUSES)
    zone = value["time_zone"]
    require(isinstance(zone, str) and 1 <= len(zone) <= 64)
    require(bool(re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)*", zone)))
    try:
        ZoneInfo(zone)
    except (ValueError, KeyError):
        raise NoticeError(ERROR) from None
    times = {key: timestamp(value[key]) for key in ("publish_at", "starts_at", "ends_at", "expires_at")}
    require(times["publish_at"] <= times["starts_at"] < times["ends_at"] <= times["expires_at"])
    require(times["ends_at"] - times["starts_at"] <= timedelta(days=7))
    require(times["starts_at"] - times["publish_at"] <= timedelta(days=90))
    require(times["expires_at"] - times["ends_at"] <= timedelta(hours=24))
    return {**value, **{key: stamp(item) for key, item in times.items()}}


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def decode(payload: bytes) -> dict:
    require(len(payload) <= MAX_BYTES)
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError):
        raise NoticeError(ERROR) from None
    require(isinstance(value, dict))
    return value


def _safe_parent(root: Path) -> None:
    require(root.is_absolute() and root.resolve() == root)
    for parent in (root, *root.parents):
        metadata = parent.lstat()
        require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == 0)
        require(not metadata.st_mode & 0o022)


def read_state(path: Path = NOTICE_FILE, *, strict: bool = True) -> dict | None:
    if strict:
        # An unconfigured notice is normal; unsafe existing ancestors are not.
        if not path.parent.exists():
            _safe_parent(path.parent.parent)
            return None
        _safe_parent(path.parent)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1)
        require(metadata.st_size <= MAX_BYTES)
        if strict:
            require(metadata.st_uid == 0 and stat.S_IMODE(metadata.st_mode) == 0o644)
        value = decode(stream.read(MAX_BYTES + 1))
    require(set(value) == {"schema", "revision", "notice", "audit"})
    require(value["schema"] == 1)
    require(isinstance(value["revision"], str) and bool(re.fullmatch(r"[0-9a-f]{32}", value["revision"])))
    if value["notice"] is not None:
        value["notice"] = validate_notice(value["notice"])
    require(isinstance(value["audit"], list) and 1 <= len(value["audit"]) <= MAX_EVENTS)
    for event in value["audit"]:
        require(isinstance(event, dict) and set(event) == {"revision", "at", "action"})
        require(isinstance(event["revision"], str) and bool(re.fullmatch(r"[0-9a-f]{32}", event["revision"])))
        timestamp(event["at"])
        require(event["action"] in {"set", "disable"})
    require(value["audit"][-1]["revision"] == value["revision"])
    return value


def public_notice(path: Path = NOTICE_FILE, *, strict: bool = True, now: datetime | None = None) -> dict:
    selected = now or utc_now()
    state = read_state(path, strict=strict)
    notice = state["notice"] if state else None
    if notice and not timestamp(notice["publish_at"]) <= selected < timestamp(notice["expires_at"]):
        notice = None
    return {"notice": notice, "server_time": stamp(selected)}


def update_notice(operation: str, notice: dict | None, *, path: Path = NOTICE_FILE,
                  strict: bool = True, now: datetime | None = None) -> str:
    """Caller holds the operator lock. Audit and notice publish as one snapshot."""
    selected = now or utc_now()
    require(operation in {"set", "disable"})
    if operation == "set":
        notice = validate_notice(notice)
        require(timestamp(notice["expires_at"]) > selected)
        require(timestamp(notice["publish_at"]) <= selected + timedelta(days=90))
    else:
        require(notice is None)
    previous = read_state(path, strict=strict)
    audit = previous["audit"] if previous else []
    # Never silently prune audit history. Operator-approved archival is needed
    # if this bound is ever reached; current notice remains untouched on failure.
    require(len(audit) < MAX_EVENTS)
    revision = uuid4().hex
    document = {"schema": 1, "revision": revision, "notice": notice, "audit": [
        *audit, {"revision": revision, "at": stamp(selected), "action": operation},
    ]}
    payload = json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    require(len(payload) <= MAX_BYTES)
    descriptor, temporary = tempfile.mkstemp(prefix=".notice-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.chmod(temporary, 0o644)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return revision


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if getattr(os, "geteuid", lambda: -1)() != 0 or arguments not in (["set"], ["disable"], ["inspect"]):
        print(ERROR, file=sys.stderr)
        return 1
    try:
        operation = arguments[0]
        if operation == "inspect":
            state = read_state(NOTICE_FILE)
            print(json.dumps({"result": "ok", "configured": bool(state and state["notice"]),
                              "revision": state["revision"] if state else None,
                              "audit_events": len(state["audit"]) if state else 0}))
            return 0
        import fcntl
        _safe_parent(NOTICE_ROOT.parent)
        if not NOTICE_ROOT.exists():
            NOTICE_ROOT.mkdir(mode=0o755)
        _safe_parent(NOTICE_ROOT)
        lock = os.open(NOTICE_ROOT / ".operator.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock, "r+") as stream:
            metadata = os.fstat(stream.fileno())
            require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
                    and metadata.st_nlink == 1 and stat.S_IMODE(metadata.st_mode) == 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            payload = sys.stdin.buffer.read(4097) if operation == "set" else b""
            require(len(payload) <= 4096)
            notice = decode(payload) if operation == "set" else None
            revision = update_notice(operation, notice, path=NOTICE_FILE)
            print(json.dumps({"result": "ok", "operation": operation, "revision": revision}))
    except (Exception, KeyboardInterrupt):
        print(ERROR, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
