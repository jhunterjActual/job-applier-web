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

    def test_bounded_benign_compression_level_only(self):
        for value in ('3', '-5', '0', '22'):
            native.configuration(config('compress-level=' + value + '\n'))
        for value in ('-6', '23', 'NaN', '3;command', '3\x01'):
            with self.subTest(value=value), self.assertRaisesRegex(native.NativeRefused,
                        '^private_configuration_compression_refused$'):
                native.configuration(config('compress-level=' + value + '\n'))

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

    def test_control_metadata_allows_recognized_online_backup_states(self):
        fields = {'Database cluster state': 'in production',
                  'Data page checksum version': '1', 'Database system identifier': '123456789'}
        for value in ('shut down', 'shut down in recovery', 'in production'):
            native.validate_control(dict(fields, **{'Database cluster state': value}))
        for changed in ({'Database cluster state': 'in crash recovery'},
                        {'Database cluster state': 'shutting down'},
                        {'Data page checksum version': '0'}, {'Database system identifier': '0'}):
            with self.subTest(changed=changed), self.assertRaises(native.NativeRefused):
                native.validate_control(dict(fields, **changed))


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

    def test_finite_containment_records_incomplete_and_raises_without_cleanup_or_signals(self):
        instance = self.instance()
        with patch.object(native.time, 'sleep') as sleep, patch.object(native.os, 'kill') as kill, \
                patch.object(native.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(native.NativeRefused, '^native_restore_requires_reconciliation$'):
                instance.contain('restore')
        self.assertTrue(instance.failed)
        marker = instance.events[0][1]
        self.assertFalse(marker['native_restore_accepted'])
        self.assertFalse(marker['automatic_retry']); self.assertFalse(marker['cleanup_performed'])
        sleep.assert_not_called(); kill.assert_not_called(); spawn.assert_not_called()

    def test_final_configuration_failure_after_complete_records_failure_and_refuses_receipt(self):
        instance = unittest.mock.Mock(); instance.restore.return_value = {'native_restore_accepted': True}
        instance.configuration_unchanged.side_effect = RuntimeError('private details')
        with patch.object(native, 'Native', return_value=instance):
            with self.assertRaisesRegex(native.NativeRefused, '^native_restore_requires_reconciliation$'):
                native.execute(prepared(), '/ignored')
        instance.record_failure.assert_called_once_with('final-configuration')
        instance.close.assert_called_once_with()


class FiniteOwnerContractTests(unittest.TestCase):
    def valid(self):
        return {'MainPID': '123', 'Type': 'exec', 'Restart': 'no', 'RemainAfterExit': 'yes',
            'KillMode': 'control-group', 'SendSIGKILL': 'yes', 'TimeoutStartUSec': '30s',
            'TimeoutStopUSec': '1min 30s', 'RuntimeMaxUSec': '2h', 'PrivateNetwork': 'yes',
            **dict.fromkeys(native.OWNER_HOOK_FIELDS, '')}
    def test_finite_exec_contract_retains_success_for_external_collector(self):
        native.validate_owner_unit(self.valid(), 123)
        native.validate_owner_unit(dict(self.valid(), TimeoutStopUSec='90000ms'), 123)
    def test_infinite_oneshot_nonretained_or_weak_termination_refuses(self):
        for key, replacement in (('Type', 'oneshot'), ('MainPID', '124'), ('Restart', 'on-failure'),
                ('RemainAfterExit', 'no'), ('KillMode', 'process'), ('SendSIGKILL', 'no'),
                ('PrivateNetwork', 'no'), ('TimeoutStartUSec', 'infinity'),
                ('TimeoutStopUSec', 'infinity'), ('RuntimeMaxUSec', 'infinity'), ('RuntimeMaxUSec', '3h')):
            with self.subTest(key=key), self.assertRaises(native.NativeRefused):
                native.validate_owner_unit(dict(self.valid(), **{key: replacement}), 123)
    def test_all_five_missing_or_nonempty_hooks_refuse_closed_owner_contract(self):
        for field in native.OWNER_HOOK_FIELDS:
            missing=self.valid();missing.pop(field)
            for value in (missing,dict(self.valid(),**{field:'/bin/true'})):
                with self.subTest(field=field),self.assertRaises(native.NativeRefused):
                    native.validate_owner_unit(value,123)
    def test_wrong_or_missing_typed_hook_refuses_before_prepare_or_pg_commands(self):
        for hook in native.OWNER_HOOK_FIELDS:
            for bad in (b'',b'a(sasbttttuii) 1 unexpected\n'):
                instance=native.Native.__new__(native.Native)
                instance.plan=prepared();instance.unit=instance.plan['owner_unit']
                instance.group='/system.slice/'+instance.unit
                instance.preflight=unittest.mock.Mock();instance.lock=unittest.mock.Mock()
                instance.prepare_permissions=unittest.mock.Mock()
                instance.verify_repository=unittest.mock.Mock();instance.reconcile_prior_attempt=unittest.mock.Mock()
                instance.group_members=unittest.mock.Mock()
                profile=dict(self.valid(),MainPID=str(os.getpid()))
                raw=''.join(key+'='+profile[key]+'\n' for key in native.OWNER_SCALAR_FIELDS).encode()
                calls=[]
                def capture(args):
                    calls.append(args)
                    if args[0]=='/usr/bin/systemctl':return raw
                    return bad if args[-1]==hook else native.common_io.EMPTY_EXEC_ARRAY
                instance.capture=capture
                with self.subTest(hook=hook,bad=bad),patch.object(native.Path,'read_text',return_value='0::'+instance.group), \
                        patch.object(native.subprocess,'Popen') as spawn:
                    with self.assertRaises(native.common_io.UnitPropertyRefused):instance.restore()
                    spawn.assert_not_called()
                instance.prepare_permissions.assert_not_called();instance.verify_repository.assert_not_called()
                instance.reconcile_prior_attempt.assert_not_called();instance.group_members.assert_not_called()
                self.assertTrue(all(args[0] in ('/usr/bin/systemctl','/usr/bin/busctl') for args in calls))


class TerminalCollectorTests(unittest.TestCase):
    def evidence(self):
        owner = {'pid': 123, 'start_ticks': 456, 'process_group': 123,
                 'boot_id': '01234567-89ab-cdef-0123-456789abcdef'}
        unit = prepared()['owner_unit']
        complete = {'schema': 'careertrellis.onedrive-native-local-restore/v1', 'result': 'verified',
            'transaction': 'c'*32, 'attempt': 1, 'native_restore_accepted': True,
            'postmaster_terminal': True, 'checksums_verified': True, 'plan_sha256': 'a'*64,
            'completed_at': '2026-10-26T12:00:00Z'}
        snapshot = {'LoadState': 'loaded', 'ActiveState': 'active', 'SubState': 'exited',
            'RemainAfterExit': 'yes', 'MainPID': '0', 'ControlPID': '0', 'Job': '',
            'Result': 'success', 'ExecMainStatus': '0', 'ExecMainPID': '123',
            'InvocationID': 'd'*32, 'ControlGroup': '/system.slice/'+unit}
        stop = {'schema': 'careertrellis.onedrive-native-stop-intent/v1', 'transaction': 'c'*32,
            'attempt': 1, 'unit': unit, 'owner': owner, 'receipt_sha256': hashlib.sha256(plans.canonical(complete)).hexdigest(),
            'unit_state': snapshot, 'observed_at': '2026-10-26T12:00:01Z'}
        proof = {'schema': 'careertrellis.onedrive-native-terminal/v1', 'transaction': 'c'*32,
            'attempt': 1, 'unit': unit, 'owner': owner, 'receipt_sha256': stop['receipt_sha256'],
            'plan_sha256': complete['plan_sha256'], 'unit_state': snapshot,
            'owner_terminal': True, 'cgroup_empty': True, 'postmaster_terminal': True,
            'checksums_verified': True, 'failure_markers_absent': True,
            'observed_at': '2026-10-26T12:00:02Z',
            'stop_intent_sha256': hashlib.sha256(plans.canonical(stop)).hexdigest(),
            'stopped_unit_state': {'LoadState': 'not-found', 'ActiveState': 'inactive',
                'SubState': 'dead', 'MainPID': '0', 'ControlPID': '0', 'Job': '', 'ControlGroup': ''}}
        return proof, complete, owner, stop
    def test_external_retained_success_then_empty_stop_and_gc_is_valid(self):
        proof, complete, owner, stop = self.evidence()
        native.validate_terminal_proof(proof, complete, owner, stop_intent=stop)
    def test_owner_complete_alone_or_missing_stop_intent_never_accepts(self):
        proof, complete, owner, stop = self.evidence()
        with self.assertRaises(native.NativeRefused): native.validate_terminal_proof(proof, complete, owner)
        with self.assertRaises(native.NativeRefused): native.validate_terminal_proof(complete, complete, owner, stop_intent=stop)
    def test_missing_collector_live_owner_failed_unit_or_final_failure_refuses(self):
        original, complete, owner, original_stop = self.evidence()
        variants = [('owner_terminal', False), ('cgroup_empty', False), ('failure_markers_absent', False),
            ('unit_state', dict(original['unit_state'], LoadState='not-found')),
            ('unit_state', dict(original['unit_state'], MainPID='123')),
            ('unit_state', dict(original['unit_state'], Result='timeout')),
            ('stopped_unit_state', dict(original['stopped_unit_state'], MainPID='123')),
            ('stop_intent_sha256', 'f'*64)]
        for key, value in variants:
            proof = dict(original, **{key: value})
            with self.subTest(key=key), self.assertRaises(native.NativeRefused):
                native.validate_terminal_proof(proof, complete, owner, stop_intent=original_stop)


class ReconciliationTests(unittest.TestCase):
    def evidence(self):
        current = plans.plan(LABEL, 'a' * 64, 'b' * 64, 'c' * 32, 20000000, attempt=2)
        old = prepared()
        # A historical immutable plan is not reconstructed against new code.
        # Model the accepted old shape and different original startup settings.
        for field in ('attempt', 'owner_unit', 'native_plan_path', 'web_preflight_path',
                      'native_intent_path', 'prior_reconciliation_path'):
            old.pop(field)
        old['start'] = old['start'] + ['historical-public-plan-byte-binding']
        owner = {'pid': 222, 'start_ticks': 333, 'process_group': 222,
                 'boot_id': '01234567-89ab-cdef-0123-456789abcdef'}
        ownership = {'schema': 'careertrellis.native-local-restore-ownership/v1',
                     'owner': owner, 'plan': old, 'plan_sha256': plans.digest(old),
                     'root_identity': [1, 2], 'download_receipt_sha256': 'd' * 64,
                     'web_preflight_sha256': 'e' * 64}
        intent = {'schema': 'careertrellis.native-local-restore-intent/v1', 'owner': owner,
                  'plan_sha256': plans.digest(old), 'observed_at': '2026-10-26T22:30:00Z'}
        ownership_raw, intent_raw = plans.canonical(ownership), plans.canonical(intent)
        proof = {'schema': 'careertrellis.onedrive-native-reconciliation/v1',
                 'transaction': current['transaction'], 'prior_attempt': 1, 'next_attempt': 2,
                 'archive_sha256': current['archive_sha256'], 'prior_plan_sha256': plans.digest(old),
                 'prior_owner': owner, 'prior_ownership_sha256': hashlib.sha256(ownership_raw).hexdigest(),
                 'prior_intent_sha256': hashlib.sha256(intent_raw).hexdigest(),
                 'prior_terminal': True, 'prior_cgroup_empty': True,
                 'no_restore_dispatch': True, 'no_postmaster': True,
                 'observed_at': '2026-10-26T22:59:00Z'}
        return current, proof, ownership_raw, intent_raw

    def test_fresh_proof_binds_historical_bytes_without_reconstructing_them(self):
        current, proof, ownership, intent = self.evidence()
        checked = native.validate_reconciliation(plans.canonical(proof), ownership, intent,
            current, dt.datetime(2026, 10, 26, 23, tzinfo=dt.timezone.utc))
        self.assertEqual(checked['proof_sha256'], hashlib.sha256(plans.canonical(proof)).hexdigest())
        self.assertEqual(checked['prior_root_identity'], [1, 2])

    def test_false_missing_stale_mismatched_or_unbound_proof_refuses(self):
        mutations = [lambda p: p.update(prior_terminal=False),
                     lambda p: p.update(prior_cgroup_empty=False),
                     lambda p: p.update(no_restore_dispatch=False),
                     lambda p: p.update(no_postmaster=False),
                     lambda p: p.update(no_postmaster=1),
                     lambda p: p.pop('no_restore_dispatch'),
                     lambda p: p.update(next_attempt=3),
                     lambda p: p.update(prior_attempt=True),
                     lambda p: p.update(archive_sha256='f' * 64),
                     lambda p: p.update(prior_plan_sha256='f' * 64),
                     lambda p: p.update(prior_intent_sha256='f' * 64),
                     lambda p: p.update(prior_ownership_sha256='f' * 64),
                     lambda p: p.update(prior_owner=dict(p['prior_owner'], start_ticks=1)),
                     lambda p: p.update(observed_at='2026-10-26T22:44:59Z'),
                     lambda p: p.update(observed_at='2026-10-26T23:00:01Z')]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                current, proof, ownership, intent = self.evidence(); mutate(proof)
                with self.assertRaises(native.NativeRefused):
                    native.validate_reconciliation(plans.canonical(proof), ownership, intent,
                        current, dt.datetime(2026, 10, 26, 23, tzinfo=dt.timezone.utc))

    def test_missing_proof_performs_no_command_or_new_attempt_write(self):
        current, _proof, _ownership, _intent = self.evidence()
        instance = native.Native.__new__(native.Native); instance.plan = current
        instance.capture = unittest.mock.Mock(); instance.event = unittest.mock.Mock()
        with patch.object(native, 'protected', side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError): instance.reconcile_prior_attempt()
        instance.capture.assert_not_called(); instance.event.assert_not_called()

    def test_prior_unit_activity_refuses_despite_claimed_terminal_proof(self):
        current, proof, ownership, intent = self.evidence()
        proof['observed_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        instance = native.Native.__new__(native.Native); instance.plan = current
        instance.owner = {'boot_id': proof['prior_owner']['boot_id']}
        instance.account = SimpleNamespace(pw_uid=1000)
        instance.capture = unittest.mock.Mock(return_value=(
            b'ActiveState=active\nMainPID=222\nControlPID=0\nJob=\nControlGroup=\n'))
        instance.event = unittest.mock.Mock()
        old_root, _unit, intent_path = native.prior_paths(current)
        values = {Path(current['prior_reconciliation_path']): plans.canonical(proof),
                  old_root / 'ownership.json': ownership, intent_path: intent}
        with patch.object(native, 'protected', side_effect=lambda path, **_: values[Path(path)]), \
                patch.object(Path, 'lstat', return_value=SimpleNamespace(st_dev=1, st_ino=2)), \
                patch.object(Path, 'iterdir', return_value=iter(())), \
                patch.object(native, 'directory'), \
                patch.object(native.os.path, 'lexists', return_value=False), \
                patch.object(native, 'process_identity', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(native.NativeRefused, '^prior_unit_not_terminal$'):
                instance.reconcile_prior_attempt()
        instance.event.assert_not_called()
        self.assertEqual(instance.capture.call_count, 1)


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
