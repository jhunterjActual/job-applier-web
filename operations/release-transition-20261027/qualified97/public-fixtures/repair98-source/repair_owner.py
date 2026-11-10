"""NEW fixed retained repair owner DRAFT; no execution while OWNER_READY=False."""
import argparse,hashlib,json,os,signal,stat,subprocess,sys
from pathlib import Path
CONTRACT_SHA='6eacc5f98edd31dfecf209f8e0692ab5ec993396fde6990e42e547cde598ce8f'
def load_contract():
 path=Path(__file__).with_name('repair_contract.py')
 for parent in path.parents:
  st=parent.lstat()
  if not stat.S_ISDIR(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise RuntimeError('PROTECTED_REPAIR_LOADER')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  st=os.fstat(fd)
  if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_nlink!=1 or st.st_mode&0o022 or st.st_size>1048576:raise RuntimeError('PROTECTED_REPAIR_LOADER')
  with os.fdopen(os.dup(fd),'rb') as stream:raw=stream.read(st.st_size+1)
  after=os.fstat(fd);leaf=path.lstat();fields=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
  if len(raw)!=st.st_size or any(getattr(st,k)!=getattr(after,k) or getattr(st,k)!=getattr(leaf,k) for k in fields):raise RuntimeError('PROTECTED_REPAIR_LOADER')
 finally:os.close(fd)
 if hashlib.sha256(raw).hexdigest()!=CONTRACT_SHA:raise RuntimeError('PINNED_REPAIR_CONTRACT')
 ns={'__name__':'fixed_repair_contract'};exec(compile(raw,'pinned repair contract','exec'),ns)
 globals().update({k:v for k,v in ns.items() if not k.startswith('__')})

import signal,threading,time
OWNER_READY=True

def drain(pipe,path,state):
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:
            sync_dir(DOMAIN)
            while True:
                raw=pipe.read(65536)
                if not raw:break
                room=max(0,1048576-state['retained_bytes']);stream.write(raw[:room]);stream.flush()
                state['retained_bytes']+=min(room,len(raw));state['discarded_bytes']+=max(0,len(raw)-room)
            os.fsync(stream.fileno())
        state['eof']=True
    except BaseException:state['error']=True

def own(a):
    client=None;captures={};threads=[]
    try:
        os.setsid();null=os.open('/dev/null',os.O_RDWR)
        for number in (0,1,2):os.dup2(null,number)
        if null>2:os.close(null)
        plan,decision,manifest,preflight,raw=load_controls(a.approved_plan_sha256,a.approved_decision_sha256)
        owner={'schema':'careertrellis.repair98-owner/v1','attempt_id':ATTEMPT_ID,'pid':os.getpid(),
            'start_ticks':birth(os.getpid()),'boot_id':boot(),'repair_plan_sha256':a.approved_plan_sha256,
            'repair_decision_sha256':a.approved_decision_sha256,'started_at':utc().isoformat()}
        record('owner.json',owner)
        require_unit_absent() # Fresh no-existing-unit guard before coordinator dispatch.
        client=subprocess.Popen(['/usr/bin/python3','-I','-S','-B',str(STAGE/'repair_coordinator.py'),
            '--approved-plan-sha256',a.approved_plan_sha256,'--approved-decision-sha256',a.approved_decision_sha256],
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
        child_spawn_hash=record('child-spawn.json',{'schema':'careertrellis.repair98-child-spawn/v1','attempt_id':ATTEMPT_ID,'pid':client.pid,'start_ticks':birth(client.pid),'boot_id':boot(),'owner_pid':os.getpid(),'repair_plan_sha256':a.approved_plan_sha256,'repair_decision_sha256':a.approved_decision_sha256,'source_manifest_sha256':plan['source_manifest_sha256']})
        for name,pipe in [('stdout',client.stdout),('stderr',client.stderr)]:
            captures[name]={'retained_bytes':0,'discarded_bytes':0,'eof':False,'error':False}
            thread=threading.Thread(target=drain,args=(pipe,DOMAIN/('coordinator-'+name+'.bin'),captures[name]),daemon=False)
            thread.start();threads.append(thread)
        code=client.wait()
        for thread in threads:thread.join()
        need(all(value['eof'] and not value['error'] for value in captures.values()))
        state=unit_state(UNIT);group=cgroup_state(UNIT,state)
        need(state.get('LoadState')=='not-found' and not state.get('Job') and group['processes']==0 and group['populated']==0)
        if code!=0:
            create(DOMAIN/'owner-failed-terminal.json',{'result':'coordinator_failed','coordinator_exit':code,'owner':owner,'captures':captures,'no_replay':True})
            os._exit(1)
        candidate_raw=read(DOMAIN/'candidate.json');candidate=closed('candidate.json',decode(candidate_raw))
        coordinator=closed('coordinator.json',decode(read(DOMAIN/'coordinator.json')))
        spawned=decode(read(DOMAIN/'child-spawn.json'))
        need(coordinator['pid']==client.pid==spawned['pid'] and coordinator['start_ticks']==spawned['start_ticks']
            and coordinator['boot_id']==spawned['boot_id']==owner['boot_id'])
        record('owner-terminal.json',{'schema':'careertrellis.repair98-owner-terminal/v1','attempt_id':ATTEMPT_ID,
            'owner':owner,'coordinator':coordinator,'coordinator_exit':0,'boot_id':boot(),
            'repair_plan_sha256':a.approved_plan_sha256,'repair_decision_sha256':a.approved_decision_sha256,
            'candidate_sha256':sha(candidate_raw),'checks_sha256':candidate['checks_sha256'],
            'unit_terminal_sha256':candidate['unit_terminal_sha256'],'runtime_after_sha256':candidate['runtime_after_sha256'],
            'source_manifest_sha256':plan['source_manifest_sha256'],'captures':captures,'ended_at':utc().isoformat(),
            'owner_self_exit_claim':False,'child_spawn_sha256':child_spawn_hash})
        os._exit(0) # Independent seal must prove this owner actually exited.
    except BaseException:
        try:create(DOMAIN/'owner-unknown.json',{'owner_pid':os.getpid(),'child_pid':client.pid if client else None,'no_replay':True})
        except BaseException:pass
        while True:
            try:time.sleep(60)
            except BaseException:pass

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--approved-plan-sha256',required=True);ap.add_argument('--approved-decision-sha256',required=True);a=ap.parse_args()
    claimed=False
    try:
        load_contract();bootstrap();need(OWNER_READY)
        current_boot=boot();plan,decision,manifest,preflight,inputs=load_controls(a.approved_plan_sha256,a.approved_decision_sha256)
        authenticate_retained(plan,preflight,original_helpers());need(not os.path.lexists(DOMAIN) and not os.path.lexists(WORK) and not os.path.lexists(CODE))
        for sig in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:None)
        require_unit_absent() # Fresh exact unit absence immediately before ledger claim.
        claimed=True # Retain even partial first namespace/intent failure.
        prepare_new_ledger_parent();DOMAIN.mkdir(mode=0o700);os.chmod(DOMAIN,0o700);sync_dir(DOMAIN.parent)
        create(DOMAIN/'intent.json',{'attempt_id':ATTEMPT_ID,'boot_id':current_boot,'started_at':utc().isoformat(),
            'plan_sha256':a.approved_plan_sha256,'decision_sha256':a.approved_decision_sha256,
            'claim_owner_pid':os.getpid(),'claim_start_ticks':birth(os.getpid()),'no_replay':True})
        for name,value in inputs.items():create(DOMAIN/name,value)
        for sig in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:None)
        pid=os.fork()
        if pid==0:own(a)
        claimed=False # Retained child owns the exclusive operation now.
        print(json.dumps({'result':'new_retained_repair_owner_created','owner_pid':pid,'accepted':False}));return 0
    except BaseException:
        try:print('{"result":"repair_owner_refused_or_unknown","no_replay":true}')
        except BaseException:pass
        if claimed:
            while True:
                try:time.sleep(60)
                except BaseException:pass
        return 1
if __name__=='__main__':raise SystemExit(main())
