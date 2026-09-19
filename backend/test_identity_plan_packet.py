"""Synthetic packet rehearsal; no host files, provider or database access."""
import unittest
from unittest.mock import patch
from uuid import uuid4

import admission_operations as ops
import identity_plan_packet as packet
import session_v2_transition as transition


def fixture():
    def pair(operation,mode,target,generation,transitions):
        proof=transition.owner_proof(operation,target,'user_synthetic_owner')
        return {'provider':{'version':1,'operation':operation,'mode':mode,
            'source_application':'a'*40,'application':'b'*40,'operations':'c'*40,
            'baseline_binding':'d'*64,'target_binding':target,
            'expected_generation':generation,'owner_proof':proof,
            'transitions':transitions,'quarantined_accounts':[],'authorized':True},
            'activation':{'operation':operation,'application':'b'*40,'operations':'c'*40,
            'realm':'synthetic','cap':25,'ttl':600,'session_seconds':3600,
            'schema':transition.HEAD,'provider_binding':target,'owner_proof':proof}}
    return {'version':1,'bind':pair(str(uuid4()),'bind_current','d'*64,0,[]),
        'switch':pair(str(uuid4()),'switch_environment','e'*64,2,[str(uuid4())])}


class PacketTests(unittest.TestCase):
    def test_two_phase_snapshot_and_canonical_roundtrip(self):
        value=fixture(); raw=ops.encoded(value)
        self.assertEqual(value,packet.validate(packet.canonical(raw)))
        for name in ('bind','switch'):
            selected=packet.selected(raw,value[name]['provider']['operation'])
            self.assertEqual(value[name],selected)
            selected['provider']['application']='f'*40
            self.assertEqual(value,packet.validate(raw))
        packet.selected(raw,value['switch']['provider']['operation'],activation=True)
        with self.assertRaises(ops.OperationError):
            packet.selected(raw,value['bind']['provider']['operation'],activation=True)

    def test_mixed_pair_and_cross_pair_drift_rejected(self):
        for field in ('operation','application','operations','owner_proof','provider_binding'):
            value=fixture(); value['switch']['activation'][field]=value['bind']['activation'][field]
            # Shared app/ops are equal normally; inject a valid but different commit.
            if field in ('application','operations'): value['switch']['activation'][field]='f'*40
            with self.subTest(field=field),self.assertRaises(ops.OperationError):
                packet.validate(ops.encoded(value))
        value=fixture(); value['switch']['provider']['source_application']='f'*40
        with self.assertRaises(ops.OperationError): packet.validate(ops.encoded(value))

    def test_generation_projection_and_uuid_reuse_rejected(self):
        for mutate in (
            lambda v:v['switch']['provider'].update(expected_generation=3),
            lambda v:v['switch']['provider'].update(transitions=[v['bind']['provider']['operation']]),
            lambda v:v['switch']['provider'].update(transitions=[]),
        ):
            value=fixture(); mutate(value)
            with self.assertRaises(ops.OperationError): packet.validate(ops.encoded(value))
        value=fixture(); value['switch']['provider']['transitions'].append(str(uuid4()))
        value['switch']['provider']['transitions'].sort()
        with self.assertRaises(ops.OperationError): packet.validate(ops.encoded(value))

    def test_quarantine_counts_against_cap(self):
        value=fixture(); value['switch']['provider']['quarantined_accounts']=list(range(1,25))
        packet.validate(ops.encoded(value))
        value['switch']['provider']['quarantined_accounts'].append(25)
        with self.assertRaises(ops.OperationError): packet.validate(ops.encoded(value))

    def test_malformed_or_unauthorized_input_has_fixed_error(self):
        value=fixture(); value['switch']['provider']['authorized']=False
        for raw in (ops.encoded(value),b'{"version":1,"version":1}',b'user_private_synthetic',
                b' '* (packet.LIMIT+1),b'[]'):
            with self.assertRaises(ops.OperationError) as caught: packet.validate(raw)
            self.assertEqual(ops.ERROR,str(caught.exception))
        with self.assertRaises(ops.OperationError): packet.selected(ops.encoded(fixture()),str(uuid4()))

    def test_policy_cannot_be_reopened_by_packet(self):
        for name in ('bind','switch'):
            for changes in ({'cap':1},{'ttl':900},{'session_seconds':7200},{'schema':'20260907_0014'}):
                value=fixture(); value[name]['activation'].update(changes)
                with self.assertRaises(ops.OperationError): packet.validate(ops.encoded(value))

    def test_consistent_pair_edits_cannot_change_shared_contract(self):
        for key,new in (('operation',None),('application','f'*40),('operations','f'*40)):
            value=fixture(); new=new or value['bind']['provider']['operation']
            for side in ('provider','activation'): value['switch'][side][key]=new
            with self.assertRaises(ops.OperationError): packet.validate(ops.encoded(value))

    def test_generation_headroom_for_all_changed_subjects_and_arm(self):
        value=fixture(); p=value['switch']['provider']
        p['transitions']=sorted(str(uuid4()) for _ in range(25))
        p['expected_generation']=9223372036854775807-52
        value['bind']['provider']['expected_generation']=p['expected_generation']-26
        packet.validate(ops.encoded(value))
        for name in ('bind','switch'): value[name]['provider']['expected_generation']+=1
        with self.assertRaises(ops.OperationError): packet.validate(ops.encoded(value))

    def test_preparation_never_reads_or_applies_host_state(self):
        value=fixture(); raw=ops.encoded(value)
        with patch.object(ops,'read_protected',side_effect=AssertionError('unexpected read')), \
                patch.object(packet.configuration,'apply',side_effect=AssertionError('unexpected mutation')), \
                patch.object(ops,'command',side_effect=AssertionError('unexpected command')):
            packet.canonical(raw)
            packet.selected(raw,value['switch']['provider']['operation'],activation=True)
        with self.assertRaises(ops.OperationError):
            packet.selected(raw,value['switch']['provider']['operation'],activation=1)
