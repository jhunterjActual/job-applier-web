"""Offline native trust-boundary tests; no processes or provider calls."""
import copy
import datetime as dt
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy.onedrive_retention import bundle
from deploy.onedrive_retention import local_restore_plan as plans
from deploy.onedrive_retention import native_restore as native


LABEL = '20261026-155056F'


def prepared():
    return plans.plan(LABEL, 'a' * 64, 'b' * 64, 'c' * 32, 20000000)


def config(extra=''):
    return ('[global]\nrepo1-type=s3\nrepo1-path=/private/repository\n'
            'repo1-cipher-type=aes-256-cbc\nrepo1-cipher-pass=synthetic-secret\n' + extra +
            '[careertrellis]\npg1-path=/var/lib/postgresql/18/main\npg1-port=5432\n').encode()


def manifest():
    files = {path: {'size': 1, 'sha256': 'e' * 64} for path in bundle.REQUIRED_METADATA}
    files['backup/careertrellis/' + LABEL + '/backup.manifest'] = {'size': 1, 'sha256': 'e' * 64}
    return {'schema': bundle.SCHEMA, 'backup_label': LABEL,
            'generation_sha256': 'b' * 64, 'files': files, 'total_bytes': len(files)}


def receipt():
    return {'schema': 'careertrellis.onedrive-bundle-download/v1',
            'result': 'download_verified', 'transaction': 'c' * 32, 'backup_label': LABEL,
            'generation_sha256': 'b' * 64, 'archive_sha256': 'a' * 64,
            'archive_bytes': 10240, 'manifest_sha256': hashlib.sha256(bundle.canonical(manifest())).hexdigest(),
            'native_restore_accepted': False, 'completed_at': '2026-01-01T00:00:00Z',
            'destination': native.PREFIX + 'a' * 64 + '/repository.tar',
            'quota_after': {'total': 100, 'free': 40}}


class ConfigurationTests(unittest.TestCase):
    def test_existing_encrypted_single_repository_configuration_allowed(self):
        self.assertIsNone(native.configuration(config()))

    def test_includes_hosts_hooks_extra_repository_or_unknown_key_refuse(self):
        for addition in ('config-include-path=/tmp\n', 'repo1-host=other\n',
                         'repo2-type=posix\n', 'recovery-option=primary_conninfo=x\n',
                         'cmd=/bin/sh\n', 'cmd-ssh=/bin/sh\n', 'unknown=true\n'):
            with self.subTest(addition=addition), self.assertRaises(native.NativeRefused):
                native.configuration(config(addition))

    def test_unencrypted_missing_cipher_or_extra_sections_refuse_without_secret_error(self):
        for raw in (config().replace(b'aes-256-cbc', b'none'),
                    config().replace(b'repo1-cipher-pass=synthetic-secret\n', b''),
                    config() + b'[extra]\nvalue=synthetic-secret\n',
                    config() + b'broken syntax synthetic-secret'):
            with self.subTest(raw_length=len(raw)), self.assertRaises(native.NativeRefused) as caught:
                native.configuration(raw)
            self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_isolated_automatic_configuration_contains_no_inherited_settings(self):
        self.assertTrue(native.ISOLATED_AUTO.startswith(b'#'))
        self.assertEqual(len(native.ISOLATED_AUTO.splitlines()), 1)
        self.assertNotIn(b'=', native.ISOLATED_AUTO)


class DownloadBindingTests(unittest.TestCase):
    def test_only_actual_download_receipt_bound_to_exact_local_plan(self):
        native.validate_download(receipt(), manifest(), prepared())

    def test_source_receipt_substitution_mismatched_digest_or_fake_acceptance_refuse(self):
        changes = [lambda r: r.update(result='source_verified'),
                   lambda r: r.update(native_restore_accepted=True),
                   lambda r: r.update(transaction='d' * 32),
                   lambda r: r.update(archive_sha256='d' * 64),
                   lambda r: r.update(generation_sha256='d' * 64),
                   lambda r: r.update(manifest_sha256='d' * 64),
                   lambda r: r.update(destination=native.PREFIX + 'd' * 64 + '/repository.tar'),
                   lambda r: r.update(archive_bytes=True),
                   lambda r: r.update(completed_at='2999-01-01T00:00:00Z'),
                   lambda r: r.update(quota_after={'total': 100, 'free': 101}),
                   lambda r: r.update(extra='ignored?')]
        for index, mutate in enumerate(changes):
            with self.subTest(index=index):
                value = receipt(); mutate(value)
                with self.assertRaises(native.NativeRefused):
                    native.validate_download(value, manifest(), prepared())

    def test_native_host_gate_precedes_all_file_or_process_access(self):
        with patch.object(native.os, 'geteuid', return_value=1000, create=True), \
                patch.object(native, 'protected') as read, \
                patch.object(native.subprocess, 'Popen') as spawn:
            with self.assertRaises(native.NativeRefused):
                native.Native(prepared(), '/ignored')
            read.assert_not_called(); spawn.assert_not_called()

    def test_tampered_plan_refuses_before_native_host_inspection(self):
        value = prepared(); value['restore'].append('--repo1-type=s3')
        with patch.object(native.os, 'geteuid', create=True) as uid:
            with self.assertRaises(plans.RestoreRefused):
                native.Native(value, '/ignored')
            uid.assert_not_called()

    def test_public_execute_sanitizes_private_exceptions(self):
        with patch.object(native, 'Native', side_effect=RuntimeError('synthetic-secret')):
            with self.assertRaisesRegex(native.NativeRefused,
                                        '^native_restore_requires_reconciliation$'):
                native.execute(prepared(), '/ignored')


class NativeLifecycleTests(unittest.TestCase):
    def instance(self):
        result = native.Native.__new__(native.Native)
        result.plan = prepared(); result.owner = {'pid': 123, 'start_ticks': 456}
        result.fds = [17, 18, 19]; result.possible_postmaster = False
        result.configuration_unchanged = lambda: None
        result.events = []
        result.event = lambda name, value: result.events.append((name, value))
        return result

    def test_dispatch_uses_exact_argv_empty_environment_private_output_and_durable_order(self):
        instance = self.instance()
        proc = unittest.mock.Mock(pid=222)
        proc.wait.return_value = 0; proc.poll.return_value = 0
        with patch.object(native.subprocess, 'Popen', return_value=proc) as spawn, \
                patch.object(native, 'process_identity', return_value={'pid': 222, 'start_ticks': 333}):
            instance.dispatch(instance.plan['restore'], 'restore', 3600)
        self.assertEqual([name for name, _ in instance.events],
                         ['restore-intent', 'restore-dispatch', 'restore-terminal'])
        self.assertEqual(spawn.call_args.args[0], instance.plan['restore'])
        options = spawn.call_args.kwargs
        self.assertEqual(options['env'], native.ENV)
        self.assertEqual(options['pass_fds'], (17, 18, 19))
        self.assertTrue(options['start_new_session'])
        self.assertEqual(options['stdout'], native.subprocess.DEVNULL)
        self.assertEqual(options['stderr'], native.subprocess.DEVNULL)
        self.assertNotIn('kill', [call[0] for call in proc.method_calls])

    def test_dispatch_refuses_unplanned_argument_before_spawn(self):
        instance = self.instance()
        with patch.object(native.subprocess, 'Popen') as spawn:
            with self.assertRaises(native.NativeRefused):
                instance.dispatch(instance.plan['restore'] + ['--repo1-type=s3'], 'restore', 3600)
            spawn.assert_not_called()

    def test_start_dispatch_uncertainty_contains_without_stop_signal_or_retry(self):
        instance = self.instance()
        def event(name, value):
            instance.events.append((name, value))
            if name == 'start-dispatch': raise RuntimeError('private journal failure')
        instance.event = event
        instance.contain = unittest.mock.Mock(side_effect=RuntimeError('test containment'))
        proc = unittest.mock.Mock(pid=222); proc.poll.return_value = 0
        with patch.object(native.subprocess, 'Popen', return_value=proc) as spawn, \
                patch.object(native, 'process_identity', return_value={'pid': 222, 'start_ticks': 333}):
            with self.assertRaisesRegex(RuntimeError, 'test containment'):
                instance.dispatch(instance.plan['start'], 'start', 120)
        self.assertEqual(spawn.call_count, 1)
        instance.contain.assert_called_once_with('start')
        self.assertTrue(instance.possible_postmaster)
        self.assertNotIn('kill', [call[0] for call in proc.method_calls])

    def test_terminal_wait_only_observes_original_pid_and_cgroup(self):
        instance = self.instance()
        instance.postmaster = {'pid': 222, 'start_ticks': 333}
        instance.group_members = lambda: {os.getpid()}
        with patch.object(native, 'process_identity', side_effect=[instance.postmaster, FileNotFoundError()]) as observe, \
                patch.object(native.time, 'sleep') as pause, \
                patch.object(native.os.path, 'lexists', return_value=False), \
                patch.object(native.subprocess, 'Popen') as spawn:
            instance.wait_terminal()
        self.assertEqual(observe.call_args_list, [unittest.mock.call(222), unittest.mock.call(222)])
        pause.assert_called_once_with(.25); spawn.assert_not_called()

    def test_terminal_wait_budget_does_not_repeat_stop(self):
        instance = self.instance(); instance.postmaster = {'pid': 222, 'start_ticks': 333}
        with patch.object(native, 'process_identity', return_value=instance.postmaster), \
                patch.object(native.time, 'monotonic', side_effect=[0, 31]), \
                patch.object(native.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(native.NativeRefused, '^postmaster_not_terminal$'):
                instance.wait_terminal()
        spawn.assert_not_called()


ROOT_POSIX = hasattr(os, 'geteuid') and os.geteuid() == 0 and hasattr(os, 'O_NOFOLLOW')


@unittest.skipUnless(ROOT_POSIX, 'requires native root POSIX no-follow configuration fixture')
class DisposableAutoFilesystemTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bundle-auto-fixture-', dir='/run')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / 'data'; self.data.mkdir(mode=0o700)
        self.original = self.data / 'postgresql.auto.conf'
        self.raw = b"primary_conninfo = 'synthetic-secret'\ninclude = '/outside'\nrestore_command = 'unsafe'\n"
        self.original.write_bytes(self.raw); self.original.chmod(0o600)
        self.instance = native.Native.__new__(native.Native)
        self.instance.root = self.root
        self.instance.plan = dict(prepared(), data_root=str(self.data))
        self.instance.account = SimpleNamespace(pw_uid=0, pw_gid=0)
        self.instance.possible_postmaster = False
        self.instance.group_members = lambda: {os.getpid()}
        self.account = patch.object(native, 'postgres_account', return_value=self.instance.account)
        self.account.start(); self.addCleanup(self.account.stop)

    def test_exact_private_original_preserved_and_only_disposable_leaf_replaced(self):
        before = self.original.stat().st_ino
        self.instance.replace_disposable_auto(self.data)
        audit = self.root / '.restored-auto-conf.original'
        self.assertEqual(audit.read_bytes(), self.raw)
        self.assertEqual(stat.S_IMODE(audit.stat().st_mode), 0o600)
        self.assertEqual(audit.stat().st_uid, 0)
        self.assertEqual(self.original.read_bytes(), native.ISOLATED_AUTO)
        self.assertNotEqual(self.original.stat().st_ino, before)
        self.assertEqual(stat.S_IMODE(self.original.stat().st_mode), 0o600)
        self.assertFalse((self.data / '.postgresql.auto.conf.native').exists())

    def test_prior_audit_refuses_without_replay_or_original_change(self):
        audit = self.root / '.restored-auto-conf.original'; audit.write_bytes(b'prior')
        with self.assertRaises(FileExistsError):
            self.instance.replace_disposable_auto(self.data)
        self.assertEqual(audit.read_bytes(), b'prior')
        self.assertEqual(self.original.read_bytes(), self.raw)

    def test_live_pid_or_unknown_group_refuses_before_audit_or_mutation(self):
        self.instance.group_members = lambda: {os.getpid(), 12345}
        with self.assertRaises(native.NativeRefused):
            self.instance.replace_disposable_auto(self.data)
        self.assertFalse((self.root / '.restored-auto-conf.original').exists())
        self.assertEqual(self.original.read_bytes(), self.raw)

    def test_symlink_original_refused_without_following_target(self):
        target = self.root / 'outside'; target.write_bytes(b'untouched')
        self.original.unlink(); self.original.symlink_to(target)
        with self.assertRaises(OSError):
            self.instance.replace_disposable_auto(self.data)
        self.assertEqual(target.read_bytes(), b'untouched')
        self.assertFalse((self.root / '.restored-auto-conf.original').exists())


if __name__ == '__main__':
    unittest.main()
