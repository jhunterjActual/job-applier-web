"""Synthetic ledgers only; no dispatch/provider/credential/network activity."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from threading import Barrier,Lock
import unittest
from unittest.mock import patch

import campaign_state as module
from campaign_state import Campaign,CampaignRefused,validate_policy
from test_campaign_state import policy

def v2():
    p=policy();p['schema']='study-campaign-policy-v2';p['max_inflight_requests']=4
    p['request_limits']={product:{'fit':8,'materials':8,'packet':16} for product in ('CT','UP')}
    return p

class ConcurrentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='v2-synthetic-');self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'campaign.jsonl';self.policy=v2()
        self.c=Campaign.create(self.path,self.policy);self.c.begin_stage('CT','V01','fit')
    def reserve(self,name,amount='1'):self.c.reserve(name,amount,'request/synthetic')
    def test_explicit_schema_capacity_and_frozen_reopen(self):
        for value in [True,False,0,5,1.0,'4',[],{}]:
            p=v2();p['max_inflight_requests']=value
            with self.assertRaises(CampaignRefused):validate_policy(p)
        for schema in [[],{},None,True,'unknown']:
            p=policy();p['schema']=schema
            with self.assertRaises(CampaignRefused):validate_policy(p)
        p=v2();del p['max_inflight_requests']
        with self.assertRaises(CampaignRefused):validate_policy(p)
        p=policy();p['max_inflight_requests']=4
        with self.assertRaises(CampaignRefused):validate_policy(p)
        p=v2();p['max_inflight_requests']=3
        with self.assertRaises(CampaignRefused):Campaign.open(self.path,p)
        with self.assertRaises(CampaignRefused):Campaign.open(self.path,policy())
    def test_four_same_handle_threads_overlap_external_work_out_of_order(self):
        admitted=Barrier(4);release=Barrier(4);guard=Lock();observed=[]
        def work(i):
            self.reserve('call'+str(i))
            with guard:observed.append(i)
            admitted.wait(timeout=5)
            # This represents overlapping external work; no journal lock is held.
            snapshot=self.c.snapshot();self.assertEqual(snapshot['pending_calls'],4)
            release.wait(timeout=5)
            self.c.settle('call'+str(i),'0.25','usage/synthetic')
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(work,range(4)))
        self.assertEqual(set(observed),set(range(4)))
        s=self.c.snapshot();self.assertEqual(s['admitted_requests'],4);self.assertEqual(s['pending_calls'],0)
        self.assertEqual(s['exposure_microdollars'],1_000_000)
        self.c.finish_stage('succeeded','terminal/synthetic')
    def test_fifth_capacity_duplicate_and_pending_stage_guards(self):
        for i in range(4):self.reserve('call'+str(i))
        before=self.path.read_bytes()
        for action in [lambda:self.reserve('fifth'),lambda:self.reserve('call0'),
                       lambda:self.c.finish_stage('failed','terminal/synthetic'),
                       lambda:self.c.begin_stage('UP','V01','fit')]:
            with self.assertRaises(CampaignRefused):action()
        self.assertEqual(self.path.read_bytes(),before)
        for i in reversed(range(4)):self.c.settle('call'+str(i),'0','usage/synthetic')
        with self.assertRaises(CampaignRefused):self.c.settle('call0','0','usage/synthetic')
    def test_second_preconstructed_and_reopened_handles_cannot_admit_orphans(self):
        pre=Campaign(self.path,self.policy);self.reserve('owned')
        for other in [pre,Campaign(self.path,self.policy),Campaign.open(self.path,self.policy)]:
            self.assertEqual(other.snapshot()['pending_calls'],1)
            with self.assertRaises(CampaignRefused):other.reserve('orphan-retry','1','request/synthetic')
        self.reserve('same-live-handle')
        self.assertEqual(self.c.snapshot()['pending_calls'],2)
    def test_two_independent_handles_contend_without_duplicate_admission(self):
        handles=[Campaign(self.path,self.policy),Campaign(self.path,self.policy)];barrier=Barrier(2)
        def contender(i):
            barrier.wait(timeout=5)
            try:handles[i].reserve('independent'+str(i),'1','request/synthetic');return True
            except CampaignRefused:return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(contender,range(2)))
        self.assertEqual(sum(results),1);self.assertEqual(self.c.snapshot()['admitted_requests'],1)
        self.assertEqual(self.c.snapshot()['exposure_microdollars'],1_000_000)
    def test_unknown_latches_new_admissions_other_known_outcomes_still_settle(self):
        for i in range(4):self.reserve('call'+str(i))
        self.c.mark_unknown('call1','outcome/synthetic')
        with self.assertRaises(CampaignRefused):self.reserve('new')
        with self.assertRaises(CampaignRefused):self.c.settle('call1','0','usage/synthetic')
        for i in [3,0,2]:self.c.settle('call'+str(i),'0.2','usage/synthetic')
        s=self.c.snapshot();self.assertTrue(s['halted']);self.assertEqual(s['pending_calls'],1)
        self.assertEqual(s['exposure_microdollars'],1_600_000)
        with self.assertRaises(CampaignRefused):self.c.finish_stage('failed','terminal/synthetic')
        with self.assertRaises(CampaignRefused):self.c.begin_stage('UP','V01','fit')
        self.assertEqual(Campaign.open(self.path,self.policy).snapshot(),s)
    def test_over_reservation_records_charge_and_other_known_settlement(self):
        self.reserve('over');self.reserve('known');self.c.settle('over','2','usage/synthetic')
        with self.assertRaises(CampaignRefused):self.reserve('new')
        self.c.settle('known','0.1','usage/synthetic');s=self.c.snapshot()
        self.assertTrue(s['halted']);self.assertEqual(s['pending_calls'],0);self.assertEqual(s['exposure_microdollars'],2_100_000)
        with self.assertRaises(CampaignRefused):self.c.finish_stage('failed','terminal/synthetic')
    def test_atomic_dollar_caps_across_contending_threads(self):
        for dimension in ['total','packet','stage']:
            p=v2()
            if dimension=='total':
                p['total_limit_usd']='2.5';p['packet_limits_usd']={x:'2.5' for x in ('CT','UP')}
                p['stage_limits_usd']={x:{'fit':'2.5','materials':'2.5'} for x in ('CT','UP')}
            elif dimension=='packet':p['packet_limits_usd']['CT']='2.5';p['stage_limits_usd']['CT']={'fit':'2.5','materials':'2.5'}
            else:p['stage_limits_usd']['CT']['fit']='2.5'
            c=Campaign.create(self.path.with_name(dimension+'.jsonl'),p);c.begin_stage('CT','V01','fit');barrier=Barrier(8)
            def contender(i):
                barrier.wait(timeout=5)
                try:c.reserve('call'+str(i),'1','request/synthetic');return True
                except CampaignRefused:return False
            with ThreadPoolExecutor(max_workers=8) as pool:outcomes=list(pool.map(contender,range(8)))
            self.assertEqual(sum(outcomes),2);s=c.snapshot();self.assertEqual(s['admitted_requests'],2);self.assertEqual(s['exposure_microdollars'],2_000_000)
    def test_atomic_request_counts_never_released_by_zero_settlement(self):
        p=v2();p['request_limits']['CT']['fit']=2
        c=Campaign.create(self.path.with_name('counts.jsonl'),p);c.begin_stage('CT','V01','fit')
        c.reserve('one','1','request/synthetic');c.reserve('two','1','request/synthetic')
        c.settle('two','0','usage/synthetic');c.settle('one','0','usage/synthetic')
        with self.assertRaises(CampaignRefused):c.reserve('three','1','request/synthetic')
        self.assertEqual(c.snapshot()['admitted_requests'],2)
    def test_reservation_crash_is_pending_not_unknown_or_retryable(self):
        self.reserve('before-crash','3');reopened=Campaign.open(self.path,self.policy)
        s=reopened.snapshot();self.assertFalse(s['halted']);self.assertEqual(s['exposure_microdollars'],3_000_000)
        with self.assertRaises(CampaignRefused):reopened.reserve('retry','1','request/synthetic')
        # Exact known outcome may be accounted without issuing a new request.
        reopened.settle('before-crash','0.5','usage/synthetic');self.assertEqual(reopened.snapshot()['pending_calls'],0)
    def test_head_failure_never_adds_live_id_and_never_resumes(self):
        with patch.object(self.c,'_write_head',side_effect=OSError('synthetic head failure')):
            with self.assertRaises(OSError):self.reserve('not-owned')
        self.assertNotIn('not-owned',self.c._live_reservations)
        with self.assertRaises(CampaignRefused):Campaign.open(self.path,self.policy)
        with self.assertRaises(CampaignRefused):self.reserve('retry')
    def test_live_id_is_added_only_after_successful_head(self):
        original=self.c._write_head;observed=[]
        def head(*a,**kw):observed.append('owned' in self.c._live_reservations);return original(*a,**kw)
        with patch.object(self.c,'_write_head',side_effect=head):self.reserve('owned')
        self.assertEqual(observed,[False]);self.assertIn('owned',self.c._live_reservations)
    def test_v1_original_canonical_journal_and_pending_reopen_behavior(self):
        original=Path(__file__).parent/'fixtures'/'campaign_state_v1.py'
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),'cd339b8e9862dbf50e94ec82a9cc419467b5c3a769712ab79676f2066e57dab5')
        spec=importlib.util.spec_from_loader('original_campaign',loader=None);old=importlib.util.module_from_spec(spec)
        exec(compile(original.read_bytes(),str(original),'exec'),old.__dict__)
        paths=[self.path.with_name('v1-new.jsonl'),self.path.with_name('v1-old.jsonl')]
        objects=[Campaign.create(paths[0],policy()),old.Campaign.create(paths[1],policy())]
        for c in objects:
            c.begin_stage('CT','V01','fit');c.reserve('known','1','request/synthetic')
        self.assertEqual(paths[0].read_bytes(),paths[1].read_bytes())
        Campaign.open(paths[0],policy()).settle('known','0.2','usage/synthetic')
        old.Campaign.open(paths[1],policy()).settle('known','0.2','usage/synthetic')
        self.assertEqual(paths[0].read_bytes(),paths[1].read_bytes())
        self.assertEqual(objects[0].snapshot(),objects[1].snapshot())
    def test_hardlink_metadata_refusal_and_actual_pending_head_without_host_link(self):
        class Linked:
            parents=()
            def is_symlink(self):return False
            def exists(self):return True
            def is_file(self):return True
            def stat(self):return type('Info',(),{'st_nlink':2})()
        with self.assertRaises(CampaignRefused):module._regular(Linked())
        self.c.pending_head.write_bytes(b'synthetic interrupted head')
        with self.assertRaises(CampaignRefused):self.c.snapshot()

if __name__=='__main__':unittest.main()
