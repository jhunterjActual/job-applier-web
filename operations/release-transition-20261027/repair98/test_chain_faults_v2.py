"""Execute actual owner/guard control flow with synthetic OS boundaries; no host operations."""
import ast
import copy
import datetime as dt
import io
import json
from pathlib import Path,PurePosixPath
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import repair_contract as c

class Retained(BaseException):pass
class OwnerTransform(ast.NodeTransformer):
    def visit_Expr(self,node):
        if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and isinstance(node.value.func.value,ast.Name) and node.value.func.value.id=='os' and node.value.func.attr=='_exit':
            return ast.copy_location(ast.Return(value=ast.Tuple(elts=[ast.Constant('exit'),node.value.args[0]],ctx=ast.Load())),node)
        return self.generic_visit(node)
    def visit_While(self,node):
        if isinstance(node.test,ast.Constant) and node.test.value is True:
            # Observable test boundary at the production persistent hold; no production escape is added.
            node.body=[ast.Raise(exc=ast.Call(func=ast.Name(id='Retained',ctx=ast.Load()),args=[],keywords=[]),cause=None)]
        return self.generic_visit(node)

def owner_fixture(fault=None):
    tree=ast.parse(Path('repair_owner.py').read_text());own=copy.deepcopy(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='own'))
    own=OwnerTransform().visit(own);ast.fix_missing_locations(own)
    storage={};events=[];pipes=[SimpleNamespace(close=Mock()),SimpleNamespace(close=Mock())]
    child=SimpleNamespace(pid=502,stdout=pipes[0],stderr=pipes[1],wait=Mock(return_value=1 if fault=='nonzero' else 0))
    candidate={'checks_sha256':'a'*64,'unit_terminal_sha256':'b'*64,'runtime_after_sha256':'c'*64}
    coordinator={'pid':502,'start_ticks':'WRONG' if fault=='birth' else '5020','boot_id':'boot'}
    def read(path):
        if path.name=='candidate.json':
            if fault=='candidate':raise FileNotFoundError()
            return c.canonical(candidate)
        if path.name=='coordinator.json':return c.canonical(coordinator)
        return c.canonical(storage[path.name])
    def record(name,value):
        if fault=='ledger' and name=='child-spawn.json':raise OSError('synthetic journal failure')
        storage[name]=value;events.append(name);return c.sha(c.canonical(value))
    class Thread:
        def __init__(self,target,args,daemon):self.args=args
        def start(self):
            self.args[2]['eof']=fault!='pipe';self.args[2]['error']=fault=='pipe'
        def join(self):pass
    ns={'Retained':Retained,'load_controls':lambda *a:({'source_manifest_sha256':'d'*64},{},{},{},{}),
        'os':SimpleNamespace(setsid=lambda:None,open=lambda *a:7,dup2=lambda *a:None,close=lambda *a:None,getpid=lambda:501,O_RDWR=2),
        'birth':lambda pid:str(pid)+'0','boot':lambda:'boot','utc':lambda:dt.datetime.now(dt.timezone.utc),
        'record':record,'create':lambda path,value:record(path.name,value),'read':read,'closed':lambda name,value:value,
        'decode':c.decode,'sha':c.sha,'need':c.need,'drain':lambda *a:None,'threading':SimpleNamespace(Thread=Thread),
        'subprocess':SimpleNamespace(Popen=Mock(return_value=child),DEVNULL=-3,PIPE=-1),
        'STAGE':PurePosixPath('/fixed/source'),'DOMAIN':PurePosixPath('/fixed/ledger'),'ATTEMPT_ID':c.ATTEMPT_ID,
        'unit_state':lambda unit:{'LoadState':'not-found','Job':''},'cgroup_state':lambda *a:{'processes':0,'populated':0},
        'require_unit_absent':Mock(),'UNIT':'fixed-repair-unit'}
    exec(compile(ast.Module(body=[own],type_ignores=[]),'<actual-owner-flow>','exec'),ns)
    return ns,storage,events,child,pipes

class ChainFaultTests(unittest.TestCase):
    def test_owner_nonzero_child_never_writes_acceptance_terminal(self):
        ns,store,events,child,pipes=owner_fixture('nonzero')
        self.assertEqual(ns['own'](SimpleNamespace(approved_plan_sha256='p',approved_decision_sha256='d')),('exit',1))
        self.assertIn('owner-failed-terminal.json',store);self.assertNotIn('owner-terminal.json',store)
    def test_owner_pipe_birth_candidate_or_postspawn_ledger_faults_retain(self):
        for fault in ('pipe','birth','candidate','ledger'):
            ns,store,events,child,pipes=owner_fixture(fault)
            with self.subTest(fault=fault),self.assertRaises(Retained):ns['own'](SimpleNamespace(approved_plan_sha256='p',approved_decision_sha256='d'))
            ns['subprocess'].Popen.assert_called_once()
            self.assertNotIn('owner-terminal.json',store)
            for pipe in pipes:pipe.close.assert_not_called()
    def test_actual_drain_read_failure_keeps_unknown_pipe_open(self):
        tree=ast.parse(Path('repair_owner.py').read_text());drain=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='drain')
        state={'retained_bytes':0,'discarded_bytes':0,'eof':False,'error':False};pipe=SimpleNamespace(read=Mock(side_effect=OSError()),close=Mock())
        ns={'os':SimpleNamespace(open=lambda *a:7,fdopen=lambda *a:io.BytesIO(),O_WRONLY=1,O_CREAT=2,O_EXCL=4,O_NOFOLLOW=8,fsync=lambda *a:None),
            'sync_dir':lambda p:None,'DOMAIN':PurePosixPath('/fixed')}
        exec(compile(ast.Module(body=[drain],type_ignores=[]),'<actual-drain>','exec'),ns)
        ns['drain'](pipe,PurePosixPath('/fixed/capture'),state)
        self.assertTrue(state['error']);self.assertFalse(state['eof']);pipe.close.assert_not_called()
    def test_final_seal_guard_live_owner_boot_job_group_expiry_all_refuse(self):
        now=dt.datetime(2026,10,27,8,tzinfo=dt.timezone.utc);plan={'expires_at':(now+dt.timedelta(minutes=1)).isoformat()}
        good={'LoadState':'not-found','MainPID':'0','ControlPID':'0','Job':''};group={'path':'fixed','exists':False,'processes':0,'populated':0}
        for fault in (None,'owner','boot','job','group','expiry'):
            state=dict(good);cg=dict(group)
            if fault=='job':state['Job']='queued'
            if fault=='group':cg['populated']=1
            with patch.object(c,'boot',return_value='different' if fault=='boot' else 'boot'),patch.object(c,'utc',return_value=now+dt.timedelta(minutes=2) if fault=='expiry' else now),\
                patch.object(c,'validate_owner_terminal',side_effect=RuntimeError('live original') if fault=='owner' else None,return_value=('absent','absent')),\
                patch.object(c,'unit_state',return_value=state),patch.object(c,'cgroup_state',return_value=cg):
                if fault:
                    with self.subTest(fault=fault),self.assertRaises(RuntimeError):c.final_seal_guard({}, {},b'',plan,'p','d','boot')
                else:self.assertEqual(c.final_seal_guard({}, {},b'',plan,'p','d','boot')[2],group)
    def test_invocation_wait_refuses_foreign_description_and_nonzero_client(self):
        for description,code in [('foreign',None),('careertrellis-repair98:'+'a'*64,1)]:
            with patch.object(c,'unit_state',return_value={'LoadState':'loaded','ActiveState':'active','Description':description}),\
                patch.object(c.time,'monotonic',side_effect=[0,0]),self.assertRaises(RuntimeError):
                c.wait_owned_execution(SimpleNamespace(poll=lambda:code),'a'*64,60)
    def test_preintent_is_durable_before_owned_systemd_spawn(self):
        source=Path('repair_coordinator.py').read_text()
        self.assertLess(source.index("intent_hash=record('unit-intent.json'"),source.index('client=subprocess.Popen(command'))
        self.assertIn("'--description=careertrellis-repair98:'+intent_hash",source)
        self.assertIn('execution=wait_owned_execution(client,intent_hash,1230)',source)

if __name__=='__main__':unittest.main()
