"""Synthetic catalogs and disposable native-file fixtures; no live repositories."""
import copy
from contextlib import contextmanager
import datetime as dt
import os
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from deploy.onedrive_retention import bundle, catalog, local_restore_plan as plans, native_restore


NOW = dt.datetime(2026, 10, 27, 12, tzinfo=dt.timezone.utc)
BOOT = '11111111-1111-4111-8111-111111111111'
LABEL = '20260101-000000F'


class Memory:
    authentication = 'synthetic-offline-test-only'
    def __init__(self): self.sources = {}; self.store = {}; self.failed_write = None; self.locked_now = False
    def read(self, path, limit=catalog.MAX_RECORD):
        raw = self.sources[str(path)] if str(path) in self.sources else None
        if raw is None: raise FileNotFoundError()
        catalog.need(len(raw) <= limit); return raw
    def directory_identity(self, path): return 1, 2
    def names_at(self, path): return {Path(name).name for name in self.sources if Path(name).parent == path}
    def names(self, branch): return {Path(name).name for name in self.store if name.startswith(branch + '/')}
    def read_store(self, name): return self.store[name]
    def write_store(self, name, raw):
        catalog.need(self.locked_now)
        if self.failed_write and name.startswith(self.failed_write): raise OSError('synthetic failure')
        if name in self.store: raise FileExistsError()
        self.store[name] = raw
    @contextmanager
    def locked(self):
        catalog.need(not self.locked_now); self.locked_now = True
        try: yield
        finally: self.locked_now = False


def fixture(memory, tx='a'*32, archive='b'*64, now=NOW):
    plan = plans.plan(LABEL, archive, 'c'*64, tx, 20000000)
    files = {name: {'size': 1, 'sha256': 'd'*64} for name in bundle.REQUIRED_METADATA}
    files['backup/careertrellis/' + LABEL + '/backup.manifest'] = {'size': 1, 'sha256': 'd'*64}
    manifest = {'schema': bundle.SCHEMA, 'backup_label': LABEL, 'generation_sha256': 'c'*64,
                'files': files, 'total_bytes': len(files)}
    download = {'schema': 'careertrellis.onedrive-bundle-download/v1', 'result': 'download_verified',
        'transaction': tx, 'archive_sha256': archive, 'generation_sha256': 'c'*64, 'backup_label': LABEL,
        'archive_bytes': 10240, 'manifest_sha256': catalog.sha(bundle.canonical(manifest)),
        'native_restore_accepted': False, 'completed_at': (now-dt.timedelta(seconds=120)).isoformat(),
        'destination': catalog.NAMESPACE + '/' + archive + '/repository.tar', 'quota_after': {'total': 100, 'free': 40}}
    download_raw = bundle.canonical(download)
    native = {'schema': 'careertrellis.onedrive-native-local-restore/v1', 'result': 'verified',
        'transaction': tx, 'attempt': 1, 'archive_sha256': archive, 'generation_sha256': 'c'*64,
        'backup_label': LABEL, 'plan_sha256': plans.digest(plan), 'download_receipt_sha256': catalog.sha(download_raw),
        'prior_reconciliation_sha256': None, 'native_restore_accepted': True, 'postmaster_terminal': True,
        'checksums_verified': True, 'cleanup_performed': False, 'elapsed_seconds': 12,
        'completed_at': (now-dt.timedelta(seconds=90)).isoformat()}
    identity = {'schema': 'careertrellis.onedrive-bundle-identity/v1',
        'observed_at': (now-dt.timedelta(seconds=60)).isoformat(), 'download_receipt_sha256': catalog.sha(download_raw),
        'path': archive + '/repository.tar', 'object_id': 'synthetic-provider-id-' + archive,
        'size': 10240, 'sha256': archive, 'identity_verified_before_after_download': True}
    owner = {'pid': 123, 'start_ticks': 456, 'process_group': 123, 'boot_id': BOOT}
    ownership = {'schema': 'careertrellis.native-local-restore-ownership/v1', 'owner': owner,
        'plan': plan, 'plan_sha256': plans.digest(plan), 'root_identity': [1, 2],
        'download_receipt_sha256': catalog.sha(download_raw), 'web_preflight_sha256': 'e'*64}
    # Deliberately a TEST schema. Only the patched fake validator accepts it.
    terminal = {'schema': 'synthetic-terminal-test-proof', 'observed_at': (now-dt.timedelta(seconds=30)).isoformat()}
    source, root = catalog.SOURCE_ROOT / tx, Path(plan['attempt_root'])
    documents = {source / 'downloaded-receipt.json': download,
        source / 'downloaded-manifest.json': manifest, source / 'bundle-object-identity.json': identity,
        root / 'complete.json': native, root / 'ownership.json': ownership, root / 'unit-terminal-proof.json': terminal,
        root / 'unit-stop-intent.json': {'schema': 'synthetic-unit-stop-test-intent'}}
    for phase in catalog.PHASES:
        documents[root / (phase+'-intent.json')] = {'owner': owner, 'plan_sha256': plans.digest(plan),
            'phase': phase, 'observed_at': (now-dt.timedelta(seconds=100)).isoformat()}
        documents[root / (phase+'-dispatch.json')] = dict(owner, pid=124, process_group=124)
        documents[root / (phase+'-terminal.json')] = dict(owner, pid=124, process_group=124, exit_code=0)
    memory.sources.update({str(path): bundle.canonical(value) for path, value in documents.items()})
    return plan, manifest, download, native, identity


def inventory(memory, observed):
    rows = []
    for name, raw in memory.store.items():
        if name.startswith('accepted/'):
            rows.extend(catalog.decode(raw)['entry']['objects'])
    return {'schema': 'careertrellis.onedrive-bundle-inventory/v1', 'namespace': catalog.NAMESPACE,
            'observed_at': observed.isoformat(), 'objects': copy.deepcopy(rows)}


class CatalogTests(unittest.TestCase):
    def test_v2_acceptance_requires_exact_metadata_digest_field(self):
        plan,manifest,download,native,identity=self.fixture
        native=dict(native,schema='careertrellis.onedrive-native-local-restore/v2',
                    info_metadata_sha256='f'*64)
        catalog.acceptance_entry(plan,manifest,bundle.canonical(download),
                                 bundle.canonical(native),identity,NOW.isoformat())
        del native['info_metadata_sha256']
        with self.assertRaises(catalog.CatalogRefused):
            catalog.acceptance_entry(plan,manifest,bundle.canonical(download),
                                     bundle.canonical(native),identity,NOW.isoformat())

    def test_v2_journals_require_original_metadata_bytes(self):
        root=catalog.RESTORE_ROOT/('c'*32)
        raw=b'{"synthetic":"original"}\n'
        self.memory.sources[str(root/'info-metadata.json')]=raw
        native={'schema':'careertrellis.onedrive-native-local-restore/v2','info_metadata_sha256':'f'*64}
        with self.assertRaises(catalog.CatalogRefused):
            catalog.verify_journals(self.memory,root,{},native,b'',b'',NOW)

    def test_native_reader_allows_only_exact_metadata_filename(self):
        from pathlib import PurePosixPath
        reader=object.__new__(catalog.NativeFiles)
        with patch.object(reader,'_protected',return_value=b'original') as read, \
             patch.object(catalog,'Path',PurePosixPath), \
             patch.object(catalog,'RESTORE_ROOT',PurePosixPath('/fixed-restore')):
            root=catalog.RESTORE_ROOT/('c'*32)
            self.assertEqual(reader.read(root/'info-metadata.json'),b'original')
            read.assert_called_once()
            for name in ('info-metadata-extra.json','info-metadata.json.extra','metadata.json'):
                with self.assertRaises(catalog.CatalogRefused):reader.read(root/name)

    def setUp(self):
        self.memory = Memory(); self.now = NOW
        self.store = catalog.Catalog(self.memory, lambda: self.now)
        self.fixture = fixture(self.memory)
        self.patch = patch.object(native_restore, 'validate_terminal_proof',
            side_effect=lambda proof, receipt, owner, now=None, stop_intent=None: proof, create=True)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def test_pure_claim_validation_never_authenticates(self):
        plan, manifest, download, native, identity = self.fixture
        value = catalog.acceptance_entry(plan, manifest, bundle.canonical(download),
            bundle.canonical(native), identity, NOW.isoformat())
        self.assertFalse(value['authenticated']); self.assertEqual(value['entry']['id'], 'b'*64)

    def test_v2_identity_requires_valid_before_after_etag_witness(self):
        plan, manifest, download, native, identity = self.fixture
        identity.update(schema='careertrellis.onedrive-bundle-identity/v2',
                        etag='"opaque-version"',etag_verified_before_after_download=True)
        result = catalog.acceptance_entry(plan,manifest,bundle.canonical(download),
            bundle.canonical(native),identity,NOW.isoformat())
        self.assertFalse(result['authenticated'])
        for change in ({'etag':'*'},{'etag':'bad\r\nheader'},
                       {'etag_verified_before_after_download':False}):
            changed = dict(identity,**change)
            with self.assertRaises(catalog.CatalogRefused):
                catalog.acceptance_entry(plan,manifest,bundle.canonical(download),
                    bundle.canonical(native),changed,NOW.isoformat())

    def test_numbered_identity_selection_never_rewrites_or_ignores_partial_later_attempt(self):
        source = catalog.SOURCE_ROOT/('a'*32)
        old = self.memory.sources[str(source/'bundle-object-identity.json')]
        refreshed = catalog.decode(old); refreshed['etag']='"new"'
        raw = bundle.canonical(refreshed)
        self.memory.sources[str(source/'bundle-object-identity-02.json')] = raw
        self.memory.sources[str(source/'identity-verification-02'/'receipt.json')] = raw
        original_names = self.memory.names_at
        self.memory.names_at = lambda path: original_names(path)|{'identity-verification-02'}
        self.assertEqual(catalog.read_identity(self.memory,'a'*32),refreshed)
        self.assertEqual(self.memory.sources[str(source/'bundle-object-identity.json')],old)
        self.memory.names_at = lambda path: original_names(path)|{'identity-verification-02','identity-verification-03'}
        with self.assertRaisesRegex(catalog.CatalogRefused,'later_identity_attempt'):
            catalog.read_identity(self.memory,'a'*32)
        self.memory.names_at = lambda path: original_names(path)|{'identity-verification-02'}
        self.memory.sources[str(source/'identity-verification-02'/'receipt.json')] = old
        with self.assertRaisesRegex(catalog.CatalogRefused,'identity_alias_receipt_mismatch'):
            catalog.read_identity(self.memory,'a'*32)

    def tombstone(self):
        self.store.accept('a'*32); self.memory.supports_tombstones = True
        name = 'b'*64+'.json'; accepted = self.memory.read_store('accepted/'+name)
        value = {'schema':'careertrellis.onedrive-retired-bundle/v1', 'archive_sha256':'b'*64,
            'acceptance_record_sha256':catalog.sha(accepted), 'retirement_receipt_sha256':'f'*64,
            'retired_at':NOW.isoformat(), 'authentication':self.memory.authentication}
        raw = bundle.canonical(value); intent = {'record_sha256':catalog.sha(raw),'archive_sha256':'b'*64}
        self.memory.store['tombstones/'+name] = raw
        self.memory.store['tombstone-intents/'+name] = bundle.canonical(intent)
        self.memory.store['tombstone-complete/'+name] = bundle.canonical({
            'intent_sha256':catalog.sha(bundle.canonical(intent)),'record_sha256':catalog.sha(raw)})
        return name

    def test_committed_tombstone_preserves_history_and_excludes_retired_inventory(self):
        name = self.tombstone()
        current = inventory(self.memory,NOW); current['objects'] = []
        self.assertEqual(self.store.snapshot(current)['entries'],[])
        self.assertIn('accepted/'+name,self.memory.store)
        with self.assertRaises(catalog.CatalogRefused):
            self.store.snapshot(current,current_ids=['b'*64])
        with self.assertRaises(catalog.CatalogRefused):
            self.store.add_hold({'name':'late-hold','owner':'test','reason':'test',
                'review_after':NOW.isoformat(),'bundles':['b'*64]})

    def test_partial_tombstone_blocks_planning_and_fake_native_commit_refuses(self):
        name = self.tombstone(); del self.memory.store['tombstone-complete/'+name]
        with self.assertRaisesRegex(catalog.CatalogRefused,'retirement_catalog_interruption'):
            self.store.snapshot(inventory(self.memory,NOW))
        self.memory.authentication = 'root-protected-native-journals'
        with self.assertRaises(catalog.CatalogRefused):self.store.commit_retirement('a'*32)
        with self.assertRaisesRegex(catalog.CatalogRefused,'caller_selected_native_catalog_refused'):
            self.store.accept('a'*32)

    def test_admission_is_unique_durable_and_bootstrap_never_invokes_planner(self):
        admitted = self.store.accept('a'*32)
        self.assertFalse(admitted['native_authenticated'])
        self.assertEqual(len(self.memory.store), 3)
        with self.assertRaisesRegex(catalog.CatalogRefused, 'duplicate_archive_is_not_a_successor'):
            self.store.accept('a'*32)
        with patch.object(catalog.policy, 'plan_retirement', side_effect=AssertionError('must not run')):
            result = self.store.retirement_plan(inventory(self.memory, NOW))
        self.assertEqual(result['candidates'], []); self.assertEqual(result['deletions_performed'], 0)

    def test_missing_identity_or_terminal_proof_never_writes_catalog(self):
        for suffix, category in (('bundle-object-identity.json', 'provider_identity_receipt_required'),
                                 ('unit-terminal-proof.json', 'native_terminal_proof_required')):
            memory = Memory(); fixture(memory)
            path = next(path for path in memory.sources if Path(path).name == suffix); del memory.sources[path]
            with self.assertRaisesRegex(catalog.CatalogRefused, category):
                catalog.Catalog(memory, lambda: NOW).accept('a'*32)
            self.assertEqual(memory.store, {})

    def test_native_simulation_wrong_digest_and_stale_identity_refuse(self):
        for suffix, mutate in (
            ('complete.json', lambda v: v.update(schema='careertrellis.local-restore-simulation/v1')),
            ('complete.json', lambda v: v.update(native_restore_accepted=False)),
            ('complete.json', lambda v: v.update(postmaster_terminal=False)),
            ('complete.json', lambda v: v.update(download_receipt_sha256='f'*64)),
            ('bundle-object-identity.json', lambda v: v.update(identity_verified_before_after_download=False)),
            ('bundle-object-identity.json', lambda v: v.update(observed_at='2020-01-01T00:00:00Z'))):
            memory = Memory(); fixture(memory)
            path = next(path for path in memory.sources if Path(path).name == suffix)
            value = catalog.decode(memory.sources[path]); mutate(value); memory.sources[path] = bundle.canonical(value)
            with self.assertRaises(catalog.CatalogRefused): catalog.Catalog(memory, lambda: NOW).accept('a'*32)
            self.assertEqual(memory.store, {})

    def test_interrupted_append_blocks_replay_and_planning(self):
        self.memory.failed_write = 'complete/'
        with self.assertRaises(catalog.CatalogRefused): self.store.accept('a'*32)
        self.memory.failed_write = None
        with self.assertRaisesRegex(catalog.CatalogRefused, 'catalog_interruption_requires_reconciliation'):
            self.store.accept('a'*32)
        with self.assertRaises(catalog.CatalogRefused): self.store.retirement_plan(inventory(self.memory, NOW))

    def test_unknown_missing_changed_or_stale_inventory_refuses(self):
        self.store.accept('a'*32); baseline = inventory(self.memory, NOW)
        variants = []
        value = copy.deepcopy(baseline); value['objects'] = []; variants.append(value)
        value = copy.deepcopy(baseline); value['objects'][0]['object_id'] = 'changed'; variants.append(value)
        value = copy.deepcopy(baseline); value['observed_at'] = (NOW-dt.timedelta(seconds=901)).isoformat(); variants.append(value)
        value = copy.deepcopy(baseline); value['objects'].append({'path': 'f'*64+'/repository.tar',
            'object_id': 'unknown', 'size': 1, 'sha256': 'f'*64}); variants.append(value)
        for value in variants:
            with self.assertRaises(catalog.CatalogRefused): self.store.retirement_plan(value)

    def test_two_successors_plus_full_48h_grace_and_named_hold(self):
        first = 'b'*64; self.store.accept('a'*32)
        self.now += dt.timedelta(hours=1); fixture(self.memory, tx='d'*32, archive='e'*64, now=self.now)
        self.store.accept('d'*32)
        self.now += dt.timedelta(hours=1); fixture(self.memory, tx='f'*32, archive='1'*64, now=self.now)
        self.store.accept('f'*32)
        self.assertEqual(self.store.retirement_plan(inventory(self.memory, self.now))['candidates'], [])
        self.now += dt.timedelta(hours=48)
        plan = self.store.retirement_plan(inventory(self.memory, self.now))
        self.assertEqual([row['bundle_id'] for row in plan['candidates']], [first])
        self.store.add_hold({'name': 'named-recovery', 'owner': 'operator', 'reason': 'synthetic recovery hold',
            'review_after': '2020-01-01T00:00:00Z', 'bundles': [first]})
        self.assertEqual(self.store.retirement_plan(inventory(self.memory, self.now))['candidates'], [])

    def test_native_host_gate_precedes_filesystem_reads(self):
        with patch.object(catalog.os, 'geteuid', return_value=1000, create=True), \
                patch.object(catalog.NativeFiles, 'require_repository_lock') as locks:
            with self.assertRaises(catalog.CatalogRefused): catalog.NativeStore(9)
            locks.assert_not_called()

    def test_failed_or_contained_native_journal_refuses(self):
        root = Path(self.fixture[0]['attempt_root'])
        self.memory.sources[str(root/'contained-start.json')] = b'{}\n'
        with self.assertRaises(catalog.CatalogRefused): self.store.accept('a'*32)
        self.assertEqual(self.memory.store, {})

    def test_missing_reviewed_terminal_validator_or_stop_intent_refuses(self):
        with patch.object(native_restore, 'validate_terminal_proof', None, create=True):
            with self.assertRaisesRegex(catalog.CatalogRefused, 'native_terminal_validator_unavailable'):
                self.store.accept('a'*32)
        root = Path(self.fixture[0]['attempt_root'])
        del self.memory.sources[str(root/'unit-stop-intent.json')]
        with self.assertRaisesRegex(catalog.CatalogRefused, 'native_terminal_stop_intent_required'):
            self.store.accept('a'*32)
        self.assertEqual(self.memory.store, {})

    def test_false_exit_code_is_not_integer_zero_and_future_terminal_proof_refuses(self):
        root = Path(self.fixture[0]['attempt_root'])
        path = str(root/'checksums-terminal.json')
        original = self.memory.sources[path]
        value = catalog.decode(original); value['exit_code'] = False
        self.memory.sources[path] = bundle.canonical(value)
        with self.assertRaises(catalog.CatalogRefused): self.store.accept('a'*32)
        self.memory.sources[path] = original
        path = str(root/'unit-terminal-proof.json')
        value = catalog.decode(self.memory.sources[path]); value['observed_at'] = (NOW+dt.timedelta(seconds=1)).isoformat()
        self.memory.sources[path] = bundle.canonical(value)
        with self.assertRaisesRegex(catalog.CatalogRefused, 'native_terminal_proof_stale'):
            self.store.accept('a'*32)
        self.assertEqual(self.memory.store, {})


class RetirementModeTests(unittest.TestCase):
    def fixture(self,mode=None):
        operation={} if mode is None else {'expiry_mode':mode}
        complete=copy.deepcopy(operation);intent=copy.deepcopy(operation);terminal=copy.deepcopy(operation)
        permanent=mode=='conditional_permanent'
        ack={'result':'deleted','object_id':'synthetic-id','request_id':'a'*64,
             'recoverable':not permanent,'permanent':permanent}
        if mode is not None:ack['expiry_mode']=mode
        return operation,complete,intent,ack,terminal

    def test_old_manual_and_each_explicit_mode_keep_exact_ack_contract(self):
        for mode in (None,'recoverable_manual','conditional_permanent'):
            operation,complete,intent,ack,terminal=self.fixture(mode)
            self.assertEqual(catalog.retirement_expiry_mode(operation,complete),mode or 'recoverable_manual')
            catalog.validate_retirement_object_mode(operation,intent,ack,terminal,'synthetic-id','a'*64)

    def test_permanent_receipt_cannot_be_mixed_with_legacy_or_recoverable_claims(self):
        for change in ('legacy-ack','missing-complete','missing-intent','wrong-terminal','changed-not-deleted'):
            operation,complete,intent,ack,terminal=self.fixture('conditional_permanent')
            if change=='legacy-ack':ack.update(recoverable=True,permanent=False)
            elif change=='missing-complete':complete.pop('expiry_mode')
            elif change=='missing-intent':intent.pop('expiry_mode')
            elif change=='wrong-terminal':terminal['expiry_mode']='recoverable_manual'
            else:ack['result']='changed_not_deleted'
            with self.subTest(change=change),self.assertRaises(catalog.CatalogRefused):
                catalog.retirement_expiry_mode(operation,complete)
                catalog.validate_retirement_object_mode(operation,intent,ack,terminal,'synthetic-id','a'*64)

    def test_unrecognized_or_boolean_expiry_mode_refuses(self):
        for mode in (True,'purge-all',''):
            with self.assertRaises(catalog.CatalogRefused):catalog.retirement_expiry_mode({'expiry_mode':mode},{'expiry_mode':mode})

    def test_numeric_acknowledgement_flags_are_not_boolean_proof(self):
        operation,complete,intent,ack,terminal=self.fixture('conditional_permanent')
        ack.update(recoverable=0,permanent=1)
        with self.assertRaises(catalog.CatalogRefused):
            catalog.validate_retirement_object_mode(operation,intent,ack,terminal,'synthetic-id','a'*64)


class ExistingOnlyCatalogTests(unittest.TestCase):
    def test_missing_catalog_never_creates_or_repairs_storage(self):
        reader=object.__new__(catalog.ExistingNativeFiles)
        with patch.dict('sys.modules',{'fcntl':object()}),patch.object(reader,'require_repository_lock'), \
             patch.object(reader,'directory',side_effect=FileNotFoundError()), \
             patch.object(catalog.os,'mkdir') as mkdir,self.assertRaises(FileNotFoundError):
            with reader.locked():pass
        mkdir.assert_not_called()

    def test_read_only_catalog_cannot_write_an_acceptance_or_tombstone(self):
        reader=object.__new__(catalog.ExistingNativeFiles)
        with patch.object(catalog.os,'open') as opened,self.assertRaises(catalog.CatalogRefused):
            reader.write_store('accepted/'+'a'*64+'.json',b'not evidence')
        opened.assert_not_called()

    @unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda:-1)()==0,
                         'requires disposable root-owned POSIX lock fixture')
    def test_native_existing_lock_is_exclusive_without_initializing_missing_state(self):
        import fcntl
        with tempfile.TemporaryDirectory(prefix='ct-catalog-readonly-',dir='/') as temporary:
            root=Path(temporary);lock=root/'repository.lock';lock.write_bytes(b'');lock.chmod(0o600)
            fd=os.open(lock,os.O_RDWR|os.O_NOFOLLOW)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with patch.object(catalog,'ROOT',root/'catalog'), \
                     patch.object(catalog,'REPOSITORY_LOCK',lock), \
                     patch.object(catalog.socket,'gethostname',return_value='job-applier-db-staging-01'):
                    reader=catalog.NativeReadStore(fd)
                    with self.assertRaises(FileNotFoundError):
                        with reader.io.locked():pass
                    self.assertFalse((root/'catalog').exists())
                    writer=catalog.NativeStore(fd)
                    with writer.io.locked():pass
                    before=sorted((root/'catalog').iterdir())
                    with reader.io.locked():
                        with self.assertRaises(BlockingIOError):
                            with catalog.NativeReadStore(fd).io.locked():pass
                    self.assertEqual(sorted((root/'catalog').iterdir()),before)
            finally:os.close(fd)


if __name__ == '__main__': unittest.main()
