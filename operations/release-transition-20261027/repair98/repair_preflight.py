"""NEW fixed read-only repair preflight draft; no operation allocation."""
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


PREFLIGHT_READY=True

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--approved-source-manifest-sha256',required=True)
    ap.add_argument('--approved-old-failure-chain-sha256',required=True);a=ap.parse_args()
    try:
        load_contract();bootstrap();need(PREFLIGHT_READY)
        manifest_raw=read(STAGE/'source-manifest.json');need(sha(manifest_raw)==a.approved_source_manifest_sha256)
        validate_source_manifest(manifest_raw)
        need(sha(read(STAGE/'old-failure-chain.json'))==a.approved_old_failure_chain_sha256)
        for path in (DOMAIN,WORK,CODE):need(not os.path.lexists(path))
        state=unit_state(UNIT);group=cgroup_state(UNIT,state)
        need(state.get('LoadState')=='not-found' and not state.get('Job') and group['processes']==0 and group['populated']==0)
        live=observe_retained(a.approved_source_manifest_sha256,a.approved_old_failure_chain_sha256,original_helpers())
        print(json.dumps(live['proof'],sort_keys=True));return 0
    except BaseException:
        print('{"result":"repair_preflight_refused","read_only":true}');return 1
if __name__=='__main__':raise SystemExit(main())
