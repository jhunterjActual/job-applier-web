import ast,hashlib,json,unittest
from pathlib import Path
import native_assertions97 as helper
import checks97
class ClosureTests(unittest.TestCase):
 def test_exact13_source_registry_and_enabled_entrypoints(self):
  root=Path(__file__).parent;registry=json.loads((root/'SOURCE_REGISTRY.json').read_text());self.assertEqual(len(registry['files']),13)
  tree=ast.parse((root/'qualified_entry.py').read_bytes());names=ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FILES' for t in n.targets)))
  self.assertEqual(names,set(registry['files']));self.assertNotIn('fixture_relocation97.py',names)
  for name,info in registry['files'].items():
   raw=(root/name).read_bytes();self.assertEqual(len(raw),info['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),info['sha256']);ast.parse(raw)
  for member in registry['readiness']:
   name,flag=member.split(':');tree=ast.parse((root/name).read_bytes());value=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==flag for t in n.targets));self.assertIs(ast.literal_eval(value),True)
 def test_native_validator_exact_helper_constants(self):
  self.assertEqual(checks97.MODULE_SHA,helper.MODULE_SHA);self.assertEqual(checks97.NATIVE_METHOD_AST_SHA,helper.METHOD_AST);self.assertEqual(checks97.NATIVE_IDS,list(helper.IDS))
 def test_original98_dependency_source_preserved(self):
  root=Path(__file__).parent;registry=json.loads((root/'SOURCE_REGISTRY.json').read_text())
  self.assertEqual(registry['files']['accepted98_dependency.py']['sha256'],'680451f765f3591594e0f1e16cb83fd055e13731f0cfa1def000a51e50adcf36')
  self.assertEqual(registry['files']['historical98_controls.py']['sha256'],'dfc9246cf028a060af4e2b02db83305212ef5bc9671c8f3391fced75261bb7ed')
if __name__=='__main__':unittest.main()
