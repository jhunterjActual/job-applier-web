import time
"""Fixed native participant RPC. No network dialing or arbitrary command payloads.

Requires a separately installed, source-commit-bound packet and exact approved
plan digest. The primary has NOT authorized running this draft on either host.
Confidential RPC bodies are transport-only and must never enter driver logs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import types
import subprocess
import threading

MODULES=('policy_encoding', 'release_transaction', 'protected_state', 'terminal_validation', 'readiness_bridge', 'native_peer', 'canonical_primitives', 'accepted_pair_validator', 'current_health_validator', 'prepared_types', 'historical98_controls', 'prepared98_history', 'prepared97_specs', 'prepared97_records', 'prepared97_checks', 'prepared_reader', 'prepared_sources', 'prepared97_reader', 'prepared97_sources', 'prepared97_history', 'prepared97_context', 'prepared_registries', 'prepared_evidence', 'native_backend')
SCRIPTS=('peer_server.py','web_coordinator.py','db_audit.py','reviewed_probe.py')
READINESS_FILES=('collector.py','terminal493.py','transaction04.py','pair_boundary.py',
                 'installed493-pins.json','transaction04-pins.json','SOURCE_MANIFEST.json')
ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}

def refuse():raise RuntimeError('peer_refused_inspect_no_replay')
def need(x):
    if not x:refuse()
def decode(raw):
    def pairs(items):
        value={}
        for k,v in items:need(k not in value);value[k]=v
        return value
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:refuse())
def protected(path,mode,limit=131072):
    p=Path(path);need(p.is_absolute())
    for parent in p.parents:
        s=parent.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)
    s=p.lstat();need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1
                    and stat.S_IMODE(s.st_mode)==mode and s.st_size<=limit)
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        a=os.fstat(fd);need((a.st_dev,a.st_ino,a.st_size)==(s.st_dev,s.st_ino,s.st_size))
        with os.fdopen(fd,'rb',closefd=False) as f:raw=f.read(s.st_size+1)
        b=os.fstat(fd);c=p.lstat()
        fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        need(len(raw)==s.st_size and all(getattr(s,k)==getattr(a,k)==getattr(b,k)==getattr(c,k) for k in fields))
        return raw
    finally:os.close(fd)

def verify_readiness_sources(folder,commit,expected_manifest=None):
    need(re.fullmatch('[0-9a-f]{40}',commit) is not None)
    cache={}
    for name in READINESS_FILES:
        raw=protected(Path(folder)/'one_shot_readiness'/name,0o600)
        p=subprocess.run(['/usr/bin/git','-C','/srv/careertrellis/job-applier-web','cat-file','blob',
            commit+':operations/release-transition-20261026/one_shot_readiness/'+name],
            capture_output=True,timeout=15,env=ENV)
        need(p.returncode==0 and p.stdout==raw);cache[name]=raw
    if expected_manifest is not None:need(hashlib.sha256(cache['SOURCE_MANIFEST.json']).hexdigest()==expected_manifest)
    manifest=decode(cache['SOURCE_MANIFEST.json'])
    need(set(manifest)=={'schema','files'} and manifest['schema']=='careertrellis.one-shot-readiness-source/v1'
        and set(manifest['files'])==set(READINESS_FILES)-{'SOURCE_MANIFEST.json'})
    need(all(hashlib.sha256(cache[name]).hexdigest()==pin for name,pin in manifest['files'].items()))
    return cache

def load_packet(folder,commit):
    need(re.fullmatch('[0-9a-f]{40}',commit) is not None)
    manifest=decode(protected(folder/'packet_manifest.json',0o600))
    need(manifest['schema']=='careertrellis.app-release-packet/v1'
         and manifest['source_commit']==commit and set(manifest['modules'])==set(MODULES)
         and set(manifest['scripts'])==set(SCRIPTS) and set(manifest['readiness'])==set(READINESS_FILES))
    nested=verify_readiness_sources(folder,commit)
    need(all(hashlib.sha256(nested[name]).hexdigest()==pin for name,pin in manifest['readiness'].items()))
    snapshots={}
    for name in MODULES:
        raw=protected(folder/(name+'.py'),0o600)
        need(hashlib.sha256(raw).hexdigest()==manifest['modules'][name])
        snapshots[name]=raw
    # Bind installation to actual tracked bytes in the exact operational source
    # commit, not a mutable manifest's assertion of a commit string.
    for filename,expected in {**{n+'.py':manifest['modules'][n] for n in MODULES},**manifest['scripts']}.items():
        raw=protected(folder/filename,0o600)
        p=subprocess.run(['/usr/bin/git','-C','/srv/careertrellis/job-applier-web','cat-file','blob',
            commit+':operations/release-transition-20261026/'+filename],capture_output=True,timeout=15,env=ENV)
        need(p.returncode==0 and p.stdout==raw and hashlib.sha256(raw).hexdigest()==expected)
    for name in MODULES:
        need(name not in sys.modules)
        module=types.ModuleType(name);module.__file__=str(folder/(name+'.py'))
        sys.modules[name]=module
        exec(compile(snapshots[name],module.__file__,'exec'),module.__dict__)
    sys.modules['native_peer'].VERIFIED_SOURCE_COMMIT=commit
    return sys.modules['native_peer'],sys.modules['release_transaction']

def hold(peer):
    # Durable journal remains. Descriptor ownership is retained on EOF/error;
    # only attended reconciliation may terminate an unknown owner. SIGKILL or
    # host loss cannot be solved here and always needs boot/journal inspection.
    for name in ('SIGHUP','SIGTERM','SIGINT'):
        signal.signal(getattr(signal,name),lambda *_:None)
    while True:signal.pause()

def _serve(peer,plan,readline,write,hold_owner):
    seq=0;token=hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    actions={'ping':(), 'abort_unclaimed':(), 'baseline':(), 'claim':('plan_sha256',), 'select_policy':('revision',),
             'read_policy':(), 'write_marker':('marker',), 'read_marker':(),
             'restore':(), 'finish':('revision','result','healthy_proven'),
             'project_verifying':('revision','proof'),
             'assert_known':(), 'assert_preserved':(), 'backup_proofs':('binding',), 'qualify_backup_proofs':('binding','web_health'), 'release':('result',),
             'phase':('phase','revision'), 'audit':('nonce','stage'),
             'recovery_started':('record',),'global_terminal':('record',),'global_readback':('record',),
             'commit_release_decision':('prepared','binding','generations','expires_at')}
    while True:
        raw=readline(32769)
        if not raw:
            if peer.claimed:hold_owner(peer)
            return
        try:
            need(len(raw)<=32768 and raw.endswith('\n'))
            msg=decode(raw);need(set(msg)=={'seq','plan_sha256','action','args'})
            need(msg['seq']==seq and msg['plan_sha256']==token and msg['action'] in actions)
            args=msg['args'];action=msg['action'];need(type(args) is dict and set(args)==set(actions[action]))
            seq+=1;peer.check_locks()
            if action=='ping':value={'pid':os.getpid(),'role':peer.role,'boot':peer.boot}
            elif action=='abort_unclaimed':
                need(not peer.claimed)
                for fd,*_ in reversed(peer.fds):os.close(fd)
                peer.fds=[];peer.released=True
                write({'seq':msg['seq'],'ok':True,'value':None});return
            elif action=='baseline':value=peer.baseline()
            elif action=='claim':peer.claim(args['plan_sha256']);value=None
            elif action=='select_policy':peer.select_policy(args['revision']);value=None
            elif action=='read_policy':value=peer.policy_readback()
            elif action=='write_marker':peer.write_marker(args['marker']);value=None
            elif action=='read_marker':value=peer.original and json.loads(peer.store.read(sys.modules['native_peer'].INTERLOCK))
            elif action=='restore':peer.restore();value=None
            elif action=='project_verifying':
                peer.project_verifying(**args);value={'result':'nonterminal_verifying'}
            elif action=='finish':
                peer.finish(**args);value={'completion_sha256':peer.completion_digest(args['revision'],args['result'])}
            elif action=='assert_known':peer.assert_known();value=None
            elif action=='assert_preserved':peer.assert_preserved();value=None
            elif action=='recovery_started':value={'sha256':peer.recovery_started(args['record'])}
            elif action=='global_terminal':value={'sha256':peer.authorize_global(args['record'])}
            elif action=='global_readback':value={'sha256':hashlib.sha256(peer.read_global(args['record'])).hexdigest()}
            elif action=='commit_release_decision':
                value=peer.commit_release_decision(args['prepared'],args['binding'],args['generations'],args['expires_at'])
            elif action=='backup_proofs':
                need(peer.role=='db');peer.assert_preserved()
                need(not peer.release_started and peer.release_decision is None)
                seen=getattr(peer,'readiness_challenges',set());need(args['binding']['nonce'] not in seen and len(seen)<16)
                seen.add(args['binding']['nonce']);peer.readiness_challenges=seen
                bridge=sys.modules['readiness_bridge'];collector=bridge.load(plan,peer.source_commit)['collector'];reader=collector.Reader()
                health=bridge.collect_local('db',plan,sys.modules['native_peer'].VERIFIED_SOURCE_COMMIT,args['binding'],reader=reader)
                pair=sys.modules['accepted_pair_validator']
                receipt=protected('/var/backups/careertrellis/paired-restore-receipts/retained-'+pair.TRANSACTION+'.json',0o600)
                need(hashlib.sha256(receipt).hexdigest()==pair.DB_DIGEST)
                need(not os.path.lexists('/var/lib/careertrellis-paired-db-acceptance/'+pair.TRANSACTION))
                for unit in ('careertrellis-paired-acceptance-cleanup-','careertrellis-retained-start-recovery-'):
                    p=subprocess.run(['/usr/bin/systemctl','show',unit+pair.TRANSACTION+'.service',
                        '--property=ActiveState,MainPID,ControlPID,Job,ControlGroup'],capture_output=True,timeout=10,env=ENV)
                    need(p.returncode==0);fields=dict(x.split('=',1) for x in p.stdout.decode().splitlines())
                    need(fields['ActiveState'] in ('inactive','failed') and fields['MainPID']==fields['ControlPID']=='0' and fields['Job']=='')
                    group=fields['ControlGroup']
                    if group:need(not (Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.procs').read_text().strip())
                bridge=sys.modules['readiness_bridge']
                bridge.verify_generation_structure(health,plan,args['binding'],'db',bridge.load(plan,peer.source_commit)['collector'],
                    now=__import__('datetime').datetime.now(__import__('datetime').timezone.utc))
                peer.pending_raw_readiness=health;peer.pending_readiness_reader=reader;peer.qualified_nonce=None
                peer.final_readiness=health if args['binding']['phase']=='final' and health['result']=='ok' else None
                peer.final_readiness_deadline=bridge.load(plan,peer.source_commit)['collector'].RUN_DEADLINE
                current=health['repository']
                value={'health':health,'receipt_hex':receipt.hex(),'repository':current,'cleanup_owner_absent':True}
            elif action=='qualify_backup_proofs':
                need(peer.role=='db' and not peer.release_started and peer.release_decision is None and peer.qualified_nonce is None)
                raw=peer.pending_raw_readiness;need(raw['binding']==args['binding'])
                peer.qualified_nonce=args['binding']['nonce'] # attempt once even on ambiguous/failure
                bridge=sys.modules['readiness_bridge'];collector=bridge.load(plan,peer.source_commit)['collector']
                need(time.monotonic()<peer.final_readiness_deadline)
                qualified=bridge.qualify_local_database(args['web_health'],raw,args['binding'],collector,plan['readiness_manifest_sha256'],reader=peer.pending_readiness_reader)
                need(time.monotonic()<peer.final_readiness_deadline)
                peer.final_readiness=qualified if args['binding']['phase']=='final' else None
                value={'health':qualified}
            elif action=='phase':
                need(peer.claimed and args['phase']=='policy_selected' and args['revision'] in (plan['target'],plan['correction']))
                peer.create(sys.modules['native_peer'].JOURNAL+'/'+args['phase']+'-'+args['revision']+'.json',
                    sys.modules['release_transaction'].canonical({'phase':args['phase'],'release':args['revision'],
                        'observed_at':sys.modules['release_transaction'].datetime.now(
                            sys.modules['release_transaction'].timezone.utc).isoformat()}));value=None
            elif action=='audit':
                need(peer.role=='db' and re.fullmatch('[0-9a-f]{64}',args['nonce'])
                     and args['stage'] in ('preflight','stopped','recovery_stopped'))
                p=subprocess.run(['/usr/bin/python3','-I','-B',str(Path(__file__).parent/'db_audit.py'),
                    '--nonce',args['nonce'],'--target',plan['target'],'--recovery',plan['correction']],
                    capture_output=True,timeout=40,env=ENV)
                need(p.returncode==0 and len(p.stdout)<=16384);value=json.loads(p.stdout)
            else:
                try:
                    value=peer.release_terminal(args['result'])
                except BaseException:
                    try:peer.create(sys.modules['native_peer'].JOURNAL+'/db-release-unknown.json',
                        sys.modules['release_transaction'].canonical({'result':'release_unknown','fds':peer.remaining_fd_state(),
                            'decision_sha256':sys.modules['release_transaction'].digest(peer.release_decision) if peer.release_decision else None}))
                    except BaseException:pass
                    raise
                write({'seq':msg['seq'],'ok':True,'value':value});return
            write({'seq':msg['seq'],'ok':True,'value':value})
        except BaseException:
            write({'seq':seq-1,'ok':False,'classification':'refused_inspect_no_replay'})
            if peer.claimed:hold_owner(peer)
            return

def serve(peer,plan,readline,write,hold_owner=hold):
    previous={}
    if threading.current_thread() is threading.main_thread():
        for name in ('SIGHUP','SIGTERM','SIGINT'):
            if hasattr(signal,name):
                number=getattr(signal,name);previous[number]=signal.getsignal(number)
                signal.signal(number,lambda *_:None)
    try:
        return _serve(peer,plan,readline,write,lambda _:None)
    finally:
        # Covers read exceptions, response BrokenPipe, and exception-report write
        # failure. No fallthrough to main's safe-unclaimed bootstrap exit.
        if peer.claimed and not getattr(peer,'released',False):hold_owner(peer)
        for number,handler in previous.items():signal.signal(number,handler)

def main():
    p=argparse.ArgumentParser();p.add_argument('--role',choices=('web','db'),required=True)
    p.add_argument('--plan',required=True);p.add_argument('--approved-plan-sha256',required=True)
    p.add_argument('--source-commit',required=True);a=p.parse_args()
    need(sys.platform=='linux' and os.geteuid()==0 and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)
    folder=Path(__file__).resolve().parent
    native,engine=load_packet(folder,a.source_commit)
    sys.modules['peer_server']=sys.modules[__name__]
    plan=engine.canonical(decode(protected(Path(a.plan),0o600)))
    need(hashlib.sha256(plan).hexdigest()==a.approved_plan_sha256)
    value=decode(plan);engine.validate_plan(value,engine.datetime.now(engine.timezone.utc))
    peer=native.NativePeer(a.role,value).acquire()
    serve(peer,value,sys.stdin.readline,lambda v:print(json.dumps(v,separators=(',',':')),flush=True))

if __name__=='__main__':
    try:main()
    except BaseException:
        print('{"ok":false,"classification":"bootstrap_refused_no_action"}',flush=True)
        raise SystemExit(1)
