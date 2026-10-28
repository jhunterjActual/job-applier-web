import portable_bootstrap as pb
"""Supplement to immutable8853. Synthetic host observation boundaries, no native authority."""
import ast,copy,hashlib,json,pathlib,sys,time,types,unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch,Mock
BASE=pb.ROOT

import collector as c
import test_real_db_core as real
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):real.Tests.setUpClass()
 def setUp(self):self.temp=real.Tests().root();self.root=real.Tests().healthy(self.temp.__enter__());self.addCleanup(self.temp.__exit__,None,None,None)
 def actual_method(self,change=None):
  h=real.Tests.h;snapshot=real.f.snapshot('database');snapshot['units']['full_backup']['missed']=True;raw={'binding':{'schema':'careertrellis.preplan-scheduler-binding/v1','nonce':'1'*64,'source_commit':'2'*40,'boots':{'web':'11111111-1111-1111-1111-111111111111','db':'22222222-2222-2222-2222-222222222222'},'issued_at':datetime.now(timezone.utc).isoformat(),'phase':'preflight','mode':'idle','release':c.BASE,'build':'20261017.96'},'boot':'22222222-2222-2222-2222-222222222222','authority':{'domain':'synthetic-authority'},'custody':{'mode':'idle','policy_sha256':'a'*64,'owner':None},'original_snapshot':snapshot,'canonical_evaluation':h.evaluate_backup_health(snapshot,receipt_root=self.root),'receipt_inventory_sha256':c.inventory_digest(h,h.scan_receipts(root=self.root))};original=copy.deepcopy(raw);calls=[];reader=c.Reader();reader.stable=Mock(side_effect=c.Refused('synthetic')if change=='stable'else None)
  boots=iter([raw['boot'],'changedboot'if change=='boot'else raw['boot']]);path=lambda _:types.SimpleNamespace(read_text=lambda:next(boots));authority=iter([raw['authority'],{'domain':'changed'}if change=='authority_after'else raw['authority']]);custodies=[dict(raw['custody']),dict(raw['custody'])]
  if change in ('policy','owner'):custodies[0]['policy_sha256'if change=='policy'else'owner']='changed'
  if change=='policy_after':custodies[1]['policy_sha256']='changed'
  if change=='owner_after':custodies[1]['owner']='changed'
  if change=='authority_before':authority=iter([{'domain':'changed'}])
  custody=iter(custodies)
  def evaluate(snapshot,receipt_root):
   calls.append('actual-evaluator');result=h.evaluate_backup_health(snapshot,receipt_root=receipt_root)
   if change=='inventory_after':next(self.root.glob('*-pgbackrest_inventory-*.sha256')).write_bytes(b'changed-synthetic-manifest')
   return result
  core=types.SimpleNamespace(scan_receipts=h.scan_receipts,serialize_operation_receipt=h.serialize_operation_receipt,evaluate_backup_health=evaluate)
  if change=='inventory_before':next(self.root.glob('*-pgbackrest_inventory-*.sha256')).write_bytes(b'changed-synthetic-manifest')
  with patch.object(c.os,'geteuid',lambda:0,create=True),patch.object(c.socket,'gethostname',lambda:c.SPECS['db'][0]),patch.object(c,'Path',path),patch.object(c,'installed493',lambda _:{}),patch.object(c,'core',lambda *a:core),patch.object(c,'authority',lambda *a:next(authority)),patch.object(c,'native_custody',lambda *a:next(custody)),patch.object(c,'RECEIPTS',self.root),patch.object(c,'RUN_DEADLINE',-1 if change=='deadline'else time.monotonic()+30):
   try:return c.reevaluate_database(raw,raw['binding'],reader=reader),raw,original,calls,reader
   finally:self.assertEqual(raw,original)
 def test_actual_reevaluator_positive_real_core_and_raw_unchanged(self):
  derived,raw,original,calls,reader=self.actual_method();self.assertEqual(derived['derived_result']['result'],'ok');self.assertEqual(raw,original);self.assertEqual(calls,['actual-evaluator']);reader.stable.assert_called_once()
 def test_actual_reevaluator_observation_drift_and_original_deadline(self):
  for kind in ('authority_before','authority_after','policy','policy_after','owner','owner_after','boot','stable','deadline','inventory_before'):
   with self.subTest(kind=kind):
    with self.assertRaises(c.Refused):self.actual_method(kind)
 def test_actual_reevaluator_real_after_inventory_change(self):
  with self.assertRaises(c.Refused):self.actual_method('inventory_after')
 def backend(self,result='alert',response='known'):
  source=(pb.ROOT/'native_backend.py').read_bytes();fn=next(n for n in ast.walk(ast.parse(source))if isinstance(n,ast.FunctionDef)and n.name=='verify_fresh_proofs');calls=[];admissions=[];now=datetime.now(timezone.utc);request={'nonce':'a'*64,'phase':'preflight','plan_sha256':'b'*64};blob=b'{}';pin=hashlib.sha256(blob).hexdigest();health={'result':result,'observed_at':now.isoformat(),'evaluation_generation_sha256':'d'*64,'latest_receipts':{'full_backup':{'receipt_sha256':'f'*64}}};web=dict(health,result='ok',evaluation_generation_sha256='c'*64);repository={'checked_at':now.isoformat()};qualified={'evaluation_generation_sha256':'e'*64}
  bridge=types.ModuleType('readiness_bridge');bridge.binding=lambda *a:request;bridge.collect_local=lambda *a:web;bridge.verify_repository_binding=lambda *a:None;bridge.verify_generation_structure=lambda *a,**k:None;bridge.verify_generation=lambda *a,**k:None;bridge.load=lambda *a:{'collector':None}
  def admit(w,d,*a,**k):admissions.append(d);return {'nonce':request['nonce']}
  bridge.admit=admit
  pair=types.ModuleType('accepted_pair_validator');pair.validate_pair=lambda *a,**k:None;pair.LABEL='synthetic';pair.TRANSACTION='synthetic';pair.METADATA=pair.MANIFEST=pin;pair.FULL='f'*64
  peer=types.ModuleType('peer_server');peer.protected=lambda *a:blob
  def call(action,**kwargs):
   calls.append(action)
   if action=='backup_proofs':return {'health':health,'receipt_hex':blob.hex(),'repository':repository,'cleanup_owner_absent':True}
   return {'health':qualified}if response=='known'else{}
  state=types.SimpleNamespace(source_commit='1'*40,db=types.SimpleNamespace(call=call),protected=lambda *a:blob,assert_preserved=lambda:None)
  def need(v):
   if not v:raise c.Refused('synthetic')
  namespace={'time':time,'datetime':datetime,'timezone':timezone,'secrets':types.SimpleNamespace(token_hex=lambda _:'a'*64),'BASE':c.BASE,'TARGET':'bc68ea0dc253425a44e268452130becdf35d764e','hashlib':hashlib,'decode':json.loads,'need':need,'timestamp':lambda value:datetime.fromisoformat(value)}
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-full-backend-observation-method','exec'),namespace)
  with patch.dict(sys.modules,{'readiness_bridge':bridge,'accepted_pair_validator':pair,'peer_server':peer}):
   try:namespace['verify_fresh_proofs'](state,{'correction':'f'*40},'preflight',c.BASE,c.BASE)
   except c.Refused:
    if response=='known':raise
  return state,calls,admissions
 def test_full_backend_solelegacy_exact_two_rpc_and_complete_token(self):
  state,calls,admissions=self.backend();self.assertEqual(calls,['backup_proofs','qualify_backup_proofs']);self.assertEqual(len(admissions),1);self.assertEqual(state.last_generation_hashes['db'],'e'*64)
 def test_full_backend_healthy_one_rpc(self):
  state,calls,admissions=self.backend('ok');self.assertEqual(calls,['backup_proofs']);self.assertEqual(state.last_generation_hashes['db'],'d'*64)
 def test_full_backend_unknown_response_never_admits_or_sets_readiness(self):
  state,calls,admissions=self.backend(response='unknown');self.assertEqual(calls,['backup_proofs','qualify_backup_proofs']);self.assertEqual(admissions,[]);self.assertFalse(hasattr(state,'last_readiness'))
if __name__=='__main__':unittest.main()
