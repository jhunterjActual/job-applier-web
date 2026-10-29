"""Explicit public-source staging/bootstrap; never invoked by read-only collection."""
import os,stat,json,re,hashlib,types,sys,time
from pathlib import Path,PurePosixPath
from native_io import need,sha,canonical,decode,parents,identity,ProtectedReader,bootstrap,boot,process_identity,deferred_child_signals,GitObjects
ROOT=Path('/opt/careertrellis/staging');RECEIPTS=Path('/var/backups/careertrellis')
PROGRAM_FILES=('native_io.py','native_runtime.py','native_pair.py','native_idle.py','public_version.py','native_ingress.py','preplan_history.py','preplan_program.py','policy_encoding.py','pure_plan_contract.py','assemble_plan.py','public_delivery.py','allocation_entry.py','private_channel.py','session_server.py','package_bootstrap.py')
PREFIX='operations/release-transition-20261027/preplan/'

def source_manifest(raw,approved_sha):
 need(type(raw) is bytes and len(raw)<=65536 and type(approved_sha) is str and re.fullmatch('[0-9a-f]{64}',approved_sha) and sha(raw)==approved_sha)
 m=decode(raw);need(type(m) is dict and set(m)=={'schema','program_commit','prefix','runtime','files'} and m['schema']=='careertrellis.preplan-source-package/v1' and type(m['program_commit']) is str and re.fullmatch('[0-9a-f]{40}',m['program_commit']) and m['program_commit']!='0'*40 and m['prefix']==PREFIX)
 need(type(m['files']) is dict and set(m['files'])==set(PROGRAM_FILES)|{'packet_manifest.json','historical_packet_manifest.json','scanner-original-native_backend.py','scanner-original-canonical_primitives.py'}|set('public-runtime35/'+n for n in public_paths()))
 from preplan_program import profile
 profile(m['runtime'])
 for name,row in m['files'].items():
  need(type(name) is str and type(row) is dict and set(row)=={'sha256','bytes'} and type(row['bytes']) is int and 0<row['bytes']<=2097152 and type(row['sha256']) is str and re.fullmatch('[0-9a-f]{64}',row['sha256']))
 return m

def public_paths():
 # Full runtime manifest determines these after its pinned bytes are supplied;
 # static complete membership comes from original packet35, not caller lists.
 return ('accepted_pair_validator.py', 'canonical_primitives.py', 'current_health_validator.py', 'db_audit.py', 'historical98_controls.py', 'native_backend.py', 'native_peer.py', 'one_shot_readiness/SOURCE_MANIFEST.json', 'one_shot_readiness/collector.py', 'one_shot_readiness/installed493-pins.json', 'one_shot_readiness/pair_boundary.py', 'one_shot_readiness/terminal493.py', 'one_shot_readiness/transaction04-pins.json', 'one_shot_readiness/transaction04.py', 'peer_server.py', 'policy_encoding.py', 'prepared97_checks.py', 'prepared97_context.py', 'prepared97_history.py', 'prepared97_reader.py', 'prepared97_records.py', 'prepared97_sources.py', 'prepared97_specs.py', 'prepared98_history.py', 'prepared_evidence.py', 'prepared_reader.py', 'prepared_registries.py', 'prepared_sources.py', 'prepared_types.py', 'protected_state.py', 'readiness_bridge.py', 'release_transaction.py', 'reviewed_probe.py', 'terminal_validation.py', 'web_coordinator.py')

def verify_sources(manifest_raw,approved_sha,raws,git_blob=None):
 m=source_manifest(manifest_raw,approved_sha);need(type(raws) is dict and all(type(k)is str and type(v)is bytes for k,v in dict.items(raws)) and set(raws)==set(m['files']));total=0
 for name,row in m['files'].items():
  raw=raws[name];need(type(raw) is bytes and len(raw)==row['bytes'] and sha(raw)==row['sha256']);total+=len(raw)
  if git_blob is not None:
   if name in PROGRAM_FILES:need(git_blob(m['program_commit'],PREFIX+name)==raw)
   elif name.startswith('scanner-original-'):need(git_blob('504756d8b949163dbe1386e60be48cec0ec4117c','operations/release-transition-20261026/'+name[len('scanner-original-'):])==raw)
   elif name.startswith('public-runtime35/'):need(git_blob(m['runtime']['source_commit'],'operations/release-transition-20261026/'+name[len('public-runtime35/'):])==raw)
 need(total<=8*1024**2)
 from preplan_program import public_sources
 class CapsuleGit:
  def blob(self,commit,path):
   from preplan_history import OPS,PREFIX
   need(commit==m['runtime']['source_commit'] and path.startswith(PREFIX));return raws['public-runtime35/'+path[len(PREFIX):]]
 need(sha(raws['historical_packet_manifest.json'])=='8014c138db42ec3d4ea5d4d2c0e0e0ae9ef11e7b9da98238ae4ec5c7e3360e17')
 current=public_sources(raws['packet_manifest.json'],CapsuleGit(),runtime_profile=m['runtime'])
 from native_runtime import methods,PINS
 methods(current,{name:raws['scanner-original-'+name] for name in PINS},m['runtime']['scanner_methods'])
 return m

def create(path,raw):
 parents(path);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
 try:
  os.fchmod(fd,0o600);offset=0
  while offset<len(raw):n=os.write(fd,raw[offset:]);need(n>0);offset+=n
  os.fsync(fd)
 finally:os.close(fd)
 directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(directory)
 finally:os.close(directory)
 need(ProtectedReader().read(path,max(2097152,len(raw)))==raw)

def hold():
 while True:
  try:time.sleep(1)
  except BaseException:pass

def stage_public(role,manifest_raw,approved_sha,raws,binding):
 """Root-only separate mutation entry, cached public sources BEFORE exclusive intent."""
 current_boot=bootstrap(role);m=verify_sources(manifest_raw,approved_sha,raws,GitObjects().blob if role=='web' else None);raws=dict(raws);stage=ROOT/('preplan-source-'+approved_sha);intent=RECEIPTS/('preplan-source-'+approved_sha+'-'+role+'-intent.json');success=intent.with_name(intent.name.replace('-intent.json','-success.json'))
 parents(stage);parents(intent);fs=os.statvfs(ROOT);need(fs.f_bavail*fs.f_frsize-sum(len(v) for v in raws.values())-65536>=5*1024**3 and fs.f_favail>=len(raws)+1024);need(not any(os.path.lexists(p) for p in (stage,intent,success)));actor=process_identity(os.getpid());claimed=False
 with deferred_child_signals():
  try:
   # Fixed authenticated source implementation, never a caller callback/flag.
   from public_delivery import preflight,binding_current
   _,binding_sha=binding_current(role,m['runtime'],binding,expected_boot=current_boot)
   public={name[len('public-runtime35/'):]:raw for name,raw in raws.items() if name.startswith('public-runtime35/')}
   state=preflight(role,public,m['runtime'],binding)
   need(state['boot']==current_boot and state['binding_sha256']==binding_sha)
   binding_current(role,m['runtime'],binding,expected_boot=current_boot,expected_sha=binding_sha)
   claimed=True;record={'schema':'careertrellis.preplan-public-stage-intent/v1','manifest_sha256':approved_sha,'program_commit':m['program_commit'],'boot_id':current_boot,'role':role,'actor':actor,'binding_sha256':binding_sha}
   create(intent,canonical(record));os.mkdir(stage,0o700)
   fd=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
   try:os.fsync(fd)
   finally:os.close(fd)
   for name,raw in raws.items():
    p=stage/name
    for parent in reversed(p.relative_to(stage).parents):
     if str(parent)=='.':continue
     q=stage/parent
     if not q.exists():os.mkdir(q,0o700)
     need(stat.S_ISDIR(q.lstat().st_mode) and q.lstat().st_uid==0 and stat.S_IMODE(q.lstat().st_mode)==0o700)
    create(p,raw)
   create(stage/'source-manifest.json',manifest_raw);need(boot()==current_boot)
   create(success,canonical(dict(record,schema='careertrellis.preplan-public-stage-success/v1',intent_sha256=sha(canonical(record)),file_count=len(raws))))
   return {'result':'public_source_staged_only','manifest_sha256':approved_sha,'source_files':len(raws)}
  except BaseException:
   if claimed:hold()
   raise

def load_staged(role,approved_sha):
 bootstrap(role);need(type(approved_sha) is str and re.fullmatch('[0-9a-f]{64}',approved_sha));root=ROOT/('preplan-source-'+approved_sha);reader=ProtectedReader();raw=reader.read(root/'source-manifest.json',65536);m=source_manifest(raw,approved_sha);caps={n:reader.read(root/n,2097152) for n in m['files']};verify_sources(raw,approved_sha,caps)
 intent=RECEIPTS/('preplan-source-'+approved_sha+'-'+role+'-intent.json');ir=reader.read(intent,65536);sr=reader.read(intent.with_name(intent.name.replace('-intent.json','-success.json')),65536);i=decode(ir);s=decode(sr)
 need(type(i)is dict and set(i)=={'schema','manifest_sha256','program_commit','boot_id','role','actor','binding_sha256'} and i['schema']=='careertrellis.preplan-public-stage-intent/v1')
 need(type(s)is dict and s==dict(i,schema='careertrellis.preplan-public-stage-success/v1',intent_sha256=sha(ir),file_count=len(caps)) and i['manifest_sha256']==approved_sha and i['role']==role and i['program_commit']==m['program_commit'])
 need(type(i['binding_sha256'])is str and re.fullmatch('[0-9a-f]{64}',i['binding_sha256']))
 actor=i['actor'];need(type(actor) is dict and set(actor)=={'pid','parent_pid','birth','state'} and type(actor['pid']) is int and actor['pid']>1 and type(actor['birth']) is str and re.fullmatch('[0-9]+',actor['birth']))
 if boot()==i['boot_id']:
  try:need(process_identity(actor['pid'])['birth']!=actor['birth'])
  except FileNotFoundError:pass
 reader.stable();return root,caps
