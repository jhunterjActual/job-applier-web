"""Offline parser/secret-boundary tests plus native POSIX protected-file tests."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import admission_operations as ops


class OperationContractTests(unittest.TestCase):
    def test_environment_forms_duplicates_and_unknown_web_delivery_rejected(self):
        self.assertEqual({'A': '', 'B': 'postgresql+psycopg://synthetic@host/db'},
            ops.assignments(b'# comment\nA=\nB=postgresql+psycopg://synthetic@host/db\n'))
        for raw in (b'A=x\nA=y', b' A=x', b'A =x', b'export A=x', b'A="secret"',
                b"A='secret'", b'A=x\\\ny', b'A=$(id)', b'A=x\x00y'):
            with self.subTest(raw=raw), self.assertRaises(ops.OperationError):
                ops.assignments(raw)
        for raw in (b'CAREERTRELLIS_AUTH_V2_FUTURE=\n', b'CAREERTRELLIS_ADMISSION_DATABASE_URL=secret\n'):
            with patch.object(ops, 'read_protected', return_value=raw), self.assertRaises(ops.OperationError):
                ops.check_worker_delivery()

    def test_hba_prefix_and_transport_policy(self):
        plan = {'recipient': 'age1' + 'a'*58, 'source_cidr': '10.70.0.1/32',
            'database_host': '10.70.0.2', 'transport': 'wireguard'}
        ops.validate_plan(plan)
        original = b'local all all peer\nhost all all 127.0.0.1/32 trust\n'
        candidate = ops.hba_candidate(original, plan)
        self.assertTrue(candidate.endswith(original))
        self.assertLess(candidate.index(b'local all careertrellis_admission_broker reject'), candidate.index(b'local all all peer'))
        self.assertIn(b'hostssl careertrellis', ops.hba_candidate(original, {**plan, 'transport': 'tls'}))
        for original in (candidate, b"include_dir 'conf.d'\n", b' include_if_exists x\n'):
            with self.assertRaises(ops.OperationError):
                ops.hba_candidate(original, plan)
        for changed in ({'source_cidr': '10.70.0.0/24'}, {'database_host': '127.0.0.1'},
                {'transport': 'plaintext'}, {'source_cidr': '10.71.0.1/32'}):
            with self.assertRaises(ops.OperationError):
                ops.validate_plan({**plan, **changed})

    def test_scram_not_plaintext_and_salted(self):
        password = 'synthetic-only-password'
        first, second = ops.scram(password), ops.scram(password)
        self.assertTrue(first.startswith('SCRAM-SHA-256$4096:'))
        self.assertNotEqual(first, second)
        self.assertNotIn(password, first)

    def test_cli_fixed_error_no_secret_leak_no_mutation_for_invalid_request(self):
        output = io.StringIO()
        with patch.object(ops, 'host_locks') as locks, redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(1, ops.main(['unknown', 'synthetic-secret']))
        locks.assert_not_called()
        self.assertNotIn('synthetic-secret', output.getvalue())


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0,
    'Native root POSIX descriptor semantics required; run in disposable Linux container')
class ProtectedFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ct-admission-test-', dir='/')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'config'

    def test_exact_file_roundtrip_no_clobber_and_compare_swap(self):
        ops.write_protected(self.path, b'synthetic-one')
        self.assertEqual(b'synthetic-one', ops.read_protected(self.path))
        with self.assertRaises(ops.OperationError):
            ops.write_protected(self.path, b'overwrite')
        with self.assertRaises(ops.OperationError):
            ops.write_protected(self.path, b'overwrite', previous=b'wrong')
        ops.write_protected(self.path, b'synthetic-two', previous=b'synthetic-one')
        self.assertEqual(b'synthetic-two', ops.read_protected(self.path))

    def test_symlink_hardlink_permissions_parent_and_size_fail_closed(self):
        ops.write_protected(self.path, b'secret')
        link = self.root / 'link'
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            ops.read_protected(link)
        link.unlink()
        os.link(self.path, link)
        with self.assertRaises(ops.OperationError):
            ops.read_protected(self.path)
        link.unlink()
        self.path.chmod(0o644)
        with self.assertRaises(ops.OperationError):
            ops.read_protected(self.path)
        self.path.chmod(0o600)
        with self.assertRaises(ops.OperationError):
            ops.read_protected(self.path, limit=2)
        self.root.chmod(0o777)
        with self.assertRaises(ops.OperationError):
            ops.read_protected(self.path)
        self.root.chmod(0o700)

    def test_worker_file_delivery_rejects_before_install(self):
        for name in ops.WORKER_FILES:
            ops.write_protected(self.root / name, b'CAREERTRELLIS_ENVIRONMENT=staging\n')
        with patch.object(ops, 'ROOT', self.root):
            ops.check_worker_delivery()
            shared = self.root / 'provider-secrets.env'
            ops.write_protected(shared, b'CAREERTRELLIS_AUTH_V2_ENVELOPE_KEY=synthetic\n', previous=shared.read_bytes())
            with self.assertRaises(ops.OperationError):
                ops.check_worker_delivery()

    def test_initial_install_native_files_and_no_implicit_rotation(self):
        self._install_case(fail_validation=False)

    def test_failed_hba_validation_retains_nologin_and_attempt_evidence(self):
        self._install_case(fail_validation=True)

    def _install_case(self, fail_validation):
        state = self.root / 'state'
        state.mkdir(mode=0o700)
        hba = self.root / 'pg_hba.conf'
        original = b'local all all peer\n'
        ops.write_protected(hba, original)
        current_policy = {'maintenance': False, 'minimum_session_version': 1}
        statements = []

        def query(sql):
            statements.append(sql)
            if 'row_to_json(p)' in sql:
                return current_policy.copy()
            if 'to_json(version_num)' in sql:
                return '20260907_0014'
            if "to_json(current_setting('hba_file'))" in sql:
                return str(hba)
            if 'pg_hba_file_rules' in sql:
                return not fail_validation
            return True

        db = Mock(query=Mock(side_effect=query))
        encrypt = Mock(return_value=b'age-encryption.org/v1\nsynthetic-ciphertext')
        plan = {'recipient': 'age1' + 'a'*58, 'source_cidr': '10.70.0.1/32',
            'database_host': '10.70.0.2', 'transport': 'wireguard'}
        with patch.object(ops, 'STATE', state), patch.object(ops, 'BUNDLE', self.root / 'credential.age'):
            if fail_validation:
                with self.assertRaises(ops.OperationError):
                    ops.install_database(db, plan, encrypt=encrypt, hba_path=hba, postgres_uid=0, postgres_gid=0)
            else:
                ops.install_database(db, plan, encrypt=encrypt, hba_path=hba, postgres_uid=0, postgres_gid=0)
            record = json.loads(ops.read_protected(state / 'install.json'))
            self.assertEqual('prepared' if fail_validation else 'installed_nologin', record['phase'])
            self.assertTrue(ops.read_protected(hba).endswith(original))
            encrypt.assert_called_once()
            self.assertFalse(any(' LOGIN;' in sql for sql in statements))
            mutations = sum('PASSWORD' in sql for sql in statements)
            with self.assertRaises(ops.OperationError):
                ops.install_database(db, plan, encrypt=encrypt, hba_path=hba, postgres_uid=0, postgres_gid=0)
            self.assertEqual(mutations, sum('PASSWORD' in sql for sql in statements))
            encrypt.assert_called_once()


@unittest.skipUnless(os.name == 'posix' and Path('/.dockerenv').exists()
    and os.environ.get('CAREERTRELLIS_NATIVE_ADMISSION_FIXTURE') == '1', 'Explicit disposable Linux delivery fixture required')
class NativeDeliveryGuardTests(unittest.TestCase):
    def test_deploy_and_legacy_rollback_preflight_use_target_independent_guard(self):
        root = Path('/etc/careertrellis')
        self.assertFalse(root.exists(), 'Refuse any pre-existing protected configuration')
        root.mkdir(mode=0o700)
        self.addCleanup(root.rmdir)
        for name in ops.WORKER_FILES:
            path = root / name
            ops.write_protected(path, b'CAREERTRELLIS_ENVIRONMENT=staging\n')
            self.addCleanup(path.unlink)
        python = Path('/usr/bin/python3')
        if not python.exists():
            python.symlink_to(sys.executable)
            self.addCleanup(python.unlink)
        scripts = Path(__file__).resolve().parents[1] / 'deploy/scripts'
        with tempfile.TemporaryDirectory(dir='/', prefix='ct-admission-guard-') as temporary:
            folder = Path(temporary)
            guard = folder / 'check-worker-admission-environment.sh'
            guard.write_bytes((scripts / guard.name).read_bytes())
            guard.chmod(0o750)
            for name in ('deploy-release.sh', 'rollback-release.sh'):
                source = (scripts / name).read_text()
                start = source.index('    local admission_guard=')
                end = source.index('    /bin/bash "${admission_guard}" || return 1', start)
                fragment = source[start:end] + '    /bin/bash "${admission_guard}" || return 1\n'
                script = f'script_dir={folder}\ntarget_release={folder}/old\nrelease={folder}/old\npreflight() {{\n{fragment}}}\npreflight || exit 1\necho would_start_services\n'
                shared = root / 'provider-secrets.env'
                good = b'CAREERTRELLIS_ENVIRONMENT=staging\n'
                for raw, success in ((good, True), (b'CAREERTRELLIS_AUTH_V2_ENVELOPE_KEY=synthetic-secret\n', False),
                        (b' CAREERTRELLIS_AUTH_V2_ENABLED="0"\n', False)):
                    ops.write_protected(shared, raw, previous=ops.read_protected(shared))
                    result = subprocess.run(['/bin/bash'], input=script.encode(), capture_output=True)
                    self.assertEqual(success, result.returncode == 0)
                    self.assertEqual(success, b'would_start_services' in result.stdout)
                    self.assertNotIn(b'synthetic-secret', result.stdout + result.stderr)
                ops.write_protected(shared, good, previous=ops.read_protected(shared))


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0,
    'Native root POSIX fixture required')
class NativeInitialWrapperTests(unittest.TestCase):
    def test_release_preparation_preserves_admission_helper_execute_modes(self):
        source = (Path(__file__).resolve().parents[1] / 'deploy/scripts/deploy-release.sh').read_text()
        start = source.index('    chmod 0755 \\\n')
        end = source.index('    bash -n ', start)
        fragment = source[start:end]
        names = re.findall(r'\$\{temporary_release\}/deploy/scripts/([^"\n]+)', fragment)
        for name in ('manage-admission-operations.sh', 'check-worker-admission-environment.sh'):
            self.assertIn(name, names)
        with tempfile.TemporaryDirectory(dir='/', prefix='ct-release-modes-') as temporary:
            folder = Path(temporary)
            (folder / 'deploy/scripts').mkdir(parents=True)
            for name in names:
                (folder / 'deploy/scripts' / name).touch(mode=0o600)
            script = f'temporary_release={folder}\n' + fragment + 'chmod -R u=rwX,g=rX,o= "${temporary_release}"\n'
            result = subprocess.run(['/bin/bash'], input=script.encode(), capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr.decode())
            for name in ('manage-admission-operations.sh', 'check-worker-admission-environment.sh'):
                self.assertEqual(0o750, (folder / 'deploy/scripts' / name).stat().st_mode & 0o777)

    def test_launcher_strips_inherited_secret_environment(self):
        scripts = Path(__file__).resolve().parents[1] / 'deploy/scripts'
        python = Path('/usr/bin/python3')
        if not python.exists():
            self.assertTrue(Path('/.dockerenv').exists())
            python.symlink_to(sys.executable)
            self.addCleanup(python.unlink)
        with tempfile.TemporaryDirectory(dir='/', prefix='ct-initial-launcher-') as temporary:
            folder = Path(temporary)
            (folder / 'deploy/scripts').mkdir(parents=True)
            (folder / 'backend/venv/bin').mkdir(parents=True)
            runner = folder / 'backend/admission_operations.py'
            # This substitute checks only the real shell launch boundary, not
            # database operations. No fake result is a host acceptance receipt.
            runner.write_text("import os,sys\nassert set(os.environ) <= {'PATH','LANG','LC_CTYPE','CAREERTRELLIS_CONFIG_READ_ONLY','PYTHONDONTWRITEBYTECODE'}\nassert sys.flags.isolated == 1\nprint('clean_environment')\n")
            runner.chmod(0o644)
            (folder / 'backend/venv/bin/python').symlink_to(sys.executable)
            launcher = folder / 'deploy/scripts/manage-admission-operations.sh'
            launcher.write_bytes((scripts / launcher.name).read_bytes())
            launcher.chmod(0o750)
            environment = {**os.environ, 'CAREERTRELLIS_MIGRATION_DATABASE_URL': 'synthetic-secret',
                'PGPASSWORD': 'synthetic-secret', 'PYTHONPATH': '/untrusted', 'PGSERVICE': 'unexpected'}
            for operation in ('db-initial-begin', 'web-initial-migrate'):
                result = subprocess.run(['/bin/bash', str(launcher), operation, 'synthetic-attempt', 'b'*40],
                    env=environment, capture_output=True)
                self.assertEqual(0, result.returncode, result.stderr.decode())
                self.assertEqual(b'clean_environment\n', result.stdout)
                self.assertNotIn(b'synthetic-secret', result.stdout + result.stderr)

    def test_retired_wrappers_never_evaluate_or_export_even_legacy_targets(self):
        scripts = Path(__file__).resolve().parents[1] / 'deploy/scripts'
        with tempfile.TemporaryDirectory(dir='/', prefix='ct-initial-wrapper-') as temporary:
            folder = Path(temporary)
            marker = folder / 'unexpected'
            env = folder / 'synthetic.env'
            env.write_text('CAREERTRELLIS_MIGRATION_DATABASE_URL=synthetic-secret\n' + f'touch {marker}\n')
            env.chmod(0o600)
            for name in ('migrate-postgres.sh', 'apply-postgres-runtime-grants.sh'):
                result = subprocess.run(['/bin/bash', str(scripts / name), str(folder), str(env)], capture_output=True)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(b'Use the reviewed admission coordinator.', result.stderr)
                self.assertNotIn(b'synthetic-secret', result.stdout + result.stderr)
                self.assertFalse(marker.exists())

    def test_legacy_cutover_refuses_admission_before_any_privileged_action(self):
        scripts = Path(__file__).resolve().parents[1] / 'deploy/scripts'
        with tempfile.TemporaryDirectory(dir='/', prefix='ct-initial-cutover-') as temporary:
            folder = Path(temporary)
            migrations = folder / 'backend/migrations/versions'
            migrations.mkdir(parents=True)
            (migrations / '20260907_0014_admission_lifecycle.py').touch()
            result = subprocess.run(['/bin/bash', str(scripts / 'finalize-postgres-r2-cutover.sh'),
                'import-and-pause', str(folder), '/does-not-exist', '/does-not-exist',
                'I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP'], capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn(b'legacy cutover is unavailable', result.stderr)



if __name__ == '__main__':
    unittest.main()
