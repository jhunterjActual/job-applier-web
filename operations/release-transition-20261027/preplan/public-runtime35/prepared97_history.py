"""PURE qualified97 historical byte-DAG consumer; no live IO or readiness authority.
Gate authenticates protected snapshots/public Git origin. This module verifies
closed records, raw hashes and original historical meaning only.
"""
import ast,hashlib,json,re
from functools import lru_cache
from datetime import datetime,timedelta
from pathlib import PurePosixPath
import prepared97_specs as specs
import prepared97_records as records
import prepared97_checks as checks97
import historical98_controls
import prepared98_history as history98
import prepared_types as adapter98

APPROVED_OPS='a62e3f1269df9390d075a263385dc024b2623d6a'
PUBLISHED_PRODUCER_REGISTRY_SHA='7e28cdde03df61fad511d03fab77235fc458b22df222edfb0d78069fc2f8b5cf'
APPROVED_CONSUMER_REGISTRY_SHA='90c3beced0522633947c39f398ae21254de7f8990e336738bac36460f6b78e7f'
OPS98='ae4e5171b9f0c874721cce8db5170a5815d59e8e'
REV='bc68ea0dc253425a44e268452130becdf35d764e'
BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS='4095417909a8b13ad252be9420e422af3662bef5'
PREFIX='operations/release-transition-20261027/corrected97/'
STAGE='/opt/careertrellis/staging/preparation97-corrected-source-20261027/corrected97-fixture-g1'
UNIT='careertrellis-preparation97-corrected-fixture-g1-20261027'
DEPLOY_SHA='1fe2a6c2527e46725a16f45fa55c4c1b7304f5740afe1cace34cbe165640da98'
LEGACY_SHA='a0d3db047dda8062969ba7005054ab147dd2ccae6efab5a9b4789b2137b852b3'
CONTRACT98_SHA='6eacc5f98edd31dfecf209f8e0692ab5ec993396fde6990e42e547cde598ce8f'
SOURCE13={'qualified_specs.py','materialize97.py','accepted98_dependency.py','source_boundary.py','qualified_route.py','admission97.py','native_assertions97.py','run_checks_qualified97.py','native97_unit.py','checks97.py','qualified97_records.py','qualified_entry.py','historical98_controls.py'}
BUNDLE22={'accepted.json','postterminal-proof.json','owner-terminal.json','owner.json','coordinator.json','child-spawn.json','coordinator-spawn-intent.json','intent.json','materialization-intent.json','materialization-child.json','materialization-proof.json','runtime-before.json','runtime-manifest.json','unit-intent.json','unit-client.json','unit-terminal.json','checks.json','candidate.json','plan.json','decision.json','source-manifest.json','preflight.json'}
BUNDLE98={'accepted.json','postterminal-owner-proof.json','owner-terminal.json','owner.json','coordinator.json','child-spawn.json','unit-intent.json','test-client.json','unit-terminal.json','repair-checks.json','candidate.json','runtime-before.json','runtime-manifest.json','repair-plan.json','repair-decision.json','source-manifest.json','full-preflight.json','old-failure-chain.json'}
SOURCE7={'repair_contract.py','repair_owner.py','repair_coordinator.py','repair_seal.py','repair_preflight.py','fixture_relocation.py','run_checks_repair98.py'}
BINDING_KEYS={'source_kind','revision','build','receipt_sha256','ops_commit','registry_sha256','accepted98_receipt_sha256'}
REGISTRY_KEYS={'schema','source_kind','revision','build','domain','attempt','ops_commit','source_git_prefix','source_files','deploy_sha256','legacy_sha256','accepted98_ops_commit','accepted98_registry_sha256'}
class Refused(RuntimeError):pass
def need(value):
 if not value:raise Refused('QUALIFIED97_HISTORY_REFUSED')
def sha(raw):
 need(type(raw) is bytes);return hashlib.sha256(raw).hexdigest()
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def pairs(items):
 out={}
 for k,v in items:need(k not in out);out[k]=v
 return out
def decode(raw):
 need(type(raw) is bytes and 0<len(raw)<=16*1024**2)
 return json.loads(raw.decode('utf8'),object_pairs_hook=pairs,parse_constant=lambda value:need(False))
def digest(v):need(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) and v!='0'*64)
def stamp(v):
 need(type(v) is str);t=datetime.fromisoformat(v.replace('Z','+00:00'));need(t.tzinfo is not None);return t
def byte_map(value,names):
 need(type(value) is dict and set(value)==names and all(type(k) is str and type(v) is bytes for k,v in value.items()))
 need(sum(len(v) for v in value.values())<=128*1024**2);return dict(value)
def closed(v,keys,schema=None):
 need(type(v) is dict and set(v)==keys)
 if schema is not None:need(v['schema']==schema)
 return v

def _registry(binding,registry_raw,sources):
 closed(binding,BINDING_KEYS);need(binding['source_kind']=='corrected-fixture97-new/v1' and binding['revision']==REV and binding['build']=='20261026.97' and binding['ops_commit']==APPROVED_OPS)
 for k in ('receipt_sha256','registry_sha256','accepted98_receipt_sha256'):digest(binding[k])
 need(sha(registry_raw)==binding['registry_sha256']==APPROVED_CONSUMER_REGISTRY_SHA);r=closed(decode(registry_raw),REGISTRY_KEYS,'careertrellis.prepared97-consumer-registry/v1')
 need(r['source_kind']==binding['source_kind'] and r['revision']==REV and r['build']==binding['build'] and r['ops_commit']==binding['ops_commit']==APPROVED_OPS
  and r['domain']==records.DOMAIN and r['attempt']==records.ATTEMPT and r['source_git_prefix']==PREFIX and r['accepted98_ops_commit']==OPS98
  and r['accepted98_registry_sha256']==history98.REGISTRY_SHA and r['deploy_sha256']==DEPLOY_SHA and r['legacy_sha256']==LEGACY_SHA)
 need(type(r['source_files']) is dict and set(r['source_files'])==SOURCE13)
 for name,meta in r['source_files'].items():
  closed(meta,{'bytes','sha256'});digest(meta['sha256']);raw=sources['qualified97/'+name]
  need(type(meta['bytes']) is int and 0<meta['bytes']<=1048576 and len(raw)==meta['bytes'] and sha(raw)==meta['sha256'])
 need(sha(sources['target97/deploy-release.sh'])==DEPLOY_SHA and sha(sources['preparation291d/prepare_release.py'])==LEGACY_SHA
  and sha(sources['repair98/registry.snapshot.json'])==history98.REGISTRY_SHA)
 return r

def inspect_history(bundle,binding,source_bytes,accepted98_bundle,accepted98_source_bytes,registry_raw):
 """Fixed snapshots in; historical runtime map/metadata out. No authority token."""
 try:return _inspect_history(bundle,binding,source_bytes,accepted98_bundle,accepted98_source_bytes,registry_raw)
 except (KeyError,TypeError,ValueError,IndexError,AssertionError,RuntimeError,SyntaxError):raise Refused('QUALIFIED97_HISTORY_SHAPE_REFUSED') from None

def _inspect_history(bundle,binding,source_bytes,accepted98_bundle,accepted98_source_bytes,registry_raw):
 bundle=byte_map(bundle,BUNDLE22);accepted98_bundle=byte_map(accepted98_bundle,BUNDLE98)
 sources=byte_map(source_bytes,{'qualified97/'+n for n in SOURCE13}|{'target97/deploy-release.sh','preparation291d/prepare_release.py','repair98/registry.snapshot.json','target97/backend/test_p0_regressions.py','target97/scripts/value_evaluation/test_campaign_transport.py'})
 source98=byte_map(accepted98_source_bytes,SOURCE7);registry=_registry(binding,registry_raw,sources)
 dependency98=_source_and_history98(accepted98_bundle,binding,source98,registry,sources)
 return _inspect97(_record_values(bundle),bundle,binding,registry,sources,dependency98)

def _record_values(bundle):
 out={}
 custom={
  'plan.json':({'schema','kind','revision','build','source_manifest_sha256','archive_sha256','provenance_sha256','accepted98_sha256','full_preflight_sha256','decision_at','expires_at'},'careertrellis.corrected97-materialization-plan/v1'),
  'decision.json':({'schema','kind','plan_sha256','source_manifest_sha256','boot_id','decision_at','expires_at'},'careertrellis.corrected97-decision/v1'),
  'source-manifest.json':({'schema','kind','ops_commit','files'},'careertrellis.corrected97-source-manifest/v1'),
  'intent.json':({'kind','plan_sha256','decision_sha256','source_manifest_sha256','owner_pid','owner_birth','boot_id','no_replay'},None),
  'coordinator-spawn-intent.json':({'argv','plan_sha256','decision_sha256','boot_id','owner_pid','owner_birth'},None),
  'materialization-child.json':({'pid','start_ticks','argv'},None)}
 for name,raw in bundle.items():
  v=decode(raw);need(type(v) is dict)
  if name in records.SCHEMAS:records.closed(name,v)
  elif name in custom:closed(v,*custom[name])
  elif name in ('runtime-before.json','runtime-manifest.json'):_runtime(v)
  else:need(name=='checks.json') # Exact closed shape is shared checks97.validate.
  out[name]=v
 return out

def _runtime(value):
 need(type(value) is dict and 0<len(value)<=50000)
 for path,h in value.items():
  need(type(path) is str and len(path)<=1024 and '\\' not in path and not path.startswith('/') and '..' not in path.split('/')
   and (path.startswith('backend/venv/') or (path.startswith('backend/') and '/__pycache__/' in path and path.endswith('.pyc'))))
  digest(h)
 return value

def _links(values,bundle,accepted98_sha):
 links={'plan_sha256':'plan.json','decision_sha256':'decision.json','source_manifest_sha256':'source-manifest.json',
  'full_preflight_sha256':'preflight.json','candidate_sha256':'candidate.json','materialization_proof_sha256':'materialization-proof.json',
  'checks_sha256':'checks.json','unit_terminal_sha256':'unit-terminal.json','owner_terminal_sha256':'owner-terminal.json',
  'postterminal_proof_sha256':'postterminal-proof.json','child_spawn_sha256':'child-spawn.json','intent_sha256':'unit-intent.json',
  'unit_intent_sha256':'unit-intent.json','client_sha256':'unit-client.json','runtime_manifest_sha256':'runtime-manifest.json',
  'runtime_after_sha256':'runtime-manifest.json','runtime_before_sha256':'runtime-before.json'}
 def visit(v):
  if type(v) is dict:
   for key,item in v.items():
    if key in links:need(item==sha(bundle[links[key]]))
    if key in ('accepted98_sha256','accepted98_receipt_sha256'):need(item==accepted98_sha)
    if key=='method_ast_sha256':need(type(item) is dict and item==checks97.NATIVE_METHOD_AST_SHA)
    elif key.endswith('_sha256'):digest(item)
    visit(item)
  elif type(v) is list:
   for item in v:visit(item)
 for name,v in values.items():
  if name not in ('runtime-before.json','runtime-manifest.json'):visit(v)

MODULES_FOR_AST='ec900a123e899e9bd07a2e4662c8d8551b894f62456c4f9ca4c3e91d6823dbb1'
class _CachedDeploy:
 def __init__(self,raw):self.raw=raw
 def read_text(self):return self.raw.decode('utf8')
class _ReleaseSource:
 def __init__(self,raw):self.raw=raw
 def __truediv__(self,name):
  need(type(name) is str and name=='deploy/scripts/deploy-release.sh');return _CachedDeploy(self.raw)
def corrected_source_contract(sources):
 return list(_corrected_source_contract(sources['target97/backend/test_p0_regressions.py'],sources['target97/scripts/value_evaluation/test_campaign_transport.py']))

@lru_cache(maxsize=4)
def _corrected_source_contract(p0,campaign):
 need(sha(p0)=='db1604118386f23c9039fea1f3ccbec933f9a4d4e40ed7b9d9f37f7cde67fe13' and sha(campaign)=='98f332330499a7678bc5189a1d685e6f45fd6c15d1a0bca76a87fe303013ee31')
 cls=next(n for n in ast.parse(p0).body if isinstance(n,ast.ClassDef) and n.name=='DependencyLockTests')
 for name,digest in checks97.NATIVE_METHOD_AST_SHA.items():
  node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name);need(sha(ast.dump(node,include_attributes=False).encode())==digest)
 systemd=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='test_backup_health_units_resolve_only_staged_executables')
 constructors=[n for n in ast.walk(systemd) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='TemporaryDirectory'];need(len(constructors)==1 and not any(k.arg=='dir' for k in constructors[0].keywords))
 cc=next(n for n in ast.parse(campaign).body if isinstance(n,ast.ClassDef) and n.name=='Tests');setup=next(n for n in cc.body if isinstance(n,ast.FunctionDef) and n.name=='setUp')
 need(not any(k.arg=='dir' for n in ast.walk(setup) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='TemporaryDirectory' for k in n.keywords))
 ids=['test_campaign_transport.Tests.'+n.name for n in cc.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test')];need(len(ids)==21 and len(set(ids))==21);return tuple(ids)

def _catalogs(sources):
 runner=sources['qualified97/run_checks_qualified97.py'];tree=ast.parse(runner)
 candidates=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='modules_for'];need(len(candidates)==1)
 fn=candidates[0];need(hashlib.sha256(ast.dump(fn,include_attributes=False).encode()).hexdigest()==MODULES_FOR_AST)
 # Only this exact reviewed pure AST runs, against one bounded cached deploy
 # facade. There is no filesystem callback, import primitive or ambient global.
 scope={'__builtins__':{'len':len,'all':all,'set':set,'list':list,'dict':dict},'need':need,'TARGET':REV}
 exec(compile(ast.Module(body=[fn],type_ignores=[]),'authenticated pure module-catalog AST','exec'),scope)
 backend=scope['modules_for'](_ReleaseSource(sources['target97/deploy-release.sh']),REV)
 catalogs={'backend':backend}
 for node in ast.walk(tree):
  if isinstance(node,ast.Assign) and len(node.targets)==1:
   target=node.targets[0]
   if isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and target.value.id=='details' and isinstance(target.slice,ast.Constant) and target.slice.value in ('summary','evaluation'):
    call=node.value;need(isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='run_suite' and len(call.args)>=2)
    values=ast.literal_eval(call.args[1]);need(type(values) is list and values and all(type(v) is str for v in values));need(target.slice.value not in catalogs);catalogs[target.slice.value]=values
 need(set(catalogs)=={'backend','summary','evaluation'});return catalogs


def _source_and_history98(bundle,binding,sources98,registry,sources):
 receipt=decode(bundle['accepted.json']);manifest=decode(bundle['source-manifest.json'])
 need(receipt['repair_ops_commit']==manifest['repair_ops_commit']==registry['accepted98_ops_commit']==OPS98)
 need(type(manifest['files']) is dict and set(manifest['files'])==SOURCE7)
 for name,meta in manifest['files'].items():
  closed(meta,{'bytes','sha256'});digest(meta['sha256']);need(type(meta['bytes']) is int and meta['bytes']==len(sources98[name]) and sha(sources98[name])==meta['sha256'])
 need(sha(sources98['repair_contract.py'])==CONTRACT98_SHA)
 app_plan={'prepared_receipt_kinds':dict(adapter98.KINDS),'prepared_receipts':{adapter98.TARGET:binding['receipt_sha256'],adapter98.CORRECTION:binding['accepted98_receipt_sha256']},
  'source_diffs':{adapter98.CORRECTION:'ad8f04dbf0afeeabb9ec1d3557cc5e709b4ebc0488490424950df83843beda12'}}
 result=history98.inspect_dag(bundle,app_plan,sources['repair98/registry.snapshot.json'])
 need(result['historical_dag_inspected'] is True and result['authorizes_preparation'] is False)
 return {'receipt_sha256':sha(bundle['accepted.json']),'receipt':receipt,'boot_id':decode(bundle['full-preflight.json'])['boot_id'],'runtime_map':decode(bundle['runtime-manifest.json']),
  'original_actors':[(decode(bundle[name])['pid'],int(decode(bundle[name])['start_ticks'])) for name in ('owner.json','coordinator.json','test-client.json')],
  'original_failure_requirements':decode(bundle['old-failure-chain.json']),'requires_original_git_failure_and_current_runtime_guards':True}

def _properties97(spec):
 release='/opt/careertrellis/staging/releases/'+REV
 return [p.replace(history98.RELEASE,release).replace(history98.WORK,spec['work']) for p in history98.approved_properties()]
def _argv97(spec,plan,manifest,runtime_sha):
 release='/opt/careertrellis/staging/releases/'+REV
 props=_properties97(spec)
 return ['systemd-run','--unit='+UNIT,'--wait','--pipe','--collect',*props,release+'/backend/venv/bin/python','-I','-B',spec['code']+'/run_checks_qualified97.py',
  '--release',release,'--revision',REV,'--work',spec['work'],'--repair-ops-commit',APPROVED_OPS,
  '--approved-runner-sha256',manifest['files']['run_checks_qualified97.py']['sha256'],'--accepted98-receipt-sha256',plan['accepted98_sha256'],
  '--retained-runtime-manifest-sha256',runtime_sha]

def _unit97(unit,post,intent_sha):
 import copy
 need(unit['unit']==unit['execution']['unit']==UNIT and unit['execution']['state']['Description']=='careertrellis-qualified97:'+intent_sha)
 path='/system.slice/'+UNIT+'.service';old='/system.slice/'+history98.UNIT+'.service'
 for group in (unit['execution']['cgroup'],unit['cgroup_after'],post['cgroup_after']):need(group['path']==path)
 for state in (unit['execution']['state'],unit['after'],post['unit']):need(state.get('ControlGroup','') in ('',path))
 need(post['cgroup_after']['exists'] is False and not post['unit'].get('InvocationID'))
 if unit['after'].get('LoadState')=='not-found':need(not unit['after'].get('InvocationID'))
 u=copy.deepcopy(unit);p=copy.deepcopy(post)
 u['unit']=u['execution']['unit']=history98.UNIT;u['client_start_ticks']=u['client_birth']
 u['execution']['state']['Description']='careertrellis-repair98:'+intent_sha
 for group in (u['execution']['cgroup'],u['cgroup_after'],p['cgroup_after']):group['path']=old
 for state in (u['execution']['state'],u['after'],p['unit']):
  if state.get('ControlGroup'):state['ControlGroup']=old
 # Reuse the exact existing PURE unit semantic inspector. This is an explicit
 # fixed-domain projection; no globals, live methods or policy are patched.
 history98.unit_history(u,p,intent_sha)

def _inspect97(v,bundle,binding,registry,sources,dependency98):
 spec=specs.exact_spec('corrected-fixture97-new/v1');a=v['accepted.json'];p=v['plan.json'];d=v['decision.json'];m=v['source-manifest.json'];pre=v['preflight.json']
 c=v['candidate.json'];o=v['owner.json'];co=v['coordinator.json'];spawn=v['child-spawn.json'];ot=v['owner-terminal.json'];post=v['postterminal-proof.json'];u=v['unit-terminal.json'];ui=v['unit-intent.json'];client=v['unit-client.json'];checks=v['checks.json'];mi=v['materialization-intent.json'];mp=v['materialization-proof.json']
 need(sha(bundle['accepted.json'])==binding['receipt_sha256']);_links(v,bundle,binding['accepted98_receipt_sha256'])
 for record in (a,p,d,m,pre,c,ot,post,ui,u):
  key='source_kind' if record is a else 'kind';need(record[key]=='corrected-fixture97-new/v1')
 need(a['result']=='corrected_fixtures_prepared_inactive' and a['activated'] is False and a['attempt_id']==records.ATTEMPT
  and a['revision']==p['revision']==c['revision']==mi['revision']==mp['revision']==REV
  and a['build']==p['build']==c['build']=='20261026.97' and a['baseline']==pre['baseline']==BASE and a['previous']==pre['previous']==PREVIOUS
  and a['ops_commit']==m['ops_commit']==c['ops_commit']==registry['ops_commit']==APPROVED_OPS
  and m['files']==registry['source_files'] and a['archive_sha256']==p['archive_sha256']==pre['archive_sha256']==spec['archive_sha256'])
 need(pre['result']=='pass' and pre['release_absent'] is True and pre['namespace_absent'] is True and pre['unit_absent'] is True
  and pre['live_build']=='20261017.96' and pre['provenance_sha256']==p['provenance_sha256'])
 capacity=pre['capacity'];closed(capacity,{'available_bytes','available_inodes','memory_available'})
 need(all(type(x) is int for x in capacity.values()) and capacity['available_bytes']>=5*1024**3 and capacity['available_inodes']>=4096 and capacity['memory_available']>=768*1024**2)
 start=stamp(p['decision_at']);deadline=stamp(p['expires_at']);observed=stamp(pre['observed_at'])
 need(start<deadline<=start+timedelta(minutes=55) and observed<=start<=observed+timedelta(minutes=5)
  and d['decision_at']==p['decision_at'] and d['expires_at']==p['expires_at'] and post['original_deadline']==p['expires_at'])
 need(bundle['runtime-before.json']==bundle['runtime-manifest.json'] and a['runtime_manifest']==records.DOMAIN+'/runtime-manifest.json')
 runtime_sha=sha(bundle['runtime-manifest.json']);runtime=v['runtime-manifest.json']
 need(mp['runtime_manifest_sha256']==c['runtime_before_sha256']==c['runtime_after_sha256']==ot['runtime_after_sha256']==a['runtime_before_sha256']==a['runtime_manifest_sha256']==post['runtime_manifest_sha256']==runtime_sha)
 need(c['browser_before_sha256']==c['browser_after_sha256']==pre['browser_manifest_sha256']==post['browser_manifest_sha256'])
 digest(pre['software_inventory_sha256'])
 boot=pre['boot_id'];need(boot==dependency98['boot_id']);need(type(boot) is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot))
 for record in (d,o,co,spawn,ui,client,u,ot,post,mi,mp,v['intent.json'],v['coordinator-spawn-intent.json']):need(record['boot_id']==boot)
 for identity in (o,co,spawn,client):need(type(identity['pid']) is int and identity['pid']>1 and type(identity['birth']) is str and identity['birth'].isdigit())
 old_pairs=set(dependency98['original_actors'])
 new_pairs=[(actor['pid'],int(actor['birth'])) for actor in (o,co,client)]
 material_child=v['materialization-child.json']
 need(type(material_child['pid']) is int and material_child['pid']>1 and type(material_child['start_ticks']) is str and material_child['start_ticks'].isdigit())
 new_pairs.append((material_child['pid'],int(material_child['start_ticks'])))
 need(len(set(new_pairs))==4) # Sequential PID reuse is valid only with a different birth.
 need(all(pair not in old_pairs for pair in new_pairs))
 need(len({o['pid'],co['pid'],client['pid']})==3 and co['owner_pid']==spawn['owner_pid']==o['pid']
  and spawn['pid']==co['pid'] and spawn['birth']==co['birth'] and ot['owner']==o and ot['coordinator']==co
  and ot['coordinator_exit']==0 and type(ot['coordinator_exit']) is int and ot['owner_self_exit_claim'] is False)
 intent=v['intent.json'];si=v['coordinator-spawn-intent.json']
 need(intent['kind']=='corrected-fixture97-new/v1' and intent['no_replay'] is True and intent['owner_pid']==si['owner_pid']==o['pid'] and intent['owner_birth']==si['owner_birth']==o['birth'])
 expected_coordinator=['/usr/bin/python3','-I','-S','-B',STAGE+'/qualified_entry.py','--role','coordinator','--ops-commit',APPROVED_OPS,
  '--approved-source-manifest-sha256',sha(bundle['source-manifest.json']),'--approved-plan-sha256',sha(bundle['plan.json']),'--approved-decision-sha256',sha(bundle['decision.json'])]
 need(si['argv']==expected_coordinator and ui['argv']==_argv97(spec,p,m,runtime_sha) and ui['properties']==_properties97(spec)
  and ui['coordinator_pid']==co['pid'] and ui['coordinator_birth']==co['birth'] and client['pid']==u['client_pid'] and client['birth']==u['client_birth'])
 _unit97(u,post,sha(bundle['unit-intent.json']))
 child=v['materialization-child.json'];need(type(child['pid']) is int and child['pid']>1 and child['pid'] not in (o['pid'],co['pid'])
  and type(child['start_ticks']) is str and child['start_ticks'].isdigit()
  and child['argv']==['/usr/bin/python3.12','-I','-S','-B','-m','venv','--without-pip','--copies','/opt/careertrellis/staging/releases/'+REV+'/backend/venv'])
 need(type(mi['peak_bytes']) is int and mi['peak_bytes']>0)
 catalogs=_catalogs(sources);checks97.validate(checks,spec,p,m,runtime_sha,catalogs)
 campaign_ids=corrected_source_contract(sources);evaluation=checks['details']['evaluation'];need(set(campaign_ids)<=set(evaluation['successful_ids']) and set(campaign_ids)=={v for v in evaluation['catalog_ids'] if v.startswith('test_campaign_transport.')} and not set(campaign_ids)&set(evaluation['skipped']))
 backend=checks['details']['backend'];need(set(backend['success_ids'])==history98.NATIVE_IDS and not(set(backend['skipped'])&history98.NATIVE_IDS))
 need(a['checks']==checks['checks'] and a['native_source_proof']==backend['native_source_proof'])
 startup=closed(checks['details']['startup'],{'api_build','dashboard_match','owned_process_stopped','exit_signal','log_sha256'})
 need(startup['api_build']=='20261026.97' and startup['dashboard_match'] is True and startup['owned_process_stopped'] is True and startup['exit_signal']=='SIGTERM');digest(startup['log_sha256'])
 closed(ot['captures'],{'stdout','stderr'})
 for cap in ot['captures'].values():
  closed(cap,{'retained_bytes','discarded_bytes','eof','error'});need(cap['eof'] is True and cap['error'] is False and type(cap['retained_bytes']) is int and 0<=cap['retained_bytes']<=1048576 and type(cap['discarded_bytes']) is int and cap['discarded_bytes']>=0)
 need(post['owner_state'] in ('absent','pid_reused_original_absent') and post['coordinator_state'] in ('absent','pid_reused_original_absent'))
 records.validate_chronology(p,o,co,mi,mp,ui,checks,u,c,stamp(a['completed_utc']),ot,post,a)
 need(stamp(dependency98['receipt']['completed_utc'])<=observed)
 return {'runtime_map':runtime,'historical':{'source_kind':'corrected-fixture97-new/v1','revision':REV,'build':'20261026.97','receipt_sha256':binding['receipt_sha256'],
  'ops_commit':APPROVED_OPS,'original_decision_at':p['decision_at'],'original_deadline':p['expires_at'],'completed_utc':a['completed_utc'],
  'runtime_manifest_sha256':runtime_sha,'source_manifest_sha256':sha(bundle['source-manifest.json']),'accepted98_receipt_sha256':dependency98['receipt_sha256'],
  'suite_counts':{k:checks['details'][k]['tests'] for k in ('backend','summary','evaluation')},
  'materialization_child_qualification':'Existing pid/start_ticks/argv record and source-controlled synchronous custody only; no invented parent/time/terminal record',
  'capacity_qualification':'Known producer minimum floors and original proof times checked; dynamic peak/inode recomputation requires original private provenance and remains an external guard',
  'requires_original_failure_guards':dependency98['original_failure_requirements'],'requires_current_source_runtime_equality':True,'authorizes_preparation':False}}
