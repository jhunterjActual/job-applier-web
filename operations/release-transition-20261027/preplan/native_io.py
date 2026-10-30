"""Read-only native primitives. No plan, lock acquisition, provider or configuration writes."""
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
import os,stat,hashlib,json,re,socket,sys,subprocess,selectors,time,signal
from contextlib import contextmanager
OPS='504756d8b949163dbe1386e60be48cec0ec4117c'
REPOSITORY='/srv/careertrellis/job-applier-web'
HOSTS={'web':'careertrellis-staging-web-1','db':'job-applier-db-staging-01'}
FIELDS=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
class Refused(RuntimeError):pass
def need(v):
 if not v:raise Refused('PREPLAN_NATIVE_REFUSED')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
def decode(raw):
 def pairs(rows):
  d={}
  for k,v in rows:need(k not in d);d[k]=v
  return d
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
def identity(s):return tuple(getattr(s,k) for k in FIELDS)
def boot():
 fd=os.open('/proc/sys/kernel/random/boot_id',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:raw=os.read(fd,4096)
 finally:os.close(fd)
 need(re.fullmatch(b'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\n?',raw));return raw.decode().strip()
def bootstrap(role):
 need(type(role) is str and role in HOSTS and sys.platform=='linux' and os.geteuid()==0 and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode and socket.gethostname()==HOSTS[role])
 return boot()
def process_identity(pid):
 need(type(pid) is int and pid>1);base=Path('/proc')/str(pid)
 raw=(base/'stat').read_text();tail=raw[raw.rfind(')')+2:].split();status=(base/'status').read_text();uids=next(x.split()[1:] for x in status.splitlines() if x.startswith('Uid:'))
 need(uids==['0']*4);again=(base/'stat').read_text();need(again==raw)
 return {'pid':pid,'parent_pid':int(tail[1]),'birth':tail[19],'state':tail[0]}
def parents(path):
 for p in Path(path).parents:
  s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)

class ProtectedReader:
 def __init__(self):self.observed={};self.total=0
 def read(self,path,maximum=2097152,modes=(0o600,)):
  p=Path(path);need(p.is_absolute());parents(p);before=p.lstat()
  need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1 and not before.st_mode&0o022 and stat.S_IMODE(before.st_mode) in modes and 0<=before.st_size<=maximum)
  fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
  try:
   need(identity(os.fstat(fd))==identity(before));chunks=[];size=0
   while True:
    block=os.read(fd,min(65536,maximum+1-size))
    if not block:break
    size+=len(block);need(size<=before.st_size);chunks.append(block)
   raw=b''.join(chunks);need(size==before.st_size and identity(os.fstat(fd))==identity(before)==identity(p.lstat()))
  finally:os.close(fd)
  old=self.observed.get(str(p));row=(maximum,tuple(modes),identity(before),raw)
  need(old is None or old==row)
  if old is None:self.total+=len(raw);need(self.total<=64*1024**2)
  self.observed[str(p)]=row;return raw
 def __call__(self,path):return self.read(path)
 def stable(self):
  for path,(maximum,modes,_,raw) in list(self.observed.items()):need(self.read(path,maximum,modes)==raw)

@contextmanager
def deferred_child_signals():
 old={}
 try:
  for number in tuple(n for n in (getattr(signal,'SIGHUP',None),signal.SIGTERM,signal.SIGINT) if n is not None):
   old[number]=signal.getsignal(number);signal.signal(number,lambda *_:None)
  yield
 finally:
  for number,handler in old.items():signal.signal(number,handler)

def terminal_child(child):
 while True:
  try:
   if child.poll() is not None:return
   child.stdout.read(65536);child.wait()
  except BaseException:continue

def retained_run(argv,maximum=134217728,input_raw=None):
 """Only terminal owned children release signal custody; no timeoutkill/retry."""
 need(type(argv) is list and argv and all(type(x) is str for x in argv));need(input_raw is None) # Private relay uses its dedicated duplex protocol.
 env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','GIT_NO_LAZY_FETCH':'1','GIT_TERMINAL_PROMPT':'0','GIT_OPTIONAL_LOCKS':'0','PYTHONDONTWRITEBYTECODE':'1'}
 with deferred_child_signals():
  child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
  try:
   chunks=[];size=0;overflow=False
   while True:
    block=child.stdout.read(65536)
    if not block:break
    size+=len(block)
    if size<=maximum:chunks.append(block)
    else:overflow=True
   rc=child.wait();need(rc==0 and not overflow);return b''.join(chunks)
  except BaseException:
   terminal_child(child);raise Refused('PREPLAN_CHILD_FAILED') from None
  finally:
   terminal_child(child)
   try:child.stdout.close()
   except BaseException:pass

class GitObjects:
 def __init__(self):self.cache={}
 def blob(self,commit,path):
  need(type(commit) is str and re.fullmatch('[0-9a-f]{40}',commit) and type(path) is str)
  p=PurePosixPath(path);need(str(p)==path and not p.is_absolute() and '..' not in p.parts and ':' not in path and '\\' not in path)
  raw=retained_run(['/usr/bin/git','-C',REPOSITORY,'cat-file','blob',commit+':'+path],2097152)
  key=(commit,path);need(key not in self.cache or self.cache[key]==raw);self.cache[key]=raw;return raw
 def run(self,*args):
  need(len(args)>=4 and all(type(x)is str for x in args) and args[0]=='git' and args[1:3]==('-C',REPOSITORY))
  tail=list(args[3:]);base='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4';revs=('bc68ea0dc253425a44e268452130becdf35d764e','f70417d4bd70c3eb086ea63c90938dd2f43e0b99')
  allowed=[]
  for rev in revs:
   allowed.extend([['rev-parse',rev+'^{commit}'],['archive',rev],['diff',base,rev,'--','backend/migrations','deploy/postgres'],['-c','core.abbrev=40','diff','--binary','--no-ext-diff','--no-textconv',base,rev,'--']])
  need(tail in allowed)
  # Callers are exact authenticated LinuxHost.source AST, never arbitrary CLI.
  return retained_run(['/usr/bin/git','-C',REPOSITORY]+tail)

# Private transport component budgets measure encoded ASCII JSON, not object count.
def wire_bytes(value):
 return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
def component_bound(value,maximum):
 need(type(maximum)is int and maximum>0);raw=wire_bytes(value);need(len(raw)<=maximum);return value

PUBLIC_PHASES=('BOOTSTRAP','SOURCE_AUTH','HISTORY','RUNTIME_SCAN','PEER_HELLO','CHALLENGE','QUALIFICATION','PAIR_HISTORY','AUTHENTICATION','MATERIALIZATION','ALLOCATION','TRANSPORT','DELIVERY_PACKET','DELIVERY_PLAN','PLAN_EXPORT')
PUBLIC_PHASE='BOOTSTRAP'
PUBLIC_ORIGINS=('native_io.py','native_runtime.py','native_idle.py','native_pair.py','public_version.py','native_ingress.py','preplan_history.py','preplan_program.py','policy_encoding.py','pure_plan_contract.py','assemble_plan.py','allocation_entry.py','package_bootstrap.py','private_channel.py','session_server.py','public_delivery.py')
def set_public_phase(value):
 global PUBLIC_PHASE
 need(type(value)is str and value in PUBLIC_PHASES);PUBLIC_PHASE=value

def public_failure(error):
 # Exception values/args/locals/paths are NEVER inspected or serialized.
 category='REFUSED' if type(error)is Refused else 'IO' if type(error)in (OSError,IOError,FileNotFoundError,PermissionError,BrokenPipeError) else 'TIMEOUT' if type(error)is TimeoutError else 'VALUE' if type(error)is ValueError else 'TYPE' if type(error)is TypeError else 'OTHER'
 origin='PUBLIC_PROGRAM';line=0;tb=error.__traceback__
 while tb is not None:
  name=Path(tb.tb_frame.f_code.co_filename).name
  if name in PUBLIC_ORIGINS and name!='native_io.py':origin=name;line=tb.tb_lineno
  tb=tb.tb_next
 return {'schema':'careertrellis.preplan-public-failure/v1','phase':PUBLIC_PHASE,'origin':origin,'line':line,'category':category}

def validate_public_failure(value):
 need(type(value)is dict and set(value)=={'schema','phase','origin','line','category'} and value['schema']=='careertrellis.preplan-public-failure/v1' and type(value['phase'])is str and value['phase'] in PUBLIC_PHASES and type(value['origin'])is str and value['origin'] in PUBLIC_ORIGINS+('PUBLIC_PROGRAM',) and type(value['line'])is int and 0<=value['line']<=100000 and type(value['category'])is str and value['category'] in ('REFUSED','IO','TIMEOUT','VALUE','TYPE','OTHER'));return dict(value)

# Only the four authenticated original504 full-map leaves have the original
# historical snapshot16MiB contract. Generic protected reads remain2MiB.
HISTORICAL_LARGE_PATHS=frozenset({
 '/var/backups/careertrellis/preparation97-corrected-20261027/corrected97-fixture-g1/runtime-before.json',
 '/var/backups/careertrellis/preparation97-corrected-20261027/corrected97-fixture-g1/runtime-manifest.json',
 '/var/backups/careertrellis/preparation98-repair-20261027/repair98-fixture-g1/runtime-before.json',
 '/var/backups/careertrellis/preparation98-repair-20261027/repair98-fixture-g1/runtime-manifest.json'})

class HistoricalReadAdapter:
 def __init__(self,reader):
  need(type(reader) is ProtectedReader);self.reader=reader
 def __call__(self,path):
  need(type(path) is str and path.startswith('/') and not path.startswith('//') and '\\' not in path and ':' not in path)
  p=PurePosixPath(path);need(str(p)==path and '..' not in p.parts)
  maximum=16777216 if path in HISTORICAL_LARGE_PATHS else 2097152
  return self.reader.read(path,maximum,(0o600,))
