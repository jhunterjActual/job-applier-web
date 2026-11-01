"""97-only NEW materialization proposal. Native admission intentionally unavailable.
Authenticated291d extraction/package primitives are reused; its timeout-killing run
and its execute/dispatch entrypoints are never called by this module.
"""
import datetime as dt,hashlib,json,os,signal,subprocess,time
from pathlib import Path
from qualified_specs import exact_spec
READY=True
ROOT=Path('/opt/careertrellis/staging');BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS='4095417909a8b13ad252be9420e422af3662bef5'

def need(v):
 if not v:raise RuntimeError('QUALIFIED97_MATERIALIZATION_REFUSED')
def sha(b):return hashlib.sha256(b).hexdigest()
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def guard_deadline(plan,boot,boot_reader,now):
 need(boot_reader()==boot and now()<dt.datetime.fromisoformat(plan['expires_at']))
def own_child(argv,record,birth,client_factory=subprocess.Popen,sleep=time.sleep):
 """No timeout/signal; every postspawn failure retains exact child to terminal."""
 child=None
 try:
  child=client_factory(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
    env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','HOME':'/root'})
  record('materialization-child.json',{'pid':child.pid,'start_ticks':birth(child.pid),'argv':argv})
  while True:
   try:
    code=child.poll()
    if code is not None:break
   except BaseException:pass
   sleep(.2)
  need(type(code) is int and code==0)
 finally:
  if child is not None:
   while True:
    try:
     if child.poll() is not None:break
    except BaseException:pass
    try:sleep(.2)
    except BaseException:pass

def materialize(plan,manifest,archive,provenance,ns,auth98,fresh,record,birth,boot_reader,now,provenance_raw=None):
 """Trusted wrappers must bind ns functions to exact reviewed291d Gitblob.
 Source-only proposal: READYFalse prevents invocation even with callbacks.
 """
 need(READY);spec=exact_spec('corrected-fixture97-new/v1');rev=spec['revision'];release=ROOT/'releases'/rev
 need(set(plan)=={'schema','kind','revision','build','source_manifest_sha256','archive_sha256','provenance_sha256','accepted98_sha256','full_preflight_sha256','decision_at','expires_at'})
 need(plan['schema']=='careertrellis.corrected97-materialization-plan/v1' and plan['kind']=='corrected-fixture97-new/v1'
  and plan['revision']==rev and plan['build']==spec['build'] and plan['archive_sha256']==spec['archive_sha256'])
 need('runtime_manifest_sha256' not in plan) # Not knowable before materialization.
 need(len(archive)==spec['archive_bytes'] and sha(archive)==spec['archive_sha256'])
 need(type(provenance_raw) is bytes and sha(provenance_raw)==plan['provenance_sha256'] and json.loads(provenance_raw)==provenance);ns['validate_provenance'](provenance)
 expected=ns['archive_files'](archive)
 need(expected==ns['archive_files'](ns['run'](['git','-C',ns['REPOSITORY'],'archive',rev]))) # Read-only bounded Git only.
 base=ROOT/'releases'/BASE;packages=base/'backend/venv/lib/python3.12/site-packages'
 for name,wanted in provenance['requirements'].items():need(sha(ns['protected'](base/name))==wanted==expected[name]['sha256'])
 interpreter=Path(provenance['interpreter']['path']);need(sha(ns['protected'](interpreter))==provenance['interpreter']['sha256'])
 need(ns['inventory'](packages)==provenance['site_packages'] and ns['inventory'](ns['BROWSER'])==provenance['browser'])
 ns['verify_reused_packages'](provenance['site_packages'],ns['accepted_provenance']())
 peak=ns['needed_bytes'](expected,provenance);state=fresh(plan,spec,peak)
 need(state['release_absent'] is True and state['materialization_absent'] is True and state['baseline']==BASE and state['previous']==PREVIOUS)
 auth98(plan['accepted98_sha256']) # Full actual new98 sealed chain, not old291d assertion.
 boot=state['boot_id'];guard_deadline(plan,boot,boot_reader,now)
 with ns['locks']():
  state=fresh(plan,spec,peak);need(state['release_absent'] is True and state['materialization_absent'] is True and state['boot_id']==boot)
  guard_deadline(plan,boot,boot_reader,now);auth98(plan['accepted98_sha256'])
  record('materialization-intent.json',{'schema':'careertrellis.corrected97-materialization-intent/v1','revision':rev,
   'plan_sha256':sha(canonical(plan)),'source_manifest_sha256':plan['source_manifest_sha256'],'accepted98_sha256':plan['accepted98_sha256'],
   'boot_id':boot,'peak_bytes':peak,'created_at':now().isoformat()})
  # External retained owner installs signal custody BEFORE this function/first mutation.
  need(not os.path.lexists(release));release.mkdir(mode=0o750);os.chown(release,0,ns['gid']);ns['sync'](release.parent)
  guard_deadline(plan,boot,boot_reader,now)
  ns['extract'](archive,release,expected,ns['gid']);ns['verify_source'](release,expected)
  venv=release/'backend/venv'
  guard_deadline(plan,boot,boot_reader,now)
  own_child([str(interpreter),'-I','-S','-B','-m','venv','--without-pip','--copies',str(venv)],record,birth)
  guard_deadline(plan,boot,boot_reader,now)
  ns['copy_packages'](packages,venv/'lib/python3.12/site-packages',provenance['site_packages'],ns['gid'])
  ns['materialize_lib64'](venv)
  for folder,_,files in os.walk(venv):
   os.chown(folder,0,ns['gid']);os.chmod(folder,0o750)
   for name in files:
    leaf=Path(folder)/name;need(not leaf.is_symlink());os.chown(leaf,0,ns['gid']);os.chmod(leaf,0o750 if leaf.stat().st_mode&0o111 else 0o640)
  ns['verify_source'](release,expected)
  need(ns['inventory'](packages)==provenance['site_packages'] and ns['inventory'](ns['BROWSER'])==provenance['browser'])
  guard_deadline(plan,boot,boot_reader,now);auth98(plan['accepted98_sha256'])
  runtime=ns['runtime_manifest'](release) # First runtime hash derived AFTER creation/auth.
  proof={'schema':'careertrellis.corrected97-materialization-proof/v1','revision':rev,'plan_sha256':sha(canonical(plan)),
   'accepted98_sha256':plan['accepted98_sha256'],'source_manifest_sha256':plan['source_manifest_sha256'],
   'runtime_manifest_sha256':sha(canonical(runtime)),'boot_id':boot,'completed_at':now().isoformat()}
  record('materialization-proof.json',proof);record('runtime-before.json',runtime)
  return proof,runtime
