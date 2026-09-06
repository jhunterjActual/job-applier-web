from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app as app_module
import maintenance_notice as notices
from web_auth import SessionAuthentication


NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def example_notice() -> dict:
    return {
        "message": "Scheduled service work. Please save your changes beforehand.",
        "impact": "Search and tailoring may be unavailable during this window.",
        "status": "planned", "time_zone": "America/New_York",
        "publish_at": "2026-09-06T11:00:00Z", "starts_at": "2026-09-06T14:00:00Z",
        "ends_at": "2026-09-06T15:00:00Z", "expires_at": "2026-09-06T15:15:00Z",
    }


class NoticeStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "current.json"

    def update(self, operation="set", notice=None):
        return notices.update_notice(operation, notice if operation == "disable" else (notice or example_notice()),
                                     path=self.path, strict=False, now=NOW)

    def read(self, now=NOW):
        return notices.public_notice(self.path, strict=False, now=now)

    def test_missing_scheduled_active_expired_and_disabled(self):
        self.assertIsNone(self.read()["notice"])
        self.update()
        self.assertIsNone(self.read(NOW - timedelta(hours=2))["notice"])
        self.assertEqual(example_notice(), self.read()["notice"])
        self.assertIsNone(self.read(NOW + timedelta(hours=4))["notice"])
        self.assertIsNone(self.read(datetime(2026, 9, 6, 15, 15, tzinfo=timezone.utc))["notice"])
        self.update("disable")
        self.assertIsNone(self.read()["notice"])

    def test_updates_audit_atomically_without_public_audit_or_old_content(self):
        first = self.update()
        updated = {**example_notice(), "message": "Updated maintenance window.", "status": "in_progress"}
        second = self.update(notice=updated)
        state = notices.read_state(self.path, strict=False)
        self.assertNotEqual(first, second)
        self.assertEqual(["set", "set"], [event["action"] for event in state["audit"]])
        self.assertEqual({"notice", "server_time"}, set(self.read()))
        self.assertNotIn(example_notice()["message"], self.path.read_text())
        self.assertNotIn("message", json.dumps(state["audit"]))
        self.assertEqual([], list(self.path.parent.glob(".notice-*")))

    def test_invalid_content_timestamps_zones_and_unknown_fields_preserve_old_state(self):
        self.update()
        before = self.path.read_bytes()
        invalid = [
            {"message": "<script>alert(1)</script>"}, {"message": "hello\nworld"},
            {"message": "visit https://attacker.example"}, {"message": "\u202econcealed"},
            {"message": "x" * 401}, {"impact": " "}, {"status": "offline"},
            {"time_zone": "../UTC"}, {"time_zone": "Not/AZone"},
            {"publish_at": "2026-09-06T16:00:00Z"}, {"starts_at": "2026-09-06T16:00:00Z"},
            {"ends_at": "2026-09-06T15:00:00"}, {"expires_at": "2026-09-06T14:30:00Z"},
            {"expires_at": "2026-09-09T15:00:00Z"}, {"secret": "must-not-appear"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(notices.NoticeError):
                self.update(notice={**example_notice(), **changes})
            self.assertEqual(before, self.path.read_bytes())
        with self.assertRaises(notices.NoticeError):
            notices.decode(b'{"message":"one","message":"two"}')

    def test_offset_times_are_normalized_and_naive_or_impossible_times_rejected(self):
        changed = {**example_notice(), "starts_at": "2026-09-06T10:00:00-04:00"}
        self.assertEqual(example_notice(), notices.validate_notice(changed))
        for value in ("2026-99-01T12:00:00Z", "2026-09-06T12:00:00", "tomorrow", None):
            with self.subTest(value=value), self.assertRaises(notices.NoticeError):
                notices.timestamp(value)

    def test_failed_replace_keeps_old_state_and_cleans_only_new_temporary_file(self):
        self.update()
        before = self.path.read_bytes()
        with patch.object(notices.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                self.update("disable")
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], list(self.path.parent.glob(".notice-*")))

    def test_audit_capacity_refuses_to_drop_history(self):
        self.update()
        before = self.path.read_bytes()
        with patch.object(notices, "MAX_EVENTS", 1), self.assertRaises(notices.NoticeError):
            self.update("disable")
        self.assertEqual(before, self.path.read_bytes())

    def test_symlink_hardlink_nonregular_oversized_and_unsafe_metadata_rejected(self):
        self.update()
        metadata = self.path.stat()
        for mode, uid, links, size in (
            (stat.S_IFREG | 0o666, 0, 1, metadata.st_size),
            (stat.S_IFREG | 0o644, 1000, 1, metadata.st_size),
            (stat.S_IFREG | 0o644, 0, 2, metadata.st_size),
            (stat.S_IFIFO | 0o644, 0, 1, metadata.st_size),
            (stat.S_IFREG | 0o644, 0, 1, notices.MAX_BYTES + 1),
        ):
            from types import SimpleNamespace
            with self.subTest(mode=mode, uid=uid, links=links, size=size), patch.object(
                notices, "_safe_parent"
            ), patch.object(notices.os, "fstat", return_value=SimpleNamespace(
                st_mode=mode, st_uid=uid, st_nlink=links, st_size=size
            )), self.assertRaises(notices.NoticeError):
                notices.read_state(self.path)
        link = self.path.parent / "link.json"
        try:
            link.symlink_to(self.path)
        except OSError:
            self.skipTest("Host cannot create symlinks")
        if hasattr(os, "O_NOFOLLOW"):
            with self.assertRaises(OSError):
                notices.read_state(link, strict=False)

    def test_cli_rejects_nonroot_and_unknown_arguments_with_fixed_errors(self):
        for uid, arguments in ((1000, ["disable"]), (0, ["set", "extra"]), (0, ["delete"])):
            with patch.object(os, "geteuid", return_value=uid, create=True), patch("sys.stderr", new_callable=io.StringIO) as error:
                self.assertEqual(1, notices.main(arguments))
            self.assertEqual(notices.ERROR + "\n", error.getvalue())


class NoticeRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app, base_url="http://127.0.0.1", follow_redirects=False)
        self.addCleanup(self.client.close)

    def test_only_notice_landing_and_exact_assets_are_public_and_not_cached(self):
        with patch.object(app_module, "get_auth_configuration", return_value=object()), patch.object(
            app_module, "authenticate_session", return_value=SessionAuthentication("unauthenticated")
        ) as authenticate, patch.object(notices, "public_notice", return_value={"notice": None, "server_time": notices.stamp(NOW)}):
            for path in ("/auth/sign-in", "/api/maintenance-notice", "/static/maintenance-notice.js", "/static/maintenance-notice.css"):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(200, response.status_code)
                    self.assertIn("no-store", response.headers["cache-control"])
            authenticate.assert_not_called()
            for path in ("/api/profile", "/static/app.js", "/static/index.html", "/static/sign-in.html"):
                self.assertEqual(401, self.client.get(path).status_code)
            response = self.client.get("/")
            self.assertEqual(303, response.status_code)
            self.assertEqual("/auth/sign-in", response.headers["location"])

    def test_read_failures_are_fixed_and_no_write_route_exists(self):
        with patch.object(app_module, "get_auth_configuration", return_value=None), patch.object(
            notices, "public_notice", side_effect=ValueError("private-path secret-canary")
        ):
            response = self.client.get("/api/maintenance-notice")
            self.assertEqual(503, response.status_code)
            self.assertNotIn("secret-canary", response.text)
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertEqual(405, self.client.post("/api/maintenance-notice", json=example_notice()).status_code)

    def test_public_notice_still_requires_trusted_host_and_proxy(self):
        self.assertEqual(400, self.client.get("/api/maintenance-notice", headers={"host": "evil.test"}).status_code)
        with patch.dict(os.environ, {"CAREERTRELLIS_ENVIRONMENT": "staging", "CAREERTRELLIS_ALLOWED_HOSTS": "staging.example"}):
            self.assertEqual(403, self.client.get("/api/maintenance-notice", headers={"host": "staging.example"}).status_code)


if __name__ == "__main__":
    unittest.main()
