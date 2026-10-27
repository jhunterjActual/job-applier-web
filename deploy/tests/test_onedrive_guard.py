"""Pure guard fixtures; no root, provider, subprocess or file operations."""
import copy
import datetime as dt
import hashlib
import unittest

from deploy.onedrive_retention import bundle, guard, local_restore_plan as plans, policy, retire


NOW = dt.datetime(2026, 10, 26, 23, tzinfo=dt.timezone.utc).isoformat()
TX = 'a' * 32
OWNER = {'pid': 222, 'start_ticks': 333, 'process_group': 222,
         'boot_id': '01234567-89ab-cdef-0123-456789abcdef'}


def key(number): return format(number, '064x')


def sha(value): return hashlib.sha256(bundle.canonical(value)).hexdigest()


def record(stage='legacy_pg_mirror', *, transaction=TX, archive=None):
    return {'schema': 'careertrellis.onedrive-persistent-attempt/v1',
            'transaction': transaction, 'attempt': 1, 'stage': stage,
            'archive_sha256': archive, 'observed_at': NOW, 'owner': copy.deepcopy(OWNER),
            'journal': {'state': 'complete', 'phase': 'complete', 'intent_sha256': key(10),
                'dispatch_sha256': key(11), 'terminal_sha256': key(12),
                'exit_code': 0, 'receipt_sha256': key(13)},
            'unit': {'state': 'inactive', 'original_process': 'terminal', 'cgroup_empty': True,
                     'terminal_proof_sha256': key(14)},
            'restore': None, 'reconciliation': None}


def evidence(operation='bundle_capture'):
    return {'schema': 'careertrellis.onedrive-growth-guard-evidence/v1',
        'operation': operation, 'transaction': TX, 'attempt': 1, 'observed_at': NOW,
        'limits': {'enabled': True, 'valid_until': '2027-01-01T00:00:00Z',
                   'reserve_percent': 20, 'peak_multiplier': 4,
                   'namespace_budget_bytes': policy.NAMESPACE_BUDGET},
        'quota': {'total': 10000, 'free': 8000, 'observed_at': NOW},
        'allocation': {'source_bytes': 100, 'candidate_bytes': 120},
        'catalog': {'schema': 'careertrellis.onedrive-bundle-catalog/v1',
                    'observed_at': NOW, 'entries': [], 'holds': []},
        'inventory': {'schema': 'careertrellis.onedrive-bundle-inventory/v1',
                      'namespace': retire.NAMESPACE, 'observed_at': NOW, 'objects': []},
        'scan': {'schema': 'careertrellis.onedrive-persistent-attempt-scan/v1',
                 'observed_at': NOW, 'complete': True, 'coverage': list(guard.COVERAGE),
                 'unknown_count': 0, 'records': []}}


def accepted(value):
    entry = {'id': key(1), 'accepted_at': '2026-10-20T00:00:00Z', 'archive_sha256': key(1),
        'download_sha256': key(1), 'restore_archive_sha256': key(1),
        'native_restore_receipt_sha256': key(31), 'complete': True, 'current': True,
        'dependencies_resolved': True, 'dependencies': [],
        'objects': [{'path': key(1) + '/repository.tar', 'object_id': 'accepted-one', 'size': 100, 'sha256': key(1)}]}
    value['catalog']['entries'].append(entry)
    value['inventory']['objects'].extend(copy.deepcopy(entry['objects']))
    completed = record('native_restore', transaction='b' * 32, archive=key(1))
    completed['journal']['receipt_sha256'] = key(31)
    completed['restore'] = {'restore_dispatched': True, 'start_dispatched': True,
                            'data_present': True, 'postmaster': 'terminal'}
    value['scan']['records'].append(completed)


def reconciled_retry():
    value = evidence('bundle_restore'); value['attempt'] = 2
    plan = plans.plan('20261026-155056F', key(1), key(2), TX, 20000000, attempt=2)
    old = plans.plan('20261026-155056F', key(1), key(2), TX, 20000000)
    intent = {'schema': 'careertrellis.native-local-restore-intent/v1', 'owner': copy.deepcopy(OWNER),
              'plan_sha256': plans.digest(old), 'observed_at': NOW, 'attempt': 1}
    ownership = {'schema': 'careertrellis.native-local-restore-ownership/v1', 'owner': copy.deepcopy(OWNER),
                 'plan': old, 'plan_sha256': plans.digest(old), 'root_identity': [1, 2],
                 'download_receipt_sha256': key(20), 'web_preflight_sha256': key(21)}
    proof = {'schema': 'careertrellis.onedrive-native-reconciliation/v1', 'transaction': TX,
        'prior_attempt': 1, 'next_attempt': 2, 'archive_sha256': key(1),
        'prior_plan_sha256': plans.digest(old), 'prior_owner': copy.deepcopy(OWNER),
        'prior_ownership_sha256': sha(ownership), 'prior_intent_sha256': sha(intent),
        'prior_terminal': True, 'prior_cgroup_empty': True, 'no_restore_dispatch': True,
        'no_postmaster': True, 'observed_at': NOW}
    dispatch = {'pid': 444, 'start_ticks': 555, 'process_group': 444, 'boot_id': OWNER['boot_id']}
    terminal = dict(dispatch, exit_code=31)
    failed = record('native_restore', archive=key(1))
    failed['journal'] = {'state': 'failed', 'phase': 'info', 'intent_sha256': sha(intent),
                        'dispatch_sha256': sha(dispatch), 'terminal_sha256': sha(terminal),
                        'exit_code': 31, 'receipt_sha256': None}
    failed['unit']['terminal_proof_sha256'] = sha(proof)
    failed['restore'] = {'restore_dispatched': False, 'start_dispatched': False,
                         'data_present': False, 'postmaster': 'absent'}
    failed['reconciliation'] = {'plan': plan, 'proof': proof, 'ownership': ownership,
                                'intent': intent, 'info_dispatch': dispatch, 'info_terminal': terminal}
    value['scan']['records'] = [record('hydration', archive=key(1)),
                               record('publication', archive=key(1)), failed]
    value['inventory']['objects'] = [{'path': key(1) + '/repository.tar',
                                     'object_id': 'candidate-one', 'size': 100, 'sha256': key(1)}]
    return value


class CapacityTests(unittest.TestCase):
    def test_exact_rounding_and_four_times_larger_allocation(self):
        result = guard.capacity(101, 25, 1, 1, 0, 0)
        self.assertTrue(result['allowed']); self.assertEqual(result['reserve_bytes'], 21)
        self.assertEqual(result['required_free_bytes'], 25)
        self.assertEqual(guard.capacity(1000, 1000, 30, 20, 0, 0)['projected_peak_bytes'], 120)
        self.assertEqual(guard.capacity(101, 24, 1, 1, 0, 0)['shortfall_bytes'], 1)

    def test_all_holds_staging_and_new_allocations_count_without_deletion_credit(self):
        self.assertFalse(guard.capacity(10 ** 10, 10 ** 10, 1, 1,
                                        policy.NAMESPACE_BUDGET, 1)['allowed'])
        self.assertTrue(guard.capacity(10 ** 10, 10 ** 10, 0, 0,
                                       policy.NAMESPACE_BUDGET, 0)['allowed'])

    def test_noninteger_negative_or_weakened_limits_refuse(self):
        for args in ((100, True, 1, 1, 0, 0), (100, '90', 1, 1, 0, 0),
                     (100, 90, 1.0, 1, 0, 0), (100, 90, -1, 1, 0, 0)):
            with self.assertRaises(ValueError): guard.capacity(*args)
        for limits in ({'reserve_percent': 19}, {'peak_multiplier': 3},
                       {'namespace_budget_bytes': policy.NAMESPACE_BUDGET + 1}):
            with self.assertRaises(ValueError): guard.capacity(100, 90, 1, 1, 0, 0, **limits)


class PersistentGuardTests(unittest.TestCase):
    def test_zero_and_first_accepted_bootstrap_can_capture_but_never_authorize_native_or_deletion(self):
        for add in (False, True):
            value = evidence()
            if add: accepted(value)
            result = guard.assess(value, NOW)
            self.assertTrue(result['allowed'], result)
            self.assertEqual(result['accepted_bundle_count'], int(add))
            self.assertFalse(result['minimum_two_verified_bundles_present'])
            for field in ('authenticated', 'native_guard_installed', 'dispatch_authorized', 'deletion_authorized'):
                self.assertFalse(result[field])

    def test_available_or_absent_unit_without_original_process_proof_does_not_clear_attempt(self):
        for change in ({'state': 'absent', 'original_process': 'unknown'},
                       {'original_process': 'reused'}, {'cgroup_empty': False},
                       {'terminal_proof_sha256': None}):
            value = evidence(); previous = record(); previous['unit'].update(change)
            value['scan']['records'] = [previous]
            self.assertFalse(guard.assess(value, NOW)['allowed'])

    def test_running_unknown_or_failed_journal_remains_blocked_without_timeout_clearing(self):
        for state in ('running', 'unknown', 'failed'):
            value = evidence(); previous = record(); previous['journal'].update(state=state, exit_code=1)
            value['scan']['records'] = [previous]
            result = guard.assess(value, NOW)
            self.assertFalse(result['allowed'])
            self.assertIn('persistent_attempt_requires_reconciliation', result['reasons'])

    def test_missing_catalog_incomplete_scan_unknown_journal_or_stale_quota_fail_closed(self):
        changes = [lambda v: v.pop('catalog'), lambda v: v['scan'].update(complete=False),
                   lambda v: v['scan']['coverage'].pop(), lambda v: v['scan'].update(unknown_count=1),
                   lambda v: v['quota'].update(observed_at='2026-10-26T22:44:59Z'),
                   lambda v: v['quota'].update(observed_at='2026-10-26T23:00:01Z'),
                   lambda v: v['quota'].update(free=True), lambda v: v['quota'].update(free='8000'),
                   lambda v: v['limits'].update(enabled=False), lambda v: v['limits'].update(peak_multiplier=3)]
        for change in changes:
            value = evidence(); change(value)
            self.assertFalse(guard.assess(value, NOW)['allowed'])

    def test_bundle_namespace_growth_cannot_understate_complete_source_allocation(self):
        value = evidence(); value['allocation']['candidate_bytes'] = 0
        self.assertFalse(guard.assess(value, NOW)['allowed'])

    def test_reconciled_info_failure_allows_only_designated_next_attempt_same_capture(self):
        value = reconciled_retry(); before = copy.deepcopy(value)
        result = guard.assess(value, NOW)
        self.assertTrue(result['allowed'], result)
        self.assertEqual(value, before)
        for change in (lambda v: v.update(transaction='b' * 32),
                       lambda v: v.update(attempt=1), lambda v: v.update(attempt=3),
                       lambda v: v.update(operation='bundle_capture'),
                       lambda v: v.update(operation='legacy_pg_mirror')):
            changed = copy.deepcopy(value); change(changed)
            self.assertFalse(guard.assess(changed, NOW)['allowed'])

    def test_failed_restored_data_unknown_unit_or_missing_proof_never_uses_info_exception(self):
        changes = [lambda r: r['restore'].update(data_present=True),
                   lambda r: r['restore'].update(restore_dispatched=True),
                   lambda r: r['restore'].update(start_dispatched=True),
                   lambda r: r['restore'].update(postmaster='unknown'),
                   lambda r: r['unit'].update(original_process='unknown'),
                   lambda r: r.update(reconciliation=None),
                   lambda r: r['reconciliation']['proof'].update(no_restore_dispatch=False)]
        for change in changes:
            value = reconciled_retry(); change(value['scan']['records'][-1])
            self.assertFalse(guard.assess(value, NOW)['allowed'])

    def test_unaccepted_previous_capture_blocks_other_growth_and_unknown_staging_is_counted(self):
        value = evidence('legacy_object_mirror')
        value['scan']['records'] = [record('hydration', transaction='b' * 32, archive=key(1))]
        result = guard.assess(value, NOW)
        self.assertIn('unaccepted_capture_blocks_other_growth', result['reasons'])
        value = evidence()
        value['inventory']['objects'] = [{'path': key(1) + '/repository.tar', 'object_id': 'unresolved',
                                         'size': policy.NAMESPACE_BUDGET, 'sha256': key(1)}]
        result = guard.assess(value, NOW)
        self.assertIn('unresolved_staging_objects', result['reasons'])
        self.assertIn('namespace_budget_exceeded', result['reasons'])

    def test_missing_native_catalog_proof_or_missing_stage_evidence_blocks(self):
        value = evidence(); accepted(value); value['scan']['records'] = []
        self.assertIn('catalog_native_attempt_proof_missing', guard.assess(value, NOW)['reasons'])
        self.assertFalse(guard.assess(evidence('bundle_publish'), NOW)['allowed'])
        self.assertFalse(guard.assess(evidence('bundle_restore'), NOW)['allowed'])

    def test_accepted_next_attempt_never_silently_clears_failed_history_for_other_growth(self):
        value = reconciled_retry()
        accepted_copy = evidence(); accepted(accepted_copy)
        replacement = accepted_copy['scan']['records'][0]
        replacement.update(transaction=TX, attempt=2)
        accepted_copy['catalog']['entries'][0]['objects'] = copy.deepcopy(value['inventory']['objects'])
        value['catalog'] = accepted_copy['catalog']
        value['scan']['records'].append(replacement)
        value.update(operation='bundle_capture', transaction='d' * 32, attempt=1)
        before = copy.deepcopy(value)
        result = guard.assess(value, NOW)
        self.assertFalse(result['allowed'])
        self.assertIn('historical_failure_resolution_evidence_required', result['reasons'])
        self.assertEqual(value, before)

    def test_native_entry_is_explicitly_unavailable(self):
        with self.assertRaisesRegex(guard.GuardRefused, '^native_growth_guard_unavailable$'):
            guard.native_check(evidence(), NOW)


if __name__ == '__main__':
    unittest.main()
