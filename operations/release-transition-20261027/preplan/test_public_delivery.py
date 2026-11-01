"""PUBLIC stage orchestration; native file/boot/source IO seams explicitly synthetic."""
import unittest,pathlib,tempfile,copy,types,contextlib,json
from unittest.mock import patch
from contextlib import ExitStack
from datetime import datetime,timezone
import public_delivery as d
import pure_plan_contract as contract
import native_io as n
import test_allocator_regressions as fixtures
import test_profile_ingress_shared as profilefixtures
class Held(Exception):pass
class Tests(unittest.TestCase):
 def binding(self,profile,now,boots=None):
  return {'schema':'careertrellis.preplan-scheduler-binding/v1','nonce':'b'*64,'source_commit':profile['source_commit'],'boots':boots or {'web':'11111111-1111-1111-1111-111111111111','db':'22222222-2222-2222-2222-222222222222'},'issued_at':now.isoformat(),'phase':'preflight','mode':'idle','release':'7a4d3f077c4da21f2a315cbbcf4fd926d91151f4','build':'20261017.96'}
 def plan(self):
  case=fixtures.AssemblerTests();case.setUp();value=case.assemble();raw=n.canonical(value);profile={'source_commit':'c'*40,'packet_sha256':value['packet_sha256'],'readiness_manifest_sha256':value['readiness_manifest_sha256']};return value,raw,profile,case.now
 def test_closed15_public_plan_exact_binding_and_no_private_fields(self):
  plan,raw,profile,now=self.plan();self.assertEqual(d.plan_bytes(raw,n.sha(raw),profile,now),plan);self.assertNotIn('private_policy',str(plan));self.assertEqual(len(plan),15)
  for key in ('private_policy','validation_evidence','unknown'):
   bad=dict(plan);bad[key]='SYNTHETIC_PRIVATE';body=n.canonical(bad);self.assertRaises((n.Refused,contract.Refused),d.plan_bytes,body,n.sha(body),profile,now)
 def test_plan_wrong_hash_packet_receipt_diff_expiry_refuse(self):
  plan,raw,profile,now=self.plan();self.assertRaises(n.Refused,d.plan_bytes,raw,'f'*64,profile,now)
  for key in ('packet_sha256','readiness_manifest_sha256'):
   bad=dict(profile);bad[key]='f'*64;self.assertRaises(n.Refused,d.plan_bytes,raw,n.sha(raw),bad,now)
  for key in ('source_diffs','prepared_receipts'):
   bad=copy.deepcopy(plan);bad[key][d.TARGET]='f'*64;body=n.canonical(bad);self.assertRaises(n.Refused,d.plan_bytes,body,n.sha(body),profile,now)
  self.assertRaises((n.Refused,contract.Refused),d.plan_bytes,raw,n.sha(raw),profile,datetime(2030,1,1,tzinfo=timezone.utc))
 def test_actual_bundle_authenticates_web_git_not_capsule_only(self):
  raws,m,raw=profilefixtures.Tests().manifest_fixture();calls=[]
  class Git:
   def blob(self,commit,path):
    calls.append((commit,path))
    if commit==m['program_commit']:return raws[path[len('operations/release-transition-20261027/preplan/'):]]
    if commit=='504756d8b949163dbe1386e60be48cec0ec4117c':return raws['scanner-original-'+path.rsplit('/',1)[-1]]
    return raws['public-runtime35/'+path[len('operations/release-transition-20261026/'):]]
  # Existing reviewed source verifier, exact raw fixture domains; no real Git process.
  with patch.object(d,'GitObjects',Git):value,packet,public=d.bundle('web',raw,n.sha(raw),raws)
  self.assertEqual(len(public),35);self.assertTrue(any(commit==m['program_commit'] for commit,_ in calls));self.assertTrue(any(commit==m['runtime']['source_commit'] for commit,_ in calls))
  class Wrong:
   def blob(self,*a):return b'WRONG_PUBLIC_GIT'
  with patch.object(d,'GitObjects',Wrong):self.assertRaises(n.Refused,d.bundle,'web',raw,n.sha(raw),raws)
 def stage(self,existing=False,fail=None):
  calls=[]
  with tempfile.TemporaryDirectory(prefix='delivery-synthetic-') as root:
   base=pathlib.Path(root);remote=base/'packet';receipts=base/'receipts';receipts.mkdir();profile={'source_commit':'c'*40,'packet_sha256':'a'*64};state={'boot':'11111111-1111-1111-1111-111111111111','pointers':{'synthetic':True}};now=datetime(2000,1,1,tzinfo=timezone.utc);binding=self.binding(profile,now);state['binding_sha256']=n.sha(n.canonical(binding))
   class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return now
   if existing:remote.mkdir()
   def create(path,raw):
    calls.append(('create',path.name))
    if fail=='intent' and path.name.endswith('-intent.json'):raise OSError('SYNTHETIC_FSYNC')
    with path.open('xb') as stream:stream.write(raw)
   with ExitStack() as stack:
    for obj,key,value in [(d,'REMOTE',remote),(d,'datetime',Clock),(d,'RECEIPTS',receipts),(d,'bundle',lambda *a:({'runtime':profile},b'PUBLIC_PACKET',{'module.py':b'PUBLIC_SOURCE'})),(d,'preflight',lambda *a:state),(d,'create',create),(d,'ProtectedReader',lambda:types.SimpleNamespace(read=lambda p,*a:p.read_bytes(),stable=lambda:None)),(d,'boot',lambda:state['boot']),(d,'pointer_state',lambda:state['pointers']),(d,'process_identity',lambda _:dict(pid=123,parent_pid=2,birth='456',state='S')),(d,'deferred_child_signals',contextlib.nullcontext),(d,'hold',lambda:(_ for _ in ()).throw(Held())),(d.os,'statvfs',lambda _:types.SimpleNamespace(f_bavail=6*1024**3,f_frsize=1,f_favail=10000)),(d.os,'open',lambda *a:55),(d.os,'close',lambda *a:None),(d.os,'fsync',lambda *a:None),(d.os,'O_DIRECTORY',0),(d.os,'O_NOFOLLOW',0)]:stack.enter_context(patch.object(obj,key,value,create=True))
    if existing:self.assertRaises(n.Refused,d.stage_packet,'web',b'x','b'*64,{},binding);return calls
    if fail:self.assertRaises(Held,d.stage_packet,'web',b'x','b'*64,{},binding);self.assertFalse(remote.exists());return calls
    value=d.stage_packet('web',b'x','b'*64,{},binding);self.assertEqual(value['result'],'PUBLIC_PACKET_STAGED_ONLY');self.assertEqual(value['intent_sha256'],n.sha((receipts/calls[0][1]).read_bytes()));self.assertEqual((remote/'module.py').read_bytes(),b'PUBLIC_SOURCE');self.assertEqual((remote/'packet_manifest.json').read_bytes(),b'PUBLIC_PACKET');return calls
 def test_actual_stage_intent_precedes_namespace_and_readback_nooverwrite(self):
  calls=self.stage();self.assertTrue(calls[0][1].endswith('-intent.json'));self.assertEqual(self.stage(existing=True),[])
 def test_actual_stage_intent_failure_retains_no_namespace(self):
  calls=self.stage(fail='intent');self.assertEqual(len(calls),1)
 def test_normal_main_rejects_unrecognized_modes_before_source_or_writes(self):
  with patch.object(d,'stage_packet') as packet,patch.object(d,'stage_plan') as plan:self.assertRaises(n.Refused,d.main,'web','PRIVATE',b'x','a'*64,{},{});packet.assert_not_called();plan.assert_not_called()

 def test_actual_stage_plan_uses_closed_public_bytes_and_intent_then_success(self):
  plan,raw,profile,now=self.plan();events=[]
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return now
  with tempfile.TemporaryDirectory(prefix='delivery-synthetic-') as root:
   base=pathlib.Path(root);remote=base/'packet';remote.mkdir();receipts=base/'receipts';receipts.mkdir();state={'boot':plan['boots']['web'],'pointers':{'synthetic':True}};binding=self.binding(profile,now,plan['boots']);state['binding_sha256']=n.sha(n.canonical(binding))
   def create(path,body):
    events.append(path.name)
    with path.open('xb') as f:f.write(body)
   with ExitStack() as stack:
    for obj,key,value in [(d,'REMOTE',remote),(d,'datetime',Clock),(d,'RECEIPTS',receipts),(d,'bundle',lambda *a:({'runtime':profile},b'PACKET',{})),(d,'preflight',lambda *a:state),(d,'stored_packet',lambda *a:types.SimpleNamespace(stable=lambda:None)),(d,'create',create),(d,'ProtectedReader',lambda:types.SimpleNamespace(read=lambda p,*a:p.read_bytes(),stable=lambda:None)),(d,'boot',lambda:state['boot']),(d,'pointer_state',lambda:state['pointers']),(d,'process_identity',lambda _:dict(pid=123,parent_pid=2,birth='456',state='S')),(d,'deferred_child_signals',contextlib.nullcontext),(d,'hold',lambda:(_ for _ in ()).throw(Held())),(d,'datetime',Clock),(d.os,'statvfs',lambda _:types.SimpleNamespace(f_bavail=6*1024**3,f_frsize=1,f_favail=10000))]:stack.enter_context(patch.object(obj,key,value,create=True))
    result=d.stage_plan('web',b'x','a'*64,{}, binding,raw,n.sha(raw));self.assertEqual(result['result'],'PUBLIC_PLAN_STAGED_NOT_DISPATCHED');self.assertEqual(result['intent_sha256'],n.sha((receipts/events[0]).read_bytes()));self.assertEqual((remote/'plan.json').read_bytes(),raw);self.assertTrue(events[0].endswith('-intent.json'));self.assertEqual(events[1],'plan.json');self.assertTrue(events[-1].endswith('-success.json'))
    self.assertRaises(n.Refused,d.stage_plan,'web',b'x','a'*64,{}, binding,raw,n.sha(raw))

 def export_fixture(self):
  plan,raw,profile,now=self.plan();nonce='b'*64;program='a'*64;contextpin=n.sha(n.canonical({'schema':'careertrellis.app-plan-source-contract/v1','mode':'actual','source_commit':profile['source_commit'],'packet_sha256':profile['packet_sha256'],'readiness_manifest_sha256':profile['readiness_manifest_sha256'],'collector_source_sha256':program}));claim={'schema':'careertrellis.app-plan-allocation-claim/v1','context_sha256':contextpin,'private_facts_sha256':'f'*64};claimraw=n.canonical(claim);intent={'schema':'careertrellis.preplan-private-materialization/v1','nonce':nonce,'boot_id':plan['boots']['web'],'actor':{'pid':123,'parent_pid':2,'birth':'456','state':'S'},'program_sha256':program,'facts_sha256':'f'*64,'context_sha256':contextpin};ir=n.canonical(intent);decision={'schema':'careertrellis.app-plan-allocation-decision/v1','decision_at':now.isoformat(),'expires_at':plan['expires_at'],'plan_sha256':n.sha(raw),'claim_sha256':n.sha(claimraw),'context_sha256':contextpin};dr=n.canonical(decision);terminal={'schema':'careertrellis.preplan-private-materialization-terminal/v1','result':'PRIVATE_PLAN_ALLOCATED_NOT_DISPATCHED','nonce':nonce,'facts_sha256':'f'*64,'context_sha256':contextpin,'plan_sha256':n.sha(raw),'decision_sha256':n.sha(dr),'intent_sha256':n.sha(ir),'boot_id':plan['boots']['web'],'authorizes_dispatch':False};return [raw,dr,claimraw,ir,n.canonical(terminal),nonce,n.sha(raw),n.sha(dr),program,profile,plan['boots']['web'],now]
 def test_public_export_chain_returns_only_closed_plan_not_private_inputs(self):
  args=self.export_fixture();plan,actor=d.export_chain(*args);self.assertEqual(len(plan),15);self.assertNotIn('private_facts_sha256',plan);self.assertEqual(actor['pid'],123)
 def test_public_export_chain_wrong_intent_decision_or_boot_refuses(self):
  args=self.export_fixture()
  for index in (1,2,3,4):
   bad=list(args);body=json.loads(bad[index]);body['UNKNOWN_PRIVATE']='never-export';bad[index]=n.canonical(body);self.assertRaises(n.Refused,d.export_chain,*bad)
  bad=list(args);bad[10]='33333333-3333-3333-3333-333333333333';self.assertRaises(n.Refused,d.export_chain,*bad)
