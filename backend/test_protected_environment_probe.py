from __future__ import annotations

import base64
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pilot_acceptance as acceptance
import protected_environment_probe as probe


class ProtectedEnvironmentProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        keyring = base64.urlsafe_b64encode(json.dumps({
            "test-key": base64.urlsafe_b64encode(b"k" * 32).decode("ascii"),
        }).encode("ascii")).decode("ascii")
        self.documents = {
            "STAGING_ENVIRONMENT_FILE": {
                acceptance.OWNER_ID_KEY: "user_synthetic000",
                acceptance.RUNTIME_ENVIRONMENT_KEY: "staging",
            },
            "MIGRATION_ENVIRONMENT_FILE": {
                acceptance.MIGRATION_URL_KEY:
                    "postgresql://careertrellis_migration:synthetic@127.0.0.1/careertrellis",
            },
            "RUNTIME_ENVIRONMENT_FILE": {
                acceptance.RUNTIME_URL_KEY:
                    "postgresql://careertrellis_runtime:synthetic@127.0.0.1/careertrellis",
            },
            "PROVIDER_ENVIRONMENT_FILE": dict(zip(acceptance.PROVIDER_KEYS, (
                "postgres_encrypted", "test-key", keyring,
            ))),
            "OBJECT_ENVIRONMENT_FILE": dict(zip(acceptance.OBJECT_KEYS, (
                "r2_encrypted", f"https://{'a' * 32}.r2.cloudflarestorage.com",
                "synthetic-bucket", "synthetic-access-key",
                "synthetic-secret-key-value-00000000", "test-key", keyring,
            ))),
        }
        self.paths = {}
        for index, (name, document) in enumerate(self.documents.items()):
            path = root / f"configuration-{index}"
            self.paths[name] = path
            self.write_document(name, document)
            self.stack.enter_context(patch.object(acceptance, name, path))
        original = acceptance.read_protected_environment
        self.stack.enter_context(patch.object(
            acceptance, "read_protected_environment",
            side_effect=lambda path, keys: original(path, keys, strict_metadata=False),
        ))
        self.guards = []
        for target in (
            "pilot_acceptance.create_engine", "sqlalchemy.create_engine",
            "boto3.client", "psycopg.connect", "socket.socket",
            "pilot_acceptance.StateStore", "pilot_acceptance._run_operation",
            "pilot_acceptance._interactive_identity",
        ):
            self.guards.append(self.stack.enter_context(patch(
                target, side_effect=AssertionError("probe must stay offline and read-only"),
            )))
        self.stack.enter_context(patch.object(os, "geteuid", return_value=0, create=True))

    def write_document(self, name: str, document: dict[str, str]) -> None:
        self.paths[name].write_text(
            "".join(f"{key}={value}\n" for key, value in document.items())
            + "SENTRY_DSN=\nOPTIONAL_TELEMETRY=\n",
            encoding="utf-8",
        )

    def run_probe(self, arguments: list[str] | None = None) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            result = probe.main([] if arguments is None else arguments)
        for guard in self.guards:
            guard.assert_not_called()
        return result, stdout.getvalue(), stderr.getvalue()

    def test_real_validators_accept_optional_blanks_without_io_or_state_changes(self) -> None:
        before_environment = dict(os.environ)
        before_files = {name: path.read_bytes() for name, path in self.paths.items()}
        self.assertEqual((0, probe.FIXED_SUCCESS + "\n", ""), self.run_probe())
        self.assertEqual(before_environment, dict(os.environ))
        self.assertEqual(before_files, {
            name: path.read_bytes() for name, path in self.paths.items()
        })

    def test_every_required_key_rejects_missing_empty_duplicate_and_whitespace(self) -> None:
        for name, document in self.documents.items():
            for key in document:
                for defect in ("missing", "empty", "duplicate", "whitespace"):
                    with self.subTest(name=name, key=key, defect=defect):
                        edited = dict(document)
                        if defect == "missing":
                            del edited[key]
                        elif defect == "empty":
                            edited[key] = ""
                        elif defect == "whitespace":
                            edited[key] += "\tsecret-canary"
                        self.write_document(name, edited)
                        if defect == "duplicate":
                            with self.paths[name].open("a", encoding="utf-8") as stream:
                                stream.write(f"{key}=secret-canary\n")
                        self.assertEqual(
                            (1, "", probe.FIXED_ERROR + "\n"), self.run_probe(),
                        )
                        self.write_document(name, document)

    def test_invalid_shapes_are_sanitized(self) -> None:
        cases = (
            ("STAGING_ENVIRONMENT_FILE", acceptance.OWNER_ID_KEY, "secret-canary"),
            ("STAGING_ENVIRONMENT_FILE", acceptance.RUNTIME_ENVIRONMENT_KEY, "local"),
            ("MIGRATION_ENVIRONMENT_FILE", acceptance.MIGRATION_URL_KEY, "sqlite:///secret-canary"),
            ("RUNTIME_ENVIRONMENT_FILE", acceptance.RUNTIME_URL_KEY,
             "postgresql://wrong:secret-canary@127.0.0.1/careertrellis"),
            ("RUNTIME_ENVIRONMENT_FILE", acceptance.RUNTIME_URL_KEY,
             "postgresql://careertrellis_runtime@127.0.0.1/careertrellis"),
            ("PROVIDER_ENVIRONMENT_FILE", acceptance.PROVIDER_KEYS[0], "sqlite"),
            ("PROVIDER_ENVIRONMENT_FILE", acceptance.PROVIDER_KEYS[1], "missing-key"),
            ("PROVIDER_ENVIRONMENT_FILE", acceptance.PROVIDER_KEYS[2], "secret-canary"),
            ("OBJECT_ENVIRONMENT_FILE", acceptance.OBJECT_KEYS[0], "filesystem"),
            ("OBJECT_ENVIRONMENT_FILE", acceptance.OBJECT_KEYS[1], "https://example.com"),
            ("OBJECT_ENVIRONMENT_FILE", acceptance.OBJECT_KEYS[2], "INVALID_BUCKET"),
            ("OBJECT_ENVIRONMENT_FILE", acceptance.OBJECT_KEYS[3], "short"),
            ("OBJECT_ENVIRONMENT_FILE", acceptance.OBJECT_KEYS[4], "short"),
            ("OBJECT_ENVIRONMENT_FILE", acceptance.OBJECT_KEYS[5], "missing-key"),
            ("OBJECT_ENVIRONMENT_FILE", acceptance.OBJECT_KEYS[6], "secret-canary"),
        )
        for name, key, value in cases:
            with self.subTest(name=name, key=key):
                self.write_document(name, {**self.documents[name], key: value})
                before_environment = dict(os.environ)
                self.assertEqual((1, "", probe.FIXED_ERROR + "\n"), self.run_probe())
                self.assertEqual(before_environment, dict(os.environ))
                self.write_document(name, self.documents[name])

    def test_cli_requires_root_no_arguments_and_maps_unexpected_failure(self) -> None:
        with patch.object(probe, "check_configuration") as check:
            with patch.object(os, "geteuid", return_value=1000):
                self.assertEqual((1, "", probe.FIXED_ERROR + "\n"), self.run_probe())
            self.assertEqual((1, "", probe.FIXED_ERROR + "\n"), self.run_probe(["secret-canary"]))
            check.assert_not_called()
        with patch.object(probe, "check_configuration", side_effect=RuntimeError("secret-canary")):
            self.assertEqual((1, "", probe.FIXED_ERROR + "\n"), self.run_probe())

    def test_cold_probe_imports_do_not_write_create_directories_or_connect(self) -> None:
        # Unlike in-process mocks, this guard is installed before any application
        # module is imported. All write attempts (including bytecode) fail the test.
        script = r'''
import os, sys
from unittest.mock import patch
events = []
def guard(event, args):
    forbidden = event in {
        "os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.chmod",
        "os.chown", "os.truncate", "os.link", "os.symlink", "os.utime",
        "socket.connect", "socket.getaddrinfo", "socket.sendto",
        "sqlite3.connect", "subprocess.Popen",
    }
    if event == "socket.bind":
        # urllib3 detects local IPv6 support at import via bind(::1, 0).
        # This sends nothing and creates no client; all other binds are forbidden.
        forbidden = args[1] != ("::1", 0)
    if event == "open":
        mode, flags = args[1], args[2]
        forbidden = bool((isinstance(mode, str) and any(c in mode for c in "wax+"))
                         or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
    if forbidden:
        events.append(event)
        raise RuntimeError("mutation or network attempted")
sys.addaudithook(guard)
import protected_environment_probe as probe
with patch.object(os, "geteuid", return_value=0, create=True), patch(
    "pathlib.Path.lstat", side_effect=FileNotFoundError
):
    result = probe.main([])
# No live configuration files are needed: the parser may fail closed, but cold
# imports must complete and must not prepare runtime storage on the way there.
assert "pilot_acceptance" in sys.modules
assert not events, events
assert result == 1
'''
        environment = {
            **os.environ,
            "CAREERTRELLIS_DATA_DIR": str(next(iter(self.paths.values())).parent / "absent-data"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parent, env=environment,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertEqual(probe.FIXED_ERROR + "\n", result.stderr)
        self.assertFalse(Path(environment["CAREERTRELLIS_DATA_DIR"]).exists())


if __name__ == "__main__":
    unittest.main()
