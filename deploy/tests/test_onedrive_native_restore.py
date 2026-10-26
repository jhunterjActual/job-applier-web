"""Offline native trust-boundary tests; no processes or provider calls."""
import copy
import datetime as dt
import hashlib
import os
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

    def test_automatic_recovery_settings_require_local_repository(self):
        value = prepared()
        command = '/usr/bin/pgbackrest --repo=1 --repo1-type=posix --repo1-path=' + value['repository_root']
        command += ' --stanza=careertrellis archive-get %f "%p"'
        raw = ("restore_command = '" + command + "'\nrecovery_target = 'immediate'\n"
               "recovery_target_action = 'promote'\n").encode()
        native.auto_configuration(raw, value)
        for changed in (raw.replace(b'--repo1-type=posix', b'--repo1-type=s3'),
                        raw + b"include = '/private/config'\n",
                        raw + b"primary_conninfo = 'secret'\n",
                        raw.replace(b'archive-get', b'; sh archive-get'),
                        raw.replace(b"'immediate'", b"'name'"),
                        raw.replace(b'--repo=1', b'--repo=2'),
                        raw + b"restore_command = 'another'\n"):
            with self.assertRaises(native.NativeRefused):
                native.auto_configuration(changed, value)


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


if __name__ == '__main__':
    unittest.main()
