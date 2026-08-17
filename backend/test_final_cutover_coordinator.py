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
ACTIVATION_LEASE_PATH = "/run/careertrellis-hosted-activation.lease"
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
            "provision-staging-host.sh",
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
        self.assertIn("I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP", source)
        self.assertIn("I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES", source)
        self.assertIn("import-and-pause", source)
        self.assertIn("resume-preparation", source)
        self.assertIn("reconcile-pending", source)
        self.assertIn("record-recovery-evidence", source)
        self.assertIn("mark-and-activate", source)
        self.assertIn("resume-hosted-activation", source)
        self.assertIn("I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED", source)
        self.assertIn("/etc/careertrellis/postgres-migration.env", source)
        self.assertIn("/etc/careertrellis/postgres-runtime.env", source)
        self.assertIn("/etc/careertrellis/object-storage.env", source)
        self.assertIn("migration != runtime", source)
        self.assertIn("The migration and runtime database targets do not match.", source)
        self.assertIn("CAREERTRELLIS_DATA_STORE=postgres", source)
        self.assertIn("CAREERTRELLIS_OBJECT_STORE=r2_encrypted", source)
        self.assertIn("both asynchronous workers to remain disabled", source)
        self.assertIn("verify_installed_start_guards", source)
        self.assertIn("cmp -s", source)
        self.assertIn("systemctl daemon-reload", source)
        self.assertIn("DropInPaths", source)
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

    def test_each_coordinator_action_holds_one_fd_and_keeps_exact_order(self) -> None:
        helper_path, _helper_source, function_name = _maintenance_helper()
        _path, source = _coordinator()

        self.assertIn(helper_path.name, source)
        self.assertIn(function_name, source)
        self.assertRegex(source, r"exec\s+10>")
        self.assertRegex(
            source,
            rf"(?:export\s+)?{INHERITED_LOCK_VARIABLE}=10",
        )
        runtime_start = source.rindex(
            'authority_state=$("${release_script_dir}/hosted-authority.sh" status)'
        )
        import_start = source.index(
            'if [[ ${cutover_action} == "import-and-pause" ]]; then',
            runtime_start,
        )
        import_branch = source[
            import_start:source.index('case "${cutover_action}" in', import_start)
        ]
        activation_branch = source[source.index("phase=post-import-preflight"):]
        import_needles = (
            "migrate-postgres.sh",
            GRANT_WRAPPER_NAME,
            "preflight-sqlite-postgres-import.sh",
            "import-sqlite-postgres.sh",
        )
        activation_needles = (
            "preflight-sqlite-postgres-import.sh",
            "reconcile-sqlite-postgres-import.sh",
            "mark-postgres-r2-authoritative",
            "deploy-release.sh",
        )
        for branch, needles in (
            (import_branch, import_needles),
            (activation_branch, activation_needles),
        ):
            positions = [branch.index(needle) for needle in needles]
            self.assertEqual(sorted(positions), positions)
        for script_name in (
            "migrate-postgres.sh",
            GRANT_WRAPPER_NAME,
            "preflight-sqlite-postgres-import.sh",
            "import-sqlite-postgres.sh",
            "reconcile-sqlite-postgres-import.sh",
            "hosted-authority.sh",
            "deploy-release.sh",
        ):
            with self.subTest(script=script_name):
                self.assertIn(f'"${{release_script_dir}}/{script_name}"', source)

    def test_services_are_stopped_and_rechecked_in_both_phases(self) -> None:
        _path, source = _coordinator()
        stop_position = source.rindex("systemctl stop")
        migrate_position = source.rindex("migrate-postgres.sh")
        marker_position = source.rindex("mark-postgres-r2-authoritative")
        deploy_position = source.rindex("deploy-release.sh")
        inactive_checks = [
            match.start()
            for match in re.finditer(
                r"^\s*assert_services_inactive_and_disabled\s*$",
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
        self.assertIn("reviewed resume-preparation action", source)
        self.assertIn("reviewed read-only reconciliation", source)
        self.assertIn("do not retry, reconcile, or activate", source)

    def test_coordinator_never_implicitly_starts_erasure_worker(self) -> None:
        _path, source = _coordinator()
        self.assertNotRegex(
            source,
            r"systemctl\s+(?:start|restart)\s+careertrellis-erasure-worker\.service",
        )

    def test_pending_attempt_binds_release_and_target_files_across_phases(self) -> None:
        _path, source = _coordinator()

        self.assertIn("/etc/careertrellis/authority/pending-cutover", source)
        self.assertIn("/etc/careertrellis/authority/post-import-recovery", source)
        self.assertIn("write_pending_cutover preparing", source)
        self.assertIn("write_pending_cutover importing", source)
        self.assertIn("write_pending_cutover imported", source)
        self.assertIn("validate_pending_cutover recovery-verified", source)
        self.assertIn("write_pending_cutover recovery-verified", source)
        self.assertIn("write_pending_cutover authority-marked", source)
        self.assertIn("write_pending_cutover activating", source)
        self.assertIn("write_pending_cutover activation-failed", source)
        self.assertIn("write_pending_cutover activated", source)
        self.assertIn(ACTIVATION_LEASE_PATH, source)
        self.assertIn("coordinator_boot_id=", source)
        self.assertIn("coordinator_start_time=", source)
        self.assertIn("/proc/sys/kernel/random/boot_id", source)
        self.assertIn('"/proc/${process_id}/stat"', source)
        self.assertIn("attempt_id=", source)
        self.assertIn("migration_environment_sha256=", source)
        self.assertIn("object_environment_sha256=", source)
        self.assertIn("provider_environment_sha256=", source)
        self.assertIn("source_snapshot_sha256=", source)
        self.assertIn("postgres_backup_label=", source)
        self.assertIn("generated_object_manifest_sha256=", source)
        self.assertIn("postgres_bad_checksums_zero=true", source)
        self.assertIn("restored_database_object_reconciliation_verified=true", source)
        self.assertIn("provider_keyring_recovery_verified=true", source)
        self.assertIn("object_keyring_recovery_verified=true", source)
        self.assertIn("decrypt_every_object_verified=true", source)
        self.assertIn("root:root 600", source)
        self.assertIn("sync -f", source)
        self.assertIn("A final-cutover attempt is already pending", source)

    def test_activation_lease_binds_process_birth_identity(self) -> None:
        _path, coordinator = _coordinator()
        authority = (SCRIPT_DIRECTORY / "hosted-authority.sh").read_text(
            encoding="utf-8"
        )
        for source in (coordinator, authority):
            with self.subTest(script="coordinator" if source is coordinator else "authority"):
                self.assertIn("coordinator_pid=", source)
                self.assertIn("coordinator_boot_id=", source)
                self.assertIn("coordinator_start_time=", source)
                self.assertIn("/proc/sys/kernel/random/boot_id", source)
                self.assertIn('"/proc/${process_id}/stat"', source)
                self.assertRegex(source, r"\$\{#lease_lines\[@\]\}[^\n]*6")

    def test_preparation_state_precedes_quiescence_hash_and_target_mutation(self) -> None:
        _path, source = _coordinator()
        runtime_start = source.rindex(
            'authority_state=$("${release_script_dir}/hosted-authority.sh" status)'
        )
        branch_start = source.index(
            'if [[ ${cutover_action} == "import-and-pause" ]]; then',
            runtime_start,
        )
        branch = source[branch_start:]

        loaded_guards = source.index(
            "verify_installed_start_guards true", runtime_start
        )
        failure_trap_armed = branch.index("maintenance_started=true")
        preparing = branch.index("write_pending_cutover preparing")
        service_stop = branch.index("stop_and_disable_application_services")
        source_hash = branch.index("compute_source_snapshot_sha256")
        migration = branch.index('"${release_script_dir}/migrate-postgres.sh"')
        importing = branch.index("write_pending_cutover importing")
        importer = branch.index('"${release_script_dir}/import-sqlite-postgres.sh"')
        self.assertLess(loaded_guards, branch_start + preparing)
        self.assertLess(failure_trap_armed, preparing)
        self.assertLess(preparing, service_stop)
        self.assertLess(preparing, source_hash)
        self.assertLess(preparing, migration)
        self.assertLess(migration, importing)
        self.assertLess(importing, importer)
        self.assertIn("source_snapshot_sha256=pending", branch[:preparing])

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


@unittest.skipIf(os.name == "nt", "POSIX coordinator execution regression")
class FinalCutoverCoordinatorExecutionTests(unittest.TestCase):
    revision = "a" * 40
    manifest_sha256 = "b" * 64

    def setUp(self) -> None:
        import grp
        import pwd

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temporary_directory.name)
        self.release_parent = self.test_root / "releases"
        self.release = self.release_parent / self.revision
        self.script_dir = self.release / "deploy" / "scripts"
        self.etc_root = self.test_root / "etc" / "careertrellis"
        self.authority_dir = self.etc_root / "authority"
        self.systemd_root = self.test_root / "systemd"
        self.fake_bin = self.test_root / "bin"
        self.event_log = self.test_root / "events.log"
        self.deployed = self.test_root / "deployed"
        self.authority_state = self.test_root / "authority-state"
        self.authority_status_failure = self.test_root / "authority-status-failure"
        self.installed_authority = self.test_root / "careertrellis-authority"
        self.activation_lease = self.test_root / "hosted-activation.lease"
        self.maintenance_lock = self.test_root / "maintenance.lock"
        for directory in (
            self.script_dir,
            self.release / "backend" / "venv" / "bin",
            self.authority_dir,
            self.systemd_root,
            self.fake_bin,
            self.test_root / "data" / "output",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.authority_dir.chmod(0o755)
        self.authority_state.write_text("sqlite-pre-cutover\n", encoding="utf-8")

        user = pwd.getpwuid(os.getuid()).pw_name
        group = grp.getgrgid(os.getgid()).gr_name
        coordinator = (SCRIPT_DIRECTORY / COORDINATOR_NAME).read_text(
            encoding="utf-8"
        )
        coordinator = coordinator.replace(
            "/opt/careertrellis/staging/releases", self.release_parent.as_posix()
        ).replace(
            "/etc/careertrellis", self.etc_root.as_posix()
        ).replace(
            "/etc/systemd/system", self.systemd_root.as_posix()
        ).replace(
            "/usr/local/sbin/careertrellis-authority",
            self.installed_authority.as_posix(),
        ).replace(
            MAINTENANCE_LOCK_PATH,
            self.maintenance_lock.as_posix(),
        ).replace(
            ACTIVATION_LEASE_PATH,
            self.activation_lease.as_posix(),
        ).replace(
            "root:root", f"{user}:{group}"
        ).replace(
            "if [[ ${EUID} -ne 0 ]]; then", "if false; then"
        )
        self.coordinator = self.script_dir / COORDINATOR_NAME
        self.coordinator.write_text(coordinator, encoding="utf-8")
        self.coordinator.chmod(0o755)

        (self.release / "deploy" / "hosted-authority.compatibility").write_text(
            "postgres-r2-authoritative-v1\n", encoding="utf-8"
        )
        (self.release / "backend" / "data_store.py").write_text(
            "HOSTED_REPOSITORY_CUTOVER_COMPLETE = True\n", encoding="utf-8"
        )
        self._write_fake_python()
        self._write_fake_authority()
        self._write_nested_scripts()
        self._write_systemd_guards()
        self._write_environment_files()
        self._write_systemctl()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_executable(self, path: Path, source: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    def _write_fake_python(self) -> None:
        python_path = self.release / "backend" / "venv" / "bin" / "python"
        self._write_executable(
            python_path,
            "#!/usr/bin/env bash\n"
            "case \"$*\" in\n"
            f"  *careertrellis-frozen-source-v1*) printf '%s\\n' '{'1' * 64}' ;;\n"
            f"  *secrets.token_hex*) printf '%s\\n' '{'2' * 32}' ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n",
        )

    def _write_fake_authority(self) -> None:
        source = (
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            f"state='{self.authority_state.as_posix()}'\n"
            f"events='{self.event_log.as_posix()}'\n"
            f"status_failure='{self.authority_status_failure.as_posix()}'\n"
            "case \"${1:-}\" in\n"
            "  status) [[ ! -e ${status_failure} ]] || exit 1; cat \"${state}\" ;;\n"
            "  mark-postgres-r2-authoritative) "
            f"[[ ${{2:-}} == '{self.release.as_posix()}' ]] && "
            "[[ ${3:-} =~ ^[0-9a-f]{32}$ ]] && "
            "[[ ${4:-} == I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED ]] && "
            "[[ ${CAREERTRELLIS_MAINTENANCE_LOCK_FD:-} == 10 ]] && "
            "printf '%s\\n' postgres-r2-authoritative > \"${state}\"; "
            "printf '%s\\n' authority-mark >> \"${events}\" ;;\n"
            "  assert-import-allowed) [[ ${2:-} =~ ^[0-9a-f]{32}$ ]] && "
            f"[[ ${{3:-}} == '{self.revision}' ]] && "
            "[[ ${4:-} =~ ^[0-9a-f]{64}$ ]] && "
            "[[ ${5:-} =~ ^[0-9a-f]{64}$ ]] && "
            "[[ ${6:-} =~ ^[0-9a-f]{64}$ ]] && "
            "[[ ${CAREERTRELLIS_MAINTENANCE_LOCK_FD:-} == 10 ]] ;;\n"
            "  assert-reconciliation-allowed) : ;;\n"
            "  *) : ;;\n"
            "esac\n"
        )
        release_authority = self.script_dir / "hosted-authority.sh"
        self._write_executable(release_authority, source)
        self._write_executable(self.installed_authority, source)

    def _write_nested_scripts(self) -> None:
        maintenance = self.script_dir / LOCK_HELPER_NAME
        maintenance.write_text(
            f"careertrellis_maintenance_lock_path='{self.maintenance_lock.as_posix()}'\n"
            f"{LOCK_FUNCTION_NAME}() {{ flock -n 10; }}\n",
            encoding="utf-8",
        )
        maintenance.chmod(0o644)
        for name in (
            "migrate-postgres.sh",
            GRANT_WRAPPER_NAME,
            "preflight-sqlite-postgres-import.sh",
            "import-sqlite-postgres.sh",
            "reconcile-sqlite-postgres-import.sh",
            "deploy-release.sh",
        ):
            extra = ""
            if name == "import-sqlite-postgres.sh":
                extra = (
                    "[[ ${CAREERTRELLIS_FINAL_CUTOVER_ATTEMPT_ID:-} "
                    "=~ ^[0-9a-f]{32}$ ]] || exit 71\n"
                    f"grep -Fx 'phase=importing' "
                    f"'{(self.authority_dir / 'pending-cutover').as_posix()}' "
                    ">/dev/null || exit 72\n"
                )
            if name == "migrate-postgres.sh":
                extra += (
                    f"grep -Fx 'phase=preparing' "
                    f"'{(self.authority_dir / 'pending-cutover').as_posix()}' "
                    ">/dev/null || exit 72\n"
                )
            if name == "deploy-release.sh":
                extra += (
                    f"grep -Fx 'phase=activating' "
                    f"'{(self.authority_dir / 'pending-cutover').as_posix()}' "
                    ">/dev/null || exit 73\n"
                    f"grep -Fx 'attempt_id={'2' * 32}' "
                    f"'{self.activation_lease.as_posix()}' >/dev/null || exit 74\n"
                    f"grep -Fx 'release={self.revision}' "
                    f"'{self.activation_lease.as_posix()}' >/dev/null || exit 75\n"
                    f"grep -Fx \"coordinator_pid=${{PPID}}\" "
                    f"'{self.activation_lease.as_posix()}' >/dev/null || exit 76\n"
                    "boot_id=$(tr '[:upper:]' '[:lower:]' < "
                    "/proc/sys/kernel/random/boot_id)\n"
                    "process_fields=$(sed 's/^.*) //' \"/proc/${PPID}/stat\")\n"
                    "set -- ${process_fields}\n"
                    f"grep -Fx \"coordinator_boot_id=${{boot_id}}\" "
                    f"'{self.activation_lease.as_posix()}' >/dev/null || exit 77\n"
                    f"grep -Fx \"coordinator_start_time=${{20}}\" "
                    f"'{self.activation_lease.as_posix()}' >/dev/null || exit 78\n"
                    f"[[ $(readlink -f /proc/${{PPID}}/fd/10) == "
                    f"'{self.maintenance_lock.as_posix()}' ]] || exit 79\n"
                    f"touch '{self.deployed.as_posix()}'\n"
                )
            self._write_executable(
                self.script_dir / name,
                "#!/usr/bin/env bash\nset -eu\n"
                + extra
                + f"printf '%s\\n' '{name}' >> '{self.event_log.as_posix()}'\n",
            )

    def _write_systemd_guards(self) -> None:
        content = (
            "[Service]\n"
            "ExecStartPre=/usr/local/sbin/careertrellis-authority "
            "check-worker-runtime\n"
        )
        for service in (
            "careertrellis-web",
            "careertrellis-worker",
            "careertrellis-erasure-worker",
        ):
            source = (
                self.release
                / "deploy"
                / "systemd"
                / f"{service}.service.d"
                / "hosted-authority.conf"
            )
            installed = (
                self.systemd_root
                / f"{service}.service.d"
                / "hosted-authority.conf"
            )
            source.parent.mkdir(parents=True, exist_ok=True)
            installed.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(content, encoding="utf-8")
            installed.write_text(content, encoding="utf-8")
            installed.chmod(0o644)

    def _write_environment_files(self) -> None:
        files = {
            "postgres-migration.env": (
                "CAREERTRELLIS_MIGRATION_DATABASE_URL="
                "postgresql+psycopg://migration@database.test/careertrellis\n"
            ),
            "postgres-runtime.env": (
                "CAREERTRELLIS_DATABASE_URL="
                "postgresql+psycopg://runtime@database.test/careertrellis\n"
            ),
            "object-storage.env": "CAREERTRELLIS_OBJECT_STORE=r2_encrypted\n",
            "provider-secrets.env": "CAREERTRELLIS_PROVIDER_KEYRING_B64=synthetic\n",
            "staging.env": (
                "CAREERTRELLIS_DATA_STORE=postgres\n"
                "CAREERTRELLIS_SEARCH_WORKER_ENABLED=false\n"
                "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=false\n"
                f"CAREERTRELLIS_DATA_DIR={self.test_root.as_posix()}/data\n"
                "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID=user_synthetic\n"
            ),
            "search-worker.env": (
                "CAREERTRELLIS_DATA_STORE=postgres\n"
                "CAREERTRELLIS_SEARCH_WORKER_ENABLED=false\n"
            ),
            "account-erasure-worker.env": (
                "CAREERTRELLIS_DATA_STORE=postgres\n"
                "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=false\n"
            ),
        }
        for name, content in files.items():
            path = self.etc_root / name
            path.write_text(content, encoding="utf-8")
            path.chmod(0o600)

    def _write_systemctl(self) -> None:
        source = (
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            f"events='{self.event_log.as_posix()}'\n"
            f"deployed='{self.deployed.as_posix()}'\n"
            "action=$1; shift\n"
            "if [[ ${action} == show ]]; then\n"
            "  service=${!#}\n"
            f"  printf '%s\\n' '{self.systemd_root.as_posix()}/'"
            "\"${service%.service}.service.d/hosted-authority.conf\"\n"
            "  exit 0\n"
            "fi\n"
            "if [[ ${1:-} == --quiet ]]; then shift; fi\n"
            "service=${1:-}\n"
            "printf 'systemctl:%s:%s\\n' \"${action}\" \"${service}\" >> \"${events}\"\n"
            "case \"${action}:${service}\" in\n"
            "  is-active:careertrellis-web.service|is-active:caddy.service|"
            "is-enabled:careertrellis-web.service|is-enabled:caddy.service) "
            "[[ -e ${deployed} ]] ;;\n"
            "  is-active:*|is-enabled:*) exit 1 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        self._write_executable(self.fake_bin / "systemctl", source)

    def run_coordinator(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PATH"] = f"{self.fake_bin}{os.pathsep}{environment['PATH']}"
        return subprocess.run(
            ["bash", str(self.coordinator), *arguments],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def common_arguments(self) -> tuple[str, str, str]:
        return (
            str(self.release),
            str(self.etc_root / "postgres-migration.env"),
            str(self.etc_root / "object-storage.env"),
        )

    def test_three_actions_preserve_order_and_durable_boundaries(self) -> None:
        phase_one = self.run_coordinator(
            "import-and-pause",
            *self.common_arguments(),
            "I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP",
        )
        self.assertEqual(0, phase_one.returncode, phase_one.stderr)
        pending = (self.authority_dir / "pending-cutover").read_text(
            encoding="utf-8"
        )
        self.assertIn("phase=imported\n", pending)
        self.assertIn(f"release={self.revision}\n", pending)
        phase_one_events = self.event_log.read_text(encoding="utf-8")
        for earlier, later in zip(
            (
                "migrate-postgres.sh",
                GRANT_WRAPPER_NAME,
                "preflight-sqlite-postgres-import.sh",
                "import-sqlite-postgres.sh",
            ),
            (
                GRANT_WRAPPER_NAME,
                "preflight-sqlite-postgres-import.sh",
                "import-sqlite-postgres.sh",
                "__end__",
            ),
        ):
            later_position = (
                len(phase_one_events)
                if later == "__end__"
                else phase_one_events.index(later)
            )
            self.assertLess(phase_one_events.index(earlier), later_position)
        self.assertNotIn("authority-mark", phase_one_events)
        self.assertNotIn("deploy-release.sh", phase_one_events)

        record = self.run_coordinator(
            "record-recovery-evidence",
            *self.common_arguments(),
            "20260817-010203F",
            self.manifest_sha256,
            "I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES",
        )
        self.assertEqual(0, record.returncode, record.stderr)
        self.assertIn(
            "phase=recovery-verified\n",
            (self.authority_dir / "pending-cutover").read_text(encoding="utf-8"),
        )
        evidence = (self.authority_dir / "post-import-recovery").read_text(
            encoding="utf-8"
        )
        self.assertIn("postgres_restore_verified=true\n", evidence)
        self.assertIn("postgres_bad_checksums_zero=true\n", evidence)
        self.assertIn("generated_object_restore_verified=true\n", evidence)
        self.assertIn(
            "restored_database_object_reconciliation_verified=true\n",
            evidence,
        )
        self.assertIn("provider_keyring_recovery_verified=true\n", evidence)
        self.assertIn("object_keyring_recovery_verified=true\n", evidence)
        self.assertIn("decrypt_every_object_verified=true\n", evidence)

        phase_two = self.run_coordinator(
            "mark-and-activate",
            *self.common_arguments(),
            "I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES",
            "I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED",
        )
        self.assertEqual(0, phase_two.returncode, phase_two.stderr)
        events = self.event_log.read_text(encoding="utf-8")
        self.assertLess(events.rindex("preflight-sqlite-postgres-import.sh"), events.index("authority-mark"))
        self.assertLess(events.rindex("reconcile-sqlite-postgres-import.sh"), events.index("authority-mark"))
        self.assertLess(events.index("authority-mark"), events.index("deploy-release.sh"))
        self.assertIn(
            "phase=activated\n",
            (self.authority_dir / "pending-cutover").read_text(encoding="utf-8"),
        )
        self.assertFalse(self.activation_lease.exists())

    def test_hosted_authority_failure_can_resume_only_forward_activation(self) -> None:
        phase_one = self.run_coordinator(
            "import-and-pause",
            *self.common_arguments(),
            "I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP",
        )
        self.assertEqual(0, phase_one.returncode, phase_one.stderr)
        record = self.run_coordinator(
            "record-recovery-evidence",
            *self.common_arguments(),
            "20260817-010203F",
            self.manifest_sha256,
            "I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES",
        )
        self.assertEqual(0, record.returncode, record.stderr)
        self._write_executable(
            self.script_dir / "deploy-release.sh",
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' 'deploy-failed' >> '{self.event_log.as_posix()}'\n"
            "exit 1\n",
        )
        marked_but_failed = self.run_coordinator(
            "mark-and-activate",
            *self.common_arguments(),
            "I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES",
            "I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED",
        )
        self.assertNotEqual(0, marked_but_failed.returncode)
        self.assertEqual(
            "postgres-r2-authoritative",
            self.authority_state.read_text(encoding="utf-8").strip(),
        )
        self.assertIn(
            "phase=activation-failed\n",
            (self.authority_dir / "pending-cutover").read_text(encoding="utf-8"),
        )
        self.assertFalse(self.activation_lease.exists())
        failed_events = self.event_log.read_text(encoding="utf-8")
        self.assertIn(
            "systemctl:stop:careertrellis-erasure-worker.service",
            failed_events,
        )
        self.assertIn(
            "systemctl:disable:careertrellis-erasure-worker.service",
            failed_events,
        )
        self._write_nested_scripts()
        resumed = self.run_coordinator(
            "resume-hosted-activation",
            *self.common_arguments(),
            "I_HAVE_REVIEWED_HOSTED_FORWARD_RECOVERY",
        )
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        self.assertIn(
            "phase=activated\n",
            (self.authority_dir / "pending-cutover").read_text(encoding="utf-8"),
        )
        self.assertFalse(self.activation_lease.exists())

    def test_preparation_failure_requires_explicit_preparation_resume(self) -> None:
        self._write_executable(
            self.script_dir / "migrate-postgres.sh",
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' 'migration-failed' >> '{self.event_log.as_posix()}'\n"
            "exit 1\n",
        )
        failed = self.run_coordinator(
            "import-and-pause",
            *self.common_arguments(),
            "I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP",
        )
        self.assertNotEqual(0, failed.returncode)
        pending = (self.authority_dir / "pending-cutover").read_text(
            encoding="utf-8"
        )
        self.assertIn("phase=preparing\n", pending)
        self.assertIn(f"source_snapshot_sha256={'1' * 64}\n", pending)
        self.assertNotIn("import-sqlite-postgres.sh", self.event_log.read_text(encoding="utf-8"))
        self.assertIn("reviewed resume-preparation action", failed.stderr)

        wrong_recovery = self.run_coordinator(
            "reconcile-pending",
            *self.common_arguments(),
            "I_HAVE_REVIEWED_AMBIGUOUS_IMPORT",
        )
        self.assertNotEqual(0, wrong_recovery.returncode)

        self._write_nested_scripts()
        resumed = self.run_coordinator(
            "resume-preparation",
            *self.common_arguments(),
            "I_HAVE_REVIEWED_PRE_IMPORT_PREPARATION_FAILURE",
        )
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        self.assertIn(
            "phase=imported\n",
            (self.authority_dir / "pending-cutover").read_text(encoding="utf-8"),
        )

    def test_import_failure_is_not_retried_and_reconcile_promotes_only_on_success(self) -> None:
        failing_import = self.script_dir / "import-sqlite-postgres.sh"
        self._write_executable(
            failing_import,
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' 'import-failed' >> '{self.event_log.as_posix()}'\n"
            "exit 1\n",
        )
        failed = self.run_coordinator(
            "import-and-pause",
            *self.common_arguments(),
            "I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP",
        )
        self.assertNotEqual(0, failed.returncode)
        self.assertIn("reviewed read-only reconciliation", failed.stderr)
        events = self.event_log.read_text(encoding="utf-8")
        self.assertEqual(1, events.count("import-failed"))
        self.assertNotIn("authority-mark", events)
        self.assertNotIn("deploy-release.sh", events)
        self.assertIn(
            "phase=importing\n",
            (self.authority_dir / "pending-cutover").read_text(encoding="utf-8"),
        )

        reconciled = self.run_coordinator(
            "reconcile-pending",
            *self.common_arguments(),
            "I_HAVE_REVIEWED_AMBIGUOUS_IMPORT",
        )
        self.assertEqual(0, reconciled.returncode, reconciled.stderr)
        self.assertIn(
            "phase=imported\n",
            (self.authority_dir / "pending-cutover").read_text(encoding="utf-8"),
        )

    def test_unknown_authority_on_failure_forbids_every_recovery_mutation(self) -> None:
        self._write_executable(
            self.script_dir / "migrate-postgres.sh",
            "#!/usr/bin/env bash\n"
            f"touch '{self.authority_status_failure.as_posix()}'\n"
            "exit 1\n",
        )
        result = self.run_coordinator(
            "import-and-pause",
            *self.common_arguments(),
            "I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "authority state is unavailable; keep application services stopped "
            "and do not import, retry, roll back, or activate",
            result.stderr,
        )
        self.assertIn(
            "phase=preparing\n",
            (self.authority_dir / "pending-cutover").read_text(encoding="utf-8"),
        )

    def test_duplicate_and_mismatched_worker_switches_fail_before_service_mutation(self) -> None:
        cases = (
            (
                "duplicate",
                "CAREERTRELLIS_DATA_STORE=postgres\n"
                "CAREERTRELLIS_SEARCH_WORKER_ENABLED=false\n"
                "CAREERTRELLIS_SEARCH_WORKER_ENABLED=garbage\n",
            ),
            (
                "mismatch",
                "CAREERTRELLIS_DATA_STORE=postgres\n"
                "CAREERTRELLIS_SEARCH_WORKER_ENABLED=true\n",
            ),
        )
        worker_path = self.etc_root / "search-worker.env"
        for name, worker_content in cases:
            with self.subTest(case=name):
                self.event_log.write_text("", encoding="utf-8")
                worker_path.write_text(worker_content, encoding="utf-8")
                worker_path.chmod(0o600)
                result = self.run_coordinator(
                    "import-and-pause",
                    *self.common_arguments(),
                    "I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP",
                )
                self.assertNotEqual(0, result.returncode)
                self.assertNotIn("systemctl:stop", self.event_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
