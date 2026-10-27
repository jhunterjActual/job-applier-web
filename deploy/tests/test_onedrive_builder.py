"""Plan derivation and exclusive guarded IO fixtures; no native/provider actions."""
import copy
import datetime as dt
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from deploy.onedrive_retention import builder, bundle, local_restore_plan as plans


NOW = dt.datetime(2026, 10, 27, 12, tzinfo=dt.timezone.utc)
FULL = '20261026-155056F'
DIFF = FULL + '_20261027-031500D'
TX = 'a' * 32


def info():
    return [{'name': 'careertrellis', 'status': {'code': 0}, 'backup': [
        {'label': FULL, 'timestamp': {'stop': 1793031000}, 'error': False, 'info': {'size': 1000}},
        {'label': DIFF, 'timestamp': {'stop': 1793071200}, 'error': False, 'info': {'size': 750}}]}]


def facts(label=DIFF):
    sizes = builder.database_sizes(info(), label, observed_at=NOW.isoformat(), now=NOW)
    return dict(sizes, schema=builder.SIZE_SCHEMA, source='pgbackrest_info', transaction=TX,
                generation_sha256='b' * 64)


def manifest(label=DIFF):
    files = {path: {'size': 1, 'sha256': 'd' * 64} for path in bundle.REQUIRED_METADATA}
    for selected in (FULL, label):
        files['backup/careertrellis/' + selected + '/backup.manifest'] = {'size': 1, 'sha256': 'd' * 64}
    return {'schema': bundle.SCHEMA, 'backup_label': label, 'generation_sha256': 'b' * 64,
            'files': files, 'total_bytes': len(files)}


def download(label=DIFF):
    return {'schema': 'careertrellis.onedrive-bundle-download/v1', 'result': 'download_verified',
        'transaction': TX, 'backup_label': label, 'generation_sha256': 'b' * 64,
        'archive_sha256': 'c' * 64, 'archive_bytes': 10240,
        'manifest_sha256': builder.digest(manifest(label)), 'native_restore_accepted': False,
        'completed_at': NOW.isoformat(), 'destination': builder.native_restore.PREFIX + 'c' * 64 + '/repository.tar',
        'quota_after': {'total': 10000, 'free': 5000}}


class OwnedFiles:
    mode = 'offline-test'
    def __init__(self):
        self.files = {'downloaded-receipt.json': bundle.canonical(download()),
                      'downloaded-manifest.json': bundle.canonical(manifest()),
                      'source-database-size.json': bundle.canonical(facts())}
        self.guards = 0; self.writes = []; self.fail_guard = None; self.on_guard = None
    def guard(self):
        self.guards += 1
        if self.on_guard: self.on_guard(self)
        if self.guards == self.fail_guard: raise RuntimeError('PRIVATE entry guard unavailable')
    def read(self, name):
        if name not in self.files: raise FileNotFoundError('PRIVATE missing metadata')
        return self.files[name]
    def write_once(self, name, raw):
        if name in self.files: raise FileExistsError('PRIVATE prior immutable plan')
        self.files[name] = raw; self.writes.append(name)


class SizeFactsTests(unittest.TestCase):
    def test_full_and_differential_use_larger_ancestor_without_guessing(self):
        for label in (FULL, DIFF):
            prepared = builder.plan_from_facts(download(label), manifest(label), facts(label), 2, now=NOW)
            self.assertEqual(prepared['maximum_database_bytes'], 1000)
            self.assertEqual(prepared['attempt'], 2)
            self.assertEqual(prepared['full_ancestor_label'], FULL)
            self.assertFalse(prepared['execution_authorized'])
        self.assertEqual(builder.database_sizes(info(), observed_at=NOW.isoformat(), now=NOW)['backup_label'], DIFF)

    def test_selected_larger_than_ancestor_is_not_truncated(self):
        value = facts(); value['selected_database_bytes'] = 1500
        self.assertEqual(builder.plan_from_facts(download(), manifest(), value, now=NOW)['maximum_database_bytes'], 1500)

    def test_ambiguous_latest_completion_refuses_instead_of_guessing(self):
        value = info(); value[0]['backup'][1]['timestamp']['stop'] = value[0]['backup'][0]['timestamp']['stop']
        with self.assertRaises(builder.BuilderRefused):
            builder.database_sizes(value, observed_at=NOW.isoformat(), now=NOW)

    def test_duplicates_missing_ancestor_bad_health_boolean_unknown_or_future_sizes_refuse(self):
        mutations = [lambda rows: rows[0]['backup'].append(copy.deepcopy(rows[0]['backup'][0])),
            lambda rows: rows[0]['backup'].pop(0),
            lambda rows: rows[0]['backup'][0].update(error=True),
            lambda rows: rows[0]['backup'][1]['info'].update(size=True),
            lambda rows: rows[0]['backup'][1]['info'].update(size='750'),
            lambda rows: rows[0]['backup'][1]['info'].update(size=0),
            lambda rows: rows[0]['backup'][1]['info'].update(size=1024 ** 4 + 1),
            lambda rows: rows[0]['backup'][1]['timestamp'].update(stop=int(NOW.timestamp()) + 1),
            lambda rows: rows[0]['status'].update(code=False)]
        for index, change in enumerate(mutations):
            value = info(); change(value)
            with self.subTest(index=index), self.assertRaises(builder.BuilderRefused):
                builder.database_sizes(value, DIFF, observed_at=NOW.isoformat(), now=NOW)

    def test_tampered_labels_generation_transaction_provenance_or_facts_refuse(self):
        changes = [lambda f: f.update(transaction='f' * 32),
                   lambda f: f.update(generation_sha256='f' * 64),
                   lambda f: f.update(full_ancestor_label='20261025-155056F'),
                   lambda f: f.update(source='unknown'),
                   lambda f: f.update(selected_database_bytes=True),
                   lambda f: f.update(observed_at='2999-01-01T00:00:00Z'),
                   lambda f: f.update(extra='unknown')]
        for index, change in enumerate(changes):
            value = facts(); change(value)
            with self.subTest(index=index), self.assertRaises(builder.BuilderRefused):
                builder.plan_from_facts(download(), manifest(), value, now=NOW)

    def test_download_receipt_digests_identity_and_acknowledgement_bind_plan(self):
        for changed in ({'manifest_sha256': 'f' * 64}, {'archive_bytes': True},
                        {'native_restore_accepted': True}, {'result': 'source_only'},
                        {'destination': 'onedrive:wrong/repository.tar'},
                        {'completed_at': '2999-01-01T00:00:00Z'}):
            value = download(); value.update(changed)
            with self.assertRaises(builder.BuilderRefused):
                builder.plan_from_facts(value, manifest(), facts(), now=NOW)


class ImmutableWriterTests(unittest.TestCase):
    def test_owned_fixture_writes_exact_numbered_plan_without_native_claim(self):
        owned = OwnedFiles()
        result = builder.write_plan(TX, 2, io=owned, now=NOW)
        self.assertEqual(owned.writes, ['native-plan-02.json'])
        prepared = builder.decode(owned.files['native-plan-02.json'], 131072)
        self.assertEqual(prepared['maximum_database_bytes'], 1000)
        self.assertEqual(result['plan_sha256'], plans.digest(prepared))
        self.assertFalse(result['native_file_write']); self.assertFalse(result['native_restore_accepted'])
        previous = dict(owned.files)
        with self.assertRaises(builder.BuilderRefused): builder.write_plan(TX, 2, io=owned, now=NOW)
        self.assertEqual(owned.files, previous)

    def test_missing_legacy_facts_or_receipt_refuses_without_output(self):
        for name in ('source-database-size.json', 'downloaded-receipt.json'):
            owned = OwnedFiles(); del owned.files[name]; before = dict(owned.files)
            with self.assertRaises(builder.BuilderRefused): builder.write_plan(TX, io=owned, now=NOW)
            self.assertEqual(owned.files, before); self.assertEqual(owned.writes, [])

    def test_guard_failure_before_reads_or_immediately_before_write_never_outputs(self):
        for guard in (1, 2):
            owned = OwnedFiles(); owned.fail_guard = guard; before = dict(owned.files)
            with self.assertRaisesRegex(builder.BuilderRefused, '^native_plan_write_requires_reconciliation$'):
                builder.write_plan(TX, io=owned, now=NOW)
            self.assertEqual(owned.files, before); self.assertEqual(owned.writes, [])

    def test_changed_protected_input_and_duplicate_json_refuse_without_output(self):
        owned = OwnedFiles()
        def mutate(io):
            if io.guards == 2: io.files['source-database-size.json'] = bundle.canonical(dict(facts(), selected_database_bytes=500))
        owned.on_guard = mutate
        with self.assertRaises(builder.BuilderRefused): builder.write_plan(TX, io=owned, now=NOW)
        self.assertEqual(owned.writes, [])
        owned = OwnedFiles()
        owned.files['source-database-size.json'] = owned.files['source-database-size.json'].replace(
            b'"source":', b'"source":"duplicate","source":')
        with self.assertRaises(builder.BuilderRefused): builder.write_plan(TX, io=owned, now=NOW)
        self.assertEqual(owned.writes, [])

    def test_default_native_guard_failure_does_not_read_or_write(self):
        owned = OwnedFiles(); owned.fail_guard = 1
        with patch.object(builder, 'NativeFiles', return_value=owned), \
                patch.object(owned, 'read', wraps=owned.read) as read:
            with self.assertRaises(builder.BuilderRefused): builder.write_plan(TX, now=NOW)
            read.assert_not_called()
        self.assertEqual(owned.writes, [])

    def test_installed_idle_false_refuses_before_any_native_read_or_write(self):
        native = builder.NativeFiles.__new__(builder.NativeFiles)
        native.transaction = TX; native.attempt = 1
        native.root = Path(plans.ROOT) / TX; native.output = 'native-plan.json'
        with patch.object(builder, 'NativeFiles', return_value=native), \
                patch.object(builder.entry, 'ROOT', Path(builder.__file__).absolute().parent), \
                patch.object(builder.entry, 'verify_install', return_value='e' * 40) as verified, \
                patch.object(builder.entry, 'retention_idle', return_value=False) as idle, \
                patch.object(builder.native_restore, 'protected') as read, \
                patch.object(builder.native_restore, 'exclusive') as write:
            with self.assertRaisesRegex(builder.BuilderRefused, '^native_plan_write_requires_reconciliation$'):
                builder.write_plan(TX, now=NOW)
            verified.assert_called_once_with(); idle.assert_called_once_with(TX, 'restore')
            read.assert_not_called(); write.assert_not_called()

    def test_native_io_whitelist_prevents_caller_paths(self):
        native = builder.NativeFiles.__new__(builder.NativeFiles)
        native.root = Path('/fixed/private'); native.output = 'native-plan.json'
        with patch.object(builder.native_restore, 'protected') as read:
            with self.assertRaises(builder.BuilderRefused): native.read('../outside')
            read.assert_not_called()
        with patch.object(builder.native_restore, 'protected', return_value=b'fixture') as read:
            self.assertEqual(native.read('source-database-size.json'), b'fixture')
            self.assertEqual(read.call_args.args[0], native.root / 'source-database-size.json')
            self.assertEqual(read.call_args.kwargs, {'maximum': 8192, 'modes': (0o600,), 'uids': (0,), 'gids': (0,)})


if __name__ == '__main__': unittest.main()
