"""Synthetic root-only sidecar tests; no network, real identities or credentials."""
from contextlib import redirect_stdout, redirect_stderr
import io
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from uuid import uuid4

import owner_recovery as recovery

BINDING = 'a' * 64


def contender(path, code, operation, queue):
    try:
        with recovery.Store(Path(path)).locked() as store:
            store.authenticate(code, BINDING, 1030, operation=operation)
        queue.put('consumed')
    except Exception:
        queue.put('rejected')


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.values = {
            'staging.env': {'CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID': 'user_synthetic_owner',
                            'WORKOS_CLIENT_ID': 'client_synthetic_test'},
            'auth-v2.env': {'CAREERTRELLIS_AUTH_V2_ENABLED': '0'},
            'postgres-migration.env': {'CAREERTRELLIS_MIGRATION_DATABASE_URL':
                'postgresql+psycopg://careertrellis_migration:synthetic-only@127.0.0.1/careertrellis'}}
        self.rows = [{'id': 7, 'workos_user_id': 'user_synthetic_owner', 'role': 'owner',
                      'created_at': datetime(2026, 9, 1, tzinfo=timezone.utc), 'disabled_at': None}]
        self.schema, self.policy, self.not_erasing, self.oid = '20260907_0014', True, True, 1234
        self.c = MagicMock()
        self.c.execute.return_value.mappings.return_value.all.side_effect = lambda: self.rows
        self.c.scalar.side_effect = self.scalar
        self.engine = MagicMock()
        self.engine.begin.return_value.__enter__.return_value = self.c
        self.patches = [patch.object(recovery, 'settings', side_effect=lambda p: self.values[p.name]),
                        patch('sqlalchemy.create_engine', return_value=self.engine)]
        for entry in self.patches:
            entry.start(); self.addCleanup(entry.stop)

    def scalar(self, sql, params=None):
        query = str(sql)
        if 'version_num' in query:
            return self.schema
        if 'minimum_session_version' in query:
            return self.policy
        if 'account_erasure_requests' in query:
            self.assertEqual({'uid': 7}, params)
            return self.not_erasing
        if 'pg_database' in query:
            return self.oid
        raise AssertionError('Unexpected SQL')

    def test_readonly_stable_snapshot_disposes_connection(self):
        self.assertEqual(recovery.owner_snapshot(), recovery.owner_snapshot())
        first = self.c.exec_driver_sql.call_args_list[0].args[0]
        self.assertEqual('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY', first)
        self.assertEqual(2, self.engine.dispose.call_count)

    def test_missing_multiple_disabled_wrong_owner_refuse(self):
        original = self.rows
        for rows in ([], original * 2, [{**original[0], 'disabled_at': datetime.now(timezone.utc)}],
                     [{**original[0], 'workos_user_id': 'user_someone_else'}]):
            self.rows = rows
            with self.assertRaises(ValueError):
                recovery.owner_snapshot()
        self.assertEqual(4, self.engine.dispose.call_count)

    def test_schema_policy_erasure_guards(self):
        for field, bad in (('schema', '20260909_0015'), ('policy', False), ('not_erasing', False)):
            before = getattr(self, field)
            setattr(self, field, bad)
            with self.assertRaises(ValueError):
                recovery.owner_snapshot()
            setattr(self, field, before)

    def test_config_auth_provider_database_guards(self):
        cases = [('auth-v2.env', 'CAREERTRELLIS_AUTH_V2_ENABLED', '1'),
                 ('staging.env', 'WORKOS_CLIENT_ID', 'invalid'),
                 ('staging.env', 'CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID', 'user_different_owner'),
                 ('postgres-migration.env', 'CAREERTRELLIS_MIGRATION_DATABASE_URL', 'sqlite:///data.db')]
        for filename, key, bad in cases:
            before = self.values[filename][key]
            self.values[filename][key] = bad
            with self.assertRaises(ValueError):
                recovery.owner_snapshot()
            self.values[filename][key] = before

    def test_recreated_account_client_database_change_binding(self):
        original = recovery.owner_snapshot()
        self.rows[0]['created_at'] = datetime(2026, 9, 2, tzinfo=timezone.utc)
        self.assertNotEqual(original, recovery.owner_snapshot())
        self.rows[0]['created_at'] = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.values['staging.env']['WORKOS_CLIENT_ID'] = 'client_other_synthetic'
        self.assertNotEqual(original, recovery.owner_snapshot())
        self.values['staging.env']['WORKOS_CLIENT_ID'] = 'client_synthetic_test'
        self.oid += 1
        self.assertNotEqual(original, recovery.owner_snapshot())

    def test_debug_logger_and_query_failure_refuse(self):
        with patch.object(recovery.logging, 'getLogger') as logger:
            logger.return_value.isEnabledFor.return_value = True
            with self.assertRaises(ValueError):
                recovery.owner_snapshot()
        self.c.scalar.side_effect = RuntimeError('synthetic-query-error')
        with self.assertRaises(RuntimeError):
            recovery.owner_snapshot()
        self.engine.dispose.assert_called_once()


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'requires isolated Linux root fixture')
class OwnerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ct-owner-test-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = recovery.Store(self.root)

    def enroll(self):
        with self.store.locked():
            code = self.store.issue(BINDING, 1000)
            self.store.authenticate(code, BINDING, 1001, confirm=True)
        return code

    def test_private_pending_confirm_and_hash_only(self):
        with self.store.locked():
            code = self.store.issue(BINDING, 1000)
            self.assertEqual('pending', self.store.read()['state'])
            self.assertNotIn(recovery.normalize(code), self.store.path.read_text())
            self.assertEqual(0o600, self.store.path.stat().st_mode & 0o777)
            with self.assertRaises(ValueError):
                self.store.authenticate(code, BINDING, 1001, operation=str(uuid4()))
            self.store.authenticate(code, BINDING, 1001, confirm=True)
            self.assertEqual('active', self.store.read()['state'])

    def test_binding_change_rejects_without_spending_or_rebinding(self):
        code = self.enroll()
        before = self.store.path.read_bytes()
        with self.store.locked(), self.assertRaises(ValueError):
            self.store.authenticate(code, 'b' * 64, 1002, operation=str(uuid4()))
        self.assertEqual(before, self.store.path.read_bytes())

    def test_malformed_and_wrong_codes_charge_durably_and_throttle(self):
        self.enroll()
        for index, code in enumerate(('bad', '0' * 64, '\n', 'f' * 64, '')):
            with recovery.Store(self.root).locked() as reopened:
                with self.assertRaises(ValueError):
                    reopened.authenticate(code, BINDING, 1002 + index * 30, operation=str(uuid4()))
                self.assertEqual(index + 1, reopened.read()['attempts'])
                with self.assertRaises(ValueError):
                    reopened.authenticate(code, BINDING, 1003 + index * 30, operation=str(uuid4()))
                self.assertEqual(index + 1, reopened.read()['attempts'])
        with self.store.locked(), self.assertRaises(ValueError):
            self.store.authenticate('0' * 64, BINDING, 2000, operation=str(uuid4()))
        self.assertEqual(5, self.store.read()['attempts'])

    def test_single_use_exact_receipt_and_expiry(self):
        code = self.enroll()
        operation = str(uuid4())
        with self.store.locked():
            self.store.authenticate(code, BINDING, 1002, operation=operation)
            self.assertEqual(operation, self.store.receipt(BINDING, operation, 1003)['operation'])
            with self.assertRaises(ValueError):
                self.store.authenticate(code, BINDING, 1003, operation=str(uuid4()))
            for binding, op, now in ((BINDING, str(uuid4()), 1003), ('b'*64, operation, 1003),
                                     (BINDING, operation, 1001), (BINDING, operation, 1302)):
                with self.assertRaises(ValueError):
                    self.store.receipt(binding, op, now)

    def test_concurrent_consumption_has_one_winner(self):
        code = self.enroll()
        queue = multiprocessing.Queue()
        children = [multiprocessing.Process(target=contender,
                    args=(str(self.root), code, str(uuid4()), queue)) for _ in range(2)]
        for child in children:
            child.start()
        for child in children:
            child.join(10)
            self.assertFalse(child.is_alive())
            self.assertEqual(0, child.exitcode)
        self.assertEqual(['consumed', 'rejected'], sorted([queue.get(timeout=1), queue.get(timeout=1)]))
        queue.close()

    def test_crash_after_attempt_charge_keeps_budget(self):
        code = self.enroll()
        with self.store.locked(), patch.object(recovery, 'verifier', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                self.store.authenticate(code, BINDING, 1002, operation=str(uuid4()))
        self.assertEqual(1, recovery.Store(self.root).read()['attempts'])
        self.assertEqual('active', self.store.read()['state'])

    def test_consumption_persists_after_caller_failure(self):
        code = self.enroll()
        operation = str(uuid4())
        with self.store.locked():
            self.store.authenticate(code, BINDING, 1002, operation=operation)
        reopened = recovery.Store(self.root)
        with reopened.locked():
            self.assertEqual('consumed', reopened.read()['state'])
            self.assertEqual(operation, reopened.receipt(BINDING, operation, 1003)['operation'])

    def test_issue_never_overwrites_pending_or_active(self):
        with self.store.locked():
            code = self.store.issue(BINDING, 1000)
            with self.assertRaises(ValueError):
                self.store.issue(BINDING, 1001)
            self.store.authenticate(code, BINDING, 1002, confirm=True)
            with self.assertRaises(ValueError):
                self.store.issue(BINDING, 1003)

    def test_expired_pending_confirmation_cannot_activate(self):
        with self.store.locked():
            code = self.store.issue(BINDING, 1000)
            with self.assertRaises(ValueError):
                self.store.authenticate(code, BINDING, 1301, confirm=True)
            self.assertEqual('pending', self.store.read()['state'])

    def test_explicit_expired_pending_replacement_retains_revoked_record(self):
        with self.store.locked():
            old_code = self.store.issue(BINDING, 1000)
            old = self.store.read()
            new_code = self.store.replace_pending(BINDING, 1301)
            new = self.store.read()
            self.assertNotEqual(old['id'], new['id'])
            self.assertNotEqual(old['hash'], new['hash'])
            retained = recovery.strict_json(recovery.protected(self.root / ('revoked-' + old['id'] + '.json')))
            self.assertEqual({**old, 'state': 'revoked'}, retained)
            with self.assertRaises(ValueError):
                self.store.authenticate(old_code, BINDING, 1302, confirm=True)
            self.store.authenticate(new_code, BINDING, 1332, confirm=True)
            self.assertEqual('active', self.store.read()['state'])

    def test_pending_replacement_rejects_unexpired_active_and_wrong_binding(self):
        with self.store.locked():
            code = self.store.issue(BINDING, 1000)
            before = self.store.path.read_bytes()
            for binding, now in ((BINDING, 1300), ('b' * 64, 1301)):
                with self.assertRaises(ValueError):
                    self.store.replace_pending(binding, now)
                self.assertEqual(before, self.store.path.read_bytes())
            self.store.authenticate(code, BINDING, 1001, confirm=True)
            with self.assertRaises(ValueError):
                self.store.replace_pending(BINDING, 2000)

    def test_pending_replacement_archive_failure_preserves_original(self):
        with self.store.locked():
            self.store.issue(BINDING, 1000)
            before = self.store.path.read_bytes()
            with patch.object(recovery.os, 'fsync', side_effect=OSError('synthetic')):
                with self.assertRaises(OSError):
                    self.store.replace_pending(BINDING, 1301)
            self.assertEqual(before, self.store.path.read_bytes())
            # Archive already exists with exact bytes; retry can complete safely.
            fsync = recovery.os.fsync
            flushed = []
            def observe(fd):
                flushed.append(os.readlink('/proc/self/fd/' + str(fd)))
                fsync(fd)
            with patch.object(recovery.os, 'fsync', side_effect=observe):
                self.store.replace_pending(BINDING, 1302)
            self.assertIn('/revoked-', flushed[0])

    def terminal_input(self, data):
        read, write = os.pipe()
        tty = recovery.Terminal()
        tty.fd = read
        tty.deadline = time.monotonic() + .1
        tty.show = MagicMock()
        try:
            os.write(write, data)
            os.close(write); write = None
            return tty.ask('Synthetic prompt')
        finally:
            os.close(read)
            if write is not None:
                os.close(write)

    def test_bracketed_and_plain_paste_have_same_value(self):
        value = b'12345678-abcd1234'
        self.assertEqual(value.decode(), self.terminal_input(value + b'\r'))
        self.assertEqual(value.decode(), self.terminal_input(b'\x1b[200~' + value + b'\x1b[201~\r'))
        self.assertEqual('OWNER', self.terminal_input(b'\x1b[200~OWNER\x1b[201~\n'))

    def test_paste_rejects_multiline_unbalanced_escape_and_excessive_input(self):
        for value in (b'\x1b[200~first\nsecond\x1b[201~\n', b'abc\x1b[201~\n',
                      b'\x1b[200~abc\n', b'\x1b[200~\x1b[200~abc\n', b'\x1b[2Jabc\n',
                      b'\x1b[200~abc\x1b[', b'\x1b[200~abc\x7f\x1b[201~\n', b'a' * 129 + b'\n'):
            with self.subTest(case=value[:10]), self.assertRaises(ValueError):
                self.terminal_input(value)

    def test_real_terminal_bracketed_paste_remains_hidden(self):
        import fcntl
        import select
        import termios
        master, slave = os.openpty()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(master); os.setsid(); fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
                with recovery.Terminal() as tty:
                    answer = tty.ask('Paste test')
                    if answer != 'synthetic-code':
                        os._exit(1)
                os._exit(0)
            except BaseException:
                os._exit(2)
        try:
            self.assertTrue(select.select([master], [], [], 5)[0])
            self.assertIn(b'Paste test', os.read(master, 1024))
            os.write(master, b'\x1b[200~synthetic-code\x1b[201~')
            self.assertFalse(select.select([master], [], [], .1)[0])
            os.write(master, b'\n')
            self.assertEqual(0, os.waitpid(pid, 0)[1]); pid = None
            self.assertEqual(b'\r\n', os.read(master, 1024))
        finally:
            if pid is not None:
                import signal
                os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            os.close(master); os.close(slave)

    def test_unsafe_state_modes_symlink_and_hardlink_refused(self):
        self.enroll()
        self.store.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.store.read()
        self.store.path.chmod(0o600)
        other = self.root / 'other'
        os.link(self.store.path, other)
        with self.assertRaises(ValueError):
            self.store.read()
        other.unlink()
        self.store.path.rename(other)
        self.store.path.symlink_to(other)
        with self.assertRaises(OSError):
            self.store.read()

    def test_directory_and_lock_permissions_refused(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError), self.store.locked():
            pass
        self.root.chmod(0o700)
        (self.root / 'lock').write_text('')
        (self.root / 'lock').chmod(0o644)
        with self.assertRaises(ValueError), self.store.locked():
            pass

    def test_runtime_cannot_read_state(self):
        self.enroll()
        pid = os.fork()
        if pid == 0:
            try:
                os.setgroups([]); os.setgid(65534); os.setuid(65534)
                self.store.path.read_bytes()
            except PermissionError:
                os._exit(0)
            os._exit(1)
        self.assertEqual(0, os.waitpid(pid, 0)[1])

    def test_malformed_state_and_duplicate_keys_refused(self):
        self.enroll()
        self.store.path.write_text('{"version":1,"version":1}')
        with self.assertRaises(ValueError):
            self.store.read()

    def test_entrypoint_scrubs_errors_and_never_prints_secret(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(recovery.os, 'uname') as uname, patch.object(recovery, 'owner_snapshot',
                  side_effect=RuntimeError('synthetic-secret-must-not-escape')):
            uname.return_value.nodename = recovery.HOST
            with patch.object(recovery.sys, 'argv', ['owner_recovery.py', 'preflight']), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(2, recovery.main())
        self.assertEqual('', stdout.getvalue())
        self.assertEqual(recovery.ERROR + '\n', stderr.getvalue())

    def test_real_terminal_hides_input_and_restores_on_signal(self):
        import fcntl
        import select
        import signal
        import termios
        master, slave = os.openpty()
        original = termios.tcgetattr(slave)
        pid = os.fork()
        if pid == 0:
            try:
                os.close(master)
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
                with recovery.Terminal() as tty:
                    tty.ask('Synthetic private input')
                os._exit(1)
            except InterruptedError:
                os._exit(0)
            except BaseException:
                os._exit(2)
        try:
            self.assertTrue(select.select([master], [], [], 5)[0])
            self.assertIn(b'Synthetic private input', os.read(master, 1024))
            os.write(master, b'synthetic-not-a-real-code')
            self.assertFalse(select.select([master], [], [], .1)[0])
            os.kill(pid, signal.SIGTERM)
            self.assertEqual(0, os.waitpid(pid, 0)[1])
            pid = None
            self.assertEqual(original, termios.tcgetattr(slave))
        finally:
            if pid is not None:
                os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            os.close(master); os.close(slave)

    def test_terminal_partial_setup_closes_fd(self):
        import termios
        with patch.object(recovery.os, 'open', return_value=123), patch.object(recovery.os, 'isatty', return_value=False), patch.object(recovery.os, 'close') as close:
            with self.assertRaises(ValueError):
                recovery.Terminal().__enter__()
            close.assert_called_once_with(123)


if __name__ == '__main__':
    unittest.main()
