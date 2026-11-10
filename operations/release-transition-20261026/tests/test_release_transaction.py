from portable_bootstrap import install
install()
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import unittest
import release_transaction as t

NOW=datetime(2026,10,26,tzinfo=timezone.utc)
CORRECTION=t.CORRECTION
BOOT='11111111-1111-1111-1111-111111111111'

def fixture():
    policy={'schema':'careertrellis.paired-maintenance-policy/v1',
        'application_release':t.BASE,'application_build':t.BUILDS[t.BASE],
        'valid_from':'2026-10-25T00:00:00Z','valid_until':'2027-01-24T00:00:00Z',
        'enabled':True,'ops_release':'a'*40,'manifest_revision':'b'*40,
        'max_capture_bytes':1000,'timezone':'America/New_York','retention_policy':'unchanged'}
    status={'schema':'careertrellis.paired-maintenance-status/v1','policy_sha256':t.policy_digest(policy),
        'boot_id':BOOT,'release':t.BASE,'phase':'idle','result':'ok',
        'observed_at':'2026-10-26T00:00:00Z','services_restored':True}
    states={r:{'boot':BOOT,'private_policy':deepcopy(policy),'public_policy':deepcopy(policy),
        'status':deepcopy(status),'interlock':None,'installed_verified':True,
        'terminal_verified':True,'no_unresolved_intents':True} for r in t.ROLES}
    states['web']['interlock']={'schema':'careertrellis.paired-maintenance-interlock/v1',
        'policy_sha256':t.policy_digest(policy),'boot_id':BOOT,'release':t.BASE,'phase':'idle'}
    plan={'schema':'careertrellis.app-policy-release/v2','base':t.BASE,'previous':t.PREVIOUS,
        'target':t.TARGET,'correction':CORRECTION,'builds':{**t.BUILDS,CORRECTION:'20261026.98'},
        'boots':{r:BOOT for r in t.ROLES},'old_policy_digests':{r:t.policy_digest(policy) for r in t.ROLES},
        'new_policy_digests':{rev:{r:t.policy_digest(t.new_policy(policy,rev,build,NOW)) for r in t.ROLES}
            for rev,build in [(t.TARGET,'20261026.97'),(CORRECTION,'20261026.98')]},
        'packet_sha256':'d'*64,'readiness_manifest_sha256':'9'*64,'source_diffs':{t.TARGET:'e'*64,CORRECTION:'f'*64},
        'prepared_receipts':{t.TARGET:'1'*64,CORRECTION:'2'*64},'prepared_receipt_kinds':{t.TARGET:'corrected-fixture97-new/v1',CORRECTION:'repair98-retained/v1'},'expires_at':'2026-10-26T00:30:00Z'}
    return states,plan

class Fake:
    def __init__(self,fail=None):
        self.states,self.plan=fixture();self.current=t.BASE;self.previous=t.PREVIOUS
        self.policies={r:deepcopy(s['private_policy']) for r,s in self.states.items()}
        self.marker=deepcopy(self.states['web']['interlock']);self.events=[];self.stopped=False
        self.claims=set();self.fail=fail;self.fired=False;self.contained=False
    def event(self,event):
        self.events.append(event)
        if self.fail==event and not self.fired:self.fired=True;raise RuntimeError('synthetic fault')
    @contextmanager
    def web_locks(self):
        self.event('web_lock');yield;self.event('web_unlock')
    @contextmanager
    def db_locks(self):
        self.event('db_lock');yield;self.event('db_unlock')
    def pointer(self,name):return self.current if name=='current' else self.previous
    def baseline(self,role):return deepcopy(self.states[role])
    def verify_packet(self,x):self.event('verify_packet')
    def verify_prepared(self,p):self.event('verify_prepared')
    def verify_fresh_proofs(self,p,*boundary):self.event('verify_fresh_proofs')
    def assert_readiness_current(self,*boundary):pass
    def assert_preserved(self):self.event('preserved')
    def authorize_terminal(self,result,revision):self.authorization=(result,revision);self.event('authorize_'+result)
    def revoke_terminal(self):self.authorization=None;self.event('revoke_terminal')
    def begin_recovery(self):self.event('begin_recovery')
    @contextmanager
    def recovery_signals(self):yield
    def assert_owned_state(self,*a):self.event('owned_state')
    def block_starts(self):self.event('block_starts');self.blocked=True
    def assert_starts_blocked(self):t.need(self.blocked)
    def audit(self,stage,p):self.event('audit_'+stage)
    def claim(self,role,d,state):
        self.event('claim_'+role);t.need(role not in self.claims);self.claims.add(role)
    def stop(self):self.event('stop');self.stopped=True
    def assert_stopped(self):t.need(self.stopped)
    def write_policies(self,role,p):
        self.assert_stopped();self.policies[role]=deepcopy(p);self.event('policy_'+role)
    def policy_readback(self,r):return self.policies[r]
    def phase(self,r,phase,rev):self.event(phase+'_'+r)
    def point(self,rev,expected):
        t.need(self.current==expected);self.assert_stopped();self.current=rev;self.event('point_'+rev)
    def write_interlock(self,m):self.marker=deepcopy(m);self.event('interlock')
    def interlock_readback(self):return self.marker
    def assert_writer_gate(self,rev):
        t.need(self.current==rev and self.policies['web']['application_release']==rev
            and self.marker['release']==rev and self.marker['policy_sha256']==t.policy_digest(self.policies['web']))
        self.event('gate_'+rev)
    def start_and_stabilize(self,rev,b):self.event('start_'+rev);self.stopped=False
    def project_verifying(self,rev,b):self.event('verifying_'+rev)
    def assert_healthy(self,rev,b):self.event('healthy_'+rev)
    def finish(self,r,rev,p,result):self.event('finish_'+r+'_'+rev)
    def restore_original(self,r,s):
        self.policies[r]=deepcopy(s['private_policy'])
        if r=='web':self.marker=deepcopy(s['interlock'])
        self.event('restore_'+r)
    def finish_original(self,r,s):self.event('original_'+r)
    def contain(self):self.contained=True;self.stopped=True;self.event('contain')
    def record_containment(self):self.event('contained_receipt')

class Tests(unittest.TestCase):
    def run_tx(self,h):return t.execute(h,h.plan,approved_digest=t.digest(h.plan),now=NOW)
    def test_target_success_both_policies_and_previous_preserved(self):
        h=Fake();self.assertEqual(self.run_tx(h)['result'],'target_healthy')
        self.assertEqual(h.previous,t.PREVIOUS)
        for role in t.ROLES:
            old=h.states[role]['private_policy'];new=h.policies[role]
            self.assertEqual({k:v for k,v in old.items() if k not in t.POLICY_KEYS},
                             {k:v for k,v in new.items() if k not in t.POLICY_KEYS})
        self.assertLess(h.events.index('claim_db'),h.events.index('stop'))
    def test_no_approval_no_host_activity(self):
        h=Fake()
        with self.assertRaises(t.Refused):t.execute(h,h.plan,now=NOW)
        self.assertEqual(h.events,[])
    def test_expiry_one_hour_inclusive(self):
        h=Fake();h.plan['expires_at']='2026-10-26T01:00:00Z'
        t.validate_plan(h.plan,NOW)
    def test_expiry_above_one_hour_refuses_without_host_activity(self):
        h=Fake();h.plan['expires_at']='2026-10-26T01:00:01Z'
        with self.assertRaises(t.Refused):self.run_tx(h)
        self.assertEqual(h.events,[])
    def test_expiry_equal_now_refuses_without_host_activity(self):
        h=Fake();h.plan['expires_at']=NOW.isoformat()
        with self.assertRaises(t.Refused):self.run_tx(h)
        self.assertEqual(h.events,[])
    def test_bad_plan_refuses_before_host(self):
        for key,value in [('target','a'*40),('previous',t.BASE),('expires_at','2026-10-25T00:00:00Z'),('correction',t.BASE)]:
            with self.subTest(key=key):
                h=Fake();h.plan[key]=value
                with self.assertRaises(t.Refused):self.run_tx(h)
                self.assertEqual(h.events,[])
    def test_bad_baseline_blocks_before_claim(self):
        for role,key,value in [('db','boot','bad'),('web','terminal_verified',False),('db','no_unresolved_intents',False)]:
            h=Fake();h.states[role][key]=value
            with self.assertRaises(t.Refused):self.run_tx(h)
            self.assertFalse(h.claims)
    def test_policy_public_mismatch_and_wrong_new_digest(self):
        h=Fake();h.states['db']['public_policy']['enabled']=False
        with self.assertRaises(t.Refused):self.run_tx(h)
        h=Fake();h.plan['new_policy_digests'][t.TARGET]['web']='3'*64
        with self.assertRaises(t.Refused):self.run_tx(h)
        self.assertFalse(h.claims)
    def test_faults_before_pointer_restore_exact_original(self):
        for fault in ['stop','audit_stopped','policy_web','policy_db','policy_selected_db']:
            with self.subTest(fault=fault):
                h=Fake(fault)
                with self.assertRaises(t.Refused):self.run_tx(h)
                self.assertEqual(h.current,t.BASE);self.assertFalse(h.contained)
                self.assertEqual(h.policies['web'],h.states['web']['private_policy'])
                self.assertNotIn('point_'+t.BASE,h.events)
    def test_faults_after_pointer_select_only_new_correction(self):
        for fault in ['point_'+t.TARGET,'interlock','gate_'+t.TARGET,'start_'+t.TARGET,'healthy_'+t.TARGET,'finish_web_'+t.TARGET]:
            with self.subTest(fault=fault):
                h=Fake(fault)
                with self.assertRaises(t.Refused):self.run_tx(h)
                self.assertEqual(h.current,CORRECTION);self.assertFalse(h.contained)
                self.assertEqual(h.policies['db']['application_release'],CORRECTION)
                self.assertNotIn('point_'+t.BASE,h.events)
    def test_failed_correction_contained_without_false_success(self):
        class Broken(Fake):
            def start_and_stabilize(self,rev,b):raise RuntimeError('synthetic')
        h=Broken()
        with self.assertRaises(t.Refused):self.run_tx(h)
        self.assertTrue(h.contained);self.assertTrue(h.stopped)
        self.assertNotIn('finish_web_'+CORRECTION,h.events)
        self.assertIsNone(h.authorization)
    def test_partial_target_finish_then_failed_correction_never_authorizes_release(self):
        class Partial(Fake):
            def finish(self,role,rev,policy,result):
                super().finish(role,rev,policy,result)
                if role=='db' and rev==t.TARGET:raise RuntimeError('synthetic DB completion failure')
            def start_and_stabilize(self,rev,build):
                if rev==CORRECTION:raise RuntimeError('synthetic correction failure')
                super().start_and_stabilize(rev,build)
        h=Partial()
        with self.assertRaises(t.Refused):self.run_tx(h)
        self.assertTrue(h.contained);self.assertIsNone(h.authorization)
        self.assertFalse(any(x.startswith('authorize_') for x in h.events))
    def test_success_requires_both_completion_receipts_before_release_authority(self):
        h=Fake();self.run_tx(h)
        self.assertEqual(h.authorization,('target_healthy',t.TARGET))
        self.assertLess(h.events.index('finish_db_'+t.TARGET),h.events.index('authorize_target_healthy'))
    def test_global_terminal_unknown_contains_without_automatic_correction(self):
        class Unknown(Fake):
            def authorize_terminal(self,result,revision):raise t.GlobalTerminalUnknown('synthetic one-sided cert')
        h=Unknown()
        with self.assertRaises(t.Refused):self.run_tx(h)
        self.assertTrue(h.contained);self.assertIsNone(h.authorization)
        self.assertEqual(h.current,t.TARGET)
        self.assertNotIn('point_'+CORRECTION,h.events)
    def test_post_authorization_failure_and_failed_correction_revoke_stale_authority(self):
        class Post(Fake):
            def authorize_terminal(self,result,revision):
                super().authorize_terminal(result,revision)
                if revision==t.TARGET:raise RuntimeError('synthetic post-auth failure')
            def start_and_stabilize(self,revision,build):
                if revision==CORRECTION:raise RuntimeError('synthetic failed correction')
                super().start_and_stabilize(revision,build)
        h=Post()
        with self.assertRaises(t.Refused):self.run_tx(h)
        self.assertIn('authorize_target_healthy',h.events)
        self.assertTrue(h.contained);self.assertIsNone(h.authorization)
        self.assertIn('begin_recovery',h.events)
    def test_unknown_pointer_contained(self):
        class Unknown(Fake):
            def point(self,rev,expected):self.current='9'*40;raise RuntimeError('unknown')
        h=Unknown()
        with self.assertRaises(t.Refused):self.run_tx(h)
        self.assertTrue(h.contained)
    def test_replay_refused_without_second_stop(self):
        h=Fake();self.run_tx(h);stops=h.events.count('stop')
        with self.assertRaises(t.Refused):self.run_tx(h)
        self.assertEqual(h.events.count('stop'),stops)
    def test_claim_failure_never_stops_or_changes_policy(self):
        h=Fake('claim_db')
        with self.assertRaises(RuntimeError):self.run_tx(h)
        self.assertNotIn('stop',h.events);self.assertEqual(h.current,t.BASE)

if __name__=='__main__':unittest.main()
