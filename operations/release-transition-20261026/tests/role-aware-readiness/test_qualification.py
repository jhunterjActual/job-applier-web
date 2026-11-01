import portable_bootstrap as pb
import ast,copy,hashlib,json,pathlib,sys,types,unittest
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
import collector as c
import pair_boundary as pair
import readiness_bridge as r
import test_role_coverage as roles
import test_real_db_core as coretests
P=pathlib.Path(__file__).parent
NOW=coretests.f.OBSERVED
class Fixed(datetime):
 @classmethod
 def now(cls,tz=None):return NOW
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):coretests.Tests.setUpClass()
 def setUp(self):
  self.local=roles.Tests();self.local.setUp();self.stack=__import__('contextlib').ExitStack();self.stack.enter_context(patch.object(c,'utc',lambda:NOW));self.stack.enter_context(patch.object(r,'datetime',Fixed));self.tmp=coretests.Tests().root();self.root=coretests.Tests().healthy(self.tmp.__enter__())
 def tearDown(self):self.tmp.__exit__(None,None,None);self.stack.close();self.local.tearDown()
 def fixture(self,phase='final'):
  bind=self.local.binding(phase);bind['issued_at']=NOW.isoformat();values={};h=coretests.Tests.h
  class Reader(roles.Reader):
   def timer_next(self,name,prop):
    if prop=='NextElapseUSecMonotonic':return 2000000
    from zoneinfo import ZoneInfo
    local=NOW.astimezone(ZoneInfo('America/New_York'));nextday=local.replace(hour=3,minute=0,second=0,microsecond=0)+timedelta(days=(6-local.weekday())%7)
    if nextday<=local:nextday+=timedelta(days=7)
    return int(nextday.timestamp())*1000000
  for role in ('web','db'):
   custody={'mode':bind['mode'],'policy_sha256':'8'*64,'scheduler_verified':False,'owner':{'pid':123 if role=='web' else 456,'start_ticks':1,'boot':bind['boots'][role]},'service_proof_sha256':'9'*64}
   custody['scheduler_coverage']=c.local_scheduler_coverage(Reader(role),role,bind,custody)
   if role=='db':snapshot=coretests.f.snapshot('database');snapshot['units']['full_backup']['missed']=True;result=h.evaluate_backup_health(snapshot,receipt_root=self.root)
   else:snapshot=coretests.f.snapshot('objects');result={'schema':h.STATUS_SCHEMA,'surface':'objects','evaluated_at':snapshot['observed_at'],'result':'ok','failure_codes':[],'failure_count':0,'ages_seconds':{}}
   manifest='a'*64;pin='b'*64;generation={'binding':bind,'core_sha256':c.SPECS[role][2],'collector_manifest_sha256':manifest,'snapshot_sha256':c.sha(c.canonical(snapshot)),'result_sha256':c.sha(c.canonical(result)),'inventory_before_sha256':pin,'inventory_after_sha256':pin,'evaluated_at':result['evaluated_at'],'failure_count':result['failure_count']}
   operations=c.DB_UNITS if role=='db'else{'generated_object_mirror':None,'generated_object_restore':None};values[role]={'binding':bind,'role':role,'boot':bind['boots'][role],'observed_at':NOW.isoformat(),'core_sha256':c.SPECS[role][2],'collector_manifest_sha256':manifest,'custody':custody,'authority':{'domain':'postgres-r2-authoritative','pending_phase':'activated','activation_lease_absent':True},'original_snapshot':snapshot,'snapshot_sha256':generation['snapshot_sha256'],'canonical_evaluation':result,'result_sha256':generation['result_sha256'],'receipt_inventory_sha256':pin,'evaluation_generation':generation,'evaluation_generation_sha256':c.sha(c.canonical(generation)),'result':result['result'],'failure_codes':result['failure_codes'],'failure_count':result['failure_count'],'inventory_verified':True,'evaluation_generation_current':True,'persisted_status_unchanged':True,'persisted_status':result,'latest_receipts':{op:{'result':'ok','completed_at':(NOW-timedelta(seconds=1)).isoformat(),'receipt_sha256':'c'*64}for op in operations}}
  return bind,values['web'],values['db']
 def qualified(self):
  bind,web,db=self.fixture();h=coretests.Tests.h
  def actual_core_derived(raw,binding,reader=None):
   snapshot=copy.deepcopy(raw['original_snapshot']);snapshot['units']['full_backup']['missed']=False;result=h.evaluate_backup_health(snapshot,receipt_root=self.root)
   return {'derived_snapshot':snapshot,'derived_snapshot_sha256':c.sha(c.canonical(snapshot)),'derived_result':result,'derived_result_sha256':c.sha(c.canonical(result)),'core_sha256':c.SPECS['db'][2],'inventory_before_sha256':db['receipt_inventory_sha256'],'inventory_after_sha256':db['receipt_inventory_sha256'],'reevaluated_at':NOW.isoformat()}
  # Only nativeDB observation/orchestration seam; core evaluator+REALreceiptroot remain unmodified.
  with patch.object(c,'reevaluate_database',actual_core_derived):value=r.qualify_local_database(web,db,bind,c,'a'*64)
  return bind,web,db,value
 def test_full_envelope_token_and_raw_strict_default(self):
  bind,web,db,value=self.qualified();self.assertEqual(value['evaluation_generation_sha256'],c.sha(c.canonical(value['envelope'])));self.assertNotEqual(value['evaluation_generation_sha256'],db['evaluation_generation_sha256']);r.verify_qualified_context(value,bind,c,'a'*64,now=NOW)
  with self.assertRaises(RuntimeError):r.verify_generation(db,{'readiness_manifest_sha256':'a'*64},bind,'db',c,now=NOW)
 def test_forgery_binding_expiry_inventory_and_derived_mutants(self):
  for kind in ('binding','expiry','inventory','derived','web','core'):
   with self.subTest(kind=kind):
    bind,web,db,value=self.qualified();e=value['envelope']
    if kind=='binding':e['binding']=dict(bind,nonce='9'*64)
    elif kind=='expiry':e['reevaluated_at']=(NOW-timedelta(seconds=31)).isoformat()
    elif kind=='inventory':e['inventory_after_sha256']='9'*64
    elif kind=='derived':e['derived_snapshot']['wal']['failed_count']=9;e['derived_snapshot_sha256']=c.sha(c.canonical(e['derived_snapshot']))
    elif kind=='web':e['raw_web']['role']='db'
    else:e['core_sha256']='9'*64
    value['evaluation_generation_sha256']=c.sha(c.canonical(e))
    with self.assertRaises((RuntimeError,c.Refused)):r.verify_qualified_context(value,bind,c,'a'*64,now=NOW)
 def test_every_other_raw_failure_refuses_before_qualification(self):
  for code in ('wal_archive_failed','pgbackrest_inventory_receipt_invalid','full_backup_stale'):
   bind,web,db=self.fixture();db['failure_codes']=['full_backup_unit_missed',code];db['failure_count']=2;db['canonical_evaluation'].update(failure_codes=db['failure_codes'],failure_count=2);db['evaluation_generation'].update(result_sha256=c.sha(c.canonical(db['canonical_evaluation'])),failure_count=2);db['result_sha256']=db['evaluation_generation']['result_sha256'];db['evaluation_generation_sha256']=c.sha(c.canonical(db['evaluation_generation']));db['persisted_status']=dict(db['canonical_evaluation'])
   with self.assertRaises((RuntimeError,c.Refused)):r.qualify_local_database(web,db,bind,c,'a'*64)
 def test_any_full_binding_original_predicate_unchanged(self):
  new=next(n for n in ast.parse((pb.ROOT/'readiness_bridge.py').read_bytes()).body if isinstance(n,ast.FunctionDef)and n.name=='verify_repository_binding');self.assertIn('latest_accepted_full_label',ast.dump(new,include_attributes=False))
  with self.assertRaises(RuntimeError):r.verify_repository_binding({'latest_backup_label':'DIFFERENTIAL','latest_full_backup_label':'FULL','checked_at':'SYNTHETIC'}, {'latest_accepted_full_label':'FULL'},'FULL','a'*64)
 def test_already_healthy_raw_db_remains_strict_normal_route(self):
  bind,web,db,value=self.qualified();e=value['envelope'];db=copy.deepcopy(db);db.update(original_snapshot=e['derived_snapshot'],snapshot_sha256=e['derived_snapshot_sha256'],canonical_evaluation=e['derived_result'],result_sha256=e['derived_result_sha256'],result='ok',failure_codes=[],failure_count=0);db['evaluation_generation'].update(snapshot_sha256=db['snapshot_sha256'],result_sha256=db['result_sha256'],failure_count=0);db['evaluation_generation_sha256']=c.sha(c.canonical(db['evaluation_generation']))
  r.verify_generation(db,{'readiness_manifest_sha256':'a'*64},bind,'db',c,now=NOW);self.assertEqual(pair.check_pair(web,db,bind,'a'*64,now=NOW)['result'],'pair_bound')
if __name__=='__main__':unittest.main()
