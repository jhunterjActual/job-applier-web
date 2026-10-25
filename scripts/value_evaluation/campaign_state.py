"""Create-once study accounting; no credentials, network, or dispatch authority.

One fsynced journal event admits a request and its dollar reservation together.
Unknown outcomes freeze the campaign. Existing budget_guard ledgers are untouched.
The private evidence directory must exclude untrusted/concurrent noncooperating writers.
"""
from contextlib import contextmanager, nullcontext
from threading import RLock
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from budget_guard import money


class CampaignRefused(ValueError):
    pass


MAX_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 20_000
ZERO = "0" * 64


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise CampaignRefused("Invalid fields")


def _label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise CampaignRefused("Invalid identifier")
    return value


def _reference(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,159}", value):
        raise CampaignRefused("Sanitized evidence reference required")
    return value


def _money(value):
    try:
        return money(value)
    except ValueError:
        raise CampaignRefused("Invalid amount") from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CampaignRefused("Duplicate JSON key")
        result[key] = value
    return result


def _stage(value):
    _fields(value, "product case stage")
    _label(value["product"])
    if (not isinstance(value["case"], str) or not isinstance(value["stage"], str) or
        value["case"] not in {f"V{i:02d}" for i in range(1, 13)} or value["stage"] not in {"fit", "materials"}):
        raise CampaignRefused("Invalid stage")
    return (value["product"], value["case"], value["stage"])


def validate_policy(value):
    schema = value.get("schema") if isinstance(value, dict) else None
    names = "schema run_id frozen_content_sha256 mode approval_reference accounting_basis total_limit_usd packet_limits_usd stage_limits_usd request_limits order"
    if schema == "study-campaign-policy-v2":
        names += " max_inflight_requests"
    _fields(value, names)
    if schema not in ("study-campaign-policy-v1", "study-campaign-policy-v2"):
        raise CampaignRefused("Unsupported campaign policy")
    if schema == "study-campaign-policy-v2" and (type(value["max_inflight_requests"]) is not int or not 1 <= value["max_inflight_requests"] <= 4):
        raise CampaignRefused("Explicit bounded concurrent request capacity required")
    _label(value["run_id"])
    if not isinstance(value["frozen_content_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["frozen_content_sha256"]):
        raise CampaignRefused("Frozen content digest required")
    if not isinstance(value["mode"], str) or value["mode"] not in {"offline", "paid"}:
        raise CampaignRefused("Explicit campaign mode required")
    if value["mode"] == "paid":
        _reference(value["approval_reference"])
    elif value["approval_reference"] is not None:
        raise CampaignRefused("Offline proof cannot declare funding approval")
    if not isinstance(value["accounting_basis"], str) or value["accounting_basis"] not in {"approved_conservative_estimate", "verified_upper_bound"}:
        raise CampaignRefused("Explicit accounting basis required")
    total = _money(value["total_limit_usd"])
    packets = value["packet_limits_usd"]
    limits = value["request_limits"]
    stage_money = value["stage_limits_usd"]
    if (not isinstance(packets, dict) or len(packets) != 2 or not isinstance(limits, dict) or set(limits) != set(packets)
        or not isinstance(stage_money, dict) or set(stage_money) != set(packets)):
        raise CampaignRefused("Two product budgets required")
    for product, cap in packets.items():
        _label(product)
        if not 0 < _money(cap) <= total:
            raise CampaignRefused("Invalid packet ceiling")
        _fields(stage_money[product], "fit materials")
        if any(not 0 < _money(amount) <= _money(cap) for amount in stage_money[product].values()):
            raise CampaignRefused("Invalid stage dollar ceiling")
        _fields(limits[product], "fit materials packet")
        for number in limits[product].values():
            if type(number) is not int or not 1 <= number <= 100:
                raise CampaignRefused("Invalid request ceiling")
        if max(limits[product]["fit"], limits[product]["materials"]) > limits[product]["packet"]:
            raise CampaignRefused("Stage request ceiling exceeds packet ceiling")
    order = value["order"]
    if not isinstance(order, list) or len(order) != 36:
        raise CampaignRefused("Complete 36-stage order required")
    actual = [_stage(item) for item in order]
    expected = {(product, f"V{i:02d}", stage) for product in packets for i in range(1, 13)
                for stage in (("fit", "materials") if i <= 6 else ("fit",))}
    if len(set(actual)) != 36 or set(actual) != expected:
        raise CampaignRefused("Unexpected or duplicate study stage")
    for product in packets:
        for i in range(1, 7):
            if actual.index((product, f"V{i:02d}", "materials")) < actual.index((product, f"V{i:02d}", "fit")):
                raise CampaignRefused("Materials precede fit")
    return json.loads(_canonical(value))


def _regular(path):
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise CampaignRefused("Linked campaign paths refused")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise CampaignRefused("Campaign target is not a private regular file")


class Campaign:
    def __init__(self, path, policy):
        self.path = Path(path).absolute()
        self.head = self.path.with_name(self.path.name + ".head")
        self.pending_head = self.path.with_name(self.path.name + ".head.pending")
        self.policy = validate_policy(policy)
        self._concurrent = self.policy["schema"] == "study-campaign-policy-v2"
        self._live_reservations = set()
        self._thread_lock = RLock() if self._concurrent else nullcontext()
        if not self.path.parent.is_dir():
            raise CampaignRefused("Existing private evidence directory required")
        _regular(self.path)
        _regular(self.head)
        _regular(self.pending_head)

    @contextmanager
    def _lock(self):
        with self._thread_lock:
            _regular(self.path)
            _regular(self.head)
            _regular(self.pending_head)
            if self.pending_head.exists():
                raise CampaignRefused("Interrupted head update; preserve and reconcile")
            lock = self.path.with_name(self.path.name + ".lock")
            _regular(lock)
            token = _canonical({"pid": os.getpid(), "owner": uuid4().hex})
            try:
                fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                raise CampaignRefused("Campaign lock exists; inspect owner, never clear by age") from None
            identity = os.fstat(fd)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(token)
                    stream.flush()
                    os.fsync(stream.fileno())
                _regular(self.path)
                yield
            finally:
                _regular(lock)
                current = lock.stat()
                if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino) or lock.read_bytes() != token:
                    raise CampaignRefused("Campaign lock owner changed; preserve for inspection")
                lock.unlink()

    @classmethod
    def create(cls, path, policy):
        campaign = cls(path, policy)
        with campaign._lock():
            if campaign.head.exists():
                raise CampaignRefused("Campaign head already exists; no reset")
            try:
                with campaign.path.open("xb") as stream:
                    campaign._write(stream, 0, ZERO, "create", campaign.policy)
            except FileExistsError:
                raise CampaignRefused("Campaign already exists; no reset") from None
            campaign._write_head(1, hashlib.sha256(campaign.path.read_bytes()).hexdigest(), create=True)
        return campaign

    def _write_head(self, events, tail, *, create=False):
        target = self.head if create else self.pending_head
        with target.open("xb") as stream:
            stream.write(_canonical({"events": events, "tail_sha256": tail}) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if not create:
            os.replace(self.pending_head, self.head)

    @classmethod
    def open(cls, path, expected_policy):
        campaign = cls(path, expected_policy)
        campaign.snapshot()
        return campaign

    @staticmethod
    def _write(stream, sequence, previous, event, payload):
        row = {"sequence": sequence, "previous_sha256": previous, "event": event, "payload": payload}
        raw = _canonical(row) + b"\n"
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())

    def _read(self):
        if not self.path.is_file() or self.path.stat().st_size > MAX_BYTES:
            raise CampaignRefused("Missing or oversized campaign")
        raw = self.path.read_bytes()
        if not raw or len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
            raise CampaignRefused("Truncated campaign; preserve and reconcile")
        lines = raw.splitlines(keepends=True)
        if len(lines) > MAX_EVENTS:
            raise CampaignRefused("Campaign event bound exceeded")
        state = {"index": 0, "active": False, "calls": {}, "completed": {}, "halted": False}
        previous = ZERO
        for sequence, line in enumerate(lines):
            try:
                row = json.loads(line, object_pairs_hook=_unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(CampaignRefused("Nonfinite JSON")))
                _fields(row, "sequence previous_sha256 event payload")
                if type(row["sequence"]) is not int or row["sequence"] != sequence or row["previous_sha256"] != previous or _canonical(row) + b"\n" != line:
                    raise CampaignRefused("Campaign hash/sequence changed")
                if sequence == 0:
                    if row["event"] != "create" or validate_policy(row["payload"]) != self.policy:
                        raise CampaignRefused("Campaign binding changed")
                else:
                    self._apply(state, row["event"], row["payload"])
            except (ValueError, TypeError, KeyError, RecursionError):
                raise CampaignRefused("Invalid campaign event; preserve and reconcile") from None
            previous = hashlib.sha256(line).hexdigest()
        if not self.head.is_file() or self.head.stat().st_size > 200:
            raise CampaignRefused("Missing or oversized campaign head")
        if self.head.read_bytes() != _canonical({"events": len(lines), "tail_sha256": previous}) + b"\n":
            raise CampaignRefused("Journal/head mismatch; preserve and reconcile")
        return state, len(lines), previous

    def _current(self, state):
        if state["index"] >= len(self.policy["order"]):
            raise CampaignRefused("Campaign already terminal")
        return self.policy["order"][state["index"]]

    @staticmethod
    def _pending(state):
        return any(call["status"] != "settled" for call in state["calls"].values())

    def _apply(self, state, event, value):
        if event == "begin":
            _stage(value)
            if state["halted"] or state["active"] or self._pending(state) or value != self._current(state):
                raise CampaignRefused("Stage admission refused")
            state["active"] = True
            return
        if event in {"reserve", "settle", "unknown", "finish"}:
            if not state["active"]:
                raise CampaignRefused("No active stage")
        else:
            raise CampaignRefused("Unknown event")
        stage = self._current(state)
        if event == "reserve":
            _fields(value, "call_id amount_usd evidence")
            _label(value["call_id"])
            _reference(value["evidence"])
            amount = _money(value["amount_usd"])
            capacity = self.policy["max_inflight_requests"] if self._concurrent else 1
            pending = sum(call["status"] != "settled" for call in state["calls"].values())
            if state["halted"] or pending >= capacity or value["call_id"] in state["calls"] or amount <= 0:
                raise CampaignRefused("Dispatch reservation refused")
            if stage["stage"] == "materials" and state["completed"].get((stage["product"], stage["case"], "fit")) != "succeeded":
                raise CampaignRefused("Materials require successful fit")
            calls = list(state["calls"].values())
            packet_calls = [call for call in calls if call["stage"]["product"] == stage["product"] and call["stage"]["case"] == stage["case"]]
            stage_calls = [call for call in packet_calls if call["stage"]["stage"] == stage["stage"]]
            limits = self.policy["request_limits"][stage["product"]]
            if len(stage_calls) >= limits[stage["stage"]] or len(packet_calls) >= limits["packet"]:
                raise CampaignRefused("Request ceiling exhausted")
            exposure = lambda call: call["actual"] if call["status"] == "settled" else call["reserved"]
            if (sum(map(exposure, calls)) + amount > _money(self.policy["total_limit_usd"]) or
                sum(map(exposure, packet_calls)) + amount > _money(self.policy["packet_limits_usd"][stage["product"]]) or
                sum(map(exposure, stage_calls)) + amount > _money(self.policy["stage_limits_usd"][stage["product"]][stage["stage"]])):
                raise CampaignRefused("Dollar ceiling exhausted")
            state["calls"][value["call_id"]] = {"stage": dict(stage), "reserved": amount, "actual": None, "status": "reserved"}
        elif event in {"settle", "unknown"}:
            _fields(value, "call_id actual_usd evidence" if event == "settle" else "call_id evidence")
            _label(value["call_id"])
            _reference(value["evidence"])
            call = state["calls"].get(value["call_id"])
            if call is None or call["stage"] != stage or call["status"] != "reserved":
                raise CampaignRefused("Call transition refused; unknown requires separate reviewed reconciliation")
            if event == "unknown":
                call["status"] = "unknown"
                state["halted"] = True
            else:
                call["actual"] = _money(value["actual_usd"])
                call["status"] = "settled"
                if call["actual"] > call["reserved"]:
                    state["halted"] = True
        else:
            _fields(value, "outcome evidence")
            _reference(value["evidence"])
            if state["halted"] or self._pending(state) or not isinstance(value["outcome"], str) or value["outcome"] not in {"succeeded", "failed", "skipped"}:
                raise CampaignRefused("Stage completion refused")
            if value["outcome"] == "succeeded" and not any(call["stage"] == stage for call in state["calls"].values()):
                raise CampaignRefused("Success requires an admitted request")
            state["completed"][_stage(stage)] = value["outcome"]
            state["index"] += 1
            state["active"] = False

    def _append(self, event, payload):
        with self._lock():
            state, sequence, previous = self._read()
            # Admission-only liveness guard: historical replay never reconstructs it.
            if self._concurrent and event == "reserve" and any(
                call["status"] != "settled" and call_id not in self._live_reservations
                for call_id, call in state["calls"].items()
            ):
                raise CampaignRefused("Unresolved reservation belongs to another or reopened handle")
            self._apply(state, event, payload)
            if sequence >= MAX_EVENTS:
                raise CampaignRefused("Campaign event bound exceeded")
            encoded = _canonical({"sequence": sequence, "previous_sha256": previous, "event": event, "payload": payload}) + b"\n"
            if self.path.stat().st_size + len(encoded) > MAX_BYTES:
                raise CampaignRefused("Campaign byte bound exceeded")
            with self.path.open("ab") as stream:
                self._write(stream, sequence, previous, event, payload)
            self._write_head(sequence + 1, hashlib.sha256(encoded).hexdigest())
            if self._concurrent and event == "reserve":
                self._live_reservations.add(payload["call_id"])

    def snapshot(self):
        with self._lock():
            state, _, _ = self._read()
            calls = list(state["calls"].values())
            return {"mode": self.policy["mode"], "completed_stages": state["index"],
                "active": state["active"], "halted": state["halted"],
                "pending_calls": sum(call["status"] != "settled" for call in calls),
                "admitted_requests": len(calls),
                "exposure_microdollars": sum(call["actual"] if call["status"] == "settled" else call["reserved"] for call in calls)}

    def begin_stage(self, product, case, stage):
        self._append("begin", {"product": product, "case": case, "stage": stage})

    def reserve(self, call_id, amount_usd, evidence):
        self._append("reserve", {"call_id": call_id, "amount_usd": amount_usd, "evidence": evidence})

    def settle(self, call_id, actual_usd, evidence):
        self._append("settle", {"call_id": call_id, "actual_usd": actual_usd, "evidence": evidence})

    def mark_unknown(self, call_id, evidence):
        self._append("unknown", {"call_id": call_id, "evidence": evidence})

    def finish_stage(self, outcome, evidence):
        self._append("finish", {"outcome": outcome, "evidence": evidence})
