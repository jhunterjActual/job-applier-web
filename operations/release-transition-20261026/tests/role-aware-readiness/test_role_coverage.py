import portable_bootstrap as pb
import ast,copy,json,types,unittest
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
import collector as c
import pair_boundary as b
NOW=datetime(2025,1,1,tzinfo=timezone.utc)
class Reader:
 def __init__(self,role):self.role=role;self.checks=[];self.values={};self.calls=[];self.artifact_pins={}
 def unit(self,name,remember=True):
  if name in c.DB_TEMPLATES:raise AssertionError('BARE_TEMPLATE_SHOW_FORBIDDEN')
  self.calls.append(name)
  if name in self.values:return self.values[name]
  template=name if '@' not in name else ('careertrellis-paired-db-recovery@.service' if '-recovery@' in name else 'careertrellis-paired-db@.service')
  timer=name.endswith('.timer');v={'LoadState':'loaded','ActiveState':'active' if timer and 'paired-' in name else 'inactive','SubState':'waiting' if timer else 'dead','UnitFileState':'enabled' if timer else 'static','Job':'','FragmentPath':'/etc/systemd/system/'+template,'DropInPaths':'','NeedDaemonReload':'no'}
  if not timer:v.update(MainPID='0',ControlPID='0',Result='success',InvocationID='',NRestarts='0')
  if name=='careertrellis-pgbackrest-full.timer':v.update(ActiveState='inactive',UnitFileState='disabled')
  self.values[name]=v;return v
 def read(self,path,mode=0o600):
  if str(path)==str(c.INSTALL/'runtime.py'):return (__import__('pathlib').Path(__file__).parent/'source-catalog493/runtime.py').read_bytes()
  return str(path).encode()
 def timer_monotonic(self,name):return [{'base':'OnBootUSec','offset_usec':30000000,'next_elapse_usec':30000000},{'base':'OnUnitActiveUSec','offset_usec':60000000,'next_elapse_usec':90000000}]
 def timer_cadence(self,name):return {'TimersCalendar':'OnCalendar=Sun *-*-* 03:00:00 America/New_York','TimersMonotonic':'audit only','Persistent':'yes','RandomizedDelayUSec':'0','AccuracyUSec':'1s'}
 def read_artifact(self,path,pin):
  assert path=='/etc/systemd/system/careertrellis-pgbackrest-full.service' and pin==c.FULL_SERVICE_PIN
  return b'SYNTHETIC_FULL_SERVICE'
 def timer_next(self,name,prop):return 1736064000000000 if prop=='NextElapseUSecRealtime'else 2000000
class Tests(unittest.TestCase):
 def setUp(self):
  units={n:c.sha(('/etc/systemd/system/'+n).encode())for n in (*c.WEB_SCHEDULER_UNITS,*c.DB_TEMPLATES)};self.pins={'units':{'/etc/systemd/system/'+n:h for n,h in units.items()}}
  self.oldpublic=c.PUBLIC_FILES;c.PUBLIC_FILES={'installed493-pins.json':json.dumps(self.pins).encode()};self.oldmanifest=c.VERIFIED_SOURCE_MANIFEST;c.VERIFIED_SOURCE_MANIFEST='a'*64
  self.stack=__import__('contextlib').ExitStack();self.stack.enter_context(patch.object(c,'utc',lambda:NOW));self.stack.enter_context(patch.object(c.time,'monotonic_ns',lambda:1000000000));self.stack.enter_context(patch.object(c,'db_instance_names',lambda:[]));self.stack.enter_context(patch.object(c,'db_registered_templates',lambda:{name:{'state':'static','preset':'-'} for name in c.DB_TEMPLATES}));self.stack.enter_context(patch.object(c,'db_unit_search_paths',lambda:['/etc/systemd/system','/usr/lib/systemd/system']));self.stack.enter_context(patch.object(c,'template_dropin_snapshot',lambda *args:{'synthetic':1}))
  real_sha=c.sha;self.stack.enter_context(patch.object(c,'sha',lambda raw:c.FULL_SERVICE_PIN if raw==b'SYNTHETIC_FULL_SERVICE' else real_sha(raw)))
 def tearDown(self):self.stack.close();c.PUBLIC_FILES=self.oldpublic;c.VERIFIED_SOURCE_MANIFEST=self.oldmanifest
 def binding(self,phase='preflight',planless=False):
  v={'nonce':'1'*64,'source_commit':'2'*40,'boots':{'web':'11111111-1111-1111-1111-111111111111','db':'22222222-2222-2222-2222-222222222222'},'issued_at':NOW.isoformat(),'phase':phase,'mode':{'preflight':'participant-preclaim','stopped':'owned-stopped','verifying':'owned-verifying','final':'owned-final'}[phase],'release':c.BASE,'build':'20261017.96','plan_sha256':'3'*64,'predecessor':c.BASE}
  if planless:v.pop('plan_sha256');v.pop('predecessor');v.update(schema='careertrellis.preplan-scheduler-binding/v1',mode='idle')
  return v
 def pair(self,phase='preflight',planless=False):
  binding=self.binding(phase,planless);values=[]
  for role in ('web','db'):
   reader=Reader(role);custody={'mode':binding['mode'],'policy_sha256':'8'*64};values.append(c.local_scheduler_coverage(reader,role,binding,custody))
  return binding,*values
 def test_both_roles_all_four_phase_paths_and_planless_same_join(self):
  for phase in ('preflight','stopped','verifying','final'):
   binding,web,db=self.pair(phase);result=b.check_scheduler_pair(web,db,binding,'a'*64,now=NOW);self.assertEqual(result['result'],'scheduler_pair_bound');self.assertFalse(result['canonical_health_qualified']);self.assertFalse(db['scheduler_verified'])
  binding,web,db=self.pair(planless=True);self.assertNotIn('plan_sha256',binding);self.assertEqual(b.check_scheduler_pair(web,db,binding,'a'*64,now=NOW)['result'],'scheduler_pair_bound')
 def test_db_does_not_probe_web_timers(self):
  binding=self.binding(planless=True);r=Reader('db');c.local_scheduler_coverage(r,'db',binding,{'mode':'idle','policy_sha256':'8'*64});self.assertFalse(any(n in c.WEB_SCHEDULER_UNITS for n in r.calls));self.assertTrue(all(n not in r.calls for n in c.DB_TEMPLATES))
 def test_missing_wrong_role_stale_nonce_boot_policy_source_manifest(self):
  for kind in ('missing','role','stale','nonce','boot','policy','source','manifest','dropin','hash','reload'):
   with self.subTest(kind=kind):
    binding,web,db=self.pair()
    if kind=='missing':web={}
    elif kind=='role':db['role']='web'
    elif kind=='stale':web['observed_at']=(NOW-timedelta(seconds=31)).isoformat()
    elif kind in ('nonce','source','boot'):
     db['binding']=copy.deepcopy(binding)
     if kind=='boot':db['binding']['boots']['web']='3'*36
     else:db['binding']['nonce'if kind=='nonce'else'source_commit']='9'*64
    elif kind=='policy':db['policy_sha256']='9'*64
    elif kind=='manifest':db['collector_manifest_sha256']='9'*64
    else:
     row=next(iter(db['units'].values()))
     if kind=='hash':row['sha256']='9'*64
     elif kind=='dropin':row['dropin_paths']=['unapproved']
     else:
      name='careertrellis-paired-db@'+'a'*32+'.service';reader=Reader('db')
      with patch.object(c,'db_instance_names',lambda:[name]):db=c.local_scheduler_coverage(reader,'db',binding,{'mode':binding['mode'],'policy_sha256':'8'*64})
      db['instances'][0]['state']['NeedDaemonReload']='unapproved'
    with self.assertRaises((c.Refused,KeyError,TypeError)):b.check_scheduler_pair(web,db,binding,'a'*64,now=NOW)
 def test_active_db_instance_refuses_unowned_work(self):
  name='careertrellis-paired-db@'+('a'*32)+'.service';binding=self.binding(planless=True);reader=Reader('db');reader.unit(name)['ActiveState']='active'
  with patch.object(c,'db_instance_names',lambda:[name]):
   with self.assertRaises(c.Refused):c.local_scheduler_coverage(reader,'db',binding,{'mode':'idle','policy_sha256':'8'*64})
 def test_template_wrong_bytes_dropin_reload_refuse_locally(self):
  for key,value in (('DropInPaths','bad'),('NeedDaemonReload','yes'),('FragmentPath','/bad')):
   reader=Reader('db');name='careertrellis-paired-db@'+'a'*32+'.service';reader.unit(name)[key]=value
   with patch.object(c,'db_instance_names',lambda:[name]),self.assertRaises(c.Refused):c.local_scheduler_coverage(reader,'db',self.binding(planless=True),{'mode':'idle','policy_sha256':'8'*64})
 def test_reviewed_web_scheduler_delta_source_bound(self):
  from contract03_test_support import actual_method_hash,scope
  self.assertEqual(actual_method_hash('web_standing_scheduler'),scope['collector']['raw_methods_after']['web_standing_scheduler'])
if __name__=='__main__':unittest.main()
