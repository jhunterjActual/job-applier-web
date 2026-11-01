"""Synthetic source-only fixtures; no actual manifest, boot, clock or host."""
import ast,base64,contextlib,hashlib,json,sys,types,unittest
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
OPERATOR=ROOT/'operator'
sys.path.insert(0,str(OPERATOR))
import carrier as c
import wire_builder as w
P=ROOT
BOOT='11111111-1111-1111-1111-111111111111'
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def fixture():
 caps={name:b'pass' for name in c.FILES};m={'schema':'careertrellis.preplan-source-package/v1','program_commit':'c'*40,'prefix':'operations/release-transition-20261027/preplan/','runtime':{'source_commit':'d'*40},'files':{n:{'sha256':c.sha(raw),'bytes':len(raw)} for n,raw in caps.items()}};raw=canonical(m);binding={'schema':'careertrellis.preplan-scheduler-binding/v1','nonce':'1'*64,'source_commit':'d'*40,'boots':{'web':BOOT,'db':'22222222-2222-2222-2222-222222222222'},'issued_at':'2025-01-01T00:00:00+00:00','phase':'preflight','mode':'idle','release':'7a4d3f077c4da21f2a315cbbcf4fd926d91151f4','build':'20261017.96'};return raw,c.sha(raw),caps,binding
class CacheTests(unittest.TestCase):
 def test_exact55_all_cache_before_program_compile(self):
  raw,digest,caps,binding=fixture();self.assertEqual(len(c.validate_cache(raw,digest,caps)[1]),55)
  bad=dict(caps);bad['native_io.py']=b'bad'
  with patch('builtins.compile',side_effect=AssertionError('NO_COMPILE')):
   with self.assertRaises(c.Refused):c.load_program(raw,digest,bad)
 def test_missing_extra_content_size_and_closed_manifest_refuse(self):
  raw,digest,caps,binding=fixture()
  for kind in ('missing','extra','content','size','schema'):
   bad=dict(caps);m=json.loads(raw)
   if kind=='missing':bad.pop('native_io.py')
   elif kind=='extra':bad['../escape.py']=b'pass'
   elif kind=='content':bad['native_io.py']=b'wrong'
   elif kind=='size':m['files']['native_io.py']['bytes']=True
   else:m['extra']='unapproved'
   altered=canonical(m)
   with self.subTest(kind=kind),self.assertRaises(c.Refused):c.validate_cache(altered,c.sha(altered),bad)
 def test_scalar_hooks_refused_before_repr_or_equality(self):
  class Bad(str):
   def __eq__(self,other):raise AssertionError('EQ_HOOK')
   def __repr__(self):raise AssertionError('REPR_HOOK')
   __hash__=str.__hash__
  raw,digest,caps,binding=fixture()
  with self.assertRaises(c.Refused):c.selector(Bad('web'),'stage',digest)
  bad=dict(caps);bad[Bad('native_io.py')]=bad.pop('native_io.py')
  with self.assertRaises(c.Refused):c.validate_cache(raw,digest,bad)
 def test_binding_structural_historical_not_current_clock(self):
  raw,digest,caps,binding=fixture();body=canonical(binding);self.assertEqual(c.stage_binding(body,c.sha(body),{'source_commit':'d'*40},'web',BOOT),binding)
  for field,value in [('boots',{'web':'3'*36,'db':BOOT}),('mode','owned-stopped'),('source_commit','e'*40),('issued_at','invalid')]:
   bad=dict(binding);bad[field]=value;body=canonical(bad)
   with self.assertRaises((c.Refused,ValueError)):c.stage_binding(body,c.sha(body),{'source_commit':'d'*40},'web',BOOT)
 def test_builder_actual_representation_bound_no_wire_execution(self):
  raw,digest,caps,binding=fixture();source=(OPERATOR/'carrier.py').read_bytes();binding_raw=canonical(binding)
  result=w.build(source,c.sha(source),'web','stage',BOOT,raw,digest,caps,binding_raw,c.sha(binding_raw));self.assertLessEqual(result['wire_bytes'],1048576);ast.parse(result['wire']);self.assertEqual(result['selector'],c.selector('web','stage',digest));self.assertNotIn(b'sys.argv=',result['wire']);self.assertNotIn(b'__file__=',result['wire'])
 def test_builder_unknown_source_and_readback_without_actual_intent_refuse(self):
  raw,digest,caps,binding=fixture();source=(OPERATOR/'carrier.py').read_bytes();body=canonical(binding)
  with self.assertRaises(ValueError):w.build(source+b'\n',c.sha(source),'web','stage',BOOT,raw,digest,caps,body,c.sha(body))
  with self.assertRaises(RuntimeError):w.build(source,c.sha(source),'web','readback',BOOT,raw,digest,caps,body,c.sha(body))
class StageGuardTests(unittest.TestCase):
 def setup_source(self,scenario):
  raw=(ROOT/'package_bootstrap.py').read_bytes();tree=ast.parse(raw);fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='stage_public');events=[];clock={'at':datetime(2025,1,1,tzinfo=timezone.utc),'boot':BOOT};binding=fixture()[3]
  class Clock:
   @staticmethod
   def now(zone):return clock['at']
   @staticmethod
   def fromisoformat(value):return datetime.fromisoformat(value)
  def need(value):
   if not value:raise c.Refused('SYNTHETIC_REFUSED')
  profile={'source_commit':'d'*40,'readiness_manifest_sha256':'e'*64}
  def verify(*args):
   events.append('git-verify')
   if scenario=='slow-git':clock['at']+=timedelta(seconds=31)
   if scenario=='reboot':clock['boot']='33333333-3333-3333-3333-333333333333'
   return {'runtime':profile,'program_commit':'c'*40}
  class Reader:
   def stable(self):
    events.append('stable')
    if scenario=='slow-reader':clock['at']+=timedelta(seconds=31)
  def custody(*args):
   events.append('custody')
   if scenario=='binding-drift':binding['nonce']='9'*64
  collector=types.SimpleNamespace(Reader=Reader,installed493=lambda r:{},native_custody=custody,authority=lambda *a:events.append('authority'))
  @contextlib.contextmanager
  def readonly(public):yield collector
  delivery=types.ModuleType('public_delivery');ns={'need':need,'re':__import__('re'),'datetime':Clock,'timezone':timezone,'boot':lambda:clock['boot'],'sha':c.sha,'canonical':canonical,'bootstrap':lambda role:clock['boot'],'clear_kernel_locks':lambda role:events.append('locks'),'readonly_modules':readonly,'pointer_state':lambda:{'current':'synthetic'}}
  old=(ROOT/'public_delivery.py').read_bytes();nodes=[n for n in ast.parse(old).body if isinstance(n,ast.FunctionDef) and n.name in ('binding_current','preflight')];exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual-fixed-preclaim-guards','exec'),ns);delivery.preflight=ns['preflight'];delivery.binding_current=ns['binding_current']
  osfake=types.SimpleNamespace(O_RDONLY=0,O_DIRECTORY=1,O_NOFOLLOW=2,statvfs=lambda p:types.SimpleNamespace(f_bavail=20*1024**3,f_frsize=1,f_favail=100000),path=types.SimpleNamespace(lexists=lambda p:False),getpid=lambda:4321,mkdir=lambda *a:events.append('mkdir'),open=lambda *a:9,fsync=lambda *a:None,close=lambda *a:None)
  def create(path,raw):events.append(('create',str(path),json.loads(raw) if raw.startswith(b'{') else None))
  scope={'ROOT':PurePosixPath('/synthetic/root'),'RECEIPTS':PurePosixPath('/synthetic/receipts'),'bootstrap':lambda role:BOOT,'verify_sources':verify,'GitObjects':lambda:types.SimpleNamespace(blob=lambda *a:b''),'parents':lambda p:None,'os':osfake,'need':need,'process_identity':lambda p:{'pid':p,'parent_pid':123,'birth':'77','state':'R'},'deferred_child_signals':contextlib.nullcontext,'create':create,'canonical':canonical,'sha':c.sha,'boot':lambda:clock['boot'],'hold':lambda:(_ for _ in ()).throw(AssertionError('UNEXPECTED_HOLD'))}
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-stage-public','exec'),scope);return scope['stage_public'],events,binding,delivery
 def test_actual_stage_success_fixed_guard_before_first_intent_binding_persisted(self):
  fn,events,binding,delivery=self.setup_source('success')
  with patch.dict(sys.modules,{'public_delivery':delivery}):result=fn('web',b'{}','a'*64,{'x.py':b'pass'},binding)
  self.assertEqual(result['result'],'public_source_staged_only');first=next(i for i,item in enumerate(events) if isinstance(item,tuple));self.assertLess(events.index('authority'),first);self.assertLess(events.index('stable'),first);self.assertEqual(events[first][2]['binding_sha256'],c.sha(canonical(binding)))
 def test_actual_stage_slow_git_reboot_binding_drift_and_slow_reads_zero_create(self):
  for scenario in ('slow-git','reboot','binding-drift','slow-reader'):
   fn,events,binding,delivery=self.setup_source(scenario)
   with self.subTest(scenario=scenario),patch.dict(sys.modules,{'public_delivery':delivery}),self.assertRaises(c.Refused):fn('web',b'{}','a'*64,{'x.py':b'pass'},binding)
   self.assertFalse(any(isinstance(item,tuple) for item in events));self.assertNotIn('mkdir',events)
 def test_fifth_stage_argument_mandatory(self):
  fn,*_=self.setup_source('success')
  with self.assertRaises(TypeError):fn('web',b'{}','a'*64,{'x.py':b'pass'})
class ReadbackTests(unittest.TestCase):
 def actual(self,scenario):
  tree=ast.parse((ROOT/'package_bootstrap.py').read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='load_staged');events=[]
  record={'schema':'careertrellis.preplan-public-stage-intent/v1','manifest_sha256':'a'*64,'program_commit':'c'*40,'boot_id':BOOT,'role':'web','actor':{'pid':4321,'parent_pid':123,'birth':'77','state':'R'},'binding_sha256':'b'*64}
  if scenario=='old-shape':record.pop('binding_sha256')
  ir=canonical(record);success=dict(record,schema='careertrellis.preplan-public-stage-success/v1',intent_sha256=c.sha(ir),file_count=1)
  class Reader:
   def read(self,path,limit):
    name=path.name
    if name=='source-manifest.json':return b'{}'
    if name=='x.py':return b'pass'
    if name.endswith('-intent.json'):return ir
    if name.endswith('-success.json'):return canonical(success)
    raise AssertionError('unknown fixed path')
   def stable(self):
    events.append('stable')
    if scenario=='drift':raise c.Refused('synthetic drift')
  def process(pid):
   events.append('birth-check')
   if scenario=='live':return {'birth':'77'}
   if scenario=='reused-pid':return {'birth':'78'}
   raise FileNotFoundError()
  scope={'bootstrap':lambda role:BOOT,'need':c.need,'re':__import__('re'),'ROOT':PurePosixPath('/synthetic/root'),'RECEIPTS':PurePosixPath('/synthetic/receipts'),'ProtectedReader':Reader,'source_manifest':lambda *a:{'files':{'x.py':{}},'program_commit':'c'*40},'verify_sources':lambda *a:events.append('source-auth'),'decode':json.loads,'sha':c.sha,'boot':lambda:BOOT,'process_identity':process}
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-load-staged','exec'),scope);return scope['load_staged'],events
 def test_independent_readback_absent_original_birth_and_reused_pid(self):
  for scenario in ('absent','reused-pid'):
   fn,events=self.actual(scenario);root,caps=fn('web','a'*64);self.assertEqual(caps,{'x.py':b'pass'});self.assertEqual(events,['source-auth','birth-check','stable'])
 def test_live_original_birth_refuses(self):
  fn,events=self.actual('live')
  with self.assertRaises(c.Refused):fn('web','a'*64)
 def test_old_schema_or_stability_failure_never_returns_success(self):
  for scenario in ('old-shape','drift'):
   fn,events=self.actual(scenario)
   with self.subTest(scenario=scenario),self.assertRaises(c.Refused):fn('web','a'*64)
if __name__=='__main__':unittest.main()
