"""Fully SYNTHETIC expired97+98 byte DAG; public code fixtures only, no real receipts."""
import copy,json,hashlib
from pathlib import Path
import qualified97_history as q
import qualified97_records as r
import checks97
import fixture98
ROOT=Path(__file__).resolve().parent

def encoded(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def sha(b):return hashlib.sha256(b).hexdigest()
def relink98(bundle):
 links={'source_manifest_sha256':'source-manifest.json','old_failure_chain_sha256':'old-failure-chain.json','full_live_preflight_sha256':'full-preflight.json','repair_plan_sha256':'repair-plan.json','repair_decision_sha256':'repair-decision.json','checks_sha256':'repair-checks.json','unit_terminal_sha256':'unit-terminal.json','candidate_sha256':'candidate.json','owner_terminal_sha256':'owner-terminal.json','postterminal_proof_sha256':'postterminal-owner-proof.json','unit_intent_sha256':'unit-intent.json','test_client_sha256':'test-client.json','child_spawn_sha256':'child-spawn.json'}
 order=['source-manifest.json','old-failure-chain.json','full-preflight.json','repair-plan.json','repair-decision.json','owner.json','coordinator.json','child-spawn.json','unit-intent.json','test-client.json','unit-terminal.json','repair-checks.json','candidate.json','owner-terminal.json','postterminal-owner-proof.json','accepted.json']
 for name in order:
  v=q.decode(bundle[name])
  for field,node in links.items():
   if field in v and node!=name:v[field]=sha(bundle[node])
  if name=='owner-terminal.json':v['owner']=q.decode(bundle['owner.json']);v['coordinator']=q.decode(bundle['coordinator.json'])
  if name=='unit-terminal.json':v['execution']['state']['Description']='careertrellis-repair98:'+sha(bundle['unit-intent.json'])
  bundle[name]=encoded(v)
 return bundle

def sources():
 reg=(ROOT/'registry97.snapshot.json').read_bytes();registry=q.decode(reg)
 public={'qualified97/'+name:(ROOT/'public-fixtures/qualified97-source'/name).read_bytes() for name in q.SOURCE13}
 public['target97/deploy-release.sh']=(ROOT/'public-fixtures/target97-deploy-release.sh').read_bytes()
 public['preparation291d/prepare_release.py']=(ROOT/'public-fixtures/prepare_release291d.py').read_bytes()
 public['target97/backend/test_p0_regressions.py']=(ROOT/'public-fixtures/corrected-test_p0_regressions.py.source').read_bytes()
 public['target97/scripts/value_evaluation/test_campaign_transport.py']=(ROOT/'public-fixtures/corrected-test_campaign_transport.py.source').read_bytes()
 public['repair98/registry.snapshot.json']=(ROOT/'registry.snapshot.json').read_bytes()
 old={name:(ROOT/'public-fixtures/repair98-source'/name).read_bytes() for name in q.SOURCE7}
 return registry,reg,public,old

def old_bundle(old):
 bundle,unused,registry=fixture98.fixture()
 for name in bundle:
  v=q.decode(bundle[name])
  if v.get('repair_ops_commit'):v['repair_ops_commit']=q.OPS98
  bundle[name]=encoded(v)
 manifest=q.decode(bundle['source-manifest.json']);manifest['repair_ops_commit']=q.OPS98
 manifest['files']={n:{'bytes':len(raw),'sha256':sha(raw)} for n,raw in old.items()};bundle['source-manifest.json']=encoded(manifest)
 runner=manifest['files']['run_checks_repair98.py']['sha256'];helper=manifest['files']['fixture_relocation.py']['sha256']
 for name in ('unit-intent.json','repair-checks.json','accepted.json'):
  v=q.decode(bundle[name]);v['runner_sha256']=runner;v['qualifier_sha256']=helper
  if name=='unit-intent.json':
   p=q.decode(bundle['repair-plan.json']);v['argv']=q.history98.approved_argv({'repair_ops_commit':q.OPS98,'runner_sha256':runner,'old_failure_chain_sha256':sha(bundle['old-failure-chain.json']),'retained_runtime_before_sha256':sha(bundle['runtime-before.json'])})
  bundle[name]=encoded(v)
 return relink98(bundle)

class Synthetic97:
 def __init__(self):
  self.registry,self.registry_raw,self.sources,self.sources98=sources();self.old=old_bundle(self.sources98)
  self.v={};self.b={};self.boot='12345678-1234-1234-1234-123456789abc';self.ops=q.APPROVED_OPS;self.runtime={'backend/venv/lib/synthetic.py':'3'*64,'backend/__pycache__/synthetic.cpython-312.pyc':'4'*64}
  def at(n):return '2025-01-02T00:%02d:00+00:00'%n
  self.at=at
  def closed(name,values):return {'schema':r.SCHEMAS[name][0],**values}
  m={'schema':'careertrellis.corrected97-source-manifest/v1','kind':'corrected-fixture97-new/v1','ops_commit':self.ops,'files':copy.deepcopy(self.registry['source_files'])};self.v['source-manifest.json']=m
  self.v['runtime-before.json']=copy.deepcopy(self.runtime);self.v['runtime-manifest.json']=copy.deepcopy(self.runtime);runtime=sha(encoded(self.runtime));browser='5'*64
  self.v['preflight.json']=closed('preflight.json',{'kind':'corrected-fixture97-new/v1','observed_at':at(0),'boot_id':self.boot,'source_manifest_sha256':'','accepted98_receipt_sha256':'','archive_sha256':q.specs.exact_spec('corrected-fixture97-new/v1')['archive_sha256'],'provenance_sha256':'6'*64,'baseline':q.BASE,'previous':q.PREVIOUS,'live_build':'20261017.96','release_absent':True,'namespace_absent':True,'unit_absent':True,'software_inventory_sha256':'7'*64,'browser_manifest_sha256':browser,'capacity':{'available_bytes':12*1024**3,'available_inodes':100000,'memory_available':1024**3},'result':'pass'})
  self.v['plan.json']={'schema':'careertrellis.corrected97-materialization-plan/v1','kind':'corrected-fixture97-new/v1','revision':q.REV,'build':'20261026.97','source_manifest_sha256':'','archive_sha256':self.v['preflight.json']['archive_sha256'],'provenance_sha256':'6'*64,'accepted98_sha256':'','full_preflight_sha256':'','decision_at':at(1),'expires_at':at(56)}
  self.v['decision.json']={'schema':'careertrellis.corrected97-decision/v1','kind':'corrected-fixture97-new/v1','plan_sha256':'','source_manifest_sha256':'','boot_id':self.boot,'decision_at':at(1),'expires_at':at(56)}
  self.v['owner.json']=closed('owner.json',{'pid':500,'birth':'100','boot_id':self.boot,'plan_sha256':'','started_at':at(2)})
  self.v['coordinator.json']=closed('coordinator.json',{'pid':501,'birth':'101','boot_id':self.boot,'owner_pid':500,'plan_sha256':'','started_at':at(3)})
  self.v['child-spawn.json']=closed('child-spawn.json',{'pid':501,'birth':'101','boot_id':self.boot,'owner_pid':500,'plan_sha256':'','decision_sha256':'','source_manifest_sha256':''})
  self.v['intent.json']={'kind':'corrected-fixture97-new/v1','plan_sha256':'','decision_sha256':'','source_manifest_sha256':'','owner_pid':500,'owner_birth':'100','boot_id':self.boot,'no_replay':True}
  self.v['coordinator-spawn-intent.json']={'argv':[],'plan_sha256':'','decision_sha256':'','boot_id':self.boot,'owner_pid':500,'owner_birth':'100'}
  self.v['materialization-intent.json']=closed('materialization-intent.json',{'revision':q.REV,'plan_sha256':'','source_manifest_sha256':'','accepted98_sha256':'','boot_id':self.boot,'peak_bytes':2*1024**3,'created_at':at(4)})
  self.v['materialization-child.json']={'pid':503,'start_ticks':'103','argv':['/usr/bin/python3.12','-I','-S','-B','-m','venv','--without-pip','--copies','/opt/careertrellis/staging/releases/'+q.REV+'/backend/venv']}
  self.v['materialization-proof.json']=closed('materialization-proof.json',{'revision':q.REV,'plan_sha256':'','accepted98_sha256':'','source_manifest_sha256':'','runtime_manifest_sha256':runtime,'boot_id':self.boot,'completed_at':at(5)})
  self.v['unit-intent.json']=closed('unit-intent.json',{'kind':'corrected-fixture97-new/v1','plan_sha256':'','decision_sha256':'','argv':[],'properties':q._properties97(q.specs.exact_spec('corrected-fixture97-new/v1')),'source_manifest_sha256':'','coordinator_pid':501,'coordinator_birth':'101','boot_id':self.boot,'created_at':at(6)})
  self.v['unit-client.json']=closed('unit-client.json',{'pid':502,'birth':'102','boot_id':self.boot,'intent_sha256':''})
  group={'path':'/system.slice/'+q.UNIT+'.service','exists':False,'processes':0,'populated':0};empty={'LoadState':'not-found','MainPID':'0','ControlPID':'0','Job':'','InvocationID':''}
  state={'LoadState':'loaded','ActiveState':'active','SubState':'exited','MainPID':'0','ControlPID':'0','Job':'','Result':'success','ExecMainStatus':'0','InvocationID':'b'*32,'Description':''}
  self.v['unit-terminal.json']=closed('unit-terminal.json',{'kind':'corrected-fixture97-new/v1','unit':q.UNIT,'intent_sha256':'','client_sha256':'','boot_id':self.boot,'observed_at':at(9),'execution':{'unit':q.UNIT,'invocation_id':'b'*32,'state':state,'cgroup':copy.deepcopy(group)},'after':copy.deepcopy(empty),'cgroup_after':copy.deepcopy(group),'client_pid':502,'client_birth':'102','client_exit':0})
  qualification={'schema':'careertrellis.corrected-fixture-native-proof/v1','source_kind':'corrected-fixture97-new/v1','candidate':q.REV,'module_sha256':checks97.MODULE_SHA,'method_ast_sha256':copy.deepcopy(checks97.NATIVE_METHOD_AST_SHA),'native_ids_succeeded':checks97.NATIVE_IDS[:],'assertion_evidence':'source_authenticated_positive_zero_and_missing_helper_nonzero_assertions_succeeded','runtime_substitution':False}
  expected=q._catalogs(self.sources);details={}
  for name,modules in expected.items():
   ids=[module+'.SyntheticCase.test_ok' for module in modules if module!='test_campaign_transport']
   if 'test_campaign_transport' in modules:ids+=q.corrected_source_contract(self.sources)
   if name=='backend':ids+=sorted(q.history98.NATIVE_IDS)
   block={'tests':len(ids),'modules':modules,'skipped':[],'success_ids':sorted(q.history98.NATIVE_IDS) if name=='backend' else [],'catalog_ids':ids,'catalog_sha256':sha(json.dumps(ids,separators=(',',':')).encode()),'successful_ids':ids[:],'failures':0,'errors':0}
   if name=='backend':block['native_source_proof']=qualification
   details[name]=block
  details['startup']={'api_build':'20261026.97','dashboard_match':True,'owned_process_stopped':True,'exit_signal':'SIGTERM','log_sha256':'8'*64}
  self.v['checks.json']={'schema':'careertrellis.preparation97-corrected-checks/v1','repair_ops_commit':self.ops,'runner_sha256':m['files']['run_checks_qualified97.py']['sha256'],'native_assertions_sha256':m['files']['native_assertions97.py']['sha256'],'accepted98_receipt_sha256':'','retained_runtime_manifest_sha256':runtime,'qualification':'permanent TMPDIR fixtures; no runtime substitution','revision':q.REV,'build':'20261026.97','checks':{k:True for k in q.specs.exact_spec('corrected-fixture97-new/v1')['checks']},'details':details,'started_utc':at(7),'ended_utc':at(8),'network_namespace_loopback_only':True,'provider_requests':0}
  self.v['candidate.json']=closed('candidate.json',{'kind':'corrected-fixture97-new/v1','revision':q.REV,'build':'20261026.97','ops_commit':self.ops,'source_manifest_sha256':'','plan_sha256':'','decision_sha256':'','accepted98_receipt_sha256':'','materialization_proof_sha256':'','runtime_before_sha256':runtime,'runtime_after_sha256':runtime,'browser_before_sha256':browser,'browser_after_sha256':browser,'checks_sha256':'','unit_terminal_sha256':'','completed_at':at(10),'result':'candidate_only_not_accepted'})
  captures={k:{'retained_bytes':0,'discarded_bytes':0,'eof':True,'error':False} for k in ('stdout','stderr')}
  self.v['owner-terminal.json']=closed('owner-terminal.json',{'kind':'corrected-fixture97-new/v1','boot_id':self.boot,'owner':{},'coordinator':{},'child_spawn_sha256':'','coordinator_exit':0,'plan_sha256':'','decision_sha256':'','source_manifest_sha256':'','candidate_sha256':'','checks_sha256':'','unit_terminal_sha256':'','runtime_after_sha256':runtime,'captures':captures,'owner_self_exit_claim':False,'ended_at':at(11)})
  self.v['postterminal-proof.json']=closed('postterminal-proof.json',{'kind':'corrected-fixture97-new/v1','boot_id':self.boot,'observed_at':at(12),'plan_sha256':'','decision_sha256':'','source_manifest_sha256':'','accepted98_receipt_sha256':'','candidate_sha256':'','owner_terminal_sha256':'','owner_state':'absent','coordinator_state':'absent','unit':copy.deepcopy(empty),'cgroup_after':copy.deepcopy(group),'runtime_manifest_sha256':runtime,'browser_manifest_sha256':browser,'original_deadline':at(56)})
  self.v['accepted.json']=closed('accepted.json',{'result':'corrected_fixtures_prepared_inactive','source_kind':'corrected-fixture97-new/v1','revision':q.REV,'build':'20261026.97','baseline':q.BASE,'previous':q.PREVIOUS,'attempt_id':r.ATTEMPT,'ops_commit':self.ops,'source_manifest_sha256':'','plan_sha256':'','decision_sha256':'','candidate_sha256':'','archive_sha256':self.v['plan.json']['archive_sha256'],'materialization_proof_sha256':'','accepted98_receipt_sha256':'','runtime_manifest':r.DOMAIN+'/runtime-manifest.json','runtime_manifest_sha256':runtime,'runtime_before_sha256':runtime,'native_source_proof':copy.deepcopy(qualification),'checks':copy.deepcopy(self.v['checks.json']['checks']),'checks_sha256':'','unit_intent_sha256':'','unit_terminal_sha256':'','child_spawn_sha256':'','owner_terminal_sha256':'','postterminal_proof_sha256':'','completed_utc':at(13),'activated':False})
  self.refresh()
 def refresh(self):
  self.b={name:encoded(value) for name,value in self.v.items()}
  oldsha=sha(self.old['accepted.json']);links={'source_manifest_sha256':'source-manifest.json','full_preflight_sha256':'preflight.json','plan_sha256':'plan.json','decision_sha256':'decision.json','materialization_proof_sha256':'materialization-proof.json','intent_sha256':'unit-intent.json','unit_intent_sha256':'unit-intent.json','client_sha256':'unit-client.json','unit_terminal_sha256':'unit-terminal.json','checks_sha256':'checks.json','candidate_sha256':'candidate.json','child_spawn_sha256':'child-spawn.json','owner_terminal_sha256':'owner-terminal.json','postterminal_proof_sha256':'postterminal-proof.json','runtime_manifest_sha256':'runtime-manifest.json','runtime_after_sha256':'runtime-manifest.json','runtime_before_sha256':'runtime-before.json'}
  order=['source-manifest.json','runtime-before.json','runtime-manifest.json','preflight.json','plan.json','decision.json','owner.json','coordinator.json','child-spawn.json','intent.json','coordinator-spawn-intent.json','materialization-intent.json','materialization-child.json','materialization-proof.json','unit-intent.json','unit-client.json','unit-terminal.json','checks.json','candidate.json','owner-terminal.json','postterminal-proof.json','accepted.json']
  for name in order:
   value=self.v[name]
   for field,node in links.items():
    if field in value and node!=name:value[field]=sha(self.b[node])
   for field in ('accepted98_sha256','accepted98_receipt_sha256'):
    if field in value:value[field]=oldsha
   if name=='unit-intent.json':value['argv']=q._argv97(q.specs.exact_spec('corrected-fixture97-new/v1'),self.v['plan.json'],self.v['source-manifest.json'],sha(self.b['runtime-manifest.json']))
   if name=='coordinator-spawn-intent.json':value['argv']=['/usr/bin/python3','-I','-S','-B',q.STAGE+'/qualified_entry.py','--role','coordinator','--ops-commit',self.ops,'--approved-source-manifest-sha256',sha(self.b['source-manifest.json']),'--approved-plan-sha256',sha(self.b['plan.json']),'--approved-decision-sha256',sha(self.b['decision.json'])]
   if name=='unit-terminal.json':value['execution']['state']['Description']='careertrellis-qualified97:'+sha(self.b['unit-intent.json'])
   if name=='owner-terminal.json':value['owner']=copy.deepcopy(self.v['owner.json']);value['coordinator']=copy.deepcopy(self.v['coordinator.json'])
   self.b[name]=encoded(value)
  self.binding={'source_kind':'corrected-fixture97-new/v1','revision':q.REV,'build':'20261026.97','receipt_sha256':sha(self.b['accepted.json']),'ops_commit':self.ops,'registry_sha256':sha(self.registry_raw),'accepted98_receipt_sha256':oldsha}
 def inspect(self):return q.inspect_history(self.b,self.binding,self.sources,self.old,self.sources98,self.registry_raw)
 def mutate_occurrence(self,node,path,value):
  """Retain one exact serialized mutant; relink only digest ancestors."""
  sp_positions={flag:self.v['coordinator-spawn-intent.json']['argv'].index(flag)+1 for flag in ('--approved-source-manifest-sha256','--approved-plan-sha256','--approved-decision-sha256')}
  unit_runtime_index=self.v['unit-intent.json']['argv'].index('--retained-runtime-manifest-sha256')+1
  target=(node,tuple(path));item=self.v[node]
  for key in path[:-1]:item=item[key]
  item[path[-1]]=value
  links={'source_manifest_sha256':'source-manifest.json','full_preflight_sha256':'preflight.json','plan_sha256':'plan.json','decision_sha256':'decision.json','materialization_proof_sha256':'materialization-proof.json','intent_sha256':'unit-intent.json','unit_intent_sha256':'unit-intent.json','client_sha256':'unit-client.json','unit_terminal_sha256':'unit-terminal.json','checks_sha256':'checks.json','candidate_sha256':'candidate.json','child_spawn_sha256':'child-spawn.json','owner_terminal_sha256':'owner-terminal.json','postterminal_proof_sha256':'postterminal-proof.json','runtime_manifest_sha256':'runtime-manifest.json','runtime_after_sha256':'runtime-manifest.json','runtime_before_sha256':'runtime-before.json'}
  order=['source-manifest.json','runtime-before.json','runtime-manifest.json','preflight.json','plan.json','decision.json','owner.json','coordinator.json','child-spawn.json','intent.json','coordinator-spawn-intent.json','materialization-intent.json','materialization-child.json','materialization-proof.json','unit-intent.json','unit-client.json','unit-terminal.json','checks.json','candidate.json','owner-terminal.json','postterminal-proof.json','accepted.json']
  def put(n,where,new):
   if (n,tuple(where))==target:return
   obj=self.v[n]
   for k in where[:-1]:obj=obj[k]
   obj[where[-1]]=new
  def visit(n,obj,where=()):
   if isinstance(obj,dict):
    for key,entry in list(obj.items()):
     if key in links and links[key]!=n:put(n,where+(key,),sha(self.b[links[key]]))
     elif key in ('accepted98_sha256','accepted98_receipt_sha256'):put(n,where+(key,),sha(self.old['accepted.json']))
     visit(n,obj[key],where+(key,))
   elif isinstance(obj,list):
    for index,entry in enumerate(obj):visit(n,entry,where+(index,))
  for name in order:
   visit(name,self.v[name])
   if name=='coordinator-spawn-intent.json':
    argv=self.v[name]['argv']
    for flag,leaf in [('--approved-source-manifest-sha256','source-manifest.json'),('--approved-plan-sha256','plan.json'),('--approved-decision-sha256','decision.json')]:
     index=sp_positions[flag];put(name,('argv',index),sha(self.b[leaf]))
   if name=='unit-intent.json':
    argv=self.v[name]['argv'];index=unit_runtime_index;put(name,('argv',index),sha(self.b['runtime-manifest.json']))
   if name=='unit-terminal.json':put(name,('execution','state','Description'),'careertrellis-qualified97:'+sha(self.b['unit-intent.json']))
   self.b[name]=encoded(self.v[name])
  self.binding['receipt_sha256']=sha(self.b['accepted.json'])
  serialized=q.decode(self.b[node])
  for key in path:serialized=serialized[key]
  assert serialized==value,('mutant repaired',node,path)
