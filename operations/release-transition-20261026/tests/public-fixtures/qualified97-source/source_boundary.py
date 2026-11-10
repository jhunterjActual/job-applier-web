"""Protected exact-source loader for the shared integration draft.
No import side effects. No caller-supplied namespace is admitted by this loader.
"""
import hashlib,os,stat,subprocess
from pathlib import Path
from qualified_specs import exact_spec
CONTRACT_SHA='6eacc5f98edd31dfecf209f8e0692ab5ec993396fde6990e42e547cde598ce8f'
CONTRACT_SOURCE='operations/release-transition-20261027/repair98/repair_contract.py'
REPOSITORY='/srv/careertrellis/job-applier-web'

def need(v):
 if not v:raise RuntimeError('SOURCE_BOUND_QUALIFIED_ROUTE_REFUSED')
def read_root(path,limit=16*1024**2):
 path=Path(path)
 for parent in path.parents:
  st=parent.lstat();need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022)
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
   and not before.st_mode&0o022 and 0<=before.st_size<=limit)
  chunks=[];left=before.st_size+1
  while left:
   chunk=os.read(fd,min(left,65536))
   if not chunk:break
   chunks.append(chunk);left-=len(chunk)
  raw=b''.join(chunks);after=os.fstat(fd);leaf=path.lstat()
  fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
  need(len(raw)==before.st_size and all(getattr(before,k)==getattr(after,k)==getattr(leaf,k) for k in fields))
  return raw
 finally:os.close(fd)
def contract_namespace(raw,ops_commit):
 need(len(ops_commit)==40 and all(c in '0123456789abcdef' for c in ops_commit))
 need(hashlib.sha256(raw).hexdigest()==CONTRACT_SHA)
 result=subprocess.run(['git','-C',REPOSITORY,'show',ops_commit+':'+CONTRACT_SOURCE],stdin=subprocess.DEVNULL,
  capture_output=True,timeout=30,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','HOME':'/root'})
 need(result.returncode==0 and result.stdout==raw)
 ns={'__name__':'source_bound_repair98_contract'};exec(compile(raw,'source-bound repair98 contract','exec'),ns)
 return ns

def load_fixed(kind,ops_commit):
 spec=exact_spec(kind)
 # Exact existing98 source domain; both kinds use the same immutable reviewed
 # primitives, while97 paths and new materialization are separate SPEC data.
 stage=Path('/opt/careertrellis/staging/preparation98-repair-source-20261027/repair98-fixture-g1')
 raw=read_root(stage/'repair_contract.py',1048576)
 return spec,contract_namespace(raw,ops_commit),raw
