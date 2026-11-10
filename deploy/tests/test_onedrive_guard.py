"""Pure guard fixtures; no root, provider, subprocess or file operations."""
import copy
import datetime as dt
import hashlib
import json
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch,Mock
from pathlib import Path

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
    def test_v2_health_cache_history_normalizer_refuses_inconsistent_recovery_age(self):
        from deploy.onedrive_retention import health,native_restore
        from deploy.tests.test_onedrive_health import evidence,NOW as observed,BINDINGS,REV,RELEASE
        cached=dict(health.evaluate(evidence(),observed),authenticated=True,baseline_bindings=BINDINGS,
            artifact_revision=REV,application_release=RELEASE,activation_sha256='d'*64)
        def normalize():
            with patch.object(guard,'_history_names',return_value=[{'name':'current.json'}]),\
                 patch.object(native_restore,'protected',return_value=bundle.canonical(cached)):
                guard._endpoint_history([{'name':'health','directory':True}],'a'*32,'prepare',SimpleNamespace())
        normalize()
        cached['recovery_point_age_seconds']=True
        with self.assertRaises(ValueError):normalize()
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

    def test_native_entry_never_accepts_caller_evidence_dictionary(self):
        with self.assertRaises(guard.GuardRefused):
            guard.native_check(evidence(), NOW)


class ProviderCollectionTests(unittest.TestCase):
    def row(self):
        return {'Path': key(1) + '/repository.tar', 'Name': 'repository.tar',
                'ID': 'object-one', 'Size': 100, 'IsDir': False}

    def test_inventory_requires_actual_unique_file_identifiers(self):
        row = self.row()
        self.assertEqual(guard.provider_rows(json.dumps([row]).encode())[row['Path']]['object_id'], 'object-one')
        for field, invalid in [('ID', ''), ('Size', True), ('IsDir', True), ('Path', '../repository.tar')]:
            broken = dict(row); broken[field] = invalid
            with self.assertRaises(guard.GuardRefused): guard.provider_rows(json.dumps([broken]).encode())
        with self.assertRaises(guard.GuardRefused): guard.provider_rows(json.dumps([row, row]).encode())
        with self.assertRaises(guard.GuardRefused): guard.provider_rows(b'[{"ID":"a","ID":"b"}]')

    def test_named_digest_without_separate_download_witness_never_authenticates_bytes(self):
        row = self.row(); rows = guard.provider_rows(json.dumps([row]).encode())
        with self.assertRaises(guard.GuardRefused): guard.witness_inventory(rows, [], NOW)
        witness = {'path': row['Path'], 'object_id': 'object-one', 'size': 100, 'sha256': key(1),
                   'identity_verified_before_after_download': True}
        result = guard.witness_inventory(rows, [witness], NOW)
        self.assertEqual(result['objects'][0]['sha256'], key(1))
        for field, invalid in [('object_id', 'replacement'), ('size', 101), ('sha256', key(2)),
                               ('identity_verified_before_after_download', False)]:
            broken = dict(witness); broken[field] = invalid
            with self.assertRaises(guard.GuardRefused): guard.witness_inventory(rows, [broken], NOW)

    def test_untrusted_fixture_cannot_issue_authenticated_provider_facts(self):
        with self.assertRaises(guard.GuardRefused): guard.AuthenticatedProviderFacts({})

    def test_default_native_collector_refuses_source_checkout_before_credentials(self):
        with self.assertRaises(guard.GuardRefused): guard.collect_provider_facts(1)


class PeerHistoryTests(unittest.TestCase):
    def snapshot(self):
        return {'schema':'careertrellis.onedrive-host-history/v1','role':'web','transaction':TX,
            'stage':'publish','observed_at':NOW,
            'baseline_bindings':dict(zip(('standalone_manifest_sha256','paired_manifest_sha256',
                'paired_policy_sha256','standalone_policy_sha256'),(key(1),key(2),key(3),key(4)))),
            'roots':[{'category':category,'root':root,'entry_count':0,'inventory_sha256':key(5)}
                     for category,root in guard.HISTORY_ROOTS['web']],
            'unresolved':[],'complete':True,'growth_authorized':False,'deletion_authorized':False,
            'authentication':'requires_authenticated_transport','owner':copy.deepcopy(OWNER),
            'reservation':{'path':'/run/lock/careertrellis-maintenance.lock','held':True,
                'identity':{'device':1,'inode':2,'owner_pid':OWNER['pid']}}}

    def check(self,value): return guard.validate_peer_history(value,TX,'publish',key(4),now=NOW)

    def test_validated_protocol_claims_do_not_claim_authentication_or_authorize_growth(self):
        value = self.snapshot(); result = self.check(value)
        self.assertEqual(result,value)
        self.assertFalse(result['growth_authorized'])
        self.assertEqual(result['authentication'],'requires_authenticated_transport')

    def test_incomplete_stale_other_transaction_or_unreserved_peer_refuses(self):
        for key_name,replacement in [('transaction','b'*32),('observed_at','2026-10-26T22:57:59Z'),
                                     ('complete',False),('role','db'),('growth_authorized',True)]:
            value = self.snapshot(); value[key_name] = replacement
            with self.assertRaises(guard.GuardRefused): self.check(value)
        value = self.snapshot(); value['reservation']['held'] = False
        with self.assertRaises(guard.GuardRefused): self.check(value)
        value = self.snapshot(); value['reservation']['identity']['owner_pid'] += 1
        with self.assertRaises(guard.GuardRefused): self.check(value)

    def test_missing_duplicate_or_caller_selected_root_never_covers_peer_history(self):
        for mutate in (lambda value:value['roots'].pop(),
                       lambda value:value['roots'].__setitem__(1,copy.deepcopy(value['roots'][0])),
                       lambda value:value['roots'][0].__setitem__('root','/tmp/other')):
            value = self.snapshot(); mutate(value)
            with self.assertRaises(guard.GuardRefused): self.check(value)

    def test_policy_rebind_and_future_observation_refuse(self):
        value = self.snapshot(); value['baseline_bindings']['standalone_policy_sha256'] = key(8)
        with self.assertRaises(guard.GuardRefused): self.check(value)
        value = self.snapshot(); value['observed_at'] = '2026-10-26T23:00:01Z'
        with self.assertRaises(guard.GuardRefused): self.check(value)


class LocalStageBoundaryTests(unittest.TestCase):
    """Synthetic trust-boundary ordering; no native acceptance or IO is claimed."""
    def context(self):
        class Context:
            authenticated = True
            role = 'db'
            bindings = {'standalone_policy_sha256':key(4)}
        return Context

    def test_nonterminal_collector_fence_blocks_before_any_receipt_or_provider_read(self):
        from deploy.onedrive_retention import baseline,entry
        context = self.context()
        with patch.object(baseline,'AuthenticatedContext',context), \
             patch.object(baseline,'native_context',return_value=context()), \
             patch.object(entry,'ROOT',Path(guard.__file__).absolute().parent), \
             patch.object(guard.catalog,'NativeFiles'), \
             patch.object(entry,'retention_idle',return_value=False) as idle, \
             patch.object(guard,'native_web_witness') as witness, \
             patch.object(guard,'NativeProvider',side_effect=AssertionError('unexpected provider')):
            with self.assertRaises(guard.GuardRefused): guard.check_local_stage(TX,'restore',11)
            idle.assert_called_once_with(TX,'restore'); witness.assert_not_called()

    def test_positive_local_fence_reaches_protected_witness_without_contacting_provider(self):
        from deploy.onedrive_retention import baseline,entry
        context = self.context()
        with patch.object(baseline,'AuthenticatedContext',context), \
             patch.object(baseline,'native_context',return_value=context()), \
             patch.object(entry,'ROOT',Path(guard.__file__).absolute().parent), \
             patch.object(guard.catalog,'NativeFiles'), \
             patch.object(entry,'retention_idle',return_value=True), \
             patch.object(guard,'native_web_witness',side_effect=guard.GuardRefused('fixture stop')) as witness, \
             patch.object(guard,'NativeProvider',side_effect=AssertionError('unexpected provider')):
            with self.assertRaisesRegex(guard.GuardRefused,'fixture stop'):
                guard.native_check('bundle_restore',TX,repository_lock_fd=11)
            witness.assert_called_once()

    def test_stable_scope_and_archive_capabilities_do_not_accept_tokens_or_fixture_authentication(self):
        self.assertEqual(guard.drive_scope('drive-one'),guard.drive_scope('drive-one'))
        self.assertNotEqual(guard.drive_scope('drive-one'),guard.drive_scope('drive-two'))
        with self.assertRaises(guard.GuardRefused): guard.drive_scope('drive\nsecret')
        with self.assertRaises(guard.GuardRefused): guard.AuthenticatedPolicyArchive({}, {}, key(1))


class PairedHistoryContractTests(unittest.TestCase):
    def handoff(self):
        parent = copy.deepcopy(OWNER); child = dict(parent,pid=223,start_ticks=334,process_group=223)
        capture = {'result':'pass','manifest_verified':True,'release':'a'*40,'backup_label':'20261026-155056F',
            'snapshot_id':'a'*40+'-20261026-155056F','authority':'postgres-r2-authoritative',
            'object_count':0,'ciphertext_bytes':0,'manifest_sha256':key(1),
            'capture_started_epoch':1,'captured_epoch':2}
        publication = {key_name:capture[key_name] for key_name in
                       ('release','backup_label','snapshot_id','manifest_sha256','authority')}
        publication['result'] = 'onedrive_snapshot_created'
        handoff = {'schema':'careertrellis.paired-retention-handoff/v1','transaction':TX,
            'paired_policy_sha256':key(4),'retention_policy_sha256':key(5),'release':'a'*40,
            'backup_label':capture['backup_label'],'capture_sha256':sha(capture),'publication_sha256':sha(publication),
            'parent_owner':parent,'parent_unit':'careertrellis-paired-maintenance.service',
            'parent_invocation':'b'*32,'issued_at':NOW,'maintenance_fd':10,
            'maintenance_identity':{'device':1,'inode':2,'owner_pid':parent['pid']}}
        dispatched = {'schema':'careertrellis.paired-retention-child/v1','transaction':TX,
            'handoff_sha256':sha(handoff),'owner':child,'command':['/usr/bin/python3','-I','-B',
                '/usr/local/libexec/careertrellis/onedrive_retention/entry.py','web-run','--transaction',TX,
                '--expected-backup-label',capture['backup_label']]}
        docs = {'bundle-dispatch-intent.json':handoff,'bundle-child-dispatched.json':dispatched,
            'start-intent.json':{'transaction':TX,'policy_sha256':key(4),'identity':{
                'release':'a'*40,'web_boot':parent['boot_id'],'web_invocation':'b'*32},
                'backup_strategy':{'mode':'native-bundle-v2','retention_policy_sha256':key(5),'activation_sha256':key(6)}},
            'capture-result.json':capture,'publication-result.json':publication,
            'writers-restored.json':{'services_restored':True,'capture_sha256':sha(capture)},
            'db-released.json':{'services_restored':True}}
        return docs,parent,child

    def test_handoff_proposal_consistency_never_authorizes_active_unit_or_child(self):
        docs,parent,child = self.handoff()
        result = guard.validate_paired_handoff(docs,TX,key(4),key(5),'a'*40,parent,child,NOW)
        self.assertEqual(result['result'],'consistent_post_restoration_handoff')
        self.assertFalse(result['authenticated']); self.assertFalse(result['continuation_authorized'])
        self.assertFalse(result['dispatch_authorized']); self.assertFalse(result['backup_accepted'])

    def test_stale_handoff_cannot_be_readmitted_and_immutable_binding_is_not_authentication(self):
        docs,parent,child = self.handoff()
        later = '2026-10-27T05:00:00Z'
        with self.assertRaises(guard.GuardRefused):
            guard.validate_paired_handoff(docs,TX,key(4),key(5),'a'*40,parent,child,later)
        structural = guard._paired_handoff_records(docs,TX,key(4),key(5),'a'*40,parent,child)
        self.assertFalse(structural['authenticated']);self.assertFalse(structural['continuation_authorized'])
        with self.assertRaises(guard.GuardRefused):guard.AuthenticatedPairedHandoff({})

    def test_handoff_requires_protected_parent_lock_identity_and_bounded_descriptor(self):
        for field,bad in (('maintenance_fd',True),('maintenance_fd',2),('maintenance_fd',1025),
                          ('maintenance_identity',{'device':1,'inode':2,'owner_pid':999})):
            docs,parent,child = self.handoff();docs['bundle-dispatch-intent.json'][field] = bad
            with self.assertRaises(guard.GuardRefused):
                guard.validate_paired_handoff(docs,TX,key(4),key(5),'a'*40,parent,child,NOW)

    def test_handoff_proposal_refuses_wrong_child_command_source_pair_or_unrestored_writers(self):
        for mutate in (lambda docs:docs['bundle-child-dispatched.json']['command'].__setitem__(-1,'f'*32),
                       lambda docs:docs['writers-restored.json'].update(services_restored=False),
                       lambda docs:docs['bundle-dispatch-intent.json'].update(backup_label='20261025-155056F'),
                       lambda docs:docs['bundle-dispatch-intent.json'].update(parent_invocation='e'*32)):
            docs,parent,child = self.handoff(); mutate(docs)
            with self.assertRaises(guard.GuardRefused):
                guard.validate_paired_handoff(docs,TX,key(4),key(5),'a'*40,parent,child,NOW)

    def web_skipped(self):
        status = {'transaction':TX,'policy_sha256':key(4),'release':'a'*40,
                  'boot_id':OWNER['boot_id'],'worker_invocation':'b'*32,
                  'state':'idle','services_restored':True}
        return {'status.json':status,'complete.json':{'state':'skipped','transaction':TX,
                'policy_sha256':key(4),'services_restored':True}}

    def db_aborted(self):
        identity = {'release':'a'*40,'web_boot':OWNER['boot_id'],'web_invocation':'c'*32}
        def message(operation,payload):
            return {'transaction':TX,'policy_sha256':key(4),'operation':operation,'payload':payload}
        return {'status.json':{'transaction':TX,'policy_sha256':key(4),'release':'a'*40,
                'boot_id':OWNER['boot_id'],'worker_invocation':'b'*32,'state':'aborted'},
            'worker-started.json':{'boot_id':OWNER['boot_id'],'worker_invocation':'b'*32},
            'begin-intent.json':message('begin',identity),
            'abort.json':message('abort',identity),
            'restored.json':message('restored',dict(identity,services_restored=True))}

    def test_recognized_skipped_or_aborted_claim_remains_unauthenticated(self):
        web = guard.paired_run_claim('web',TX,self.web_skipped(),key(4),'a'*40)
        db = guard.paired_run_claim('db',TX,self.db_aborted(),key(4),'a'*40)
        self.assertEqual(web['state'],'skipped'); self.assertEqual(db['state'],'aborted')
        self.assertFalse(web['authenticated']); self.assertFalse(db['authenticated'])

    def test_failed_or_dispatched_skipped_web_attempt_is_not_completed_history(self):
        for mutate in (lambda docs:docs['status.json'].update(state='operator_required'),
                       lambda docs:docs.update({'capture-dispatch-intent.json':{}}),
                       lambda docs:docs['complete.json'].update(services_restored=False)):
            docs = self.web_skipped(); mutate(docs)
            with self.assertRaises(guard.GuardRefused): guard.paired_run_claim('web',TX,docs,key(4),'a'*40)

    def test_aborted_db_with_unresolved_mirror_or_changed_identity_is_blocked(self):
        for mutate in (lambda docs:docs.update({'mirror-intent.json':{}}),
                       lambda docs:docs['restored.json']['payload'].update(web_invocation='d'*32),
                       lambda docs:docs['status.json'].update(state='operator_required'),
                       lambda docs:docs['worker-started.json'].update(worker_invocation='d'*32)):
            docs = self.db_aborted(); mutate(docs)
            with self.assertRaises(guard.GuardRefused): guard.paired_run_claim('db',TX,docs,key(4),'a'*40)

    def test_unknown_historical_paired_policy_requires_separate_provenance(self):
        docs = self.web_skipped(); docs['status.json']['policy_sha256'] = key(9)
        docs['complete.json']['policy_sha256'] = key(9)
        with self.assertRaises(guard.GuardRefused): guard.paired_run_claim('web',TX,docs,key(4),'a'*40)


class EmptyPairedHistoryTests(unittest.TestCase):
    def test_active_weekly_unit_without_matching_history_is_not_admitted(self):
        import sys
        from types import SimpleNamespace
        from deploy import paired_maintenance as package
        from deploy.paired_maintenance import entry as paired_entry,host,web_control
        from deploy.onedrive_retention import entry
        with patch.dict(sys.modules,{'paired_maintenance':package}), \
             patch.object(paired_entry,'verify_install'), \
             patch.object(entry,'PAIRED_ROOT',Path(host.__file__).absolute().parent), \
             patch.object(guard,'native_paired_handoff',return_value=None), \
             patch.object(guard,'_paired_unit_state',return_value={'ActiveState':'active'}), \
             patch.object(guard,'_paired_unit_terminal'),self.assertRaises(guard.GuardRefused):
            guard._paired_history('web',[],SimpleNamespace(),TX)


class RetirementParentForwardingTests(unittest.TestCase):
    def test_missing_rows_require_native_exact_absence_factory_with_same_parent(self):
        from contextlib import nullcontext
        path='b'*64+'/repository.tar';parent=object()
        store=Mock();store.io.locked.side_effect=lambda:nullcontext()
        store.records.return_value={'b'*64:{'entry':{'objects':[{'path':path,'object_id':'synthetic','size':1,'sha256':'b'*64}]}}}
        store.retired.return_value=set()
        with patch.object(guard.catalog,'NativeReadStore',return_value=store), \
                patch.object(retire,'authenticated_retirement_absences',return_value=frozenset({path})) as authenticate:
            accepted,held,candidate=guard._namespace_owners({'rows':{}},9,retirement_transaction=TX,retirement_parent=parent)
        self.assertEqual((accepted,held,candidate),({},{},{}))
        authenticate.assert_called_once_with(9,TX,{},parent=parent)

    def test_parent_alone_does_not_authorize_provider_inventory(self):
        with patch.object(guard,'collect_provider_facts') as provider,self.assertRaises(guard.GuardRefused):
            guard.native_catalog_inventory(9,retirement_parent=object())
        provider.assert_not_called()


class ActivationWebHistoryTests(unittest.TestCase):
    def test_empty_default_root_needs_no_unavailable_native_callback(self):
        from deploy.onedrive_retention import activation_web as web
        with patch.object(web,'history_complete',side_effect=AssertionError('no row exists')):
            self.assertTrue(guard._activation_web_history([],TX,'hydrate',SimpleNamespace(role='web')))
        self.assertIn(('activation_web','/var/lib/careertrellis/onedrive-retention/activation-web'),guard.HISTORY_ROOTS['web'])

    def test_only_prepare_exact_actual_cgroup_selects_current_owner_callback(self):
        from deploy.onedrive_retention import activation_web as web
        rows=[{'name':TX,'directory':True},{'name':'b'*32,'directory':True}]
        group='0::/system.slice/careertrellis-onedrive-activation@'+TX+'.service'
        with patch.object(Path,'read_text',return_value=group),patch.object(web,'history_complete',return_value=True) as complete:
            guard._activation_web_history(rows,TX,'prepare',SimpleNamespace(role='web'))
            self.assertEqual(complete.call_args_list,[unittest.mock.call(TX,current_transaction=TX),
                unittest.mock.call('b'*32,current_transaction=None)])
        for stage,current_group in (('hydrate',group),('prepare','0::/system.slice/other.service')):
            with self.subTest(stage=stage,group=current_group),patch.object(Path,'read_text',return_value=current_group),\
                    patch.object(web,'history_complete',return_value=True) as complete:
                guard._activation_web_history(rows[:1],TX,stage,SimpleNamespace(role='web'))
                complete.assert_called_once_with(TX,current_transaction=None)

    def test_wrong_current_owner_or_unknown_past_never_gets_fallback(self):
        from deploy.onedrive_retention import activation_web as web
        group='0::/system.slice/careertrellis-onedrive-activation@'+TX+'.service'
        for name in (TX,'b'*32):
            with self.subTest(name=name),patch.object(Path,'read_text',return_value=group),\
                    patch.object(web,'history_complete',return_value=False) as complete:
                with self.assertRaises(guard.GuardRefused):
                    guard._activation_web_history([{'name':name,'directory':True}],TX,'prepare',SimpleNamespace(role='web'))
                complete.assert_called_once_with(name,current_transaction=TX if name==TX else None)

    def test_exhaustive_bad_file_orphan_name_duplicate_and_overflow_refuse(self):
        from deploy.onedrive_retention import activation_web as web
        bad_rows=([{'name':TX,'directory':False}],[{'name':'orphan','directory':True}],
            [{'name':'0'*32,'directory':True}],[{'name':TX,'directory':True}]*2,
            [{'name':TX,'directory':True}]*(guard.MAX_RECORDS+1))
        with patch.object(Path,'read_text',return_value='0::/system.slice/other.service'),\
                patch.object(web,'history_complete',return_value=True):
            for rows in bad_rows:
                with self.subTest(length=len(rows)),self.assertRaises(guard.GuardRefused):
                    guard._activation_web_history(rows,TX,'prepare',SimpleNamespace(role='web'))


class SharedProviderDeadlineTests(unittest.TestCase):
    def provider(self,deadline=280):
        # Structural unit fixture only: the native constructor is not admitted.
        provider=object.__new__(guard.NativeProvider);provider.operation_deadline=deadline
        provider.unchanged=Mock();provider.binary='fixture-rclone';provider.fd=42
        provider.files=SimpleNamespace(repository_lock_fd=9);provider.context=SimpleNamespace(bindings={})
        provider.account_scope='a'*64
        provider.config=(('[r2]\ntype=s3\nprovider=Cloudflare\nendpoint=https://'+'f'*32+
            '.r2.cloudflarestorage.com\n').encode(),None)
        return provider

    def test_source_count_quota_parent_inventory_share_one_deadline(self):
        from deploy.onedrive_retention import hydrate
        provider=self.provider();clock=[100.0];calls=[]
        def capture(args,**kwargs):
            calls.append((args[1],kwargs['timeout']));clock[0]+=30
            if args[1]=='size':return b'{"bytes":100,"count":2}'
            if args[1]=='about':return b'{"total":1000,"free":900}'
            if '--dirs-only' in args:return b'[{"Path":"postgresql-bundles","IsDir":true}]'
            return b'[]'
        fcntl=SimpleNamespace(F_ADD_SEALS=1,F_SEAL_SEAL=2,F_SEAL_SHRINK=4,F_SEAL_GROW=8,F_SEAL_WRITE=16,fcntl=Mock())
        with patch.object(guard.time,'monotonic',side_effect=lambda:clock[0]),patch.object(hydrate,'command',side_effect=capture),\
                patch.dict(sys.modules,{'fcntl':fcntl}),patch.object(guard.os,'memfd_create',return_value=99,create=True),\
                patch.object(guard.os,'MFD_ALLOW_SEALING',1,create=True),\
                patch.object(guard.os,'write',side_effect=lambda fd,raw:len(raw)),patch.object(guard.os,'fchmod',create=True),\
                patch.object(guard.os,'lseek'),patch.object(guard.os,'close'):
            source,candidate=provider.source_allocation();provider.collect()
        self.assertEqual(source,100);self.assertGreater(candidate,source)
        self.assertEqual(calls,[('size',180),('about',150),('lsjson',120),('lsjson',90)])

    def test_expired_budget_starts_no_child_and_default_remains_600(self):
        from deploy.onedrive_retention import hydrate
        provider=self.provider()
        with patch.object(guard.time,'monotonic',return_value=280),patch.object(hydrate,'command') as child:
            with self.assertRaises(guard.GuardRefused):provider.read('quota')
            child.assert_not_called()
        provider.operation_deadline=None
        with patch.object(hydrate,'command',return_value=b'{}') as child:provider.read('quota')
        self.assertEqual(child.call_args.kwargs['timeout'],600)

    def test_command_timeout_or_late_return_never_returns_capacity_or_starts_next_read(self):
        from deploy.onedrive_retention import hydrate
        provider=self.provider()
        with patch.object(guard.time,'monotonic',return_value=100),\
                patch.object(hydrate,'command',side_effect=TimeoutError('synthetic known wait failure')) as child:
            with self.assertRaises(TimeoutError):provider.collect()
            child.assert_called_once()
        clock=[100]
        def late(*args,**kwargs):clock[0]=281;return b'{"total":1000,"free":900}'
        with patch.object(guard.time,'monotonic',side_effect=lambda:clock[0]),patch.object(hydrate,'command',side_effect=late) as child:
            with self.assertRaises(guard.GuardRefused):provider.collect()
            child.assert_called_once()


class DedicatedIdentityHistoryTests(unittest.TestCase):
    def test_past_child_history_receives_exact_live_parent_context_for_owner_or_child2(self):
        from deploy.onedrive_retention import pre_growth_native as p,preservation,publish
        parent='b'*32;source='c'*32;root=Path('/var/lib/careertrellis/onedrive-bundles')/source
        unit=p.identity_unit(parent,source,1)
        owner={'pid':2,'start_ticks':3,'boot_id':'fixture','unit':unit,'invocation_id':'a'*32}
        docs={'intent.json':{'schema':'careertrellis.onedrive-identity-intent/v1','transaction':source,'verification':1,'owner':owner},
            'receipt.json':{'sha256':'e'*64,'size':100}}
        names={'intent.json','receipt.json','archive.tar'}
        for phase in ('stat-before','download','stat-after'):
            names|={phase+'-'+suffix+'.json' for suffix in ('intent','child','terminal')}
            docs[phase+'-child.json']={'pid':10,'start_ticks':11,'boot_id':'fixture'}
            docs[phase+'-terminal.json']={'pid':10,'returncode':0}
        def rows(path):
            return [{'name':name} for name in (['identity-verification-01','bundle-object-identity-01.json'] if path==root else names)]
        def protected(path,**kwargs):
            return bundle.canonical(docs['receipt.json'] if path.name=='bundle-object-identity-01.json' else docs[path.name])
        def exact_parent(tx,src,number,current,fd,**kwargs):
            if (tx,src,number,current,fd)!=(parent,source,1,parent,9):raise p.PreGrowthNativeRefused('parent_not_terminal')
            return True
        for caller in ('actual_owner','actual_identity_child2'):
            with self.subTest(caller=caller),patch.object(guard,'_pre_growth_identity_scope',return_value=None),\
                    patch.object(guard,'_history_names',side_effect=rows),patch.object(guard.native_restore,'protected',side_effect=protected),\
                    patch.object(preservation,'_terminal_birth'),patch.object(publish,'digest_file',return_value=('e'*64,100)),\
                    patch.object(p,'identity_history_complete',side_effect=exact_parent) as completed:
                guard._source_auxiliary_history(source,parent,9)
                completed.assert_called_once_with(parent,source,1,parent,9,identity_owner=owner)
                with self.assertRaises(p.PreGrowthNativeRefused):guard._source_auxiliary_history(source,None,9)

    def test_wrong_current_kernel_owner_cannot_issue_partial_exception(self):
        from deploy.onedrive_retention import pre_growth_native as p
        group='0::/system.slice/'+p.identity_unit('b'*32,TX,1)
        with patch.object(Path,'read_text',return_value=group),\
                patch.object(p,'native_identity',side_effect=p.PreGrowthNativeRefused('wrong_owner')):
            with self.assertRaises(p.PreGrowthNativeRefused):guard._pre_growth_identity_scope(TX)

    def test_partial_attempt_requires_exact_owner_and_ordered_zero_exit_records(self):
        from deploy.onedrive_retention import pre_growth_native as p
        owner={'pid':2,'start_ticks':3,'boot_id':'fixture','unit':'fixture','invocation_id':'a'*32}
        intent={'schema':'careertrellis.onedrive-identity-intent/v1','transaction':TX,'verification':1,
            'archive_sha256':'a'*64,'archive_bytes':100,'download_receipt_sha256':'b'*64,'owner':owner,'started_at':NOW}
        documents={'intent.json':intent};scope={'verification':1,'owner':owner,'cap':SimpleNamespace(require_identity=Mock())}
        with patch.object(p,'require_parent'):
            self.assertTrue(guard._current_identity_partial(Path('/fixture'),TX,1,{'intent.json'},scope,
                lambda path:documents[path.name]))
            for names in ({'intent.json','failed.json'},{'intent.json','download-terminal.json'}):
                with self.assertRaises(guard.GuardRefused):guard._current_identity_partial(Path('/fixture'),TX,1,names,scope,
                    lambda path:documents[path.name])
            wrong=dict(scope,owner=dict(owner,pid=99))
            with self.assertRaises(guard.GuardRefused):guard._current_identity_partial(Path('/fixture'),TX,1,{'intent.json'},wrong,
                lambda path:documents[path.name])

    def test_expiry_history_uses_closed_prior_proof_not_fresh_capability(self):
        from deploy.onedrive_retention import expiry_capability as expiry
        with patch.object(expiry,'_prior_history',return_value=(b'{}',{})) as prior,\
                patch.object(expiry,'native_current',side_effect=AssertionError('must not issue a witness')):
            context=object();self.assertTrue(guard._expiry_capability_history(context,[{'name':'latest.json'}]))
            prior.assert_called_once_with(context,False)
        with patch.object(expiry,'_prior_history',side_effect=expiry.CapabilityRefused('orphan')):
            with self.assertRaises(expiry.CapabilityRefused):guard._expiry_capability_history(object(),[{'name':'checking'}])


if __name__ == '__main__':
    unittest.main()
