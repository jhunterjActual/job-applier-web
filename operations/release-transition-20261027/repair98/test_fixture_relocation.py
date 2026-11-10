"""Pure mechanics/fault checks; no claim of native analyzer execution."""
from pathlib import Path
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import fixture_relocation as q

class SyntheticDirectory:
    name='/new-work/fixture-root/.systemd-verify-synthetic'
    enter_error=False;exit_error=False
    def __enter__(self):
        if self.enter_error:raise OSError('synthetic enter failure')
        return self.name
    def __exit__(self,*args):
        if self.exit_error:raise OSError('synthetic cleanup failure')

class QualificationTests(unittest.TestCase):
    def invoke(self,fault=None):
        release=Path('/readonly-release');fixture=Path('/new-work/fixture-root');directory=SyntheticDirectory()
        directory.enter_error=fault=='enter';directory.exit_error=fault=='exit'
        constructor=Mock(side_effect=OSError('synthetic constructor')) if fault=='constructor' else Mock(return_value=directory)
        tempfile=SimpleNamespace(TemporaryDirectory=constructor,untouched='value')
        real=Mock(side_effect=[SimpleNamespace(returncode=0),SimpleNamespace(returncode=1)])
        sub=SimpleNamespace(run=real);g={'tempfile':tempfile,'subprocess':sub};state={'calls':0,'constructor_returned':False,'method_returned':False,'binding_restored':False,'analyzer_returncodes':[]}
        def body():
            kwargs={'prefix':'.systemd-verify-','dir':release}
            if fault=='prefix':kwargs['prefix']='wrong'
            if fault=='dir':kwargs['dir']=Path('/other')
            if fault=='extra':kwargs['suffix']='extra'
            with g['tempfile'].TemporaryDirectory(**kwargs) as result:
                self.assertEqual(g['tempfile'].untouched,'value')
                command=['/usr/bin/systemd-analyze','--recursive-errors=no','--root='+str(Path(result)),'verify',*q.UNITS]
                options={'cwd':release,'env':{'PUBLIC_SYNTHETIC':'unchanged'},'capture_output':True,'text':True,'check':False}
                first=g['subprocess'].run(command,**options);self.assertEqual(first.returncode,0)
                second=g['subprocess'].run(command,**options);self.assertNotEqual(second.returncode,0)
                if fault=='assertion':raise AssertionError('synthetic assertion')
                if fault=='twice':g['tempfile'].TemporaryDirectory(prefix='.systemd-verify-',dir=release)
        stages=[]
        with patch.object(q,'_REAL_SUBPROCESS',sub),patch.object(q,'_REAL_RUN',real):
            if fault:
                with self.assertRaises((OSError,AssertionError,q.RelocationRefused)):
                    q._scoped_invoke(g,release,fixture,body,state,stages.append)
            else:q._scoped_invoke(g,release,fixture,body,state,stages.append)
        self.assertIs(g['tempfile'],tempfile);self.assertIs(g['subprocess'],sub);self.assertTrue(state['binding_restored'])
        self.assertIs(sub.run,real)
        return state,constructor,real,stages
    def test_exact_dir_only_original_constructor_and_arguments_preserved(self):
        state,ctor,run,stages=self.invoke()
        ctor.assert_called_once_with(prefix='.systemd-verify-',dir=Path('/new-work/fixture-root'))
        self.assertEqual(state['analyzer_returncodes'],[0,1]);self.assertTrue(state['method_returned']);self.assertEqual(run.call_count,2)
        self.assertEqual(stages,['F10_CONSTRUCTOR','F20_POSITIVE_ANALYZER','F30_NEGATIVE_ANALYZER','F40_METHOD_RETURNED'])
    def test_restore_constructor_context_assertion_and_second_call_faults(self):
        for fault in ('constructor','enter','exit','assertion','twice'):
            with self.subTest(fault=fault):
                state,*_=self.invoke(fault);self.assertFalse(state['method_returned'])
    def test_wrong_prefix_dir_extra_arguments_refuse_without_constructor(self):
        for fault in ('prefix','dir','extra'):
            with self.subTest(fault=fault):
                state,ctor,run,_=self.invoke(fault);ctor.assert_not_called();run.assert_not_called()
    def test_counters_or_unittest_success_alone_never_qualify(self):
        state,_,_,_=self.invoke();result=SimpleNamespace(success_ids=[q.TEST_ID],wasSuccessful=lambda:True)
        q.require_qualified_success(state,result)
        for key,value in [('calls',0),('method_returned',False),('binding_restored',False),('analyzer_returncodes',[]),('analyzer_returncodes',[0,0])]:
            with self.subTest(key=key),self.assertRaises(q.RelocationRefused):q.require_qualified_success(dict(state,**{key:value}),result)
        result.success_ids=[]
        with self.assertRaises(q.RelocationRefused):q.require_qualified_success(state,result)
    def test_serial_suite_rejects_custom_parallel_or_case_run(self):
        case=unittest.FunctionTestCase(lambda:None);suite=unittest.TestSuite([case]);q.serial_suite(suite)
        class OtherSuite(unittest.TestSuite):pass
        with self.assertRaises(q.RelocationRefused):q.serial_suite(OtherSuite([case]))
        case.run=lambda result:None
        with self.assertRaises(q.RelocationRefused):q.serial_suite(suite)
    def test_loaded_runtime_code_must_equal_pinned_compilation(self):
        expected=next(x for x in compile('def example():\n return 1\n','/fixed/test.py','exec').co_consts if isinstance(x,type((lambda:None).__code__)))
        q.same_runtime_code(expected,expected)
        changed=next(x for x in compile('def example():\n return 2\n','/fixed/test.py','exec').co_consts if isinstance(x,type(expected)))
        with self.assertRaises(q.RelocationRefused):q.same_runtime_code(changed,expected)
        with self.assertRaises(q.RelocationRefused):q.same_runtime_code(expected.replace(co_filename='/other'),expected)
    def test_every_fixture_ancestor_owner_mode_and_nosymlink(self):
        def good(path):return SimpleNamespace(st_mode=0o040700 if path==q.FIXTURE_ROOT else 0o040755,st_uid=65534 if path==q.FIXTURE_ROOT else 0)
        with patch.object(Path,'lstat',good):q.safe_fixture_ancestors(q.FIXTURE_ROOT)
        for bad in [SimpleNamespace(st_mode=0o040755,st_uid=1000),SimpleNamespace(st_mode=0o120777,st_uid=0),SimpleNamespace(st_mode=0o040777,st_uid=0)]:
            with patch.object(Path,'lstat',lambda path:bad if path==q.WORK else good(path)),self.assertRaises(q.RelocationRefused):q.safe_fixture_ancestors(q.FIXTURE_ROOT)
    def test_typed_receipt_requires_genuine_result_success_object(self):
        class Recorded(unittest.TextTestResult):
            def __init__(self):super().__init__(io.StringIO(),False,0);self.success_ids=[];self.success_objects=[]
            def addSuccess(self,test):self.success_ids.append(test.id());self.success_objects.append(test);super().addSuccess(test)
        case=unittest.FunctionTestCase(lambda:None);case.id=lambda:q.TEST_ID
        result=Recorded();result.addSuccess(case)
        state={'schema':'careertrellis.exact-fixture-relocation/v1','test_id':q.TEST_ID,'calls':1,'constructor_returned':True,
            'method_returned':True,'binding_restored':True,'analyzer_returncodes':[0,1],'serial_suite_verified':True,
            'runtime_code_verified':True,'fixture_ancestors_verified':True,'module_sha256':q.MODULE_SHA,
            'method_source_sha256':q.METHOD_SOURCE_SHA,'method_ast_sha256':q.METHOD_AST_SHA,
            'qualification':'one TemporaryDirectory dir relocated; unchanged body and real positive/negative analyzer calls','_case_object':case}
        out=q.typed_qualification(state,result,Recorded,Recorded.addSuccess.__code__);self.assertNotIn('_case_object',out)
        for key,value in [('calls',True),('runtime_code_verified','true'),('fixture_ancestors_verified',False),('analyzer_returncodes',[0,True])]:
            with self.subTest(key=key),self.assertRaises(q.RelocationRefused):q.typed_qualification(dict(state,**{key:value}),result,Recorded,Recorded.addSuccess.__code__)
        other=unittest.FunctionTestCase(lambda:None);other.id=lambda:q.TEST_ID
        with self.assertRaises(q.RelocationRefused):q.typed_qualification(dict(state,_case_object=other),result,Recorded,Recorded.addSuccess.__code__)

if __name__=='__main__':unittest.main()
