"""Planless protected historical gate. No Backend, live plan, clocks or expired Admission."""
import ast,hashlib,json,re,sys,types
from contextlib import contextmanager
OPS='cf95f9603484ee18bb6bea95f1721000a19d955f'
PACKET_SHA='625dac8c93b87853bf4e5473902f9426924c151f2fc208bf02f01b090eefb649'
READINESS_SHA='1de62597d5ab81e113d9d3ee69078bcdae6375b2ac1eba6eb78b90f9ffbd7244'
TARGET='bc68ea0dc253425a44e268452130becdf35d764e';CORRECTION='f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
ACCEPTED97='199a0c5db19644054d50ea09160146c5c325a6c7fe95733ffc41ffea619885c7'
ACCEPTED98='d91cb6623a18a98ce082e02f55b09e5e2a971ba985dd062f1ba6094ba3bacff3'
PURE=('prepared_types','historical98_controls','prepared98_history','prepared97_specs','prepared97_records','prepared97_checks','prepared_reader','prepared_sources','prepared97_reader','prepared97_sources','prepared97_history','prepared_registries')
PREFIX='operations/release-transition-20261026/'
class Refused(RuntimeError):pass
def need(v):
 if not v:raise Refused('PREPLAN_HISTORICAL_GATE_REFUSED')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def decode(raw):
 def pairs(rows):
  d={}
  for k,v in rows:need(k not in d);d[k]=v
  return d
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
def validate_context(c,*,test_only=False):
 need(type(c) is dict and set(c)=={'schema','qualification','runtime_source_commit','packet_sha256','readiness_manifest_sha256','prepared_receipts'} and c['schema']=='careertrellis.preplan-historical-context/v1')
 need(c['runtime_source_commit']==OPS and c['packet_sha256']==PACKET_SHA and c['readiness_manifest_sha256']==READINESS_SHA)
 need(type(c['prepared_receipts']) is dict and set(c['prepared_receipts'])=={TARGET,CORRECTION} and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) and v!='0'*64 for v in c['prepared_receipts'].values()))
 if test_only:need(c['qualification']=='SYNTHETIC_HISTORY_TEST_ONLY')
 else:need(c['qualification']=='authenticated_private_historical_inputs' and c['prepared_receipts']=={TARGET:ACCEPTED97,CORRECTION:ACCEPTED98})
 return c

@contextmanager
def authenticated_modules(packet_raw,git_blob):
 need(type(packet_raw) is bytes and sha(packet_raw)==PACKET_SHA);m=decode(packet_raw)
 need(m['source_commit']==OPS and m['schema']=='careertrellis.app-release-packet/v1' and m['readiness']['SOURCE_MANIFEST.json']==READINESS_SHA)
 paths={name+'.py':pin for name,pin in m['modules'].items()};paths.update(m['scripts']);paths.update({'one_shot_readiness/'+name:pin for name,pin in m['readiness'].items()});need(len(paths)==35 and len(m['modules'])==24 and len(m['scripts'])==4 and len(m['readiness'])==7)
 cache={path:git_blob(OPS,PREFIX+path) for path in paths}
 need(all(type(raw) is bytes and sha(raw)==paths[path] for path,raw in cache.items()))
 need(all(git_blob(OPS,PREFIX+path)==raw for path,raw in cache.items()))
 need(not any(name in sys.modules for name in PURE));loaded={}
 try:
  for name in PURE:
   value=types.ModuleType(name);value.__file__='verified-public-Git:'+OPS+':'+PREFIX+name+'.py';sys.modules[name]=value;loaded[name]=value
   exec(compile(cache[name+'.py'],value.__file__,'exec'),value.__dict__)
  yield loaded
 finally:
  for name,value in loaded.items():
   need(sys.modules.get(name) is value);sys.modules.pop(name)

def private_failure_check(modules,raw,protected_read):
 # Exact loaded source's full originalfailure validator; no boolean/presence fallback.
 modules['prepared_sources'].inspect_original_failure(raw,protected_read)

def inspect_preplan_history(c,packet_raw,protected_read,git_blob,*,test_only=False):
 try:return _inspect_preplan_history(c,packet_raw,protected_read,git_blob,test_only=test_only)
 except (KeyError,TypeError,ValueError,RuntimeError,SyntaxError,ImportError):raise Refused('PREPLAN_HISTORICAL_GATE_REFUSED') from None

def _inspect_preplan_history(c,packet_raw,protected_read,git_blob,*,test_only=False):
 """Callback IO MUST be original strict protected reader + fixed readonly Git route.
 No application plan is manufactured; historical bindings are first-class.
 """
 validate_context(c,test_only=test_only)
 with authenticated_modules(packet_raw,git_blob) as m:
  protected_read=historical_read_adapter(protected_read,m,test_only=test_only)
  a=m['prepared_types'];q=m['prepared97_history'];reg98=m['prepared_registries'].REGISTRY98;reg97=m['prepared_registries'].REGISTRY97
  bundle=m['prepared97_reader'].snapshot(protected_read);old=m['prepared_reader'].snapshot_protected_history(protected_read,reg98)
  need(sha(bundle['accepted.json'])==c['prepared_receipts'][TARGET] and sha(old['accepted.json'])==c['prepared_receipts'][CORRECTION])
  public=m['prepared97_sources'].snapshot(q.APPROVED_OPS,bundle['source-manifest.json'],reg98,git_blob)
  oldpublic=m['prepared_sources'].inspect_git_sources(old['source-manifest.json'],reg98,git_blob,return_bytes=True)
  private_failure_check(m,old['old-failure-chain.json'],protected_read)
  binding={'source_kind':'corrected-fixture97-new/v1','revision':TARGET,'build':'20261026.97','receipt_sha256':c['prepared_receipts'][TARGET],'ops_commit':q.APPROVED_OPS,'registry_sha256':sha(reg97),'accepted98_receipt_sha256':c['prepared_receipts'][CORRECTION]}
  result=q.inspect_history(bundle,binding,public,old,oldpublic,reg97)
  need(set(result)=={'runtime_map','historical'} and result['historical']['authorizes_preparation'] is False and result['historical']['requires_current_source_runtime_equality'] is True)
  # Old runtime map is already inside the completely validated dependency DAG.
  old_runtime=a.decode(old['runtime-manifest.json']);need(type(old_runtime) is dict and old_runtime)
  return {'schema':'careertrellis.preplan-historical-result/v1','runtime_source_commit':OPS,'packet_sha256':PACKET_SHA,'historical':result['historical'],'runtime_maps':{TARGET:result['runtime_map'],CORRECTION:old_runtime},'validated_layers':('fixed_protected_double_snapshot','exact_public_git35_and_historical_sources','full_corrected97_and_dependency98_historical_dag','original_private_failure_bytes_and_semantics'),'unresolved_contracts':('actual_reader_and_ssh_bootstrap_source_authentication','complete_current_archive_venv_cache_runtime_equality','installed_authority_source_and_state','actual_prior_intent_owner_global_terminal_custody','fresh_native_backup_database_health_and_plan_bound_admission'),'ready_for_plan':False}

def historical_read_adapter(protected_read,modules,*,test_only=False):
 # Called ONLY inside authenticated_modules, after full original504 Git35
 # source+packet+registry bytes were verified; never a caller-provided path list.
 from native_io import ProtectedReader,HistoricalReadAdapter,HISTORICAL_LARGE_PATHS
 need(type(test_only) is bool and type(modules) is dict and set(modules)==set(PURE))
 need(all(type(value) is types.ModuleType and sys.modules.get(name) is value for name,value in modules.items()))
 reg97=modules['prepared_registries'].REGISTRY97;reg98=modules['prepared_registries'].REGISTRY98
 need(type(reg97) is bytes and sha(reg97)=='90c3beced0522633947c39f398ae21254de7f8990e336738bac36460f6b78e7f')
 need(type(reg98) is bytes and sha(reg98)=='18c97f07801ca83c05d1c434be829b5d894ffcd8dd7d905715e0bebc805a4901')
 domain97=modules['prepared97_reader'].DOMAIN;names=modules['prepared97_reader'].NAMES;domain98=modules['prepared_types'].REPAIR_ROOT
 need(type(domain97) is str and type(domain98) is str and type(names) is frozenset and all(type(name) is str for name in names))
 need(sha(json.dumps(sorted(names),separators=(',',':')).encode())=='302b5ad043f8e7244ea966bfc3cd29f87cf7cd54cb7fcc950cc19048c2ebd935')
 r97=decode(reg97);r98=decode(reg98);need(r97['domain']+'/'==domain97 and r98['domain']+'/'==domain98)
 leaves={'runtime-before.json','runtime-manifest.json'};need(leaves<=names and set(r98['plain_full_maps'])==leaves)
 derived={domain97+name for name in leaves}|{r98['plain_full_maps'][name] for name in leaves}
 need(all(r98['plain_full_maps'][name]==domain98+name for name in leaves) and derived==HISTORICAL_LARGE_PATHS)
 if test_only and type(protected_read) is not ProtectedReader:
  # Existing explicitly synthetic pure-history test callback, never production.
  return protected_read
 return HistoricalReadAdapter(protected_read)

def main(argv=None):
 # Actual collection/assembler wiring stays held; no bootstrap/clock/private IO.
 print('{"result":"preplan_historical_source_gate_only","collector_enabled":false}');return 1
if __name__=='__main__':raise SystemExit(main())
