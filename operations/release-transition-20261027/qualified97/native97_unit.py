"""Fixed97 read-only native unit/cgroup snapshot; no generic unit or service action."""
import os,re,subprocess
from pathlib import Path
UNIT='careertrellis-preparation97-qualified-fixture-g1-20261027.service'
CG=Path('/sys/fs/cgroup/system.slice')/UNIT
KEYS={'LoadState','ActiveState','SubState','MainPID','ControlPID','Job','ControlGroup','Result','ExecMainStatus','InvocationID','Description'}
def need(v):
 if not v:raise RuntimeError('QUALIFIED97_UNIT_REFUSED')
def snapshot():
 p=subprocess.run(['systemctl','show',UNIT,'--property='+','.join(sorted(KEYS))],stdin=subprocess.DEVNULL,capture_output=True,timeout=15,
  env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
 need(p.returncode in (0,1,4) and len(p.stdout)<=16384);state={}
 for line in p.stdout.decode('utf8').splitlines():
  k,sep,v=line.partition('=');need(sep and k in KEYS and k not in state);state[k]=v
 need(state.get('LoadState') in ('not-found','loaded'))
 actual=state.get('ControlGroup','')
 need(not actual or actual=='/system.slice/'+UNIT)
 exists=CG.exists()
 if exists:
  need(not CG.is_symlink());raw=(CG/'cgroup.procs').read_bytes();need(len(raw)<=65536)
  pids=raw.splitlines();need(all(re.fullmatch(rb'[1-9][0-9]{0,9}',x) for x in pids))
  events=(CG/'cgroup.events').read_text();need(len(events)<=4096);ev=dict(line.split() for line in events.splitlines())
  need(ev.get('populated') in ('0','1'));populated=int(ev['populated'])
 else:pids=[];populated=0
 return state,{'path':str(CG),'exists':exists,'processes':len(pids),'populated':populated}
def require_absent():
 state,cg=snapshot()
 need(state['LoadState']=='not-found' and state.get('MainPID','0') in ('','0') and state.get('ControlPID','0') in ('','0')
  and not state.get('Job') and not state.get('InvocationID') and cg['exists'] is False and cg['processes']==0 and cg['populated']==0)
 return state,cg
