"""Fixed97 source-bound root entrypoint DRAFT. No live activation permitted."""
import argparse,hashlib,json,os,socket,stat,subprocess,sys,types
from pathlib import Path
READY=True
STAGE=Path('/opt/careertrellis/staging/preparation97-qualified-source-20261027/qualified97-fixture-g1')
SOURCE='operations/release-transition-20261027/qualified97/'
FILES={'qualified_specs.py','materialize97.py','accepted98_dependency.py','source_boundary.py','qualified_route.py','admission97.py','fixture_relocation97.py','run_checks_qualified97.py','native97_unit.py','checks97.py','qualified97_records.py','qualified_entry.py','historical98_controls.py'}
def need(v):
 if not v:raise RuntimeError('QUALIFIED97_ENTRY_REFUSED')
def raw(path,limit=1048576):
 for parent in path.parents:
  st=parent.lstat();need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022)
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1 and not before.st_mode&0o022 and before.st_size<=limit)
  with os.fdopen(os.dup(fd),'rb') as f:b=f.read(before.st_size+1)
  after=os.fstat(fd);leaf=path.lstat();fields=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
  need(len(b)==before.st_size and all(getattr(before,k)==getattr(after,k)==getattr(leaf,k) for k in fields));return b
 finally:os.close(fd)
def closed_pairs(items):
 result={}
 for k,v in items:need(k not in result);result[k]=v
 return result
def load(a):
 b=raw(STAGE/'source-manifest.json');need(hashlib.sha256(b).hexdigest()==a.approved_source_manifest_sha256)
 manifest=json.loads(b,object_pairs_hook=closed_pairs)
 need(set(manifest)=={'schema','kind','ops_commit','files'} and manifest['schema']=='careertrellis.qualified97-source-manifest/v1'
  and manifest['kind']=='qualified97-new/v1' and manifest['ops_commit']==a.ops_commit and set(manifest['files'])==FILES)
 snapshots={}
 for name,info in manifest['files'].items():
  need(set(info)=={'bytes','sha256'});data=raw(STAGE/name)
  need(len(data)==info['bytes'] and hashlib.sha256(data).hexdigest()==info['sha256'])
  p=subprocess.run(['git','-C','/srv/careertrellis/job-applier-web','cat-file','blob',a.ops_commit+':'+SOURCE+name],stdin=subprocess.DEVNULL,
   capture_output=True,timeout=30,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','HOME':'/root','GIT_OPTIONAL_LOCKS':'0'})
  need(p.returncode==0 and p.stdout==data);snapshots[name]=data
 # Execute the exact authenticated snapshots, never reread modules for import.
 for name in ('qualified_specs','materialize97','historical98_controls','accepted98_dependency','source_boundary','qualified97_records','native97_unit','checks97','admission97','qualified_route'):
  module=types.ModuleType(name);module.__file__=str(STAGE/(name+'.py'));sys.modules[name]=module
  exec(compile(snapshots[name+'.py'],module.__file__,'exec'),module.__dict__)
 return sys.modules['admission97'],sys.modules['qualified_route']
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--role',choices=('preflight','decision-proposal','owner','coordinator','seal'),required=True)
 ap.add_argument('--ops-commit',required=True);ap.add_argument('--approved-source-manifest-sha256',required=True)
 ap.add_argument('--approved-plan-sha256');ap.add_argument('--approved-decision-sha256');ap.add_argument('--accepted98-sha256');a=ap.parse_args()
 try:
  need(READY and sys.platform=='linux' and os.geteuid()==0 and socket.gethostname()=='careertrellis-staging-web-1'
   and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)
  need(len(a.ops_commit)==40 and all(x in '0123456789abcdef' for x in a.ops_commit))
  admission,route=load(a);boundary=admission.Admission97(a.ops_commit)
  if a.role=='preflight':
   need(a.accepted98_sha256 is not None);value=boundary.build_readonly_preflight(a.accepted98_sha256)
   print(json.dumps(value,sort_keys=True));return 0
  if a.role=='decision-proposal':
   plan,decision=boundary.build_decision_from_actual_preflight()
   print(json.dumps({'schema':'careertrellis.qualified97-decision-proposal/v1','plan':plan,'decision':decision,'allocated':False},sort_keys=True));return 0
  need(a.approved_plan_sha256 is not None and a.approved_decision_sha256 is not None)
  plan,decision,manifest=boundary.load_closed_controls('qualified97-new/v1')
  need(boundary.digest(plan)==a.approved_plan_sha256 and boundary.digest(decision)==a.approved_decision_sha256
   and boundary.digest(manifest)==a.approved_source_manifest_sha256)
  if a.role=='owner':result=route.retained_owner(boundary,'qualified97-new/v1')
  elif a.role=='coordinator':result=route.coordinate(boundary,'qualified97-new/v1')
  else:result=route.seal(boundary,'qualified97-new/v1')
  print(json.dumps({'role':a.role,'result':'terminal_role_returned','accepted_claim':a.role=='seal' and result==0}));return result or 0
 except BaseException:
  try:print('{"result":"qualified97_refused_or_unknown","no_replay":true}')
  except BaseException:pass
  return 1
if __name__=='__main__':raise SystemExit(main())
