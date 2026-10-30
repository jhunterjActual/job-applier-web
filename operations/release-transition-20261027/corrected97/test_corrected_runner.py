"""Pure actual-runner flow controls; no app, socket, native unit or provider."""
import ast,hashlib,json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
class RunnerTests(unittest.TestCase):
 def namespace(self):
  ns={'unittest':unittest,'os':os,'sys':sys,'hashlib':hashlib,'json':json,'repair_stage':lambda value:None,'need':lambda v:None if v else (_ for _ in ()).throw(RuntimeError('fixed')),'NATIVE_TESTS':{}}
  tree=ast.parse(Path(__file__).with_name('run_checks_qualified97.py').read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_suite');exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual corrected runner','exec'),ns);return ns
 def test_actual_serial_result_runs_without_any_helper_for_nonbackend(self):
  class Case(unittest.TestCase):
   def test_ok(self):self.assertTrue(True)
  ns=self.namespace();suite=unittest.TestSuite([Case('test_ok')])
  with patch.object(unittest.defaultTestLoader,'loadTestsFromNames',return_value=suite):value=ns['run_suite'](Path(__file__).parent,['synthetic'])
  self.assertEqual(value['tests'],1);self.assertEqual(value['successful_ids'],value['catalog_ids']);self.assertNotIn('native_source_proof',value)
 def test_duplicate_refused_before_execution(self):
  class Case(unittest.TestCase):
   def test_no(self):self.fail('must not run')
  ns=self.namespace();suite=unittest.TestSuite([Case('test_no'),Case('test_no')])
  with patch.object(unittest.defaultTestLoader,'loadTestsFromNames',return_value=suite),self.assertRaises(RuntimeError):ns['run_suite'](Path(__file__).parent,['synthetic'])
 def test_backend_observer_gets_genuine_addSuccess(self):
  class Case(unittest.TestCase):
   def test_ok(self):self.assertTrue(True)
  seen=[]
  class Observer(unittest.TextTestResult):
   def addSuccess(self,test):seen.append(test.id());super().addSuccess(test)
   def native_source_proof(self):return {'synthetic_observed_ids':seen[:]}
  ns=self.namespace();ns['NATIVE_HELPER']={'result_class':lambda root:Observer};suite=unittest.TestSuite([Case('test_ok')])
  with patch.object(unittest.defaultTestLoader,'loadTestsFromNames',return_value=suite):value=ns['run_suite'](Path(__file__).parent,['synthetic'],native=True)
  self.assertEqual(value['native_source_proof']['synthetic_observed_ids'],value['successful_ids'])
 def test_actual_runner_with_helper02_genuine_unittest_flow(self):
  import native_assertions97 as helper
  cls=type(helper.CLASS,(unittest.TestCase,),dict({name:(lambda case:None) for name in helper.METHOD_AST},__module__=helper.MODULE))
  suite=unittest.TestSuite([cls(name) for name in helper.METHOD_AST]);ns=self.namespace();ns['NATIVE_HELPER']={'result_class':helper.result_class};ns['NATIVE_TESTS']={str(i):name for i,name in enumerate(helper.IDS)}
  with patch.object(unittest.defaultTestLoader,'loadTestsFromNames',return_value=suite),patch.object(helper,'authenticate_case',return_value='synthetic-source-auth-seam'):
   value=ns['run_suite'](Path(__file__).parent,['synthetic'],native=True)
  self.assertEqual(value['native_source_proof']['native_ids_succeeded'],list(helper.IDS));self.assertEqual(value['successful_ids'],list(helper.IDS))
 def test_helper_loaded_before_any_suite_import(self):
  tree=ast.parse(Path(__file__).with_name('run_checks_qualified97.py').read_bytes());main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
  load=[n.lineno for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='load_native_helper'];suites=[n.lineno for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='run_suite']
  self.assertEqual(len(load),1);self.assertLess(load[0],min(suites))
 def test_old_qualification_mechanism_absent(self):
  raw=Path(__file__).with_name('run_checks_qualified97.py').read_text()
  for old in ('qualified_suite','typed_qualification','FIXTURE_ROOT','load_qualifier','fixture_relocation','analyzer_returncodes'):self.assertNotIn(old,raw)
if __name__=='__main__':unittest.main()
