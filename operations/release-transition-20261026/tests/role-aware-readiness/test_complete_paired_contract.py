"""Synthetic source/FD/manager fixtures only; no native claims or host IO."""
import ast,copy,hashlib,importlib.util,io,json,stat,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
import portable_bootstrap as pb
import collector as c
import pair_boundary as pair
P=Path(__file__).parent
RUNTIME=(P/'source-catalog493/runtime.py').read_bytes()
DATA={'application_release':'a'*40,'ops_release':'b'*40,'application_build':'20261028.97','max_capture_objects':128,'max_capture_bytes':33554432}
ORACLE=json.loads((P/'PAIRED_CONTRACT03_ORACLE.json').read_bytes())
def oracle(ops='b'*40):return {role:{p.replace('/OPS/','/'+ops+'/'):v for p,v in values.items()} for role,values in ORACLE.items()}
def source_reader(raw=RUNTIME):return types.SimpleNamespace(read=lambda *unused:raw)
GOOD=b'a(stt) 2 "OnBootUSec" 30000000 30000000 "OnUnitActiveUSec" 60000000 90000000'
class MapTests(unittest.TestCase):
 def test_independent_literal_all15_default_and_optional_overrides(self):
  for role in ('web','db'):
   for overrides in (None,{}, {'web':{}},{'db':{}}, {role:{next(iter(oracle()[role])):'c'*64}}):
    data=copy.deepcopy(DATA)
    if overrides is not None:data['artifact_hashes']=overrides
    expected=oracle()
    for key,values in (overrides or {}).items():expected[key].update(values)
    with self.subTest(role=role,overrides=overrides):self.assertEqual(c.effective_artifact_map(source_reader(),types.SimpleNamespace(data=data),role),expected)
 def test_four_previously_omitted_and_ops_relocation(self):
  data=dict(DATA,ops_release='d'*40);actual=c.effective_artifact_map(source_reader(),types.SimpleNamespace(data=data),'db');self.assertEqual(actual,oracle('d'*40))
  self.assertEqual(len(actual['db']),10)
  for name in ('manage-admission-purge.sh','manage-totp-cleanup.sh','careertrellis-admission-purge@.service','careertrellis-totp-cleanup@.service'):self.assertTrue(any(p.endswith('/'+name) for p in actual['db']))
 def test_constructor_source_unknown_override_and_rewrite_collisions_refuse(self):
  for raw in (RUNTIME+b'\n',RUNTIME.replace(b'PINS[\'db\'].update',b'PINS[\'web\'].update'),RUNTIME.replace(b'for _name,_hash in (',b'for _other,_hash in (')):
   with self.assertRaises(c.Refused):c.effective_artifact_map(source_reader(raw),types.SimpleNamespace(data=DATA),'db')
  for values in ({'other':{}},{'db':{'/foreign':'f'*64}},{'db':{'/opt/careertrellis/staging/releases/98c58a180c87d637fdb87c58388a0e482b03beac/backend/admission_purge.py':'f'*64}},{'web':{'/usr/local/sbin/careertrellis-authority':'bad'}}):
   with self.subTest(values=values),self.assertRaises(c.Refused):c.effective_artifact_map(source_reader(),types.SimpleNamespace(data=dict(DATA,artifact_hashes=values)),'db')
 def test_actual_each15_read_and_hash_failure_no_missing_leaf(self):
  # Explicit hash seam maps public symbolic bytes to authenticated literal pins;
  # actual source constructor/loop/15 memberships exercised, not native hashes.
  expected=oracle();realsha=c.sha
  for role in ('web','db'):
   class Reader:
    def __init__(self,bad=None):self.artifact_pins={};self.calls=[];self.bad=bad
    def read(self,*unused):return RUNTIME
    def read_artifact(self,path,pin):self.calls.append(path);return b'bad' if path==self.bad else path.encode()
   def digest(raw):
    if raw==RUNTIME:return realsha(raw)
    try:return expected[role][raw.decode()]
    except (KeyError,UnicodeDecodeError):return realsha(raw)
   with patch.object(c,'sha',digest):
    r=Reader();self.assertEqual(c.verify_effective_artifacts(r,types.SimpleNamespace(data=DATA),role),expected[role]);self.assertEqual(r.calls,list(expected[role]))
    for bad in expected[role]:
     with self.subTest(role=role,bad=bad),self.assertRaises(c.Refused):c.verify_effective_artifacts(Reader(bad),types.SimpleNamespace(data=DATA),role)
class FakeFS:
 def __init__(self,raw=b'public-code',mode=0o644):
  self.raw=raw;self.info=types.SimpleNamespace(st_dev=1,st_ino=2,st_mode=stat.S_IFREG|mode,st_uid=0,st_gid=0,st_nlink=1,st_size=len(raw),st_mtime_ns=3,st_ctime_ns=4);self.parent=types.SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0);self.pin=hashlib.sha256(raw).hexdigest();self.closed=[]
 def context(self):
  fs=self
  class File:
   parents=[types.SimpleNamespace(lstat=lambda:fs.parent)]
   def lstat(self):return fs.info
  stack=__import__('contextlib').ExitStack()
  stack.enter_context(patch.object(c,'Path',lambda *args:File()))
  stack.enter_context(patch.object(c.os,'open',return_value=17))
  stack.enter_context(patch.object(c.os,'fstat',lambda fd:copy.copy(fs.info)))
  stack.enter_context(patch.object(c.os,'fdopen',lambda *a,**kw:io.BytesIO(fs.raw)))
  stack.enter_context(patch.object(c.os,'close',lambda fd:fs.closed.append(fd)))
  # POSIX metadata constants only; no Windows/Linux native claim.
  stack.enter_context(patch.object(c.os,'O_NOFOLLOW',0x20000,create=True));stack.enter_context(patch.object(c.os,'O_NONBLOCK',0x800,create=True))
  return stack
 def reader(self):
  r=c.Reader();r.artifact_pins={'/fixed/code':self.pin};return r
class ArtifactTests(unittest.TestCase):
 def test_modes_nonempty_and_private_default_unmodified(self):
  for mode in (0o600,0o644,0o755):
   fs=FakeFS(mode=mode)
   with fs.context():self.assertEqual(fs.reader().read_artifact('/fixed/code',fs.pin),fs.raw)
   self.assertEqual(fs.closed,[17])
  for mode in (0o640,0o664,0o666,0o775,0o777):
   fs=FakeFS(mode=mode)
   with fs.context(),self.assertRaises(c.Refused):fs.reader().read_artifact('/fixed/code',fs.pin)
  fs=FakeFS(raw=b'')
  with fs.context(),self.assertRaises(c.Refused):fs.reader().read_artifact('/fixed/code',fs.pin)
  fs=FakeFS(mode=0o644)
  with fs.context(),self.assertRaises(c.Refused):fs.reader().read('/fixed/private')
 def test_metadata_path_and_content_negatives(self):
  for key,value in [('st_uid',1),('st_gid',1),('st_nlink',2),('st_mode',stat.S_IFLNK|0o644),('st_size',1048577)]:
   fs=FakeFS();setattr(fs.info,key,value)
   with self.subTest(key=key),fs.context(),self.assertRaises(c.Refused):fs.reader().read_artifact('/fixed/code',fs.pin)
  for path in ('/foreign','/fixed/../code','relative'):
   fs=FakeFS()
   with fs.context(),self.assertRaises(c.Refused):fs.reader().read_artifact(path,fs.pin)
  fs=FakeFS();fs.parent.st_mode|=0o020
  with fs.context(),self.assertRaises(c.Refused):fs.reader().read_artifact('/fixed/code',fs.pin)
  fs=FakeFS();fs.raw=b'other-code!'
  with fs.context(),self.assertRaises(c.Refused):fs.reader().read_artifact('/fixed/code',fs.pin)
 def test_each_fd_identity_and_later_stability_drift(self):
  fields=['st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns']
  for field in fields:
   fs=FakeFS()
   with fs.context():
    r=fs.reader();r.read_artifact('/fixed/code',fs.pin);setattr(fs.info,field,getattr(fs.info,field)+1)
    with self.subTest(field=field),self.assertRaises(c.Refused):r.stable()
  fs=FakeFS()
  with fs.context():
   bad=copy.copy(fs.info);bad.st_ino+=1
   with patch.object(c.os,'fstat',return_value=bad),self.assertRaises(c.Refused):fs.reader().read_artifact('/fixed/code',fs.pin)
  for field in fields:
   fs=FakeFS()
   with fs.context():
    bad=copy.copy(fs.info);setattr(bad,field,getattr(bad,field)+1)
    with self.subTest(mid_read=field),patch.object(c.os,'fstat',side_effect=[copy.copy(fs.info),bad]),self.assertRaises(c.Refused):fs.reader().read_artifact('/fixed/code',fs.pin)
class UnitTests(unittest.TestCase):
 def unit(self,name):
  fields=c.TIMER_UNIT_FIELDS if name.endswith('.timer') else c.SERVICE_UNIT_FIELDS
  row={k:'' for k in fields};row.update(LoadState='loaded',NeedDaemonReload='no',ActiveState='inactive',SubState='dead',Job='',FragmentPath='/etc/systemd/system/'+name,DropInPaths='')
  if name.endswith('.timer'):row['UnitFileState']='disabled'
  else:row.update(MainPID='0',ControlPID='0',Result='success')
  return row
 def test_actual_query_timer_no_pid_service_query_retains_nrestarts(self):
  for name in ('careertrellis-paired-recovery.timer','careertrellis-web.service'):
   def run(args,maximum):
    keys=args[-1].split('=',1)[1].split(',');values=self.unit(name)
    if name.endswith('.service'):values.update(InvocationID='',NRestarts='0',UnitFileState='enabled')
    self.assertEqual(set(keys),set(values));return ''.join(k+'='+values[k]+'\n' for k in keys).encode()
   with patch.object(c,'run',run):
    row=c.Reader().unit(name)
    if name.endswith('.timer'):self.assertEqual(set(row),set(c.TIMER_UNIT_FIELDS));self.assertNotIn('MainPID',row)
    else:self.assertEqual(row['NRestarts'],'0')
 def test_separate_cadence_cache_and_original_ttl(self):
  values={'TimersCalendar':'','TimersMonotonic':'audit','Persistent':'yes','RandomizedDelayUSec':'0','AccuracyUSec':'1s'}
  raw=''.join(k+'='+v+'\n' for k,v in values.items()).encode()
  with patch.object(c,'run',return_value=raw):
   r=c.Reader();self.assertEqual(r.timer_cadence('careertrellis-paired-recovery.timer'),values);r.stable()
   with patch.object(c,'run',return_value=raw.replace(b'1s',b'2s')),self.assertRaises(c.Refused):r.stable()
  with self.assertRaises(c.Refused):c.unit_properties(b'ActiveState=inactive\n',c.TIMER_UNIT_FIELDS)
  with self.assertRaises(c.Refused):c.unit_properties(raw+b'Persistent=no\n',tuple(values))
  with patch.object(c,'timer_monotonic',side_effect=[c.parse_monotonic_timers(GOOD),c.parse_monotonic_timers(GOOD.replace(b'90000000',b'90000001'))]):
   r=c.Reader();r.timer_monotonic('careertrellis-paired-recovery.timer')
   with self.assertRaises(c.Refused):r.stable()
  r=c.Reader()
  with patch.object(c.time,'monotonic',return_value=r.start+31),self.assertRaises(c.Refused):r.stable()
 def test_retired_timer_plus_real_service_closed_pair_and_activity_failures(self):
  test=self
  class Reader:
   def __init__(self,bad=None):self.artifact_pins={};self.bad=bad
   def read(self,*a):return RUNTIME
   def unit(self,name):
    row=test.unit(name)
    if self.bad=='active' and name.endswith('.service'):row['ActiveState']='active'
    if self.bad=='pid' and name.endswith('.service'):row['MainPID']='123'
    if self.bad=='timer':row['UnitFileState']='enabled'
    if self.bad=='dropin':row['DropInPaths']='unexpected'
    return row
   def read_artifact(self,path,pin):return b'synthetic-full-service'
  realsha=c.sha
  with patch.object(c,'sha',lambda raw:c.FULL_SERVICE_PIN if raw==b'synthetic-full-service' else realsha(raw)):
   value=c.retired_full_coverage(Reader());pair._retired_full(value);self.assertNotIn('MainPID',value['timer']['state'])
   for bad in ('active','pid','timer','dropin'):
    with self.subTest(bad=bad),self.assertRaises(c.Refused):c.retired_full_coverage(Reader(bad))
  for kind in ('timerpid','servicepid','pin','extra'):
   bad=copy.deepcopy(value)
   if kind=='timerpid':bad['timer']['state']['MainPID']='0'
   elif kind=='servicepid':bad['service']['state']['MainPID']='1'
   elif kind=='pin':bad['service']['sha256']='f'*64
   else:bad['extra']=True
   with self.subTest(kind=kind),self.assertRaises(c.Refused):pair._retired_full(bad)
 def test_all_roles_phases_coverage_v2_and_history_functions_raw(self):
  # Actual custody must run complete artifact guard BEFORE any phase branch.
  for role in ('web','db'):
   for phase in ('preflight','stopped','verifying','final'):
    class Reached(Exception):pass
    with patch.object(c,'module',return_value=types.SimpleNamespace(Policy=lambda data:types.SimpleNamespace(data=data))),patch.object(c,'verify_effective_artifacts',side_effect=Reached):
     with self.assertRaises(Reached):c.native_custody(types.SimpleNamespace(read=lambda *a:c.canonical(DATA)),role,{'phase':phase},{'common.py':b'fixed'})
  original=(P/'fixtures/paired-contract06-collector.reference.py').read_bytes();current=(pb.ROOT/'one_shot_readiness/collector.py').read_bytes()
  def fn(raw,name):
   n=next(n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name==name);return b''.join(raw.splitlines(True)[n.lineno-1:n.end_lineno])
  for name in ('archive','web_prestart_record','database_complete_record','private_complete_record','web_complete_proof','derived_complete_input','historical_terminal'):self.assertEqual(fn(original,name),fn(current,name))
  source=(pb.ROOT/'one_shot_readiness/collector.py').read_text();self.assertIn("careertrellis.local-scheduler-coverage/v2",source)
 def test_actual_both_role_full_coverage_join_all_phases_and_failures(self):
  from datetime import datetime,timezone,timedelta
  from zoneinfo import ZoneInfo
  now=datetime(2026,10,28,12,tzinfo=timezone.utc);test=self
  local=now.astimezone(ZoneInfo('America/New_York'));candidate=local.replace(hour=3,minute=0,second=0,microsecond=0)+timedelta(days=(6-local.weekday())%7)
  if candidate<=local:candidate+=timedelta(days=7)
  class Reader:
   def __init__(self,role,bad=None):self.role=role;self.checks=[];self.artifact_pins={};self.bad=bad
   def read(self,path,mode=0o600):return RUNTIME if str(path)==str(c.INSTALL/'runtime.py') else str(path).encode()
   def read_artifact(self,path,pin):return b'synthetic-full-service'
   def unit(self,name):
    if name in c.DB_TEMPLATES:raise AssertionError('NO_BARE_TEMPLATE_SHOW')
    row=test.unit(name)
    if name.endswith('.timer') and name!='careertrellis-pgbackrest-full.timer':row.update(ActiveState='active',SubState='waiting',UnitFileState='enabled')
    if name.endswith('.service'):row.update(InvocationID='',NRestarts='0',UnitFileState='static')
    if self.bad=='fullactive' and name=='careertrellis-pgbackrest-full.service':row['ActiveState']='active'
    if self.bad=='reload':row['NeedDaemonReload']='yes'
    if self.bad=='timerpid' and name.endswith('.timer'):row['MainPID']='0'
    return row
   def timer_cadence(self,name):return {'TimersCalendar':'OnCalendar=Sun *-*-* 03:00:00 America/New_York','TimersMonotonic':'audit','Persistent':'yes','RandomizedDelayUSec':'0','AccuracyUSec':'1s'}
   def timer_monotonic(self,name):return c.parse_monotonic_timers(GOOD)
   def timer_next(self,name,prop):return int(candidate.timestamp())*1000000 if prop=='NextElapseUSecRealtime' else 2000000
  pins={'units':{'/etc/systemd/system/'+n:hashlib.sha256(('/etc/systemd/system/'+n).encode()).hexdigest() for n in (*c.WEB_SCHEDULER_UNITS,*c.DB_TEMPLATES)}}
  realsha=c.sha
  with __import__('contextlib').ExitStack() as stack:
   for obj,name,value in [(c,'public_document',lambda name:pins),(pair,'public_document',lambda name:pins),(c,'utc',lambda:now),(c.time,'monotonic_ns',lambda:1000000000),(c,'db_registered_templates',lambda:{n:{'state':'static','preset':'-'} for n in c.DB_TEMPLATES}),(c,'db_unit_search_paths',lambda:['/etc/systemd/system','/usr/lib/systemd/system']),(c,'db_instance_names',lambda:[]),(c,'template_dropin_snapshot',lambda *a:{'synthetic':1}),(c,'sha',lambda raw:c.FULL_SERVICE_PIN if raw==b'synthetic-full-service' else realsha(raw))]:stack.enter_context(patch.object(obj,name,value))
   stack.enter_context(patch.object(c,'VERIFIED_SOURCE_MANIFEST','f'*64))
   for phase in ('preflight','stopped','verifying','final'):
    binding={'nonce':'1'*64,'source_commit':'2'*40,'boots':{'web':'11111111-1111-1111-1111-111111111111','db':'22222222-2222-2222-2222-222222222222'},'issued_at':now.isoformat(),'phase':phase,'mode':{'preflight':'participant-preclaim','stopped':'owned-stopped','verifying':'owned-verifying','final':'owned-final'}[phase],'release':c.BASE,'build':'20261017.96','plan_sha256':'3'*64,'predecessor':c.BASE}
    values={role:c.local_scheduler_coverage(Reader(role),role,binding,{'mode':binding['mode'],'policy_sha256':'4'*64}) for role in ('web','db')}
    self.assertEqual(pair.check_scheduler_pair(values['web'],values['db'],binding,'f'*64,now=now)['result'],'scheduler_pair_bound')
    self.assertEqual(values['db']['schema'],'careertrellis.local-scheduler-coverage/v2')
    for row in values['web']['units'].values():self.assertEqual(set(row['state']),set(c.TIMER_UNIT_FIELDS if row['name'].endswith('.timer') else c.SERVICE_UNIT_FIELDS))
    for bad in ('fullactive','reload'):
     with self.subTest(phase=phase,bad=bad),self.assertRaises(c.Refused):c.local_scheduler_coverage(Reader('db',bad),'db',binding,{'mode':binding['mode'],'policy_sha256':'4'*64})
    for kind in ('nonce','boot','policy','stale','timerpid'):
     db=copy.deepcopy(values['db']);web=copy.deepcopy(values['web'])
     if kind=='nonce':db['binding']['nonce']='9'*64
     elif kind=='boot':db['binding']['boots']['db']='3'*36
     elif kind=='policy':db['policy_sha256']='9'*64
     elif kind=='stale':db['observed_at']=(now-timedelta(seconds=31)).isoformat()
     else:web['units']['careertrellis-paired-recovery.timer']['state']['MainPID']='0'
     with self.subTest(phase=phase,kind=kind),self.assertRaises(c.Refused):pair.check_scheduler_pair(web,db,binding,'f'*64,now=now)
if __name__=='__main__':unittest.main()
