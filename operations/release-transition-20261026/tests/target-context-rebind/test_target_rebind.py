"""Pure context guard and actual driver flow; no host/native/real receipt calls."""
import ast,io,json,types,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
ROOT=Path(__file__).parent;OLD='56c8b137fd6c6e778a9f09e3b04b925edea6270e';NEW='bc68ea0dc253425a44e268452130becdf35d764e'
def load(name,folder='TARGET_BYTES'):
 path=ROOT/folder/(name+'.py');m=types.ModuleType(name);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
class TargetRebindTests(unittest.TestCase):
 def test_full_AST_including_locations_only_target_literal(self):
  class Normalize(ast.NodeTransformer):
   def visit_Constant(self,node):
    if node.value==NEW:node.value=OLD
    return node
  for name in ('accepted_pair_validator.py','drive.py'):
   self.assertEqual(ast.dump(ast.parse((ROOT/'BASE_BYTES'/name).read_bytes()),include_attributes=True),ast.dump(Normalize().visit(ast.parse((ROOT/'TARGET_BYTES'/name).read_bytes())),include_attributes=True))
 def pair_guard(self,target):
  module=load('accepted_pair_validator');tree=ast.parse((ROOT/'TARGET_BYTES/accepted_pair_validator.py').read_bytes());function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='validate_pair')
  guard=next(n for n in function.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and any(isinstance(k,ast.Subscript) and isinstance(k.slice,ast.Constant) and k.slice.value=='target' for k in ast.walk(n)))
  scope=dict(module.__dict__);scope['context']={'baseline':module.BASE,'target':target,'correction':module.CORRECTION,'accepted_backup_label':module.LABEL,'checked_at':'SYNTHETIC_NOT_PARSED'}
  exec(compile(ast.Module(body=[guard],type_ignores=[]),'actual-context-guard-ONLY','exec'),scope)
 def test_actual_context_guard_new_target_positive(self):self.pair_guard(NEW)
 def test_actual_context_guard_old_target_negative(self):
  with self.assertRaises(RuntimeError):self.pair_guard(OLD)
 def test_historical_BASE_hashes_and_labels_unchanged(self):
  before=load('accepted_pair_validator','BASE_BYTES');after=load('accepted_pair_validator')
  for key in ('BASE','CORRECTION','LABEL','WEB_DIGEST','DB_DIGEST','FULL','MANIFEST','METADATA','TRANSACTION','PLAN'):self.assertEqual(getattr(before,key),getattr(after,key))
 def actual_driver(self,release=None,frame=None):
  module=load('drive');child=Mock();child.pid=123;child.stdin=io.StringIO();child.wait.return_value=0
  message=frame or {'kind':'terminal','receipt':{'result':'target_healthy','release':release,'build':'20261026.97'}}
  reader=Mock();reader.read.return_value=json.dumps(message)+'\n'
  with tempfile.TemporaryDirectory(dir=ROOT) as temp,patch.object(module,'start',return_value=child),patch.object(module,'Reader',return_value=reader):
   receipt=Path(temp)/'synthetic-metadata.jsonl';code=module.drive('f'*40,'1'*64,receipt);rows=[json.loads(x) for x in receipt.read_text().splitlines()]
  child.kill.assert_not_called();child.terminate.assert_not_called();return code,rows
 def test_actual_driver_new_target_success(self):
  code,rows=self.actual_driver(NEW);self.assertEqual(code,0);self.assertTrue(any(v.get('phase')=='terminal' and v.get('result')=='target_healthy' for v in rows))
 def test_actual_driver_old_target_refuses_unknown_no_kill(self):
  code,rows=self.actual_driver(OLD);self.assertEqual(code,1);self.assertTrue(any(v.get('phase')=='transport_or_protocol_unknown' and v['no_replay'] is True for v in rows))
 def test_actual_driver_unknown_protocol_keeps_unknown_no_kill(self):
  code,rows=self.actual_driver(frame={'kind':'synthetic_invalid'});self.assertEqual(code,1);self.assertTrue(any(v.get('no_replay') is True for v in rows))
 def test_actual_driver_recovery_refusal_classification_unchanged(self):
  code,rows=self.actual_driver(frame={'kind':'terminal','receipt':{'result':'refused_or_failed_inspect_no_replay'}});self.assertEqual(code,1);self.assertTrue(any(v.get('phase')=='terminal' and v.get('result')=='refused_or_failed_inspect_no_replay' for v in rows));self.assertFalse(any(v.get('phase')=='transport_or_protocol_unknown' for v in rows))
 def test_operator_public_path_and_host_config_unchanged(self):
  before=load('drive','BASE_BYTES');after=load('drive');self.assertEqual(before.REMOTE,after.REMOTE);self.assertEqual(before.HOSTS,after.HOSTS);self.assertEqual(before.SSH,after.SSH)
if __name__=='__main__':unittest.main()
