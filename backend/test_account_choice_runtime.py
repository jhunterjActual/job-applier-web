"""Loopback-only runtime smoke test with temporary synthetic SQLite storage."""
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx
import uvicorn

import app
import config
import database


class AccountChoiceRuntimeTests(unittest.TestCase):
    def test_loopback_start_version_dashboard_and_sign_in_then_clean_stop(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            root = Path(directory)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            environment = {
                "CAREERTRELLIS_ENVIRONMENT": "local",
                "CAREERTRELLIS_AUTH_MODE": "local",
                "CAREERTRELLIS_DATA_STORE": "sqlite",
                "CAREERTRELLIS_IDENTITY_STORE": "",
                "CAREERTRELLIS_PROVIDER_SECRET_STORE": "",
                "CAREERTRELLIS_OBJECT_STORE": "",
            }
            with (
                patch.dict(os.environ, environment),
                patch.object(config, "DATA_DIR", root),
                patch.object(config, "DB_PATH", root / "synthetic.db"),
                patch.object(database, "DB_PATH", root / "synthetic.db"),
                patch.object(app, "initialize_analytics"),
            ):
                server = uvicorn.Server(uvicorn.Config(app.app, log_config=None, access_log=False))
                thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
                thread.start()
                try:
                    deadline = time.monotonic() + 10
                    while not server.started and thread.is_alive() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(server.started, "Synthetic loopback server did not become ready")
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                        version = client.get("/api/version")
                        self.assertEqual(200, version.status_code)
                        self.assertEqual(app.APP_BUILD, version.json()["build"])
                        dashboard = client.get("/")
                        self.assertEqual(200, dashboard.status_code)
                        self.assertIn("CareerTrellis", dashboard.text)
                        landing = client.get("/auth/sign-in")
                        self.assertEqual(200, landing.status_code)
                        self.assertEqual("no-store, max-age=0", landing.headers["cache-control"])
                        self.assertIn("Use a different account", landing.text)
                        self.assertEqual(200, client.get("/static/maintenance-notice.css?v=2").status_code)
                        self.assertEqual(404, client.get("/auth/login?account=different").status_code)
                        self.assertEqual(404, client.post("/auth/logout").status_code)
                finally:
                    server.should_exit = True
                    thread.join(timeout=10)
                self.assertFalse(thread.is_alive(), "Synthetic loopback server did not stop cleanly")


if __name__ == "__main__":
    unittest.main()
