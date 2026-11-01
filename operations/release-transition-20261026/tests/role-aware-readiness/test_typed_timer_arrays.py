"""Synthetic formatter/typed arrays; no actual timer-array values or host IO."""
import ast,copy,hashlib,importlib.util,json,sys,types,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import portable_bootstrap as pb
import collector as c
import pair_boundary as pair
P=Path(__file__).parent
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
baseline=load('scalar_baseline',P/'fixtures/pre-typed-array-collector.reference.py')
MONO=b'a(stt) 2 "OnBootUSec" 30000000 30000000 "OnUnitActiveUSec" 60000000 2000000'
CAL=b'a(sst) 1 "OnCalendar" "Sun *-*-* 03:00:00 America/New_York" 1793520000000000'
SHOW_MAIN=b'Persistent=yes\nRandomizedDelayUSec=0\nAccuracyUSec=1s\nTimersCalendar={ OnCalendar=Sun *-*-* 03:00:00 America/New_York ; next_elapse=synthetic }\n'
SHOW_RECOVERY=b'Persistent=no\nRandomizedDelayUSec=0\nAccuracyUSec=1s\nTimersMonotonic={ OnBootUSec=30s ; next_elapse=synthetic }\nTimersMonotonic={ OnUnitActiveUSec=1min ; next_elapse=synthetic }\n'
class ParsingTests(unittest.TestCase):
 def test_independent_formatter_zero_rows_duplicate_names_baseline_refuses(self):
  fields=('TimersCalendar','TimersMonotonic','Persistent','RandomizedDelayUSec','AccuracyUSec')
  self.assertNotIn(b'TimersMonotonic=',SHOW_MAIN);self.assertNotIn(b'TimersCalendar=',SHOW_RECOVERY);self.assertEqual(SHOW_RECOVERY.count(b'TimersMonotonic='),2)
  for raw in (SHOW_MAIN,SHOW_RECOVERY):
   with self.assertRaises(baseline.Refused):baseline.unit_properties(raw,fields)
  calls=[]
  def run(args,maximum):
   calls.append(args);self.assertIn('--all',args);selected=next(a for a in args if a.startswith('--property='));self.assertEqual(selected,'--property=Persistent,RandomizedDelayUSec,AccuracyUSec')
   return b''.join(line+b'\n' for line in SHOW_RECOVERY.splitlines() if line.split(b'=')[0] in (b'Persistent',b'RandomizedDelayUSec',b'AccuracyUSec'))
  with patch.object(c,'run',run):self.assertEqual(c.Reader().timer_cadence('careertrellis-paired-recovery.timer'),{'Persistent':'no','RandomizedDelayUSec':'0','AccuracyUSec':'1s'})
  self.assertEqual(len(calls),1)
 def test_actual_typed_zero_only_and_preserve_all_nonzero_rows(self):
  self.assertEqual(c.parse_calendar_array(b'a(sst) 0'),[]);self.assertEqual(c.parse_monotonic_array(b'a(stt) 0'),[])
  self.assertEqual(c.parse_calendar_array(CAL),[{'base':'OnCalendar','specification':'Sun *-*-* 03:00:00 America/New_York','next_elapse_usec':1793520000000000}])
  expected=[{'base':'OnBootUSec','offset_usec':30000000,'next_elapse_usec':30000000},{'base':'OnUnitActiveUSec','offset_usec':60000000,'next_elapse_usec':2000000}]
  self.assertEqual(c.parse_monotonic_array(MONO),expected)
  reverse=b'a(stt) 2 "OnUnitActiveUSec" 60000000 2000000 "OnBootUSec" 30000000 30000000'
  self.assertEqual(c.parse_monotonic_array(reverse),list(reversed(expected)))
 def test_missing_counts_signatures_duplicates_types_uint64_escapes_refuse(self):
  for parse,good in ((c.parse_calendar_array,CAL),(c.parse_monotonic_array,MONO)):
   for raw in (b'',None,[],True,good+b' extra',good.replace(b'a(',b'('),good.replace(b' 1 ',b' 2 ',1) if parse==c.parse_calendar_array else good.replace(b' 2 ',b' 1 ',1)):
    with self.subTest(raw=raw),self.assertRaises(c.Refused):parse(raw)
  for raw in (CAL.replace(b'OnCalendar',b'Unknown'),CAL.replace(b'Sun ',b'Mon '),CAL.replace(b'New_York',b'New\\_York'),CAL.replace(b'1793520000000000',str(2**64).encode()),CAL.replace(b'1793520000000000',b'-1'),CAL.replace(b'1793520000000000',b'True'),b'a(sst) 0 missing'):
   with self.subTest(raw=raw),self.assertRaises(c.Refused):c.parse_calendar_array(raw)
  for raw in (MONO.replace(b'OnUnitActiveUSec',b'OnBootUSec'),MONO.replace(b'60000000',b'60',1),MONO.replace(b'2000000',str(2**64).encode()),b'a(stt) 0 missing'):
   with self.subTest(raw=raw),self.assertRaises(c.Refused):c.parse_monotonic_array(raw)
 def test_unit_specific_empty_no_null_or_inferred_default(self):
  main={'calendar_timers':c.parse_calendar_array(CAL),'monotonic_timers':[]};recovery={'calendar_timers':[],'monotonic_timers':c.parse_monotonic_array(MONO)}
  c.validate_unit_timer_arrays('careertrellis-paired-maintenance.timer',main);c.validate_unit_timer_arrays('careertrellis-paired-recovery.timer',recovery)
  for name,value in [('careertrellis-paired-maintenance.timer',recovery),('careertrellis-paired-recovery.timer',main),('careertrellis-paired-recovery.timer',{'calendar_timers':None,'monotonic_timers':recovery['monotonic_timers']}),('careertrellis-paired-maintenance.timer',{'calendar_timers':[],'monotonic_timers':[]})]:
   with self.assertRaises(c.Refused):c.validate_unit_timer_arrays(name,value)
class CacheTests(unittest.TestCase):
 def test_fixed_typed_queries_raw_cache_and_drift(self):
  calls=[]
  def run(args,maximum):
   calls.append((args,maximum));self.assertEqual(args[:3],['/usr/bin/busctl','--system','get-property']);self.assertIn('org.freedesktop.systemd1.Timer',args)
   return b'a(sst) 0' if args[-1]=='TimersCalendar' else MONO
  with patch.object(c,'run',run):
   reader=c.Reader();value=reader.timer_arrays('careertrellis-paired-recovery.timer');reader.stable();self.assertEqual(value['calendar_timers'],[]);self.assertEqual([n[1] for n in calls],[4096,1024,4096,1024])
  for changed in ((b'a(sst) 0\n',MONO,[],c.parse_monotonic_array(MONO)),(b'a(sst) 0',MONO+b'\n',[],c.parse_monotonic_array(MONO))):
   record=(b'a(sst) 0',MONO,[],c.parse_monotonic_array(MONO))
   with patch.object(c,'timer_array_record',side_effect=[record,changed]):
    reader=c.Reader();reader.timer_arrays('careertrellis-paired-recovery.timer')
    with self.assertRaises(c.Refused):reader.stable()
  reader=c.Reader()
  with patch.object(c.time,'monotonic',return_value=reader.start+31),self.assertRaises(c.Refused):reader.stable()
 def test_missing_typed_property_never_becomes_empty(self):
  for raw in (b'',b'No such property',b'a(sst)'):
   with patch.object(c,'run',return_value=raw),self.assertRaises(c.Refused):c.Reader().timer_arrays('careertrellis-paired-maintenance.timer')
class CoverageTests(unittest.TestCase):
 def fixture(self,now):
  local=now.astimezone(ZoneInfo('America/New_York'));candidate=local.replace(hour=3,minute=0,second=0,microsecond=0)+timedelta(days=(6-local.weekday())%7)
  if candidate<=local:candidate+=timedelta(days=7)
  epoch=int(candidate.timestamp())*1000000
  main={'Persistent':'yes','RandomizedDelayUSec':'0','AccuracyUSec':'1s','property':'NextElapseUSecRealtime','next_value':epoch,'monotonic_observed_us':1000000,'calendar_timers':[{'base':'OnCalendar','specification':'Sun *-*-* 03:00:00 America/New_York','next_elapse_usec':epoch}],'monotonic_timers':[]}
  recovery={'Persistent':'no','RandomizedDelayUSec':'0','AccuracyUSec':'1s','property':'NextElapseUSecMonotonic','next_value':2000000,'monotonic_observed_us':1000000,'calendar_timers':[],'monotonic_timers':c.parse_monotonic_array(MONO)}
  return {'careertrellis-paired-maintenance.timer':main,'careertrellis-paired-recovery.timer':recovery}
 def test_calendar_next_exact_sunday_dst_and_original_mono_window(self):
  for now in (datetime(2026,10,28,12,tzinfo=timezone.utc),datetime(2026,11,3,12,tzinfo=timezone.utc),datetime(2026,3,5,12,tzinfo=timezone.utc),datetime(2026,3,10,12,tzinfo=timezone.utc)):
   schedule=self.fixture(now);pair._web_schedule(schedule,now)
   for bad in ('next','arraynext','calendar','monoempty','calextra','boolnext','window','keys'):
    value=copy.deepcopy(schedule);main=value['careertrellis-paired-maintenance.timer'];recovery=value['careertrellis-paired-recovery.timer']
    if bad=='next':main['next_value']+=1
    elif bad=='arraynext':main['calendar_timers'][0]['next_elapse_usec']+=1
    elif bad=='calendar':main['calendar_timers'][0]['specification']='Mon *-*-* 03:00:00 America/New_York'
    elif bad=='monoempty':recovery['monotonic_timers']=[]
    elif bad=='calextra':recovery['calendar_timers']=main['calendar_timers']
    elif bad=='boolnext':main['calendar_timers'][0]['next_elapse_usec']=True
    elif bad=='window':recovery['next_value']=62000001
    else:main['TimersCalendar']='old scalar format'
    with self.subTest(now=now,bad=bad),self.assertRaises(c.Refused):pair._web_schedule(value,now)
 def test_actual_web_scheduler_retains_unit_hash_state_and_arrays_guards(self):
  now=datetime(2026,10,28,12,tzinfo=timezone.utc);schedule=self.fixture(now)
  class Reader:
   role='web'
   def __init__(self,bad=None):self.bad=bad
   def unit(self,name):
    timer=name.endswith('.timer');v={'FragmentPath':'/etc/systemd/system/'+name,'DropInPaths':'','ActiveState':'active' if timer else 'inactive','SubState':'waiting' if timer else 'dead','UnitFileState':'enabled' if timer else 'static','Job':''}
    if not timer:v.update(MainPID='0',ControlPID='0')
    if self.bad=='dropin':v['DropInPaths']='unknown'
    if self.bad=='active' and not timer:v['ActiveState']='active'
    return v
   def read(self,path,mode):return b'bad' if self.bad=='hash' else path.encode()
   def timer_cadence(self,name):return {k:schedule[name][k] for k in ('Persistent','RandomizedDelayUSec','AccuracyUSec')}
   def timer_arrays(self,name):
    v={k:copy.deepcopy(schedule[name][k]) for k in ('calendar_timers','monotonic_timers')}
    if self.bad=='missing' and 'maintenance' in name:v['calendar_timers']=[]
    return v
   def timer_next(self,name,prop):return schedule[name]['next_value']+(1 if self.bad=='next' else 0)
  pins={'units':{'/etc/systemd/system/'+n:hashlib.sha256(('/etc/systemd/system/'+n).encode()).hexdigest() for n in c.WEB_SCHEDULER_UNITS}}
  with patch.object(c,'public_document',return_value=pins),patch.object(c,'utc',return_value=now),patch.object(c.time,'monotonic_ns',return_value=1000000000):
   c.web_standing_scheduler(Reader())
   for bad in ('hash','dropin','active','missing','next'):
    with self.subTest(bad=bad),self.assertRaises(c.Refused):c.web_standing_scheduler(Reader(bad))
 def test_preserved_private_artifacts_history_and_strict_scalar_parser(self):
  old=(P/'fixtures/pre-typed-array-collector.reference.py').read_bytes();new=(pb.ROOT/'one_shot_readiness/collector.py').read_bytes()
  def functions(raw):
   lines=raw.splitlines(True);return {n.name:b''.join(lines[n.lineno-1:n.end_lineno]) for n in ast.walk(ast.parse(raw)) if isinstance(n,ast.FunctionDef)}
  a=functions(old);b=functions(new)
  for name in ('read','read_artifact','effective_artifact_map','verify_effective_artifacts','unit','unit_properties','archive','web_prestart_record','database_complete_record','private_complete_record','web_complete_proof','derived_complete_input','historical_terminal'):self.assertEqual(a[name],b[name])
 def test_actual_full_both_role_join_v3_all_phases_planless_old_missing_refuse(self):
  now=datetime(2026,10,28,12,tzinfo=timezone.utc);schedule=self.fixture(now)
  def state(name):
   v={'LoadState':'loaded','NeedDaemonReload':'no','FragmentPath':'/etc/systemd/system/'+name,'DropInPaths':'','ActiveState':'inactive','SubState':'dead','Job':''}
   if name.endswith('.timer'):v.update(UnitFileState='disabled')
   else:v.update(MainPID='0',ControlPID='0',Result='success',InvocationID='',NRestarts='0',UnitFileState='static')
   return v
  class Reader:
   def __init__(self,role):self.role=role;self.checks=[]
   def read(self,path,mode=0o600):return str(path).encode()
   def unit(self,name):
    if name in c.DB_TEMPLATES:raise AssertionError('NO_BARE_TEMPLATE_SHOW')
    v=state(name)
    if name in c.WEB_SCHEDULER_UNITS and name.endswith('.timer'):v.update(ActiveState='active',SubState='waiting',UnitFileState='enabled')
    return v
   def timer_cadence(self,name):return {k:schedule[name][k] for k in ('Persistent','RandomizedDelayUSec','AccuracyUSec')}
   def timer_arrays(self,name):return {k:copy.deepcopy(schedule[name][k]) for k in ('calendar_timers','monotonic_timers')}
   def timer_next(self,name,prop):return schedule[name]['next_value']
  legacy={'timer':{'name':'careertrellis-pgbackrest-full.timer','state':{k:state('careertrellis-pgbackrest-full.timer')[k] for k in c.TIMER_UNIT_FIELDS}},'service':{'name':'careertrellis-pgbackrest-full.service','sha256':c.FULL_SERVICE_PIN,'state':{k:state('careertrellis-pgbackrest-full.service')[k] for k in c.SERVICE_UNIT_FIELDS}}}
  pins={'units':{'/etc/systemd/system/'+n:c.sha(('/etc/systemd/system/'+n).encode()) for n in (*c.WEB_SCHEDULER_UNITS,*c.DB_TEMPLATES)}}
  with __import__('contextlib').ExitStack() as stack:
   for obj,name,value in [(c,'public_document',lambda _:pins),(pair,'public_document',lambda _:pins),(c,'utc',lambda:now),(c.time,'monotonic_ns',lambda:1000000000),(c,'VERIFIED_SOURCE_MANIFEST','f'*64),(c,'db_registered_templates',lambda:{n:{'state':'static','preset':'-'} for n in c.DB_TEMPLATES}),(c,'db_unit_search_paths',lambda:['/etc/systemd/system','/usr/lib/systemd/system']),(c,'db_instance_names',lambda:[]),(c,'template_dropin_snapshot',lambda *args:{'synthetic':1}),(c,'retired_full_coverage',lambda _:copy.deepcopy(legacy))]:stack.enter_context(patch.object(obj,name,value))
   for phase in ('preflight','stopped','verifying','final','planless'):
    binding={'nonce':'1'*64,'source_commit':'2'*40,'boots':{'web':'11111111-1111-1111-1111-111111111111','db':'22222222-2222-2222-2222-222222222222'},'issued_at':now.isoformat(),'phase':'preflight' if phase=='planless' else phase,'mode':{'preflight':'participant-preclaim','stopped':'owned-stopped','verifying':'owned-verifying','final':'owned-final','planless':'idle'}[phase],'release':c.BASE,'build':'20261017.96','plan_sha256':'3'*64,'predecessor':c.BASE}
    if phase=='planless':binding.pop('plan_sha256');binding.pop('predecessor');binding['schema']='careertrellis.preplan-scheduler-binding/v1'
    values={role:c.local_scheduler_coverage(Reader(role),role,binding,{'mode':binding['mode'],'policy_sha256':'4'*64}) for role in ('web','db')}
    self.assertEqual(pair.check_scheduler_pair(values['web'],values['db'],binding,'f'*64,now=now)['result'],'scheduler_pair_bound')
    for kind in ('v2','missing','nonce','boot','stale'):
     web=copy.deepcopy(values['web']);db=copy.deepcopy(values['db'])
     if kind=='v2':web['schema']='careertrellis.local-scheduler-coverage/v2'
     elif kind=='missing':web['web_schedule']['careertrellis-paired-maintenance.timer'].pop('calendar_timers')
     elif kind=='nonce':db['binding']['nonce']='9'*64
     elif kind=='boot':db['binding']['boots']['db']='3'*36
     else:web['observed_at']=(now-timedelta(seconds=31)).isoformat()
     with self.subTest(phase=phase,kind=kind),self.assertRaises(c.Refused):pair.check_scheduler_pair(web,db,binding,'f'*64,now=now)
if __name__=='__main__':unittest.main()
