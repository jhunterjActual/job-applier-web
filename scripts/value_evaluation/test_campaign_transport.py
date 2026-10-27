"""Synthetic ledgers and explicitly scripted injected functions only."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
assert hashlib.sha256((HERE/'campaign_state.py').read_bytes()).hexdigest()=='b0394af9fa065f601840b53c03ccba39dc874e758c824503764917342fab16e6'
assert hashlib.sha256((HERE/'native_cost_envelope.py').read_bytes()).hexdigest()=='992f6a0b45200753f6071460d024e32a24dac14ed194f8a3b79f93f8b9f7d844'
from campaign_state import Campaign,CampaignRefused
from native_cost_envelope import request_reservation
from test_campaign_state import policy as base_policy
import campaign_transport as m

HEADERS=[['content-type','application/json'],['anthropic-version','2023-06-01']]
PRIVATE={'x-api-key':'synthetic-only-not-a-credential'}
RESPONSE=b'{"scripted":"complete"}'

class Harness:
    def __init__(self):self.mapper_calls=[];self.counter_calls=[];self.sender_calls=[];self.count_hook=None;self.send_hook=None;self.output=2;self.guard=True;self.parser_hook=None
    def adapters(self):
        def mapper(binding,body,sha):
            self.mapper_calls.append(body)
            return {'fixture_id':'scripted-fixture','original_sha256':sha,'count_body':m.canonical({'scripted_original_sha256':sha}),'mapping_sha256':'b'*64}
        def counter(body,headers,private,timeout):
            self.counter_calls.append((body,timeout))
            if self.count_hook:self.count_hook(body)
            return {'fixture_id':'scripted-fixture','complete':True,'input_tokens':10,'request_sha256':m.digest(body),'response_body':b'{"scripted_count":10}','status':200,'content_type':'application/json'}
        def sender(body,headers,private,timeout):
            self.sender_calls.append((body,timeout))
            if self.send_hook:self.send_hook(body)
            return {'status':200,'content_type':'application/json','body':RESPONSE}
        def parser(binding,original,response,meta):
            value={'fixture_id':'scripted-fixture','request_sha256':m.digest(original),'response_sha256':m.digest(response),'model':m.MODEL,'complete':True,'input_tokens':10,'output_tokens':self.output,'priced_categories':['input_tokens','output_tokens'],'evidence_sha256':'c'*64}
            if self.parser_hook:self.parser_hook(value)
            return value
        def guard(binding,req,res,body):return {'approved':self.guard,'request_sha256':req,'response_sha256':res,'fixture_id':'scripted-fixture'}
        sha=m.file_hash(Path(__file__))
        return [m.ScriptedAdapter(role,fn,sha) for role,fn in zip(m.ROLES,[mapper,counter,sender,parser,guard])]

def campaign_policy(capacity=4):
    p=base_policy();p['schema']='study-campaign-policy-v2';p['max_inflight_requests']=capacity
    p['request_limits']={x:{'fit':4,'materials':4,'packet':8} for x in ('CT','UP')};return p

def broker_policy(campaign,adapters):
    p=campaign.policy;stages={};packets={};total=0
    for x in p['order']:
        lim=p['request_limits'][x['product']];stages['/'.join(x[k] for k in ('product','case','stage'))]=lim[x['stage']]
        packets[x['product']+'/'+x['case']]=lim['packet'];total+=lim[x['stage']]
    sources={'broker':m.file_hash(Path(m.__file__)),'campaign':m.CAMPAIGN_SHA,'budget_guard':m.BUDGET_SHA,'native_cost_envelope':m.ENVELOPE_SHA,**{a.role:a.source_sha256 for a in adapters}}
    return {'schema':'broker-scripted-policy-v1','mode':'scripted','run_id':p['run_id'],'campaign_policy_sha256':m.digest(m.canonical(p)),
      'source_sha256':sources,'native_content_sha256':'d'*64,'model':m.MODEL,
      'pricing':{'model':m.MODEL,'input_usd_per_million':'3','output_usd_per_million':'15','source':'https://platform.claude.com/docs/en/about-claude/pricing','verified_utc':'synthetic-declaration-not-current-price-proof'},
      'operations':{k:v[2] for k,v in m.OPS.items()},'body_caps':{'CT':16*1024**2,'UP':4*1024**2},'response_cap_bytes':8*1024**2,
      'count_policy':{'run_limit':min(total,256),'per_stage_limits':stages,'per_packet_limits':packets},
      'count_mapping_sha256':sources['count_mapper'],'usage_parser_sha256':sources['usage_parser'],'response_guard_sha256':sources['response_guard'],
      'evidence_limit_bytes':1024**3,'generation_timeout_seconds':180,'count_timeout_seconds':30}

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='synthetic-broker-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.patch=patch.object(m,'fsync_dir',lambda p:None);self.patch.start();self.addCleanup(self.patch.stop)
        self.c=Campaign.create(self.root/'campaign.jsonl',campaign_policy());self.h=Harness();self.adapters=self.h.adapters();self.policy=broker_policy(self.c,self.adapters)
        self.b=m.Broker.create(self.root/'broker',self.c,self.policy,*self.adapters,reservation_calculator=request_reservation)
        self.c.begin_stage('CT','V01','fit')
    def binding(self,name='call'):
        return {'run_id':self.c.policy['run_id'],'call_id':name,'product':'CT','case':'V01','stage':'fit','operation':'ct_fit'}
    def raw(self,name='call'):
        return m.canonical({'model':m.MODEL,'max_tokens':16384,'metadata':{'id':name},'system':'non-ASCII: café','tools':[{'name':'synthetic'}],'messages':[{'role':'user','content':[{'type':'image','source':{'type':'base64','data':'synthetic'}}]}]})
    def exchange(self,name='call',seconds=90):return self.b.exchange(self.binding(name),self.raw(name),HEADERS,PRIVATE,seconds)
    def test_exact_raw_saved_pending_before_send_settle_before_return(self):
        raw=self.raw()
        def pending(body):self.assertEqual(body,raw);self.assertEqual(self.c.snapshot()['pending_calls'],1)
        self.h.send_hook=pending;result=self.exchange();self.assertEqual(result.body,RESPONSE)
        self.assertEqual((self.b.root/'call/request.bin').read_bytes(),raw)
        self.assertEqual(self.h.sender_calls[0][0],raw);self.assertEqual(self.c.snapshot()['pending_calls'],0)
        self.assertGreater(self.c.snapshot()['exposure_microdollars'],0);self.assertFalse(self.b.slots)
        self.assertFalse(self.c.snapshot()['completed_stages']);self.assertTrue(self.c.snapshot()['active'])
        self.assertNotIn(PRIVATE['x-api-key'].encode(),b''.join(p.read_bytes() for p in self.b.root.rglob('*') if p.is_file()))
    def test_four_counts_overlap_and_fifth_zero_external_work(self):
        entered=threading.Barrier(5);release=threading.Event();errors=[]
        self.h.count_hook=lambda body:(entered.wait(timeout=5),release.wait(5))
        def run(i):
            try:self.exchange('call'+str(i))
            except BaseException as e:errors.append(type(e).__name__)
        threads=[threading.Thread(target=run,args=(i,)) for i in range(4)]
        for t in threads:t.start()
        entered.wait(timeout=5);self.assertEqual(self.c.snapshot()['pending_calls'],0)
        with self.assertRaises(m.Refused):self.exchange('fifth')
        self.assertEqual(len(self.h.counter_calls),4);self.assertEqual(len(self.h.mapper_calls),4);self.assertEqual(len(self.h.sender_calls),0)
        release.set()
        for t in threads:t.join(10);self.assertFalse(t.is_alive())
        self.assertEqual(errors,[]);self.assertEqual(len(self.h.sender_calls),4);self.assertEqual(len(self.b.counts),4);self.assertFalse(self.b.slots)
    def test_count_completion_after_halt_never_new_reserve_or_sender(self):
        blocked=threading.Event();release=threading.Event();a_sha=m.digest(m.canonical({'scripted_original_sha256':m.digest(self.raw('A'))}));errors=[]
        def count(body):
            if m.digest(body)==a_sha:blocked.set();release.wait(5)
        self.h.count_hook=count;self.h.send_hook=lambda body:(_ for _ in ()).throw(TimeoutError())
        def work():
            try:self.exchange('A')
            except BaseException as e:errors.append(e)
        thread=threading.Thread(target=work);thread.start();self.assertTrue(blocked.wait(5))
        with self.assertRaises(m.Unknown):self.exchange('B')
        release.set();thread.join(5);self.assertFalse(thread.is_alive())
        self.assertTrue((self.b.root/'A/count-terminal.json').exists());self.assertEqual(len(self.h.sender_calls),1)
        self.assertEqual(self.c.snapshot()['admitted_requests'],1);self.assertTrue(self.b.halted)
    def test_known_charge_guard_and_later_delivery_failure_never_financial_unknown(self):
        self.h.guard=False
        with self.assertRaises(m.NativeRefused):self.exchange()
        s=self.c.snapshot();self.assertEqual(s['pending_calls'],0);self.assertGreater(s['exposure_microdollars'],0)
        self.assertFalse(s['halted']);self.assertTrue(self.b.halted);self.assertFalse(self.b.slots)
        with self.assertRaises(m.Refused):self.b.report_delivery('call','delivery_unknown')
        self.assertEqual(self.c.snapshot(),s)
    def test_known_charge_later_delivery_failure_is_separate_receipt(self):
        self.exchange();snapshot=self.c.snapshot();self.b.report_delivery('call','delivery_unknown')
        self.assertEqual(self.c.snapshot(),snapshot);self.assertTrue(self.b.halted)
    def test_unknown_usage_retains_reserved_slot_and_global_halt(self):
        self.h.parser_hook=lambda v:v.update(priced_categories=['unsupported_server_tool'])
        with self.assertRaises(m.Unknown):self.exchange()
        s=self.c.snapshot();self.assertTrue(s['halted']);self.assertEqual(s['pending_calls'],1)
        self.assertIn('call',self.b.slots)
        with self.assertRaises(m.Refused):self.exchange('new')
        self.assertEqual(len(self.h.sender_calls),1)
    def test_count_unknown_has_own_halt_without_generation_reservation(self):
        self.h.count_hook=lambda body:(_ for _ in ()).throw(TimeoutError())
        with self.assertRaises(m.Unknown):self.exchange()
        self.assertTrue(self.b.halted);self.assertEqual(self.c.snapshot()['admitted_requests'],0);self.assertIn('call',self.b.slots)
        self.assertEqual(len(self.h.sender_calls),0)
    def test_known_pre_count_refusal_releases_slot_after_terminal_only(self):
        c=Campaign.create(self.root/'zero-count-campaign.jsonl',campaign_policy());p=broker_policy(c,self.adapters);p['count_policy']['run_limit']=0
        b=m.Broker.create(self.root/'zero-count-broker',c,p,*self.adapters,reservation_calculator=request_reservation);c.begin_stage('CT','V01','fit')
        with self.assertRaises(m.Unknown):b.exchange(self.binding(),self.raw(),HEADERS,PRIVATE,90)
        self.assertTrue((b.root/'call/terminal.json').exists());self.assertFalse(b.slots)
        self.assertEqual(self.h.counter_calls,[]);self.assertEqual(self.h.sender_calls,[])
    def test_terminal_write_fault_retains_slot_known_charge_and_halt(self):
        original=self.b._write
        def fail(call,name,raw):
            if name=='terminal.json':raise OSError('synthetic')
            return original(call,name,raw)
        with patch.object(self.b,'_write',side_effect=fail),self.assertRaises(m.Unknown):self.exchange()
        self.assertTrue(self.b.halted);self.assertIn('call',self.b.slots);self.assertEqual(self.c.snapshot()['pending_calls'],0)
    def test_settlement_failure_pending_not_retried_or_marked_settled(self):
        with patch.object(self.c,'settle',side_effect=OSError('synthetic')) as settle,patch.object(self.c,'mark_unknown') as mark:
            with self.assertRaises(m.Unknown):self.exchange()
            self.assertEqual(settle.call_count,1);mark.assert_not_called()
        self.assertTrue(self.b.halted);self.assertEqual(self.c.snapshot()['pending_calls'],1)
    def test_generation_intent_failure_after_reserve_marks_unknown_zero_sends(self):
        original=self.b._write
        def fail(call,name,raw):
            if name=='generation-intent.json':raise OSError('synthetic')
            return original(call,name,raw)
        with patch.object(self.b,'_write',side_effect=fail),self.assertRaises(m.Unknown):self.exchange()
        self.assertTrue(self.c.snapshot()['halted']);self.assertEqual(self.h.sender_calls,[])
    def test_startup_active_or_reopened_dispatch_refused_inspection_only(self):
        with self.assertRaises(m.Refused):m.Broker.create(self.root/'second',self.c,self.policy,*self.adapters,reservation_calculator=request_reservation)
        info=m.Broker.open_for_inspection(self.b.root,self.c,self.policy);self.assertFalse(info['dispatch_supported'])
        binding=self.binding();binding['case']='V02'
        with self.assertRaises(m.Refused):self.b.exchange(binding,self.raw(),HEADERS,PRIVATE,90)
        self.assertEqual(self.h.counter_calls,[])
    def test_bad_native_headers_types_and_timeout_refuse_before_external(self):
        for value in [True,0,91,float('nan'),None]:
            with self.assertRaises(m.Refused):self.exchange(seconds=value)
        raw=self.raw().replace(b'16384',b'4096')
        with self.assertRaises(m.Refused):self.b.exchange(self.binding(),raw,HEADERS,PRIVATE,90)
        with self.assertRaises(m.Refused):self.b.exchange(self.binding(),self.raw(),HEADERS+[['Content-Type','application/json']],PRIVATE,90)
        self.assertEqual(self.h.counter_calls,[]);self.assertEqual(self.h.sender_calls,[])
    def test_saved_request_drift_refuses_before_sender(self):
        def drift(body):(self.b.root/'call/request.bin').write_bytes(b'X'*len(self.raw()))
        self.h.count_hook=drift
        with self.assertRaises(m.Unknown):self.exchange()
        self.assertEqual(self.h.sender_calls,[]);self.assertTrue(self.b.halted)
    def test_actual_over_reserve_records_known_charge_halts_new_admissions(self):
        self.h.output=100000;self.exchange()
        s=self.c.snapshot();self.assertTrue(s['halted']);self.assertEqual(s['pending_calls'],0);self.assertGreater(s['exposure_microdollars'],1_000_000)
        with self.assertRaises(m.Refused):self.exchange('new')
    def test_frozen_policy_and_manifest_corruption_refuse_zero_external_work(self):
        self.b.policy['count_policy']['run_limit']=0
        with self.assertRaises(m.Refused):self.exchange()
        self.b.policy=m.strict_json(self.b._frozen_raw)
        (self.b.root/'manifest.json').write_bytes(b'corrupt')
        with self.assertRaises(m.Unknown):self.exchange()
        self.assertEqual(self.h.counter_calls,[]);self.assertEqual(self.h.sender_calls,[])
    def test_other_admitted_known_call_settles_after_global_unknown(self):
        sends=threading.Barrier(2);unknown_done=threading.Event();outcomes={}
        def hook(body):
            name=json.loads(body)['metadata']['id'];sends.wait(timeout=5)
            if name=='unknown':raise TimeoutError()
            if not unknown_done.wait(5):raise AssertionError('unknown did not finish')
        self.h.send_hook=hook
        def work(name):
            try:outcomes[name]=self.exchange(name)
            except BaseException as exc:outcomes[name]=type(exc).__name__
            finally:
                if name=='unknown':unknown_done.set()
        threads=[threading.Thread(target=work,args=(name,)) for name in ['unknown','known']]
        for t in threads:t.start()
        for t in threads:t.join(10);self.assertFalse(t.is_alive())
        self.assertEqual(outcomes['unknown'],'Unknown');self.assertIsInstance(outcomes['known'],m.CompleteResponse)
        snapshot=self.c.snapshot();self.assertTrue(snapshot['halted']);self.assertEqual(snapshot['pending_calls'],1)
        self.assertIn('unknown',self.b.slots);self.assertNotIn('known',self.b.slots)
    def test_capacity_one_through_four_before_count_and_duplicate_owner(self):
        for capacity in range(1,5):
            c=Campaign.create(self.root/('capacity'+str(capacity)+'.jsonl'),campaign_policy(capacity));p=broker_policy(c,self.adapters)
            b=m.Broker.create(self.root/('broker'+str(capacity)),c,p,*self.adapters,reservation_calculator=request_reservation);c.begin_stage('CT','V01','fit')
            with b.lock:
                for i in range(capacity):b.slots['owned'+str(i)]='owner'
            before=len(self.h.counter_calls)
            with self.assertRaises(m.Refused):b.exchange(self.binding(),self.raw(),HEADERS,PRIVATE,90)
            self.assertEqual(len(self.h.counter_calls),before)
        self.exchange('already-used');before=len(self.h.counter_calls)
        with self.assertRaises(m.Refused):self.exchange('already-used')
        self.assertEqual(len(self.h.counter_calls),before)
    def test_public_native_refused_from_each_callback_cannot_bypass_state_accounting(self):
        for role in ('counter','sender','usage_parser','response_guard'):
            c=Campaign.create(self.root/(role+'.jsonl'),campaign_policy());h=Harness();adapters=h.adapters()
            def boom(*args):raise m.NativeRefused('injected-public-class')
            adapters=[m.ScriptedAdapter(a.role,boom,a.source_sha256) if a.role==role else a for a in adapters]
            p=broker_policy(c,adapters);b=m.Broker.create(self.root/('broker-'+role),c,p,*adapters,reservation_calculator=request_reservation);c.begin_stage('CT','V01','fit')
            with patch.object(c,'mark_unknown',wraps=c.mark_unknown) as mark:
                with self.assertRaises(m.Unknown):b.exchange(self.binding(),self.raw(),HEADERS,PRIVATE,90)
                self.assertTrue(b.halted);snapshot=c.snapshot()
                if role=='counter':
                    mark.assert_not_called();self.assertEqual(snapshot['admitted_requests'],0);self.assertIn('call',b.slots)
                elif role=='response_guard':
                    mark.assert_not_called();self.assertEqual(snapshot['pending_calls'],0);self.assertGreater(snapshot['exposure_microdollars'],0)
                    self.assertTrue((b.root/'call/terminal.json').exists());self.assertNotIn('call',b.slots)
                else:
                    self.assertEqual(mark.call_count,1);self.assertTrue(snapshot['halted']);self.assertEqual(snapshot['pending_calls'],1);self.assertIn('call',b.slots)
    def test_partial_write_flush_and_fsync_failures_retain_full_byte_reservation(self):
        original_fdopen=m.os.fdopen
        class FaultFile:
            def __init__(self,inner,fault):self.inner=inner;self.fault=fault
            def __enter__(self):return self
            def __exit__(self,*args):self.inner.close()
            def write(self,raw):
                if self.fault=='partial':self.inner.write(raw[:2]);raise OSError('synthetic partial')
                return self.inner.write(raw)
            def flush(self):
                self.inner.flush()
                if self.fault=='flush':raise OSError('synthetic flush')
            def fileno(self):return self.inner.fileno()
        for fault in ('partial','flush','fsync'):
            before=self.b.bytes;raw=b'0123456789';name='fault-'+fault+'.bin'
            def opened(fd,mode):return FaultFile(original_fdopen(fd,mode),fault)
            with patch.object(m.os,'fdopen',side_effect=opened):
                if fault=='fsync':
                    with patch.object(m.os,'fsync',side_effect=OSError('synthetic fsync')),self.assertRaises(OSError):self.b._write(None,name,raw)
                else:
                    with self.assertRaises(OSError):self.b._write(None,name,raw)
            self.assertEqual(self.b.bytes,before+len(raw));self.assertTrue((self.b.root/name).exists())
            self.assertEqual((self.b.root/name).stat().st_size,2 if fault=='partial' else len(raw))
    def test_partial_evidence_failure_cannot_leave_space_for_another_calls_bookkeeping(self):
        c=Campaign.create(self.root/'small-cap.jsonl',campaign_policy());p=broker_policy(c,self.adapters);p['evidence_limit_bytes']=16384
        b=m.Broker.create(self.root/'small-cap-broker',c,p,*self.adapters,reservation_calculator=request_reservation)
        intended=p['evidence_limit_bytes']-b.bytes-4;self.assertGreater(intended,10)
        original_fdopen=m.os.fdopen
        class Partial:
            def __init__(self,inner):self.inner=inner
            def __enter__(self):return self
            def __exit__(self,*args):self.inner.close()
            def write(self,raw):self.inner.write(raw[:2]);raise OSError('synthetic retained partial')
        with patch.object(m.os,'fdopen',side_effect=lambda fd,mode:Partial(original_fdopen(fd,mode))),self.assertRaises(OSError):b._write(None,'partial.bin',b'x'*intended)
        self.assertEqual(b.bytes,p['evidence_limit_bytes']-4);self.assertEqual((b.root/'partial.bin').stat().st_size,2)
        with self.assertRaises(m.Unknown):b._write(None,'another-call-terminal.json',b'12345')
        self.assertFalse((b.root/'another-call-terminal.json').exists());self.assertEqual(b.bytes,p['evidence_limit_bytes']-4)

if __name__=='__main__':unittest.main()
