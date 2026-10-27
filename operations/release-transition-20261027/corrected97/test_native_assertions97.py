"""Pure genuine unittest flow with explicitly mocked source-auth seam; no native test executed."""
import io,sys,types,tempfile,unittest,inspect
from pathlib import Path
from unittest.mock import patch
import native_assertions97 as h
class FlowTests(unittest.TestCase):
 def result(self):return h.result_class(Path('.').absolute())(unittest.runner._WritelnDecorator(io.StringIO()),True,0)
 def cases(self,body=None):
  body=body or (lambda case:None)
  cls=type(h.CLASS,(unittest.TestCase,),dict({name:body for name in h.METHOD_AST},__module__=h.MODULE))
  return [cls(name) for name in h.METHOD_AST]
 def run_cases(self,cases=None):
  result=self.result()
  with patch.object(h,'authenticate_case',return_value='synthetic-source-auth-seam'):
   unittest.TestSuite(cases or self.cases()).run(result)
   return result,result.native_source_proof()
 def test_genuine_flow_positive(self):self.assertEqual(self.run_cases()[1]['native_ids_succeeded'],list(h.IDS))
 def test_manual_callbacks_refused(self):
  result=self.result();case=self.cases()[0]
  with patch.object(h,'authenticate_case'),self.assertRaises(h.ProofRefused):result.startTest(case)
 def test_instance_dispatch_noop_refused(self):
  case=self.cases()[0];case._callTestMethod=lambda method:None
  with patch.object(h,'authenticate_case'),self.assertRaises(h.ProofRefused):case.run(self.result())
 def test_assertEqual_class_noop_refused(self):
  with patch.object(unittest.TestCase,'assertEqual',lambda *a:None),patch.object(h,'authenticate_case'):
   with self.assertRaises(h.ProofRefused):self.cases()[0].run(self.result())
 def test_instance_assertion_noop_refused(self):
  case=self.cases()[0];case.assertTrue=lambda *a:None
  with patch.object(h,'authenticate_case'),self.assertRaises(h.ProofRefused):case.run(self.result())
 def test_equality_dispatch_mapping_substitution_refused(self):
  case=self.cases()[0];case._type_equality_funcs[dict]=lambda *a:None
  with patch.object(h,'authenticate_case'),self.assertRaises(h.ProofRefused):case.run(self.result())
 def test_missing_case_refused(self):
  with self.assertRaises(h.ProofRefused):self.run_cases(self.cases()[:1])
 def test_skip_refused(self):
  with self.assertRaises(h.ProofRefused):self.run_cases(self.cases(lambda case:case.skipTest('synthetic')))
 def test_actual_failure_refused(self):
  with self.assertRaises(h.ProofRefused):self.run_cases(self.cases(lambda case:case.fail('synthetic')))
 def test_duplicate_case_refused(self):
  cases=self.cases();cases.append(cases[0])
  with self.assertRaises(h.ProofRefused):self.run_cases(cases)
 def test_subclass_result_compatible(self):
  base=h.result_class(Path('.').absolute())
  class ActualResult(base):
   def addSuccess(self,test):super().addSuccess(test)
  result=ActualResult(unittest.runner._WritelnDecorator(io.StringIO()),True,0)
  with patch.object(h,'authenticate_case'):
   unittest.TestSuite(self.cases()).run(result);self.assertFalse(result.native_source_proof()['runtime_substitution'])
 def test_outcome_binding_required(self):
  result=self.result();case=self.cases()[0];result.current_case=case;result.native_started[case.id()]=case
  with patch.object(h,'authenticate_case'),self.assertRaises(h.ProofRefused):result.addSuccess(case)
class PublicSourceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).absolute();(self.root/'backend').mkdir();self.path=self.root/'backend/test_p0_regressions.py'
  self.raw=Path(__file__).parent.joinpath('fixtures/test_p0_regressions.py.source').read_bytes();self.path.write_bytes(self.raw)
  self.module=types.ModuleType(h.MODULE);self.module.__file__=str(self.path);self.module.tempfile=h.tempfile;self.module.subprocess=h.subprocess;self.module.Path=Path
  code=compile(self.raw,str(self.path),'exec',dont_inherit=True,optimize=0);cc=h.child_code(code,h.CLASS)
  methods={name:types.FunctionType(h.child_code(cc,name),self.module.__dict__) for name in h.METHOD_AST}
  methods['project_dir']=staticmethod(types.FunctionType(h.child_code(cc,'project_dir'),self.module.__dict__))
  self.cls=type(h.CLASS,(unittest.TestCase,),dict(methods,__module__=h.MODULE));self.module.DependencyLockTests=self.cls
  self.previous=sys.modules.get(h.MODULE);sys.modules[h.MODULE]=self.module;self.addCleanup(self.restore)
  # Windows metadata seam ONLY; actual helper Linux FD checks remain unchanged.
  r=patch.object(h,'source_bytes',side_effect=lambda path:Path(path).read_bytes());r.start();self.addCleanup(r.stop)
 def restore(self):
  if self.previous is None:sys.modules.pop(h.MODULE,None)
  else:sys.modules[h.MODULE]=self.previous
 def case(self):return self.cls(next(iter(h.METHOD_AST)))
 def test_actual_public_code_auth(self):self.assertEqual(h.authenticate_case(self.case(),self.root),next(iter(h.METHOD_AST)))
 def test_wrong_project_root_refused(self):
  self.cls.project_dir=staticmethod(lambda:Path('/wrong'))
  with self.assertRaises(h.ProofRefused):h.authenticate_case(self.case(),self.root)
 def test_alternating_instance_project_dir_never_called(self):
  case=self.case();calls=[]
  def alternating():
   calls.append(1);return self.root if len(calls)%2 else Path('/wrong')
  case.project_dir=alternating
  with self.assertRaises(h.ProofRefused):h.authenticate_case(case,self.root)
  self.assertEqual(calls,[])
 def test_instance_same_function_shadow_refused(self):
  case=self.case();case.project_dir=inspect.getattr_static(self.cls,'project_dir').__func__
  with self.assertRaises(h.ProofRefused):h.authenticate_case(case,self.root)
 def test_callable_equality_spoof_not_called(self):
  class Spoof:
   def __eq__(self,other):return True
   def __call__(inner):self.fail('must not call shadow')
  case=self.case();case.project_dir=Spoof()
  with self.assertRaises(h.ProofRefused):h.authenticate_case(case,self.root)
 def test_shared_code_wrong_globals_refused(self):
  original=inspect.getattr_static(self.cls,'project_dir').__func__
  other=dict(self.module.__dict__);other['__file__']='/wrong/backend/test_p0_regressions.py'
  self.cls.project_dir=staticmethod(types.FunctionType(original.__code__,other))
  with self.assertRaises(h.ProofRefused):h.authenticate_case(self.case(),self.root)
 def test_wrong_Path_binding_refused(self):
  self.module.Path=lambda *a:Path('/wrong')
  with self.assertRaises(h.ProofRefused):h.authenticate_case(self.case(),self.root)
 def test_wrong_module_file_refused(self):
  self.module.__file__='/wrong'
  with self.assertRaises(h.ProofRefused):h.authenticate_case(self.case(),self.root)
 def test_source_drift_refused(self):
  self.path.write_bytes(self.raw+b'\n')
  with self.assertRaises(h.ProofRefused):h.authenticate_case(self.case(),self.root)
 def test_native_method_substitution_refused(self):
  setattr(self.cls,next(iter(h.METHOD_AST)),lambda self:None)
  with self.assertRaises(h.ProofRefused):h.authenticate_case(self.case(),self.root)
 def test_temp_constructor_substitution_refused(self):
  with patch.object(h.tempfile,'TemporaryDirectory',object()),self.assertRaises(h.ProofRefused):h.authenticate_case(self.case(),self.root)
 def test_subprocess_substitution_refused(self):
  with patch.object(h.subprocess,'run',object()),self.assertRaises(h.ProofRefused):h.authenticate_case(self.case(),self.root)
if __name__=='__main__':unittest.main()
