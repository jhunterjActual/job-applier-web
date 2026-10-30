import portable_bootstrap as pb
import ast,copy,hashlib,json,pathlib,sys,types,unittest,time
from unittest.mock import patch
from datetime import timezone,timedelta
import collector as c
import readiness_bridge as bridge
import test_qualification as q
P=pathlib.Path(__file__).parent
BASE=pb.FIXTURES/'git504'
class Refused(Exception):pass
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):q.Tests.setUpClass()
 def setUp(self):self.fixture=q.Tests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
 def need(self,v):
  if not v:raise Refused()
 def function(self,path,name):return next(n for n in ast.walk(ast.parse(path.read_bytes()))if isinstance(n,ast.FunctionDef)and n.name==name)
 def test_actual_commit_context_complete_envelope_token_decision_and_ack(self):
  binding,web,db,value=self.fixture.qualified()
  engine=types.ModuleType('release_transaction');tree=ast.parse((BASE/'release_transaction.py').read_bytes());engine.BASE=c.BASE;engine.TARGET=next(ast.literal_eval(n.value)for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(a,ast.Name)and a.id=='TARGET'for a in n.targets));engine.hashlib=hashlib;engine.json=json
  nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in ('canonical','digest')];exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual-engine-canonical-functions-only','exec'),engine.__dict__)
  target='bc68ea0dc253425a44e268452130becdf35d764e';correction='f70417d4bd70c3eb086ea63c90938dd2f43e0b99';plan={'boots':binding['boots'],'packet_sha256':'c'*64,'readiness_manifest_sha256':'a'*64,'correction':correction,'expires_at':(q.NOW+timedelta(seconds=30)).isoformat()};binding['release']=target;binding['predecessor']=c.BASE;binding['build']='20261026.97';binding['plan_sha256']=engine.digest(plan)
  for raw in (web,db):raw['evaluation_generation_sha256']=c.sha(c.canonical(raw['evaluation_generation']))
  e=value['envelope'];e['raw_web_generation_sha256']=web['evaluation_generation_sha256'];e['raw_db_generation_sha256']=db['evaluation_generation_sha256'];e['coverage']=q.pair.check_scheduler_pair(web['custody']['scheduler_coverage'],db['custody']['scheduler_coverage'],binding,'a'*64,now=q.NOW);value['evaluation_generation_sha256']=c.sha(c.canonical(e))
  prepared={'schema':'careertrellis.app-global-terminal-authorization/v1','plan_sha256':engine.digest(plan),'packet_sha256':plan['packet_sha256'],'source_commit':binding['source_commit'],'outcome':'target_healthy','selected_release':target,'selected_build':'20261026.97','boots':plan['boots'],'authorization_id':'a'*64,'recovery_epoch_sha256':None}
  saved={};store=types.SimpleNamespace(allowed={},read=lambda path:saved[path]);paths=['/var/lib/careertrellis/paired-maintenance/db/control.lock','/run/lock/careertrellis-maintenance.lock','/run/lock/careertrellis-pgbackrest-repository.lock']
  peer=types.SimpleNamespace(role='db',release_decision=None,assert_known=lambda:None,claim_window=lambda:None,global_certificate=prepared,read_global=lambda _:engine.canonical(prepared),final_readiness=value,final_readiness_deadline=time.monotonic()+30,plan=plan,source_commit=binding['source_commit'],intent={'started_at':q.NOW.isoformat()},original={'private_policy':{'planned_stop_budget_seconds':900}},fds=[(i+10,path,1,i+100)for i,path in enumerate(paths)],create=lambda path,raw:saved.__setitem__(path,raw),decision_path=lambda value:'/synthetic-decision',store=store)
  fn=self.function(BASE/'native_peer.py','commit_release_decision');ns={'need':self.need,'datetime':q.Fixed,'timezone':timezone,'timedelta':timedelta,'time':time,'digest':engine.digest,'canonical':engine.canonical}
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-unmodified-commit-method','exec'),ns);generations={'web':web['evaluation_generation_sha256'],'db':value['evaluation_generation_sha256']}
  with patch.dict(sys.modules,{'release_transaction':engine}),patch.object(bridge,'load',lambda *a,**k:{'collector':c}):
   reply=ns['commit_release_decision'](peer,prepared,binding,generations,(q.NOW+timedelta(seconds=25)).isoformat())
   self.assertEqual(reply['decision']['generation_sha256']['db'],value['evaluation_generation_sha256']);self.assertNotEqual(reply['decision']['generation_sha256']['db'],db['evaluation_generation_sha256'])
   ack={'role':'db','result':'target_healthy','decision_sha256':engine.digest(reply['decision']),'closed_fds':list(reversed(reply['decision']['db_fd_identities']))};bridge.validate_db_release_ack(ack,reply['decision'])
 def test_actual_rpc_refuses_after_first_close_and_duplicate_qualification(self):
  binding,web,db,value=self.fixture.qualified();fn=self.function(pb.ROOT/'peer_server.py','_serve');calls=[];responses=[]
  class Held(BaseException):pass
  module=types.SimpleNamespace(load=lambda *a:{'collector':c},qualify_local_database=lambda *a,**k:calls.append('qualification')or value)
  def execute(release_started,qualified_nonce):
   peer=types.SimpleNamespace(role='db',claimed=True,check_locks=lambda:None,release_started=release_started,release_decision=None,qualified_nonce=qualified_nonce,pending_raw_readiness=db,pending_readiness_reader=None,source_commit=binding['source_commit'],final_readiness_deadline=time.monotonic()+30)
   plan={'readiness_manifest_sha256':'a'*64};token=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest();msg=json.dumps({'seq':0,'plan_sha256':token,'action':'qualify_backup_proofs','args':{'binding':binding,'web_health':web}})+'\n';inputs=iter([msg,''])
   ns={'hashlib':hashlib,'json':json,'decode':json.loads,'need':self.need,'sys':sys,'time':time};exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-rpc-no-os','exec'),ns)
   def hold(_):raise Held()
   with patch.dict(sys.modules,{'readiness_bridge':module}):
    with self.assertRaises(Held):ns['_serve'](peer,plan,lambda _:next(inputs),responses.append,hold)
   return peer
  execute(True,None);execute(False,binding['nonce']);self.assertEqual(calls,[])
  peer=execute(False,None);self.assertEqual(calls,['qualification']);self.assertEqual(peer.final_readiness['evaluation_generation_sha256'],value['evaluation_generation_sha256'])
 def test_backend_unknown_qualification_response_refuses_before_admit(self):
  fn=self.function(pb.ROOT/'native_backend.py','verify_fresh_proofs');node=next(n for n in fn.body if isinstance(n,ast.If)and isinstance(n.test,ast.Compare)and "db_ready['result']"in ast.unparse(n.test));ns={'db_ready':{'result':'alert'},'self':types.SimpleNamespace(db=types.SimpleNamespace(call=lambda *a,**k:{})),'request':{},'web_health':{},'started':0,'need':self.need}
  with self.assertRaises(Refused):exec(compile(ast.Module(body=[node],type_ignores=[]),'actual-backend-response-guard-only','exec'),ns)
 def test_finite_core_source_bodies_untouched(self):
  old=(BASE/'native_peer.py').read_bytes();self.assertEqual(hashlib.sha256(old).hexdigest(),'baeb0f91e08d08009394cafe4885713051e5d309bc4f3000fbebfa066c979137');self.assertEqual(hashlib.sha256((BASE/'release_transaction.py').read_bytes()).hexdigest(),'75b871612b0d394dd90be69fad336654fb332bfe17d7a62d25e35d4a3d7bd8b2')
if __name__=='__main__':unittest.main()
