"""Bounded two-SSH memory relay derived from existing drive; bodies NEVER logged."""
import json,re,subprocess,sys,queue,threading,signal,hashlib,os,stat
from pathlib import Path
from native_io import need,decode,canonical,deferred_child_signals,validate_public_failure,public_failure,set_public_phase
SSH=r'C:\Windows\System32\OpenSSH\ssh.exe'
HOSTS={'web':'hunter@129.212.153.245','db':'hunter@157.245.113.244'}
MAXIMUM=3*1024**2
RAW_HEALTH_MAXIMUM=256*1024
CHALLENGE_MAXIMUM=512*1024
QUALIFIED_MAXIMUM=2*1024**2
MODULE_ORDER=('native_io','preplan_history','native_runtime','native_pair','native_idle','public_version','native_ingress','preplan_program','policy_encoding','pure_plan_contract','assemble_plan','package_bootstrap','public_delivery','allocation_entry','private_channel','session_server')

# Literal bootstrap runs before imports from staged source. Only stdlib, Linux/root
# and exact protected files; all semantic exec uses the SAME cached verified bytes.
BOOTSTRAP=r'''import sys,os,stat,json,hashlib,types,socket,re
sha,role,mode=sys.argv[1:]
if not (sys.platform=='linux' and os.geteuid()==0 and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode and role in ('web','db') and socket.gethostname()=={'web':'careertrellis-staging-web-1','db':'job-applier-db-staging-01'}[role] and mode in ('inspect','allocate') and re.fullmatch('[0-9a-f]{64}',sha)):raise SystemExit(1)
from pathlib import Path
root=Path('/opt/careertrellis/staging')/('preplan-source-'+sha)
fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
def read(p,limit):
 for q in p.parents:
  x=q.lstat()
  if not (stat.S_ISDIR(x.st_mode) and x.st_uid==0 and not x.st_mode&0o022):raise SystemExit(1)
 before=p.lstat();ident=tuple(getattr(before,k) for k in fields)
 if not (stat.S_ISREG(before.st_mode) and before.st_uid==0 and stat.S_IMODE(before.st_mode)==0o600 and before.st_nlink==1 and 0<=before.st_size<=limit):raise SystemExit(1)
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  if tuple(getattr(os.fstat(fd),k) for k in fields)!=ident:raise SystemExit(1)
  raw=b''
  while len(raw)<=before.st_size:
   block=os.read(fd,min(65536,before.st_size+1-len(raw)))
   if not block:break
   raw+=block
  if not (len(raw)==before.st_size and tuple(getattr(os.fstat(fd),k) for k in fields)==ident==tuple(getattr(p.lstat(),k) for k in fields)):raise SystemExit(1)
  return raw
 finally:os.close(fd)
def pairs(rows):
 d={}
 for k,v in rows:
  if k in d:raise SystemExit(1)
  d[k]=v
 return d
raw=read(root/'source-manifest.json',65536)
if hashlib.sha256(raw).hexdigest()!=sha:raise SystemExit(1)
m=json.loads(raw,object_pairs_hook=pairs);cache={};total=0
if not (type(m)is dict and set(m)=={'schema','program_commit','prefix','runtime','files'} and m['schema']=='careertrellis.preplan-source-package/v1' and m['prefix']=='operations/release-transition-20261027/preplan/' and re.fullmatch('[0-9a-f]{40}',m['program_commit'])):raise SystemExit(1)
for name,row in m['files'].items():
 if not (type(name)is str and not Path(name).is_absolute() and '..'not in Path(name).parts and str(Path(name))==name and type(row)is dict and set(row)=={'sha256','bytes'} and type(row['bytes'])is int and 0<row['bytes']<=2097152 and re.fullmatch('[0-9a-f]{64}',row['sha256'])):raise SystemExit(1)
 body=read(root/name,2097152);total+=len(body)
 if not (len(body)==row['bytes'] and hashlib.sha256(body).hexdigest()==row['sha256'] and total<=8*1024**2):raise SystemExit(1)
 cache[name]=body
order=('native_io','preplan_history','native_runtime','native_pair','native_idle','public_version','native_ingress','preplan_program','policy_encoding','pure_plan_contract','assemble_plan','package_bootstrap','public_delivery','allocation_entry','private_channel','session_server')
if any(name in sys.modules for name in order):raise SystemExit(1)
for name in order:
 value=types.ModuleType(name);sys.modules[name]=value;exec(compile(cache[name+'.py'],str(root/(name+'.py')),'exec'),value.__dict__)
sys.modules['package_bootstrap'].source_manifest(raw,sha)
_,again=sys.modules['package_bootstrap'].load_staged(role,sha)
if again!=cache:raise SystemExit(1)
raise SystemExit(sys.modules['session_server'].main(role,mode,sha,cache,m['runtime']))
'''

def command(role,program_sha,mode):
 need(type(role)is str and role in HOSTS and type(program_sha)is str and re.fullmatch('[0-9a-f]{64}',program_sha) and type(mode)is str and mode in ('inspect','allocate'))
 # No policy/body/secret argv. Source bootstrap is public code; shell quote fixed -c body.
 quoted="'"+BOOTSTRAP.replace("'","'\"'\"'")+"'"
 return [SSH,'-T','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','ForwardAgent=no','-o','ClearAllForwardings=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','HostKeyAlgorithms=ssh-ed25519','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2',HOSTS[role],'sudo','-n','/usr/bin/python3','-I','-S','-B','-c',quoted,program_sha,role,mode]

class Reader:
 def __init__(self,stream):
  self.items=queue.Queue(maxsize=4)
  def collect():
   try:
    while True:
     raw=stream.readline(MAXIMUM+1);self.items.put(raw)
     if not raw:return
   except BaseException:self.items.put(None)
  threading.Thread(target=collect,daemon=True).start()
 def read(self):
  try:raw=self.items.get(timeout=180)
  except queue.Empty:raise RuntimeError('PRIVATE_TRANSPORT_UNKNOWN') from None
  need(type(raw)is bytes and 0<len(raw)<=MAXIMUM and raw.endswith(b'\n'));return raw

def send(stream,value):
 raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')+b'\n';need(len(raw)<=MAXIMUM);offset=0
 while offset<len(raw):
  written=stream.write(memoryview(raw)[offset:]);need(type(written)is int and 0<written<=len(raw)-offset);offset+=written
 stream.flush()

def public_result(value,mode,program_sha,runtime_profile):
 from datetime import datetime
 from preplan_program import profile
 binding=profile(runtime_profile)
 need(type(value)is dict)
 if mode=='inspect':
  need(set(value)=={'result','source_commit','packet_sha256','program_sha256','nonce','facts_sha256','runtime_file_count','historical_receipts','scan_started_at','scan_finished_at'} and value['result']=='PREPLAN_INSPECTED_NOT_ALLOCATED' and value['source_commit']==binding['source_commit'] and value['packet_sha256']==binding['packet_sha256'] and value['program_sha256']==program_sha and type(value['runtime_file_count'])is int and 0<value['runtime_file_count']<=200000 and type(value['historical_receipts'])is int and value['historical_receipts']==4)
  need(all(type(value[k])is str for k in ('scan_started_at','scan_finished_at')));start=datetime.fromisoformat(value['scan_started_at']);end=datetime.fromisoformat(value['scan_finished_at']);need(start.tzinfo is not None and end.tzinfo is not None and start<=end);hashes=('nonce','facts_sha256')
 else:
  need(mode=='allocate' and set(value)=={'result','nonce','plan_sha256','decision_sha256','authorizes_dispatch'} and value['result']=='PRIVATE_PLAN_ALLOCATED_NOT_DISPATCHED' and value['authorizes_dispatch'] is False);hashes=('nonce','plan_sha256','decision_sha256')
 need(all(type(value[k])is str and re.fullmatch('[0-9a-f]{64}',value[k]) for k in hashes));return value

def actor(value):
 need(type(value)is dict and set(value)=={'pid','parent_pid','birth','state'} and type(value['pid'])is int and value['pid']>1 and type(value['parent_pid'])is int and value['parent_pid']>0 and type(value['birth'])is str and re.fullmatch('[1-9][0-9]*',value['birth']) and type(value['state'])is str and value['state'] in ('R','S','D','T','t','I'));return dict(value)

def boot_id(value):
 need(type(value)is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value));return value

def custody_value(owner,hello,request,program_sha,runtime_profile):
 from preplan_program import validate_peer
 from datetime import datetime,timezone
 need(type(owner)is dict and set(owner)=={'kind','role','actor','boot','program_sha256'} and owner['kind']=='owner' and owner['role']=='web' and owner['program_sha256']==program_sha)
 peer=validate_peer(hello,'db',program_sha,runtime_profile);web_actor=actor(owner['actor']);db_actor=actor(peer['actor']);boots={'web':boot_id(owner['boot']),'db':boot_id(peer['boot_id'])}
 need(type(request)is dict and set(request)=={'nonce','issued_at','boots','context_sha256'} and request['boots']==boots and all(type(request[k])is str and re.fullmatch('[0-9a-f]{64}',request[k]) for k in ('nonce','context_sha256')))
 need(type(request['issued_at'])is str);issued=datetime.fromisoformat(request['issued_at']);need(issued.tzinfo is not None and 0<=(datetime.now(timezone.utc)-issued).total_seconds()<=30)
 contract={'schema':'careertrellis.app-plan-source-contract/v1','mode':'actual','source_commit':runtime_profile['source_commit'],'packet_sha256':runtime_profile['packet_sha256'],'readiness_manifest_sha256':runtime_profile['readiness_manifest_sha256'],'collector_source_sha256':program_sha}
 need(request['context_sha256']==hashlib.sha256(canonical(contract)).hexdigest())
 return {'schema':'careertrellis.preplan-observable-custody/v1','program_sha256':program_sha,'runtime_source_commit':runtime_profile['source_commit'],'packet_sha256':runtime_profile['packet_sha256'],'readiness_manifest_sha256':runtime_profile['readiness_manifest_sha256'],'boots':boots,'actors':{'web':web_actor,'db':db_actor},'challenge':dict(request),'scope':'public-reconciliation-selectors-only','durability':'file-fsync-and-readback; directory-durability-not-claimed'}

def create_custody_receipt(path,value):
 # PUBLIC selectors only. Existing task-specific parent; no implicit mkdir/adopt.
 need(type(path)is str);p=Path(path);need(p.is_absolute() and p.name=='preplan-observable-custody.json' and p.parent.resolve(strict=True)==p.parent)
 for q in (p.parent,*p.parent.parents):need(not q.is_symlink() and not (hasattr(q,'is_junction') and q.is_junction()))
 before=p.parent.stat();need(stat.S_ISDIR(before.st_mode));parent_identity=(before.st_dev,before.st_ino);raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')+b'\n';need(len(raw)<=8192)
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0),0o600)
 try:
  with os.fdopen(os.dup(fd),'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
  own=os.fstat(fd);need(stat.S_ISREG(own.st_mode) and own.st_nlink==1 and own.st_size==len(raw));need(p.read_bytes()==raw);after=p.lstat();need((after.st_dev,after.st_ino)==(own.st_dev,own.st_ino))
 finally:os.close(fd)
 after_parent=p.parent.stat();need((after_parent.st_dev,after_parent.st_ino)==parent_identity)
 return hashlib.sha256(raw).hexdigest()

class PublicSessionUnknown(RuntimeError):
 def __init__(self,failure):self.failure=validate_public_failure(failure);super().__init__('PRIVATE_PREPLAN_TRANSPORT_UNKNOWN')

def relay(program_sha,mode,runtime_profile,custody_receipt=None):
 """Root invokes after source-stage/SSH-target checks. Returns PUBLIC fields only."""
 from preplan_program import profile
 runtime_profile=profile(runtime_profile);need(mode in ('inspect','allocate'));need(mode!='allocate' or type(custody_receipt)is str)
 remote_failure=None;web=None;db=None;seq=0;owner_seen=False;closed_inputs=set();owner=None;db_hello=None;custody_written=False
 def close_input(child):
  if child is not None and id(child) not in closed_inputs:
   closed_inputs.add(id(child));child.stdin.close()
 with deferred_child_signals():
  try:
   web=subprocess.Popen(command('web',program_sha,mode),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL);wr=Reader(web.stdout);dr=None
   while True:
    frame=decode(wr.read());need(type(frame)is dict)
    if frame.get('kind')=='unknown':
     need(set(frame)=={'kind','no_replay','failure'} and frame['no_replay'] is True);remote_failure=validate_public_failure(frame['failure']);raise PublicSessionUnknown(remote_failure)
    if frame.get('kind')=='owner':
     need(not owner_seen);owner_seen=True
     need(set(frame)=={'kind','role','actor','boot','program_sha256'} and frame['role']=='web' and frame['program_sha256']==program_sha);actor(frame['actor']);boot_id(frame['boot']);owner=frame;continue
    if frame.get('kind')=='terminal':
     need(set(frame)=={'kind','value'});value=frame['value'];need(type(value)is dict and value.get('result') in ('PREPLAN_INSPECTED_NOT_ALLOCATED','PRIVATE_PLAN_ALLOCATED_NOT_DISPATCHED'))
     allowed={'result','source_commit','packet_sha256','program_sha256','nonce','facts_sha256','runtime_file_count','historical_receipts','scan_started_at','scan_finished_at'} if mode=='inspect' else {'result','nonce','plan_sha256','decision_sha256','authorizes_dispatch'}
     need(set(value)==allowed and db is not None and seq>=4 and (mode!='allocate' or custody_written));public_result(value,mode,program_sha,runtime_profile)
     # Only exact closed sanitized fields leave this process.
     if db is not None:close_input(db)
     close_input(web);webcode=web.wait();dbcode=0 if db is None else db.wait();need(webcode==dbcode==0);return value
    need(set(frame)=={'kind','request'} and frame['kind']=='db_rpc');request=frame['request'];need(type(request)is dict and set(request)=={'seq','program_sha256','action','args'} and type(request['seq'])is int and request['seq']==seq and request['program_sha256']==program_sha and request['action']==('hello' if seq==0 else 'challenge' if seq==1 else 'qualify' if seq==2 else 'stable'))
    if seq==1:
     need(owner is not None and db_hello is not None and set(request['args'])=={'request'});proof=custody_value(owner,db_hello,request['args']['request'],program_sha,runtime_profile)
     if custody_receipt is not None:create_custody_receipt(custody_receipt,proof);custody_written=True
     need(mode!='allocate' or custody_written)
    if db is None:db=subprocess.Popen(command('db',program_sha,'inspect'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL);dr=Reader(db.stdout)
    send(db.stdin,request);response=decode(dr.read());need(type(response)is dict and set(response)=={'seq','ok','value'} and type(response['seq'])is int and response['seq']==seq and response['ok'] is True)
    if seq==0:
     from preplan_program import validate_peer
     db_hello=validate_peer(response['value'],'db',program_sha,runtime_profile);actor(db_hello['actor']);boot_id(db_hello['boot_id'])
    send(web.stdin,response);seq+=1
  except BaseException as error:
   if remote_failure is None:
    set_public_phase('TRANSPORT');remote_failure=public_failure(error)
   # Original transports remain owned; closing input requests ordinary read-only
   # EOF, never signals/kills/retries. Claimed allocator may retain remote owner.
   for child in (db,web):
    if child is not None:
     try:close_input(child)
     except BaseException:pass
   for child in (db,web):
    if child is not None:
     while True:
      try:
       if child.poll() is not None:break
       child.stdout.read(65536);child.wait()
      except BaseException:continue
   raise PublicSessionUnknown(remote_failure) from None

def main(argv=None):
 """Public manifest only. Allocation remains an explicit separate mode, never default."""
 import argparse
 from package_bootstrap import source_manifest
 parser=argparse.ArgumentParser();parser.add_argument('--source-manifest',required=True);parser.add_argument('--approved-source-sha256',required=True);parser.add_argument('--mode',choices=('inspect','allocate'),required=True);parser.add_argument('--custody-receipt')
 args=parser.parse_args(argv)
 # The manifest is PUBLIC and externally approved; no private capsule/file is accepted.
 raw=Path(args.source_manifest).read_bytes();m=source_manifest(raw,args.approved_source_sha256)
 try:result=relay(args.approved_source_sha256,args.mode,m['runtime'],args.custody_receipt)
 except PublicSessionUnknown as error:print(json.dumps({'result':'PREPLAN_UNKNOWN_NO_REPLAY','failure':error.failure},sort_keys=True,separators=(',',':')));return 1
 except BaseException as error:print(json.dumps({'result':'PREPLAN_UNKNOWN_NO_REPLAY','failure':public_failure(error)},sort_keys=True,separators=(',',':')));return 1
 print(json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=True));return 0
if __name__=='__main__':raise SystemExit(main())
