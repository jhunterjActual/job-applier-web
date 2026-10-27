"""Synthetic read-only identity witnesses; no provider/native acceptance."""
from contextlib import contextmanager
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import types
import unittest
from unittest.mock import MagicMock, patch

from deploy.onedrive_retention import bundle, identity_collect as identity


NOW = dt.datetime(2026, 10, 27, 12, tzinfo=dt.timezone.utc)
TX = 'a'*32
DATA = b'synthetic encrypted repository archive fixture'
SHA = hashlib.sha256(DATA).hexdigest()


def downloaded():
    return {'schema': 'careertrellis.onedrive-bundle-download/v1', 'result': 'download_verified',
        'transaction': TX, 'archive_sha256': SHA, 'archive_bytes': len(DATA),
        'generation_sha256': 'b'*64, 'backup_label': '20260101-000000F', 'manifest_sha256': 'c'*64,
        'native_restore_accepted': False, 'completed_at': '2026-10-27T11:00:00Z',
        'destination': identity.NAMESPACE+'/'+SHA+'/repository.tar', 'quota_after': {'total': 100, 'free': 40}}


def stat_row(identifier='synthetic-drive-item-id'):
    return {'Path': 'repository.tar', 'Name': 'repository.tar', 'Size': len(DATA),
            'IsDir': False, 'ID': identifier, 'ModTime': '2026-10-27T11:00:00Z'}


class Fake:
    mode = 'offline-test'
    def __init__(self):
        self.events = []; self.fail = None; self.before = stat_row(); self.after = stat_row()
        self.raw = bundle.canonical(downloaded()); self.digest = SHA; self.size = len(DATA)
        self.existing = False; self.receipt = None; self.elapsed = 1.0; self.counter = 0
    def event(self, name):
        self.events.append(name)
        if name == self.fail: raise RuntimeError('synthetic-private-provider-detail')
    @contextmanager
    def locked(self, transaction, verification):
        self.event('lock')
        try: yield
        finally: self.events.append('unlock')
    def now(self): return NOW
    def monotonic(self):
        self.counter += 1; return 0.0 if self.counter == 1 else self.elapsed
    def preflight(self): self.event('preflight')
    def download_receipt(self, transaction): self.event('receipt'); return self.raw
    def begin(self, transaction, verification, observed, digest):
        self.event('begin')
        if self.existing: raise FileExistsError('existing numbered attempt')
    def stat_object(self, observed, phase):
        self.event('stat-'+phase)
        return json.dumps(self.before if phase == 'before' else self.after).encode()
    def download(self, observed): self.event('download')
    def downloaded_digest(self): self.event('hash'); return self.digest, self.size
    def unchanged(self, raw): self.event('unchanged')
    def finish(self, value): self.event('finish'); self.receipt = copy.deepcopy(value)
    def failed(self): self.events.append('failed')


class IdentityTests(unittest.TestCase):
    def test_default_refuses_without_creating_backend_or_provider_calls(self):
        with patch.object(identity.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(identity.IdentityRefused, 'native_identity_collector_not_installed'):
                identity.collect(TX)
            spawn.assert_not_called()

    def test_success_is_a_fresh_witness_not_retroactive_original_identity(self):
        fake = Fake(); result = identity.collect(TX, 1, fake)
        self.assertEqual(fake.events, ['lock', 'preflight', 'receipt', 'begin', 'stat-before',
            'download', 'hash', 'stat-after', 'unchanged', 'finish', 'unlock'])
        self.assertFalse(result['native_identity_accepted'])
        self.assertFalse(result['original_download_identity_proven'])
        self.assertEqual(result['provider_writes'], 0); self.assertEqual(result['deletions_performed'], 0)
        self.assertEqual(fake.receipt, {'schema': 'careertrellis.onedrive-bundle-identity/v1',
            'observed_at': NOW.isoformat(), 'download_receipt_sha256': hashlib.sha256(fake.raw).hexdigest(),
            'path': SHA+'/repository.tar', 'object_id': 'synthetic-drive-item-id', 'size': len(DATA),
            'sha256': SHA, 'identity_verified_before_after_download': True})

    def test_missing_changed_identity_wrong_bytes_or_size_never_publishes(self):
        variants = []
        fake = Fake(); del fake.before['ID']; variants.append(fake)
        fake = Fake(); fake.after['ID'] = 'changed'; variants.append(fake)
        fake = Fake(); fake.after['Size'] += 1; variants.append(fake)
        fake = Fake(); fake.after['IsDir'] = True; variants.append(fake)
        fake = Fake(); fake.digest = '0'*64; variants.append(fake)
        fake = Fake(); fake.size += 1; variants.append(fake)
        for fake in variants:
            with self.assertRaisesRegex(identity.IdentityRefused, '^identity_reverification_requires_reconciliation$'):
                identity.collect(TX, 1, fake)
            self.assertIsNone(fake.receipt); self.assertEqual(fake.events.count('failed'), 1)

    def test_provider_failure_or_changed_config_is_sanitized_without_retry(self):
        for phase in ('stat-before', 'download', 'stat-after', 'unchanged'):
            fake = Fake(); fake.fail = phase
            with self.assertRaises(identity.IdentityRefused) as caught: identity.collect(TX, 1, fake)
            self.assertNotIn('private', str(caught.exception))
            self.assertEqual(fake.events.count(phase), 1); self.assertNotIn('finish', fake.events)

    def test_existing_attempt_refuses_without_writing_other_owners_failure(self):
        fake = Fake(); fake.existing = True
        with self.assertRaises(FileExistsError): identity.collect(TX, 2, fake)
        self.assertNotIn('failed', fake.events); self.assertNotIn('download', fake.events)

    def test_invalid_scope_and_verification_refuse_before_backend_io(self):
        for tx, number in (('../escape', 1), ('A'*32, 1), (TX, True), (TX, 0), (TX, 100)):
            fake = Fake()
            with self.assertRaises(identity.IdentityRefused): identity.collect(tx, number, fake)
            self.assertEqual(fake.events, [])

    def test_future_receipt_or_unexpected_destination_refuses_before_attempt(self):
        for change in ({'completed_at': '2999-01-01T00:00:00Z'}, {'destination': 'onedrive:other/file'},
                       {'transaction': 'd'*32}, {'native_restore_accepted': True}):
            fake = Fake(); value = downloaded(); value.update(change); fake.raw = bundle.canonical(value)
            with self.assertRaises(identity.IdentityRefused): identity.collect(TX, 1, fake)
            self.assertNotIn('begin', fake.events); self.assertNotIn('stat-before', fake.events)

    def test_over_budget_witness_never_publishes(self):
        fake = Fake(); fake.elapsed = 901
        with self.assertRaises(identity.IdentityRefused): identity.collect(TX, 1, fake)
        self.assertIsNone(fake.receipt)

    def test_strict_stat_projection_rejects_duplicate_missing_and_noninteger_fields(self):
        good = stat_row()
        for changed in ({**good, 'Size': True}, {**good, 'Path': '../repository.tar'},
                        {**good, 'Name': 'another'}, {**good, 'ID': ''}, {**good, 'ID': ' '},
                        {**good, 'ID': 'line\nbreak'}):
            with self.assertRaises(identity.IdentityRefused): identity.stat_identity(json.dumps(changed).encode(), len(DATA))
        with self.assertRaises(identity.IdentityRefused):
            identity.stat_identity(b'{"ID":"first","ID":"second"}', len(DATA))

    def test_native_gate_and_unknown_commands_refuse_before_process_spawn(self):
        native = identity.Native()
        with patch.object(identity.os, 'geteuid', return_value=1000, create=True), \
                patch.object(identity.subprocess, 'Popen') as spawn:
            with self.assertRaises(identity.IdentityRefused):
                with native.locked(TX, 1): pass
            spawn.assert_not_called()
        native.config_fd = 7; native.fds = [8]; native.root = Path('/fixed/task'); native.expected = downloaded()
        with patch.object(identity.subprocess, 'Popen') as spawn:
            with self.assertRaises(identity.IdentityRefused): native.command(['deletefile', 'onedrive:other'], 'download')
            spawn.assert_not_called()

    def test_native_download_command_is_source_only_bounded_and_inherits_fences(self):
        native = identity.Native(); native.config_fd = 7; native.fds = [8, 9, 10]
        native.root = Path('/fixed/task'); native.expected = downloaded(); native.owner = {'boot_id': 'synthetic'}
        native._unchanged = lambda: None
        child = types.SimpleNamespace(pid=123, returncode=0, wait=lambda **kw: 0, poll=lambda: 0)
        manager = MagicMock(); manager.__enter__.return_value = child
        with patch.object(identity.subprocess, 'Popen', return_value=manager) as spawn, \
                patch.object(identity.hydrate, 'exclusive'), \
                patch.object(identity.Path, 'read_text', return_value='123 (fixture) '+'0 '*20):
            native.download(downloaded())
        arguments = spawn.call_args.args[0]
        self.assertEqual(arguments[:3], [identity.capture_plan.BINARY, 'copyto', identity.NAMESPACE+'/'+SHA+'/repository.tar'])
        self.assertEqual(arguments[3], str(native.root/'archive.tar'))
        self.assertIn('--immutable', arguments)
        self.assertEqual(arguments[arguments.index('--max-transfer')+1], str(len(DATA))+'B')
        for forbidden in ('--max-size', '--min-size', '--include', '--exclude', '--files-from', '--files-from-raw'):
            self.assertNotIn(forbidden, arguments)
        self.assertEqual(arguments[arguments.index('--cutoff-mode')+1], 'HARD')
        self.assertEqual(spawn.call_args.kwargs['pass_fds'], (8, 9, 10, 7))
        self.assertEqual(spawn.call_args.kwargs['stderr'], identity.subprocess.DEVNULL)

    def test_finite_exec_profile_refuses_oneshot_restart_pending_jobs_or_wrong_cgroup(self):
        unit = 'careertrellis-onedrive-bundle-identity-'+TX+'-01.service'
        good = {'LoadState': 'loaded', 'Type': 'exec', 'Restart': 'no', 'RemainAfterExit': 'no',
            'ActiveState': 'active', 'SubState': 'running', 'MainPID': '123', 'ControlPID': '0',
            'Job': '', 'KillMode': 'control-group', 'SendSIGKILL': 'yes', 'RuntimeMaxUSec': '15min',
            'TimeoutStartUSec': '30s', 'TimeoutStopUSec': '30s', 'InvocationID': 'e'*32,
            'ControlGroup': '/system.slice/'+unit,
            'ExecCondition': '', 'ExecStartPre': '', 'ExecStartPost': '', 'ExecStop': '', 'ExecStopPost': ''}
        self.assertEqual(identity.validate_unit_profile(good, 123, unit), 'e'*32)
        with patch.object(identity.subprocess, 'Popen') as spawn:
            for key, value in (('Type', 'oneshot'), ('Restart', 'always'), ('RemainAfterExit', 'yes'),
                    ('Job', '123'), ('ControlPID', '124'), ('ActiveState', 'activating'),
                    ('RuntimeMaxUSec', 'infinity'), ('TimeoutStartUSec', 'infinity'),
                    ('TimeoutStopUSec', 'infinity'), ('ControlGroup', '/system.slice/other.service'),
                    ('ExecCondition', '/bin/true'), ('ExecStartPre', '/bin/true'),
                    ('ExecStartPost', '/bin/true'), ('ExecStop', '/bin/true'), ('ExecStopPost', '/bin/true')):
                with self.subTest(key=key), self.assertRaises(identity.IdentityRefused):
                    identity.validate_unit_profile(dict(good, **{key: value}), 123, unit)
            spawn.assert_not_called()

    def test_caller_cannot_label_fake_backend_native(self):
        fake = Fake(); fake.mode = 'native-installed'
        with self.assertRaisesRegex(identity.IdentityRefused, 'caller_selected_native_adapter_refused'):
            identity.collect(TX, 1, fake)
        self.assertEqual(fake.events, [])

    def test_native_profile_reads_all_five_hooks_as_typed_arrays_not_show_omissions(self):
        backend=identity.Native.__new__(identity.Native)
        backend.unit='careertrellis-onedrive-bundle-identity-'+TX+'-01.service'
        scalars={key:'' for key in identity.PROFILE_FIELDS if key not in identity.common_io.HOOK_PROPERTIES}
        raw=''.join(key+'='+value+'\n' for key,value in scalars.items()).encode()
        calls=[]
        def capture(args):
            calls.append(args)
            return raw if args[0]=='/usr/bin/systemctl' else identity.common_io.EMPTY_EXEC_ARRAY
        backend.system=capture
        values=backend.unit_profile()
        self.assertEqual(set(values),set(identity.PROFILE_FIELDS))
        self.assertEqual(len(calls),6)
        for field in identity.common_io.HOOK_PROPERTIES:self.assertEqual(values[field],'')
        requested=calls[0][-1].removeprefix('--property=').split(',')
        self.assertFalse(set(requested)&identity.common_io.HOOK_PROPERTIES)
        backend.system=lambda args:raw if args[0]=='/usr/bin/systemctl' else b''
        with self.assertRaises(identity.common_io.UnitPropertyRefused):backend.unit_profile()


if __name__ == '__main__': unittest.main()
