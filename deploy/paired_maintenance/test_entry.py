"""Bootstrap and dispatch tests use synthetic bytes and mocked host surfaces."""
import hashlib
import io
import json
import stat
import types
import unittest
from unittest.mock import Mock, patch

from deploy.paired_maintenance import entry, common, client, db_control

TX = 'b' * 32
HASH = 'c' * 64
RELEASE = 'a' * 40


class EntryTests(unittest.TestCase):
    def test_manifest_exact_files_and_hashes(self):
        files = {name: (name + '\n').encode() for name in entry.FILES}
        manifest = {'schema': 'careertrellis.paired-maintenance-install/v1',
                    'revision': RELEASE,
                    'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
        def read(path, *args):
            return common.canonical(manifest) if path.name == 'manifest.json' else files[path.name]
        metadata = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
        with patch.object(entry.os, 'lstat', return_value=metadata), patch.object(entry, '_protected_read', side_effect=read), patch.object(entry, '_module', side_effect=AssertionError('no imports')):
            self.assertEqual(entry.verify_install(), RELEASE)
            manifest['files']['other.py'] = 'a' * 64
            with self.assertRaises(entry.EntryRefused):
                entry.verify_install()
            del manifest['files']['other.py']
            files['entry.py'] = b'altered'
            with self.assertRaises(entry.EntryRefused):
                entry.verify_install()

    def test_duplicate_and_nonfinite_json_refused(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":Infinity}', b'[]']:
            with self.assertRaises((ValueError, entry.EntryRefused)):
                entry._decode(raw)

    def test_policy_requires_equal_public_projection(self):
        factory = Mock(side_effect=lambda value: types.SimpleNamespace(digest=value['hash'], data=value))
        with patch.object(entry, '_module', return_value=types.SimpleNamespace(Policy=factory)), patch.object(entry, '_protected_read', side_effect=[b'{"hash":"a"}', b'{"hash":"a"}']):
            self.assertEqual(entry.load_policy().digest, 'a')
        with patch.object(entry, '_module', return_value=types.SimpleNamespace(Policy=factory)), patch.object(entry, '_protected_read', side_effect=[b'{"hash":"a"}', b'{"hash":"b"}']):
            with self.assertRaises(entry.EntryRefused):
                entry.load_policy()

    def test_policy_manifest_revision_is_bound_to_verified_installed_revision(self):
        document = common.canonical({'hash': 'same', 'manifest_revision': RELEASE})
        factory = lambda value: types.SimpleNamespace(digest=value['hash'], data=value)
        with patch.object(entry, '_module', return_value=types.SimpleNamespace(Policy=factory)), patch.object(entry, '_protected_read', return_value=document), patch.object(entry, 'verify_install', return_value=RELEASE) as verify:
            self.assertEqual(entry.load_policy().data['manifest_revision'], RELEASE)
            verify.assert_called_once()
        with patch.object(entry, '_module', return_value=types.SimpleNamespace(Policy=factory)), patch.object(entry, '_protected_read', return_value=document), patch.object(entry, 'verify_install', return_value='b' * 40):
            with self.assertRaises(entry.EntryRefused): entry.load_policy()

    def test_writer_gate_only_reads_public_paths_and_fails_closed(self):
        module = types.SimpleNamespace(Policy=lambda value: value, validate_gate=Mock(return_value=True))
        with patch.object(entry, '_module', return_value=module), patch.object(entry, '_protected_read', return_value=b'{}') as read, patch.object(entry, '_boot', return_value='boot'), patch.object(entry, '_release', return_value=RELEASE):
            self.assertEqual(entry.writer_gate(), 0)
            self.assertEqual([call.args[0] for call in read.call_args_list], [entry.PUBLIC_POLICY, entry.PUBLIC_MARKER])
            module.validate_gate.return_value = False
            self.assertEqual(entry.writer_gate(), 1)
        with patch.object(entry, '_protected_read', side_effect=PermissionError('PRIVATE')):
            self.assertEqual(entry.writer_gate(), 1)

    def test_missing_readonly_transaction_never_creates(self):
        request = {'transaction': TX, 'operation': 'status'}
        def module(name):
            return common if name == 'common' else db_control
        with patch.object(entry, '_module', side_effect=module), patch.object(entry.os, 'lstat', side_effect=FileNotFoundError()), patch.object(entry.os, 'mkdir') as mkdir:
            store = entry._transaction_store(Mock(), request)
            self.assertIsInstance(store, entry.NoStore)
            self.assertFalse(store.exists('anything.json'))
            with self.assertRaises(entry.EntryRefused):
                store.read('anything.json')
            mkdir.assert_not_called()

    def test_new_begin_checks_window_before_mkdir(self):
        policy = Mock()
        policy.require_new_start.side_effect = RuntimeError('PRIVATE')
        with patch.object(entry, '_module', side_effect=lambda name: common if name == 'common' else db_control), patch.object(entry.os, 'lstat', side_effect=FileNotFoundError()), patch.object(entry.os, 'mkdir') as mkdir:
            with self.assertRaises(RuntimeError):
                entry._transaction_store(policy, {'transaction': TX, 'operation': 'begin'})
            mkdir.assert_not_called()

    def test_endpoint_wraps_bound_payload_under_lock(self):
        request = {'schema': db_control.SCHEMA, 'transaction': TX,
                   'policy_sha256': HASH, 'operation': 'status', 'payload': {}}
        policy = types.SimpleNamespace(digest=HASH)
        backend = types.SimpleNamespace(worker_status=lambda tx: {'state': 'idle'},
                                        mirror_status=Mock(), health_status=Mock())
        modules = {'common': common, 'client': client, 'db_control': db_control,
                   'db_host': types.SimpleNamespace(DBBackend=Mock(return_value=backend))}
        with patch.object(entry, '_module', side_effect=modules.__getitem__), patch.object(entry.os, 'geteuid', return_value=0, create=True), patch.object(entry.socket, 'gethostname', return_value=entry.DB_HOST), patch.object(entry, 'load_policy', return_value=policy), patch.object(entry, '_control_lock', return_value=123), patch.object(entry.os, 'close') as close, patch.object(entry, '_transaction_store', return_value=entry.NoStore()):
            value = json.loads(entry.db_endpoint(common.canonical(request)))
            self.assertEqual(value, {'schema': entry.RESPONSE_SCHEMA, 'transaction': TX,
                                    'policy_sha256': HASH, 'payload': {'state': 'idle'}})
            close.assert_called_once_with(123)

    def test_endpoint_failure_is_private_and_no_mutation_on_bad_binding(self):
        request = {'schema': db_control.SCHEMA, 'transaction': TX,
                   'policy_sha256': HASH, 'operation': 'status', 'payload': {}}
        modules = {'common': common, 'client': client, 'db_control': db_control}
        with patch.object(entry, '_module', side_effect=modules.__getitem__), patch.object(entry.os, 'geteuid', return_value=0, create=True), patch.object(entry.socket, 'gethostname', return_value=entry.DB_HOST), patch.object(entry, 'load_policy', return_value=types.SimpleNamespace(digest='d' * 64)), patch.object(entry, '_control_lock') as lock:
            raw = entry.db_endpoint(common.canonical(request))
            self.assertEqual(json.loads(raw)['payload'], {'state': 'unknown', 'operator_required': True})
            self.assertNotIn(b'PRIVATE', raw)
            lock.assert_not_called()

    def test_dispatch_has_no_caller_path_or_argv(self):
        host = types.SimpleNamespace(run_web=Mock())
        with patch.object(entry.os, 'geteuid', return_value=0, create=True), patch.object(entry.socket, 'gethostname', return_value=entry.WEB_HOST), patch.object(entry, 'load_policy', return_value='policy'), patch.object(entry, '_module', return_value=host):
            self.assertEqual(entry.main(['web-recover']), 0)
            host.run_web.assert_called_once_with('policy', recovery_only=True)
            self.assertEqual(entry.main(['web-run', '/arbitrary']), 1)
            self.assertEqual(entry.main(['db-worker', '../escape']), 1)

    def test_ssh_gate_does_not_load_private_policy(self):
        gate = types.SimpleNamespace(run=Mock(return_value=b'{}\n'))
        incoming = types.SimpleNamespace(buffer=io.BytesIO(b'{}'))
        outgoing = types.SimpleNamespace(buffer=io.BytesIO())
        with patch.object(entry.sys, 'stdin', incoming), patch.object(entry.sys, 'stdout', outgoing), patch.object(entry, '_module', return_value=gate), patch.object(entry, 'load_policy', side_effect=AssertionError('private read')):
            self.assertEqual(entry.main(['ssh-gate']), 0)
            self.assertEqual(outgoing.buffer.getvalue(), b'{}\n')

    def test_db_inspect_discards_private_internal_fields(self):
        value = {'state': 'fenced', 'transaction': TX, 'boot_id': '11111111-2222-3333-4444-555555555555',
                 'operator_required': False, 'private_internal': 'PRIVATE'}
        with patch.object(entry.socket, 'gethostname', return_value=entry.DB_HOST), patch.object(entry, '_protected_read', return_value=common.canonical(value)), patch.object(entry, '_module', return_value=common):
            output = entry._inspect()
            self.assertNotIn(b'PRIVATE', output)
            self.assertEqual(set(json.loads(output)), {'schema', 'state', 'transaction', 'boot_id', 'operator_required'})
        value['state'] = 'PRIVATE'
        with patch.object(entry.socket, 'gethostname', return_value=entry.DB_HOST), patch.object(entry, '_protected_read', return_value=common.canonical(value)):
            with self.assertRaises(entry.EntryRefused):
                entry._inspect()

    def test_protected_read_guards_metadata_and_detects_replacement(self):
        path = entry.PUBLIC_POLICY
        good = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_gid=0,
                    st_nlink=1, st_size=3, st_dev=1, st_ino=2, st_mtime_ns=3, st_ctime_ns=4)
        directory = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        def metadata(value):
            return good if value == path else directory
        with patch.object(entry.os, 'lstat', side_effect=metadata), patch.object(entry.os, 'fstat', return_value=good), patch.object(entry.os, 'open', return_value=123), patch.object(entry.os, 'close'), patch.object(entry.os, 'fdopen', side_effect=lambda *args, **kw: io.BytesIO(b'{}\n')):
            self.assertEqual(entry._protected_read(path, {0o644}), b'{}\n')
            for field, value in [('st_uid', 1), ('st_gid', 1), ('st_nlink', 2), ('st_mode', stat.S_IFLNK | 0o644), ('st_size', 9999999)]:
                previous = getattr(good, field)
                setattr(good, field, value)
                with self.assertRaises(entry.EntryRefused):
                    entry._protected_read(path, {0o644})
                setattr(good, field, previous)
            replacement = types.SimpleNamespace(**vars(good))
            replacement.st_ino = 77
            with patch.object(entry.os, 'fstat', return_value=replacement):
                with self.assertRaises(entry.EntryRefused):
                    entry._protected_read(path, {0o644})


if __name__ == '__main__':
    unittest.main()
