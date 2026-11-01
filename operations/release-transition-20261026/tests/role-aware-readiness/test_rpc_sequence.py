import portable_bootstrap as pb
import ast,hashlib,json,pathlib,sys,time,types,unittest
from unittest.mock import patch
BASE=pb.ROOT;
import collector as c
import test_qualification as q
class Held(BaseException):pass
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):q.Tests.setUpClass()
 def setUp(self):self.q=q.Tests();self.q.setUp();self.addCleanup(self.q.tearDown)
 def run_sequence(self,actions):
  binding,web,db,qualified=self.q.qualified();db['repository']={'checked_at':q.NOW.isoformat()};qualified['evaluation_generation_sha256']=c.sha(c.canonical(qualified['envelope']));calls=[];responses=[];receipt=b'{}';pair=types.SimpleNamespace(TRANSACTION='synthetic',DB_DIGEST=hashlib.sha256(receipt).hexdigest())
  bridge=types.SimpleNamespace(load=lambda *a:{'collector':c},collect_local=lambda *a,**k:calls.append('raw')or db,verify_generation_structure=lambda *a,**k:None,qualify_local_database=lambda *a,**k:calls.append('qualify')or qualified)
  plan={'readiness_manifest_sha256':'a'*64};token=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':')).encode()).hexdigest();messages=[]
  for seq,action in enumerate(actions):messages.append(json.dumps({'seq':seq,'plan_sha256':token,'action':action,'args':{'binding':binding,**({'web_health':web}if action=='qualify_backup_proofs'else{})}})+'\n')
  messages.append('');read=iter(messages);peer=types.SimpleNamespace(role='db',claimed=True,check_locks=lambda:None,assert_preserved=lambda:None,release_started=False,release_decision=None,source_commit=binding['source_commit'])
  def need(v):
   if not v:raise c.Refused('synthetic')
  def hold(_):raise Held()
  ns={'hashlib':hashlib,'json':json,'decode':json.loads,'need':need,'sys':sys,'time':time,'protected':lambda *a:receipt,'os':types.SimpleNamespace(path=types.SimpleNamespace(lexists=lambda _:False)),'subprocess':types.SimpleNamespace(run=lambda *a,**k:types.SimpleNamespace(returncode=0,stdout=b'ActiveState=inactive\nMainPID=0\nControlPID=0\nJob=\nControlGroup=\n')),'ENV':{}}
  fn=next(n for n in ast.walk(ast.parse((pb.ROOT/'peer_server.py').read_bytes()))if isinstance(n,ast.FunctionDef)and n.name=='_serve');exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-full-rpc-sequence-no-host','exec'),ns)
  with patch.object(c,'RUN_DEADLINE',time.monotonic()+30),patch.dict(sys.modules,{'readiness_bridge':bridge,'native_peer':types.SimpleNamespace(VERIFIED_SOURCE_COMMIT=binding['source_commit']),'accepted_pair_validator':pair}):
   with self.assertRaises(Held):ns['_serve'](peer,plan,lambda _:next(read),responses.append,hold)
  return peer,calls,responses
 def test_full_backupN_qualifyN_backupN_refuses_before_reset(self):
  peer,calls,responses=self.run_sequence(['backup_proofs','qualify_backup_proofs','backup_proofs']);self.assertEqual(calls,['raw','qualify']);self.assertEqual([v['ok']for v in responses],[True,True,False]);self.assertEqual(len(peer.readiness_challenges),1);self.assertIsNotNone(peer.qualified_nonce)
 def test_full_direct_qualification_repeat_refuses(self):
  peer,calls,responses=self.run_sequence(['backup_proofs','qualify_backup_proofs','qualify_backup_proofs']);self.assertEqual(calls,['raw','qualify']);self.assertEqual([v['ok']for v in responses],[True,True,False])
if __name__=='__main__':unittest.main()
