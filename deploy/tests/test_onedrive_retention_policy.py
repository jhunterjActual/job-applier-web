"""Boundary and adversarial controls for the offline retention planner."""
import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    'onedrive_retention_policy',
    Path(__file__).resolve().parents[1] / 'onedrive_retention' / 'policy.py')
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)
NOW = '2026-10-26T20:00:00Z'


def key(index):
    return format(index, '064x')


def entry(index, accepted):
    return {'id': key(index), 'accepted_at': accepted,
            'archive_sha256': key(index + 100),
            'download_sha256': key(index + 100),
            'restore_archive_sha256': key(index + 100),
            'native_restore_receipt_sha256': key(index + 200),
            'complete': True, 'current': False, 'dependencies_resolved': True,
            'dependencies': [], 'objects': [
                {'path': key(index) + '/repository.tar',
                 'object_id': 'object-' + str(index), 'size': 100,
                 'sha256': key(index + 100)}]}


def catalog():
    return {'schema': 'careertrellis.onedrive-bundle-catalog/v1',
            'observed_at': NOW, 'entries': [
                entry(1, '2026-10-24T20:00:00Z'),
                entry(2, '2026-10-22T20:00:00Z'),
                entry(3, '2026-10-20T20:00:00Z'),
                entry(4, '2026-10-16T20:00:00Z')], 'holds': []}


class CapacityTests(unittest.TestCase):
    def test_reserve_rounds_up_and_exact_boundary_allows(self):
        result = policy.capacity(101, 25, 2, 100, 2)
        self.assertEqual(result['reserve_bytes'], 21)
        self.assertEqual(result['required_free_bytes'], 25)
        self.assertTrue(result['allowed'])
        self.assertEqual(policy.capacity(101, 24, 2, 100, 2)['shortfall_bytes'], 1)

    def test_real_quota_requires_peak_in_addition_to_reserve(self):
        result = policy.capacity(5368709120, 1825361100, 80000000, 300000000, 80000000)
        self.assertTrue(result['allowed'])
        self.assertEqual(result['required_free_bytes'], 1233741824)
        self.assertFalse(policy.capacity(5368709120, 429496729, 0, 0, 0)['allowed'])

    def test_namespace_counts_candidate_without_crediting_retirement(self):
        result = policy.capacity(10 ** 10, 10 ** 10, 1, policy.NAMESPACE_BUDGET, 1)
        self.assertEqual(result['reasons'], ['namespace_budget_exceeded'])
        self.assertTrue(policy.capacity(10 ** 10, 10 ** 10, 0,
                                        policy.NAMESPACE_BUDGET, 0)['allowed'])

    def test_alert_thresholds_are_exact(self):
        self.assertEqual(policy.capacity(100, 21, 0, 0, 0)['severity'], 'ok')
        self.assertEqual(policy.capacity(100, 20, 0, 0, 0)['severity'], 'warning')
        self.assertEqual(policy.capacity(100, 15, 0, 0, 0)['severity'], 'urgent')

    def test_missing_negative_boolean_or_understated_measurements_refuse(self):
        for args in [(0, 0, 0, 0, 0), (100, 101, 0, 0, 0),
                     (100, 100, None, 0, 1), (100, 100, 0, 0, 1),
                     (100, True, 0, 0, 0), (100, 100, 1, -1, 1)]:
            with self.subTest(args=args), self.assertRaises(policy.Refused):
                policy.capacity(*args)


class RetirementTests(unittest.TestCase):
    def test_exact_grace_and_newest_two_retained_without_mutation(self):
        value = catalog(); before = copy.deepcopy(value)
        result = policy.plan_retirement(value, NOW)
        self.assertEqual(result['retained_bundle_ids'], [key(1), key(2)])
        self.assertEqual([item['bundle_id'] for item in result['candidates']], [key(3), key(4)])
        self.assertEqual(result['candidates'][0]['objects'], value['entries'][2]['objects'])
        self.assertEqual(result['deletions_performed'], 0)
        self.assertTrue(result['requires_locked_revalidation'])
        self.assertEqual(value, before)
        result['candidates'][0]['objects'][0]['size'] = 9
        self.assertEqual(value, before)

    def test_one_microsecond_before_grace_retains_third(self):
        value = catalog()
        value['entries'][0]['accepted_at'] = '2026-10-24T20:00:00.000001Z'
        result = policy.plan_retirement(value, NOW)
        self.assertIn(key(3), result['retained_bundle_ids'])

    def test_second_newer_acceptance_starts_grace_not_first_newer(self):
        value = catalog()
        value['entries'][0]['accepted_at'] = '2026-10-26T19:00:00Z'
        result = policy.plan_retirement(value, NOW)
        self.assertIn(key(3), result['retained_bundle_ids'])
        self.assertEqual([item['bundle_id'] for item in result['candidates']], [key(4)])
        self.assertEqual(result['candidates'][0]['successor_id'], key(2))

    def test_expired_hold_review_never_expires_hold_or_its_dependency(self):
        value = catalog()
        value['entries'][2]['dependencies'] = [key(4)]
        value['holds'] = [{'name': 'cutover', 'owner': 'backup maintenance',
                           'reason': 'named recovery checkpoint',
                           'review_after': '2026-01-01T00:00:00Z', 'bundles': [key(3)]}]
        self.assertEqual(policy.plan_retirement(value, NOW)['candidates'], [])

    def test_current_and_transitive_dependencies_retained(self):
        value = catalog(); value['entries'][2]['current'] = True
        value['entries'][2]['dependencies'] = [key(4)]
        self.assertEqual(policy.plan_retirement(value, NOW)['candidates'], [])

    def test_catalog_digest_is_stable_but_binds_changed_evidence(self):
        value = catalog()
        result = policy.plan_retirement(value, NOW)
        self.assertEqual(result['catalog_sha256'], policy.plan_retirement(copy.deepcopy(value), NOW)['catalog_sha256'])
        value['entries'][3]['objects'][0]['object_id'] = 'changed'
        self.assertNotEqual(result['catalog_sha256'], policy.plan_retirement(value, NOW)['catalog_sha256'])

    def test_unsafe_or_incomplete_evidence_refuses_entire_plan(self):
        changes = [
            lambda v: v['entries'][2].update(complete=False),
            lambda v: v['entries'][2].update(dependencies_resolved=False),
            lambda v: v['entries'][2].update(download_sha256=key(999)),
            lambda v: v['entries'][2].update(restore_archive_sha256=key(999)),
            lambda v: v['entries'][2].update(native_restore_receipt_sha256=None),
            lambda v: v['entries'][2].update(accepted_at='2027-01-01T00:00:00Z'),
            lambda v: v.update(observed_at='2027-01-01T00:00:00Z'),
            lambda v: v.update(observed_at='2026-10-26T19:44:59Z'),
            lambda v: v['entries'][2].update(accepted_at='2026-10-24T20:00:00Z'),
            lambda v: v['entries'][2].update(dependencies=[key(999)]),
            lambda v: v['entries'][2].update(dependencies=[{}]),
            lambda v: v['entries'][2]['objects'][0].update(path='../escape'),
            lambda v: v['entries'][2]['objects'][0].update(path='/absolute'),
            lambda v: v['entries'][2]['objects'][0].update(path='folder\\escape'),
            lambda v: v['entries'][2]['objects'][0].update(path=key(999) + '/repository.tar'),
            lambda v: v['entries'][2]['objects'][0].update(path=key(3) + '/unexpected.txt'),
            lambda v: v['entries'][2]['objects'][0].update(object_id='object-1'),
            lambda v: v['entries'][2]['objects'][0].update(size=policy.NAMESPACE_BUDGET),
            lambda v: v['entries'][2]['objects'][0].update(sha256=key(999)),
            lambda v: v['entries'][2].update(current=1),
            lambda v: v['entries'][2].update(unknown_field='ignored?'),
            lambda v: v.update(entries=v['entries'][:1]),
        ]
        for index, mutate in enumerate(changes):
            with self.subTest(case=index):
                value = catalog(); mutate(value)
                with self.assertRaises(policy.Refused):
                    policy.plan_retirement(value, NOW)


if __name__ == '__main__':
    unittest.main()
