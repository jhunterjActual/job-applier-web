"""Offline safety transitions; never instantiate a provider/native adapter."""
from contextlib import contextmanager
import copy
import datetime as dt
import unittest

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
    def test_default_and_native_adapter_refuse_before_any_activity(self):
        with self.assertRaisesRegex(retire.RetirementRefused, '^native_retirement_adapter_unsupported$'):
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
