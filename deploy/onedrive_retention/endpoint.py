"""Strict fixed DB-stage protocol, with no caller-selected paths or commands.

Existing paired db_control/entry must route the five new operations explicitly.
The new installed standalone entry supplies manifest checking, stage execution
and retirement receipt/catalog integration. Until installed, native paths refuse.
"""
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat

from . import bundle, coordinator as c, hydrate

SCHEMA = "careertrellis.onedrive-retention-request/v1"
RESPONSE = "careertrellis.onedrive-stage-status/v1"
OPERATIONS = {"bundle_" + stage: stage for stage in (*c.STAGES, "status")}
ENTRY = "/usr/local/libexec/careertrellis/onedrive_retention/entry.py"


def request(policy_sha, transaction, stage, payload):
    return validate_request({"schema": SCHEMA, "operation": "bundle_" + stage,
        "retention_policy_sha256": policy_sha, "capture_transaction": transaction, "payload": payload})


def validate_request(value):
    c.need(type(value) is dict and set(value) == {"schema", "operation", "retention_policy_sha256",
        "capture_transaction", "payload"} and value["schema"] == SCHEMA)
    c.need(type(value["operation"]) is str and value["operation"] in OPERATIONS
         and type(value["retention_policy_sha256"]) is str and c.HEX.fullmatch(value["retention_policy_sha256"])
         and type(value["capture_transaction"]) is str and c.TX.fullmatch(value["capture_transaction"]))
    operation, payload = OPERATIONS[value["operation"]], value["payload"]
    keys = {"stage", "receipt_facts", "web_preflight"} if operation == "restore" else {"stage", "receipt_facts"}
    if operation != 'status' and type(payload) is dict and 'web_history' in payload:
        keys |= {'web_history'}
    c.need(type(payload) is dict and set(payload) == keys and payload["stage"] in c.STAGES)
    c.need(operation == "status" or payload["stage"] == operation)
    facts = payload["receipt_facts"]
    expected = set() if operation in ("hydrate", "status") else {"predecessor_receipt_sha256"}
    c.need(type(facts) is dict and set(facts) == expected)
    c.need(all(type(value) is str and c.HEX.fullmatch(value) for value in facts.values()))
    if operation == "restore": c.web_proof(payload["web_preflight"], dt.datetime.now(dt.timezone.utc))
    if operation != 'status' and 'web_history' in payload:
        from . import guard
        guard.validate_peer_history(payload['web_history'],value['capture_transaction'],operation,
                                    value['retention_policy_sha256'])
    return value


def from_paired_request(value):
    """Called only after existing authenticated envelope/paired-policy checks."""
    c.need(type(value) is dict and value.get("operation") in OPERATIONS)
    payload = value.get("payload")
    c.need(type(payload) is dict and set(payload) == {"retention_policy_sha256", "capture_transaction", "payload"}
         and payload["capture_transaction"] == value.get("transaction"))
    requested = validate_request(dict(payload, schema=SCHEMA, operation=value["operation"]))
    require_native_peer(requested)
    return requested


def require_native_peer(value):
    """Authenticated native mutations require the protected WEB reservation.

    Minimal pure request fixtures may omit it; neither the paired bridge nor
    the concrete native endpoint accepts those fixtures for dispatch.
    """
    if value['operation'] != 'bundle_status':
        c.need('web_history' in value['payload'])
        from . import guard
        guard.validate_peer_history(value['payload']['web_history'],value['capture_transaction'],
            OPERATIONS[value['operation']],value['retention_policy_sha256'])
    return value


def validate_response(value, requested):
    c.need(type(value) is dict and set(value) == {"schema", "capture_transaction", "retention_policy_sha256",
        "stage", "state", "receipt_sha256", "facts"} and value["schema"] == RESPONSE)
    c.need(value["capture_transaction"] == requested["capture_transaction"]
         and value["retention_policy_sha256"] == requested["retention_policy_sha256"]
         and value["stage"] == requested["payload"]["stage"]
         and value["state"] in ("running", "complete", "failed", "unknown")
         and type(value["facts"]) is dict)
    if value["state"] == "complete":
        c.need(type(value["receipt_sha256"]) is str and c.HEX.fullmatch(value["receipt_sha256"]))
        base = {"archive_sha256", "archive_bytes", "generation_sha256", "backup_label"}
        expected = ({"catalog_sha256", "retired_count"} if value["stage"] == "retire" else
                    {"catalog_sha256", "prepared_count"} if value['stage'] == 'prepare' else base)
        if value["stage"] in ("publish", "restore"): expected |= {"native_restore_accepted"}
        c.need(set(value["facts"]) == expected)
        if value["stage"] == "publish": c.need(value["facts"]["native_restore_accepted"] is False)
        if value["stage"] == "restore": c.need(value["facts"]["native_restore_accepted"] is True)
    else: c.need(value["receipt_sha256"] == "" and value["facts"] == {})
    allowed = {"archive_sha256", "archive_bytes", "generation_sha256", "backup_label",
               "native_restore_accepted", "catalog_sha256", "retired_count", "prepared_count"}
    c.need(set(value["facts"]) <= allowed)
    for key, item in value["facts"].items():
        if key.endswith("sha256"): c.need(type(item) is str and c.HEX.fullmatch(item))
        elif key in ("archive_bytes", "retired_count", "prepared_count"):
            c.need(type(item) is int and (1 if key == "archive_bytes" else 0) <= item <= 512*1024**2)
        elif key == "native_restore_accepted": c.need(type(item) is bool)
        else: c.need(type(item) is str and re.fullmatch(r"[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?", item))
    return value


class Endpoint:
    def __init__(self, policy, backend): self.policy, self.backend = policy, backend

    def handle(self, value):
        value = validate_request(value)
        c.need(value["retention_policy_sha256"] == self.policy.digest)
        stage = value["payload"]["stage"]
        if type(self.backend) is NativeDB: require_native_peer(value)
        if value["operation"] == "bundle_status":
            # Progress observations may not acquire/create the operation lock.
            # preflight verifies installed bytes and policy only; observe must
            # remain provider-free and must never dispatch a terminal collector.
            self.backend.preflight()
            return validate_response(self.backend.observe(value), value)
        with self.backend.locked():
            self.backend.preflight()
            self.policy.require_active(self.backend.now())
            if self.backend.intent_exists(value):
                self.backend.require_same_intent(value)
                return validate_response(self.backend.observe(value), value)
            self.backend.require_previous_terminal(value)
            self.backend.bind_predecessor(value)
            self.backend.intent(value)  # Durable before WEB proof write or systemd dispatch.
            try:
                self.backend.dispatch(value)
                return validate_response(self.backend.observe(value), value)
            except BaseException:
                self.backend.failed(value)
                raise c.Refused("retention_coordinator_requires_reconciliation") from None


def unit(stage, transaction):
    c.need(stage in c.STAGES and type(transaction) is str and c.TX.fullmatch(transaction))
    return "careertrellis-onedrive-bundle-" + stage + "-" + transaction + ".service"


def command_plan(stage, transaction, journal_root):
    name = unit(stage, transaction)
    # Only a fixed manifest-guarded entry, fixed stage and validated transaction.
    log = str(Path(journal_root) / "private-output.log")
    args = ["/usr/bin/systemd-run", "--no-block", "--unit=" + name,
        "--property=Type=exec", "--property=Restart=no", "--property=RemainAfterExit=yes",
        "--property=UMask=0077", "--property=TimeoutStartSec=30s", "--property=TimeoutStopSec=90s",
        "--property=KillMode=control-group", "--property=SendSIGKILL=yes",
        "--property=StandardOutput=append:" + log, "--property=StandardError=append:" + log]
    limits = {'hydrate':'9h','publish':'6h','prepare':'9h','retire':'1h'}
    if stage != 'restore': args += ['--property=RuntimeMaxSec=' + limits[stage]]
    if stage == "restore":
        args += ["--property=RuntimeMaxSec=2h", "--property=PrivateNetwork=yes"]
    return args + ["/usr/bin/python3", "-I", "-B", ENTRY, "db-stage", stage, "--transaction", transaction]


class NativeDB:
    def __init__(self, policy): self.policy, self.root = policy, c.STATE / "db"
    def now(self): return dt.datetime.now(dt.timezone.utc)

    @contextmanager
    def locked(self):
        c.need(os.geteuid() == os.getegid() == 0 and socket.gethostname() == "job-applier-db-staging-01")
        with c.control(self.root): yield

    def preflight(self):
        from . import entry
        c.need(entry.verify_install() == self.policy.data["artifact_revision"])
        c.native_policy_binding(self.policy)
        from paired_maintenance import entry as paired_entry
        paired_entry.verify_install()
        c.need(paired_entry.load_policy().digest == self.policy.data["paired_policy_sha256"])

    def folder(self, value): return self.root / value["capture_transaction"] / value["payload"]["stage"]
    def intent_exists(self, value): return os.path.lexists(self.folder(value) / "intent.json")
    def require_same_intent(self, value): c.need(c.read_private(self.folder(value) / "intent.json") == value)

    def bind_predecessor(self, value):
        stage = value["payload"]["stage"]
        if stage == "hydrate": return
        previous = c.STAGES[c.STAGES.index(stage)-1]
        query = request(self.policy.digest, value["capture_transaction"], "status", {"stage": previous, "receipt_facts": {}})
        observed = self.observe(query)
        c.need(observed["state"] == "complete" and observed["receipt_sha256"] ==
               value["payload"]["receipt_facts"]["predecessor_receipt_sha256"])
        if stage in ('prepare','retire'):
            restored = request(self.policy.digest,value['capture_transaction'],'status',{'stage':'restore','receipt_facts':{}})
            accepted = self.observe(restored)
            c.need(accepted['state'] == 'complete' and accepted['facts'].get('native_restore_accepted') is True)

    def require_previous_terminal(self, value):
        # New installed entry checks every earlier unit, child identity, original
        # cgroup and postmaster fence. Unknown state is not terminal evidence.
        from . import entry
        c.need(entry.retention_idle(value["capture_transaction"], value["payload"]["stage"]) is True)

    def intent(self, value):
        transaction = self.root / value["capture_transaction"]
        if not os.path.lexists(transaction):
            transaction.mkdir(mode=0o700); c.fsync_directory(self.root)
        c.directory(transaction)
        folder = self.folder(value); folder.mkdir(mode=0o700)
        c.fsync_directory(transaction)
        hydrate.exclusive(folder / "intent.json", bundle.canonical(value))

    def dispatch(self, value):
        stage, tx = value["payload"]["stage"], value["capture_transaction"]
        folder = self.folder(value)
        if stage == "restore":
            c.web_proof(value["payload"]["web_preflight"], self.now(),
                        expected_release=self.policy.data["application_release"])
            target = Path("/var/lib/careertrellis/onedrive-bundles") / tx / "web-preflight.json"
            hydrate.exclusive(target, bundle.canonical(value["payload"]["web_preflight"]))
        log = folder / "private-output.log"
        hydrate.exclusive(log, b"")
        if stage == "restore":
            # A separate, upfront-dispatched collector must retain actual success
            # metadata, then explicitly stop only the verified empty owner unit.
            # It must never be launched as a side effect of status/progress polls.
            # Missing entry/collector implementation is an intentional hard gate.
            from . import entry
            dispatch = getattr(entry, "dispatch_native_with_collector", None)
            c.need(callable(dispatch))
            c.need(dispatch(tx) is True)
        else:
            from . import execution
            execution.dispatch_stage(tx, stage)
        hydrate.exclusive(folder / "dispatch.json", bundle.canonical({"unit": unit(stage, tx),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "dispatched_at": self.now().isoformat()}))

    def observe(self, value):
        # Fixed entry owns version-specific journal/receipt schema normalization;
        # it must inspect only this unit/PID/journal, never provider repositories.
        from . import entry
        result = validate_response(entry.observe_retention_stage(value["capture_transaction"],
            value["payload"]["stage"], self.policy.digest), value)
        if value["payload"]["stage"] == "restore" and result.get("state") == "complete":
            from . import native_restore as native, local_restore_plan as plans
            root = Path(plans.RESTORE_ROOT) / value["capture_transaction"]
            def document(name):
                raw = native.protected(root / name, maximum=131072)
                parsed = native.decode(raw)
                c.need(plans.canonical(parsed) == raw)
                return parsed
            receipt = document("complete.json")
            ownership = document("ownership.json")
            proof = document("unit-terminal-proof.json")
            stop = document("unit-stop-intent.json")
            native.validate_terminal_proof(proof, receipt, ownership["owner"], stop_intent=stop)
            tx = value["capture_transaction"]
            c.need(receipt.get("transaction") == tx and type(receipt.get("attempt")) is int
                   and receipt["attempt"] == 1)
            c.need(result.get("receipt_sha256") == hashlib.sha256(plans.canonical(receipt)).hexdigest())
            download_raw = native.protected(Path(plans.ROOT) / tx / "downloaded-receipt.json", maximum=16384)
            download = native.decode(download_raw)
            c.need(plans.canonical(download) == download_raw and type(download) is dict
                   and set(download) == {"schema", "result", "transaction", "backup_label", "generation_sha256",
                        "archive_sha256", "archive_bytes", "manifest_sha256", "native_restore_accepted",
                        "completed_at", "destination", "quota_after"}
                   and download["schema"] == "careertrellis.onedrive-bundle-download/v1"
                   and download["result"] == "download_verified" and download["native_restore_accepted"] is False
                   and receipt.get("download_receipt_sha256") == hashlib.sha256(download_raw).hexdigest()
                   and ownership.get("download_receipt_sha256") == receipt["download_receipt_sha256"])
            c.need(download["transaction"] == tx
                   and all(result["facts"][key] == receipt.get(key) == download[key]
                           for key in ("archive_sha256", "generation_sha256", "backup_label"))
                   and result["facts"]["native_restore_accepted"] is receipt["native_restore_accepted"] is True
                   and type(download["archive_bytes"]) is int and 0 < download["archive_bytes"] <= 512*1024**2
                   and result["facts"]["archive_bytes"] == download["archive_bytes"])
        return validate_response(result, value)

    def failed(self, value):
        hydrate.exclusive(self.folder(value) / "failed.json", bundle.canonical({"category": "stage_requires_reconciliation"}))
