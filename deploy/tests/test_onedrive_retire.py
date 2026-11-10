"""Offline safety transitions; never instantiate a provider/native adapter."""
from contextlib import contextmanager
import copy
import datetime as dt
import unittest
from unittest.mock import Mock,patch

from deploy.onedrive_retention import coordinator, policy, retire


NOW = dt.datetime(2026, 10, 26, 20, tzinfo=dt.timezone.utc)
TX = 'a' * 32


def key(number):
    return format(number, '064x')


def entry(number, accepted):
    return {'id': key(number), 'accepted_at': accepted, 'archive_sha256': key(100 + number),
            'download_sha256': key(100 + number), 'restore_archive_sha256': key(100 + number),
            'native_restore_receipt_sha256': key(300 + number), 'complete': True,
            'current': False, 'dependencies_resolved': True, 'dependencies': [],
            'objects': [{'path': key(number) + '/repository.tar', 'object_id': 'tar-' + str(number),
                         'size': 100, 'sha256': key(100 + number)},
                        {'path': key(number) + '/manifest.json', 'object_id': 'manifest-' + str(number),
                         'size': 20, 'sha256': key(200 + number)}]}


def catalog():
    return {'schema': 'careertrellis.onedrive-bundle-catalog/v1', 'observed_at': NOW.isoformat(),
            'entries': [entry(1, '2026-10-24T20:00:00Z'), entry(2, '2026-10-22T20:00:00Z'),
                        entry(3, '2026-10-20T20:00:00Z'), entry(4, '2026-10-16T20:00:00Z')],
            'holds': []}


def active_policy():
    return {'schema': 'careertrellis.onedrive-retention-policy/v1', 'enabled': True,
            'application_release': coordinator.RELEASE, 'valid_until': '2027-01-23T00:00:00Z',
            'reserve_percent': 20, 'peak_multiplier': 4, 'namespace_budget_bytes': policy.NAMESPACE_BUDGET,
            'keep_verified': 2, 'grace_seconds': policy.GRACE_SECONDS,
            'schedule_hour_utc': 0, 'schedule_minute_utc': 0, 'artifact_revision': 'a' * 40,
            'paired_policy_sha256': 'b' * 64}


class Fake:
    mode = 'offline-test'

    def __init__(self, value=None):
        self.catalog = copy.deepcopy(catalog() if value is None else value)
        self.policy = active_policy()
        self.inventory = {obj['path']: copy.deepcopy(obj) for item in self.catalog['entries']
                          for obj in item['objects']}
        self.records, self.deletes, self.calls = {}, [], []
        self.fail = None; self.blocked = False; self.pending = False; self.locked_now = False
        self.snapshot_count = 0; self.quota_count = 0; self.revalidate = None
        self.namespace = retire.NAMESPACE

    @contextmanager
    def locked(self, lock):
        self.assert_lock = lock
        assert lock == retire.LOCK and not self.locked_now
        self.locked_now = True
        try:
            yield
        finally:
            if self.pending: self.blocked = True
            self.locked_now = False

    def now(self): return NOW

    def trusted_snapshot(self, transaction):
        assert self.locked_now
        self.snapshot_count += 1
        if self.snapshot_count == 2 and self.revalidate is not None: self.revalidate(self)
        return {'policy': copy.deepcopy(self.policy), 'catalog': copy.deepcopy(self.catalog),
                'inventory': {'schema': 'careertrellis.onedrive-bundle-inventory/v1',
                    'namespace': self.namespace, 'observed_at': NOW.isoformat(),
                    'objects': copy.deepcopy(list(self.inventory.values()))},
                'repository_idle': True, 'unresolved_retirement': self.blocked}

    def quota(self):
        assert self.locked_now
        self.quota_count += 1
        return {'total': 10000, 'free': 4000, 'observed_at': NOW.isoformat()}

    def once(self, transaction, name, value):
        assert self.locked_now
        token = (transaction, name)
        if token in self.records: raise FileExistsError('private prior journal')
        self.records[token] = copy.deepcopy(value)
        if name == 'operation-intent.json': self.pending = True
        if (self.fail == 'intent_publication' and name.startswith('object-') and name.endswith('-intent.json')) \
                or (self.fail == 'dispatch_publication' and name.endswith('-dispatch.json')):
            raise TimeoutError('PRIVATE intent acknowledgement lost')
        if self.fail == 'ack_publication' and name.endswith('-ack.json'):
            raise TimeoutError('PRIVATE journal acknowledgement lost')
        if name == 'operation-complete.json': self.pending = False
        return retire.digest(value)

    def delete_exact(self, namespace, obj, request_id, *, recoverable, permanent):
        assert self.locked_now and namespace == retire.NAMESPACE
        assert recoverable is True and permanent is False
        assert self.inventory[obj['path']] == obj
        self.deletes.append(copy.deepcopy(obj))
        if self.fail != 'not_absent': del self.inventory[obj['path']]
        if self.fail == 'delete_ack_lost': raise TimeoutError('PRIVATE provider error')
        return {'result': 'deleted', 'object_id': 'wrong-id' if self.fail == 'wrong_id' else obj['object_id'],
                'request_id': request_id, 'recoverable': True, 'permanent': False}


class RetirementTests(unittest.TestCase):
    def test_existing_parent_requires_opaque_authority_and_no_normal_stage_fallback(self):
        with patch.object(retire,'run') as run:
            with self.assertRaises(retire.RetirementRefused):retire.run_existing_for_parent(TX,{'authenticated':True})
            run.assert_not_called()
        parent=object()
        with patch.object(retire.graph_delete,'_parent_authority') as authority,patch.object(retire,'run',return_value={'fixture':True}) as run:
            self.assertEqual(retire.run_existing_for_parent(TX,parent),{'fixture':True})
            authority.assert_called_once_with(parent,None,None,TX)
            self.assertIs(type(run.call_args.args[1]),retire.Native)
            self.assertIs(run.call_args.args[1].parent,parent)

    def test_permanent_native_contract_binds_mode_and_preserves_plan(self):
        fake=Fake();fake.policy['expiry_mode']='conditional_permanent'
        native=self.native_contract_fixture(fake)
        def deletion(namespace,obj,request_id,*,recoverable,permanent):
            self.assertIs(recoverable,False);self.assertIs(permanent,True)
            value=fake.delete_exact(namespace,obj,request_id,recoverable=True,permanent=False)
            value.update(recoverable=False,permanent=True,expiry_mode='conditional_permanent');return value
        native.delete_exact=deletion
        result=retire.run(TX,native)
        self.assertEqual(result['expiry_mode'],'conditional_permanent')
        self.assertEqual(fake.records[(TX,'operation-intent.json')]['expiry_mode'],'conditional_permanent')
        for (tx,name),record in fake.records.items():
            if name.endswith(('-intent.json','-ack.json','-terminal.json')):
                self.assertEqual(record['expiry_mode'],'conditional_permanent')
        native.commit_catalog.assert_called_once_with(TX)
        self.assertEqual(native.require_expiry_capability.call_count,1+2*result['deletions_performed'])

    def native_contract_fixture(self, fake):
        # Exact class exercises schema/commit routing only. Every side effect is
        # replaced by the synthetic memory fixture; this is no native acceptance.
        native = retire.Native()
        for name in ('locked','trusted_snapshot','quota','once','delete_exact','now'):
            setattr(native,name,getattr(fake,name))
        original=native.delete_exact
        def deletion(*args,**kwargs):
            value=original(*args,**kwargs);value['expiry_mode']='recoverable_manual';return value
        native.delete_exact=deletion
        native.commit_catalog = Mock(return_value=['synthetic-only'])
        native.require_expiry_capability=Mock(return_value='f'*64)
        return native

    def test_missing_or_stale_expiry_proof_never_creates_retirement_ledger(self):
        for message in ('missing','stale'):
            fake=Fake();fake.policy['expiry_mode']='conditional_permanent';native=self.native_contract_fixture(fake)
            native.require_expiry_capability.side_effect=ValueError(message)
            with self.subTest(message=message),self.assertRaises(retire.RetirementRefused):retire.run(TX,native)
            self.assertEqual(fake.records,{});self.assertEqual(fake.deletes,[]);self.assertEqual(fake.quota_count,0)

    def test_expiry_refresh_is_checked_before_object_intent_and_again_before_dispatch(self):
        for fail_at in (2,3):
            fake=Fake();fake.policy['expiry_mode']='conditional_permanent';native=self.native_contract_fixture(fake)
            native.require_expiry_capability.side_effect=['f'*64]*(fail_at-1)+[ValueError('stale')]
            with self.subTest(fail_at=fail_at),self.assertRaises(retire.RetirementRefused):retire.run(TX,native)
            self.assertIn((TX,'operation-intent.json'),fake.records)
            self.assertNotIn((TX,'object-00000-dispatch.json'),fake.records)
            self.assertEqual((TX,'object-00000-intent.json') in fake.records,fail_at==3)
            self.assertEqual(fake.deletes,[])

    def test_zero_candidate_native_conditional_bootstrap_needs_no_expiry_proof(self):
        value=catalog();value['entries']=value['entries'][:2]
        fake=Fake(value);fake.policy['expiry_mode']='conditional_permanent';native=self.native_contract_fixture(fake)
        native.require_expiry_capability.side_effect=AssertionError('no expiry action')
        result=retire.run(TX,native)
        self.assertEqual(result['deletions_performed'],0);native.require_expiry_capability.assert_not_called()
        self.assertEqual(fake.deletes,[])

    def test_capability_preflight_only_reads_current_scope_without_grant_or_refresh(self):
        from deploy.onedrive_retention import entry
        from types import SimpleNamespace
        native=retire.Native();native.files=Mock()
        context=SimpleNamespace(authenticated=True,role='db',revision='a'*40)
        raw=b'[r2]\ntype=s3\n[onedrive]\ntype=onedrive\ndrive_id=private-fixture-drive\n'
        with patch.object(entry,'verify_install',return_value='a'*40),patch.object(entry,'_native_dependencies',return_value=context),\
             patch.object(retire.hydrate,'protected',return_value=(raw,'private-config-identity')),\
             patch.object(retire.graph_delete,'_permanent_capability',return_value='f'*64) as current,\
             patch.object(retire.graph_delete,'native_grant') as grant,\
             patch.object(retire.graph_delete,'refresh_existing_remote') as refresh:
            self.assertEqual(native.require_expiry_capability(),'f'*64)
        current.assert_called_once_with('private-fixture-drive','','a'*40)
        self.assertEqual(native.files.require_repository_lock.call_count,2)
        grant.assert_not_called();refresh.assert_not_called()

    def test_native_contract_commits_only_after_exact_absence_and_durable_complete(self):
        fake = Fake(); native = self.native_contract_fixture(fake)
        result = retire.run(TX,native)
        native.commit_catalog.assert_called_once_with(TX)
        self.assertEqual(result['schema'],'careertrellis.bundle-retirement-native/v1')
        self.assertEqual(result['deletions_performed'],4)
        self.assertIsNone(result['physical_bytes_reclaimed'])
        self.assertNotIn('simulated_retired_count',result)
        operation = fake.records[(TX,'operation-intent.json')]
        self.assertFalse(operation['simulation']); self.assertEqual(operation['catalog'],fake.catalog)
        self.assertEqual(result['operation_intent_sha256'],retire.digest(operation))
        self.assertEqual(result['completed_at'],NOW.isoformat())

    def test_native_contract_lost_delete_ack_never_commits_catalog_or_retries(self):
        fake = Fake();fake.fail='delete_ack_lost';native=self.native_contract_fixture(fake)
        with self.assertRaises(retire.RetirementRefused):retire.run(TX,native)
        native.commit_catalog.assert_not_called();self.assertEqual(len(fake.deletes),1)
        self.assertNotIn((TX,'operation-complete.json'),fake.records)

    def test_absence_exception_requires_contiguous_exact_current_native_ack_chain(self):
        from deploy.onedrive_retention import entry,execution,hydrate,catalog,bundle
        fake=Fake();native=self.native_contract_fixture(fake);retire.run(TX,native)
        class Files:
            @contextmanager
            def directory(self,*args,**kwargs):yield 7
            def require_repository_lock(self):pass
            def read_retirement(self,tx,name):
                if (tx,name) not in fake.records:raise FileNotFoundError()
                return bundle.canonical(fake.records[(tx,name)])
        files=Files()
        with patch.object(execution,'require_stage_owner'),patch.object(entry,'verify_install',return_value='a'*40), \
             patch.object(entry,'_native_dependencies'),patch.object(catalog,'NativeFiles',return_value=files), \
             patch.object(retire.os,'listdir',side_effect=lambda fd:[name for tx,name in fake.records if tx==TX]), \
             patch.object(hydrate,'protected',return_value=(bundle.canonical(fake.policy),None)), \
             patch.object(coordinator,'native_policy_binding'):
            expected={obj['path'] for obj in fake.deletes}
            self.assertEqual(retire.authenticated_retirement_absences(8,TX,fake.inventory),expected)
            original=fake.records.pop((TX,'object-00000-ack.json'))
            with self.assertRaises((retire.RetirementRefused,FileNotFoundError)):
                retire.authenticated_retirement_absences(8,TX,fake.inventory)
            fake.records[(TX,'object-00000-ack.json')]=dict(original,permanent=True)
            with self.assertRaises(retire.RetirementRefused):
                retire.authenticated_retirement_absences(8,TX,fake.inventory)

    def test_default_and_native_adapter_refuse_before_any_activity(self):
        with self.assertRaisesRegex(retire.RetirementRefused, '^retirement_requires_reconciliation$'):
            retire.run(TX)
        backend = Fake(); backend.mode = 'native'
        with self.assertRaises(retire.RetirementRefused): retire.run(TX, backend)
        self.assertEqual(backend.snapshot_count, 0)
        self.assertEqual(backend.records, {})

    def test_only_exact_obsolete_objects_retire_in_simulation_with_separate_quota(self):
        backend = Fake(); before = copy.deepcopy(backend.catalog)
        result = retire.run(TX, backend)
        self.assertEqual(backend.catalog, before)
        self.assertEqual({obj['object_id'] for obj in backend.deletes},
                         {'tar-3', 'manifest-3', 'tar-4', 'manifest-4'})
        self.assertEqual(result['simulated_retired_count'], 4)
        self.assertEqual(result['simulated_logical_bytes'], 240)
        self.assertEqual(result['provider_reported_free_delta_bytes'], 0)
        self.assertIsNone(result['physical_bytes_reclaimed'])
        self.assertFalse(result['native_retirement_accepted'])
        self.assertEqual(result['deletions_performed'], 0)
        for index, obj in enumerate(backend.deletes):
            intent = backend.records[(TX, 'object-%05d-intent.json' % index)]
            terminal = backend.records[(TX, 'object-%05d-terminal.json' % index)]
            self.assertEqual(intent['object'], obj)
            self.assertEqual(terminal['intent_sha256'], retire.digest(intent))
            self.assertTrue(terminal['absent']); self.assertTrue(terminal['simulation'])

    def test_named_hold_and_dependency_closure_preserve_old_generations(self):
        value = catalog(); value['entries'][2]['dependencies'] = [key(4)]
        value['holds'] = [{'name': 'cutover', 'owner': 'backup maintenance', 'reason': 'recovery',
                           'review_after': '2026-01-01T00:00:00Z', 'bundles': [key(3)]}]
        backend = Fake(value)
        self.assertEqual(retire.run(TX, backend)['simulated_retired_count'], 0)
        self.assertEqual(backend.deletes, [])

    def test_new_second_successor_requires_full_grace(self):
        value = catalog(); value['entries'][0]['accepted_at'] = '2026-10-26T19:00:00Z'
        backend = Fake(value); retire.run(TX, backend)
        self.assertEqual({obj['object_id'] for obj in backend.deletes}, {'tar-4', 'manifest-4'})

    def test_zero_or_one_verified_bundle_bootstraps_without_deletion(self):
        for count in (0, 1):
            value = catalog(); value['entries'] = value['entries'][:count]
            backend = Fake(value); result = retire.run(TX, backend)
            self.assertTrue(result['bootstrap'])
            self.assertEqual(result['simulated_retired_count'], 0)
            self.assertEqual(backend.deletes, [])

    def test_incomplete_bootstrap_never_claims_verified_catalog(self):
        value = catalog(); value['entries'] = value['entries'][:1]
        value['entries'][0]['complete'] = False
        backend = Fake(value)
        with self.assertRaises(retire.RetirementRefused): retire.run(TX, backend)
        self.assertEqual(backend.records, {})

    def test_legacy_namespace_unknown_objects_changed_identity_or_size_refuse(self):
        for change in (
                lambda b: setattr(b, 'namespace', 'onedrive:job-applier-staging/postgresql'),
                lambda b: b.inventory.update({'unowned': {'path': 'unowned', 'object_id': 'unowned', 'size': 1, 'sha256': key(1)}}),
                lambda b: b.inventory[key(3) + '/repository.tar'].update(object_id='changed'),
                lambda b: b.inventory[key(3) + '/repository.tar'].update(size=101),
                lambda b: b.inventory[key(3) + '/repository.tar'].update(sha256=key(999))):
            backend = Fake(); change(backend)
            with self.assertRaises(retire.RetirementRefused): retire.run(TX, backend)
            self.assertEqual(backend.deletes, [])

    def test_stricter_unsupported_policy_disabled_or_expired_policy_refuses(self):
        for changed in ({'enabled': False}, {'keep_verified': 3}, {'grace_seconds': 172801},
                        {'namespace_budget_bytes': 1000}, {'valid_until': '2026-10-25T00:00:00Z'}):
            backend = Fake(); backend.policy.update(changed)
            with self.assertRaises(retire.RetirementRefused): retire.run(TX, backend)
            self.assertEqual(backend.deletes, [])

    def test_fresh_revalidation_blocks_new_hold_or_policy_change_before_delete(self):
        callbacks = [lambda b: b.policy.update(enabled=False),
                     lambda b: b.catalog['entries'][2].update(current=True),
                     lambda b: b.inventory[key(1) + '/repository.tar'].update(sha256=key(999))]
        for change in callbacks:
            backend = Fake(); backend.revalidate = change
            with self.assertRaises(retire.RetirementRefused): retire.run(TX, backend)
            self.assertEqual(backend.deletes, [])
            self.assertTrue(backend.blocked)

    def test_lost_ack_wrong_ack_or_unobserved_absence_never_replay_or_tombstone(self):
        for failure in ('delete_ack_lost', 'ack_publication', 'wrong_id', 'not_absent'):
            with self.subTest(failure=failure):
                backend = Fake(); backend.fail = failure
                with self.assertRaisesRegex(retire.RetirementRefused, '^retirement_requires_reconciliation$') as caught:
                    retire.run(TX, backend)
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertEqual(len(backend.deletes), 1)
                self.assertFalse(any(name.endswith('-terminal.json') for _tx, name in backend.records))
                self.assertTrue(backend.blocked)
                with self.assertRaises(retire.RetirementRefused): retire.run(TX, backend)
                self.assertEqual(len(backend.deletes), 1)

    def test_uncertain_intent_or_dispatch_publication_never_dispatches_delete(self):
        for failure in ('intent_publication', 'dispatch_publication'):
            backend = Fake(); backend.fail = failure
            with self.assertRaises(retire.RetirementRefused): retire.run(TX, backend)
            self.assertEqual(backend.deletes, [])
            self.assertTrue(backend.blocked)
            with self.assertRaises(retire.RetirementRefused): retire.run(TX, backend)
            self.assertEqual(backend.deletes, [])

    def test_namespace_budget_refuses_instead_of_evicting_a_checkpoint(self):
        value = catalog(); value['entries'][3]['objects'][0]['size'] = policy.NAMESPACE_BUDGET
        backend = Fake(value)
        with self.assertRaises(retire.RetirementRefused): retire.run(TX, backend)
        self.assertEqual(backend.deletes, [])


if __name__ == '__main__':
    unittest.main()
