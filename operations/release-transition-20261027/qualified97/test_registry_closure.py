"""Portable source-cache/transitive-closure tests; no host/Git/native operation."""
import ast,hashlib,json,sys,types,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import qualified_entry as entry
ROOT=Path(__file__).resolve().parent
class RegistryClosureTests(unittest.TestCase):
 def test_every_local_import_is_registered_and_loaded_before_use(self):
  known={p.stem for p in ROOT.glob('*.py')};runtime={n.removesuffix('.py') for n in entry.FILES}
  tree=ast.parse((ROOT/'qualified_entry.py').read_bytes())
  loop=next(n for n in ast.walk(tree) if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='name' and isinstance(n.iter,ast.Tuple))
  order=[ast.literal_eval(x) for x in loop.iter.elts];seen=set()
  for module in order:
   self.assertIn(module,runtime)
   for n in ast.walk(ast.parse((ROOT/(module+'.py')).read_bytes())):
    names=[a.name.split('.')[0] for a in n.names] if isinstance(n,ast.Import) else [n.module.split('.')[0]] if isinstance(n,ast.ImportFrom) and n.module else []
    for name in names:
     if name in known:self.assertIn(name,seen,('%s depends on uncached %s'%(module,name)))
   seen.add(module)
  self.assertIn('historical98_controls',seen);self.assertLess(order.index('historical98_controls'),order.index('accepted98_dependency'))
 def inputs(self):
  files={n:(ROOT/n).read_bytes() for n in sorted(entry.FILES)}
  manifest={'schema':'careertrellis.qualified97-source-manifest/v1','kind':'qualified97-new/v1','ops_commit':'2'*40,'files':{n:{'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in files.items()}}
  raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode();a=SimpleNamespace(approved_source_manifest_sha256=hashlib.sha256(raw).hexdigest(),ops_commit='2'*40)
  return files,raw,a
 def test_actual_loader_uses_each_authenticated_snapshot_once(self):
  files,manifest,a=self.inputs();reads={}
  def read(p,*args):
   name=p.name;reads[name]=reads.get(name,0)+1
   self.assertEqual(reads[name],1,'source reread rather than cached snapshot')
   return manifest if name=='source-manifest.json' else files[name]
  def git(args,**kwargs):return SimpleNamespace(returncode=0,stdout=files[args[-1].rsplit('/',1)[1]])
  grp=types.ModuleType('grp');grp.getgrnam=Mock(side_effect=AssertionError('native group query not permitted in source test'))
  with patch.dict(sys.modules,{'grp':grp}),patch.object(entry,'raw',side_effect=read),patch.object(entry.subprocess,'run',side_effect=git):
   admission,route=entry.load(a);self.assertTrue(admission.READY);self.assertTrue(route.READY) # Reviewed publication projection enables entrypoints only.
  self.assertEqual(set(reads),entry.FILES|{'source-manifest.json'});grp.getgrnam.assert_not_called()
 def test_wrong_transitive_bytes_refuse_before_module_execution(self):
  files,manifest,a=self.inputs();files['historical98_controls.py']+=b'\n#SYNTHETIC tamper\n'
  with patch.object(entry,'raw',side_effect=lambda p,*args:manifest if p.name=='source-manifest.json' else files[p.name]),patch.object(entry.subprocess,'run',side_effect=lambda args,**kwargs:SimpleNamespace(returncode=0,stdout=files[args[-1].rsplit('/',1)[1]])),self.assertRaises(RuntimeError):entry.load(a)
if __name__=='__main__':unittest.main()
