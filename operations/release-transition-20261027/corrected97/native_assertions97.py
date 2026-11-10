"""Read-only genuine unittest-result observer. Source proposal; no native proof yet."""
import ast,hashlib,sys,types,unittest,tempfile,subprocess
from pathlib import Path
import inspect
CANDIDATE='bc68ea0dc253425a44e268452130becdf35d764e'
MODULE_SHA='db1604118386f23c9039fea1f3ccbec933f9a4d4e40ed7b9d9f37f7cde67fe13'
MODULE='test_p0_regressions'
CLASS='DependencyLockTests'
TEMPORARY_DIRECTORY=tempfile.TemporaryDirectory
SUBPROCESS_RUN=subprocess.run
# Capture trusted dispatch/assertions BEFORE importing suite modules. No runtime mutation.
STANDARD_NAMES=('run','id','_callTestMethod','_callSetUp','_callTearDown','doCleanups','_callCleanup','assertEqual','assertNotEqual','assertFalse','assertIn','assertTrue','fail','_baseAssertEqual','_getAssertEqualityFunc','setUp','tearDown','assertDictEqual','assertListEqual','assertTupleEqual','assertSetEqual','assertMultiLineEqual','assertSequenceEqual')
STANDARD={n:(getattr(unittest.TestCase,n),getattr(unittest.TestCase,n).__code__) for n in STANDARD_NAMES}
OUTCOME=unittest.case._Outcome
OUTCOME_INIT=OUTCOME.__init__
OUTCOME_EXEC=OUTCOME.testPartExecutor
OUTCOME_INIT_CODE=OUTCOME_INIT.__code__
OUTCOME_EXEC_CODE=OUTCOME_EXEC.__code__
TYPE_EQUALITY=dict(unittest.TestCase()._type_equality_funcs)
RUN_CODE=STANDARD['run'][1]
TEMP_INIT=tempfile.TemporaryDirectory.__init__
TEMP_INIT_CODE=TEMP_INIT.__code__
RUN_FUNC_CODE=SUBPROCESS_RUN.__code__
METHOD_AST={'test_hosted_authority_marker_is_one_way_and_runtime_checks_fail_closed': 'a6a08ee80e453655bdedc142bbaa2400a25032afd6a35639fddf863de3ae1682', 'test_backup_health_units_resolve_only_staged_executables': '7d4149105c78a3540878369367d697b71d275d04b9235c6d6f4a41b6e5c7ddd0'}

IDS=tuple(MODULE+'.'+CLASS+'.'+name for name in METHOD_AST)
class ProofRefused(RuntimeError):pass
def need(value):
 if not value:raise ProofRefused('CORRECTED97_NATIVE_SOURCE_PROOF_REFUSED')
def digest(raw):return hashlib.sha256(raw).hexdigest()
def source_bytes(path):
 import os,stat
 fields=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
 before=path.lstat();need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<2000000)
 fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
 try:
  first=os.fstat(fd)
  with os.fdopen(os.dup(fd),'rb') as stream:raw=stream.read(before.st_size+1)
  after=os.fstat(fd);leaf=path.lstat()
  need(len(raw)==before.st_size and all(getattr(before,k)==getattr(first,k)==getattr(after,k)==getattr(leaf,k) for k in fields));return raw
 finally:os.close(fd)
def child_code(code,name):
 found=[c for c in code.co_consts if isinstance(c,types.CodeType) and c.co_name==name];need(len(found)==1);return found[0]
def source_contract(raw,path):
 need(digest(raw)==MODULE_SHA);tree=ast.parse(raw,filename=str(path))
 classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==CLASS];need(len(classes)==1)
 methods={n.name:n for n in classes[0].body if isinstance(n,ast.FunctionDef) and n.name in METHOD_AST}
 need(set(methods)==set(METHOD_AST))
 for name,node in methods.items():need(digest(ast.dump(node,include_attributes=False).encode())==METHOD_AST[name])
 systemd=methods['test_backup_health_units_resolve_only_staged_executables']
 constructors=[n for n in ast.walk(systemd) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='TemporaryDirectory']
 need(len(constructors)==1 and not any(k.arg=='dir' for k in constructors[0].keywords))
 assertions=[]
 for n in ast.walk(systemd):
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('assertEqual','assertNotEqual'):
   if len(n.args)>=2 and isinstance(n.args[0],ast.Constant) and type(n.args[0].value) is int and n.args[0].value==0:
    r=n.args[1]
    if isinstance(r,ast.Attribute) and r.attr=='returncode' and isinstance(r.value,ast.Name):assertions.append((n.func.attr,r.value.id))
 need(('assertEqual','accepted') in assertions and ('assertNotEqual','rejected') in assertions)
 compiled=compile(raw,str(path),'exec',dont_inherit=True,optimize=0)
 return {name:child_code(child_code(compiled,CLASS),name) for name in METHOD_AST}

def same_code(left,right):
 fields=('co_argcount','co_posonlyargcount','co_kwonlyargcount','co_nlocals','co_stacksize','co_flags','co_code','co_names','co_varnames','co_filename','co_name','co_qualname','co_firstlineno','co_linetable','co_exceptiontable','co_freevars','co_cellvars')
 if not all(getattr(left,k)==getattr(right,k) for k in fields) or len(left.co_consts)!=len(right.co_consts):return False
 def same(a,b):
  if type(a) is not type(b):return False
  if isinstance(a,types.CodeType):return same_code(a,b)
  if isinstance(a,tuple):return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
  return a==b
 return all(same(a,b) for a,b in zip(left.co_consts,right.co_consts))

def authenticate_dispatch(case):
 for name,(function,code) in STANDARD.items():
  bound=getattr(case,name);need(isinstance(bound,types.MethodType) and bound.__func__ is function and function.__code__ is code and getattr(unittest.TestCase,name) is function)
 need(unittest.case._Outcome is OUTCOME and OUTCOME.__init__ is OUTCOME_INIT and OUTCOME.testPartExecutor is OUTCOME_EXEC and OUTCOME_INIT.__code__ is OUTCOME_INIT_CODE and OUTCOME_EXEC.__code__ is OUTCOME_EXEC_CODE)
 need(type(case._type_equality_funcs) is dict and case._type_equality_funcs==TYPE_EQUALITY)

def require_run_frame(case,result,success=False):
 frame=sys._getframe(1);found=None
 for _ in range(8):
  if frame is None:break
  if frame.f_code is RUN_CODE and frame.f_locals.get('self') is case and frame.f_locals.get('result') is result:found=frame;break
  frame=frame.f_back
 need(found is not None)
 if success:
  outcome=getattr(case,'_outcome',None);need(type(outcome) is OUTCOME and outcome.result is result and outcome.success is True and found.f_locals.get('outcome') is outcome and outcome.expecting_failure is False)

def authenticate_case(case,source_root):
 authenticate_dispatch(case)
 need(type(case.id()) is str and case.id() in IDS);name=case.id().rsplit('.',1)[1];need(case._testMethodName==name)
 path=Path(source_root).absolute()/'backend'/'test_p0_regressions.py';raw=source_bytes(path);codes=source_contract(raw,path)
 module=sys.modules.get(MODULE);need(isinstance(module,types.ModuleType) and Path(module.__file__).absolute()==path)
 need(type(case).__module__==MODULE and type(case).__name__==CLASS and type(case) is getattr(module,CLASS))
 method=getattr(case,name);need(isinstance(method,types.MethodType) and method.__self__ is case)
 function=method.__func__;need(function.__globals__ is module.__dict__ and function.__module__==MODULE and function.__closure__ is None)
 need(same_code(function.__code__,codes[name]))
 need(module.__dict__.get('tempfile') is tempfile and tempfile.TemporaryDirectory is TEMPORARY_DIRECTORY
  and module.__dict__.get('subprocess') is subprocess and subprocess.run is SUBPROCESS_RUN and SUBPROCESS_RUN.__code__ is RUN_FUNC_CODE
  and tempfile.TemporaryDirectory.__init__ is TEMP_INIT and TEMP_INIT.__code__ is TEMP_INIT_CODE)
 need(module.__dict__.get('Path') is Path)
 need('project_dir' not in vars(case))
 descriptor=inspect.getattr_static(type(case),'project_dir');need(type(descriptor) is staticmethod)
 project=descriptor.__func__;resolved=case.project_dir;need(resolved is project)
 expected=child_code(child_code(compile(raw,str(path),'exec',dont_inherit=True,optimize=0),CLASS),'project_dir')
 need(project.__globals__ is module.__dict__ and same_code(project.__code__,expected) and resolved()==Path(source_root).absolute())
 return name

def result_class(source_root):
 """Use unittest.TextTestRunner(resultclass=result_class(actual_source_root)); no test substitution."""
 root=Path(source_root).absolute()
 class SourceBoundResult(unittest.TextTestResult):
  def __init__(self,*args,**kwargs):
   super().__init__(*args,**kwargs);self.native_started={};self.native_success=set();self.native_bad=set();self.native_finished=set();self.current_case=None
  def startTest(self,test):
   need(self.current_case is None);self.current_case=test
   if test.id() in IDS:authenticate_dispatch(test);require_run_frame(test,self)
   if test.id() in IDS:
    need(test.id() not in self.native_started);authenticate_case(test,root);self.native_started[test.id()]=test
   super().startTest(test)
  def stopTest(self,test):
   need(self.current_case is test)
   if test.id() in IDS:self.native_finished.add(test.id())
   self.current_case=None;super().stopTest(test)
  def addSuccess(self,test):
   need(self.current_case is test)
   if test.id() in IDS:
    need(self.native_started.get(test.id()) is test);authenticate_dispatch(test);require_run_frame(test,self,success=True);authenticate_case(test,root);self.native_success.add(test.id())
   super().addSuccess(test)
  def addSkip(self,test,reason):
   if test.id() in IDS:self.native_bad.add(test.id())
   super().addSkip(test,reason)
  def addFailure(self,test,err):
   if test.id() in IDS:self.native_bad.add(test.id())
   super().addFailure(test,err)
  def addError(self,test,err):
   if test.id() in IDS:self.native_bad.add(test.id())
   super().addError(test,err)
  def addSubTest(self,test,subtest,err):
   if test.id() in IDS and err is not None:self.native_bad.add(test.id())
   super().addSubTest(test,subtest,err)
  def native_source_proof(self):
   need(self.current_case is None and self.wasSuccessful() and not self.native_bad and set(self.native_started)==self.native_success==self.native_finished==set(IDS))
   for test in self.native_started.values():authenticate_dispatch(test);authenticate_case(test,root)
   return {'schema':'careertrellis.corrected-fixture-native-proof/v1','source_kind':'corrected-fixture97-new/v1',
    'candidate':CANDIDATE,'module_sha256':MODULE_SHA,'method_ast_sha256':dict(METHOD_AST),'native_ids_succeeded':list(IDS),
    'assertion_evidence':'source_authenticated_positive_zero_and_missing_helper_nonzero_assertions_succeeded','runtime_substitution':False}
 return SourceBoundResult
