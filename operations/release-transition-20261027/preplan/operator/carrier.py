"""Initial public-source carrier proposal; no private RPC or allocator."""
import ast,base64,hashlib,json,os,re,socket,stat,sys,time,types
from pathlib import Path,PurePosixPath
from datetime import datetime,timezone
ORDER=('native_io','preplan_history','native_runtime','native_pair','native_idle','public_version','native_ingress','preplan_program','policy_encoding','pure_plan_contract','assemble_plan','package_bootstrap','public_delivery','allocation_entry','private_channel','session_server')
PUBLIC_PATHS=('accepted_pair_validator.py','canonical_primitives.py','current_health_validator.py','db_audit.py','historical98_controls.py','native_backend.py','native_peer.py','one_shot_readiness/SOURCE_MANIFEST.json','one_shot_readiness/collector.py','one_shot_readiness/installed493-pins.json','one_shot_readiness/pair_boundary.py','one_shot_readiness/terminal493.py','one_shot_readiness/transaction04-pins.json','one_shot_readiness/transaction04.py','peer_server.py','policy_encoding.py','prepared97_checks.py','prepared97_context.py','prepared97_history.py','prepared97_reader.py','prepared97_sources.py','prepared97_records.py','prepared97_specs.py','prepared98_history.py','prepared_evidence.py','prepared_reader.py','prepared_registries.py','prepared_sources.py','prepared_types.py','protected_state.py','readiness_bridge.py','release_transaction.py','reviewed_probe.py','terminal_validation.py','web_coordinator.py')
FILES=frozenset(n+'.py' for n in ORDER)|{'packet_manifest.json','historical_packet_manifest.json','scanner-original-native_backend.py','scanner-original-canonical_primitives.py'}|frozenset('public-runtime35/'+n for n in PUBLIC_PATHS)
HOSTS={'web':'careertrellis-staging-web-1','db':'job-applier-db-staging-01'}
class Refused(RuntimeError):pass
def need(value):
 if not value:raise Refused('PUBLIC_SOURCE_CARRIER_REFUSED')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def pairs(rows):
 d={}
 for key,value in rows:need(key not in d);d[key]=value
 return d
def validate_cache(manifest_raw,approved_sha,caps):
 need(type(manifest_raw)is bytes and 0<len(manifest_raw)<=65536 and type(approved_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_sha) and sha(manifest_raw)==approved_sha)
 need(type(caps)is dict)
 for name,raw in dict.items(caps):need(type(name)is str and type(raw)is bytes)
 need(set(caps)==FILES)
 m=json.loads(manifest_raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False));need(type(m)is dict and set(m)=={'schema','program_commit','prefix','runtime','files'} and m['schema']=='careertrellis.preplan-source-package/v1' and m['prefix']=='operations/release-transition-20261027/preplan/' and type(m['program_commit'])is str and re.fullmatch('[0-9a-f]{40}',m['program_commit']) and m['program_commit']!='0'*40 and type(m['runtime'])is dict and type(m['files'])is dict and set(m['files'])==FILES)
 total=0;cached={}
 for name,row in m['files'].items():
  need(type(row)is dict and set(row)=={'sha256','bytes'} and type(row['sha256'])is str and re.fullmatch('[0-9a-f]{64}',row['sha256']) and type(row['bytes'])is int and 0<row['bytes']<=2097152)
  raw=caps[name];need(len(raw)==row['bytes'] and sha(raw)==row['sha256']);cached[name]=raw;total+=len(raw)
 need(total<=8*1024**2);return m,cached

def load_program(manifest_raw,approved_sha,caps):
 m,cache=validate_cache(manifest_raw,approved_sha,caps);need(not any(name in sys.modules for name in ORDER));modules={}
 for name in ORDER:
  module=types.ModuleType(name);sys.modules[name]=module;modules[name]=module
  exec(compile(cache[name+'.py'],'verified-public-preplan:'+name,'exec'),module.__dict__)
 return m,cache,modules

def selector(role,mode,approved_sha):
 need(type(role)is str and role in HOSTS and type(mode)is str and mode in ('preflight','stage','readback','stage-packet','readback-packet','export-plan','stage-plan','readback-plan') and type(approved_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_sha))
 need(mode!='export-plan' or role=='web')
 return 'careertrellis-preplan-public-'+mode+'-'+role+'-'+approved_sha

def local_preflight(role,expected_boot,manifest_sha,cache,modules):
    io=modules['native_io'];need(io.bootstrap(role)==expected_boot);modules['native_idle'].clear_kernel_locks(role)
    pointers=modules['public_version'].pointer_state() if role=='web' else None
    names=('careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service') if role=='web' else ('postgresql@18-main.service','wg-quick@wg0.service')
    states={}
    for name in names:
        raw=io.retained_run(['/usr/bin/systemctl','show',name,'--property=LoadState,ActiveState,SubState,MainPID,ControlPID,Job,NeedDaemonReload'],maximum=16384)
        value={}
        for line in raw.decode('ascii').splitlines():
            key,item=line.split('=',1);need(key not in value);value[key]=item
        need(set(value)=={'LoadState','ActiveState','SubState','MainPID','ControlPID','Job','NeedDaemonReload'} and value['LoadState']=='loaded' and value['ActiveState']=='active' and value['SubState'] in ('running','exited') and value['ControlPID']=='0' and value['Job']=='' and value['NeedDaemonReload']=='no')
        need(value['MainPID'].isdigit() and (int(value['MainPID'])>1 if value['SubState']=='running' else value['MainPID']=='0'));states[name]=value
    root=modules['package_bootstrap'].ROOT;io.parents(root/'source-stage-parent-check');fs=os.statvfs(root);need(fs.f_bavail*fs.f_frsize-sum(len(raw) for raw in cache.values())-65536>=5*1024**3 and fs.f_favail>=len(cache)+1024)
    stage=root/('preplan-source-'+manifest_sha);intent=modules['package_bootstrap'].RECEIPTS/('preplan-source-'+manifest_sha+'-'+role+'-intent.json');success=intent.with_name(intent.name.replace('-intent.json','-success.json'))
    need(not any(os.path.lexists(path) for path in (stage,intent,success)))
    need(io.boot()==expected_boot);modules['native_idle'].clear_kernel_locks(role)
    return {'pointers':pointers,'unit_count':len(states),'capacity_checked':True,'canonical_locks_unheld':True,'no_process_absence_claim':True}

def stage_binding(raw,approved_sha,profile,role,expected_boot):
    need(type(raw)is bytes and 0<len(raw)<=8192 and type(approved_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_sha) and sha(raw)==approved_sha)
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
    need(type(value)is dict and set(value)=={'schema','nonce','source_commit','boots','issued_at','phase','mode','release','build'} and value['schema']=='careertrellis.preplan-scheduler-binding/v1' and type(value['nonce'])is str and re.fullmatch('[0-9a-f]{64}',value['nonce']) and value['source_commit']==profile['source_commit'] and type(value['boots'])is dict and set(value['boots'])=={'web','db'} and all(type(v)is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',v) for v in value['boots'].values()) and value['boots'][role]==expected_boot and type(value['issued_at'])is str and value['phase']=='preflight' and value['mode']=='idle' and value['release']=='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4' and value['build']=='20261017.96')
    issued=datetime.fromisoformat(value['issued_at']);need(issued.tzinfo is not None and issued.utcoffset()==timezone.utc.utcoffset(issued))
    need(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')==raw)
    return value # Structural/historical only. Real current clock is checked remotely.

PLAN_KEYS=frozenset(('schema','base','previous','target','correction','builds','boots','old_policy_digests','new_policy_digests','packet_sha256','source_diffs','prepared_receipts','prepared_receipt_kinds','expires_at','readiness_manifest_sha256'))
def closed_plan(raw,approved_sha):
    need(type(raw)is bytes and 0<len(raw)<=65536 and type(approved_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_sha) and sha(raw)==approved_sha)
    plan=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False));need(type(plan)is dict and set(plan)==PLAN_KEYS and plan['schema']=='careertrellis.app-policy-release/v2' and json.dumps(plan,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()==raw);return plan

def validate_operation(mode,operation):
    need(type(mode)is str and mode in ('preflight','stage','readback','stage-packet','readback-packet','export-plan','stage-plan','readback-plan'))
    if mode in ('preflight','stage','readback'):need(operation is None);return None
    need(type(operation)is dict)
    for key,value in dict.items(operation):need(type(key)is str and type(value) in (str,bytes))
    expected={'stage-packet':{'fresh_binding_raw','fresh_binding_sha256'},'readback-packet':{'approved_packet_intent_sha256'},'export-plan':{'nonce','approved_plan_sha256','approved_decision_sha256'},'stage-plan':{'fresh_binding_raw','fresh_binding_sha256','plan_raw','approved_plan_sha256'},'readback-plan':{'approved_plan_sha256','approved_plan_intent_sha256'}}[mode]
    need(set(operation)==expected)
    for key,value in operation.items():
        if key.endswith('_raw'):need(type(value)is bytes and 0<len(value)<=(65536 if key=='plan_raw' else 8192))
        else:need(type(value)is str and re.fullmatch('[0-9a-f]{64}',value))
    if mode=='stage-plan':
        closed_plan(operation['plan_raw'],operation['approved_plan_sha256'])
    return dict(operation)

def completed_source(role,expected_boot,approved_sha,cache,modules,approved_binding_sha,approved_intent_sha):
    need(type(approved_intent_sha)is str and re.fullmatch('[0-9a-f]{64}',approved_intent_sha));io=modules['native_io'];package=modules['package_bootstrap'];intent=package.RECEIPTS/('preplan-source-'+approved_sha+'-'+role+'-intent.json');reader=io.ProtectedReader();ir=reader.read(intent,65536);record=io.decode(ir);need(io.sha(ir)==approved_intent_sha and record['boot_id']==expected_boot and record['binding_sha256']==approved_binding_sha)
    root,actual=package.load_staged(role,approved_sha);need(actual==cache and io.boot()==expected_boot);reader.stable();return io.sha(ir)

def public_operation(role,mode,expected_boot,approved_sha,manifest_raw,cache,source_binding,source_binding_sha,modules,operation):
    delivery=modules['public_delivery'];profile=json.loads(manifest_raw)['runtime'];io=modules['native_io']
    if mode in ('stage-packet','stage-plan'):
        need(operation['fresh_binding_sha256']!=source_binding_sha);binding=stage_binding(operation['fresh_binding_raw'],operation['fresh_binding_sha256'],profile,role,expected_boot);need(binding['nonce']!=source_binding['nonce'])
        if mode=='stage-packet':
            result=delivery.main(role,mode,manifest_raw,approved_sha,cache,binding)
            need(type(result)is dict and set(result)=={'result','packet_sha256','source_commit','files','intent_sha256'} and result['result']=='PUBLIC_PACKET_STAGED_ONLY' and result['files']==36)
        else:
            delivery.plan_bytes(operation['plan_raw'],operation['approved_plan_sha256'],profile,datetime.now(timezone.utc))
            result=delivery.main(role,mode,manifest_raw,approved_sha,cache,binding,payload=operation['plan_raw'],approved_plan_sha=operation['approved_plan_sha256'])
            need(type(result)is dict and set(result)=={'result','plan_sha256','packet_sha256','source_commit','intent_sha256'} and result['result']=='PUBLIC_PLAN_STAGED_NOT_DISPATCHED' and result['plan_sha256']==operation['approved_plan_sha256'])
        need(type(result['intent_sha256'])is str and re.fullmatch('[0-9a-f]{64}',result['intent_sha256']))
    elif mode=='readback-packet':
        result=delivery.readback_packet(role,manifest_raw,approved_sha,cache,operation['approved_packet_intent_sha256'])
        need(type(result)is dict and set(result)=={'result','packet_sha256','source_commit','files','intent_sha256','success_sha256','original_actor_absent','authorizes_dispatch'} and result['result']=='PUBLIC_PACKET_READBACK_ONLY' and result['intent_sha256']==operation['approved_packet_intent_sha256'] and result['files']==36 and result['original_actor_absent'] is True and result['authorizes_dispatch'] is False)
    elif mode=='readback-plan':
        result=delivery.readback_plan(role,manifest_raw,approved_sha,cache,operation['approved_plan_sha256'],operation['approved_plan_intent_sha256'])
        need(type(result)is dict and set(result)=={'result','plan_sha256','packet_sha256','source_commit','intent_sha256','success_sha256','original_actor_absent','authorizes_dispatch'} and result['result']=='PUBLIC_PLAN_READBACK_ONLY' and result['intent_sha256']==operation['approved_plan_intent_sha256'] and result['plan_sha256']==operation['approved_plan_sha256'] and result['original_actor_absent'] is True and result['authorizes_dispatch'] is False)
    else:
        need(mode=='export-plan' and role=='web')
        result=delivery.main(role,mode,manifest_raw,approved_sha,cache,None,nonce=operation['nonce'],approved_plan_sha=operation['approved_plan_sha256'],approved_decision_sha=operation['approved_decision_sha256'])
        need(type(result)is dict and set(result)=={'result','source_commit','plan_sha256','plan','authorizes_dispatch'} and result['result']=='SANITIZED_OPERATIONAL_PLAN' and result['authorizes_dispatch'] is False)
        closed_plan(io.canonical(result['plan']),operation['approved_plan_sha256']);need(result['plan_sha256']==operation['approved_plan_sha256'])
    need(result['source_commit']==profile['source_commit'])
    if 'packet_sha256' in result:need(result['packet_sha256']==profile['packet_sha256'])
    return result

def main(role,mode,expected_boot,approved_sha,manifest_raw,caps,binding_raw,approved_binding_sha,approved_intent_sha=None,operation=None):
    tag=selector(role,mode,approved_sha);need(type(expected_boot)is str and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',expected_boot));operation=validate_operation(mode,operation)
    need(sys.platform=='linux' and os.geteuid()==0 and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode and socket.gethostname()==HOSTS[role] and sys.argv==['-',tag])
    before,cache=validate_cache(manifest_raw,approved_sha,caps);binding=stage_binding(binding_raw,approved_binding_sha,before['runtime'],role,expected_boot)
    m,cache,modules=load_program(manifest_raw,approved_sha,cache);io=modules['native_io'];need(io.bootstrap(role)==expected_boot);package=modules['package_bootstrap'];package.verify_sources(manifest_raw,approved_sha,cache,io.GitObjects().blob if role=='web' else None)
    if mode=='readback' or mode in ('stage-packet','readback-packet','export-plan','stage-plan','readback-plan'):
        intent_sha=completed_source(role,expected_boot,approved_sha,cache,modules,approved_binding_sha,approved_intent_sha)
        if mode=='readback':return {'result':'public_source_readback_only','manifest_sha256':approved_sha,'intent_sha256':intent_sha,'binding_sha256':approved_binding_sha,'source_files':len(cache),'original_stage_actor_absent':True}
        return public_operation(role,mode,expected_boot,approved_sha,manifest_raw,cache,binding,approved_binding_sha,modules,operation)
    need(approved_intent_sha is None);state=local_preflight(role,expected_boot,approved_sha,cache,modules)
    if mode=='preflight':
        public={name[len('public-runtime35/'):]:raw for name,raw in cache.items() if name.startswith('public-runtime35/')};guard=modules['public_delivery'].preflight(role,public,m['runtime'],binding);need(guard['boot']==expected_boot and guard['binding_sha256']==approved_binding_sha)
        return dict(state,result='public_source_preflight_only',manifest_sha256=approved_sha,binding_sha256=approved_binding_sha,source_files=len(cache),operative_peer_custody_checked=True)
    actor=io.process_identity(os.getpid());result=package.stage_public(role,manifest_raw,approved_sha,cache,binding)
    intent=package.RECEIPTS/('preplan-source-'+approved_sha+'-'+role+'-intent.json');reader=io.ProtectedReader();ir=reader.read(intent,65536);record=io.decode(ir);need(record['binding_sha256']==approved_binding_sha and record['boot_id']==expected_boot and record['actor']==actor);reader.stable()
    need(type(result)is dict and set(result)=={'result','manifest_sha256','source_files'} and result['result']=='public_source_staged_only' and result['manifest_sha256']==approved_sha and result['source_files']==55)
    return dict(result,intent_sha256=io.sha(ir),binding_sha256=approved_binding_sha,original_actor=actor,original_argv_selector=tag)
