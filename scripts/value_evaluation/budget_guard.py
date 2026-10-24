"""Local evaluation reservations, not a provider-enforced billing cap.

This ledger is not a provider billing cap. It authorizes dispatch only against a
frozen accounting policy including every billable call/retry. Unknown settlements
retain their complete reservation. Estimates must not be described as guarantees.
Create once explicitly; never reset a missing ledger or clear an abandoned lock.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile


class BudgetRefused(ValueError):
    pass


def money(value):
    # Integer microdollars prevent rounding at ceilings.
    # Callers must round a verified maximum UP to microdollars, never down.
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,6})?", value):
        raise BudgetRefused("Use nonnegative decimal strings with at most six places")
    whole, _, fraction = value.partition(".")
    return int(whole) * 1_000_000 + int(fraction.ljust(6, "0"))


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
        raise BudgetRefused("Invalid identifier")
    return value


def reference(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,240}", value):
        raise BudgetRefused("Invalid sanitized evidence reference")
    return value


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    manifest_sha256: str
    total_limit_usd: str
    packet_attempt_limit_usd: str
    accounting_basis: str
    approval_reference: str

    def __post_init__(self):
        identifier(self.run_id)
        if not isinstance(self.manifest_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", self.manifest_sha256):
            raise BudgetRefused("Exact frozen manifest hash required")
        if not 0 < money(self.packet_attempt_limit_usd) <= money(self.total_limit_usd):
            raise BudgetRefused("Positive ordered spending limits required")
        if self.accounting_basis not in ("verified_upper_bound", "approved_conservative_estimate"):
            raise BudgetRefused("Explicit accounting basis required")
        reference(self.approval_reference)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BudgetRefused("Duplicate ledger key")
        result[key] = value
    return result


class Ledger:
    MAX_CALLS = 1000
    MAX_BYTES = 1024 * 1024

    def __init__(self, path, config):
        if not isinstance(config, RunConfig):
            raise BudgetRefused("Frozen run configuration required")
        self.path = Path(path).absolute()
        self.config = config

    @classmethod
    def create(cls, path, config):
        ledger = cls(path, config)
        with ledger._lock():
            state = {"version": 2, "config": asdict(config), "halted": False, "calls": {}}
            try:
                with ledger.path.open("x", encoding="utf-8", newline="\n") as stream:
                    json.dump(state, stream, indent=2, allow_nan=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                raise BudgetRefused("Ledger already exists") from None
        return ledger

    @contextmanager
    def _lock(self):
        self.path = self.path.parent.resolve(strict=True) / self.path.name
        if self.path.is_symlink():
            raise BudgetRefused("Linked ledger refused")
        lock = self.path.with_name(self.path.name + ".lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise BudgetRefused("Ledger locked; inspect owning attempt") from None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "run_id": self.config.run_id,
                           "started_at_utc": datetime.now(timezone.utc).isoformat()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            yield
        finally:
            lock.unlink()

    def _validate(self, state):
        if (not isinstance(state, dict) or set(state) != {"version", "config", "halted", "calls"}
                or type(state["version"]) is not int or state["version"] != 2
                or state["config"] != asdict(self.config) or type(state["halted"]) is not bool
                or not isinstance(state["calls"], dict) or len(state["calls"]) > self.MAX_CALLS):
            raise BudgetRefused("Unexpected ledger contract")
        total, packets, breached = 0, {}, False
        for call_id, call in state["calls"].items():
            identifier(call_id)
            if not isinstance(call, dict) or set(call) != {
                    "packet_attempt", "reserved_usd", "actual_usd", "status", "bound_evidence"}:
                raise BudgetRefused("Invalid ledger call")
            identifier(call["packet_attempt"])
            reference(call["bound_evidence"])
            reserved = money(call["reserved_usd"])
            if reserved <= 0:
                raise BudgetRefused("Invalid reservation")
            if call["actual_usd"] is None:
                expected = "reserved_or_outcome_unknown"
            else:
                expected = "reservation_exceeded" if money(call["actual_usd"]) > reserved else "settled"
            if call["status"] != expected:
                raise BudgetRefused("Inconsistent settlement")
            breached |= expected == "reservation_exceeded"
            exposure = self.exposure(call)
            total += exposure
            packet = call["packet_attempt"]
            packets[packet] = packets.get(packet, 0) + exposure
        if (breached or total > money(self.config.total_limit_usd)
                or any(value > money(self.config.packet_attempt_limit_usd) for value in packets.values())):
            if not state["halted"]:
                raise BudgetRefused("Overexposed ledger is not halted")

    @contextmanager
    def transaction(self):
        with self._lock():
            try:
                with self.path.open("rb") as stream:
                    raw = stream.read(self.MAX_BYTES + 1)
                if len(raw) > self.MAX_BYTES:
                    raise BudgetRefused("Ledger size exceeded")
                state = json.loads(raw, object_pairs_hook=_unique)
            except (OSError, ValueError, UnicodeError, RecursionError):
                raise BudgetRefused("Existing complete ledger required; inspect recovery evidence") from None
            self._validate(state)
            yield state
            self._validate(state)
            descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + "-", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(state, stream, indent=2, allow_nan=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    @staticmethod
    def exposure(call):
        return money(call["actual_usd"] if call["actual_usd"] is not None else call["reserved_usd"])

    def reserve(self, call_id, packet_attempt, upper_bound_usd, evidence):
        identifier(call_id)
        identifier(packet_attempt)
        reference(evidence)
        amount = money(upper_bound_usd)
        if amount == 0:
            raise BudgetRefused("Positive reservation required")
        with self.transaction() as state:
            if state["halted"] or call_id in state["calls"] or len(state["calls"]) >= self.MAX_CALLS:
                raise BudgetRefused("Halted, duplicate dispatch or call limit reached")
            calls = list(state["calls"].values())
            total = sum(self.exposure(call) for call in calls)
            packet = sum(self.exposure(call) for call in calls if call["packet_attempt"] == packet_attempt)
            if total + amount > money(self.config.total_limit_usd):
                raise BudgetRefused("Total budget exceeded")
            if packet + amount > money(self.config.packet_attempt_limit_usd):
                raise BudgetRefused("Packet attempt budget exceeded")
            state["calls"][call_id] = {
                "packet_attempt": packet_attempt, "reserved_usd": upper_bound_usd,
                "actual_usd": None, "status": "reserved_or_outcome_unknown",
                "bound_evidence": evidence,
            }
        # Caller may dispatch only after durable reservation succeeds.

    def settle(self, call_id, actual_usd):
        """Caller must establish complete billing, never infer zero from failure."""
        identifier(call_id)
        actual = money(actual_usd)
        breached = False
        with self.transaction() as state:
            if call_id not in state["calls"]:
                raise BudgetRefused("Unreserved call")
            call = state["calls"][call_id]
            if call["actual_usd"] is not None:
                raise BudgetRefused("Already settled")
            call["actual_usd"] = actual_usd
            breached = actual > money(call["reserved_usd"])
            call["status"] = "reservation_exceeded" if breached else "settled"
            state["halted"] = state["halted"] or breached
        if breached:
            raise BudgetRefused("Reservation exceeded; ledger halted and charge retained")
