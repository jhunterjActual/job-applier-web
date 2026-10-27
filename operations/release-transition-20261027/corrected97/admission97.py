"""Fixed97 admission, new materialization and retained owner/seal adapter.
Source-only review packet: every readiness flag remains false.
"""
import contextlib,datetime as dt,grp,hashlib,json,os,shutil,stat
import native97_unit
import qualified97_records as records
from pathlib import Path
from source_boundary import load_fixed,read_root
from qualified_specs import exact_spec
import accepted98_dependency as dependency
import materialize97
READY=True
STAGE=Path('/opt/careertrellis/staging/preparation97-corrected-source-20261027/corrected97-fixture-g1')
DOMAIN=Path('/var/backups/careertrellis/preparation97-corrected-20261027/corrected97-fixture-g1')
RELEASE=Path('/opt/careertrellis/staging/releases/bc68ea0dc253425a44e268452130becdf35d764e')
def need(v):
 if not v:raise RuntimeError('QUALIFIED97_BOUNDARY_REFUSED')
class Admission97:
 def __init__(self,ops_commit):
  need(READY);self.spec,self.ns,self.contract_raw=load_fixed('corrected-fixture97-new/v1',ops_commit);self.ops_commit=ops_commit;self.fenced=False
  self.old=self.ns['original_helpers']()
 def digest(self,v):return self.ns['sha'](self.ns['canonical'](v))
 def boot(self):return self.ns['boot']()
 def birth(self,pid):return self.ns['birth'](pid)
 def now(self):return dt.datetime.now(dt.timezone.utc)
 def source_manifest_sha256(self,m):return self.digest(m)
 def load_closed_controls(self,kind):
  need(kind=='corrected-fixture97-new/v1');decode=self.ns['decode'];raw={n:read_root(STAGE/n) for n in ('plan.json','decision.json','source-manifest.json')}
  p=decode(raw['plan.json']);d=decode(raw['decision.json']);m=decode(raw['source-manifest.json'])
  need(all(raw[n]==self.ns['canonical'](v) for n,v in [('plan.json',p),('decision.json',d),('source-manifest.json',m)]))
  need(set(p)=={'schema','kind','revision','build','source_manifest_sha256','archive_sha256','provenance_sha256','accepted98_sha256','full_preflight_sha256','decision_at','expires_at'})
  need(p['schema']=='careertrellis.corrected97-materialization-plan/v1' and p['kind']==kind and p['revision']==self.spec['revision'] and p['build']==self.spec['build'])
  need(set(d)=={'schema','kind','plan_sha256','source_manifest_sha256','boot_id','decision_at','expires_at'}
   and d['schema']=='careertrellis.corrected97-decision/v1' and d['kind']==kind and d['plan_sha256']==self.ns['sha'](raw['plan.json'])
   and d['source_manifest_sha256']==p['source_manifest_sha256']==self.ns['sha'](raw['source-manifest.json']))
  need(d['boot_id']==self.boot() and d['decision_at']==p['decision_at'] and d['expires_at']==p['expires_at'])
  start=dt.datetime.fromisoformat(p['decision_at']);end=dt.datetime.fromisoformat(p['expires_at'])
  need(start<=self.now()<end<=start+dt.timedelta(minutes=55))
  need(set(m)=={'schema','kind','ops_commit','files'} and m['schema']=='careertrellis.corrected97-source-manifest/v1' and m['kind']==kind and m['ops_commit']==self.ops_commit)
  expected={'qualified_specs.py','materialize97.py','accepted98_dependency.py','source_boundary.py','qualified_route.py','admission97.py','native_assertions97.py','run_checks_qualified97.py','native97_unit.py','checks97.py','qualified97_records.py','qualified_entry.py','historical98_controls.py'}
  need(set(m['files'])==expected)
  for name,item in m['files'].items():
   need(set(item)=={'bytes','sha256'});b=read_root(STAGE/name,1048576)
   need(len(b)==item['bytes'] and self.ns['sha'](b)==item['sha256'] and b==self.ns['run'](['git','-C',self.ns['REPOSITORY'],'cat-file','blob',self.ops_commit+':operations/release-transition-20261027/corrected97/'+name]))
  pre_raw=read_root(STAGE/'preflight.json');pre=records.closed('preflight.json',decode(pre_raw))
  need(self.ns['sha'](pre_raw)==p['full_preflight_sha256'] and pre['kind']==kind and pre['result']=='pass'
   and pre['boot_id']==self.boot() and pre['source_manifest_sha256']==p['source_manifest_sha256']
   and pre['accepted98_receipt_sha256']==p['accepted98_sha256'] and pre['archive_sha256']==p['archive_sha256']
   and pre['provenance_sha256']==p['provenance_sha256'] and pre['baseline']==self.ns['BASE'] and pre['previous']==self.ns['PREVIOUS']
   and pre['live_build']=='20261017.96' and pre['release_absent'] is True and pre['namespace_absent'] is True and pre['unit_absent'] is True)
  observed=dt.datetime.fromisoformat(pre['observed_at']);need(observed<=start and 0<=(start-observed).total_seconds()<=300)
  self.plan,self.decision,self.manifest=p,d,m
  return p,d,m
 def authenticate_accepted98(self,plan):return dependency.authenticate(self.ns,plan['accepted98_sha256'],self.contract_raw)
 @contextlib.contextmanager
 def canonical_fences(self):
  need(not self.fenced)
  with self.old['locks']():
   self.fenced=True
   try:yield
   finally:self.fenced=False
 def full_preflight(self,spec,plan,dep,materialized=False,postterminal=False):
  need(spec is self.spec and dep is not None)
  # Reauthenticate the ACTUAL dependency/currentbaseline/fullsource+software on
  # every fresh check; a previously returned boolean/map is insufficient.
  current=self.authenticate_accepted98(plan);need(current['boot_id']==self.boot())
  release_absent=not os.path.lexists(RELEASE)
  if not materialized:need(release_absent)
  for path in (Path(spec['code']),Path(spec['work'])):
   if not materialized:need(not os.path.lexists(path))
  unit=spec.get('unit','careertrellis-preparation97-corrected-fixture-g1-20261027')
  native97_unit.require_absent()
  materialization_absent=not os.path.lexists(DOMAIN/'materialization-intent.json')
  if not materialized:need(materialization_absent)
  if os.path.lexists(DOMAIN):
   if postterminal:self.authenticate_historical_owner_phase(plan)
   else:self.authenticate_current_owner_phase(plan)
  archive=read_root(STAGE/'application-bc68ea0d-source.tar',128*1024**2)
  need(len(archive)==spec['archive_bytes'] and self.ns['sha'](archive)==spec['archive_sha256']==plan['archive_sha256'])
  expected=self.old['archive_files'](archive)
  need(expected==self.old['archive_files'](self.ns['run'](['git','-C',self.ns['REPOSITORY'],'archive',spec['revision']])))
  provenance_raw=read_root(STAGE/'baseline-provenance.json');need(self.ns['sha'](provenance_raw)==plan['provenance_sha256'])
  provenance=self.ns['decode'](provenance_raw);self.old['validate_provenance'](provenance)
  baseline=Path('/opt/careertrellis/staging/releases')/self.ns['BASE']
  packages=baseline/'backend/venv/lib/python3.12/site-packages'
  for name,wanted in provenance['requirements'].items():need(self.ns['sha'](read_root(baseline/name))==wanted==expected[name]['sha256'])
  interpreter=Path(provenance['interpreter']['path']);need(self.ns['sha'](read_root(interpreter))==provenance['interpreter']['sha256'])
  need(self.old['inventory'](packages)==provenance['site_packages'] and self.old['inventory'](self.old['BROWSER'])==provenance['browser'])
  peak=self.old['needed_bytes'](expected,provenance);fs=os.statvfs('/opt/careertrellis/staging')
  self.old['validate_capacity'](fs.f_bavail*fs.f_frsize,peak)
  need(fs.f_favail>=len(expected)+len(provenance['site_packages'])*3+4096)
  memory=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines());need(int(memory['MemAvailable'].split()[0])*1024>=768*1024**2)
  result={'release_absent':release_absent,'materialization_absent':materialization_absent,'baseline':self.ns['BASE'],
   'previous':self.ns['PREVIOUS'],'boot_id':current['boot_id'],'peak_bytes':peak,'observed_at':self.now().isoformat(),
   'browser':provenance['browser'],'software_inventory_sha256':self.digest({'packages':provenance['site_packages'],'browser':provenance['browser'],'interpreter':provenance['interpreter']}),
   'capacity':{'available_bytes':fs.f_bavail*fs.f_frsize,'available_inodes':fs.f_favail,'memory_available':int(memory['MemAvailable'].split()[0])*1024},
   'accepted98_sha256':plan['accepted98_sha256']}
  if materialized:
   self.old['verify_source'](RELEASE,expected);runtime=self.old['runtime_manifest'](RELEASE)
   need(runtime==self.old['runtime_manifest'](RELEASE)
    and self.old['inventory'](RELEASE/'backend/venv/lib/python3.12/site-packages')==provenance['site_packages']
    and self.old['inventory'](RELEASE/'backend/venv/lib64/python3.12/site-packages')==provenance['site_packages'])
   result['runtime']=runtime
  need(self.old['inventory'](packages)==provenance['site_packages'] and self.old['inventory'](self.old['BROWSER'])==provenance['browser'])
  native97_unit.require_absent() # Fresh unit/cgroup readback after full long inventory.
  if 'full_preflight_sha256' in plan:
   pre_raw=read_root(STAGE/'preflight.json');pre=records.closed('preflight.json',self.ns['decode'](pre_raw))
   need(self.ns['sha'](pre_raw)==plan['full_preflight_sha256'] and pre['software_inventory_sha256']==result['software_inventory_sha256']
    and pre['browser_manifest_sha256']==self.digest(result['browser']))
  if not materialized:
   need(not os.path.lexists(RELEASE) and not os.path.lexists(Path(spec['code'])) and not os.path.lexists(Path(spec['work'])) and not os.path.lexists(DOMAIN/'materialization-intent.json'))
  need(self.boot()==current['boot_id']);return result
 def authenticate_current_owner_phase(self,plan):
  st=DOMAIN.lstat();need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and stat.S_IMODE(st.st_mode)==0o700)
  owner=self.ns['decode'](read_root(DOMAIN/'owner.json'))
  need(set(owner)=={'schema','pid','birth','boot_id','plan_sha256','started_at'} and owner['schema']=='careertrellis.corrected97-owner/v1'
   and owner['boot_id']==self.boot() and owner['plan_sha256']==self.digest(plan) and self.birth(owner['pid'])==owner['birth'])
  if os.getpid()==owner['pid']:need(self.birth(os.getpid())==owner['birth']);return
  child=self.ns['decode'](read_root(DOMAIN/'coordinator.json'))
  need(set(child)=={'schema','pid','birth','boot_id','owner_pid','plan_sha256','started_at'} and child['schema']=='careertrellis.corrected97-coordinator/v1'
   and child['owner_pid']==owner['pid'] and child['plan_sha256']==self.digest(plan) and child['boot_id']==self.boot()
   and child['pid']==os.getpid() and child['birth']==self.birth(os.getpid()))
 def materialize_new97(self,plan,manifest,state,dep):
  need(self.fenced);raw=read_root(STAGE/'baseline-provenance.json');provenance=self.ns['decode'](raw)
  archive=read_root(STAGE/'application-bc68ea0d-source.tar',128*1024**2)
  primitives=dict(self.old);primitives['gid']=grp.getgrnam('careertrellis').gr_gid
  @contextlib.contextmanager
  def existing_fence():
   need(self.fenced);yield
  primitives['locks']=existing_fence
  return materialize97.materialize(plan,manifest,archive,provenance,primitives,
   lambda digest:self.authenticate_accepted98(plan),lambda *args:self.full_preflight(self.spec,plan,dep),
   self.create_record,self.birth,self.boot,self.now,provenance_raw=raw)
 def create_record(self,name,value):
  allowed={'materialization-intent.json','materialization-child.json','materialization-proof.json','runtime-before.json','unit-intent.json','unit-client.json','unit-terminal.json','runtime-manifest.json','candidate.json','owner-terminal.json','postterminal-proof.json','accepted.json'}
  need(name in allowed)
  if not self.fenced:
   need(name=='owner-terminal.json');self.authenticate_current_owner_phase(self.plan)
  if name in records.SCHEMAS:records.closed(name,value)
  return self.ns['create'](DOMAIN/name,value)

 def lifecycle(self):
  # Same authenticated cached source bytes, fresh isolated globals. The ONLY
  # literal policy substitution is the exact fixed97 UNIT; never caller input.
  ns={'__name__':'fixed97_lifecycle'};exec(compile(self.contract_raw,'pinned shared lifecycle','exec'),ns)
  ns['UNIT']='careertrellis-preparation97-corrected-fixture-g1-20261027'
  return ns
 def require_new_unit_absent(self,spec):
  need(spec is self.spec and self.boot()==self.decision['boot_id'] and self.now()<dt.datetime.fromisoformat(self.plan['expires_at']));return native97_unit.require_absent()
 def prepare_root_readonly_code_and_private_work(self,spec,manifest):
  need(self.fenced and spec is self.spec);gid=grp.getgrnam('careertrellis').gr_gid
  code=Path(spec['code']);work=Path(spec['work'])
  for parent in (code.parent,work.parent):
   if not os.path.lexists(parent):parent.mkdir(mode=0o750);os.chown(parent,0,gid);self.ns['sync_dir'](parent.parent)
   st=parent.lstat();need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022)
  need(not os.path.lexists(code) and not os.path.lexists(work));code.mkdir(mode=0o750);os.chown(code,0,gid);self.ns['sync_dir'](code.parent)
  for name in ('native_assertions97.py','run_checks_qualified97.py'):
   raw=read_root(STAGE/name);need(self.ns['sha'](raw)==manifest['files'][name]['sha256'])
   fd=os.open(code/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
   try:
    os.fchown(fd,0,gid);os.fchmod(fd,0o640)
    with os.fdopen(os.dup(fd),'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
   finally:os.close(fd)
   self.ns['sync_dir'](code);need(read_root(code/name)==raw)
  work.mkdir(mode=0o700);os.chown(work,65534,65534);self.ns['sync_dir'](work.parent)
  alias=work/'browsers';alias.mkdir(mode=0o700);os.chown(alias,65534,65534);self.ns['sync_dir'](work)
 def exact_sandbox_command(self,spec,plan,manifest):
  need(spec is self.spec);work=Path(spec['work']);code=Path(spec['code']);props=self.old['sandbox_properties'](RELEASE,work,self.old['BROWSER'])
  argv=['systemd-run','--unit=careertrellis-preparation97-corrected-fixture-g1-20261027','--wait','--pipe','--collect',*props,
   str(RELEASE/'backend/venv/bin/python'),'-I','-B',str(code/'run_checks_qualified97.py'),'--release',str(RELEASE),
   '--revision',spec['revision'],'--work',str(work),'--repair-ops-commit',self.ops_commit,
   '--approved-runner-sha256',manifest['files']['run_checks_qualified97.py']['sha256'],
   '--accepted98-receipt-sha256',plan['accepted98_sha256'],'--retained-runtime-manifest-sha256',self.materialized_runtime_sha]
  return argv,props
 def require_materialization_proof(self,proof,runtime,plan):
  keys={'schema','revision','plan_sha256','accepted98_sha256','source_manifest_sha256','runtime_manifest_sha256','boot_id','completed_at'}
  need(type(proof) is dict and set(proof)==keys and proof['schema']=='careertrellis.corrected97-materialization-proof/v1' and proof['revision']==self.spec['revision']
   and proof['plan_sha256']==self.digest(plan) and proof['accepted98_sha256']==plan['accepted98_sha256']
   and proof['source_manifest_sha256']==plan['source_manifest_sha256']==self.digest(self.manifest)
   and proof['boot_id']==self.boot() and proof['runtime_manifest_sha256']==self.digest(runtime))
  completed=dt.datetime.fromisoformat(proof['completed_at']);need(completed.tzinfo is not None)
  need(dt.datetime.fromisoformat(plan['decision_at'])<=completed<=self.now()<dt.datetime.fromisoformat(plan['expires_at']))
  self.materialized_runtime_sha=self.digest(runtime)
 def spawn_exact_unit(self,argv,intent_sha):
  import subprocess
  need(self.fenced);self.require_new_unit_absent(self.spec)
  self.unit_output=os.open(DOMAIN/'synthetic-tests.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  self.ns['sync_dir'](DOMAIN)
  self.require_new_unit_absent(self.spec)
  return subprocess.Popen(argv[:2]+['--description=careertrellis-qualified97:'+intent_sha]+argv[2:],stdin=subprocess.DEVNULL,
   stdout=self.unit_output,stderr=subprocess.STDOUT,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
 def wait_and_collect_exact_owned_unit(self,child,spec,intent_sha):
  import time
  need(self.fenced and spec is self.spec);life=self.lifecycle();unit=life['UNIT'];deadline=time.monotonic()+1230
  while True:
   state=life['unit_state'](unit);need(child.poll() in (None,0) and state.get('ActiveState')!='failed')
   if state.get('LoadState')=='loaded':
    need(state.get('Description')=='careertrellis-qualified97:'+intent_sha)
    if state.get('ActiveState')=='active' and state.get('SubState')=='exited':
     group=life['cgroup_state'](unit,state)
     if group['processes']==0 and group['populated']==0:break
   need(time.monotonic()<deadline);time.sleep(1)
  execution=life['execution_terminal'](unit,state,group)
  terminal=life['collect_empty_unit'](unit,execution,child)
  os.fsync(self.unit_output);os.close(self.unit_output);self.unit_output=None
  clientraw=read_root(DOMAIN/'unit-client.json');clientrec=records.closed('unit-client.json',self.ns['decode'](clientraw))
  value={'schema':'careertrellis.corrected97-unit-terminal/v1','kind':'corrected-fixture97-new/v1','unit':unit,
   'intent_sha256':intent_sha,'client_sha256':self.ns['sha'](clientraw),'boot_id':self.boot(),'observed_at':self.now().isoformat(),
   **terminal,'client_birth':clientrec['birth']}
  return records.closed('unit-terminal.json',value)
 def retain_owned_unit_until_terminal(self,child,spec):
  import time
  if child is None:return
  while True:
   try:
    if child.poll() is not None:
     native97_unit.require_absent();return
   except BaseException:pass
   try:time.sleep(1)
   except BaseException:pass
 def read_and_authenticate_checks(self,spec,plan,manifest,terminal,protected=False):
  import ast,checks97
  need(spec is self.spec);raw=read_root(DOMAIN/'checks.json') if protected else checks97.read_checks();value=self.ns['decode'](raw)
  source=read_root(STAGE/'run_checks_qualified97.py');need(self.ns['sha'](source)==manifest['files']['run_checks_qualified97.py']['sha256'])
  fn=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='modules_for')
  scope={'need':need,'TARGET':self.spec['revision']};exec(compile(ast.Module(body=[fn],type_ignores=[]),'pinned97 modulecatalog','exec'),scope)
  modules={'backend':scope['modules_for'](RELEASE,self.spec['revision']),
   'summary':['test_ai_usage_summary','test_claude_cache_summary','test_study_readiness'],
   'evaluation':['test_budget_guard','test_ct_budget_transport','test_campaign_state','test_study_upstream','test_study_native_corrections',
    'test_campaign_v2','test_native_cost_envelope','test_campaign_transport','test_ct_campaign_opener','test_blind_packets','test_study_ct']}
  checks97.validate(value,spec,plan,manifest,self.materialized_runtime_sha,modules)
  startup=value['details']['startup'];need(set(startup)=={'api_build','dashboard_match','owned_process_stopped','exit_signal','log_sha256'}
   and startup['api_build']==spec['build'] and startup['dashboard_match'] is True and startup['owned_process_stopped'] is True
   and startup['exit_signal']=='SIGTERM' and type(startup['log_sha256']) is str and len(startup['log_sha256'])==64)
  need(dt.datetime.fromisoformat(plan['decision_at'])<=dt.datetime.fromisoformat(value['started_utc'])
   <=dt.datetime.fromisoformat(value['ended_utc'])<=self.now()<dt.datetime.fromisoformat(plan['expires_at']))
  if not protected:self.ns['create'](DOMAIN/'checks.json',raw)
  self.checks_sha=self.ns['sha'](raw);return value
 def authenticate_actual_catalogs(self,spec,checks):
  import checks97
  for name in ('backend','summary','evaluation'):checks97.catalog(checks['details'][name],name=='backend')
 def candidate(self,spec,plan,decision,manifest,checks,terminal,after):
  material=self.ns['decode'](read_root(DOMAIN/'materialization-proof.json'))
  need(material['runtime_manifest_sha256']==self.materialized_runtime_sha==self.digest(after['runtime']))
  unitraw=read_root(DOMAIN/'unit-terminal.json')
  pre=records.closed('preflight.json',self.ns['decode'](read_root(STAGE/'preflight.json')))
  need(self.digest(after['browser'])==pre['browser_manifest_sha256'])
  value={'schema':'careertrellis.corrected97-candidate/v1','kind':'corrected-fixture97-new/v1','revision':spec['revision'],'build':spec['build'],
   'ops_commit':self.ops_commit,'source_manifest_sha256':self.digest(manifest),'plan_sha256':self.digest(plan),'decision_sha256':self.digest(decision),
   'accepted98_receipt_sha256':plan['accepted98_sha256'],'materialization_proof_sha256':self.ns['sha'](read_root(DOMAIN/'materialization-proof.json')),
   'runtime_before_sha256':self.materialized_runtime_sha,'runtime_after_sha256':self.digest(after['runtime']),
   'browser_before_sha256':pre['browser_manifest_sha256'],'browser_after_sha256':self.digest(after['browser']),
   'checks_sha256':self.checks_sha,'unit_terminal_sha256':self.ns['sha'](unitraw),'completed_at':self.now().isoformat(),'result':'candidate_only_not_accepted'}
  return records.closed('candidate.json',value)
 def build_readonly_preflight(self,accepted98_sha256):
  manifest_raw=read_root(STAGE/'source-manifest.json');manifest=self.ns['decode'](manifest_raw)
  need(set(manifest)=={'schema','kind','ops_commit','files'} and manifest['schema']=='careertrellis.corrected97-source-manifest/v1'
   and manifest['kind']=='corrected-fixture97-new/v1' and manifest['ops_commit']==self.ops_commit and manifest_raw==self.ns['canonical'](manifest))
  # The immutable source file registry is validated before any native operation.
  names={'qualified_specs.py','materialize97.py','accepted98_dependency.py','source_boundary.py','qualified_route.py','admission97.py',
   'native_assertions97.py','run_checks_qualified97.py','native97_unit.py','checks97.py','qualified97_records.py','qualified_entry.py','historical98_controls.py'}
  need(set(manifest['files'])==names)
  for name,item in manifest['files'].items():
   raw=read_root(STAGE/name,1048576);need(set(item)=={'bytes','sha256'} and len(raw)==item['bytes'] and self.ns['sha'](raw)==item['sha256']
    and raw==self.ns['run'](['git','-C',self.ns['REPOSITORY'],'cat-file','blob',self.ops_commit+':operations/release-transition-20261027/corrected97/'+name]))
  need(not os.path.lexists(DOMAIN))
  provenance_raw=read_root(STAGE/'baseline-provenance.json')
  request={'accepted98_sha256':accepted98_sha256,'archive_sha256':self.spec['archive_sha256'],'provenance_sha256':self.ns['sha'](provenance_raw)}
  live=self.full_preflight(self.spec,request,self.authenticate_accepted98(request));need(not os.path.lexists(DOMAIN))
  value={'schema':'careertrellis.corrected97-preflight/v1','kind':'corrected-fixture97-new/v1','observed_at':live['observed_at'],'boot_id':live['boot_id'],
   'source_manifest_sha256':self.ns['sha'](manifest_raw),'accepted98_receipt_sha256':accepted98_sha256,'archive_sha256':request['archive_sha256'],
   'provenance_sha256':request['provenance_sha256'],'baseline':self.ns['BASE'],'previous':self.ns['PREVIOUS'],'live_build':'20261017.96',
   'release_absent':True,'namespace_absent':True,'unit_absent':True,'software_inventory_sha256':live['software_inventory_sha256'],
   'browser_manifest_sha256':self.digest(live['browser']),'capacity':live['capacity'],'result':'pass'}
  return records.closed('preflight.json',value)
 def exclusive_owner_intent(self,spec,plan,decision):
  need(spec is self.spec and not os.path.lexists(DOMAIN));parent=DOMAIN.parent
  if not os.path.lexists(parent):parent.mkdir(mode=0o700);self.ns['sync_dir'](parent.parent)
  st=parent.lstat();need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and stat.S_IMODE(st.st_mode)==0o700)
  DOMAIN.mkdir(mode=0o700);self.ns['sync_dir'](parent)
  self.ns['create'](DOMAIN/'intent.json',{'kind':'corrected-fixture97-new/v1','plan_sha256':self.digest(plan),'decision_sha256':self.digest(decision),
   'source_manifest_sha256':plan['source_manifest_sha256'],'owner_pid':os.getpid(),'owner_birth':self.birth(os.getpid()),'boot_id':self.boot(),'no_replay':True})
  self.ns['create'](DOMAIN/'owner.json',{'schema':'careertrellis.corrected97-owner/v1','pid':os.getpid(),'birth':self.birth(os.getpid()),'boot_id':self.boot(),'plan_sha256':self.digest(plan),'started_at':self.now().isoformat()})
  for name in ('plan.json','decision.json','source-manifest.json','preflight.json'):
   self.ns['create'](DOMAIN/name,read_root(STAGE/name))
 def spawn_fixed_coordinator(self,kind):
  import subprocess
  need(kind=='corrected-fixture97-new/v1')
  command=['/usr/bin/python3','-I','-S','-B',str(STAGE/'qualified_entry.py'),'--role','coordinator',
   '--ops-commit',self.ops_commit,'--approved-source-manifest-sha256',self.digest(self.manifest),'--approved-plan-sha256',self.digest(self.plan),'--approved-decision-sha256',self.digest(self.decision)]
  self.ns['create'](DOMAIN/'coordinator-spawn-intent.json',{'argv':command,'plan_sha256':self.digest(self.plan),'decision_sha256':self.digest(self.decision),'boot_id':self.boot(),'owner_pid':os.getpid(),'owner_birth':self.birth(os.getpid())})
  self.require_new_unit_absent(self.spec)
  child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
   env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
  self.coordinator_child=child;return child
 def record_coordinator_birth(self,pid,birth,plan,decision):
  self.ns['create'](DOMAIN/'child-spawn.json',{'schema':'careertrellis.corrected97-child-spawn/v1','pid':pid,'birth':birth,'boot_id':self.boot(),
   'owner_pid':os.getpid(),'plan_sha256':self.digest(plan),'decision_sha256':self.digest(decision),'source_manifest_sha256':plan['source_manifest_sha256']})
 def record_current_coordinator(self):
  owner=records.closed('owner.json',self.ns['decode'](read_root(DOMAIN/'owner.json')))
  need(owner['boot_id']==self.boot() and self.birth(owner['pid'])==owner['birth'])
  self.ns['create'](DOMAIN/'coordinator.json',{'schema':'careertrellis.corrected97-coordinator/v1','pid':os.getpid(),'birth':self.birth(os.getpid()),
   'boot_id':self.boot(),'owner_pid':owner['pid'],'plan_sha256':self.digest(self.plan),'started_at':self.now().isoformat()})
 def drain_and_retain_coordinator(self,child):
  import threading
  states={};threads=[]
  def drain(pipe,name):
   state=states[name]
   try:
    fd=os.open(DOMAIN/('coordinator-'+name+'.bin'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as out:
     self.ns['sync_dir'](DOMAIN)
     while True:
      block=pipe.read(65536)
      if not block:break
      room=max(0,1048576-state['retained_bytes']);out.write(block[:room]);out.flush()
      state['retained_bytes']+=min(room,len(block));state['discarded_bytes']+=max(0,len(block)-room)
     os.fsync(out.fileno())
    state['eof']=True
   except BaseException:state['error']=True
  self.captures=states
  for name,pipe in [('stdout',child.stdout),('stderr',child.stderr)]:
   states[name]={'retained_bytes':0,'discarded_bytes':0,'eof':False,'error':False}
   thread=threading.Thread(target=drain,args=(pipe,name),daemon=False);thread.start();threads.append(thread)
  code=child.wait()
  for thread in threads:thread.join()
  return {'exit_code':code,'stdout_eof':states['stdout']['eof'],'stderr_eof':states['stderr']['eof'],
   'capture_errors':sum(int(x['error']) for x in states.values()),'captures':states}
 def retain_coordinator_and_unit_until_terminal(self,child,spec):
  import time
  if child is None:
   # An exclusive partialclaim without child still needs independent inspection.
   while True:
    try:time.sleep(60)
    except BaseException:pass
  while True:
   try:
    if child.poll() is not None and all(x['eof'] and not x['error'] for x in self.captures.values()):
     native97_unit.require_absent();return
   except BaseException:pass
   try:time.sleep(1)
   except BaseException:pass
 def validate_unit_chain(self,plan,decision,manifest):
  raw=read_root(DOMAIN/'unit-terminal.json');u=records.closed('unit-terminal.json',self.ns['decode'](raw))
  iraw=read_root(DOMAIN/'unit-intent.json');intent=records.closed('unit-intent.json',self.ns['decode'](iraw))
  craw=read_root(DOMAIN/'unit-client.json');client=records.closed('unit-client.json',self.ns['decode'](craw))
  coord=records.closed('coordinator.json',self.ns['decode'](read_root(DOMAIN/'coordinator.json')))
  argv,props=self.exact_sandbox_command(self.spec,plan,manifest)
  need(intent['kind']==u['kind']=='corrected-fixture97-new/v1' and intent['plan_sha256']==self.digest(plan) and intent['decision_sha256']==self.digest(decision)
   and intent['source_manifest_sha256']==self.digest(manifest) and intent['argv']==argv and intent['properties']==props
   and intent['coordinator_pid']==coord['pid'] and intent['coordinator_birth']==coord['birth']
   and intent['boot_id']==client['boot_id']==u['boot_id']==coord['boot_id']==self.boot()
   and u['intent_sha256']==client['intent_sha256']==self.ns['sha'](iraw) and u['client_sha256']==self.ns['sha'](craw)
   and u['client_pid']==client['pid'] and u['client_birth']==client['birth'] and type(u['client_exit']) is int and u['client_exit']==0)
  t0=dt.datetime.fromisoformat(plan['decision_at']);t1=dt.datetime.fromisoformat(intent['created_at']);t2=dt.datetime.fromisoformat(u['observed_at'])
  need(t0<=t1<=t2<=self.now()<dt.datetime.fromisoformat(plan['expires_at']))
  exe=u['execution'];state=exe['state'];after=u['after']
  need(u['unit']==exe['unit']=='careertrellis-preparation97-corrected-fixture-g1-20261027'
   and state['LoadState']=='loaded' and state['ActiveState']=='active' and state['SubState']=='exited'
   and state['Result']=='success' and state['ExecMainStatus']=='0' and state['MainPID']=='0' and state['ControlPID']=='0' and not state['Job']
   and state['Description']=='careertrellis-qualified97:'+u['intent_sha256'] and state['InvocationID']==exe['invocation_id']
   and exe['cgroup']['processes']==0 and exe['cgroup']['populated']==0 and u['cgroup_after']['processes']==0 and u['cgroup_after']['populated']==0
   and after.get('LoadState') in ('not-found','loaded') and after.get('MainPID','0') in ('','0') and after.get('ControlPID','0') in ('','0') and not after.get('Job'))
  if after['LoadState']=='loaded':need(after['ActiveState']=='inactive' and after['SubState']=='dead' and after['Result']=='success' and after['ExecMainStatus']=='0' and after['InvocationID']==exe['invocation_id'])
  self.ns['absent_original']({'pid':client['pid'],'start_ticks':client['birth'],'boot_id':client['boot_id']},self.boot())
  return u
 def validate_candidate_and_unit_chain(self,spec,plan,decision):
  need(spec is self.spec);material=self.ns['decode'](read_root(DOMAIN/'materialization-proof.json'))
  runtime=self.ns['decode'](read_root(DOMAIN/'runtime-before.json'));self.require_materialization_proof(material,runtime,plan)
  u=self.validate_unit_chain(plan,decision,self.manifest)
  c=records.closed('candidate.json',self.ns['decode'](read_root(DOMAIN/'candidate.json')))
  need(c['kind']=='corrected-fixture97-new/v1' and c['revision']==spec['revision'] and c['build']==spec['build'] and c['ops_commit']==self.ops_commit
   and c['source_manifest_sha256']==self.digest(self.manifest) and c['plan_sha256']==self.digest(plan) and c['decision_sha256']==self.digest(decision)
   and c['accepted98_receipt_sha256']==plan['accepted98_sha256'] and c['materialization_proof_sha256']==self.ns['sha'](read_root(DOMAIN/'materialization-proof.json'))
   and c['runtime_before_sha256']==c['runtime_after_sha256']==self.materialized_runtime_sha
   and c['checks_sha256']==self.ns['sha'](read_root(DOMAIN/'checks.json')) and c['unit_terminal_sha256']==self.ns['sha'](read_root(DOMAIN/'unit-terminal.json'))
   and c['result']=='candidate_only_not_accepted')
  pre=records.closed('preflight.json',self.ns['decode'](read_root(STAGE/'preflight.json')))
  need(c['browser_before_sha256']==c['browser_after_sha256']==pre['browser_manifest_sha256'])
  self.check_chronology(plan,c,u)
  return c,u
 def owner_terminal(self,spec,plan,decision,terminal,owner_self_exit_claim):
  need(owner_self_exit_claim is False and terminal['exit_code']==0)
  owner=records.closed('owner.json',self.ns['decode'](read_root(DOMAIN/'owner.json')))
  coord=records.closed('coordinator.json',self.ns['decode'](read_root(DOMAIN/'coordinator.json')))
  spawnraw=read_root(DOMAIN/'child-spawn.json');spawn=records.closed('child-spawn.json',self.ns['decode'](spawnraw))
  need(spawn['pid']==coord['pid'] and spawn['birth']==coord['birth'] and spawn['owner_pid']==owner['pid']
   and spawn['plan_sha256']==self.digest(plan) and spawn['decision_sha256']==self.digest(decision) and spawn['source_manifest_sha256']==self.digest(self.manifest))
  c,u=self.validate_candidate_and_unit_chain(spec,plan,decision)
  value={'schema':'careertrellis.corrected97-owner-terminal/v1','kind':'corrected-fixture97-new/v1','boot_id':self.boot(),'owner':owner,'coordinator':coord,
   'child_spawn_sha256':self.ns['sha'](spawnraw),'coordinator_exit':terminal['exit_code'],'plan_sha256':self.digest(plan),'decision_sha256':self.digest(decision),
   'source_manifest_sha256':self.digest(self.manifest),'candidate_sha256':self.ns['sha'](read_root(DOMAIN/'candidate.json')),
   'checks_sha256':c['checks_sha256'],'unit_terminal_sha256':c['unit_terminal_sha256'],'runtime_after_sha256':c['runtime_after_sha256'],
   'captures':terminal['captures'],'owner_self_exit_claim':False,'ended_at':self.now().isoformat()}
  records.closed('owner-terminal.json',value);self.check_chronology(plan,c,u,owner_terminal=value)
  return value
 def authenticate_historical_owner_phase(self,plan):
  st=DOMAIN.lstat();need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and stat.S_IMODE(st.st_mode)==0o700)
  owner=records.closed('owner.json',self.ns['decode'](read_root(DOMAIN/'owner.json')))
  coord=records.closed('coordinator.json',self.ns['decode'](read_root(DOMAIN/'coordinator.json')))
  need(owner['boot_id']==coord['boot_id']==self.boot() and owner['plan_sha256']==coord['plan_sha256']==self.digest(plan) and coord['owner_pid']==owner['pid'])
  states=[]
  for value in (owner,coord):states.append(self.ns['absent_original']({'pid':value['pid'],'start_ticks':value['birth'],'boot_id':value['boot_id']},self.boot()))
  return tuple(states)
 def full_runtime_before(self):return self.ns['decode'](read_root(DOMAIN/'runtime-before.json'))
 def full_runtime_after(self):return self.ns['decode'](read_root(DOMAIN/'runtime-manifest.json'))
 def authenticate_browser_before_after(self,candidate,live):
  pre=records.closed('preflight.json',self.ns['decode'](read_root(STAGE/'preflight.json')))
  need(candidate['browser_before_sha256']==candidate['browser_after_sha256']==pre['browser_manifest_sha256']==self.digest(live['browser']))
 def authenticate_complete_nested_chain(self,spec,plan,decision,manifest):
  c,u=self.validate_candidate_and_unit_chain(spec,plan,decision)
  checks=self.read_and_authenticate_checks(spec,plan,manifest,u,protected=True)
  owner=records.closed('owner-terminal.json',self.ns['decode'](read_root(DOMAIN/'owner-terminal.json')))
  ownerrec=records.closed('owner.json',self.ns['decode'](read_root(DOMAIN/'owner.json')))
  coord=records.closed('coordinator.json',self.ns['decode'](read_root(DOMAIN/'coordinator.json')))
  spawnraw=read_root(DOMAIN/'child-spawn.json');spawn=records.closed('child-spawn.json',self.ns['decode'](spawnraw))
  need(owner['kind']=='corrected-fixture97-new/v1' and owner['boot_id']==self.boot() and owner['owner']==ownerrec and owner['coordinator']==coord
   and type(owner['coordinator_exit']) is int and owner['coordinator_exit']==0 and owner['owner_self_exit_claim'] is False
   and owner['plan_sha256']==self.digest(plan) and owner['decision_sha256']==self.digest(decision) and owner['source_manifest_sha256']==self.digest(manifest)
   and owner['candidate_sha256']==self.ns['sha'](read_root(DOMAIN/'candidate.json')) and owner['checks_sha256']==c['checks_sha256']
   and owner['unit_terminal_sha256']==c['unit_terminal_sha256'] and owner['runtime_after_sha256']==c['runtime_after_sha256']
   and owner['child_spawn_sha256']==self.ns['sha'](spawnraw) and spawn['pid']==coord['pid'] and spawn['birth']==coord['birth']
   and spawn['owner_pid']==ownerrec['pid'] and spawn['boot_id']==coord['boot_id']==ownerrec['boot_id']==self.boot()
   and spawn['plan_sha256']==self.digest(plan) and spawn['decision_sha256']==self.digest(decision) and spawn['source_manifest_sha256']==self.digest(manifest))
  need(set(owner['captures'])=={'stdout','stderr'})
  for cap in owner['captures'].values():
   need(set(cap)=={'retained_bytes','discarded_bytes','eof','error'} and cap['eof'] is True and cap['error'] is False
    and type(cap['retained_bytes']) is int and 0<=cap['retained_bytes']<=1048576 and type(cap['discarded_bytes']) is int and cap['discarded_bytes']>=0)
  self.authenticate_historical_owner_phase(plan);self.check_chronology(plan,c,u,owner_terminal=owner)
  return owner,c,checks,u
 def final_original_birth_unit_cgroup_deadline_guard(self,spec,plan,decision,owner):
  need(self.fenced and self.boot()==decision['boot_id'] and self.now()<dt.datetime.fromisoformat(plan['expires_at']))
  actual=records.closed('owner-terminal.json',self.ns['decode'](read_root(DOMAIN/'owner-terminal.json')));need(actual==owner)
  states=self.authenticate_historical_owner_phase(plan);state,cg=native97_unit.require_absent()
  need(self.boot()==decision['boot_id'] and self.now()<dt.datetime.fromisoformat(plan['expires_at']))
  return states,state,cg
 def postterminal_proof(self,spec,plan,decision,owner,candidate,unit,live):
  states,state,cg=self.final_original_birth_unit_cgroup_deadline_guard(spec,plan,decision,owner)
  value={'schema':'careertrellis.corrected97-postterminal-proof/v1','kind':'corrected-fixture97-new/v1','boot_id':self.boot(),'observed_at':self.now().isoformat(),
   'plan_sha256':self.digest(plan),'decision_sha256':self.digest(decision),'source_manifest_sha256':self.digest(self.manifest),
   'accepted98_receipt_sha256':plan['accepted98_sha256'],'candidate_sha256':self.ns['sha'](read_root(DOMAIN/'candidate.json')),
   'owner_terminal_sha256':self.ns['sha'](read_root(DOMAIN/'owner-terminal.json')),'owner_state':states[0],'coordinator_state':states[1],
   'unit':state,'cgroup_after':cg,'runtime_manifest_sha256':self.digest(live['runtime']),'browser_manifest_sha256':self.digest(live['browser']),
   'original_deadline':plan['expires_at']}
  records.closed('postterminal-proof.json',value);self.check_chronology(plan,candidate,unit,owner_terminal=owner,postterminal=value)
  return value
 def acceptance(self,spec,plan,decision,manifest,candidate,checks,unit,proof_sha,dep):
  need(dep['accepted_sha256']==plan['accepted98_sha256'])
  value={'schema':'careertrellis.preparation97-corrected-acceptance/v1','result':'corrected_fixtures_prepared_inactive','source_kind':'corrected-fixture97-new/v1',
   'revision':spec['revision'],'build':spec['build'],'baseline':self.ns['BASE'],'previous':self.ns['PREVIOUS'],'attempt_id':records.ATTEMPT,
   'ops_commit':self.ops_commit,'source_manifest_sha256':self.digest(manifest),'plan_sha256':self.digest(plan),'decision_sha256':self.digest(decision),
   'candidate_sha256':self.ns['sha'](read_root(DOMAIN/'candidate.json')),'archive_sha256':plan['archive_sha256'],
   'materialization_proof_sha256':candidate['materialization_proof_sha256'],'accepted98_receipt_sha256':plan['accepted98_sha256'],
   'runtime_manifest':str(DOMAIN/'runtime-manifest.json'),'runtime_manifest_sha256':candidate['runtime_after_sha256'],
   'runtime_before_sha256':candidate['runtime_before_sha256'],'native_source_proof':checks['details']['backend']['native_source_proof'],
   'checks':checks['checks'],'checks_sha256':candidate['checks_sha256'],'unit_intent_sha256':unit['intent_sha256'],
   'unit_terminal_sha256':candidate['unit_terminal_sha256'],'child_spawn_sha256':self.ns['sha'](read_root(DOMAIN/'child-spawn.json')),
   'owner_terminal_sha256':self.ns['sha'](read_root(DOMAIN/'owner-terminal.json')),'postterminal_proof_sha256':proof_sha,
   'completed_utc':self.now().isoformat(),'activated':False}
  records.closed('accepted.json',value)
  owner=records.closed('owner-terminal.json',self.ns['decode'](read_root(DOMAIN/'owner-terminal.json')))
  post=records.closed('postterminal-proof.json',self.ns['decode'](read_root(DOMAIN/'postterminal-proof.json')))
  self.check_chronology(plan,candidate,unit,owner_terminal=owner,postterminal=post,acceptance=value)
  return value
 def authenticate_retained98(self,*args):
  #97adapter cannot silently dispatch/validate98 through a renamed schema.
  raise RuntimeError('USE_EXACT_REVIEWED98_ROUTE')
 def build_decision_from_actual_preflight(self):
  # Returns canonicalizable controls only; does NOT write/allocate a plan.
  # Actual allocation/transport remains a separately reviewed exclusive step.
  manifest_raw=read_root(STAGE/'source-manifest.json');manifest=self.ns['decode'](manifest_raw)
  pre_raw=read_root(STAGE/'preflight.json');pre=records.closed('preflight.json',self.ns['decode'](pre_raw))
  need(pre['kind']=='corrected-fixture97-new/v1' and pre['result']=='pass' and pre['source_manifest_sha256']==self.ns['sha'](manifest_raw)
   and pre['boot_id']==self.boot() and pre['release_absent'] is True and pre['namespace_absent'] is True and pre['unit_absent'] is True)
  request={'accepted98_sha256':pre['accepted98_receipt_sha256'],'archive_sha256':pre['archive_sha256'],'provenance_sha256':pre['provenance_sha256']}
  current=self.full_preflight(self.spec,request,self.authenticate_accepted98(request))
  need(current['software_inventory_sha256']==pre['software_inventory_sha256'] and self.digest(current['browser'])==pre['browser_manifest_sha256'])
  start=self.now();observed=dt.datetime.fromisoformat(pre['observed_at']);need(observed<=start and 0<=(start-observed).total_seconds()<=300)
  expiry=start+dt.timedelta(minutes=55)
  plan={'schema':'careertrellis.corrected97-materialization-plan/v1','kind':'corrected-fixture97-new/v1','revision':self.spec['revision'],'build':self.spec['build'],
   'source_manifest_sha256':self.ns['sha'](manifest_raw),'archive_sha256':pre['archive_sha256'],'provenance_sha256':pre['provenance_sha256'],
   'accepted98_sha256':pre['accepted98_receipt_sha256'],'full_preflight_sha256':self.ns['sha'](pre_raw),'decision_at':start.isoformat(),'expires_at':expiry.isoformat()}
  decision={'schema':'careertrellis.corrected97-decision/v1','kind':'corrected-fixture97-new/v1','plan_sha256':self.digest(plan),
   'source_manifest_sha256':plan['source_manifest_sha256'],'boot_id':self.boot(),'decision_at':plan['decision_at'],'expires_at':plan['expires_at']}
  return plan,decision

 def check_chronology(self,plan,candidate,unit,owner_terminal=None,postterminal=None,acceptance=None):
  decode=self.ns['decode'];owner=records.closed('owner.json',decode(read_root(DOMAIN/'owner.json')))
  coord=records.closed('coordinator.json',decode(read_root(DOMAIN/'coordinator.json')))
  material=records.closed('materialization-proof.json',decode(read_root(DOMAIN/'materialization-proof.json')))
  intent=records.closed('materialization-intent.json',decode(read_root(DOMAIN/'materialization-intent.json')))
  unit_intent=records.closed('unit-intent.json',decode(read_root(DOMAIN/'unit-intent.json')))
  checks=decode(read_root(DOMAIN/'checks.json'))
  need(intent['revision']==material['revision']==self.spec['revision'] and intent['plan_sha256']==material['plan_sha256']==self.digest(plan)
   and intent['source_manifest_sha256']==material['source_manifest_sha256']==plan['source_manifest_sha256']==self.digest(self.manifest)
   and intent['accepted98_sha256']==material['accepted98_sha256']==plan['accepted98_sha256'] and intent['boot_id']==material['boot_id']==self.boot()
   and type(intent['peak_bytes']) is int and intent['peak_bytes']>0)
  records.validate_chronology(plan,owner,coord,intent,material,unit_intent,checks,unit,candidate,self.now(),owner_terminal,postterminal,acceptance)
