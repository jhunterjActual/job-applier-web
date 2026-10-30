"""Synthetic closed-operation/actual producer-readback paths; no host/real records."""
import ast,copy,hashlib,json,re,stat,types,unittest,contextlib
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import sys
ROOT=Path(__file__).resolve().parent
OPERATOR=ROOT/'operator'
sys.path.insert(0,str(OPERATOR))
import carrier as c
import wire_builder as w
from test_operator_carrier import fixture,canonical,BOOT
import test_operator_carrier as carrier_fixture
P=ROOT;NOW=datetime(2025,1,1,tzinfo=timezone.utc)
class Held(BaseException):pass
class Memory:
 def __init__(self,write_mode='short'):
  self.rows={'/synthetic':('dir',b''),'/synthetic/receipts':('dir',b'')};self.fd={};self.seq=20;self.writes=0;self.write_mode=write_mode
 def path_class(self):
  fs=self
  class F(PurePosixPath):
   def lstat(self):
    kind,raw=fs.rows[str(self)];mode={'dir':stat.S_IFDIR|0o700,'file':stat.S_IFREG|0o600,'link':stat.S_IFLNK|0o777}[kind]
    return types.SimpleNamespace(st_dev=1,st_ino=abs(hash(str(self)))%100000+1,st_uid=0,st_gid=0,st_mode=mode,st_nlink=1,st_size=len(raw),st_mtime_ns=0,st_ctime_ns=0)
   def iterdir(self):
    prefix=str(self)+'/'
    return iter(F(path) for path in sorted(fs.rows) if path.startswith(prefix) and '/' not in path[len(prefix):])
  return F
 def api(self):
  fs=self
  def open_(path,flags,*args):
   key=str(path);fs.seq+=1
   if flags&4:
    if key in fs.rows:raise FileExistsError()
    fs.rows[key]=('file',b'')
   elif key not in fs.rows:raise FileNotFoundError()
   fs.fd[fs.seq]=key;return fs.seq
  def write(fd,raw):
   fs.writes+=1
   if fs.write_mode=='zero':return 0
   if fs.write_mode=='error' and fs.writes==2:raise OSError('SYNTHETIC_PRIVATE')
   count=min(7,len(raw));key=fs.fd[fd];fs.rows[key]=('file',fs.rows[key][1]+raw[:count]);return count
  return types.SimpleNamespace(O_WRONLY=1,O_CREAT=4,O_EXCL=8,O_NOFOLLOW=16,O_NONBLOCK=32,O_RDONLY=0,O_DIRECTORY=64,path=types.SimpleNamespace(lexists=lambda path:str(path) in fs.rows),getpid=lambda:4321,open=open_,write=write,close=lambda fd:None,fchmod=lambda *args:None,fsync=lambda *args:None,mkdir=lambda path,mode:fs.rows.__setitem__(str(path),('dir',b'')),statvfs=lambda p:types.SimpleNamespace(f_bavail=20*1024**3,f_frsize=1,f_favail=100000))
 def reader_class(self):
  fs=self
  class Reader:
   def __init__(self):self.seen={}
   def read(self,path,limit=2097152):
    key=str(path)
    if key not in fs.rows:raise FileNotFoundError()
    kind,raw=fs.rows[key];c.need(kind=='file' and len(raw)<=limit);old=self.seen.get(key);c.need(old is None or old==raw);self.seen[key]=raw;return raw
   def stable(self):c.need(all(fs.rows[key][1]==raw for key,raw in self.seen.items()))
  return Reader

def environment(write_mode='short'):
 memory=Memory(write_mode);F=memory.path_class();public={name:('public:'+name).encode() for name in c.PUBLIC_PATHS};packet=b'{"synthetic-public-packet":true}';profile={'source_commit':'d'*40,'packet_sha256':c.sha(packet),'readiness_manifest_sha256':'e'*64};actor={'pid':4321,'parent_pid':123,'birth':'77','state':'R'};active={'live':True,'pid':4321,'birth':'77'};guard=carrier_fixture.StageGuardTests().setup_source('success')[3]
 def process(pid):
  if active['live'] and pid==active['pid']:return dict(actor,pid=active['pid'],birth=active['birth'])
  raise FileNotFoundError()
 def identity(info):return tuple(getattr(info,k) for k in ('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
 ns={'os':memory.api(),'re':re,'Path':F,'ROOT':F('/synthetic'),'REMOTE':F('/synthetic/packet'),'RECEIPTS':F('/synthetic/receipts'),'need':c.need,'sha':c.sha,'canonical':canonical,'decode':json.loads,'bootstrap':lambda role:BOOT,'boot':lambda:BOOT,'process_identity':process,'ProtectedReader':memory.reader_class(),'parents':lambda path:None,'deferred_child_signals':contextlib.nullcontext,'hold':lambda:(_ for _ in ()).throw(Held()),'identity':identity,'pointer_state':lambda:{'current':'synthetic'},'bundle':lambda *args:({'runtime':profile},packet,public),'preflight':guard.preflight,'binding_current':guard.binding_current,'datetime':types.SimpleNamespace(now=lambda zone:NOW),'timezone':timezone}
 ns['os'].getpid=lambda:active['pid']
 # Exact published pure schema validator, no clock/global replacement in production.
 contract={n for n in ('BASE','PREVIOUS','TARGET','CORRECTION','ROLES','BUILDS','POLICY_KEYS')};tree=ast.parse((ROOT/'pure_plan_contract.py').read_bytes());nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in contract for t in n.targets) or isinstance(n,ast.FunctionDef) and n.name in ('timestamp','validate_plan')];space={'need':c.need,'re':re,'datetime':datetime,'timezone':timezone};exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual-pure-plan-contract','exec'),space);ns.update({k:space[k] for k in ('TARGET','CORRECTION','validate_plan')})
 history={}
 for n in ast.parse((ROOT/'preplan_history.py').read_bytes()).body:
  if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant):
   for target in n.targets:
    if isinstance(target,ast.Name):history[target.id]=n.value.value
 tree=ast.parse((ROOT/'preplan_program.py').read_bytes());constants=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('HISTORY','DIFFS') for t in n.targets)];config={'history':types.SimpleNamespace(**history),'TARGET':space['TARGET'],'CORRECTION':space['CORRECTION']};exec(compile(ast.Module(body=constants,type_ignores=[]),'actual-public-contract-constants','exec'),config);ns.update({k:config[k] for k in ('HISTORY','DIFFS')})
 tree=ast.parse((ROOT/'public_delivery.py').read_bytes());names={'plan_bytes','stored_packet','stage_packet','stage_plan','created_intent_sha','packet_inventory','terminal_actor','packet_records','readback_packet','readback_plan'};nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names];exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual-public-delivery','exec'),ns)
 fn=next(n for n in ast.parse((ROOT/'package_bootstrap.py').read_bytes()).body if isinstance(n,ast.FunctionDef) and n.name=='create');exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-exclusive-complete-create','exec'),ns)
 plan={'schema':'careertrellis.app-policy-release/v2','base':space['BASE'],'previous':space['PREVIOUS'],'target':space['TARGET'],'correction':space['CORRECTION'],'builds':{**space['BUILDS'],space['CORRECTION']:'20261026.98'},'boots':{'web':BOOT,'db':'22222222-2222-2222-2222-222222222222'},'old_policy_digests':{'web':'1'*64,'db':'2'*64},'new_policy_digests':{revision:{'web':'3'*64,'db':'4'*64} for revision in (space['TARGET'],space['CORRECTION'])},'packet_sha256':profile['packet_sha256'],'source_diffs':config['DIFFS'],'prepared_receipts':{space['TARGET']:config['HISTORY']['corrected_acceptance'],space['CORRECTION']:config['HISTORY']['legacy_acceptance']},'prepared_receipt_kinds':{space['TARGET']:'corrected-fixture97-new/v1',space['CORRECTION']:'repair98-retained/v1'},'expires_at':(NOW+timedelta(minutes=30)).isoformat(),'readiness_manifest_sha256':profile['readiness_manifest_sha256']}
 binding=fixture()[3];binding['nonce']='2'*64;return ns,memory,profile,binding,plan,active

class OperationTests(unittest.TestCase):
 def test_exact_eight_modes_and_unknown_callable_private_fields_refuse(self):
  for mode in ('preflight','stage','readback','stage-packet','readback-packet','export-plan','stage-plan','readback-plan'):self.assertIn(mode,c.selector('web',mode,'a'*64))
  for mode in ('allocate','drive','__import__','stage;echo'):
   with self.assertRaises(c.Refused):c.selector('web',mode,'a'*64)
  for operation in ({'private_facts':b'PRIVATE'},{'method':'stage_plan'},{'path':'/private'}):
   with self.assertRaises(c.Refused):c.validate_operation('stage-packet',operation)
  with self.assertRaises(c.Refused):c.selector('db','export-plan','a'*64)
 def test_closed15_hash_schema_and_extra_payload(self):
  ns,fs,profile,binding,plan,active=environment();raw=canonical(plan);op={'fresh_binding_raw':canonical(binding),'fresh_binding_sha256':c.sha(canonical(binding)),'plan_raw':raw,'approved_plan_sha256':c.sha(raw)};self.assertEqual(c.validate_operation('stage-plan',op),op)
  for key in ('private_policy','validation_evidence','unknown'):
   bad=dict(plan);bad[key]='PRIVATE';body=canonical(bad)
   with self.assertRaises(c.Refused):c.validate_operation('stage-plan',{**op,'plan_raw':body,'approved_plan_sha256':c.sha(body)})
  with self.assertRaises(c.Refused):c.validate_operation('stage-plan',{**op,'approved_plan_sha256':'f'*64})
  with self.assertRaises(c.Refused):c.validate_operation('stage-plan',{**op,'payload':b'PRIVATE'})
 def test_actual_packet_plan_intent_ack_independent_readback_handoff(self):
  ns,fs,profile,binding,plan,active=environment();packet=ns['stage_packet']('web',b'{}','a'*64,{},binding);self.assertEqual(packet['files'],36);packet_intent='/synthetic/receipts/app-public-packet-'+profile['packet_sha256']+'-web-intent.json';self.assertEqual(packet['intent_sha256'],c.sha(fs.rows[packet_intent][1]));active['live']=False
  checked=ns['readback_packet']('web',b'{}','a'*64,{},packet['intent_sha256']);self.assertEqual(checked['intent_sha256'],packet['intent_sha256']);self.assertFalse(checked['authorizes_dispatch'])
  with self.assertRaises(c.Refused):ns['readback_packet']('web',b'{}','a'*64,{},'9'*64)
  active.update(live=True,pid=5432,birth='88');raw=canonical(plan);result=ns['stage_plan']('web',b'{}','a'*64,{},binding,raw,c.sha(raw));active['live']=False
  checked=ns['readback_plan']('web',b'{}','a'*64,{},c.sha(raw),result['intent_sha256']);self.assertEqual(checked['intent_sha256'],result['intent_sha256']);self.assertFalse(checked['authorizes_dispatch'])
 def test_original_packet_actor_live_unknown_leaf_link_and_expiry_refuse(self):
  for kind in ('live','extra','link','expired'):
   ns,fs,profile,binding,plan,active=environment();result=ns['stage_packet']('web',b'{}','a'*64,{},binding);active['live']=kind=='live'
   if kind=='extra':fs.rows['/synthetic/packet/unknown']=('file',b'PRIVATE')
   if kind=='link':fs.rows['/synthetic/packet/native_peer.py']=('link',b'PRIVATE')
   if kind=='expired':
    with self.assertRaises(RuntimeError):ns['plan_bytes'](canonical(plan),c.sha(canonical(plan)),profile,NOW+timedelta(hours=1))
   else:
    with self.subTest(kind=kind),self.assertRaises(c.Refused):ns['readback_packet']('web',b'{}','a'*64,{},result['intent_sha256'])
 def test_actual_create_shortwrite_zero_midframe_unknown_no_success(self):
  for mode in ('short','zero','error'):
   ns,fs,profile,binding,plan,active=environment(mode)
   if mode=='short':self.assertIn('intent_sha256',ns['stage_packet']('web',b'{}','a'*64,{},binding));self.assertGreater(fs.writes,36)
   else:
    with self.assertRaises(Held):ns['stage_packet']('web',b'{}','a'*64,{},binding)
    self.assertFalse(any(name.endswith('-success.json') for name in fs.rows))
 def test_actual_packet_fresh_action_stale_refuses_before_any_write(self):
  ns,fs,profile,binding,plan,active=environment();binding['issued_at']=(NOW-timedelta(seconds=31)).isoformat()
  with self.assertRaises(c.Refused):ns['stage_packet']('web',b'{}','a'*64,{},binding)
  self.assertEqual(fs.writes,0);self.assertFalse(any(name.endswith('-intent.json') for name in fs.rows))
 def test_original_cache_binding_can_be_expired_structurally_no_live_clock(self):
  raw,digest,caps,binding=fixture();body=canonical(binding);self.assertEqual(c.stage_binding(body,c.sha(body),{'source_commit':'d'*40},'web',BOOT),binding)
  with self.assertRaises(c.Refused):c.public_operation('web','stage-packet',BOOT,digest,raw,caps,binding,c.sha(body),{'public_delivery':types.SimpleNamespace(main=lambda *a:(_ for _ in ()).throw(AssertionError('NO_DISPATCH'))),'native_io':types.SimpleNamespace()}, {'fresh_binding_raw':body,'fresh_binding_sha256':c.sha(body)})
class CarrierPipelineTests(unittest.TestCase):
 def main_environment(self,scenario):
  raw,digest,caps,binding=fixture();body=canonical(binding);pin=c.sha(body);record=canonical({'boot_id':BOOT,'binding_sha256':pin});calls=[]
  class Reader:
   def read(self,*args):return record
   def stable(self):calls.append('source-reader-stable')
  def staged(*args):
   calls.append('original-source-actor-absence')
   if scenario=='unknown-old-source':raise c.Refused('synthetic unapproved source')
   return PurePosixPath('/synthetic/source'),caps
  io=types.SimpleNamespace(bootstrap=lambda role:BOOT,boot=lambda:BOOT,ProtectedReader=Reader,decode=json.loads,sha=c.sha,GitObjects=lambda:types.SimpleNamespace(blob=lambda *a:b''))
  package=types.SimpleNamespace(RECEIPTS=PurePosixPath('/synthetic/receipts'),load_staged=staged,verify_sources=lambda *args:calls.append('full-source-proof'))
  modules={'native_io':io,'package_bootstrap':package,'public_delivery':types.SimpleNamespace(binding_current=lambda *a:(_ for _ in ()).throw(AssertionError('NO_LIVE_CACHE_CLOCK')))}
  namespace=dict(c.__dict__);namespace.update({'sys':types.SimpleNamespace(platform='linux',flags=types.SimpleNamespace(isolated=1,no_site=1,dont_write_bytecode=1),argv=['-',c.selector('web','readback',digest)]),'os':types.SimpleNamespace(geteuid=lambda:0),'socket':types.SimpleNamespace(gethostname=lambda:c.HOSTS['web']),'load_program':lambda *args:(json.loads(raw),caps,modules),'public_operation':lambda *args:(_ for _ in ()).throw(AssertionError('NO_DELIVERY_DISPATCH'))})
  node=next(n for n in ast.parse((OPERATOR/'carrier.py').read_bytes()).body if isinstance(n,ast.FunctionDef) and n.name=='main');exec(compile(ast.Module(body=[node],type_ignores=[]),'actual-carrier-main-isolated-host-seams','exec'),namespace)
  return namespace['main'],(raw,digest,caps,body,pin,c.sha(record)),calls
 def test_actual_main_expired_historical_source_binding_positive(self):
  main,args,calls=self.main_environment('known');raw,digest,caps,body,pin,intent=args;result=main('web','readback',BOOT,digest,raw,caps,body,pin,intent)
  self.assertEqual(result['result'],'public_source_readback_only');self.assertTrue(result['original_stage_actor_absent']);self.assertEqual(calls,['full-source-proof','original-source-actor-absence','source-reader-stable']);self.assertEqual(json.loads(body)['issued_at'],'2025-01-01T00:00:00+00:00')
 def test_unknown_original_source_never_dispatches_delivery(self):
  main,args,calls=self.main_environment('unknown-old-source');raw,digest,caps,body,pin,intent=args
  with self.assertRaises(c.Refused):main('web','readback',BOOT,digest,raw,caps,body,pin,intent)
  self.assertEqual(calls,['full-source-proof','original-source-actor-absence'])
 def test_actual_builder_all_eight_fixed_modes_source_only(self):
  raw,digest,caps,binding=fixture();body=canonical(binding);fresh=dict(binding,nonce='2'*64);fresh_raw=canonical(fresh);source=(OPERATOR/'carrier.py').read_bytes();_,_,profile,_,plan,_=environment();plan_raw=canonical(plan)
  operations={'preflight':None,'stage':None,'readback':None,'stage-packet':{'fresh_binding_raw':fresh_raw,'fresh_binding_sha256':c.sha(fresh_raw)},'readback-packet':{'approved_packet_intent_sha256':'f'*64},'export-plan':{'nonce':'3'*64,'approved_plan_sha256':c.sha(plan_raw),'approved_decision_sha256':'e'*64},'stage-plan':{'fresh_binding_raw':fresh_raw,'fresh_binding_sha256':c.sha(fresh_raw),'plan_raw':plan_raw,'approved_plan_sha256':c.sha(plan_raw)},'readback-plan':{'approved_plan_sha256':c.sha(plan_raw),'approved_plan_intent_sha256':'f'*64}}
  for mode,operation in operations.items():
   result=w.build(source,c.sha(source),'web',mode,BOOT,raw,digest,caps,body,c.sha(body),None if mode in ('preflight','stage') else 'f'*64,operation=operation);ast.parse(result['wire']);self.assertLessEqual(result['wire_bytes'],1048576);self.assertEqual(result['selector'],c.selector('web',mode,digest))
if __name__=='__main__':unittest.main()
