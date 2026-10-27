import ast,copy,datetime as dt,hashlib,json,unittest
from pathlib import Path,PurePosixPath
from types import SimpleNamespace
import qualified97_records as records
from qualified_specs import exact_spec

def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def need(v):
 if not v:raise RuntimeError('TEST_ACTUAL_METHOD_REFUSED')
def method(name,scope):
 tree=ast.parse((Path(__file__).resolve().parent/'admission97.py').read_bytes());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Admission97')
 fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name)
 exec(compile(ast.Module(body=[fn],type_ignores=[]),'<actual Admission97 method>','exec'),scope);return scope[name]
class MaterializationProofTests(unittest.TestCase):
 def setup_data(self):
  manifest={'files':{'public':'synthetic'}};runtime={'backend/venv/bin/python':'a'*64}
  plan={'source_manifest_sha256':digest(manifest),'accepted98_sha256':'b'*64,'decision_at':'2020-01-01T00:00:00+00:00','expires_at':'2020-01-01T00:55:00+00:00'}
  obj=SimpleNamespace(spec=exact_spec('qualified97-new/v1'),manifest=manifest,digest=digest,boot=lambda:'syntheticboot',now=lambda:dt.datetime.fromisoformat('2020-01-01T00:10:00+00:00'))
  proof={'schema':'careertrellis.qualified97-materialization-proof/v1','revision':obj.spec['revision'],'plan_sha256':digest(plan),'accepted98_sha256':plan['accepted98_sha256'],'source_manifest_sha256':digest(manifest),'runtime_manifest_sha256':digest(runtime),'boot_id':'syntheticboot','completed_at':'2020-01-01T00:05:00+00:00'}
  return obj,plan,runtime,proof
 def test_actual_closed_positive(self):
  o,p,r,v=self.setup_data();method('require_materialization_proof',{'need':need,'dt':dt})(o,v,r,p);self.assertEqual(o.materialized_runtime_sha,digest(r))
 def test_actual_wrong_source_bad_time_extra_and_digest_refuse(self):
  for field,value in [('source_manifest_sha256','9'*64),('completed_at','not-a-time'),('EXTRA',True),('runtime_manifest_sha256','9'*64),('plan_sha256','9'*64)]:
   o,p,r,v=self.setup_data();v[field]=value
   with self.subTest(field=field),self.assertRaises((RuntimeError,ValueError)):method('require_materialization_proof',{'need':need,'dt':dt})(o,v,r,p)

class ChronologyTests(unittest.TestCase):
 def data(self):
  def t(n):return '2020-01-01T00:%02d:00+00:00'%n
  plan={'decision_at':t(0),'expires_at':t(55),'accepted98_sha256':'b'*64};manifest={'files':{'public':'synthetic'}};plan['source_manifest_sha256']=digest(manifest)
  def closed(name,values):return {'schema':records.SCHEMAS[name][0],**values}
  owner=closed('owner.json',{'pid':2000001,'birth':'11001','boot_id':'boot','plan_sha256':digest(plan),'started_at':t(1)})
  coord=closed('coordinator.json',{'pid':2000002,'birth':'11002','boot_id':'boot','owner_pid':2000001,'plan_sha256':digest(plan),'started_at':t(2)})
  intent=closed('materialization-intent.json',{'revision':exact_spec('qualified97-new/v1')['revision'],'plan_sha256':digest(plan),'source_manifest_sha256':digest(manifest),'accepted98_sha256':'b'*64,'boot_id':'boot','peak_bytes':100,'created_at':t(3)})
  proof=closed('materialization-proof.json',{'revision':intent['revision'],'plan_sha256':digest(plan),'accepted98_sha256':'b'*64,'source_manifest_sha256':digest(manifest),'runtime_manifest_sha256':'c'*64,'boot_id':'boot','completed_at':t(4)})
  unitintent=closed('unit-intent.json',{'kind':'qualified97-new/v1','plan_sha256':digest(plan),'decision_sha256':'d'*64,'argv':['fixed'],'properties':['strict'],'source_manifest_sha256':digest(manifest),'coordinator_pid':2000002,'coordinator_birth':'11002','boot_id':'boot','created_at':t(5)})
  checks={'started_utc':t(6),'ended_utc':t(7)};unit={'client_pid':2000003,'observed_at':t(8)};candidate={'completed_at':t(9)};ot={'ended_at':t(10)};post={'observed_at':t(11)};accept={'completed_utc':t(12)}
  return [plan,owner,coord,intent,proof,unitintent,checks,unit,candidate,dt.datetime.fromisoformat(t(13)),ot,post,accept],manifest
 def test_pure_complete_order_positive(self):
  args,_=self.data();self.assertTrue(records.validate_chronology(*args))
 def test_each_causal_timestamp_and_same_actors_refuse(self):
  for index,key in [(1,'started_at'),(2,'started_at'),(3,'created_at'),(4,'completed_at'),(5,'created_at'),(6,'started_utc'),(6,'ended_utc'),(7,'observed_at'),(8,'completed_at'),(10,'ended_at'),(11,'observed_at'),(12,'completed_utc')]:
   args,_=self.data();args[index][key]='2019-12-31T23:59:00+00:00'
   with self.subTest(index=index,key=key),self.assertRaises(RuntimeError):records.validate_chronology(*args)
  args,_=self.data();args[2]['pid']=args[1]['pid']
  with self.assertRaises(RuntimeError):records.validate_chronology(*args)
 def test_actual_nested_method_rejects_candidate_owner_chronology(self):
  for target in ('candidate','owner'):
   args,manifest=self.data();p,o,c,mi,mp,ui,k,u,candidate,now,ot,post,a=args
   data={'owner.json':o,'coordinator.json':c,'materialization-intent.json':mi,'materialization-proof.json':mp,'unit-intent.json':ui,'checks.json':k}
   if target=='candidate':candidate['completed_at']='2020-01-01T00:00:30+00:00'
   else:ot['ended_at']='2020-01-01T00:00:30+00:00'
   obj=SimpleNamespace(ns={'decode':lambda raw:json.loads(raw)},spec=exact_spec('qualified97-new/v1'),manifest=manifest,digest=digest,boot=lambda:'boot',now=lambda:now)
   scope={'records':records,'need':need,'DOMAIN':PurePosixPath('/synthetic'),'read_root':lambda path:json.dumps(data[path.name]).encode()}
   with self.subTest(target=target),self.assertRaises(RuntimeError):method('check_chronology',scope)(obj,p,candidate,u,owner_terminal=ot,postterminal=post,acceptance=a)
if __name__=='__main__':unittest.main()
