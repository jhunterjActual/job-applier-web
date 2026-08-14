"""Static and POSIX shell tests for the dormant final-cutover coordinator."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIRECTORY = PROJECT_ROOT / "deploy" / "scripts"
MAINTENANCE_LOCK_PATH = "/run/lock/careertrellis-maintenance.lock"
INHERITED_LOCK_VARIABLE = "CAREERTRELLIS_MAINTENANCE_LOCK_FD"
LOCK_HELPER_NAME = "maintenance-lock.sh"
LOCK_FUNCTION_NAME = "acquire_careertrellis_maintenance_lock"
GRANT_WRAPPER_NAME = "apply-postgres-runtime-grants.sh"
COORDINATOR_NAME = "finalize-postgres-r2-cutover.sh"


def _script_sources() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in SCRIPT_DIRECTORY.glob("*.sh")
    }


def _find_unique_script(*needles: str) -> tuple[Path, str]:
    matches = [
        (path, source)
        for path, source in _script_sources().items()
        if all(needle in source for needle in needles)
    ]
    if len(matches) != 1:
        names = ", ".join(path.name for path, _source in matches) or "none"
        raise AssertionError(
            f"Expected one script containing {needles!r}; found {names}."
        )
    return matches[0]


def _maintenance_helper() -> tuple[Path, str, str]:
    path = SCRIPT_DIRECTORY / LOCK_HELPER_NAME
    source = path.read_text(encoding="utf-8")
    if not re.search(
        rf"^{LOCK_FUNCTION_NAME}\(\)\s*\{{",
        source,
        flags=re.MULTILINE,
    ):
        raise AssertionError(f"{path.name} does not define {LOCK_FUNCTION_NAME}.")
    return path, source, LOCK_FUNCTION_NAME


def _coordinator() -> tuple[Path, str]:
    path = SCRIPT_DIRECTORY / COORDINATOR_NAME
    return path, path.read_text(encoding="utf-8")


class FinalCutoverStaticContractTests(unittest.TestCase):
    def test_shared_helper_has_fail_closed_standalone_and_inherited_contract(self) -> None:
        _path, source, _function_name = _maintenance_helper()

        self.assertIn(MAINTENANCE_LOCK_PATH, source)
        self.assertIn(INHERITED_LOCK_VARIABLE, source)
        self.assertIn("/proc/self/fd/10", source)
        self.assertRegex(source, r"flock\s+-n\s+(?:\"?10\"?|\"?\$\{[^}]+\}\"?)")
        self.assertRegex(source, r"exec\s+8>")
        self.assertNotRegex(
            source,
            rf"{INHERITED_LOCK_VARIABLE}[^\n]*(?:true|yes|held)",
            "Inheritance must be proved by the exact open descriptor, not a boolean bypass.",
        )

    def test_every_mutating_wrapper_uses_the_shared_lock_helper(self) -> None:
        helper_path, _helper_source, function_name = _maintenance_helper()
        sources = _script_sources()
        fixed_wrappers = (
            "deploy-release.sh",
            "rollback-release.sh",
            "migrate-postgres.sh",
            "import-sqlite-postgres.sh",
            "reconcile-sqlite-postgres-import.sh",
            "hosted-authority.sh",
        )
        for script_name in fixed_wrappers:
            with self.subTest(script=script_name):
                source = sources[SCRIPT_DIRECTORY / script_name]
                self.assertIn(helper_path.name, source)
                self.assertIn(function_name, source)

        grant_path = SCRIPT_DIRECTORY / GRANT_WRAPPER_NAME
        grant_source = grant_path.read_text(encoding="utf-8")
        self.assertIn(helper_path.name, grant_source)
        self.assertIn(function_name, grant_source)

    def test_coordinator_is_exact_release_root_env_and_no_omission_only(self) -> None:
        _path, source = _coordinator()

        self.assertIn("${EUID}", source)
        self.assertIn("/opt/careertrellis/staging/releases", source)
        self.assertRegex(source, r"\[\[.*=~.*\^\[0-9a-f\]\{40\}\$")
        self.assertIn("root:root 600", source)
        self.assertIn("-L", source)
        self.assertNotIn("--omit-provider-secrets", source)
        self.assertNotIn("--omit-generated-files", source)
        self.assertIn("I_HAVE_VERIFIED_QUIESCENT_SNAPSHOTS_AND_BACKUPS", source)
        self.assertIn("I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED", source)
        self.assertIn("/etc/careertrellis/postgres-migration.env", source)
        self.assertIn("/etc/careertrellis/postgres-runtime.env", source)
        self.assertIn("/etc/careertrellis/object-storage.env", source)
        self.assertIn("migration != runtime", source)
        self.assertIn("The migration and runtime database targets do not match.", source)
        self.assertIn("CAREERTRELLIS_DATA_STORE=postgres", source)
        self.assertIn("CAREERTRELLIS_OBJECT_STORE=r2_encrypted", source)
        self.assertNotIn("if ! env", source)

    def test_coordinator_compares_effective_libpq_targets(self) -> None:
        _path, source = _coordinator()
        match = re.search(
            r'"\$\{release_dir\}/backend/venv/bin/python" -c \'\n(.*?)\n\'; then',
            source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        identity_code = match.group(1)
        common_visible_target = (
            "postgresql+psycopg://role@expected.example/appdb"
        )

        def run_identity_check(
            migration_url: str, runtime_url: str
        ) -> subprocess.CompletedProcess[str]:
            environment = os.environ.copy()
            environment.update(
                {
                    "CAREERTRELLIS_CUTOVER_MIGRATION_DATABASE_URL": migration_url,
                    "CAREERTRELLIS_CUTOVER_RUNTIME_DATABASE_URL": runtime_url,
                }
            )
            return subprocess.run(
                [sys.executable, "-c", identity_code],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        migration_url = (
            f"{common_visible_target}?host=migration.example&port=6543&"
            "dbname=migrationdb"
        )
        runtime_url = (
            f"{common_visible_target}?host=runtime.example&port=6543&"
            "dbname=runtimedb"
        )
        self.assertNotEqual(
            0,
            run_identity_check(migration_url, runtime_url).returncode,
        )

        same_effective_target = (
            f"{common_visible_target}?host=database.example.&port=6543&"
            "dbname=careertrellis"
        )
        normalized_equivalent = (
            f"{common_visible_target}?host=DATABASE.EXAMPLE&port=6543&"
            "dbname=careertrellis"
        )
        self.assertEqual(
            0,
            run_identity_check(
                same_effective_target, normalized_equivalent
            ).returncode,
        )

        private_detail = "private-database-password"
        malformed = run_identity_check(
            f"postgresql+psycopg://role:{private_detail}@expected.example/%",
            normalized_equivalent,
        )
        self.assertNotEqual(0, malformed.returncode)
        self.assertNotIn(private_detail, malformed.stdout)
        self.assertNotIn(private_detail, malformed.stderr)
        self.assertEqual("", malformed.stdout)
        self.assertEqual("", malformed.stderr)

        service_indirection = run_identity_check(
            f"{common_visible_target}?service=private-service",
            common_visible_target,
        )
        self.assertNotEqual(0, service_indirection.returncode)
        self.assertEqual("", service_indirection.stdout)
        self.assertEqual("", service_indirection.stderr)

        primary_only = run_identity_check(
            f"{common_visible_target}?target_session_attrs=primary",
            common_visible_target,
        )
        self.assertNotEqual(0, primary_only.returncode)

        load_balanced = run_identity_check(
            f"{common_visible_target}?load_balance_hosts=random",
            common_visible_target,
        )
        self.assertNotEqual(0, load_balanced.returncode)

    def test_coordinator_requires_future_approved_release_before_lock_or_mutation(self) -> None:
        _path, source = _coordinator()
        lock_position = source.index("exec 10>")
        capability_position = source.index("hosted-authority.compatibility")
        cutover_flag_position = source.index(
            "HOSTED_REPOSITORY_CUTOVER_COMPLETE = True"
        )

        self.assertIn("postgres-r2-authoritative-v1", source)
        self.assertIn("-L ${capability_file}", source)
        self.assertIn("-x ${capability_file}", source)
        self.assertIn('wc -l < "${capability_file}"', source)
        self.assertLess(capability_position, lock_position)
        self.assertLess(cutover_flag_position, lock_position)
        self.assertLess(lock_position, source.rindex("systemctl stop"))

    def test_coordinator_holds_one_fd_across_the_ordered_cutover(self) -> None:
        helper_path, _helper_source, function_name = _maintenance_helper()
        _path, source = _coordinator()

        self.assertIn(helper_path.name, source)
        self.assertIn(function_name, source)
        self.assertRegex(source, r"exec\s+10>")
        self.assertRegex(
            source,
            rf"(?:export\s+)?{INHERITED_LOCK_VARIABLE}=10",
        )
        ordered_needles = (
            "migrate-postgres.sh",
            GRANT_WRAPPER_NAME,
            "preflight-sqlite-postgres-import.sh",
            "import-sqlite-postgres.sh",
            "mark-postgres-r2-authoritative",
            "deploy-release.sh",
        )
        positions = [source.rindex(needle) for needle in ordered_needles]
        self.assertEqual(sorted(positions), positions)
        for script_name in (
            "migrate-postgres.sh",
            GRANT_WRAPPER_NAME,
            "preflight-sqlite-postgres-import.sh",
            "import-sqlite-postgres.sh",
            "hosted-authority.sh",
            "deploy-release.sh",
        ):
            with self.subTest(script=script_name):
                self.assertIn(f'"${{release_script_dir}}/{script_name}"', source)

    def test_services_are_stopped_and_rechecked_before_mutation_and_marker(self) -> None:
        _path, source = _coordinator()
        stop_position = source.rindex("systemctl stop")
        migrate_position = source.rindex("migrate-postgres.sh")
        marker_position = source.rindex("mark-postgres-r2-authoritative")
        deploy_position = source.rindex("deploy-release.sh")
        inactive_checks = [
            match.start()
            for match in re.finditer(
                r"^\s*assert_services_inactive\s*$",
                source,
                flags=re.MULTILINE,
            )
        ]

        self.assertLess(stop_position, migrate_position)
        self.assertTrue(
            any(stop_position < position < migrate_position for position in inactive_checks)
        )
        self.assertGreaterEqual(
            sum(migrate_position < position < marker_position for position in inactive_checks),
            2,
        )
        self.assertLess(marker_position, deploy_position)

    def test_failure_policy_never_rolls_back_or_restarts_before_marker(self) -> None:
        _path, source = _coordinator()
        marker_position = source.rindex("mark-postgres-r2-authoritative")

        self.assertNotIn("rollback-release.sh", source)
        self.assertNotIn("careertrellis-rollback", source)
        self.assertNotIn("systemctl restart", source[:marker_position])
        self.assertNotIn("systemctl start", source[:marker_position])
        self.assertRegex(source, r"(?i)(?:services remain|keep application services) stopped")
        self.assertRegex(source, r"(?i)forward (?:correction|fix)")

    def test_erasure_worker_is_restored_only_after_successful_activation(self) -> None:
        _path, source = _coordinator()
        deploy_position = source.rindex("deploy-release.sh")
        erasure_start_matches = list(
            re.finditer(
                r"systemctl\s+(?:start|restart)\s+careertrellis-erasure-worker\.service",
                source,
            )
        )

        self.assertRegex(source, r"(?i)erasure[^\n]*(?:was|prior|previous)[^\n]*active")
        self.assertEqual(1, len(erasure_start_matches))
        active_condition = source.rindex(
            "if [[ ${erasure_worker_was_active} == true ]]"
        )
        self.assertGreater(active_condition, deploy_position)
        self.assertLess(active_condition, erasure_start_matches[0].start())
        self.assertGreater(erasure_start_matches[0].start(), deploy_position)

    def test_slice_remains_dormant(self) -> None:
        data_store = (PROJECT_ROOT / "backend" / "data_store.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("HOSTED_REPOSITORY_CUTOVER_COMPLETE = False", data_store)
        self.assertFalse(
            (PROJECT_ROOT / "deploy" / "hosted-authority.compatibility").exists()
        )


class RuntimeGrantApplicationTests(unittest.TestCase):
    @staticmethod
    def grant_module():
        try:
            from backend import apply_postgres_runtime_grants as grant_module
        except ModuleNotFoundError as exc:
            if exc.name != "backend":
                raise
            import apply_postgres_runtime_grants as grant_module
        return grant_module

    def test_missing_or_failed_connection_uses_only_the_fixed_error(self) -> None:
        grant_module = self.grant_module()
        with patch.dict(grant_module.os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as missing_context:
                grant_module.apply_runtime_grants(Path("missing.sql"))
        self.assertEqual(grant_module.FIXED_ERROR, str(missing_context.exception))

        with tempfile.TemporaryDirectory() as temp_dir:
            sql_path = Path(temp_dir) / "grant-runtime.sql"
            sql_path.write_text("\\set ON_ERROR_STOP on\nSELECT 1;\n", encoding="utf-8")
            environment = {
                "CAREERTRELLIS_MIGRATION_DATABASE_URL": (
                    "postgresql+psycopg://migration:synthetic-secret@"
                    "database.test/careertrellis"
                )
            }
            with (
                patch.dict(grant_module.os.environ, environment, clear=True),
                patch.object(
                    grant_module.psycopg,
                    "connect",
                    side_effect=ValueError("synthetic-secret"),
                ),
                self.assertRaises(RuntimeError) as connection_context,
            ):
                grant_module.apply_runtime_grants(sql_path)
        self.assertEqual(grant_module.FIXED_ERROR, str(connection_context.exception))
        self.assertNotIn("synthetic-secret", str(connection_context.exception))

    def test_reviewed_sql_executes_once_without_preparing(self) -> None:
        grant_module = self.grant_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            sql_path = Path(temp_dir) / "grant-runtime.sql"
            sql_path.write_text(
                "\\set ON_ERROR_STOP on\nSET ROLE careertrellis_schema_owner;\nSELECT 1;\n",
                encoding="utf-8",
            )
            connection_context = MagicMock()
            connection = connection_context.__enter__.return_value
            cursor = connection.cursor.return_value.__enter__.return_value
            environment = {
                "CAREERTRELLIS_MIGRATION_DATABASE_URL": (
                    "postgresql+psycopg://migration:synthetic-secret@"
                    "database.test/careertrellis"
                )
            }
            with (
                patch.dict(grant_module.os.environ, environment, clear=True),
                patch.object(
                    grant_module.psycopg,
                    "connect",
                    return_value=connection_context,
                ) as connect,
            ):
                grant_module.apply_runtime_grants(sql_path)

        connection_url = connect.call_args.args[0]
        self.assertTrue(connection_url.startswith("postgresql://"))
        self.assertNotIn("postgresql+psycopg://", connection_url)
        cursor.execute.assert_called_once_with(
            "SET ROLE careertrellis_schema_owner;\nSELECT 1;",
            prepare=False,
        )


@unittest.skipIf(os.name == "nt", "POSIX flock and /proc descriptor regression")
class MaintenanceLockExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temporary_directory.name)
        helper_path, helper_source, self.function_name = _maintenance_helper()
        self.lock_path = self.test_root / "careertrellis-maintenance.lock"
        self.other_path = self.test_root / "not-the-maintenance-lock"
        transformed_source = helper_source.replace(
            MAINTENANCE_LOCK_PATH,
            self.lock_path.as_posix(),
        )
        self.helper_path = self.test_root / helper_path.name
        self.helper_path.write_text(transformed_source, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_bash(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            check=False,
        )

    def source_and_acquire(self) -> str:
        return f'source "{self.helper_path}"; {self.function_name}'

    def test_standalone_process_excludes_a_competitor(self) -> None:
        holder = subprocess.Popen(
            [
                "bash",
                "-c",
                f'{self.source_and_acquire()} || exit; echo ready; read -r _line',
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual("ready\n", holder.stdout.readline())
            competitor = self.run_bash(self.source_and_acquire())
            self.assertNotEqual(0, competitor.returncode)
        finally:
            if holder.stdin:
                holder.stdin.write("done\n")
                holder.stdin.flush()
            holder.communicate(timeout=5)

    def test_exact_inherited_fd_is_shared_with_nested_children(self) -> None:
        nested = self.run_bash(
            f'''
                exec 10>"{self.lock_path}"
                flock -n 10
                export {INHERITED_LOCK_VARIABLE}=10
                bash -c 'source "{self.helper_path}"; {self.function_name}'
                if flock -n "{self.lock_path}" -c true; then
                    exit 91
                fi
            ''',
        )
        self.assertEqual(0, nested.returncode, nested.stderr)

    def test_wrong_closed_and_wrong_target_descriptors_fail(self) -> None:
        wrong_number = self.run_bash(
            f'{INHERITED_LOCK_VARIABLE}=9; export {INHERITED_LOCK_VARIABLE}; '
            + self.source_and_acquire()
        )
        closed = self.run_bash(
            f'{INHERITED_LOCK_VARIABLE}=10; export {INHERITED_LOCK_VARIABLE}; '
            + self.source_and_acquire()
        )
        wrong_target = self.run_bash(
            f'exec 10>"{self.other_path}"; '
            f'{INHERITED_LOCK_VARIABLE}=10; export {INHERITED_LOCK_VARIABLE}; '
            + self.source_and_acquire()
        )

        for result in (wrong_number, closed, wrong_target):
            with self.subTest(stderr=result.stderr):
                self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
