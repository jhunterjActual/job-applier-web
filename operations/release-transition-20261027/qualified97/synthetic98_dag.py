"""SYNTHETIC closed byte-DAG. Never a real receipt or real actor/plan.
Only host/Git/protected-read observations are faked. Every validate_* function
is the actual authenticated6eacc implementation and is never patched.
"""
import copy,datetime as dt,hashlib,json
from pathlib import Path
import accepted98_dependency as dependency
ROOT=Path(__file__).resolve().parent/'public-fixtures'
def fixture_bytes(name):
 r=json.loads((ROOT/'HASH_REGISTER.json').read_text());item=r[name];p=ROOT/('repair98-source/'+name if name in ('repair_contract.py','repair_owner.py','repair_coordinator.py','repair_seal.py','repair_preflight.py','fixture_relocation.py','run_checks_repair98.py') else name)
 b=p.read_bytes();assert len(b)==item['bytes'] and hashlib.sha256(b).hexdigest()==item['sha256'];return b
class SyntheticDag:
 def __init__(self):
  self.contract_raw=fixture_bytes('repair_contract.py');assert hashlib.sha256(self.contract_raw).hexdigest()==dependency.CONTRACT_SHA
  self.ns={'__name__':'synthetic_authenticated98'};exec(compile(self.contract_raw,'authenticated6eacc fixture','exec'),self.ns)
  n=self.ns;self.canonical=n['canonical'];self.sha=n['sha'];self.nodes={};self.raw={}
  self.sources={name:fixture_bytes(name) for name in n['SOURCE_FILES']};self.legacy=fixture_bytes('prepare_release291d.py')
  self.boot='00000000-1111-4222-8333-444444444444';self.ops='1'*40
  self.runtime={'backend/venv/bin/python':'a'*64,'backend/__pycache__/synthetic.pyc':'b'*64};self.browser={'synthetic-browser-file':'c'*64}
  self.live_runtime=copy.deepcopy(self.runtime);self.live_browser=copy.deepcopy(self.browser)
  self.t0='2020-01-01T00:00:00+00:00';self.end='2020-01-01T00:55:00+00:00';self.completed='2020-01-01T00:13:00+00:00'
  def closed(name,values):return {'schema':n['SCHEMAS'][name][0],'attempt_id':n['ATTEMPT_ID'],**values}
  self.closed=closed
  self.nodes['old-failure-chain.json']={'schema':'SYNTHETIC-old-host-evidence','not_real':True}
  self.nodes['runtime-before.json']=copy.deepcopy(self.runtime);self.nodes['runtime-manifest.json']=copy.deepcopy(self.runtime)
  r=self.sha(self.canonical(self.runtime));b=self.sha(self.canonical(self.browser))
  self.nodes['source-manifest.json']={'schema':n['SCHEMAS']['source-manifest.json'][0],'repair_ops_commit':self.ops,'files':{}}
  self.nodes['full-preflight.json']=closed('full-preflight.json',{'observed_at':self.t0,'boot_id':self.boot,'baseline':n['BASE'],'previous':n['PREVIOUS'],'live_build':'20261017.96','authority':'postgres-r2-authoritative','source_archive_sha256':'67b392f0bd1c66b3391aa6e4c7f18b90b0a17fe7da72a4b5c32e0da594afb327','source_diff_sha256':'ad8f04dbf0afeeabb9ec1d3557cc5e709b4ebc0488490424950df83843beda12','runtime_manifest_sha256':r,'browser_manifest_sha256':b,'source_manifest_sha256':'','old_failure_chain_sha256':'','capacity':{'available_bytes':10**10,'available_inodes':100000,'memory_available':2**30},'units':{unit:{'active_running':True,'main_pid':2000010+index} for index,unit in enumerate(('careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service'))},'locks_clear':True,'result':'pass'})
  self.nodes['repair-plan.json']=closed('repair-plan.json',{'release':n['REV'],'build':'20261026.98','baseline':n['BASE'],'previous':n['PREVIOUS'],'repair_ops_commit':self.ops,'source_manifest_sha256':'','old_failure_chain_sha256':'','full_live_preflight_sha256':'','retained_runtime_manifest_sha256':r,'source_archive_sha256':self.nodes['full-preflight.json']['source_archive_sha256'],'source_diff_sha256':self.nodes['full-preflight.json']['source_diff_sha256'],'decision_at':self.t0,'expires_at':self.end})
  self.nodes['repair-decision.json']=closed('repair-decision.json',{'decision_at':self.t0,'expires_at':self.end,'repair_plan_sha256':'','source_manifest_sha256':'','full_live_preflight_sha256':''})
  self.nodes['owner.json']=closed('owner.json',{'pid':2000001,'start_ticks':'11001','boot_id':self.boot,'repair_plan_sha256':'','repair_decision_sha256':'','started_at':'2020-01-01T00:01:00+00:00'})
  self.nodes['coordinator.json']=closed('coordinator.json',{'pid':2000002,'start_ticks':'11002','boot_id':self.boot,'owner_pid':2000001,'started_at':'2020-01-01T00:01:30+00:00'})
  self.nodes['child-spawn.json']=closed('child-spawn.json',{'pid':2000002,'start_ticks':'11002','boot_id':self.boot,'owner_pid':2000001,'repair_plan_sha256':'','repair_decision_sha256':'','source_manifest_sha256':''})
  self.nodes['unit-intent.json']=closed('unit-intent.json',{'unit':n['UNIT'],'argv':[],'sandbox_properties':[],'runner_sha256':'','qualifier_sha256':'','repair_plan_sha256':'','repair_decision_sha256':'','source_manifest_sha256':'','boot_id':self.boot,'coordinator_pid':2000002,'coordinator_start_ticks':'11002','created_at':'2020-01-01T00:02:00+00:00','description_binding':'sha256-of-this-record'})
  self.nodes['test-client.json']=closed('test-client.json',{'pid':2000003,'start_ticks':'11003','boot_id':self.boot,'unit':n['UNIT'],'unit_intent_sha256':''})
  cg={'path':'/system.slice/'+n['UNIT']+'.service','exists':False,'processes':0,'populated':0}
  state={'LoadState':'loaded','ActiveState':'active','SubState':'exited','MainPID':'0','ControlPID':'0','Job':'','Result':'success','ExecMainStatus':'0','InvocationID':'a'*32,'Description':''}
  self.unit_absent={'LoadState':'not-found','MainPID':'0','ControlPID':'0','Job':'','InvocationID':''}
  self.nodes['unit-terminal.json']=closed('unit-terminal.json',{'unit':n['UNIT'],'execution':{'unit':n['UNIT'],'state':state,'invocation_id':'a'*32,'cgroup':copy.deepcopy(cg)},'after':copy.deepcopy(self.unit_absent),'cgroup_after':copy.deepcopy(cg),'client_pid':2000003,'client_start_ticks':'11003','client_exit':0,'boot_id':self.boot,'observed_at':'2020-01-01T00:07:00+00:00','unit_intent_sha256':'','test_client_sha256':''})
  q={'schema':'careertrellis.exact-fixture-relocation/v1','test_id':'test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables','calls':1,'constructor_returned':True,'method_returned':True,'binding_restored':True,'analyzer_returncodes':[0,1],'serial_suite_verified':True,'runtime_code_verified':True,'fixture_ancestors_verified':True,'module_sha256':'341e728a03f4f76bfa04c64aec612fae9fd87dc831266e547d9f42c7f0a42ab4','method_source_sha256':'584aec03af3f6347fbce3f10f56b449deeea0925f3365e6f28a5d3cfcbd85cac','method_ast_sha256':'ad2abbb01e5d212e3045b604105c2dc788281d56d64735887a827caad841af73','qualification':'one TemporaryDirectory dir relocated; unchanged body and real positive/negative analyzer calls'}
  self.nodes['repair-checks.json']={'schema':'careertrellis.preparation98-repair-checks/v1','repair_ops_commit':self.ops,'runner_sha256':'','qualifier_sha256':'','old_failure_chain_sha256':'','retained_runtime_manifest_sha256':r,'qualification':'exact-one fixture-root relocation; original app/test bytes unchanged','revision':n['REV'],'build':'20261026.98','checks':{k:True for k in n['CHECKS']},'details':{'backend':{'tests':899,'failures':0,'errors':0,'fixture_qualification':q}},'started_utc':'2020-01-01T00:03:00+00:00','ended_utc':'2020-01-01T00:06:00+00:00','network_namespace_loopback_only':True,'provider_requests':0}
  self.nodes['candidate.json']=closed('candidate.json',{'release':n['REV'],'build':'20261026.98','repair_ops_commit':self.ops,'repair_plan_sha256':'','repair_decision_sha256':'','source_manifest_sha256':'','old_failure_chain_sha256':'','full_live_preflight_sha256':'','checks_sha256':'','unit_terminal_sha256':'','runtime_before_sha256':r,'runtime_after_sha256':r,'browser_before_sha256':b,'browser_after_sha256':b,'completed_utc':'2020-01-01T00:10:00+00:00','result':'candidate_only_not_accepted'})
  captures={name:{'retained_bytes':0,'discarded_bytes':0,'eof':True,'error':False} for name in ('stdout','stderr')}
  self.nodes['owner-terminal.json']=closed('owner-terminal.json',{'owner':{},'coordinator':{},'coordinator_exit':0,'boot_id':self.boot,'repair_plan_sha256':'','repair_decision_sha256':'','candidate_sha256':'','checks_sha256':'','unit_terminal_sha256':'','runtime_after_sha256':r,'source_manifest_sha256':'','captures':captures,'ended_at':'2020-01-01T00:11:00+00:00','owner_self_exit_claim':False,'child_spawn_sha256':''})
  self.nodes['postterminal-owner-proof.json']=closed('postterminal-owner-proof.json',{'observed_at':'2020-01-01T00:12:00+00:00','boot_id':self.boot,'owner_terminal_sha256':'','candidate_sha256':'','owner_state':'absent','coordinator_state':'absent','unit':copy.deepcopy(self.unit_absent),'cgroup_after':copy.deepcopy(cg),'runtime_manifest_sha256':r,'browser_manifest_sha256':b,'original_deadline':self.end,'source_manifest_sha256':''})
  self.nodes['accepted.json']={}
  self.ns['read']=self.read;self.ns['run']=self.git;self.ns['boot']=lambda:self.boot
  self.ns['birth']=lambda pid:(_ for _ in ()).throw(FileNotFoundError('synthetic absent original'))
  self.ns['unit_state']=lambda unit:copy.deepcopy(self.unit_absent);self.ns['cgroup_state']=lambda *a:copy.deepcopy(cg)
  self.ns['observe_retained']=self.host_observation
  self.refresh(initial=True)
 def read(self,path,*args):
  name=path.name
  if name=='baseline-provenance.json':raise AssertionError('Private live-admission inventory is excluded from portable historical tests')
  if name in self.sources:return self.sources[name]
  return self.raw[name]
 def git(self,args,*a,**k):
  assert args[:4]==['git','-C',self.ns['REPOSITORY'],'cat-file'] and args[4]=='blob'
  ref=args[5]
  if ref.startswith(self.ns['OLD_OPS']+':'):return self.legacy
  name=ref.rsplit('/',1)[1];return self.sources[name]
 def host_observation(self,*args,**kwargs):
  return {'runtime':copy.deepcopy(self.live_runtime),'browser':copy.deepcopy(self.live_browser),'proof':{'boot_id':self.boot}}
 def emit(self,name):self.raw[name]=self.canonical(self.nodes[name]);return self.sha(self.raw[name])
 def refresh(self,initial=False):
  n=self.ns;nodes=self.nodes
  for name,data in self.sources.items():nodes['source-manifest.json']['files'][name]={'bytes':len(data),'sha256':self.sha(data)}
  m=self.emit('source-manifest.json');failure=self.emit('old-failure-chain.json')
  pre=nodes['full-preflight.json'];pre['source_manifest_sha256']=m;pre['old_failure_chain_sha256']=failure;pf=self.emit('full-preflight.json')
  p=nodes['repair-plan.json'];p.update(source_manifest_sha256=m,old_failure_chain_sha256=failure,full_live_preflight_sha256=pf);ps=self.emit('repair-plan.json')
  d=nodes['repair-decision.json'];d.update(repair_plan_sha256=ps,source_manifest_sha256=m,full_live_preflight_sha256=pf);ds=self.emit('repair-decision.json')
  nodes['owner.json'].update(repair_plan_sha256=ps,repair_decision_sha256=ds);self.emit('owner.json');self.emit('coordinator.json')
  nodes['child-spawn.json'].update(repair_plan_sha256=ps,repair_decision_sha256=ds,source_manifest_sha256=m);spawn=self.emit('child-spawn.json')
  i=nodes['unit-intent.json'];i.update(repair_plan_sha256=ps,repair_decision_sha256=ds,source_manifest_sha256=m,runner_sha256=nodes['source-manifest.json']['files']['run_checks_repair98.py']['sha256'],qualifier_sha256=nodes['source-manifest.json']['files']['fixture_relocation.py']['sha256'])
  if initial:
   old=n['original_helpers']();i['argv']=n['expected_unit_command'](p,nodes['source-manifest.json'],old);i['sandbox_properties']=old['sandbox_properties'](n['RELEASE'],n['WORK'],old['BROWSER'])
  ih=self.emit('unit-intent.json');nodes['test-client.json']['unit_intent_sha256']=ih;client=self.emit('test-client.json')
  u=nodes['unit-terminal.json'];u.update(unit_intent_sha256=ih,test_client_sha256=client);u['execution']['state']['Description']='careertrellis-repair98:'+ih;uh=self.emit('unit-terminal.json')
  k=nodes['repair-checks.json'];k.update(runner_sha256=i['runner_sha256'],qualifier_sha256=i['qualifier_sha256'],old_failure_chain_sha256=failure);kh=self.emit('repair-checks.json')
  before=self.emit('runtime-before.json');after=self.emit('runtime-manifest.json')
  c=nodes['candidate.json'];c.update(repair_plan_sha256=ps,repair_decision_sha256=ds,source_manifest_sha256=m,old_failure_chain_sha256=failure,full_live_preflight_sha256=pf,checks_sha256=kh,unit_terminal_sha256=uh,runtime_before_sha256=before,runtime_after_sha256=after);ch=self.emit('candidate.json')
  o=nodes['owner-terminal.json'];o.update(owner=copy.deepcopy(nodes['owner.json']),coordinator=copy.deepcopy(nodes['coordinator.json']),repair_plan_sha256=ps,repair_decision_sha256=ds,candidate_sha256=ch,checks_sha256=kh,unit_terminal_sha256=uh,runtime_after_sha256=after,source_manifest_sha256=m,child_spawn_sha256=spawn);oh=self.emit('owner-terminal.json')
  post=nodes['postterminal-owner-proof.json'];post.update(owner_terminal_sha256=oh,candidate_sha256=ch,source_manifest_sha256=m);qh=self.emit('postterminal-owner-proof.json')
  if initial:
   nodes['accepted.json']={'schema':'careertrellis.preparation98-repair-acceptance/v1','result':'qualified_prepared_inactive','release':n['REV'],'build':'20261026.98','baseline':n['BASE'],'previous':n['PREVIOUS'],'repair_attempt_id':n['ATTEMPT_ID'],'repair_ops_commit':self.ops,'source_manifest_sha256':m,'repair_plan_sha256':ps,'repair_decision_sha256':ds,'candidate_sha256':ch,'source_archive_sha256':p['source_archive_sha256'],'source_diff_sha256':p['source_diff_sha256'],'runner_sha256':i['runner_sha256'],'qualifier_sha256':i['qualifier_sha256'],'old_failure_chain_sha256':failure,'full_live_preflight_sha256':pf,'retained_runtime_before_sha256':before,'retained_runtime_after_sha256':after,'runtime_manifest':str(n['DOMAIN']/'runtime-manifest.json'),'runtime_manifest_sha256':after,'fixture_qualification':copy.deepcopy(k['details']['backend']['fixture_qualification']),'checks':copy.deepcopy(k['checks']),'checks_sha256':kh,'unit_terminal_sha256':uh,'owner_terminal_sha256':oh,'postterminal_proof_sha256':qh,'completed_utc':self.completed,'activated':False}
  a=nodes['accepted.json'];a.update(source_manifest_sha256=m,repair_plan_sha256=ps,repair_decision_sha256=ds,candidate_sha256=ch,checks_sha256=kh,unit_terminal_sha256=uh,owner_terminal_sha256=oh,postterminal_proof_sha256=qh,retained_runtime_before_sha256=before,retained_runtime_after_sha256=after,runtime_manifest_sha256=after,old_failure_chain_sha256=failure,full_live_preflight_sha256=pf)
  self.expected=self.emit('accepted.json')
 def authenticate(self):return dependency.authenticate(self.ns,self.expected,self.contract_raw)
 def mutate_occurrence(self,node,path,value):
  """One exact serialized occurrence; ONLY digest ancestors are relinked.
  Duplicate semantic claims are NOT recopied from the mutated occurrence.
  """
  target=(node,tuple(path));current=self.nodes[node]
  for key in path[:-1]:current=current[key]
  current[path[-1]]=value
  def put(name,where,new):
   where=tuple(where)
   if (name,where)==target:return
   item=self.nodes[name]
   for key in where[:-1]:item=item[key]
   item[where[-1]]=new
  def refs(h,items):
   for name,where in items:put(name,(where,) if isinstance(where,str) else where,h)
  m=self.emit('source-manifest.json');refs(m,[(n,k) for n,k in [('full-preflight.json','source_manifest_sha256'),('repair-plan.json','source_manifest_sha256'),('repair-decision.json','source_manifest_sha256'),('child-spawn.json','source_manifest_sha256'),('unit-intent.json','source_manifest_sha256'),('candidate.json','source_manifest_sha256'),('owner-terminal.json','source_manifest_sha256'),('postterminal-owner-proof.json','source_manifest_sha256'),('accepted.json','source_manifest_sha256')]])
  old=self.emit('old-failure-chain.json');refs(old,[(n,'old_failure_chain_sha256') for n in ('full-preflight.json','repair-plan.json','repair-checks.json','candidate.json','accepted.json')])
  pf=self.emit('full-preflight.json');refs(pf,[(n,'full_live_preflight_sha256') for n in ('repair-plan.json','repair-decision.json','candidate.json','accepted.json')])
  p=self.emit('repair-plan.json');refs(p,[(n,'repair_plan_sha256') for n in ('repair-decision.json','owner.json','child-spawn.json','unit-intent.json','candidate.json','owner-terminal.json','accepted.json')]+[('owner-terminal.json',('owner','repair_plan_sha256'))])
  d=self.emit('repair-decision.json');refs(d,[(n,'repair_decision_sha256') for n in ('owner.json','child-spawn.json','unit-intent.json','candidate.json','owner-terminal.json','accepted.json')]+[('owner-terminal.json',('owner','repair_decision_sha256'))])
  self.emit('owner.json');self.emit('coordinator.json')
  spawn=self.emit('child-spawn.json');refs(spawn,[('owner-terminal.json','child_spawn_sha256')])
  intent=self.emit('unit-intent.json');refs(intent,[('test-client.json','unit_intent_sha256'),('unit-terminal.json','unit_intent_sha256')])
  put('unit-terminal.json',('execution','state','Description'),'careertrellis-repair98:'+intent)
  client=self.emit('test-client.json');refs(client,[('unit-terminal.json','test_client_sha256')])
  unit=self.emit('unit-terminal.json');refs(unit,[(n,'unit_terminal_sha256') for n in ('candidate.json','owner-terminal.json','accepted.json')])
  checks=self.emit('repair-checks.json');refs(checks,[(n,'checks_sha256') for n in ('candidate.json','owner-terminal.json','accepted.json')])
  before=self.emit('runtime-before.json');refs(before,[('candidate.json','runtime_before_sha256'),('accepted.json','retained_runtime_before_sha256')])
  after=self.emit('runtime-manifest.json');refs(after,[('candidate.json','runtime_after_sha256'),('owner-terminal.json','runtime_after_sha256'),('accepted.json','retained_runtime_after_sha256'),('accepted.json','runtime_manifest_sha256')])
  c=self.emit('candidate.json');refs(c,[(n,'candidate_sha256') for n in ('owner-terminal.json','postterminal-owner-proof.json','accepted.json')])
  owner=self.emit('owner-terminal.json');refs(owner,[('postterminal-owner-proof.json','owner_terminal_sha256'),('accepted.json','owner_terminal_sha256')])
  proof=self.emit('postterminal-owner-proof.json');refs(proof,[('accepted.json','postterminal_proof_sha256')])
  self.expected=self.emit('accepted.json')
  serialized=self.ns['decode'](self.raw[node])
  for key in path:serialized=serialized[key]
  assert serialized==value,('mutated occurrence was repaired',node,path)
  self.mutated_occurrence={'node':node,'path':list(path),'serialized_value':serialized}
