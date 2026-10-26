"""Offline publisher gates; native/provider acceptance remains separate."""
from contextlib import contextmanager
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
publish = importlib.import_module("deploy.onedrive_retention.publish")


class Fake:
    def __init__(self, failure=None, free=2000000000):
        self.events = []; self.failure = failure; self.free = free
    def event(self, name):
        self.events.append(name)
        if name == self.failure: raise RuntimeError("private provider detail")
    @contextmanager
    def locked(self):
        self.event("lock")
        try: yield
        finally: self.events.append("unlock")
    def preflight(self): self.event("preflight")
    def candidate(self, tx): self.event("candidate"); return {"archive_bytes": 100000000}
    def begin_publish(self, value): self.event("begin")
    def capacity_facts(self, value):
        self.event("capacity"); return {"total": 5000000000, "free": self.free, "namespace_bytes": 0}
    def status(self, phase, result=None): self.event("status:"+phase)
    def upload(self, value): self.event("upload")
    def download(self, value): self.event("download")
    def verify_download(self, value): self.event("verify"); return {"manifest": "synthetic"}
    def unchanged(self): self.event("unchanged")
    def quota(self): self.event("quota"); return {"total": 5000000000, "free": self.free}
    def receipt(self, *args): self.event("receipt"); return "a"*64


class PublishTests(unittest.TestCase):
    def test_success_requires_download_and_never_claims_native_restore(self):
        backend = Fake(); value = publish.publish("a"*32, backend)
        self.assertFalse(value["native_restore_accepted"])
        self.assertEqual(backend.events, ["lock", "preflight", "candidate", "begin", "capacity",
            "status:uploading", "upload", "status:downloading", "download", "status:verifying_download",
            "verify", "unchanged", "quota", "unchanged", "receipt", "status:complete", "unlock"])

    def test_capacity_denial_preserves_intent_without_upload(self):
        backend = Fake(free=1199999999)
        with self.assertRaises(publish.PublishError): publish.publish("a"*32, backend)
        self.assertNotIn("upload", backend.events)
        self.assertIn("begin", backend.events); self.assertIn("status:failed", backend.events)

    def test_no_replay_or_receipt_after_failure_or_changed_config(self):
        for failure in ("upload", "download", "verify", "unchanged"):
            backend = Fake(failure)
            with self.subTest(failure=failure), self.assertRaisesRegex(publish.PublishError,
                    "^candidate_publication_refused$"):
                publish.publish("a"*32, backend)
            self.assertEqual(backend.events.count(failure), 1)
            self.assertNotIn("receipt", backend.events)
            self.assertNotIn("status:complete", backend.events)

    def test_existing_intent_never_updates_other_attempt(self):
        backend = Fake("begin")
        with self.assertRaises(RuntimeError): publish.publish("a"*32, backend)
        self.assertNotIn("status:failed", backend.events)
        self.assertNotIn("upload", backend.events)

    def test_parent_listing_failure_never_means_empty_namespace(self):
        native = publish.Native()
        with patch.object(native, "read_remote", side_effect=RuntimeError("private")):
            with self.assertRaises(RuntimeError): native.capacity_facts({"archive_sha256": "a"*64})

    def test_namespace_counts_all_files_and_refuses_existing_candidate(self):
        native = publish.Native()
        import json
        parent = json.dumps([{"Path": "postgresql-bundles", "IsDir": True}]).encode()
        rows = json.dumps([{"Path": "a"*64+"/repository.tar", "IsDir": False, "Size": 25}]).encode()
        with patch.object(native, "read_remote", side_effect=[parent, rows]), patch.object(native,
                "quota", return_value={"total": 5000000000, "free": 2000000000}):
            result = native.capacity_facts({"archive_sha256": "b"*64})
            self.assertEqual(result["namespace_bytes"], 25)
        with patch.object(native, "read_remote", side_effect=[parent, rows]):
            with self.assertRaises(publish.PublishError): native.capacity_facts({"archive_sha256": "a"*64})

    def test_case_insensitive_namespace_alias_or_duplicate_refuses(self):
        native = publish.Native()
        import json
        for rows in ([{"Path": "PostgreSQL-Bundles", "IsDir": True}],
                     [{"Path": "postgresql-bundles", "IsDir": True}]*2):
            with patch.object(native, "read_remote", return_value=json.dumps(rows).encode()):
                with self.assertRaises(publish.PublishError):
                    native.capacity_facts({"archive_sha256": "a"*64})

    def test_conservative_two_version_bound_uses_four_candidate_peak(self):
        backend = Fake(free=1399999999)
        with self.assertRaises(publish.PublishError): publish.publish("a"*32, backend)
        self.assertNotIn("upload", backend.events)

    def test_transaction_validation_precedes_all_io(self):
        backend = Fake()
        with self.assertRaises(publish.PublishError): publish.publish("../"+"a"*32, backend)
        self.assertEqual(backend.events, [])

    def test_reserve_breach_after_download_refuses_receipt(self):
        backend = Fake()
        backend.quota = lambda: {"total": 5000000000, "free": 999999999}
        with self.assertRaises(publish.PublishError): publish.publish("a"*32, backend)
        self.assertIn("download", backend.events)
        self.assertNotIn("receipt", backend.events)

    def test_encryption_gate_requires_explicit_cipher_and_nonempty_passphrase(self):
        good = b"[global]\nrepo1-cipher-type=aes-256-cbc\nrepo1-cipher-pass=synthetic-fixture\n"
        self.assertIsNone(publish.require_encryption(good))
        for bad in (good.replace(b"aes-256-cbc", b"none"),
                    good.replace(b"synthetic-fixture", b" "),
                    good + b"[careertrellis]\nrepo1-cipher-type=none\n",
                    good + b"[global:backup]\nrepo1-cipher-type=none\n"):
            with self.assertRaises(publish.PublishError): publish.require_encryption(bad)

    def test_native_transfer_caps_and_secret_safe_descriptor_inheritance(self):
        native = publish.Native(); native.root = Path("/fixed/task")
        native.base = {"boot_id": "synthetic"}; native.fd = 8; native.lock_fd = 9
        native.unchanged = lambda: None
        child = types.SimpleNamespace(pid=123, returncode=0, wait=lambda **kw: 0,
                                      poll=lambda: 0)
        manager = unittest.mock.MagicMock()
        manager.__enter__.return_value = child
        with patch.object(publish.subprocess, "Popen", return_value=manager) as call, \
                patch.object(publish.hydrate, "exclusive"), \
                patch.object(publish.Path, "read_text", return_value="123 (fixture) " + "0 "*20), \
                patch.object(publish.os.path, "lexists", return_value=True):
            native.transfer("upload", "/fixed/task/repository.tar", "onedrive:fixed", True)
        args = call.call_args.args[0]
        for option, expected in (("--max-size", "512M"), ("--max-transfer", "512M"),
                ("--cutoff-mode", "HARD"), ("--transfers", "1"), ("--retries", "1"),
                ("--low-level-retries", "1"), ("--contimeout", "15s"), ("--timeout", "45s")):
            self.assertEqual(args[args.index(option)+1], expected)
        self.assertIn("--onedrive-hard-delete=false", args)
        self.assertEqual(call.call_args.kwargs["pass_fds"], (8, 9))
        self.assertEqual(call.call_args.kwargs["stderr"], publish.subprocess.DEVNULL)


if __name__ == "__main__": unittest.main()
