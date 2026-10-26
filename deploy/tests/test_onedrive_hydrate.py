"""Offline state-machine tests; native trust boundaries require Linux acceptance."""
from contextlib import contextmanager
import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
hydrate = importlib.import_module("deploy.onedrive_retention.hydrate")


def generation():
    label = "20261026-155056F"
    files = {name: 1 for name in hydrate.plan.METADATA}
    files["backup/careertrellis/" + label + "/backup.manifest"] = 1
    return {"schema": "careertrellis.pgbackrest-file-set/v1", "backup_label": label,
        "started_at": "2026-10-26T20:35:00Z", "files": files,
        "metadata_sha256": {name: hashlib.sha256(b"x").hexdigest() for name in hydrate.plan.METADATA}}


class Fake:
    def __init__(self, fail=None):
        self.events = []; self.fail = fail; self.owned = False
    def event(self, name):
        self.events.append(name)
        if name == self.fail: raise RuntimeError("private provider detail")
    @contextmanager
    def locked(self):
        self.event("lock"); self.owned = True
        try: yield
        finally: self.owned = False; self.events.append("unlock")
    def preflight(self): self.event("preflight")
    def begin(self, transaction): self.event("begin")
    def status(self, phase, result=None): self.event("status:" + phase)
    def capture(self): self.event("capture"); return generation()
    def accept_label(self, label): self.event("accept_label")
    def save_generation(self, value): self.event("save_generation")
    def copy_source(self, value): self.event("copy_source")
    def verify_source(self, value): self.event("verify_source")
    def build(self, value): self.event("build"); return {"sha256": "a"*64}
    def unchanged(self): self.event("unchanged")


class HydrateTests(unittest.TestCase):
    def test_lock_owns_complete_success_sequence(self):
        backend = Fake()
        self.assertEqual(hydrate.hydrate("a"*32, backend), {"sha256": "a"*64})
        self.assertEqual(backend.events, ["lock", "preflight", "begin", "status:capturing",
            "capture", "accept_label", "save_generation", "status:hydrating", "copy_source",
            "status:verifying", "verify_source", "status:bundling", "build", "unchanged",
            "status:complete", "unlock"])
    def test_failed_operations_never_retry_or_upload(self):
        for failure in ("capture", "accept_label", "copy_source", "verify_source", "build", "unchanged"):
            backend = Fake(failure)
            with self.subTest(failure=failure), self.assertRaisesRegex(hydrate.HydrateError,
                    "^repository_hydration_refused$"):
                hydrate.hydrate("a"*32, backend)
            self.assertEqual(backend.events.count(failure), 1)
            self.assertEqual(backend.events[-2:], ["status:failed", "unlock"])
            self.assertNotIn("status:complete", backend.events)
    def test_existing_attempt_refusal_does_not_write_failure_over_other_owner(self):
        backend = Fake("begin")
        with self.assertRaises(RuntimeError): hydrate.hydrate("a"*32, backend)
        self.assertNotIn("status:failed", backend.events)
    def test_transaction_invalid_before_any_operation(self):
        for tx in (True, "../" + "a"*32, "A"*32, "a"*31):
            backend = Fake()
            with self.assertRaises(hydrate.HydrateError): hydrate.hydrate(tx, backend)
            self.assertEqual(backend.events, [])
    def test_bad_generation_stops_before_copy(self):
        backend = Fake(); backend.capture = lambda: {"schema": "wrong"}
        with self.assertRaises(hydrate.HydrateError): hydrate.hydrate("a"*32, backend)
        self.assertNotIn("copy_source", backend.events)
        self.assertIn("status:failed", backend.events)

    def test_native_remote_only_reads_fixed_source_and_passes_lock(self):
        native = hydrate.Native()
        native.pgbackrest_inventory = types.SimpleNamespace(Remote=object)
        native.fd = 8; native.lock_fd = 9; native.check_lock = lambda: None
        remote = native.remote()
        with patch.object(hydrate, "command", return_value=b"[]") as call:
            remote.run("lsjson", hydrate.plan.SOURCE)
            self.assertEqual(call.call_args.kwargs["descriptor"], 8)
            self.assertEqual(call.call_args.kwargs["lock_descriptor"], 9)
            self.assertIn("/proc/self/fd/8", call.call_args.args[0])
            for args in (("copy", hydrate.plan.SOURCE), ("cat", "onedrive:x"),
                         ("lsjson", hydrate.plan.SOURCE + "-other")):
                with self.assertRaises(hydrate.HydrateError): remote.run(*args)
            self.assertEqual(call.call_count, 1)

    def test_native_latest_receipt_failure_or_label_drift_refuses(self):
        native = hydrate.Native()
        good = {"result": "ok", "backup_label": "20261026-155056F",
                "completed_at": "2026-10-26T16:00:00Z", "started_at": "2026-10-26T15:50:00Z"}
        receipts = {"full_backup": (good,), "differential_backup": ()}
        inventory = types.SimpleNamespace(invalid_operations=set(), receipts=receipts)
        native.backup_health = types.SimpleNamespace(scan_receipts=lambda: inventory)
        native.accept_label(good["backup_label"])
        with self.assertRaises(hydrate.HydrateError): native.accept_label("20261025-032436F")
        receipts["differential_backup"] = ({**good, "result": "failed",
            "completed_at": "2026-10-26T17:00:00Z"},)
        with self.assertRaises(hydrate.HydrateError): native.accept_label(good["backup_label"])

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
                         "requires native root-owned POSIX filesystem")
    def test_protected_native_read_ignores_atime_but_detects_replacement(self):
        with tempfile.TemporaryDirectory(prefix="retention-protected-test-", dir="/root") as temp:
            path = Path(temp) / "fixture"
            path.write_bytes(b"synthetic"); path.chmod(0o600)
            os.utime(path, (1, 1))
            before = path.stat()
            self.assertEqual(hydrate.protected(path, 0o600)[0], b"synthetic")
            self.assertGreater(path.stat().st_atime_ns, before.st_atime_ns)
            original_read = os.read
            replaced = False
            def swapping_read(fd, size):
                nonlocal replaced
                raw = original_read(fd, size)
                if not replaced:
                    replaced = True
                    successor = Path(temp) / "successor"
                    successor.write_bytes(b"synthetic"); successor.chmod(0o600)
                    os.replace(successor, path)
                return raw
            with patch.object(hydrate.os, "read", side_effect=swapping_read):
                with self.assertRaises(hydrate.HydrateError): hydrate.protected(path, 0o600)


if __name__ == "__main__": unittest.main()
