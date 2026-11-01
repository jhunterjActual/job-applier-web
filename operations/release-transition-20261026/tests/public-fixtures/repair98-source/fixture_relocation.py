"""Exact one-test module-local fixture qualification; no app/test/global tempfile mutation."""
import ast
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest

REV='f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
RELEASE=Path('/opt/careertrellis/staging/releases')/REV
WORK=Path('/opt/careertrellis/staging/preparation98-repair-work-20261027/repair98-fixture-g1')
FIXTURE_ROOT=WORK/'fixture-root'
TEST_ID='test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables'
METHOD='test_backup_health_units_resolve_only_staged_executables'
MODULE_SHA='341e728a03f4f76bfa04c64aec612fae9fd87dc831266e547d9f42c7f0a42ab4'
METHOD_SOURCE_SHA='584aec03af3f6347fbce3f10f56b449deeea0925f3365e6f28a5d3cfcbd85cac'
METHOD_AST_SHA='ad2abbb01e5d212e3045b604105c2dc788281d56d64735887a827caad841af73'
_REAL_SUBPROCESS=subprocess
_REAL_RUN=subprocess.run
_REAL_TEMPFILE=tempfile
_REAL_TEMPORARY_DIRECTORY=tempfile.TemporaryDirectory
_SERIAL_SUITE_RUN=unittest.TestSuite.run
_SERIAL_CASE_RUN=unittest.TestCase.run
_SERIAL_ASYNC_CASE_RUN=unittest.IsolatedAsyncioTestCase.run
QUALIFICATION='one TemporaryDirectory dir relocated; unchanged body and real positive/negative analyzer calls'
UNITS=('careertrellis-pgbackrest-full.service','careertrellis-pgbackrest-full.timer',
    'careertrellis-pgbackrest-differential.service','careertrellis-pgbackrest-differential.timer',
    'careertrellis-pgbackrest-onedrive.service','careertrellis-pgbackrest-onedrive.timer',
    'careertrellis-pgbackrest-inventory.service','careertrellis-pgbackrest-inventory.timer',
    'careertrellis-pgbackrest-restore.service','careertrellis-pgbackrest-restore.timer',
    'careertrellis-backup-health.service','careertrellis-backup-health.timer','careertrellis-backup-health-alert@.service')

class RelocationRefused(RuntimeError):pass
def need(value):
    if not value:raise RelocationRefused('EXACT_ONE_FIXTURE_QUALIFICATION_REFUSED')
def sha(raw):return hashlib.sha256(raw).hexdigest()

def same_runtime_code(actual,expected):
    need(isinstance(actual,types.CodeType) and isinstance(expected,types.CodeType)
        and actual==expected and actual.co_filename==expected.co_filename
        and actual.co_firstlineno==expected.co_firstlineno and actual.co_qualname==expected.co_qualname)

def safe_fixture_ancestors(fixture_root):
    need(fixture_root==FIXTURE_ROOT)
    for path in (fixture_root,*fixture_root.parents):
        s=path.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid in (0,65534) and not s.st_mode&0o022)
    s=fixture_root.lstat();need(s.st_uid==65534 and stat.S_IMODE(s.st_mode)==0o700)

def serial_suite(suite):
    need(type(suite) is unittest.TestSuite and suite.run.__func__ is _SERIAL_SUITE_RUN)
    for item in suite:
        if isinstance(item,unittest.TestSuite):serial_suite(item)
        else:
            run=getattr(item.run,'__func__',None)
            need(isinstance(item,unittest.TestCase) and (run is _SERIAL_CASE_RUN
                or isinstance(item,unittest.IsolatedAsyncioTestCase) and run is _SERIAL_ASYNC_CASE_RUN))

def authenticate(case,method,fixture_root):
    need(sys.platform=='linux' and sys.flags.optimize==0 and os.geteuid()==65534 and case.id()==TEST_ID
        and fixture_root==FIXTURE_ROOT and threading.current_thread() is threading.main_thread()
        and threading.active_count()==1)
    cls=type(case);need(cls.__module__=='test_p0_regressions' and cls.__name__=='DependencyLockTests'
        and method.__self__ is case and cls.__dict__[METHOD] is method.__func__)
    filename=Path(method.__func__.__code__.co_filename);need(filename==RELEASE/'backend/test_p0_regressions.py')
    for parent in filename.parents:
        s=parent.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)
    fd=os.open(filename,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        s=os.fstat(fd);need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=2097152)
        with os.fdopen(os.dup(fd),'rb') as stream:raw=stream.read(s.st_size+1)
        after=os.fstat(fd);need(len(raw)==s.st_size and (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns))
    finally:os.close(fd)
    need(sha(raw)==MODULE_SHA)
    tree=ast.parse(raw);clsnode=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DependencyLockTests')
    node=next(n for n in clsnode.body if isinstance(n,ast.FunctionDef) and n.name==METHOD)
    body=b''.join(raw.splitlines(keepends=True)[node.lineno-1:node.end_lineno])
    need(sha(body)==METHOD_SOURCE_SHA and sha(ast.dump(node,include_attributes=False).encode())==METHOD_AST_SHA)
    compiled=compile(raw,str(filename),'exec',dont_inherit=True,optimize=0)
    class_code=next(value for value in compiled.co_consts if isinstance(value,types.CodeType) and value.co_name=='DependencyLockTests')
    method_code=next(value for value in class_code.co_consts if isinstance(value,types.CodeType) and value.co_name==METHOD)
    same_runtime_code(method.__func__.__code__,method_code)
    module=sys.modules.get('test_p0_regressions')
    need(module is not None and method.__func__.__globals__ is module.__dict__
        and module.__dict__['tempfile'] is _REAL_TEMPFILE
        and _REAL_TEMPFILE.TemporaryDirectory is _REAL_TEMPORARY_DIRECTORY)
    safe_fixture_ancestors(fixture_root)
    return method.__func__.__globals__

class _ModuleProxy:
    def __init__(self,original,release,fixture_root,state,stage):
        self.original=original;self.release=release;self.fixture_root=fixture_root;self.state=state
        self.stage=stage
    def __getattr__(self,name):
        if name=='TemporaryDirectory':return self.construct
        return getattr(self.original,name)
    def construct(self,*args,**kwargs):
        need(not args and set(kwargs)=={'prefix','dir'} and kwargs['prefix']=='.systemd-verify-'
            and Path(kwargs['dir'])==self.release and self.state['calls']==0)
        self.state['calls']+=1
        changed=dict(kwargs);changed['dir']=self.fixture_root
        self.stage('F10_CONSTRUCTOR')
        value=self.original.TemporaryDirectory(**changed)
        self.state['constructor_returned']=True
        self.fixture_path=Path(value.name)
        return value

class _AnalyzerObserver:
    def __init__(self,original,fixture,state):self.original=original;self.fixture=fixture;self.state=state
    def __getattr__(self,name):return self.run if name=='run' else getattr(self.original,name)
    def run(self,*args,**kwargs):
        need(self.original is _REAL_SUBPROCESS and self.original.run is _REAL_RUN
            and len(args)==1 and type(args[0]) is list and len(self.state['analyzer_returncodes'])<2)
        command=args[0]
        need(command==['/usr/bin/systemd-analyze','--recursive-errors=no','--root='+str(self.fixture.fixture_path),'verify',*UNITS]
            and set(kwargs)=={'cwd','env','capture_output','text','check'} and Path(kwargs['cwd'])==self.fixture.release
            and type(kwargs['env']) is dict and kwargs['capture_output'] is True and kwargs['text'] is True and kwargs['check'] is False)
        # Delegate ORIGINAL args/kwargs unchanged; never simulate either analyzer result.
        self.fixture.stage('F20_POSITIVE_ANALYZER' if not self.state['analyzer_returncodes'] else 'F30_NEGATIVE_ANALYZER')
        result=_REAL_RUN(*args,**kwargs)
        need(self.original.run is _REAL_RUN and type(result.returncode) is int and 0<=result.returncode<=255)
        self.state['analyzer_returncodes'].append(result.returncode)
        return result

def _scoped_invoke(globals_dict,release,fixture_root,callback,state,stage_callback=None):
    original=globals_dict['tempfile'];need(not isinstance(original,_ModuleProxy))
    original_subprocess=globals_dict['subprocess'];need(original_subprocess is _REAL_SUBPROCESS and original_subprocess.run is _REAL_RUN)
    stage=stage_callback or (lambda value:None)
    proxy=_ModuleProxy(original,release,fixture_root,state,stage)
    globals_dict['tempfile']=proxy
    globals_dict['subprocess']=_AnalyzerObserver(original_subprocess,proxy,state)
    try:
        result=callback()
        need(state['calls']==1 and state['constructor_returned'] is True and len(state['analyzer_returncodes'])==2
            and state['analyzer_returncodes'][0]==0 and state['analyzer_returncodes'][1]>0)
        state['method_returned']=True;stage('F40_METHOD_RETURNED');return result
    finally:
        # Restore even constructor, context-manager, assertion or bookkeeping failure.
        globals_dict['tempfile']=original;globals_dict['subprocess']=original_subprocess
        state['binding_restored']=globals_dict['tempfile'] is original and globals_dict['subprocess'] is original_subprocess

def flatten(suite):
    for item in suite:
        if hasattr(item,'_tests'):yield from flatten(item)
        else:yield item

@contextmanager
def qualified_suite(suite,fixture_root,stage_callback=None):
    serial_suite(suite)
    matches=[case for case in flatten(suite) if case.id()==TEST_ID]
    need(len(matches)==1);case=matches[0];need('_callTestMethod' not in case.__dict__)
    original_call=case._callTestMethod
    state={'schema':'careertrellis.exact-fixture-relocation/v1','test_id':TEST_ID,'calls':0,
        'constructor_returned':False,'method_returned':False,'binding_restored':False,'analyzer_returncodes':[],
        'serial_suite_verified':True,'runtime_code_verified':False,'fixture_ancestors_verified':False,
        'module_sha256':MODULE_SHA,'method_source_sha256':METHOD_SOURCE_SHA,'method_ast_sha256':METHOD_AST_SHA,
        'qualification':QUALIFICATION,'_case_object':case}
    def invoke(method):
        g=authenticate(case,method,fixture_root)
        state['runtime_code_verified']=True;state['fixture_ancestors_verified']=True
        return _scoped_invoke(g,RELEASE,fixture_root,lambda:original_call(method),state,stage_callback)
    case._callTestMethod=invoke
    try:yield state
    finally:
        del case.__dict__['_callTestMethod']

def require_qualified_success(state,result):
    need(state['calls']==1 and state['constructor_returned'] is True and state['method_returned'] is True
        and state['binding_restored'] is True and len(state['analyzer_returncodes'])==2
        and state['analyzer_returncodes'][0]==0 and state['analyzer_returncodes'][1]>0
        and TEST_ID in result.success_ids and result.wasSuccessful())

def typed_qualification(state,result,expected_result_type,expected_add_success_code):
    require_qualified_success(state,result)
    need(type(result) is expected_result_type and isinstance(result,unittest.TextTestResult)
        and result.addSuccess.__func__.__code__ is expected_add_success_code
        and any(test is state['_case_object'] for test in result.success_objects))
    keys={'schema','test_id','calls','constructor_returned','method_returned','binding_restored','analyzer_returncodes',
        'serial_suite_verified','runtime_code_verified','fixture_ancestors_verified','module_sha256','method_source_sha256',
        'method_ast_sha256','qualification','_case_object'}
    need(type(state) is dict and set(state)==keys and state['schema']=='careertrellis.exact-fixture-relocation/v1'
        and state['test_id']==TEST_ID and type(state['calls']) is int and state['calls']==1
        and all(state[k] is True for k in ('constructor_returned','method_returned','binding_restored',
            'serial_suite_verified','runtime_code_verified','fixture_ancestors_verified'))
        and state['module_sha256']==MODULE_SHA and state['method_source_sha256']==METHOD_SOURCE_SHA
        and state['method_ast_sha256']==METHOD_AST_SHA and state['qualification']==QUALIFICATION
        and type(state['analyzer_returncodes']) is list
        and all(type(code) is int for code in state['analyzer_returncodes']))
    return {key:value for key,value in state.items() if key!='_case_object'}
