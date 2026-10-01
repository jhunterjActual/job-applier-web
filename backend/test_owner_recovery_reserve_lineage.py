"""Actual successor derivation/packet/eligibility with synthetic SQL and files.

No database, filesystem state, provider or subprocess operations. Only external
reads, candidate decryption, and bridge's separately tested live snapshot are
substituted; Host._successor, replacement, packet validation and reserve policy
remain the production functions.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime,timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import owner_recovery_reserve_host as host
import owner_recovery_reserve as reserve
import owner_recovery as recovery
from test_identity_plan_packet import fixture
from test_owner_recovery_reserve import credential,instant

class Result:
    def __init__(self,value):self.value=value
    def mappings(self):return self
    def scalars(self):return self
    def one(self):return deepcopy(self.value)
    def all(self):return deepcopy(self.value)

class Connection:
    def __init__(self,test):self.test=test;self.queries=[]
    def exec_driver_sql(self,sql):self.queries.append(sql)
    def scalar(self,sql,params=None):
        sql=str(sql);self.queries.append(sql);t=self.test
        if 'version_num' in sql:return host.TARGET_SCHEMA
        if 'SELECT generation' in sql:return 4
        if 'SELECT oid' in sql:return 42
        if 'identity_configuration_receipts' in sql:
            return not t.configuration_committed if 'NOT EXISTS' in sql else True
        if 'deadline<=clock_timestamp' in sql:return t.old_expired
        if 'SELECT NOT EXISTS' in sql and 'identity_transitions' in sql:return not t.further_successor
        if 'CAST(:expiry' in sql:return t.primary_expired
        if any(part in sql for part in ('maintenance AND NOT signup_open','count(*)=4','SELECT binding=:b',
            'JOIN careertrellis_admission.identity_accounts')):return True
        raise AssertionError('Unexpected synthetic scalar query')
    def execute(self,sql,params=None):
        sql=str(sql);self.queries.append(sql);t=self.test
        if 'SELECT evidence_digest' in sql:return Result(t.ack)
        if 'SELECT id FROM' in sql:return Result([] if 'WHERE disabled OR' in sql else [1])
        if 'SELECT * FROM' in sql:
            if 'WHERE predecessor=' in sql:return Result(t.new_row)
            if params['op']==t.old['operation']:return Result(t.old_row)
        raise AssertionError('Unexpected synthetic row query')

class SuccessorLineageTests(unittest.TestCase):
    def setUp(self):
        self.original=fixture()
        for pair in (self.original['bind'],self.original['switch']):
            for side in ('provider','activation'):pair[side]['operations']=host.OPS
            pair['activation']['schema']=host.TARGET_SCHEMA
        self.old=dict(operation=self.original['switch']['provider']['transitions'][0],account_id=1,
            incarnation='44444444-4444-4444-8444-444444444444',epoch=1,old_binding='d'*64,
            old_subject='user_source1',target_binding='e'*64,target_subject='user_synthetic_owner',
            expected_role='owner',owner_authorized=True,started=instant(100),deadline=instant(500),predecessor=None)
        self.new={**self.old,'operation':'55555555-5555-4555-8555-555555555555','epoch':2,
            'predecessor':self.old['operation'],'started':instant(501),'deadline':instant(900)}
        def row(context,state):
            value={**context,'state':state}
            for key in ('started','deadline'):value[key]=datetime.fromisoformat(value[key])
            return value
        self.old_row=row(self.old,'superseded');self.new_row=row(self.new,'held')
        self.original_raw=host.packet.canonical(host.ops.encoded(self.original));self.original_digest=reserve.sha(self.original_raw)
        operation=self.original['switch']['provider']['operation']
        self.enrolled=host.ops.encoded(dict(version=1,digest=self.original_digest,operation=operation,sequence=1))
        self.primary_selected=host.ops.encoded(dict(version=1,digest=self.original_digest,operation=operation,sequence=3))
        self.successor=deepcopy(self.original);self.successor.update(version=2,predecessor=self.original_digest)
        self.successor['switch']['provider'].update(source_application=self.original['switch']['provider']['application'],
            expected_generation=4,transitions=[self.new['operation']])
        self.refresh_successor()
        self.primary=credential();self.child=credential('22222222-2222-4222-8222-222222222222')
        self.meta=reserve.enrollment(self.primary,dict(binding='a'*64,original_operation=self.old['operation'],
            original_selection=self.enrolled,original_plan=self.original_raw,prehold_verified=True))
        self.issued=dict(reserve_id=self.child['id'],enrollment_sha256=reserve.sha(recovery.encoded(self.meta)))
        self.primary.update(version=2,state='consumed',receipt=dict(operation=self.old['operation'],scope=recovery.TRANSITION_SCOPE,
            binding='a'*64,issued=110,expires=410,context=self.old,selection=reserve.sha(self.primary_selected),
            schema=host.TARGET_SCHEMA,credential=self.primary['id']))
        self.configuration_committed=False;self.old_expired=True;self.primary_expired=True;self.further_successor=False
        self.ack=dict(evidence_digest=None,verified_at=None,evidence_expires=None)
        self.intake_present=False;self.journal_present=False;self.journal={}
        self.db_selected=None;self.approved=None
        self.connection=Connection(self)
        self.adapter=host.Host(SimpleNamespace(url=SimpleNamespace(host='synthetic',port=5432,database='synthetic')),
            verify_sources=lambda:True,metadata_reader=lambda:self.meta,
            approval_reader=lambda:self.selected if self.approved is None else self.approved,
            primary_plan_reader=lambda:self.primary_selected,library_pins={},action='consume',closure_sha256='f'*64,
            convergence=lambda context:self.selected if self.db_selected is None else self.db_selected)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        def patched(obj,name,**kwargs):return self.stack.enter_context(patch.object(obj,name,**kwargs))
        patched(host.plans,'load',side_effect=lambda **kwargs:(self.selected,self.successor['switch']))
        def protected(path,**kwargs):
            if isinstance(path,SimpleNamespace):return b'{}'
            if path==host.plans.archive(self.original_digest):return self.original_raw
            if path==host.plans.archive(reserve.sha(self.successor_raw)):return self.successor_raw
            raise AssertionError('Unexpected synthetic protected-file read')
        patched(host.ops,'read_protected',side_effect=protected)
        patched(host.operator,'setup')
        patched(host.recovery,'Store',return_value=SimpleNamespace(read=lambda:self.primary))
        self.candidate=patched(self.adapter,'_candidate',return_value={'staging.env':'','auth-v2.env':''})
        patched(host.bridge,'snapshot',side_effect=lambda *args,**kwargs:dict(binding='a'*64,context=deepcopy(self.new),
            schema=host.TARGET_SCHEMA,selection=reserve.sha(self.selected)))
        intake=SimpleNamespace(exists=lambda:self.intake_present,is_symlink=lambda:False)
        journal=SimpleNamespace(exists=lambda:self.journal_present,is_symlink=lambda:False)
        patched(host.operator,'locations',return_value=(intake,journal))
        patched(host.operator,'fingerprint',return_value=('synthetic-stable-input',))
        patched(host.operator,'journal',side_effect=lambda *args:deepcopy(self.journal))
    def refresh_successor(self):
        self.successor_raw=host.packet.canonical(host.ops.encoded(self.successor))
        self.selected=host.ops.encoded(dict(version=1,digest=reserve.sha(self.successor_raw),
            operation=self.original['switch']['provider']['operation'],sequence=4))
    def run_view(self):
        view,selected,files=self.adapter._successor(self.connection,self.new['operation'])
        reserve.eligible(self.meta,self.issued,self.primary,self.child,view,510)
        return view
    def test_actual_valid_lineage_and_core_eligibility(self):
        view=self.run_view()
        self.assertEqual(self.primary_selected,view['lineage']['primary_selection'])
        self.assertNotEqual(self.enrolled,self.primary_selected)
        self.assertTrue(any('identity_configuration_receipts' in sql for sql in self.connection.queries))
        self.candidate.assert_called_once()
    def test_primary_receipt_selection_mismatch(self):
        self.primary['receipt']['selection']=reserve.sha(self.enrolled)
        with self.assertRaises(ValueError):self.run_view()
        self.candidate.assert_not_called()
    def test_committed_original_refused_by_real_replacement(self):
        self.old_row['state']='committed'
        with self.assertRaises(Exception):self.run_view()
    def test_configuration_commit_refused_by_real_replacement(self):
        self.configuration_committed=True
        with self.assertRaises(Exception):self.run_view()
        self.candidate.assert_not_called()
    def test_multihop_or_changed_epoch_refused(self):
        self.further_successor=True
        with self.assertRaises(ValueError):self.run_view()
        self.further_successor=False;self.new_row['epoch']=3
        with self.assertRaises(ValueError):self.run_view()
    def test_database_selection_or_independent_approval_mismatch(self):
        self.db_selected=self.enrolled
        with self.assertRaises(ValueError):self.run_view()
        self.db_selected=None;self.approved=self.enrolled
        with self.assertRaises(ValueError):self.run_view()
    def test_plan_ancestry_drift(self):
        self.successor['predecessor']='f'*64;self.refresh_successor()
        with self.assertRaises(ValueError):self.run_view()
    def test_unexpired_primary_or_original_transition(self):
        self.primary_expired=False
        with self.assertRaises(ValueError):self.run_view()
        self.primary_expired=True;self.old_expired=False
        with self.assertRaises(ValueError):self.run_view()
    def test_malformed_unsubmitted_intake_refused(self):
        self.intake_present=True
        with self.assertRaises(host.operator.evidence.EvidenceRejected):self.run_view()
        self.candidate.assert_not_called()
    def test_exact_recorded_ack_accepted_and_mismatch_refused(self):
        verified=datetime.fromtimestamp(120,timezone.utc);expires=datetime.fromtimestamp(400,timezone.utc)
        self.ack=dict(evidence_digest='a'*64,verified_at=verified,evidence_expires=expires)
        self.journal_present=True;self.journal=dict(phase='recorded',digest='a'*64,verified=verified.isoformat(),expires=expires.isoformat())
        self.assertTrue(self.run_view()['lineage']['acknowledgement_resolved'])
        self.journal['digest']='b'*64
        with self.assertRaises(ValueError):self.run_view()
    def test_derived_database_successor_must_match_exact_packet(self):
        self.new_row['operation']='66666666-6666-4666-8666-666666666666'
        with self.assertRaises(ValueError):self.run_view()

if __name__=='__main__':unittest.main()
