"""Host-native root CLI tests using only a disposable synthetic directory."""
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import maintenance_notice as notices


@unittest.skipUnless(os.name == "posix" and getattr(os, "geteuid", lambda: -1)() == 0,
                     "Requires a disposable Linux root test environment")
class RootNoticeCliTests(unittest.TestCase):
    def setUp(self):
        # /tmp is deliberately writable by other users and must fail the real
        # ancestor contract. Use a new root-owned directory below /root instead.
        temporary = tempfile.TemporaryDirectory(prefix="notice-test-", dir="/root")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "announcements"
        self.path = self.root / "current.json"
        self.root_patch = patch.object(notices, "NOTICE_ROOT", self.root)
        self.path_patch = patch.object(notices, "NOTICE_FILE", self.path)
        self.root_patch.start()
        self.path_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.path_patch.stop)
        now = datetime.now(timezone.utc)
        self.notice = {"message": "Synthetic maintenance.", "impact": "Synthetic test only.",
                       "status": "planned", "time_zone": "UTC",
                       "publish_at": notices.stamp(now - timedelta(minutes=1)),
                       "starts_at": notices.stamp(now + timedelta(hours=1)),
                       "ends_at": notices.stamp(now + timedelta(hours=2)),
                       "expires_at": notices.stamp(now + timedelta(hours=3))}

    def cli(self, operation, payload=b""):
        stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with stdin, patch("sys.stdin", stdin), patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            code = notices.main([operation])
        return code, stdout.getvalue(), stderr.getvalue()

    def publish(self):
        code, output, error = self.cli("set", json.dumps(self.notice).encode())
        self.assertEqual(0, code, error)
        self.assertNotIn("Synthetic", output)
        self.assertEqual("ok", json.loads(output)["result"])

    def test_set_inspect_disable_and_permissions(self):
        self.assertEqual(0, self.cli("inspect")[0])
        self.assertFalse(self.root.exists(), "inspect must not create state or lock files")
        self.publish()
        self.assertEqual(0o644, self.path.stat().st_mode & 0o777)
        self.assertEqual(0, self.path.stat().st_uid)
        self.assertEqual(self.notice, notices.public_notice(self.path)["notice"])
        self.assertEqual(1, json.loads(self.cli("inspect")[1])["audit_events"])
        self.assertEqual(0, self.cli("disable")[0])
        self.assertIsNone(notices.public_notice(self.path)["notice"])
        self.assertEqual(2, len(notices.read_state(self.path)["audit"]))

    def test_lock_contention_and_oversized_input_leave_current_state_unchanged(self):
        import fcntl
        self.publish()
        before = self.path.read_bytes()
        with (self.root / ".operator.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual((1, "", notices.ERROR + "\n"), self.cli("disable"))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual((1, "", notices.ERROR + "\n"), self.cli("set", b" " * 4097))
        self.assertEqual(before, self.path.read_bytes())

    def test_symlink_fifo_and_writable_parent_fail_closed(self):
        self.publish()
        original = self.root / "retained.json"
        self.path.rename(original)
        self.path.symlink_to(original)
        self.assertEqual((1, "", notices.ERROR + "\n"), self.cli("disable"))
        self.path.unlink()
        os.mkfifo(self.path, 0o644)
        self.assertEqual((1, "", notices.ERROR + "\n"), self.cli("inspect"))
        self.path.unlink()
        original.rename(self.path)
        self.root.chmod(0o777)
        self.assertEqual((1, "", notices.ERROR + "\n"), self.cli("inspect"))


if __name__ == "__main__":
    unittest.main()
