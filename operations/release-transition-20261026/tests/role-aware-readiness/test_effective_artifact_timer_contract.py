"""Synthetic source-shaped contracts; no native/systemd/host or private facts."""
import ast,copy,hashlib,importlib.util,json,sys,types,unittest
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
from unittest.mock import patch
import portable_bootstrap as pb
import collector as c
import pair_boundary as pair
HERE=pb.ROOT/'one_shot_readiness'
RAW=(Path(__file__).parent/'source-catalog493/runtime.py').read_bytes()
DATA={'application_release':'a'*40,'ops_release':'b'*40,'application_build':'20261028.97','max_capture_objects':128,'max_capture_bytes':33554432}
GOOD=b'a(stt) 2 "OnBootUSec" 30000000 30000000 "OnUnitActiveUSec" 60000000 90000000\n'
class SourceReader:
 def __init__(self,raw=RAW):self.raw=raw;self.calls=[]
 def read(self,path,mode):self.calls.append((str(path),mode));return self.raw
def configured(data,role):
 # Independently compile original assignment statements preceding configure,
 # including its original full constant namespace; never imports/classes/actions.
 tree=ast.parse(RAW);nodes=[]
 for n in tree.body:
  if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id in {'RELEASE','OPS','BUILD','BASE','HELPER','CORE','PINS','STATE_ROOT','MAX_CAPTURE_OBJECTS','MAX_CAPTURE_BYTES','_BASE_PINS','_BASE_OPS'}:nodes.append(n)
  elif isinstance(n,ast.FunctionDef) and n.name=='configure':nodes.append(n);break
 scope={'Path':PurePosixPath,'require':lambda v,*_:c.need(v)}
 exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual493-configure-test','exec'),scope)
 scope['configure'](types.SimpleNamespace(data=data),role);return scope['PINS']
class ArtifactTests(unittest.TestCase):
 def test_actual_configure_default_both_roles_complete(self):
  for role in ('web','db'):
   with self.subTest(role=role):
    reader=SourceReader();actual=c.effective_artifact_map(reader,types.SimpleNamespace(data=copy.deepcopy(DATA)),role)
    self.assertEqual(actual,configured(DATA,role));self.assertEqual(set(actual),{'web','db'});self.assertEqual({k:len(v) for k,v in actual.items()},{'web':5,'db':6})
    self.assertEqual(reader.calls,[(str(c.INSTALL/'runtime.py'),0o644)])
    self.assertIn('/opt/careertrellis/staging/releases/'+'b'*40+'/backend/admission_operations.py',actual['db'])
    self.assertEqual(actual['web']['/usr/local/libexec/careertrellis/backup_health.py'],'e143adbeea83df31d4fc4b5c1b25f0f4972dad7dbeef1b427688ca09e468ee91')
 def test_partial_known_override_keeps_full_membership(self):
  base=configured(DATA,'web')
  for role in ('web','db'):
   path=next(iter(base[role]));data=copy.deepcopy(DATA);data['artifact_hashes']={role:{path:'c'*64}}
   actual=c.effective_artifact_map(SourceReader(),types.SimpleNamespace(data=data),role)
   self.assertEqual(actual,configured(data,role));self.assertEqual(set(actual[role]),set(base[role]));self.assertEqual(actual[role][path],'c'*64)
 def test_empty_overrides_are_not_empty_maps(self):
  for overrides in ({},{'web':{}},{'db':{}},{'web':{},'db':{}}):
   data=dict(DATA,artifact_hashes=overrides);actual=c.effective_artifact_map(SourceReader(),types.SimpleNamespace(data=data),'db')
   self.assertEqual(len(actual['db']),6);self.assertEqual(len(actual['web']),5)
 def test_source_role_path_pin_failures(self):
  with self.assertRaises(c.Refused):c.effective_artifact_map(SourceReader(RAW+b'\n'),types.SimpleNamespace(data=DATA),'web')
  for role in ('other',None,True):
   with self.subTest(role=role),self.assertRaises(c.Refused):c.effective_artifact_map(SourceReader(),types.SimpleNamespace(data=DATA),role)
  for overrides in ({'other':{}},{'web':{'/foreign':'d'*64}},{'db':{'/foreign':'d'*64}},{'web':{'/usr/local/sbin/careertrellis-authority':'bad'}},{'web':[]}):
   with self.subTest(overrides=overrides),self.assertRaises(c.Refused):c.effective_artifact_map(SourceReader(),types.SimpleNamespace(data=dict(DATA,artifact_hashes=overrides)),'web')
 def test_all_selected_leaf_reads_modes_and_every_drift(self):
  # Explicit synthetic map seam; real raw hashing and exact read loop exercised.
  leaves={'/usr/local/sbin/synthetic':b'executable','/usr/local/libexec/synthetic.py':b'module','/opt/synthetic.py':b'ops'}
  mapping={k:hashlib.sha256(v).hexdigest() for k,v in leaves.items()}
  for role in ('web','db'):
   class Reader:
    def __init__(self,bad=None):self.calls=[];self.bad=bad
    def read(self,path,mode):self.calls.append((path,mode));return b'changed' if path==self.bad else leaves[path]
   with patch.object(c,'effective_artifact_map',return_value={role:mapping}):
    r=Reader();self.assertEqual(c.verify_effective_artifacts(r,None,role),mapping);self.assertEqual(r.calls,[(p,0o755 if p.startswith('/usr/local/sbin/') else 0o644) for p in leaves])
    for bad in leaves:
     with self.subTest(role=role,bad=bad),self.assertRaises(c.Refused):c.verify_effective_artifacts(Reader(bad),None,role)
 def test_all_custody_phases_guard_before_branch(self):
  tree=ast.parse((HERE/'collector.py').read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='native_custody')
  guard=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and getattr(n.value.func,'id',None)=='verify_effective_artifacts')
  branch=next(i for i,n in enumerate(fn.body) if isinstance(n,ast.If))
  self.assertLess(guard,branch)
  # Actual method reaches artifact refusal before any lock/transaction observation.
  for role in ('web','db'):
   for phase in ('preflight','stopped','verifying','final'):
    class Sentinel(Exception):pass
    common=types.SimpleNamespace(Policy=lambda data:types.SimpleNamespace(data=data))
    reader=types.SimpleNamespace(read=lambda *args:c.canonical(DATA))
    with patch.object(c,'module',return_value=common),patch.object(c,'verify_effective_artifacts',side_effect=Sentinel) as called:
     with self.assertRaises(Sentinel):c.native_custody(reader,role,{'phase':phase},{'common.py':b'fixed'})
     self.assertEqual(called.call_count,1)
class TimerTests(unittest.TestCase):
 def test_typed_format_and_reversed_order(self):
  value=c.parse_monotonic_timers(GOOD);self.assertEqual(len(value),2);self.assertEqual(value[0],{'base':'OnBootUSec','offset_usec':30000000,'next_elapse_usec':30000000})
  reverse=b'a(stt) 2 "OnUnitActiveUSec" 60000000 90000000 "OnBootUSec" 30000000 30000000'
  self.assertEqual(c.parse_monotonic_timers(reverse),value)
 def test_bad_formats_duplicates_units_and_uint64(self):
  bad=[GOOD.replace(b'a(stt)',b'a(st)'),GOOD.replace(b' 2 ',b' 1 ',1),GOOD.replace(b'OnUnitActiveUSec',b'OnBootUSec'),GOOD.replace(b'OnBootUSec',b'OnBootSec'),GOOD.replace(b'30000000',b'30',1),GOOD.replace(b'60000000',b'60000001',1),GOOD.replace(b'90000000',b'-1'),GOOD.replace(b'90000000',str(2**64).encode()),GOOD+b'extra',b'\xff',GOOD.decode(),True]
  for raw in bad:
   with self.subTest(raw=raw),self.assertRaises(c.Refused):c.parse_monotonic_timers(raw)
 def test_closed_rows_bool_float_extra_missing(self):
  value=c.parse_monotonic_timers(GOOD)
  for field,v in [('offset_usec',True),('offset_usec',30000000.0),('next_elapse_usec',True),('next_elapse_usec',-1),('base','OnActiveUSec'),('extra',0)]:
   bad=copy.deepcopy(value);bad[0][field]=v
   with self.subTest(field=field,v=v),self.assertRaises(c.Refused):c.validate_monotonic_timers(bad)
  for bad in (value[:1],value+value[:1],tuple(value)):
   with self.assertRaises(c.Refused):c.validate_monotonic_timers(bad)
 def test_actual_dbus_command_and_reader_stability(self):
  with patch.object(c,'run',return_value=GOOD) as run:
   reader=c.Reader();value=reader.timer_monotonic('careertrellis-paired-recovery.timer');reader.stable()
   self.assertEqual(run.call_count,2);self.assertEqual(run.call_args.args[0][-1],'TimersMonotonic');self.assertEqual(run.call_args.kwargs,{'maximum':1024})
  with patch.object(c,'timer_monotonic',side_effect=[value,copy.deepcopy(value)]) as query:
   reader=c.Reader();reader.timer_monotonic('careertrellis-paired-recovery.timer');reader.stable();self.assertEqual(query.call_count,2)
  changed=copy.deepcopy(value);changed[0]['next_elapse_usec']+=1
  with patch.object(c,'timer_monotonic',side_effect=[value,changed]):
   reader=c.Reader();reader.timer_monotonic('careertrellis-paired-recovery.timer')
   with self.assertRaises(c.Refused):reader.stable()
 def test_pair_typed_coverage_required_and_tick_guard(self):
  now=datetime(2026,10,28,12,tzinfo=timezone.utc);local=now.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'));candidate=local.replace(hour=3,minute=0,second=0,microsecond=0)+__import__('datetime').timedelta(days=(6-local.weekday())%7)
  if candidate<=local:candidate+=__import__('datetime').timedelta(days=7)
  maintenance={'TimersCalendar':'OnCalendar=Sun *-*-* 03:00:00 America/New_York','TimersMonotonic':'','Persistent':'yes','RandomizedDelayUSec':'0','AccuracyUSec':'1s','property':'NextElapseUSecRealtime','next_value':int(candidate.timestamp())*1000000,'monotonic_observed_us':100,'monotonic_timers':None}
  recovery=dict(maintenance,TimersCalendar='',TimersMonotonic='audit-only-no-substring',property='NextElapseUSecMonotonic',next_value=1000000,monotonic_timers=c.parse_monotonic_timers(GOOD))
  schedule={'careertrellis-paired-maintenance.timer':maintenance,'careertrellis-paired-recovery.timer':recovery};pair._web_schedule(schedule,now)
  for kind in ('missing','duplicate','tick','maintenance_extra'):
   bad=copy.deepcopy(schedule)
   if kind=='missing':bad['careertrellis-paired-recovery.timer'].pop('monotonic_timers')
   elif kind=='duplicate':bad['careertrellis-paired-recovery.timer']['monotonic_timers'][1]['base']='OnBootUSec'
   elif kind=='tick':bad['careertrellis-paired-recovery.timer']['next_value']=61000101
   else:bad['careertrellis-paired-maintenance.timer']['monotonic_timers']=[]
   with self.subTest(kind=kind),self.assertRaises(c.Refused):pair._web_schedule(bad,now)
 def test_actual_web_scheduler_preserves_existing_guards(self):
  now=datetime(2026,10,28,12,tzinfo=timezone.utc);local=now.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'));candidate=local.replace(hour=3,minute=0,second=0,microsecond=0)+__import__('datetime').timedelta(days=(6-local.weekday())%7)
  if candidate<=local:candidate+=__import__('datetime').timedelta(days=7)
  class Reader:
   role='web'
   def __init__(self,bad=None):self.bad=bad
   def read(self,path,mode):return b'bad' if self.bad=='hash' else path.encode()
   def unit(self,name):
    value={'FragmentPath':'/etc/systemd/system/'+name,'DropInPaths':'','ActiveState':'inactive','MainPID':'0','ControlPID':'0','Job':'','SubState':'dead','UnitFileState':'static','RandomizedDelayUSec':'0','AccuracyUSec':'1s','TimersCalendar':'OnCalendar=Sun *-*-* 03:00:00 America/New_York','Persistent':'yes','TimersMonotonic':'audit only'}
    if name.endswith('.timer'):value.update(ActiveState='active',SubState='waiting',UnitFileState='enabled')
    failures={'dropin':('DropInPaths','/foreign'),'fragment':('FragmentPath','/foreign'),'job':('Job','42'),'delay':('RandomizedDelayUSec','1s'),'accuracy':('AccuracyUSec','2s'),'timerstate':('ActiveState','inactive'),'enabled':('UnitFileState','disabled'),'persistent':('Persistent','no'),'calendar':('TimersCalendar','wrong')}
    if self.bad in failures and name.endswith('.timer'):key,v=failures[self.bad];value[key]=v
    if self.bad=='servicepid' and name.endswith('.service'):value['MainPID']='123'
    return value
   def timer_next(self,name,prop):
    if prop=='NextElapseUSecRealtime':return int(candidate.timestamp())*1000000
    return 1000000 if self.bad!='tick' else 62000001
   def timer_monotonic(self,name):
    value=c.parse_monotonic_timers(GOOD)
    if self.bad=='typed':value[0]['offset_usec']=1
    return value
  pins={'units':{'/etc/systemd/system/'+name:hashlib.sha256(('/etc/systemd/system/'+name).encode()).hexdigest() for name in c.WEB_SCHEDULER_UNITS}}
  with patch.object(c,'public_document',return_value=pins),patch.object(c,'utc',return_value=now),patch.object(c.time,'monotonic_ns',return_value=1000000000):
   c.web_standing_scheduler(Reader())
   for bad in ('hash','dropin','fragment','job','delay','accuracy','timerstate','enabled','persistent','calendar','servicepid','tick','typed'):
    with self.subTest(bad=bad),self.assertRaises(c.Refused):c.web_standing_scheduler(Reader(bad))
if __name__=='__main__':unittest.main()
