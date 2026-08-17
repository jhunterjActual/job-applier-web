import hashlib
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CONFIRMATION = "I_UNDERSTAND_THIS_WRITES_ONLY_TO_DISPOSABLE_POSTGRES_AND_R2"
REVISION = "a" * 40


class CutoverRehearsalSourceContractTests(unittest.TestCase):
    @classmethod
    def project_dir(cls) -> Path:
        return Path(__file__).resolve().parents[1]

    def test_wrapper_has_only_disposable_pre_cutover_actions(self):
        source = (
            self.project_dir()
            / "deploy"
            / "scripts"
            / "rehearse-postgres-r2-cutover.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("assert-rehearsal-import-allowed", source)
        self.assertIn("CAREERTRELLIS_MAINTENANCE_LOCK_FD=10", source)
        self.assertIn("CAREERTRELLIS_REHEARSAL_IMPORT_MIGRATION_ENVIRONMENT_SHA256", source)
        self.assertIn("careertrellis-erasure-worker.service", source)
        self.assertIn("root:root 600", source)
        self.assertIn("^${setting_name}=", source)
        self.assertIn("database_identity", source)
        self.assertIn("object_identity", source)
        self.assertIn("conninfo_to_dict", source)
        self.assertIn("make_url", source)
        self.assertIn('parameters.get("service")', source)
        self.assertIn('parameters.get("hostaddr")', source)
        self.assertIn(CONFIRMATION, source)
        self.assertIn('"${release_dir}/deploy/scripts/preflight-sqlite-postgres-import.sh"', source)
        self.assertIn('"${release_dir}/deploy/scripts/import-sqlite-postgres.sh"', source)
        self.assertNotIn("--omit-provider-secrets", source)
        self.assertNotIn("--omit-generated-files", source)
        self.assertNotIn("mark-postgres-r2-authoritative", source)
        self.assertNotIn("systemctl start", source)
        self.assertNotIn("systemctl restart", source)
        self.assertNotIn("systemctl enable", source)
        self.assertNotIn("systemctl stop", source)
        self.assertNotIn("rm -", source)
        self.assertNotIn("if ! env", source)

    def test_release_preparation_and_ci_run_the_rehearsal_contract(self):
        project_dir = self.project_dir()
        deploy = (project_dir / "deploy" / "scripts" / "deploy-release.sh").read_text(
            encoding="utf-8"
        )
        workflow = (project_dir / ".github" / "workflows" / "web-foundation.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("test_cutover_rehearsal.py", deploy)
        self.assertIn("test_cutover_rehearsal.py", workflow)
        self.assertIn("rehearse-postgres-r2-cutover.sh", deploy)

    def test_effective_database_identity_rejects_libpq_indirection(self):
        source = (
            self.project_dir()
            / "deploy"
            / "scripts"
            / "rehearse-postgres-r2-cutover.sh"
        ).read_text(encoding="utf-8")
        match = re.search(
            r'"\$\{release_dir\}/backend/venv/bin/python" -c \'\n(.*?)\n\'; then',
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        identity_code = match.group(1)
        base_environment = {
            **os.environ,
            "CAREERTRELLIS_LIVE_MIGRATION_DATABASE_URL": (
                "postgresql+psycopg://migration@db.example:5432/live"
            ),
            "CAREERTRELLIS_LIVE_RUNTIME_DATABASE_URL": (
                "postgresql+psycopg://runtime@db.example:5432/live"
            ),
            "CAREERTRELLIS_REHEARSAL_OBJECT_ENDPOINT": (
                "https://account.r2.example"
            ),
            "CAREERTRELLIS_REHEARSAL_OBJECT_BUCKET": "rehearsal-materials",
            "CAREERTRELLIS_LIVE_OBJECT_ENDPOINT": (
                "https://account.r2.example"
            ),
            "CAREERTRELLIS_LIVE_OBJECT_BUCKET": "live-materials",
        }

        def check(url: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-c", identity_code],
                env={
                    **base_environment,
                    "CAREERTRELLIS_REHEARSAL_DATABASE_URL": url,
                },
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(
            0,
            check(
                "postgresql+psycopg://migration@db.example:5432/rehearsal"
            ).returncode,
        )
        unsafe_urls = (
            "postgresql+psycopg://migration@decoy.example/rehearsal?"
            "hostaddr=10.70.0.2&dbname=live",
            "postgresql+psycopg://migration@decoy.example/rehearsal?"
            "service=private-service",
            "postgresql+psycopg://migration@decoy.example/rehearsal?"
            "host=decoy.example,db.example&port=5432,5432",
            "postgresql+psycopg://migration@decoy.example/rehearsal?"
            "target_session_attrs=primary",
        )
        for unsafe_url in unsafe_urls:
            with self.subTest(control=unsafe_url.split("?")[1].split("=")[0]):
                completed = check(unsafe_url)
                self.assertNotEqual(0, completed.returncode)
                self.assertEqual("", completed.stdout)
                self.assertEqual("", completed.stderr)

    def test_final_candidate_pairs_cutover_flag_and_capability(self):
        project_dir = self.project_dir()
        data_store = (project_dir / "backend" / "data_store.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("HOSTED_REPOSITORY_CUTOVER_COMPLETE = True", data_store)
        self.assertEqual(
            "postgres-r2-authoritative-v1\n",
            (project_dir / "deploy" / "hosted-authority.compatibility").read_text(
                encoding="utf-8"
            ),
        )


@unittest.skipIf(os.name == "nt", "POSIX cutover-rehearsal shell regression")
class CutoverRehearsalShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.release = self.root / "staging" / "releases" / REVISION
        self.bin_dir = self.root / "bin"
        self.etc_dir = self.root / "etc"
        self.command_log = self.root / "commands.log"
        self.authority_log = self.root / "authority.log"
        self.authority_state = self.root / "authority-state"
        self.active_services = self.root / "active-services"
        self.project_dir = Path(__file__).resolve().parents[1]
        self.wrapper = (
            self.project_dir
            / "deploy"
            / "scripts"
            / "rehearse-postgres-r2-cutover.sh"
        )

        (self.release / "backend" / "venv" / "bin").mkdir(parents=True)
        (self.release / "deploy" / "scripts").mkdir(parents=True)
        self.bin_dir.mkdir(parents=True)
        self.etc_dir.mkdir(parents=True)
        (self.root / "run" / "lock").mkdir(parents=True)
        self.authority_state.write_text("sqlite-pre-cutover\n", encoding="utf-8")
        self.active_services.write_text("", encoding="utf-8")
        self.command_log.write_text("", encoding="utf-8")
        self.authority_log.write_text("", encoding="utf-8")

        self._write_executable(
            self.release / "backend" / "venv" / "bin" / "python",
            f"#!/usr/bin/env bash\nexec {shlex.quote(Path(sys.executable).as_posix())} \"$@\"\n",
        )
        self._write_executable(
            self.bin_dir / "careertrellis-authority",
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "[[ $# -eq 5 ]] || exit 2\n"
            f"[[ ${{1:-}} == assert-rehearsal-import-allowed && ${{2:-}} == {REVISION} ]] || exit 2\n"
            "[[ ${3:-} =~ ^[0-9a-f]{64}$ && ${4:-} =~ ^[0-9a-f]{64}$ && ${5:-} =~ ^[1-9][0-9]*$ ]] || exit 2\n"
            "[[ ${CAREERTRELLIS_MAINTENANCE_LOCK_FD:-} == 10 ]] || exit 2\n"
            f"[[ $(readlink -f /proc/${{5}}/fd/10) == {shlex.quote((self.root / 'run' / 'lock' / 'careertrellis-maintenance.lock').as_posix())} ]] || exit 2\n"
            f"[[ $(cat {shlex.quote(self.authority_state.as_posix())}) == sqlite-pre-cutover ]]\n"
            f"printf '%s %s %s %s\\n' \"$2\" \"$3\" \"$4\" \"$5\" > {shlex.quote(self.authority_log.as_posix())}\n",
        )
        self._write_executable(
            self.bin_dir / "systemctl",
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "[[ ${1:-} == is-active && ${2:-} == --quiet ]] || exit 2\n"
            f"grep -Fx \"${{3:-}}\" {shlex.quote(self.active_services.as_posix())} >/dev/null 2>&1\n",
        )
        self._write_executable(
            self.release / "deploy" / "scripts" / "preflight-sqlite-postgres-import.sh",
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            f"printf 'preflight\\n' >> {shlex.quote(self.command_log.as_posix())}\n"
            "printf '{\"counts\":{\"jobs\":1}}\\n'\n",
        )
        self._write_executable(
            self.release / "deploy" / "scripts" / "import-sqlite-postgres.sh",
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            f"[[ ${{CAREERTRELLIS_REHEARSAL_IMPORT_RELEASE:-}} == {REVISION} ]] || exit 2\n"
            "[[ ${CAREERTRELLIS_REHEARSAL_IMPORT_COORDINATOR_PID:-} =~ ^[1-9][0-9]*$ ]] || exit 2\n"
            "[[ $(sha256sum \"$2\" | awk '{print $1}') == ${CAREERTRELLIS_REHEARSAL_IMPORT_MIGRATION_ENVIRONMENT_SHA256:-} ]] || exit 2\n"
            "[[ $(sha256sum \"$3\" | awk '{print $1}') == ${CAREERTRELLIS_REHEARSAL_IMPORT_OBJECT_ENVIRONMENT_SHA256:-} ]] || exit 2\n"
            "[[ ${CAREERTRELLIS_MAINTENANCE_LOCK_FD:-} == 10 ]] || exit 2\n"
            f"printf 'import:%s\\n' \"$#\" >> {shlex.quote(self.command_log.as_posix())}\n"
            "printf '{\"verified_target_counts\":{\"jobs\":1}}\\n'\n"
            "printf '{\"verified_counts\":{\"jobs\":1},\"verified_objects\":0}\\n'\n",
        )
        (self.release / "deploy" / "scripts" / "maintenance-lock.sh").write_text(
            "#!/usr/bin/env bash\n",
            encoding="utf-8",
        )

        self.live_migration_environment = self.etc_dir / "postgres-migration.env"
        self.live_runtime_environment = self.etc_dir / "postgres-runtime.env"
        self.live_object_environment = self.etc_dir / "object-storage.env"
        self.rehearsal_database_environment = self.etc_dir / "postgres-rehearsal.env"
        self.rehearsal_object_environment = self.etc_dir / "object-storage-rehearsal.env"
        self._write_environment(
            self.live_migration_environment,
            "CAREERTRELLIS_MIGRATION_DATABASE_URL=postgresql+psycopg://migration:live-secret@db.example:5432/live\n",
        )
        self._write_environment(
            self.live_runtime_environment,
            "CAREERTRELLIS_DATABASE_URL=postgresql+psycopg://runtime:live-secret@db.example:5432/live\n",
        )
        self._write_environment(
            self.live_object_environment,
            "CAREERTRELLIS_OBJECT_STORE=r2_encrypted\n"
            "CAREERTRELLIS_OBJECT_STORE_ENDPOINT=https://account.r2.example\n"
            "CAREERTRELLIS_OBJECT_STORE_BUCKET=live-materials\n",
        )
        self._write_environment(
            self.rehearsal_database_environment,
            "CAREERTRELLIS_MIGRATION_DATABASE_URL=postgresql+psycopg://migration:rehearsal-secret@db.example:5432/rehearsal\n",
        )
        self._write_environment(
            self.rehearsal_object_environment,
            "CAREERTRELLIS_OBJECT_STORE=r2_encrypted\n"
            "CAREERTRELLIS_OBJECT_STORE_ENDPOINT=https://account.r2.example\n"
            "CAREERTRELLIS_OBJECT_STORE_BUCKET=rehearsal-materials\n",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def _write_executable(path: Path, source: str) -> None:
        path.write_text(source, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    @staticmethod
    def _write_environment(path: Path, source: str) -> None:
        path.write_text(source, encoding="utf-8")
        path.chmod(0o600)

    def run_wrapper(self, *extra_arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["CAREERTRELLIS_REHEARSAL_TESTING"] = "true"
        bash_command = environment.get("CAREERTRELLIS_TEST_BASH", "bash")
        return subprocess.run(
            [
                bash_command,
                self.wrapper.as_posix(),
                "--test-root",
                self.root.as_posix(),
                self.release.as_posix(),
                self.rehearsal_database_environment.as_posix(),
                self.rehearsal_object_environment.as_posix(),
                CONFIRMATION,
                *extra_arguments,
            ],
            cwd=self.project_dir,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_success_emits_only_sanitized_receipts_and_exact_release(self):
        completed = self.run_wrapper()

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn(REVISION, completed.stdout)
        self.assertIn('"authority":"sqlite-pre-cutover"', completed.stdout)
        self.assertIn('"no_omissions":true', completed.stdout)
        self.assertEqual(["preflight", "import:3"], self.command_log.read_text(encoding="utf-8").splitlines())
        authority_fields = self.authority_log.read_text(encoding="utf-8").split()
        self.assertEqual(4, len(authority_fields))
        self.assertEqual(REVISION, authority_fields[0])
        self.assertEqual(
            hashlib.sha256(self.rehearsal_database_environment.read_bytes()).hexdigest(),
            authority_fields[1],
        )
        self.assertEqual(
            hashlib.sha256(self.rehearsal_object_environment.read_bytes()).hexdigest(),
            authority_fields[2],
        )
        for secret in ("live-secret", "rehearsal-secret", "live-materials", "rehearsal-materials"):
            self.assertNotIn(secret, completed.stdout)
            self.assertNotIn(secret, completed.stderr)

    def test_authoritative_state_and_active_service_fail_before_commands(self):
        self.authority_state.write_text("postgres-r2-authoritative\n", encoding="utf-8")
        authority_failure = self.run_wrapper()
        self.assertNotEqual(0, authority_failure.returncode)
        self.assertEqual("", self.command_log.read_text(encoding="utf-8"))

        self.authority_state.write_text("sqlite-pre-cutover\n", encoding="utf-8")
        self.active_services.write_text("careertrellis-worker.service\n", encoding="utf-8")
        service_failure = self.run_wrapper()
        self.assertNotEqual(0, service_failure.returncode)
        self.assertIn("Stop the web, search-worker, and erasure-worker services", service_failure.stderr)
        self.assertEqual("", self.command_log.read_text(encoding="utf-8"))

    def test_live_database_or_object_identity_is_rejected_without_disclosure(self):
        self._write_environment(
            self.rehearsal_database_environment,
            "CAREERTRELLIS_MIGRATION_DATABASE_URL=postgresql+psycopg://different-user:different-secret@db.example:5432/live\n",
        )
        database_failure = self.run_wrapper()
        self.assertNotEqual(0, database_failure.returncode)
        self.assertIn("Rehearsal targets must differ from live targets.", database_failure.stderr)
        self.assertNotIn("different-secret", database_failure.stderr)

        self._write_environment(
            self.rehearsal_database_environment,
            "CAREERTRELLIS_MIGRATION_DATABASE_URL=postgresql+psycopg://migration:rehearsal-secret@db.example:5432/rehearsal\n",
        )
        self._write_environment(
            self.rehearsal_object_environment,
            "CAREERTRELLIS_OBJECT_STORE=r2_encrypted\n"
            "CAREERTRELLIS_OBJECT_STORE_ENDPOINT=https://ACCOUNT.r2.example/\n"
            "CAREERTRELLIS_OBJECT_STORE_BUCKET=live-materials\n",
        )
        object_failure = self.run_wrapper()
        self.assertNotEqual(0, object_failure.returncode)
        self.assertIn("Rehearsal targets must differ from live targets.", object_failure.stderr)
        self.assertNotIn("live-materials", object_failure.stderr)
        self.assertEqual("", self.command_log.read_text(encoding="utf-8"))

    def test_live_object_identity_normalizes_default_port_and_terminal_dot(self):
        self._write_environment(
            self.rehearsal_object_environment,
            "CAREERTRELLIS_OBJECT_STORE=r2_encrypted\n"
            "CAREERTRELLIS_OBJECT_STORE_ENDPOINT=https://ACCOUNT.r2.example.:443/\n"
            "CAREERTRELLIS_OBJECT_STORE_BUCKET=live-materials\n",
        )

        completed = self.run_wrapper()

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("Rehearsal targets must differ from live targets.", completed.stderr)
        self.assertNotIn("live-materials", completed.stderr)
        self.assertEqual("", self.command_log.read_text(encoding="utf-8"))

    def test_live_database_identity_normalizes_terminal_dot(self):
        self._write_environment(
            self.rehearsal_database_environment,
            "CAREERTRELLIS_MIGRATION_DATABASE_URL="
            "postgresql+psycopg://different-user:different-secret@db.example.:5432/live\n",
        )

        completed = self.run_wrapper()

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("Rehearsal targets must differ from live targets.", completed.stderr)
        self.assertNotIn("different-secret", completed.stderr)
        self.assertEqual("", self.command_log.read_text(encoding="utf-8"))

    def test_database_indirection_and_multi_host_controls_fail_closed(self):
        unsafe_urls = (
            "postgresql+psycopg://migration:rehearsal-secret@decoy.example/"
            "rehearsal?hostaddr=10.70.0.2&dbname=live",
            "postgresql+psycopg://migration:rehearsal-secret@decoy.example/"
            "rehearsal?service=private-service",
            "postgresql+psycopg://migration:rehearsal-secret@decoy.example/"
            "rehearsal?host=decoy.example,db.example&port=5432,5432",
            "postgresql+psycopg://migration:rehearsal-secret@decoy.example/"
            "rehearsal?target_session_attrs=primary",
        )
        for unsafe_url in unsafe_urls:
            with self.subTest(url=unsafe_url.split("?")[1].split("=")[0]):
                self._write_environment(
                    self.rehearsal_database_environment,
                    f"CAREERTRELLIS_MIGRATION_DATABASE_URL={unsafe_url}\n",
                )
                completed = self.run_wrapper()
                self.assertNotEqual(0, completed.returncode)
                self.assertIn(
                    "Rehearsal target identity validation failed.",
                    completed.stderr,
                )
                self.assertNotIn("rehearsal-secret", completed.stderr)
                self.assertEqual(
                    "", self.command_log.read_text(encoding="utf-8")
                )

    def test_bad_mode_wrong_confirmation_and_extra_argument_fail_closed(self):
        self.rehearsal_database_environment.chmod(0o640)
        mode_failure = self.run_wrapper()
        self.assertNotEqual(0, mode_failure.returncode)
        self.assertIn("root:root mode 0600", mode_failure.stderr)

        self.rehearsal_database_environment.chmod(0o600)
        wrong_confirmation = subprocess.run(
            [
                os.environ.get("CAREERTRELLIS_TEST_BASH", "bash"),
                self.wrapper.as_posix(),
                "--test-root",
                self.root.as_posix(),
                self.release.as_posix(),
                self.rehearsal_database_environment.as_posix(),
                self.rehearsal_object_environment.as_posix(),
                "WRONG",
            ],
            env={**os.environ, "CAREERTRELLIS_REHEARSAL_TESTING": "true"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, wrong_confirmation.returncode)

        for omission in ("--omit-generated-files", "--omit-provider-secrets"):
            with self.subTest(omission=omission):
                extra_argument = self.run_wrapper(omission)
                self.assertNotEqual(0, extra_argument.returncode)
                self.assertEqual("", self.command_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
