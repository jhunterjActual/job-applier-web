"""Source-bound planless idle authority/policy/custody. No future plan or transaction admission."""
import ast,types,sys,os,re,time,hashlib
from pathlib import Path
from datetime import datetime,timezone
from contextlib import contextmanager
from native_io import need,sha,decode,canonical,boot,process_identity,retained_run,component_bound
BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS='4095417909a8b13ad252be9420e422af3662bef5'
ROOT=Path('/var/lib/careertrellis/paired-maintenance')
LOCKS=('/run/lock/careertrellis-maintenance.lock','/run/lock/careertrellis-postgres-migration.lock','/run/lock/careertrellis-sqlite-import.lock','/run/lock/careertrellis-provider-secret-rotation.lock','/run/lock/careertrellis-generated-object-recovery.lock','/run/lock/careertrellis-pgbackrest-repository.lock','/run/lock/careertrellis-pilot-auth-acceptance.lock')

def clear_kernel_locks(role):
 # Only root-owned exact known lock inodes; never acquire/delete a lock.
 raw=Path('/proc/locks').read_bytes();need(len(raw)<=2097152);rows=raw.decode('ascii').splitlines()
 paths=[ROOT/role/'control.lock']+[Path(p) for p in LOCKS]
 for path in paths:
  try:s=path.lstat()
  except FileNotFoundError:
   # Optional locks remain absent; required locks are checked by unchanged493 idle custody.
   need(str(path) not in (str(ROOT/role/'control.lock'),'/run/lock/careertrellis-maintenance.lock'));continue
  import stat
  need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022)
  key=f'{os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x}:{s.st_ino}'
  need(not any(len(row.split())>=6 and row.split()[5].lower()==key.lower() for row in rows))
 need(not os.path.lexists('/run/careertrellis-hosted-activation.lease'))

@contextmanager
def readonly_modules(public):
 names=('collector','terminal493','transaction04','pair_boundary','readiness_bridge','_one_shot_common493','_one_shot_health_web','_one_shot_health_db');need(not any(n in sys.modules for n in names));loaded={}
 try:
  for name in ('collector','terminal493','transaction04','pair_boundary','readiness_bridge'):
   raw=public['readiness_bridge.py' if name=='readiness_bridge' else 'one_shot_readiness/'+name+'.py'];m=types.ModuleType(name);sys.modules[name]=m;loaded[name]=m
   exec(compile(raw,'verified-packet-native-readonly-'+name,'exec'),m.__dict__)
  c=loaded['collector'];c.PUBLIC_FILES={n:public['one_shot_readiness/'+n] for n in ('installed493-pins.json','transaction04-pins.json')}
  yield c
 finally:
  for name in names:
   if name in sys.modules:
    if name in loaded:need(sys.modules[name] is loaded[name])
    sys.modules.pop(name)

def recovery_history(reader):
 pending_raw=reader.read('/etc/careertrellis/authority/pending-cutover');need(pending_raw.count(b'\n')==8);pending=pending_raw.decode().splitlines()
 need(len(pending)==8 and pending[:2]==['version=1','phase=activated'])
 pairs=dict(line.split('=',1) for line in pending);need(re.fullmatch('[0-9a-f]{32}',pairs['attempt_id']) and re.fullmatch('[0-9a-f]{40}',pairs['release']))
 lines=reader.read('/etc/careertrellis/authority/post-import-recovery').decode().splitlines()
 need(len(lines)==15 and lines[:3]==['version=1','attempt_id='+pairs['attempt_id'],'release='+pairs['release']])
 need(re.fullmatch('postgres_backup_label=[0-9]{8}-[0-9]{6}F',lines[3]) and re.fullmatch('generated_object_manifest_sha256=[0-9a-f]{64}',lines[4]))
 need(lines[5:]==[key+'=true' for key in ('postgres_mirror_verified','postgres_restore_verified','postgres_bad_checksums_zero','generated_object_mirror_verified','generated_object_restore_verified','restored_database_object_reconciliation_verified','provider_keyring_recovery_verified','object_keyring_recovery_verified','decrypt_every_object_verified','reconciliation_verified')])

def authority_helper(reader,git):
 config={}
 import stat
 from native_io import parents,identity
 for name in ('/etc/careertrellis/staging.env','/etc/careertrellis/object-storage.env'):
  path=Path(name);parents(path);st=path.lstat();need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_nlink==1 and stat.S_IMODE(st.st_mode)==0o600);config[name]=identity(st)
 raw=reader.read('/usr/local/sbin/careertrellis-authority',2097152,(0o755,));expected=git.blob(BASE,'deploy/scripts/hosted-authority.sh');need(raw==expected)
 # Audited status branch: read_authority_state -> validate_authority_storage only.
 need(retained_run(['/usr/local/sbin/careertrellis-authority','status'],128).strip()==b'postgres-r2-authoritative')
 recovery_history(reader)
 for rev in ('bc68ea0dc253425a44e268452130becdf35d764e','f70417d4bd70c3eb086ea63c90938dd2f43e0b99'):
  # Audited assert-release-startable -> fixed public compatibility and exact secret-safe config selection; no source/lock/mutation.
  retained_run(['/usr/local/sbin/careertrellis-authority','assert-release-startable','/opt/careertrellis/staging/releases/'+rev],128)
 need(reader.read('/usr/local/sbin/careertrellis-authority',2097152,(0o755,))==raw)
 need(all(identity(Path(name).lstat())==row for name,row in config.items()))
 return {'helper_source_sha256':sha(raw),'config_metadata':config}

class IdleChallenge:
 def __init__(self,role,public,reader,git,runtime_profile):
  self.role=role;self.public=public;self.protected=reader;self.git=git;self.runtime_profile=runtime_profile;self._context=readonly_modules(public);self.collector=self._context.__enter__();self.bridge=sys.modules['readiness_bridge'];self.pair=sys.modules['pair_boundary'];self.collector.VERIFIED_SOURCE_MANIFEST=runtime_profile['readiness_manifest_sha256']
 def close(self):self._context.__exit__(None,None,None)
 def collect(self,request):
  need(type(request) is dict and set(request)=={'nonce','issued_at','boots','context_sha256'} and re.fullmatch('[0-9a-f]{64}',request['nonce']) and set(request['boots'])=={'web','db'})
  now=datetime.now(timezone.utc);issued=datetime.fromisoformat(request['issued_at']);need(issued.tzinfo is not None and 0<=(now-issued).total_seconds()<=30 and boot()==request['boots'][self.role]);clear_kernel_locks(self.role)
  c=self.collector;need(callable(getattr(c,'collect_preplan',None)));r=c.Reader();binding={'schema':'careertrellis.preplan-scheduler-binding/v1','nonce':request['nonce'],'source_commit':self.runtime_profile['source_commit'],'boots':request['boots'],'issued_at':request['issued_at'],'phase':'preflight','mode':'idle','release':BASE,'build':'20261017.96'}
  raw=c.collect_preplan(self.role,binding,reader=r);component_bound(raw,256*1024);self.bridge.verify_raw_context(raw,binding,self.role,c,self.runtime_profile['readiness_manifest_sha256'],now=datetime.now(timezone.utc))
  helper=authority_helper(self.protected,self.git) if self.role=='web' else None;ingress=None
  if self.role=='web':
   from native_ingress import collect as ingress_collect
   ingress=ingress_collect(int(r.units['careertrellis-web.service']['MainPID']));release=Path('/opt/careertrellis/staging');need((release/'current').resolve(strict=True)==release/'releases'/BASE and (release/'previous').resolve(strict=True)==release/'releases'/PREVIOUS)
  private=r.json('/etc/careertrellis/paired-maintenance/policy.json');public=r.json(ROOT/'public-policy.json',0o644);r.stable();clear_kernel_locks(self.role)
  need(boot()==request['boots'][self.role] and 0<=(datetime.now(timezone.utc)-issued).total_seconds()<=30)
  evidence={'source_commit':self.runtime_profile['source_commit'],'role':self.role,'nonce':request['nonce'],'context_sha256':request['context_sha256'],'boot_id':request['boots'][self.role],'authority':raw['authority'],'authority_helper':helper,'ingress':ingress,'custody':raw['custody'],'protected':{path:sha(row[2]) for path,row in r.observed.items()},'units_sha256':sha(canonical(r.units)),'raw_health_generation_sha256':raw['evaluation_generation_sha256']}
  self.last_reader=r;self.last_deadline=c.RUN_DEADLINE;self.last_boot=request['boots'][self.role];self.last_request=dict(request);self.last_binding=binding;self.raw_health=raw
  return {'schema':'careertrellis.app-plan-host-facts/v1','role':self.role,'boot_id':self.last_boot,'nonce':request['nonce'],'source_commit':self.runtime_profile['source_commit'],'observed_at':datetime.now(timezone.utc).isoformat(),'context_sha256':request['context_sha256'],'private_policy':private,'public_policy':public,'evidence_sha256':sha(canonical(evidence)),'_private_evidence':evidence}
 def stable(self):
  need(hasattr(self,'last_reader') and time.monotonic()<self.last_deadline and boot()==self.last_boot);self.last_reader.stable();clear_kernel_locks(self.role);issued=datetime.fromisoformat(self.last_request['issued_at']);need(0<=(datetime.now(timezone.utc)-issued).total_seconds()<=30 and boot()==self.last_boot and time.monotonic()<self.last_deadline)
 def qualify(self,web_raw,db_raw,binding):
  need(not hasattr(self,'adopted_generation_sha256'));need(self.role=='db' and binding==self.last_binding and canonical(db_raw)==canonical(self.raw_health));self.stable();c=self.collector;manifest=self.runtime_profile['readiness_manifest_sha256'];now=datetime.now(timezone.utc)
  self.bridge.verify_raw_context(web_raw,binding,'web',c,manifest,now=now);self.bridge.verify_raw_context(db_raw,binding,'db',c,manifest,now=now)
  if db_raw['result']=='ok':
   self.pair.check_pair(web_raw,db_raw,binding,manifest,now=now);generation=db_raw
  else:
   generation=self.bridge.qualify_local_database(web_raw,db_raw,binding,c,manifest,reader=self.last_reader);self.pair.check_pair(web_raw,db_raw,binding,manifest,now=datetime.now(timezone.utc),qualified_database=generation)
  component_bound(generation,2*1024**2);self.stable();self.adopted_generation_sha256=generation['evaluation_generation_sha256'];return generation
 def accept(self,generation,db_raw):
  need(self.role=='web');self.stable();binding=self.last_binding;c=self.collector;manifest=self.runtime_profile['readiness_manifest_sha256'];now=datetime.now(timezone.utc);web_raw=self.raw_health
  if generation.get('schema')=='careertrellis.qualified-database-generation/v1':
   envelope=self.bridge.verify_qualified_context(generation,binding,c,manifest,now=now);need(canonical(envelope['raw_web'])==canonical(web_raw) and canonical(envelope['raw_db'])==canonical(db_raw));pair=self.pair.check_pair(web_raw,db_raw,binding,manifest,now=now,qualified_database=generation);kind='joint_qualified'
  else:
   need(canonical(generation)==canonical(db_raw));self.bridge.verify_raw_context(generation,binding,'db',c,manifest,now=now);pair=self.pair.check_pair(web_raw,db_raw,binding,manifest,now=now);kind='raw_strict_healthy'
  self.accepted_generation=generation;self.health_evidence={'schema':'careertrellis.preplan-paired-health/v1','binding':binding,'raw_web_generation_sha256':web_raw['evaluation_generation_sha256'],'raw_db_generation_sha256':db_raw['evaluation_generation_sha256'],'database_generation_sha256':generation['evaluation_generation_sha256'],'database_generation_kind':kind,'pair_sha256':sha(c.canonical(pair))};self.stable();return self.health_evidence
