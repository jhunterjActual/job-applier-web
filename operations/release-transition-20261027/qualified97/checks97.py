"""Closed97 checks and source-runner actual catalog validator; no assumed counts."""
import hashlib,json,os,re,stat
from pathlib import Path
WORK=Path('/opt/careertrellis/staging/preparation97-qualified-work-20261027/qualified97-fixture-g1')
MODULE_SHA='86e52d2e9fbc15acc28e94af5beb607cdf278bb4a091b50899e77ccd91dcafdc'
def need(v):
 if not v:raise RuntimeError('QUALIFIED97_CHECKS_REFUSED')
def read_checks():
 path=WORK/'qualified-checks.json';s=path.lstat();fields=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
 need(stat.S_ISREG(s.st_mode) and s.st_uid==65534 and s.st_nlink==1 and not s.st_mode&0o022 and 0<s.st_size<=1048576)
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 try:
  identity=tuple(getattr(s,k) for k in fields);need(tuple(getattr(os.fstat(fd),k) for k in fields)==identity)
  with os.fdopen(os.dup(fd),'rb') as stream:raw=stream.read(s.st_size+1)
  need(len(raw)==s.st_size and tuple(getattr(os.fstat(fd),k) for k in fields)==identity and tuple(getattr(path.lstat(),k) for k in fields)==identity)
  return raw
 finally:os.close(fd)
def catalog(value,qualified):
 keys={'tests','modules','skipped','success_ids','catalog_ids','catalog_sha256','successful_ids','failures','errors'}|({'fixture_qualification'} if qualified else set())
 need(type(value) is dict and set(value)==keys and type(value['tests']) is int and value['tests']>0
  and type(value['failures']) is int and value['failures']==0 and type(value['errors']) is int and value['errors']==0)
 ids=value['catalog_ids'];need(type(ids) is list and len(ids)==len(set(ids))==value['tests'])
 need(all(type(x) is str and len(x)<=512 and re.fullmatch(r'test_[A-Za-z0-9_]+\.[A-Za-z0-9_.]+',x) for x in ids))
 need(all(x.split('.')[0] in value['modules'] for x in ids))
 need(hashlib.sha256(json.dumps(ids,separators=(',',':')).encode()).hexdigest()==value['catalog_sha256'])
 need(type(value['successful_ids']) is list and type(value['skipped']) is list and sorted(value['successful_ids']+value['skipped'])==sorted(ids))
 need(set(value['success_ids'])<=set(value['successful_ids']))
 return ids
def validate(value,spec,plan,manifest,runtime_sha,expected_modules):
 keys={'schema','repair_ops_commit','runner_sha256','qualifier_sha256','accepted98_receipt_sha256','retained_runtime_manifest_sha256','qualification','revision','build','checks','details','started_utc','ended_utc','network_namespace_loopback_only','provider_requests'}
 need(type(value) is dict and set(value)==keys and value['schema']=='careertrellis.preparation97-qualified-checks/v1'
  and value['revision']==spec['revision'] and value['build']==spec['build'] and value['repair_ops_commit']==manifest['ops_commit']
  and value['runner_sha256']==manifest['files']['run_checks_qualified97.py']['sha256']
  and value['qualifier_sha256']==manifest['files']['fixture_relocation97.py']['sha256']
  and value['accepted98_receipt_sha256']==plan['accepted98_sha256'] and value['retained_runtime_manifest_sha256']==runtime_sha
  and set(value['checks'])==spec['checks'] and all(x is True for x in value['checks'].values())
  and value['qualification']=='exact-one fixture-root relocation; original app/test bytes unchanged'
  and value['network_namespace_loopback_only'] is True and type(value['provider_requests']) is int and value['provider_requests']==0)
 need(set(value['details'])=={'backend','summary','evaluation','startup'})
 for name in ('backend','summary','evaluation'):
  block=value['details'][name];need(block['modules']==expected_modules[name]);catalog(block,name=='backend')
 qualification(value['details']['backend']['fixture_qualification'])
 return value
def qualification(q):
 keys={'schema','test_id','calls','constructor_returned','method_returned','binding_restored','analyzer_returncodes','serial_suite_verified','runtime_code_verified','fixture_ancestors_verified','module_sha256','method_source_sha256','method_ast_sha256','qualification'}
 need(type(q) is dict and set(q)==keys and q['schema']=='careertrellis.exact-fixture-relocation/v1'
  and q['qualification']=='one TemporaryDirectory dir relocated; unchanged body and real positive/negative analyzer calls'
  and type(q['calls']) is int and q['calls']==1 and q['test_id']=='test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables'
  and all(q[k] is True for k in ('constructor_returned','method_returned','binding_restored','serial_suite_verified','runtime_code_verified','fixture_ancestors_verified'))
  and q['module_sha256']==MODULE_SHA and q['method_source_sha256']=='584aec03af3f6347fbce3f10f56b449deeea0925f3365e6f28a5d3cfcbd85cac'
  and q['method_ast_sha256']=='ad2abbb01e5d212e3045b604105c2dc788281d56d64735887a827caad841af73'
  and type(q['analyzer_returncodes']) is list and len(q['analyzer_returncodes'])==2
  and all(type(v) is int for v in q['analyzer_returncodes']) and q['analyzer_returncodes'][0]==0 and 1<=q['analyzer_returncodes'][1]<=255)
