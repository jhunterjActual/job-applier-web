"""QA-only exact-function execution with synthetic boundaries. No host operations."""
import ast
from contextlib import contextmanager
import datetime as dt
import hashlib
import io
import json
import re
from pathlib import Path, PurePosixPath as P
import sys
from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parent
FREEZE='5734432f8fcb75b5bd4f62e8a6596cea97ac12aa2ecc8bd24afb19ec23d4d725'
NOW=dt.datetime(2026,10,27,8,tzinfo=dt.timezone.utc)
class Held(BaseException):pass
class Exited(BaseException):
    def __init__(self,code):self.code=code
def need(x):
    if not x:raise RuntimeError('synthetic refusal')
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def sha(x):return hashlib.sha256(x).hexdigest()
def function(file,name,ns):
    tree=ast.parse((ROOT/file).read_bytes())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),file,'exec'),ns)
    return node
def args():return N(approved_plan_sha256='p'*64,approved_decision_sha256='d'*64)
class Parser:
    def add_argument(self,*a,**k):pass
    def parse_args(self):return args()
class Stream(io.BytesIO):
    def fileno(self):return 17
def common():
    events=[];ledger={};lock=[False]
    @contextmanager
    def locks():
        lock[0]=True;events.append('lock_enter')
        try:yield
        finally:events.append('lock_exit');lock[0]=False
    plan={'source_manifest_sha256':'s'*64,'old_failure_chain_sha256':'f'*64,
          'retained_runtime_manifest_sha256':'r'*64,'repair_ops_commit':'a'*40,
          'expires_at':(NOW+dt.timedelta(minutes=10)).isoformat()}
    manifest={'files':{n:{'sha256':'h'*64} for n in ('run_checks_repair98.py','fixture_relocation.py')}}
    live={'runtime':{'synthetic.py':'r'*64},'browser':{'browser':'b'*64}}
    def record(name,value):ledger[name]=value;events.append('record:'+name);return sha(canonical(value))
    ns={'argparse':N(ArgumentParser=Parser),'load_contract':lambda:None,'bootstrap':lambda:None,
        'COORDINATOR_READY':True,'SEAL_READY':True,'need':need,'load_controls':lambda *a:(plan,{},manifest,{},{}),
        'original_helpers':lambda:{'locks':locks,'sandbox_properties':lambda *a:[],'BROWSER':P('/synthetic/browser')},
        'authenticate_retained':lambda *a:live,'DOMAIN':P('/synthetic/ledger'),'STAGE':P('/synthetic/stage'),
        'RELEASE':P('/synthetic/release'),'WORK':P('/synthetic/work'),'CODE':P('/synthetic/code'),
        'REV':'r'*40,'UNIT':'synthetic-only.service','ATTEMPT_ID':'synthetic-only',
        'utc':lambda:NOW,'boot':lambda:'boot','birth':lambda p:str(p)+'0',
        'record':record,'create':lambda p,v:record(p.name,v),'closed':lambda n,v:v,
        'decode':json.loads,'canonical':canonical,'sha':sha,'print':lambda *a,**k:None,
        'prepare_new_code_and_work':lambda *a:None,'require_unit_absent':lambda:None,
        'sync_dir':lambda *a:None,'os':N(getpid=lambda:501,open=lambda *a:17,
          fdopen=lambda *a:Stream(),fsync=lambda *a:None,O_WRONLY=1,O_CREAT=2,O_EXCL=4,O_NOFOLLOW=8),
        'signal':N(SIGHUP=1,SIGTERM=15,SIGINT=2,getsignal=lambda n:None,signal=lambda *a:None)}
    return ns,events,ledger,lock,plan,live

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw=(ROOT/'SOURCE_BINDINGS.json').read_bytes();assert sha(raw)==FREEZE
        cls.pins=json.loads(raw)['files']
        for n,v in cls.pins.items():
            b=(ROOT/n).read_bytes();assert len(b)==v['bytes'] and sha(b)==v['sha256'],n

    def coordinator_fault(self,fault):
        ns,events,ledger,lock,plan,live=common();client=N(pid=502)
        ns['read']=lambda p:canonical({'pid':501})
        ns['subprocess']=N(DEVNULL=-3,STDOUT=-2,Popen=Mock(return_value=client))
        def popen(*a,**k):
            events.append('popen')
            if fault=='popen':raise OSError('synthetic spawn refused')
            return client
        ns['subprocess'].Popen.side_effect=popen
        def birth(pid):
            if pid==502 and fault=='birth':events.append('birth_fault');raise OSError('synthetic birth unknown')
            return str(pid)+'0'
        ns['birth']=birth;original=ns['record']
        def record(n,v):
            if n=='test-client.json' and fault=='postspawn_record':events.append('record_fault');raise OSError('synthetic record unknown')
            return original(n,v)
        ns['record']=record
        def hold(c):events.append(('custody',c is client,lock[0]))
        ns['hold_owned']=hold
        ns['wait_owned_execution']=Mock(side_effect=RuntimeError('synthetic terminal unknown'))
        function('repair_coordinator.py','main',ns)
        self.assertEqual(ns['main'](),1)
        self.assertIn('record:unit-intent.json',events)
        self.assertLess(events.index('record:unit-intent.json'),events.index('popen'))
        self.assertNotIn('candidate.json',ledger)
        custody=[e for e in events if isinstance(e,tuple)]
        self.assertTrue(custody and custody[0][2],events)
        self.assertEqual(custody[0][1],fault!='popen')
        if fault=='birth':self.assertIn('birth_fault',events)
        if fault=='postspawn_record':self.assertIn('record_fault',events)
        if fault=='wait':ns['wait_owned_execution'].assert_called_once()
        return events
    def test_coordinator_popen_birth_postspawn_and_wait_unknown(self):
        for fault in ('popen','birth','postspawn_record','wait'):
            with self.subTest(fault=fault):self.coordinator_fault(fault)

    def owner_fault(self,fault):
        ns,events,ledger,lock,plan,live=common();pipes=[N(close=Mock()),N(close=Mock())]
        client=N(pid=502,stdout=pipes[0],stderr=pipes[1],wait=Mock(return_value=1 if fault=='nonzero' else 0))
        ns['os']=N(setsid=lambda:None,open=lambda *a:17,dup2=lambda *a:None,close=lambda *a:None,
          getpid=lambda:501,O_RDWR=2,_exit=lambda code:(_ for _ in ()).throw(Exited(code)))
        ns['subprocess']=N(DEVNULL=-3,PIPE=-1,Popen=Mock(return_value=client))
        if fault=='popen':ns['subprocess'].Popen.side_effect=OSError('synthetic spawn refused')
        def birth(pid):
            if pid==502 and fault=='birth':events.append('birth_fault');raise OSError('synthetic birth unknown')
            return str(pid)+'0'
        ns['birth']=birth;original=ns['record']
        def create(p,v):
            if p.name=='child-spawn.json' and fault=='postspawn_record':events.append('record_fault');raise OSError('synthetic child record unknown')
            return original(p.name,v)
        ns['create']=create
        def record(n,v):
            if n=='child-spawn.json' and fault=='postspawn_record':events.append('record_fault');raise OSError('synthetic child record unknown')
            return original(n,v)
        ns['record']=record
        class Thread:
            def __init__(self,target,args,daemon):self.args=args
            def start(self):
                self.args[2]['eof']=fault!='capture';self.args[2]['error']=fault=='capture'
                events.append('capture')
            def join(self):events.append('join')
        ns['threading']=N(Thread=Thread);ns['drain']=lambda *a:None
        ns['unit_state']=lambda u:{'LoadState':'not-found','Job':''}
        ns['cgroup_state']=lambda *a:{'processes':0,'populated':0}
        ns['time']=N(sleep=lambda *a:None)
        def read(p):events.append('read:'+p.name);raise FileNotFoundError('synthetic candidate unavailable')
        ns['read']=read
        node=function('repair_owner.py','own',ns)
        # Stop only at the unchanged function's persistent unknown-hold statement.
        hold_line=max(n.lineno for n in ast.walk(node) if isinstance(n,ast.While))
        def tracer(frame,event,arg):
            if frame.f_code is ns['own'].__code__ and event=='line' and frame.f_lineno==hold_line:
                events.append('actual_persistent_hold');raise Held()
            return tracer
        prior=sys.gettrace();sys.settrace(tracer)
        try:
            with self.assertRaises(Held):ns['own'](args())
        finally:sys.settrace(prior)
        self.assertIn('owner-unknown.json',ledger)
        self.assertNotIn('owner-terminal.json',ledger)
        self.assertIn('actual_persistent_hold',events)
        for pipe in pipes:pipe.close.assert_not_called()
        if fault=='birth':self.assertIn('birth_fault',events)
        if fault=='postspawn_record':self.assertIn('record_fault',events)
        if fault=='capture':self.assertEqual(events.count('join'),2)
        if fault=='candidate':self.assertIn('read:candidate.json',events)
        return events
    def test_owner_spawn_birth_record_capture_and_candidate_unknown(self):
        for fault in ('popen','birth','postspawn_record','capture','candidate'):
            with self.subTest(fault=fault):self.owner_fault(fault)

    def seal_fault(self,fault):
        ns,events,ledger,lock,plan,live=common()
        runtime=canonical(live['runtime']);checks=b'checks';unit=b'unit';candidate_raw=b'candidate';owner_raw=b'ownerterminal'
        candidate={'runtime_after_sha256':sha(runtime),'checks_sha256':sha(checks),'unit_terminal_sha256':sha(unit), 'browser_before_sha256':sha(canonical(live['browser'])),'browser_after_sha256':sha(canonical(live['browser']))}
        terminal={'coordinator_exit':0,'owner_self_exit_claim':False,'repair_plan_sha256':args().approved_plan_sha256,
          'repair_decision_sha256':args().approved_decision_sha256,'boot_id':'boot','owner':{'pid':501},
          'coordinator':{'pid':502},'candidate_sha256':sha(candidate_raw),**candidate}
        sourceinput=b'approved input';ns['load_controls']=lambda *a:(plan,{}, {},{'browser_manifest_sha256':sha(canonical(live['browser']))}, {'repair-plan.json':sourceinput})
        rawmap={'owner-terminal.json':owner_raw,'candidate.json':candidate_raw,'runtime-before.json':runtime,
          'runtime-manifest.json':runtime,'repair-checks.json':checks,'unit-terminal.json':unit,'repair-plan.json':sourceinput}
        def read(p):
            events.append('read:'+p.name)
            if p.name=='accepted.json':
                if fault=='readback':return b'wrong readback'
                return ledger[p.name]
            return b'tampered' if fault=='inputtamper' and p.name=='repair-plan.json' else rawmap[p.name]
        ns['read']=read
        ns['decode']=lambda b:terminal if b==owner_raw else candidate if b==candidate_raw else {} if b==unit else json.loads(b)
        def absent(actor,boot):
            events.append('absent:'+str(actor['pid']))
            if fault=='actoralive' and actor['pid']==501:raise RuntimeError('synthetic original live')
            return 'absent'
        ns['absent_original']=absent
        ns['unit_state']=lambda *a:{'LoadState':'not-found','Job':''}
        ns['cgroup_state']=lambda *a:{'processes':0,'populated':0}
        ns['validate_candidate']=lambda *a:events.append('validate_candidate')
        validations=[0]
        def validate_owner(*a):
            validations[0]+=1;events.append('owner_validation:'+str(validations[0]))
            if fault=='final_actor' and validations[0]==4:raise RuntimeError('synthetic final live actor')
            return 'absent','absent'
        ns['validate_owner_terminal']=validate_owner
        ns['validate_repair_checks']=lambda *a:{};ns['validate_unit_terminal']=lambda *a:events.append('validate_unit')
        ns['dt']=dt
        function('repair_contract.py','final_seal_guard',ns)
        function('repair_contract.py','validate_browser_binding',ns)
        if fault=='expiry':
            def expired():events.append('expired_clock_observed');return NOW+dt.timedelta(hours=1)
            ns['utc']=expired
        ns['build_acceptance']=lambda *a:{'synthetic':'accepted'}
        class Capture(Stream):
            def write(self,b):ledger['accepted.json']=b;events.append('accepted_bytes_written');return super().write(b)
        def opened(p,flags,mode):
            self.assertEqual(flags,1|2|4|8);self.assertEqual(mode,0o600)
            events.append('accepted_attempt')
            if fault=='exclusive':raise FileExistsError('synthetic existing receipt')
            return 17
        def fsync(fd):
            events.append('accepted_file_fsync')
            if fault=='fsync':raise OSError('synthetic fsync unknown')
        ns['parents']=lambda p:None
        ns['os']=N(open=opened,dup=lambda fd:18,close=lambda fd:None,fchmod=lambda *a:None,
            fdopen=lambda *a:Capture(),fsync=fsync,O_WRONLY=1,O_CREAT=2,O_EXCL=4,O_NOFOLLOW=8)
        ns['sync_dir']=lambda p:events.append('accepted_parent_fsync')
        function('repair_contract.py','create',ns)
        function('repair_seal.py','main',ns)
        result=ns['main']()
        self.assertEqual(result,0 if fault is None else 1)
        if fault in ('inputtamper','actoralive','expiry','final_actor'):
            self.assertNotIn('accepted_attempt',events)
        else:self.assertIn('accepted_attempt',events)
        if fault=='inputtamper':self.assertIn('read:repair-plan.json',events)
        if fault=='actoralive':self.assertIn('absent:501',events)
        if fault=='final_actor':self.assertIn('owner_validation:4',events)
        if fault=='expiry':self.assertIn('expired_clock_observed',events)
        if fault=='fsync':self.assertIn('accepted_file_fsync',events)
        if fault=='readback':
            self.assertIn('accepted_parent_fsync',events);self.assertIn('read:accepted.json',events)
        if fault in ('fsync','readback'):
            self.assertIn('accepted.json',ledger) # Artifact may exist: refusal != proven absence.
        return events
    def test_actual_seal_main_preacceptance_refusals_and_write_unknown(self):
        for fault in (None,'actoralive','inputtamper','expiry','final_actor','exclusive','fsync','readback'):
            with self.subTest(fault=fault):self.seal_fault(fault)

    def test_actual_v3_browser_crosslinks(self):
        ns={'sha':sha,'canonical':canonical,'need':need}
        function('repair_contract.py','validate_browser_binding',ns)
        live={'browser':{'actual':'a'*64}};expected=sha(canonical(live['browser']))
        good={'browser_before_sha256':expected,'browser_after_sha256':expected}
        ns['validate_browser_binding'](good,{'browser_manifest_sha256':expected},live)
        for candidate,preflight in [(dict.fromkeys(good,'9'*64),{'browser_manifest_sha256':expected}),
                (good,{'browser_manifest_sha256':'9'*64}),
                (dict(good,browser_after_sha256='9'*64),{'browser_manifest_sha256':expected})]:
            with self.assertRaises(RuntimeError):ns['validate_browser_binding'](candidate,preflight,live)

    def test_actual_v3_rehashed_unit_argv_controls_birth_and_time(self):
        ns,_,_,_,plan,_=common();ns.update(re=re,dt=dt)
        manifest={'files':{'run_checks_repair98.py':{'sha256':'runner'},'fixture_relocation.py':{'sha256':'helper'}}}
        helper={'BROWSER':'synthetic','sandbox_properties':lambda *a:['--property=ProtectSystem=strict']}
        plan.update(decision_at=NOW.isoformat())
        function('repair_contract.py','expected_unit_command',ns)
        function('repair_contract.py','validate_unit_terminal',ns)
        for fault in (None,'argv','sandbox','plan','decision','source','birth','future'):
            intent={'unit':ns['UNIT'],'boot_id':'boot','repair_plan_sha256':'p','repair_decision_sha256':'d',
                'source_manifest_sha256':plan['source_manifest_sha256'],'runner_sha256':'runner','qualifier_sha256':'helper',
                'coordinator_pid':501,'coordinator_start_ticks':'5010','description_binding':'sha256-of-this-record',
                'argv':ns['expected_unit_command'](plan,manifest,helper),'sandbox_properties':helper['sandbox_properties'](),
                'created_at':NOW.isoformat()}
            if fault=='argv':intent['argv']=['systemd-run','--property=User=root']
            if fault=='sandbox':intent['sandbox_properties']=['--property=ProtectSystem=no']
            if fault in ('plan','decision'):intent['repair_'+fault+'_sha256']='9'*64
            if fault=='source':intent['source_manifest_sha256']='9'*64
            if fault=='birth':intent['coordinator_start_ticks']='otherbirth'
            if fault=='future':intent['created_at']=(NOW+dt.timedelta(hours=1)).isoformat()
            ir=canonical(intent);ih=sha(ir)
            client={'pid':502,'start_ticks':'5020','boot_id':'boot','unit':ns['UNIT'],'unit_intent_sha256':ih};cr=canonical(client)
            state={'LoadState':'loaded','ActiveState':'active','SubState':'exited','MainPID':'0','ControlPID':'0',
                'Job':'','Result':'success','ExecMainStatus':'0','InvocationID':'a'*32,'Description':'careertrellis-repair98:'+ih}
            value={'unit':ns['UNIT'],'boot_id':'boot','client_exit':0,'client_pid':502,'client_start_ticks':'5020',
                'unit_intent_sha256':ih,'test_client_sha256':sha(cr),'observed_at':(NOW+dt.timedelta(minutes=1)).isoformat(),
                'execution':{'unit':ns['UNIT'],'state':state,'invocation_id':'a'*32,'cgroup':{'processes':0,'populated':0}},
                'after':{'LoadState':'not-found'},'cgroup_after':{'processes':0,'populated':0}}
            records={'unit-intent.json':ir,'test-client.json':cr,
                'coordinator.json':canonical({'pid':501,'start_ticks':'5010','boot_id':'boot'})}
            seen=[]
            def read(p):seen.append(p.name);return records[p.name]
            ns['read']=read;ns['absent_original']=lambda *a:'absent'
            # Every wrapper hash and actual Description is rebuilt, preserving the bad field.
            self.assertEqual(value['unit_intent_sha256'],sha(records['unit-intent.json']))
            if fault:
                with self.subTest(fault=fault),self.assertRaises(RuntimeError):ns['validate_unit_terminal'](value,'boot',plan,manifest,'p','d',helper)
                self.assertIn('unit-intent.json',seen)
            else:ns['validate_unit_terminal'](value,'boot',plan,manifest,'p','d',helper)

if __name__=='__main__':unittest.main(verbosity=2)
