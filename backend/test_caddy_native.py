"""Opt-in, isolated native encoder test; never contacts staging or providers.

Run with CADDY_TEST_BINARY pointing to Caddy 2.11.4. Only synthetic HTTP data
is used, listeners bind loopback, and the owned Caddy process is always stopped.
"""

import base64
import gzip
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import caddy_log_privacy as privacy


@unittest.skipUnless(os.environ.get("CADDY_TEST_BINARY"), "native Caddy binary required")
class NativeCaddyPrivacyTests(unittest.TestCase):
    def test_real_encoder_and_proxy_contract(self):
        binary = os.environ["CADDY_TEST_BINARY"]
        version = subprocess.run([binary, "version"], capture_output=True, check=True)
        self.assertTrue(version.stdout.startswith(b"v2.11.4 "))
        synthetic = "synthetic-private@example.invalid"
        observed = []

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/runtime-error":
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if self.path != "/health/ready":
                    observed.append(dict(self.headers))
                self.send_response(200)
                self.send_header("X-Unfamiliar-Response", synthetic)
                self.send_header("Set-Cookie", "synthetic-cookie")
                self.end_headers()
                self.wfile.write(b"synthetic-ok")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        source = (Path(__file__).resolve().parent.parent / "deploy/caddy/Caddyfile.staging").read_text()
        password = "synthetic-test-only"
        hashed = subprocess.run(
            [binary, "hash-password", "--algorithm", "argon2id", "--plaintext", password],
            capture_output=True, check=True,
        ).stdout.decode().strip()
        with tempfile.TemporaryDirectory(prefix="careertrellis-caddy-test-") as tmp:
            root = Path(tmp)
            access = root / "access.log"
            config = root / "Caddyfile"
            runtime = root / "runtime.log"
            fixture = source.replace("{\n", "{\n\tadmin off\n\tauto_https off\n\tpersist_config off\n", 1)
            fixture = fixture.replace("staging-app.jhunterj.com {", f"http://127.0.0.1:{port} {{")
            fixture = fixture.replace("127.0.0.1:8001", f"127.0.0.1:{server.server_port}")
            self.assertIn("access-v7.log", source)
            fixture = fixture.replace("/var/log/caddy/careertrellis-staging-access-v7.log", str(access))
            # Accelerate only the isolated writer's rotation clock, not filters.
            fixture = fixture.replace("roll_size 10MiB", "roll_size 10MiB\n\t\t\troll_interval 1s")
            config.write_text(fixture)
            env = dict(os.environ, STAGING_BASIC_AUTH_USER="synthetic-owner",
                       STAGING_BASIC_AUTH_HASH=hashed, STAGING_BASIC_AUTH_PILOT_01_HASH=hashed,
                       CAREERTRELLIS_TRUSTED_PROXY_TOKEN="synthetic-origin-token")
            opener = build_opener(ProxyHandler({}))

            def request(path, headers=None):
                try:
                    response = opener.open(Request(f"http://127.0.0.1:{port}{path}", headers=headers or {}), timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, response.read(), response.headers

            with runtime.open("wb") as log:
                process = subprocess.Popen([binary, "run", "--config", str(config), "--adapter", "caddyfile"],
                                           env=env, stdout=log, stderr=log)
                try:
                    for attempt in range(50):
                        try:
                            if request("/auth/readiness")[0] == 401:
                                break
                        except (URLError, TimeoutError):
                            time.sleep(.1)
                    else:
                        self.fail("isolated Caddy did not become ready")
                    hostile = {"X-Unfamiliar-Client": synthetic, "Accept-Charset": "synthetic-charset",
                               "X-Request-ID": "synthetic-request", "Accept": synthetic,
                               "User-Agent": synthetic, "Origin": "https://synthetic.invalid",
                               "X-CareerTrellis-Proxy-Token": "synthetic-spoof"}
                    self.assertEqual(401, request("/api/version?view=compact", hostile)[0])
                    hostile["Authorization"] = "Basic " + base64.b64encode(b"wrong:wrong").decode()
                    self.assertEqual(401, request("/api/version", hostile)[0])
                    for user in ("synthetic-owner", "pilot-01"):
                        hostile["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
                        status, body, headers = request("/api/version?view=compact", hostile)
                        self.assertEqual((200, b"synthetic-ok"), (status, body))
                        self.assertEqual(synthetic, headers["X-Unfamiliar-Response"])
                        self.assertEqual("same-origin", headers["Referrer-Policy"])
                    self.assertEqual(200, request("/auth/callback?state=synthetic-state", hostile)[0])
                    self.assertEqual(200, request("/%61uth/callback?state=synthetic-state", hostile)[0])
                    self.assertEqual(502, request("/runtime-error", hostile)[0])
                    self.assertTrue(observed)
                    for headers in observed:
                        lowered = {key.lower(): value for key, value in headers.items()}
                        self.assertEqual("synthetic-origin-token", lowered["x-careertrellis-proxy-token"])
                        self.assertEqual("https://synthetic.invalid", lowered["origin"])
                        self.assertEqual(synthetic, lowered["x-unfamiliar-client"])
                    time.sleep(1.1)
                    self.assertEqual(401, request("/rotation-control")[0])
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            rotations = list(root.glob("access-*.log")) + list(root.glob("access-*.log.gz"))
            self.assertTrue(rotations, "native writer must actually rotate")
            lines = access.read_text().splitlines()
            for rotated in rotations:
                if rotated.suffix == ".gz":
                    with gzip.open(rotated, "rt") as handle:
                        lines.extend(handle.read().splitlines())
                else:
                    lines.extend(rotated.read_text().splitlines())
            records = [json.loads(line) for line in lines]
            self.assertEqual(6, len(records), "auth routes must not be logged")
            summary = privacy.scan_json_lines(lines)
            self.assertEqual(6, summary.representative_records)
            for record in records:
                self.assertNotIn("headers", record["request"])
                self.assertNotIn("resp_headers", record)
                self.assertNotIn("user_id", record)
            self.assertNotIn(synthetic, "\n".join(lines))
            self.assertNotIn(synthetic, runtime.read_text())
            runtime_requests = 0
            for line in runtime.read_text().splitlines():
                record = json.loads(line)
                if "request" in record:
                    runtime_requests += 1
                    self.assertNotIn("headers", record["request"])
                    self.assertNotIn("uri", record["request"])
                    self.assertNotIn("resp_headers", record)
                    self.assertNotIn("user_id", record)
                    for field in ("remote_ip", "client_ip"):
                        if field in record["request"]:
                            self.assertRegex(record["request"][field], privacy.OPAQUE_HASH_PATTERN)
            self.assertGreater(runtime_requests, 0, "exercise runtime HTTP error logger")
            # Use real encoder records in both retained rotation representations.
            config.write_text(source)
            plain = root / "rotation.log"
            compressed = root / "rotation.log.gz"
            plain.write_text(lines[0] + "\n")
            with gzip.open(compressed, "wt") as handle:
                handle.write(lines[1] + "\n")
            with patch("sys.stdout", io.StringIO()) as output:
                args = ["--config", str(config), "--log", str(access),
                        "--log", str(plain), "--log", str(compressed)]
                for rotated in rotations:
                    args.extend(("--log", str(rotated)))
                result = privacy.main(args)
            self.assertEqual(0, result)
            self.assertIn("records=8", output.getvalue())


if __name__ == "__main__":
    unittest.main()
