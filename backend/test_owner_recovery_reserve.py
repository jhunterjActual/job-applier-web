"""Synthetic policy and native protected-file checks; never enrolls live state."""
from copy import deepcopy
import ast
from contextlib import contextmanager,nullcontext
from datetime import datetime,timezone
import os
import hmac
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import owner_recovery as recovery
import owner_recovery_reserve as reserve

def instant(value):return datetime.fromtimestamp(value,timezone.utc).isoformat()
def credential(identifier='11111111-1111-4111-8111-111111111111'):
    return dict(version=1,id=identifier,binding='a'*64,salt='b'*64,hash='c'*64,state='active',
        created=10,confirmed=20,attempts=0,last_attempt=0,receipt=None)

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.primary=credential();self.child=credential('22222222-2222-4222-8222-222222222222')
        self.old=dict(operation='33333333-3333-4333-8333-333333333333',account_id=1,
            incarnation='44444444-4444-4444-8444-444444444444',epoch=1,
            old_binding='b'*64,old_subject='user_source1',target_binding='c'*64,target_subject='user_target1',
            expected_role='owner',owner_authorized=True,started=instant(100),deadline=instant(500),predecessor=None)
        self.new={**self.old,'operation':'55555555-5555-4555-8555-555555555555','epoch':2,
            'predecessor':self.old['operation'],'started':instant(501),'deadline':instant(900)}
        view=dict(binding='a'*64,original_operation=self.old['operation'],original_selection=b'original-selection',
            original_plan=b'original-plan',prehold_verified=True)
        self.meta=reserve.enrollment(self.primary,view)
        self.issued=dict(reserve_id=self.child['id'],enrollment_sha256=reserve.sha(recovery.encoded(self.meta)))
        self.primary.update(version=2,state='consumed',receipt=dict(operation=self.old['operation'],
            scope=recovery.TRANSITION_SCOPE,binding='a'*64,issued=110,expires=410,context=self.old,
            selection=reserve.sha(b'original-selection'),schema='20260929_0021',credential=self.primary['id']))
        self.view={'bridge':dict(binding='a'*64,context=self.new,selection=reserve.sha(b'new-selection'),schema='20260929_0021'),
            'lineage':dict(predecessor_context=self.old,predecessor_state='superseded',original_selection=b'original-selection',
                original_plan=b'original-plan',primary_selection=b'original-selection',successor_context=self.new,successor_selection=b'new-selection',
                uncommitted_verified=True,acknowledgement_resolved=True,successor_ready_verified=True)}
    def check(self,now=510):return reserve.eligible(self.meta,self.issued,self.primary,self.child,self.view,now)
    def test_exact_immediate_successor_allowed(self):self.assertEqual(self.view['bridge'],self.check())
    def test_original_hold_precedes_reserve_confirmation_refused(self):
        self.child['confirmed']=101
        with self.assertRaises(ValueError):self.check()
    def test_primary_proof_and_transition_must_both_expire(self):
        for now in (409,499):
            with self.assertRaises(ValueError):self.check(now)
    def test_wrong_or_multihop_lineage_refused(self):
        for field,value in (('epoch',3),('account_id',2),('incarnation','66666666-6666-4666-8666-666666666666'),
            ('predecessor',None),('target_subject','user_other1')):
            old=self.new[field];self.new[field]=value
            with self.assertRaises(ValueError):self.check()
            self.new[field]=old
    def test_unresolved_commit_or_missing_readiness_refused(self):
        for name in ('uncommitted_verified','acknowledgement_resolved','successor_ready_verified'):
            self.view['lineage'][name]=False
            with self.assertRaises(ValueError):self.check()
            self.view['lineage'][name]=True
    def test_changed_primary_verifier_or_archive_refused(self):
        self.primary['hash']='d'*64
        with self.assertRaises(ValueError):self.check()
        self.primary['hash']='c'*64;self.view['lineage']['original_plan']=b'changed'
        with self.assertRaises(ValueError):self.check()
    def test_active_primary_refused(self):
        self.primary.update(version=1,state='active')
        with self.assertRaises(ValueError):self.check()
    def test_preconsume_reselection_matches_frozen_primary_not_enrollment(self):
        self.view['lineage']['primary_selection']=b'actual-primary-selection-after-B-P'
        self.primary['receipt']['selection']=reserve.sha(self.view['lineage']['primary_selection'])
        self.assertEqual(self.view['bridge'],self.check())
        self.view['lineage']['primary_selection']=b'different-selection'
        with self.assertRaises(ValueError):self.check()
    def test_enrollment_after_hold_refused(self):
        with self.assertRaises(ValueError):reserve.enrollment(credential(),dict(binding='a'*64,
            original_operation=self.old['operation'],original_selection=b's',original_plan=b'p',prehold_verified=False))
    def test_transactional_attest_uses_same_connection_guard(self):
        events=[];connection=object();engine=object()
        coordinator=object.__new__(reserve.Coordinator)
        coordinator.primary=SimpleNamespace(read=lambda:self.primary)
        coordinator.reserve=SimpleNamespace(read=lambda:self.child,immutable=lambda name:
            self.meta if name=='enrollment.json' else self.issued)
        coordinator.now=lambda:510;coordinator.locked=lambda:nullcontext()
        @contextmanager
        def transaction_view(actual,operation):
            self.assertIs(connection,actual);self.assertEqual(self.new['operation'],operation)
            events.append('transaction-view');yield self.view;events.append('transaction-rechecked')
        coordinator.host=SimpleNamespace(transaction_view=transaction_view,locks=lambda:nullcontext())
        packet={'version':2,'context':self.new,'holder':{'completed_at':instant(505)},'expires_at':instant(800)}
        def submit(raw,target,guard):
            self.assertIs(engine,target);guard(connection,packet);events.append('attested');return {'recorded':True}
        def process(action,operation,source,target,submit):return submit(b'synthetic',target)
        evidence=SimpleNamespace(submit_with_holder_guard=submit,_same_context=lambda left,right:self.assertEqual(left,right),
            instant=datetime.fromisoformat)
        bridge=SimpleNamespace(ops=SimpleNamespace(host_locks=lambda:nullcontext()),
            operator=SimpleNamespace(process=process),identity_entry=SimpleNamespace(evidence=evidence),
            verify=lambda store,view,now:{'issued':505,'expires':805})
        with patch.dict(sys.modules,{'owner_recovery_bridge':bridge}):
            self.assertEqual({'recorded':True},coordinator.attest(self.new['operation'],'source',engine))
            self.assertEqual(['transaction-view','transaction-rechecked','attested'],events)
            events.clear();self.view['lineage']['uncommitted_verified']=False
            with self.assertRaises(ValueError):coordinator.attest(self.new['operation'],'source',engine)
            self.assertNotIn('attested',events)

@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'native root-owned filesystem contract')
class NativeStoreTests(unittest.TestCase):
    def setUp(self):
        # /root avoids the shared writable /tmp ancestry forbidden by the real Store.
        self.temp=tempfile.TemporaryDirectory(prefix='reserve-test-',dir='/root')
        self.root=Path(self.temp.name);os.chmod(self.root,0o700)
        self.patch=patch.object(reserve,'ROOT',self.root);self.patch.start();self.store=reserve.Store()
    def tearDown(self):self.patch.stop();self.temp.cleanup()
    def test_metadata_exclusive_and_readback(self):
        self.store.immutable('enrollment.json',{'synthetic':1})
        with self.assertRaises(FileExistsError):self.store.immutable('enrollment.json',{'synthetic':2})
        self.assertEqual({'synthetic':1},self.store.read_metadata())
    def test_symlink_and_hardlink_metadata_refused(self):
        target=self.root/'target';target.write_bytes(b'{}');os.chmod(target,0o600)
        path=self.root/'enrollment.json';path.symlink_to(target)
        with self.assertRaises((OSError,ValueError)):self.store.read_metadata()
        path.unlink();os.link(target,path)
        with self.assertRaises(ValueError):self.store.read_metadata()
    def test_no_replacement_or_direct_generation(self):
        for action in (self.store.replace_pending,self.store.new_generation):
            with self.assertRaises(ValueError):action('a'*64,10)
        self.assertFalse(self.store.path.exists())
    def test_one_issuance_even_without_issued_receipt(self):
        self.store.immutable('enrollment.json',{'binding':'a'*64})
        with self.store.locked():
            self.store.issue_once('a'*64,10)
            old=self.store.path.read_bytes()
            with self.assertRaises(ValueError):self.store.issue_once('a'*64,20)
            self.assertEqual(old,self.store.path.read_bytes())
    def test_interrupted_issuance_never_regenerates_after_missing_credential(self):
        self.store.immutable('enrollment.json',{'binding':'a'*64})
        with self.store.locked():
            with patch.object(recovery.Store,'new_generation',side_effect=RuntimeError('synthetic interruption')):
                with self.assertRaises(RuntimeError):self.store.issue_once('a'*64,10)
            self.assertFalse(self.store.path.exists())
            self.assertTrue((self.root/'issuance-claim.json').exists())
            with self.assertRaises(FileExistsError):self.store.issue_once('a'*64,20)

@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'native root-owned filesystem contract')
class NativeCoordinatorTests(unittest.TestCase):
    def setUp(self):
        PolicyTests.setUp(self)
        self.temp=tempfile.TemporaryDirectory(prefix='reserve-coordinator-',dir='/root')
        base=Path(self.temp.name);os.chmod(base,0o700)
        self.primary_root=base/'primary';self.reserve_root=base/'reserve'
        self.primary_root.mkdir(mode=0o700);self.reserve_root.mkdir(mode=0o700)
        self.patches=[patch.object(recovery,'STATE',self.primary_root),patch.object(reserve,'ROOT',self.reserve_root)]
        for item in self.patches:item.start()
        self.primary_store=recovery.Store(self.primary_root);self.reserve_store=reserve.Store()
        with self.primary_store.locked():self.primary_store.write(credential())
        self.now=30;self.events=[];self.answers=[];self.code=None
        original_primary_lock=self.primary_store.locked;original_reserve_lock=self.reserve_store.locked
        @contextmanager
        def primary_lock():
            with original_primary_lock():
                self.events.append('primary-enter')
                try:yield
                finally:self.events.append('primary-exit')
        @contextmanager
        def reserve_lock():
            self.assertEqual('primary-enter',self.events[-1])
            with original_reserve_lock():
                self.events.append('reserve-enter')
                try:yield
                finally:self.events.append('reserve-exit')
        self.primary_store.locked=primary_lock;self.reserve_store.locked=reserve_lock
        @contextmanager
        def host_view(value):
            self.assertIn('reserve-enter',self.events);self.events.append('host-enter')
            try:yield deepcopy(value)
            finally:self.events.append('host-exit')
        enrollment_view=dict(binding='a'*64,original_operation=self.old['operation'],
            original_selection=b'original-selection',original_plan=b'original-plan',prehold_verified=True)
        self.host=SimpleNamespace(enrollment_view=lambda:host_view(enrollment_view),
            successor_view=lambda op:host_view(self.view),primary_selection=lambda:b'original-selection')
        self.coordinator=reserve.Coordinator(self.host,primary=self.primary_store,reserve=self.reserve_store,now=lambda:self.now)
        def show(value):self.code=value.splitlines()[1]
        self.terminal=SimpleNamespace(show=show,ask=lambda prompt:self.answers.pop(0))
        # Reuse the actual bridge functions without importing operational modules.
        tree=ast.parse((Path(__file__).parent/'owner_recovery_bridge.py').read_text(encoding='utf-8'))
        nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ('consume','verify')]
        namespace={'recovery':recovery,'hmac':hmac}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'reviewed-bridge-functions','exec'),namespace)
        self.bridge=SimpleNamespace(consume=namespace['consume'],verify=namespace['verify'],
            ops=SimpleNamespace(host_locks=lambda:nullcontext()))
        self.module_patch=patch.dict(sys.modules,{'owner_recovery_bridge':self.bridge});self.module_patch.start()
    def tearDown(self):
        self.module_patch.stop()
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()
    def enroll_confirm(self):
        original=self.primary_store.path.read_bytes()
        self.coordinator.enroll(self.terminal);self.assertIsNotNone(self.code)
        self.now=40;self.answers=[self.code];self.coordinator.confirm(self.terminal)
        self.assertEqual(original,self.primary_store.path.read_bytes())
        self.assertEqual('active',self.reserve_store.read()['state'])
    def expire_primary(self):
        # Model the separately owned primary consumption. Reserve coordinator
        # never performs this write; its enrolled credential anchor is unchanged.
        with recovery.Store.locked(self.primary_store):self.primary_store.write(self.primary)
        self.now=510
    def consume(self):
        self.answers=['RESERVE-SUCCESSOR',self.code]
        return self.coordinator.consume(self.new['operation'],self.terminal)
    def test_enroll_confirm_consume_preserves_primary_and_lock_order(self):
        self.enroll_confirm();self.expire_primary();original=self.primary_store.path.read_bytes()
        self.consume();self.assertEqual(original,self.primary_store.path.read_bytes())
        result=self.coordinator.receipt(self.new['operation'])
        self.assertEqual({'outcome','issued_at','expires_at'},set(result))
        self.assertEqual(instant(510),result['issued_at']);self.assertEqual(instant(810),result['expires_at'])
        self.assertEqual('primary-exit',self.events[-1])
    def test_active_or_unexpired_primary_never_consumes_reserve(self):
        self.enroll_confirm()
        with self.assertRaises(ValueError):self.consume()
        self.expire_primary();self.now=409
        with self.assertRaises(ValueError):self.consume()
        self.assertEqual('active',self.reserve_store.read()['state'])
    def test_failure_before_consume_preserves_active_reserve(self):
        self.enroll_confirm();self.expire_primary();original=self.primary_store.path.read_bytes()
        with patch.object(self.bridge,'consume',side_effect=RuntimeError('synthetic before-write')):
            with self.assertRaises(RuntimeError):self.consume()
        self.assertEqual('active',self.reserve_store.read()['state'])
        self.assertEqual(original,self.primary_store.path.read_bytes())
    def test_lost_durable_consume_ack_no_third_credential_or_replay(self):
        self.enroll_confirm();self.expire_primary();original=self.primary_store.path.read_bytes()
        write=self.reserve_store.write
        def lost_ack(value):
            write(value)
            if value['state']=='consumed':raise RuntimeError('synthetic lost consumption acknowledgement')
        with patch.object(self.reserve_store,'write',side_effect=lost_ack):
            with self.assertRaises(RuntimeError):self.consume()
        self.assertEqual('consumed',self.reserve_store.read()['state'])
        with self.assertRaises(ValueError):self.consume()
        with self.assertRaises((ValueError,FileExistsError)):self.coordinator.enroll(self.terminal)
        self.assertEqual(original,self.primary_store.path.read_bytes())
    def test_expired_historical_ack_uses_no_submit_or_fresh_proof(self):
        self.enroll_confirm();self.expire_primary();self.consume();self.now=901
        def process(action,operation,source,engine,submit):
            self.assertEqual('reconcile',action)
            with self.assertRaises(ValueError):submit(b'synthetic',engine)
            return {'recorded':True,'historical':True}
        self.bridge.operator=SimpleNamespace(process=process)
        self.assertEqual({'recorded':True,'historical':True},self.coordinator.historical(
            'reconcile',self.new['operation'],'synthetic-source',object()))
        with self.assertRaises(ValueError):self.coordinator.receipt(self.new['operation'])
    def test_consume_post_yield_delay_refuses_success_preserves_consumption(self):
        self.enroll_confirm();self.expire_primary()
        @contextmanager
        def delayed(operation):
            yield deepcopy(self.view)
            if self.reserve_store.read()['state']=='consumed':self.now=811
        self.host.successor_view=delayed
        with self.assertRaises(ValueError):self.consume()
        self.assertEqual('consumed',self.reserve_store.read()['state'])
        self.assertEqual(810,self.reserve_store.read()['receipt']['expires'])
    def test_receipt_post_yield_delay_refuses_success(self):
        self.enroll_confirm();self.expire_primary();self.consume()
        @contextmanager
        def delayed(operation):
            yield deepcopy(self.view)
            self.now=811
        self.host.successor_view=delayed
        with self.assertRaises(ValueError):self.coordinator.receipt(self.new['operation'])
    def test_manifest_post_yield_delay_retains_intake_without_success(self):
        self.enroll_confirm();self.expire_primary();self.consume()
        path=self.reserve_root/'synthetic-intake';journal=self.reserve_root/'synthetic-journal'
        @contextmanager
        def delayed(operation):
            yield deepcopy(self.view)
            if path.exists():self.now=811
        self.host.successor_view=delayed
        self.bridge.operator=SimpleNamespace(locations=lambda operation:(path,journal))
        def build(context,ask,verify_holder,now):
            verify_holder();return b'synthetic-manifest'
        self.bridge.identity_entry=SimpleNamespace(build_with_verified_holder_receipt=build,
            evidence=SimpleNamespace(validate=lambda raw:{'expires_at':instant(800)},instant=datetime.fromisoformat))
        def publish(destination,raw):
            with destination.open('xb') as stream:stream.write(raw)
        self.bridge.ops.write_protected=publish
        with self.assertRaises(ValueError):self.coordinator.enter(self.new['operation'],self.terminal)
        self.assertEqual(b'synthetic-manifest',path.read_bytes())
        self.assertFalse(journal.exists())

if __name__=='__main__':unittest.main()
