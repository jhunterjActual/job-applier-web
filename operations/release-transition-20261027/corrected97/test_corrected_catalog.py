import ast,hashlib,unittest
from pathlib import Path
class CatalogTests(unittest.TestCase):
 def test_fixed_candidate_catalog_actual_published_function(self):
  root=Path(__file__).parent;tree=ast.parse((root/'run_checks_qualified97.py').read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='modules_for')
  class CachedSource:
   def __truediv__(self,path):self.path=path;return self
   def read_text(self):assert self.path=='deploy/scripts/deploy-release.sh';return (root/'public-fixtures/target-deploy-release.sh').read_text()
  scope={'TARGET':'bc68ea0dc253425a44e268452130becdf35d764e','need':lambda x:None if x else (_ for _ in ()).throw(RuntimeError())}
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual candidate catalog','exec'),scope)
  modules=scope['modules_for'](CachedSource(),scope['TARGET']);self.assertEqual(len(modules),47);self.assertEqual(len(set(modules)),47);self.assertNotIn('test_match_api',modules)
  self.assertEqual(hashlib.sha256((root/'public-fixtures/target-deploy-release.sh').read_bytes()).hexdigest(),'1fe2a6c2527e46725a16f45fa55c4c1b7304f5740afe1cace34cbe165640da98')
if __name__=='__main__':unittest.main()
