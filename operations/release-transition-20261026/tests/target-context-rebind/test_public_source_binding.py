"""Actual public package paths, not fixture-to-fixture comparisons."""
import ast,hashlib,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
PUBLIC=HERE.parent.parent
NEW='bc68ea0dc253425a44e268452130becdf35d764e'
OLD='56c8b137fd6c6e778a9f09e3b04b925edea6270e'
FAILED_OPS='e490115388d764af28b45692b61eba07fa899775'
class PublicSourceTests(unittest.TestCase):
 def test_actual_public_pair_source_matches_exact_reviewed_fixture(self):
  raw=(PUBLIC/'accepted_pair_validator.py').read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),'e4384b6ed229f5b03e3773e12549bb849bca898dac4eb4c1465ef8d3697c1b8b');self.assertEqual(raw,(HERE/'TARGET_BYTES/accepted_pair_validator.py').read_bytes())
 def test_actual_public_operator_matches_exact_reviewed_fixture(self):
  raw=(PUBLIC/'operator/drive.py').read_bytes();self.assertEqual(hashlib.sha256(raw).hexdigest(),'f166f00ce55220bdf2590ea14dd380605584ce455c4f8c48f1d7f7e61dfc61ae');self.assertEqual(raw,(HERE/'FRAMING_BYTES/drive.py').read_bytes())
 def test_complete35_membership_active_python_target_consistency(self):
  tree=ast.parse((PUBLIC/'peer_server.py').read_bytes());values={}
  for node in tree.body:
   if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('MODULES','SCRIPTS','READINESS_FILES'):values[node.targets[0].id]=ast.literal_eval(node.value)
  self.assertEqual(len(values['MODULES']),24);self.assertEqual(len(values['SCRIPTS']),4);self.assertEqual(len(values['READINESS_FILES']),7)
  names={n+'.py' for n in values['MODULES']}|set(values['SCRIPTS'])|{'one_shot_readiness/'+n for n in values['READINESS_FILES']};self.assertEqual(len(names),35);python_names={n for n in names if n.endswith('.py')};self.assertEqual(len(python_names),32)
  # Exact membership excludes every historical test fixture. Three generated
  # JSON bytes are not source-literal audit inputs or invented here.
  for name in sorted(python_names|{'operator/drive.py'}):
   with self.subTest(public_path=name):
    source=ast.parse((PUBLIC/name).read_bytes())
    for node in ast.walk(source):
     if isinstance(node,ast.Constant) and isinstance(node.value,str):self.assertNotIn(OLD,node.value);self.assertNotIn(FAILED_OPS,node.value)
    for node in source.body:
     if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TARGET' for t in node.targets):self.assertEqual(ast.literal_eval(node.value),NEW)
if __name__=='__main__':unittest.main()
