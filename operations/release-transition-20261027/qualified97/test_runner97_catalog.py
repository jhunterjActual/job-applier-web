"""Actual97 run_suite path, synthetic unittest cases; no app/provider imports."""
import ast,hashlib,json,os,sys,tempfile,unittest,contextlib
from pathlib import Path
from unittest.mock import Mock,patch
import fixture_relocation97 as q
class Runner97Tests(unittest.TestCase):
 def namespace(self):
  ns={'unittest':unittest,'os':os,'sys':sys,'hashlib':hashlib,'json':json,'repair_stage':lambda value:None,
   'need':lambda v:None if v else (_ for _ in ()).throw(RuntimeError('fixed')),'NATIVE_TESTS':{},
   'QUALIFIER':{'serial_suite':q.serial_suite,'qualified_suite':Mock(side_effect=AssertionError('nonbackend must not qualify'))}}
  tree=ast.parse(Path('run_checks_qualified97.py').read_text())
  nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in ('RecordedResult','run_suite')]
  exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual97suite>','exec'),ns)
  ns['RECORDED_SUCCESS_CODE']=ns['RecordedResult'].addSuccess.__code__;return ns
 def test_nonbackend_actual_serial_suite_runs_without_fixture_interception(self):
  ns=self.namespace()
  class Synthetic(unittest.TestCase):
   def test_synthetic(self):self.assertTrue(True)
  suite=unittest.TestSuite([Synthetic('test_synthetic')])
  with contextlib.nullcontext(str(Path(__file__).resolve().parent)) as work,patch.object(unittest.defaultTestLoader,'loadTestsFromNames',return_value=suite):
   result=ns['run_suite'](work,['synthetic'],qualify=False)
  self.assertEqual(result['tests'],1);self.assertEqual(result['successful_ids'],result['catalog_ids'])
  self.assertNotIn('fixture_qualification',result);ns['QUALIFIER']['qualified_suite'].assert_not_called()
 def test_duplicate_actual_catalog_refused_before_run(self):
  ns=self.namespace()
  class Synthetic(unittest.TestCase):
   def test_synthetic(self):self.fail('must not run')
  suite=unittest.TestSuite([Synthetic('test_synthetic'),Synthetic('test_synthetic')])
  with contextlib.nullcontext(str(Path(__file__).resolve().parent)) as work,patch.object(unittest.defaultTestLoader,'loadTestsFromNames',return_value=suite),self.assertRaises(RuntimeError):
   ns['run_suite'](work,['synthetic'],qualify=False)
if __name__=='__main__':unittest.main()
