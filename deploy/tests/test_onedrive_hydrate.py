"""Offline state-machine tests; native trust boundaries require Linux acceptance."""
from contextlib import contextmanager
import hashlib
import importlib
import json
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
    def test_capture_projects_sizes_from_same_private_info_without_extra_command(self):
        value = generation()
        private_info = [{'name': 'careertrellis', 'status': {'code': 0}, 'backup': [
            {'label': value['backup_label'], 'timestamp': {'stop': 1},
             'info': {'size': 12345}, 'error': False}],
             'ignored_private_metadata': 'PRIVATE_SYNTHETIC_DETAIL'}]
        native = hydrate.Native(); native.lock_fd = 9
        native.accept_label = unittest.mock.Mock(); native.remote = unittest.mock.Mock(return_value=object())
        prepare = unittest.mock.Mock(return_value=value)
        native.pgbackrest_inventory = types.SimpleNamespace(prepare=prepare)
        with patch.object(hydrate, 'command', return_value=json.dumps(private_info).encode()) as command:
            self.assertEqual(native.capture(), value)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(native.source_sizes['selected_database_bytes'], 12345)
        self.assertEqual(native.source_sizes['full_ancestor_database_bytes'], 12345)
        self.assertNotIn('PRIVATE_SYNTHETIC_DETAIL', repr(native.source_sizes))
        native.accept_label.assert_called_once_with(value['backup_label'])

    def test_save_generation_writes_bound_private_size_facts_without_raw_info(self):
        native = hydrate.Native(); native.root = Path('/fixed/private')
        native.base = {'transaction': 'a' * 32}
        value = generation()
        native.source_sizes = {'backup_label': value['backup_label'],
            'full_ancestor_label': value['backup_label'], 'selected_database_bytes': 12345,
            'full_ancestor_database_bytes': 12345, 'observed_at': value['started_at']}
        with patch.object(hydrate, 'exclusive') as write, \
                patch('shutil.disk_usage', return_value=types.SimpleNamespace(free=10 ** 12)):
            native.save_generation(value)
        outputs = {call.args[0].name: call.args[1] for call in write.call_args_list}
        self.assertEqual(set(outputs), {'generation.json', 'source-database-size.json', 'files.txt'})
        facts = hydrate.builder.decode(outputs['source-database-size.json'], 8192)
        self.assertEqual(facts['schema'], hydrate.builder.SIZE_SCHEMA)
        self.assertEqual(facts['source'], 'pgbackrest_info')
        self.assertEqual(facts['transaction'], 'a' * 32)
        self.assertEqual(facts['generation_sha256'], hydrate.builder.digest(value))
        self.assertEqual(facts['selected_database_bytes'], 12345)
        self.assertEqual(facts['full_ancestor_label'], value['backup_label'])

    def test_missing_or_mismatched_size_facts_refuse_before_any_output(self):
        for sizes in (None, {'backup_label': '20261025-155056F'}):
            native = hydrate.Native(); native.root = Path('/fixed/private')
            native.base = {'transaction': 'a' * 32}; native.source_sizes = sizes
            with patch.object(hydrate, 'exclusive') as write:
                with self.assertRaises(ValueError): native.save_generation(generation())
                write.assert_not_called()

    def test_unproved_size_does_not_prepare_remote_generation(self):
        native = hydrate.Native(); native.lock_fd = 9
        native.accept_label = unittest.mock.Mock()
        native.pgbackrest_inventory = types.SimpleNamespace(prepare=unittest.mock.Mock())
        malformed = [{'name': 'careertrellis', 'status': {'code': 0}, 'backup': [
            {'label': generation()['backup_label'], 'timestamp': {'stop': 1}, 'info': {'size': True}}]}]
        with patch.object(hydrate, 'command', return_value=json.dumps(malformed).encode()):
            with self.assertRaises(hydrate.builder.BuilderRefused): native.capture()
        native.accept_label.assert_not_called(); native.pgbackrest_inventory.prepare.assert_not_called()

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
        native.unchanged = lambda: None
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

    def test_copy_source_limits_size_and_transfer_and_retries(self):
        native = hydrate.Native(); native.root = Path("/fixed/task")
        native.fd = 8; native.lock_fd = 9; native.unchanged = lambda: None
        value = generation()
        with patch.object(hydrate, "command") as call:
            native.copy_source(value)
        args = call.call_args.args[0]
        for option, expected in (("--max-size", "1B"),
                ("--max-transfer", str(sum(value["files"].values())) + "B"),
                ("--cutoff-mode", "HARD"), ("--transfers", "2"), ("--checkers", "4"),
                ("--retries", "1"), ("--low-level-retries", "1"),
                ("--contimeout", "15s"), ("--timeout", "45s")):
            self.assertEqual(args[args.index(option)+1], expected)

    def test_binary_identity_drift_refuses_without_secret_hashing(self):
        native = hydrate.Native(); native.config = (b"synthetic", (1,))
        native.binary_identity = (0,)*9; native.check_lock = lambda: None
        with patch.object(hydrate, "protected", return_value=native.config), \
                patch.object(hydrate.Path, "lstat", return_value=types.SimpleNamespace(
                    st_dev=1, st_ino=0, st_mode=0, st_uid=0, st_gid=0, st_nlink=0,
                    st_size=0, st_mtime_ns=0, st_ctime_ns=0)):
            with self.assertRaises(hydrate.HydrateError): native.unchanged()

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
