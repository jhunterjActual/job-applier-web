"""NEW separate postterminal seal DRAFT; cannot run while SEAL_READY=False."""
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


SEAL_READY=True

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--approved-plan-sha256',required=True);ap.add_argument('--approved-decision-sha256',required=True);a=ap.parse_args()
    try:
        load_contract();bootstrap();need(SEAL_READY)
        plan,decision,manifest,preflight,inputs=load_controls(a.approved_plan_sha256,a.approved_decision_sha256)
        owner_terminal_raw=read(DOMAIN/'owner-terminal.json');owner_terminal=closed('owner-terminal.json',decode(owner_terminal_raw))
        need(owner_terminal['coordinator_exit']==0 and owner_terminal['owner_self_exit_claim'] is False
            and owner_terminal['repair_plan_sha256']==a.approved_plan_sha256 and owner_terminal['repair_decision_sha256']==a.approved_decision_sha256)
        current_boot=boot();need(current_boot==owner_terminal['boot_id'])
        owner_state=absent_original(owner_terminal['owner'],current_boot)
        coordinator_state=absent_original(owner_terminal['coordinator'],current_boot)
        state=unit_state(UNIT);group=cgroup_state(UNIT,state)
        need(state.get('LoadState')=='not-found' and not state.get('Job') and group['processes']==0 and group['populated']==0)
        candidate_raw=read(DOMAIN/'candidate.json');candidate=closed('candidate.json',decode(candidate_raw))
        need(sha(candidate_raw)==owner_terminal['candidate_sha256'])
        validate_candidate(candidate,plan,a.approved_plan_sha256,a.approved_decision_sha256)
        owner_state,coordinator_state=validate_owner_terminal(owner_terminal,candidate,candidate_raw,plan,
            a.approved_plan_sha256,a.approved_decision_sha256,current_boot)
        for name,value in inputs.items():need(read(DOMAIN/name)==value)
        ns=original_helpers()
        with ns['locks']():
            live=authenticate_retained(plan,preflight,ns)
            validate_browser_binding(candidate,preflight,live)
            before=decode(read(DOMAIN/'runtime-before.json'));after=decode(read(DOMAIN/'runtime-manifest.json'))
            need(before==after==live['runtime'] and sha(canonical(after))==candidate['runtime_after_sha256'])
            checks_raw=read(DOMAIN/'repair-checks.json');checks=validate_repair_checks(checks_raw,plan,manifest)
            unit_raw=read(DOMAIN/'unit-terminal.json');unit=closed('unit-terminal.json',decode(unit_raw));validate_unit_terminal(unit,current_boot,plan,manifest,a.approved_plan_sha256,a.approved_decision_sha256,ns)
            need(sha(checks_raw)==candidate['checks_sha256']==owner_terminal['checks_sha256']
                and sha(unit_raw)==candidate['unit_terminal_sha256']==owner_terminal['unit_terminal_sha256'])
            (owner_state,coordinator_state),state,group=final_seal_guard(owner_terminal,candidate,candidate_raw,plan,
                a.approved_plan_sha256,a.approved_decision_sha256,current_boot)
            proof={'schema':'careertrellis.repair98-postterminal-proof/v1','attempt_id':ATTEMPT_ID,'observed_at':utc().isoformat(),
                'boot_id':current_boot,'owner_terminal_sha256':sha(owner_terminal_raw),'candidate_sha256':sha(candidate_raw),
                'owner_state':owner_state,'coordinator_state':coordinator_state,'unit':state,'cgroup_after':group,
                'runtime_manifest_sha256':sha(canonical(after)),'browser_manifest_sha256':sha(canonical(live['browser'])),
                'original_deadline':plan['expires_at'],'source_manifest_sha256':plan['source_manifest_sha256']}
            need(boot()==current_boot and utc()<dt.datetime.fromisoformat(plan['expires_at']))
            (owner_state,coordinator_state),state,group=final_seal_guard(owner_terminal,candidate,candidate_raw,plan,
                a.approved_plan_sha256,a.approved_decision_sha256,current_boot)
            proof_hash=record('postterminal-owner-proof.json',proof)
            accepted=build_acceptance(plan,manifest,candidate,checks,sha(candidate_raw),sha(owner_terminal_raw),proof_hash)
            final_seal_guard(owner_terminal,candidate,candidate_raw,plan,a.approved_plan_sha256,a.approved_decision_sha256,current_boot)
            create(DOMAIN/'accepted.json',accepted)
        print('{"result":"qualified_prepared_inactive_sealed","activated":false}');return 0
    except BaseException:
        print('{"result":"repair_seal_refused_no_acceptance_claim","no_replay":true}');return 1
if __name__=='__main__':raise SystemExit(main())
