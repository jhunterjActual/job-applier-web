"""Closed97 checks and source-runner actual catalog validator; no assumed counts."""
import hashlib,json,os,re,stat
from pathlib import Path
WORK=Path('/opt/careertrellis/staging/preparation97-corrected-work-20261027/corrected97-fixture-g1')
NATIVE_METHOD_AST_SHA={'test_hosted_authority_marker_is_one_way_and_runtime_checks_fail_closed':'a6a08ee80e453655bdedc142bbaa2400a25032afd6a35639fddf863de3ae1682','test_backup_health_units_resolve_only_staged_executables':'7d4149105c78a3540878369367d697b71d275d04b9235c6d6f4a41b6e5c7ddd0'}
NATIVE_IDS=['test_p0_regressions.DependencyLockTests.test_hosted_authority_marker_is_one_way_and_runtime_checks_fail_closed','test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables']
MODULE_SHA='db1604118386f23c9039fea1f3ccbec933f9a4d4e40ed7b9d9f37f7cde67fe13'
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
 keys={'tests','modules','skipped','success_ids','catalog_ids','catalog_sha256','successful_ids','failures','errors'}|({'native_source_proof'} if qualified else set())
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
 keys={'schema','repair_ops_commit','runner_sha256','native_assertions_sha256','accepted98_receipt_sha256','retained_runtime_manifest_sha256','qualification','revision','build','checks','details','started_utc','ended_utc','network_namespace_loopback_only','provider_requests'}
 need(type(value) is dict and set(value)==keys and value['schema']=='careertrellis.preparation97-corrected-checks/v1'
  and value['revision']==spec['revision'] and value['build']==spec['build'] and value['repair_ops_commit']==manifest['ops_commit']
  and value['runner_sha256']==manifest['files']['run_checks_qualified97.py']['sha256']
  and value['native_assertions_sha256']==manifest['files']['native_assertions97.py']['sha256']
  and value['accepted98_receipt_sha256']==plan['accepted98_sha256'] and value['retained_runtime_manifest_sha256']==runtime_sha
  and set(value['checks'])==spec['checks'] and all(x is True for x in value['checks'].values())
  and value['qualification']=='permanent TMPDIR fixtures; no runtime substitution'
  and value['network_namespace_loopback_only'] is True and type(value['provider_requests']) is int and value['provider_requests']==0)
 need(set(value['details'])=={'backend','summary','evaluation','startup'})
 for name in ('backend','summary','evaluation'):
  block=value['details'][name];need(block['modules']==expected_modules[name]);catalog(block,name=='backend')
 qualification(value['details']['backend']['native_source_proof'])
 return value
def qualification(q):
 keys={'schema','source_kind','candidate','module_sha256','method_ast_sha256','native_ids_succeeded','assertion_evidence','runtime_substitution'}
 need(type(q) is dict and set(q)==keys and q['schema']=='careertrellis.corrected-fixture-native-proof/v1'
  and q['source_kind']=='corrected-fixture97-new/v1'
  and q['candidate']=='bc68ea0dc253425a44e268452130becdf35d764e'
  and q['module_sha256']==MODULE_SHA
  and q['method_ast_sha256']==NATIVE_METHOD_AST_SHA
  and q['native_ids_succeeded']==NATIVE_IDS
  and q['assertion_evidence']=='source_authenticated_positive_zero_and_missing_helper_nonzero_assertions_succeeded'
  and q['runtime_substitution'] is False)
