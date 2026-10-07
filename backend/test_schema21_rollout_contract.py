"""Synthetic local bindings; no host, provider, database or credential access."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import admission_operations as ops
import factor_release_packet as source
import identity_plan_packet as packet
import schema21_rollout_contract as contract
from test_factor_release_packet import fixture as source_fixture
from test_identity_plan_packet import fixture as plan_fixture


class Schema21RolloutContractTests(unittest.TestCase):
    def setUp(self):
        self.old = plan_fixture()
        self.archive = packet.canonical(ops.encoded(self.old))
        self.selection = ops.encoded(dict(version=1,digest=contract.sha(self.archive),
            operation=self.old['bind']['provider']['operation'],sequence=4))
        self.files = source_fixture()
        self.files['backend/version.py'] = b'APP_BUILD = "20260930.79"\n'
        self.release = b'synthetic archive bytes; contents not attested'
        plan = plan_fixture()
        for name in ('bind','switch'):
            plan[name]['activation']['schema'] = contract.SCHEMA
        evidence = source.source_packet(self.files,base='b'*40)
        self.value = dict(version=1,kind='schema21_rollout_contract_v1',
            predecessor_schema=contract.PREDECESSOR,target_schema=contract.SCHEMA,
            application='b'*40,operations='c'*40,build='20260930.79',
            archive_sha256=contract.sha(self.release),source_manifest_sha256=evidence['source_manifest_sha256'],
            predecessor_selection_sha256=contract.sha(self.selection),
            predecessor_archive_sha256=contract.sha(self.archive),plan=plan,execution_authorized=False)

    def validate(self, value=None, **changes):
        return contract.validate(ops.encoded(self.value if value is None else value),
            predecessor_selection=changes.get('selection',self.selection),
            predecessor_archive=changes.get('archive',self.archive),
            source_files=changes.get('files',self.files),release_archive=changes.get('release',self.release))

    def test_exact_bindings_and_no_executable_output(self):
        receipt = self.validate()
        self.assertEqual(contract.sha(ops.encoded(self.value)),receipt['contract_sha256'])
        self.assertEqual(contract.sha(ops.encoded(self.value['plan'])),receipt['target_plan_sha256'])
        for name in ('execution_authorized','host_state_verified','consumers_enabled'):
            self.assertIs(receipt[name],False)
        self.assertNotIn('plan',receipt)
        self.assertEqual(list(contract.PENDING),receipt['pending'])

    def test_receipt_cannot_be_used_as_operational_packet(self):
        self.assertEqual(self.value['plan'],packet.validate(ops.encoded(self.value['plan'])))
        receipt=self.validate()
        with self.assertRaises(ops.OperationError):
            packet.validate(ops.encoded(receipt))

    def test_top_level_types_unknown_fields_and_authority_flags_rejected(self):
        for field,bad in (('version',True),('version',2),('execution_authorized',True),
                ('execution_authorized',0),('application','B'*40),('operations',None),
                ('predecessor_schema','20260915_0016'),('target_schema','20260929_0020'),
                ('build',contract.DEPLOYED_BUILD),('build',True),('host_state_verified',True)):
            value=deepcopy(self.value); value[field]=bad
            with self.subTest(field=field,bad=bad),self.assertRaisesRegex(ValueError,contract.ERROR):
                self.validate(value)

    def test_all_evidence_hashes_and_supplied_bytes_are_bound(self):
        for field in ('archive_sha256','source_manifest_sha256',
                      'predecessor_selection_sha256','predecessor_archive_sha256'):
            value=deepcopy(self.value); value[field]='0'*64
            with self.subTest(field=field),self.assertRaises(ValueError): self.validate(value)
        for changes in ({'release':self.release+b'x'},{'archive':self.archive+b' '},
                        {'selection':self.selection+b' '},{'release':b''}):
            with self.assertRaises(ValueError): self.validate(**changes)
        files=dict(self.files); files['backend/app.py']=b'changed'
        with self.assertRaises(ValueError): self.validate(files=files)

    def test_source_build_and_application_bindings_rejected_on_drift(self):
        for field,bad in (('application','f'*40),('operations','f'*40),('build','20261001.80')):
            value=deepcopy(self.value); value[field]=bad
            with self.assertRaises(ValueError): self.validate(value)

    def test_initial_plan_only_and_exact_target_schema(self):
        for mutate in (lambda p:p.update(version=True),lambda p:p.update(version=2),
                lambda p:p.update(predecessor='a'*64),
                lambda p:p['bind']['activation'].update(schema='20260909_0015')):
            value=deepcopy(self.value); mutate(value['plan'])
            with self.assertRaises(ValueError): self.validate(value)

    def test_fresh_pair_and_transition_ids_against_predecessor(self):
        for name in ('bind','switch'):
            value=deepcopy(self.value)
            reused=self.old[name]['provider']['operation']
            for side in ('provider','activation'): value['plan'][name][side]['operation']=reused
            with self.assertRaises(ValueError): self.validate(value)
        value=deepcopy(self.value)
        value['plan']['switch']['provider']['transitions']=self.old['switch']['provider']['transitions']
        with self.assertRaises(ValueError): self.validate(value)

    def test_policy_proofs_pair_generation_and_baseline_remain_exact(self):
        for name,side,field,bad in (
                ('switch','activation','owner_proof','f'*64),
                ('switch','activation','provider_binding','f'*64),
                ('bind','activation','cap',24),('switch','activation','ttl',900),
                ('switch','activation','session_seconds',7200),
                ('switch','provider','source_application','f'*40),
                ('bind','provider','expected_generation',1),
                ('bind','provider','authorized',False)):
            value=deepcopy(self.value); value['plan'][name][side][field]=bad
            with self.subTest(field=field),self.assertRaises(ValueError): self.validate(value)

    def test_predecessor_pointer_must_identify_exact_archive_member(self):
        pointer=ops.strict_json(self.selection); pointer['operation']='00000000-0000-4000-8000-000000000001'
        raw=ops.encoded(pointer); value=deepcopy(self.value)
        value['predecessor_selection_sha256']=contract.sha(raw)
        with self.assertRaises(ValueError): self.validate(value,selection=raw)

    def test_replacement_cannot_expand_or_change_predecessor_identity_scope(self):
        from uuid import uuid4
        for change in ('provider','realm','quarantine','transition_count'):
            value=deepcopy(self.value)
            provider=value['plan']['switch']['provider']
            activation=value['plan']['switch']['activation']
            if change=='provider':
                provider['target_binding']='f'*64
                activation['provider_binding']='f'*64
            elif change=='realm':
                activation['realm']='another_synthetic_realm'
            elif change=='quarantine':
                provider['quarantined_accounts']=[7]
            else:
                provider['transitions']=sorted(provider['transitions']+[str(uuid4())])
                provider['expected_generation']+=1
            # Each edited projected packet still satisfies the historical
            # internal pair rules; rejection must bind predecessor scope.
            projection=deepcopy(value['plan'])
            for name in ('bind','switch'):
                projection[name]['activation']['schema']=packet.transition.HEAD
            packet.validate(ops.encoded(projection))
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.validate(value)

    def test_duplicate_json_private_errors_and_no_host_actions(self):
        for raw in (b'{"version":1,"version":1}',b'user_private_synthetic',b'[]',b' '*65537):
            with self.assertRaisesRegex(ValueError,contract.ERROR):
                contract.validate(raw,predecessor_selection=self.selection,
                    predecessor_archive=self.archive,source_files=self.files,release_archive=self.release)
        with patch.object(ops,'read_protected',side_effect=AssertionError('host read')), \
                patch.object(ops,'command',side_effect=AssertionError('command')):
            self.validate()
