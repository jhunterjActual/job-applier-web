"""Offline finite-credit, restart, concurrency and lease-boundary checks."""
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import serper_budget as budget

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def process_reserve(arguments):
    policy_path, ledger_path, owner, run = arguments
    with patch.object(budget, 'validate_runtime_paths'), patch.object(budget, 'host_identity', return_value='synthetic-host'):
        return budget.reserve(owner, run, 'synthetic-key', lambda: None,
                              policy_path=Path(policy_path), ledger_path=Path(ledger_path), now=NOW)


class CreditBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy_path = Path(self.temp.name) / 'policy.json'
        self.ledger_path = Path(self.temp.name) / 'budget.sqlite3'
        self.policy = dict(version=1, allocation=str(uuid4()), host='synthetic-host',
                           key_digest=hashlib.sha256(b'synthetic-key').hexdigest(),
                           lifetime=12, daily=6, owner_daily=4)
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        budget.create_ledger(self.ledger_path, self.policy)
        paths = patch.object(budget, 'validate_runtime_paths')
        paths.start(); self.addCleanup(paths.stop)
        host = patch.object(budget, 'host_identity', return_value='synthetic-host')
        host.start(); self.addCleanup(host.stop)

    def reserve(self, owner=1, run=None, key='synthetic-key', checkpoint=lambda: None, now=NOW):
        return budget.reserve(owner, run or str(uuid4()), key, checkpoint,
                              policy_path=self.policy_path, ledger_path=self.ledger_path, now=now)

    def used(self):
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            return connection.execute('SELECT used FROM allocation').fetchone()[0]

    def test_lifetime_is_not_reset_by_daily_rollover_or_restart(self):
        for day in range(2):
            for owner in (1, 2, 3):
                for _ in range(2):
                    self.assertTrue(self.reserve(owner=owner, now=NOW + timedelta(days=day)))
        self.assertEqual(12, self.used())
        self.assertFalse(self.reserve(now=NOW + timedelta(days=3)))
        with self.assertRaises(FileExistsError):
            budget.create_ledger(self.ledger_path, self.policy)

    def test_owner_and_global_daily_caps(self):
        self.assertTrue(all(self.reserve() for _ in range(4)))
        self.assertFalse(self.reserve())
        self.assertTrue(all(self.reserve(owner=2) for _ in range(2)))
        self.assertFalse(self.reserve(owner=3))
        self.assertEqual(6, self.used())

    def test_same_run_cannot_change_owner_or_exceed_six_across_days(self):
        run = str(uuid4())
        self.assertTrue(self.reserve(run=run))
        self.assertFalse(self.reserve(owner=2, run=run))
        for day in range(1, 6):
            self.assertTrue(self.reserve(run=run, now=NOW + timedelta(days=day)))
        self.assertFalse(self.reserve(run=run, now=NOW + timedelta(days=6)))

    def test_concurrent_processes_cannot_overspend(self):
        arguments = [(str(self.policy_path), str(self.ledger_path), 1, str(uuid4())) for _ in range(12)]
        with ProcessPoolExecutor(max_workers=4) as pool:
            accepted = list(pool.map(process_reserve, arguments))
        self.assertEqual(4, sum(accepted))
        self.assertEqual(4, self.used())

    def test_missing_file_never_creates_budget(self):
        self.ledger_path.unlink()
        self.assertFalse(self.reserve())
        self.assertFalse(self.ledger_path.exists())

    def test_wrong_host_key_policy_and_invalid_owner_refuse(self):
        with patch.object(budget, 'host_identity', return_value='other-host'):
            self.assertFalse(self.reserve())
        for key in ('other-key', '', None):
            self.assertFalse(self.reserve(key=key))
        for owner in (0, -1, True, '1'):
            self.assertFalse(self.reserve(owner=owner))
        self.policy['lifetime'] = 20
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        self.assertFalse(self.reserve())
        self.assertEqual(0, self.used())

    def test_physical_and_logical_corruption_refuse(self):
        self.assertTrue(self.reserve())
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            connection.execute('DELETE FROM daily')
            connection.commit()
        self.assertFalse(self.reserve())
        self.ledger_path.write_bytes(b'not sqlite')
        self.assertFalse(self.reserve())

    def test_clock_reversal_refuses(self):
        self.assertTrue(self.reserve())
        self.assertFalse(self.reserve(now=NOW - timedelta(days=1)))

    def test_busy_state_refuses_without_increment(self):
        connection = sqlite3.connect(self.ledger_path)
        try:
            connection.execute('BEGIN IMMEDIATE')
            self.assertFalse(self.reserve())
        finally:
            connection.close()
        self.assertEqual(0, self.used())

    def test_lease_failure_before_commit_rolls_back_and_after_commit_retains_credit(self):
        class LeaseLost(Exception):
            pass
        for failure_at, expected in ((1, 0), (2, 0), (3, 1)):
            checkpoint = Mock(side_effect=[None] * (failure_at - 1) + [LeaseLost()])
            with self.assertRaises(LeaseLost):
                self.reserve(checkpoint=checkpoint)
            self.assertEqual(expected, self.used())


class WorkerBudgetWiringTests(unittest.TestCase):
    def test_key_is_opt_in_and_rejected_by_web(self):
        import config
        with patch.dict(os.environ, {'CAREERTRELLIS_SERPER_API_KEY': 'synthetic-key',
                                    'CAREERTRELLIS_SEARCH_WORKER_ENABLED': 'true'}, clear=True):
            self.assertEqual('', config.get_serper_api_key())
            os.environ['CAREERTRELLIS_SERPER_ENABLED'] = '1'
            self.assertEqual('synthetic-key', config.get_serper_api_key())
            with self.assertRaises(config.RuntimeConfigurationError):
                config.validate_runtime_configuration()

    def test_claim_identity_and_live_checkpoint_reach_reservation(self):
        import search_run_execution as execution
        from search_run_repository import SearchRunClaim
        claim = SearchRunClaim(str(uuid4()), 42, str(uuid4()), 'Engineer', 'Remote', str(uuid4()), 1)
        repository = Mock()
        lease = MagicMock()
        def search(*args, **kwargs):
            self.assertEqual('synthetic-key', kwargs['serper_api_key'])
            self.assertIs(True, kwargs['reserve_search_request']())
            return {'success': True}
        with patch.object(execution, '_LeaseHeartbeat', return_value=lease), \
                patch.object(execution.config, 'get_serper_api_key', return_value='synthetic-key'), \
                patch.object(execution, 'run_job_search_and_matching', side_effect=search), \
                patch.object(budget, 'reserve', return_value=True) as reserve:
            execution.execute_search_claim(claim, repository)
        reserve.assert_called_once_with(42, claim.run_id, 'synthetic-key', lease.checkpoint)
        repository.succeed.assert_called_once()


@unittest.skipUnless(os.name == 'posix' and os.environ.get('CT_SERPER_NATIVE_PATHS') == '1',
                     'Requires isolated Linux tmpfs fixture, never host paths')
class NativePathTests(unittest.TestCase):
    def test_provisioner_accepts_host_lock_without_changing_it(self):
        from serper_provision import validate_maintenance_lock
        with tempfile.TemporaryDirectory(dir='/run/serper-fixture') as folder:
            path = Path(folder) / 'maintenance.lock'
            path.touch(mode=0o644)
            with path.open('r+b') as stream:
                for mode in (0o600, 0o644):
                    path.chmod(mode)
                    before = path.stat()
                    validate_maintenance_lock(stream.fileno())
                    self.assertEqual(before.st_ino, path.stat().st_ino)
                    self.assertEqual(mode, stat.S_IMODE(path.stat().st_mode))
                for mode in (0o664, 0o666):
                    path.chmod(mode)
                    with self.assertRaises(RuntimeError):
                        validate_maintenance_lock(stream.fileno())
                path.chmod(0o644)
                alias = Path(folder) / 'alias'
                os.link(path, alias)
                with self.assertRaises(RuntimeError):
                    validate_maintenance_lock(stream.fileno())

    def test_optional_delivery_file_is_strict_and_never_discloses_key(self):
        import subprocess
        import sys
        guard = (Path(__file__).resolve().parents[1] / 'deploy/scripts/check-worker-admission-environment.sh').read_text()
        code = guard.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        with tempfile.TemporaryDirectory(dir='/run/serper-fixture') as folder:
            root = Path(folder)
            code = code.replace("Path('/etc/careertrellis')", f'Path({str(root)!r})')
            for name in ('search-worker.env', 'postgres-worker.env', 'provider-secrets.env',
                         'account-erasure-worker.env', 'postgres-erasure-worker.env', 'account-erasure-object-storage.env'):
                path = root / name
                path.write_text('CAREERTRELLIS_ENVIRONMENT=staging\n')
                path.chmod(0o600)
            def check(expected):
                result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True)
                self.assertEqual(expected, result.returncode == 0)
                self.assertNotIn(b'synthetic-secret-key-1234', result.stdout + result.stderr)
            check(True)
            path = root / 'serper-worker.env'
            good = 'CAREERTRELLIS_SERPER_ENABLED=1\nCAREERTRELLIS_SERPER_API_KEY=synthetic-secret-key-1234\n'
            for raw, expected in ((good, True), (good.replace('=1\n', '=0\n'), True),
                                  (good.replace('=1\n', '=true\n'), False),
                                  (good + 'CAREERTRELLIS_SERPER_ENABLED=0\n', False),
                                  (good + 'CAREERTRELLIS_AUTH_V2_ENABLED=1\n', False),
                                  (good.splitlines()[0] + '\n', False),
                                  (good + 'UNRELATED=1\n', False)):
                path.write_text(raw)
                path.chmod(0o600)
                check(expected)
            path.write_text(good)
            path.chmod(0o644)
            check(False)
            path.unlink()
            path.symlink_to(root / 'missing')
            check(False)

    def test_real_modes_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory(dir='/run/serper-fixture') as root:
            root = Path(root)
            policy = root / 'policy.json'
            private = root / 'ledger'
            private.mkdir(mode=0o700)
            ledger = private / 'budget.sqlite3'
            policy.write_text('{}')
            policy.chmod(0o640)
            ledger.touch(mode=0o600)
            budget.validate_runtime_paths(policy, ledger)
            private.chmod(0o750)
            with self.assertRaises(budget.BudgetUnavailable):
                budget.validate_runtime_paths(policy, ledger)
            private.chmod(0o700)
            root.chmod(0o777)
            with self.assertRaises(budget.BudgetUnavailable):
                budget.validate_runtime_paths(policy, ledger)
            root.chmod(0o700)
            alias = private / 'alias'
            alias.symlink_to(ledger)
            with self.assertRaises(budget.BudgetUnavailable):
                budget.validate_runtime_paths(policy, alias)


if __name__ == '__main__':
    unittest.main()
