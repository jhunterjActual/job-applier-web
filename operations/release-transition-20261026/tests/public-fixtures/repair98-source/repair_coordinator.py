"""NEW fixed root coordinator DRAFT; explicit hold until retained-authentication completes."""
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

COORDINATOR_READY=True

def hold_owned(client):
    if client is None:return
    while True:
        try:
            state=unit_state(UNIT);group=cgroup_state(UNIT,state)
            if client.poll() is not None and state.get('LoadState')=='not-found' and not state.get('Job') and group['processes']==0 and group['populated']==0:return
        except BaseException:pass
        try:__import__('time').sleep(1)
        except BaseException:pass

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--approved-plan-sha256',required=True);ap.add_argument('--approved-decision-sha256',required=True);a=ap.parse_args()
    client=None;handlers={}
    try:
        load_contract();bootstrap();need(COORDINATOR_READY) # Explicit unready guard, before any mutation.
        plan,decision,manifest,preflight,inputs=load_controls(a.approved_plan_sha256,a.approved_decision_sha256)
        ns=original_helpers();before=authenticate_retained(plan,preflight,ns)
        owner=closed('owner.json',decode(read(DOMAIN/'owner.json')))
        for sig in (signal.SIGHUP,signal.SIGTERM,signal.SIGINT):handlers[sig]=signal.getsignal(sig);signal.signal(sig,lambda *_:None)
        record('coordinator.json',{'schema':'careertrellis.repair98-coordinator/v1','attempt_id':ATTEMPT_ID,
            'pid':os.getpid(),'start_ticks':birth(os.getpid()),'boot_id':boot(),'owner_pid':owner['pid'],'started_at':utc().isoformat()})
        with ns['locks']():
            try:
                before=authenticate_retained(plan,preflight,ns)
                create(DOMAIN/'runtime-before.json',before['runtime'])
                prepare_new_code_and_work(manifest) # New root-owned RO CODE; nobody writable WORK only.
                command=['systemd-run','--unit='+UNIT,'--wait','--pipe','--collect',*ns['sandbox_properties'](RELEASE,WORK,ns['BROWSER']),
                    str(RELEASE/'backend/venv/bin/python'),'-I','-B',str(CODE/'run_checks_repair98.py'),
                    '--release',str(RELEASE),'--revision',REV,'--work',str(WORK),'--repair-ops-commit',plan['repair_ops_commit'],
                    '--approved-runner-sha256',manifest['files']['run_checks_repair98.py']['sha256'],
                    '--approved-failure-chain-sha256',plan['old_failure_chain_sha256'],
                    '--retained-runtime-manifest-sha256',plan['retained_runtime_manifest_sha256']]
                require_unit_absent()
                intent={'schema':'careertrellis.repair98-unit-intent/v1','attempt_id':ATTEMPT_ID,'unit':UNIT,
                    'argv':command,'sandbox_properties':ns['sandbox_properties'](RELEASE,WORK,ns['BROWSER']),
                    'runner_sha256':manifest['files']['run_checks_repair98.py']['sha256'],
                    'qualifier_sha256':manifest['files']['fixture_relocation.py']['sha256'],
                    'repair_plan_sha256':a.approved_plan_sha256,'repair_decision_sha256':a.approved_decision_sha256,
                    'source_manifest_sha256':plan['source_manifest_sha256'],'boot_id':boot(),'coordinator_pid':os.getpid(),
                    'coordinator_start_ticks':birth(os.getpid()),'created_at':utc().isoformat(),'description_binding':'sha256-of-this-record'}
                intent_hash=record('unit-intent.json',intent)
                command=command[:2]+['--description=careertrellis-repair98:'+intent_hash]+command[2:]
                fd=os.open(DOMAIN/'synthetic-tests.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as output:
                    sync_dir(DOMAIN)
                    require_unit_absent() # Fresh exact absence immediately before systemd Popen.
                    client=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
                    client_birth=birth(client.pid)
                    test_client_hash=record('test-client.json',{'schema':'careertrellis.repair98-test-client/v1','attempt_id':ATTEMPT_ID,'pid':client.pid,'start_ticks':client_birth,'boot_id':boot(),'unit':UNIT,'unit_intent_sha256':intent_hash})
                    execution=wait_owned_execution(client,intent_hash,1230)
                    collected=collect_empty_unit(UNIT,execution,client)
                    output.flush();os.fsync(output.fileno())
                unit={'schema':'careertrellis.repair98-unit-terminal/v1','attempt_id':ATTEMPT_ID,'unit':UNIT,
                    **{k:collected[k] for k in ('execution','after','cgroup_after','client_pid','client_exit')},
                    'client_start_ticks':client_birth,'boot_id':boot(),'observed_at':utc().isoformat(),'unit_intent_sha256':intent_hash,'test_client_sha256':test_client_hash}
                unit_hash=record('unit-terminal.json',unit)
                checks_raw=read_unprivileged_checks(WORK/'repair-checks.json')
                checks=validate_repair_checks(checks_raw,plan,manifest)
                checks_hash=create(DOMAIN/'repair-checks.json',checks_raw)
                after=authenticate_retained(plan,preflight,ns)
                need(before['runtime']==after['runtime'] and before['browser']==after['browser'])
                after_hash=create(DOMAIN/'runtime-manifest.json',after['runtime'])
                candidate={'schema':'careertrellis.repair98-candidate/v1','attempt_id':ATTEMPT_ID,'release':REV,'build':'20261026.98',
                    'repair_ops_commit':plan['repair_ops_commit'],'repair_plan_sha256':a.approved_plan_sha256,
                    'repair_decision_sha256':a.approved_decision_sha256,'source_manifest_sha256':plan['source_manifest_sha256'],
                    'old_failure_chain_sha256':plan['old_failure_chain_sha256'],'full_live_preflight_sha256':plan['full_live_preflight_sha256'],
                    'checks_sha256':checks_hash,'unit_terminal_sha256':unit_hash,'runtime_before_sha256':sha(canonical(before['runtime'])),
                    'runtime_after_sha256':after_hash,'browser_before_sha256':sha(canonical(before['browser'])),
                    'browser_after_sha256':sha(canonical(after['browser'])),'completed_utc':utc().isoformat(),'result':'candidate_only_not_accepted'}
                record('candidate.json',candidate)
            finally:
                hold_owned(client) # Remains INSIDE canonical fences until exact terminal.
        print('{"result":"candidate_written_not_accepted","requires_owner_exit_and_separate_seal":true}');return 0
    except BaseException:
        try:print('{"result":"repair_coordinator_refused_or_unknown","no_replay":true}')
        except BaseException:pass
        return 1
    finally:
        hold_owned(client)
        for sig,previous in handlers.items():signal.signal(sig,previous)
if __name__=='__main__':raise SystemExit(main())
