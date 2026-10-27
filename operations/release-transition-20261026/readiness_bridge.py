"""Fixed memory-only readiness over the EXISTING DB participant.

No channel/key/store/install, persisted-alert override, status write or retry.
Collector identity is an explicit plan/packet binding, never a hardcoded digest
that would create source→pinJSON→manifest→source hash recursion.
"""
from datetime import datetime,timezone,timedelta
import hashlib,json,re,sys,time,types
from pathlib import Path

FOLDER=Path('/opt/careertrellis/staging/pr98-117-release-ops-20261026')
FILES=('collector.py','terminal493.py','transaction04.py','pair_boundary.py',
       'installed493-pins.json','transaction04-pins.json')
_LOADED={} # verified immutable PUBLIC CODE only, never observations/policy/WAL
def need(value):
    if not value:raise RuntimeError('one_shot_readiness_refused')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def load(plan,source_commit,*,folder=FOLDER):
    from peer_server import protected
    from native_peer import decode
    key=(source_commit,plan['readiness_manifest_sha256'])
    if key in _LOADED:return _LOADED[key]
    manifest_raw=protected(folder/'one_shot_readiness'/'SOURCE_MANIFEST.json',0o600)
    need(sha(manifest_raw)==plan['readiness_manifest_sha256'])
    manifest=decode(manifest_raw);need(set(manifest)=={'schema','files'}
        and manifest['schema']=='careertrellis.one-shot-readiness-source/v1' and set(manifest['files'])==set(FILES))
    # Full packet loader has independently checked every nested member's exact
    # Git blob in source_commit; this rechecks cached bytes immediately for use.
    from peer_server import verify_readiness_sources
    cache=verify_readiness_sources(folder,source_commit,expected_manifest=plan['readiness_manifest_sha256'])
    for name in FILES:need(sha(cache[name])==manifest['files'][name])
    modules={}
    for name in ('collector','terminal493','transaction04','pair_boundary'):
        value=types.ModuleType(name);value.__file__=str(folder/'one_shot_readiness'/(name+'.py'));sys.modules[name]=value
        exec(compile(cache[name+'.py'],value.__file__,'exec'),value.__dict__);modules[name]=value
        if name=='collector':
            value.PUBLIC_FILES={k:cache[k] for k in ('installed493-pins.json','transaction04-pins.json')}
            value.VERIFIED_SOURCE_MANIFEST=plan['readiness_manifest_sha256']
    _LOADED[key]=modules
    return modules
def binding(plan,source_commit,nonce,phase,release,predecessor,issued_at):
    from release_transaction import digest
    need(phase in ('preflight','stopped','verifying','final') and release in plan['builds'])
    mode={'preflight':'participant-preclaim','stopped':'owned-stopped','verifying':'owned-verifying','final':'owned-final'}[phase]
    return {'nonce':nonce,'plan_sha256':digest(plan),'release':release,'build':plan['builds'][release],
        'phase':phase,'mode':mode,'predecessor':predecessor,'boots':dict(plan['boots']),
        'source_commit':source_commit,'issued_at':issued_at}
def collect_local(role,plan,source_commit,request):
    from release_transaction import digest
    need(type(request) is dict and request['plan_sha256']==digest(plan) and request['boots']==plan['boots']
        and request['source_commit']==source_commit and request['release'] in plan['builds']
        and request['build']==plan['builds'][request['release']])
    modules=load(plan,source_commit);collector=modules['collector'];collector.request(request)
    result=collector.collect(role,request)
    result['collector_manifest_sha256']=plan['readiness_manifest_sha256']
    return result

def verify_generation(value,plan,request,role,collector,*,now):
    """Verify explicit fresh evaluation generation, NEVER old status flag."""
    need(value['binding']==request and value['role']==role and value['boot']==request['boots'][role]
        and value['core_sha256']==collector.SPECS[role][2]
        and value['collector_manifest_sha256']==plan['readiness_manifest_sha256'])
    generation=value['evaluation_generation']
    need(set(generation)=={'binding','core_sha256','collector_manifest_sha256','snapshot_sha256','result_sha256',
         'inventory_before_sha256','inventory_after_sha256','evaluated_at','failure_count'})
    need(generation['binding']==request and generation['core_sha256']==value['core_sha256']
        and generation['collector_manifest_sha256']==plan['readiness_manifest_sha256']
        and generation['inventory_before_sha256']==generation['inventory_after_sha256']==value['receipt_inventory_sha256'])
    need(generation['snapshot_sha256']==value['snapshot_sha256']==sha(collector.canonical(value['original_snapshot']))
        and generation['result_sha256']==value['result_sha256']==sha(collector.canonical(value['canonical_evaluation']))
        and value['evaluation_generation_sha256']==sha(collector.canonical(generation)))
    result=value['canonical_evaluation'];evaluation=collector.datetime.fromisoformat(generation['evaluated_at'].replace('Z','+00:00'))
    need(result['evaluated_at']==generation['evaluated_at']==value['original_snapshot']['observed_at']
        and result['surface']==value['original_snapshot']['surface']==collector.SPECS[role][1]
        and evaluation.tzinfo is not None and 0<=(now-evaluation).total_seconds()<=30)
    need(value['inventory_verified'] is True and value['evaluation_generation_current'] is True
         and value['persisted_status_unchanged'] is True and generation['failure_count']==result['failure_count']==value['failure_count']==0
         and result['result']==value['result']=='ok' and result['failure_codes']==value['failure_codes']==[])
    operations=set(collector.DB_UNITS) if role=='db' else {'generated_object_mirror','generated_object_restore'}
    need(set(value['latest_receipts'])==operations)
    for summary in value['latest_receipts'].values():
        need({'result','completed_at','receipt_sha256'}<=set(summary)<={'result','completed_at','receipt_sha256','backup_label'}
            and summary['result']=='ok' and re.fullmatch('[0-9a-f]{64}',summary['receipt_sha256']))
        completed=collector.datetime.fromisoformat(summary['completed_at'].replace('Z','+00:00'))
        need(completed.tzinfo is not None and completed<=evaluation)
    collector.persisted_status_qualifies(role,value['persisted_status'],now)

def verify_repository_binding(repository,health,accepted_label,accepted_full_sha256):
    """Retain BOTH actual ANY equality and actual FULL binding; one info read."""
    need(set(repository)=={'latest_backup_label','latest_full_backup_label','checked_at'})
    need(repository['latest_backup_label']==health['latest_accepted_full_label']==accepted_label
        and repository['latest_full_backup_label']==accepted_label
        and health['original_snapshot']['latest_backup_label']==repository['latest_backup_label']
        and health['latest_receipts']['full_backup']['backup_label']==accepted_label
        and health['latest_receipts']['full_backup']['receipt_sha256']==accepted_full_sha256)

def admit(web,db,plan,source_commit,request,*,started_monotonic):
    need(0<=time.monotonic()-started_monotonic<=30)
    modules=load(plan,source_commit)
    now=datetime.now(timezone.utc)
    verify_generation(web,plan,request,'web',modules['collector'],now=now)
    verify_generation(db,plan,request,'db',modules['collector'],now=now)
    result=modules['pair_boundary'].check_pair(web,db,request,plan['readiness_manifest_sha256'],now=datetime.now(timezone.utc))
    need(0<=time.monotonic()-started_monotonic<=30)
    return result

DECISION_KEYS={'schema','authorization_id','prepared_certificate_sha256','plan_sha256','packet_sha256',
    'source_commit','outcome','selected_release','selected_build','boots','readiness_nonce',
    'readiness_binding_sha256','readiness_issued_at','readiness_expires_at','generation_sha256',
    'db_intent_sha256','db_fd_identities','committed_at'}

def release_stamp(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'))
    need(t.tzinfo is not None and t.utcoffset().total_seconds()==0)
    return t

def validate_final_binding(plan,binding,prepared,source_commit,expires_at,*,now,require_fresh):
    from release_transaction import digest,BASE,TARGET
    need(binding['phase']=='final' and binding['mode']=='owned-final'
        and binding['plan_sha256']==digest(plan) and binding['boots']==plan['boots']
        and binding['source_commit']==source_commit
        and binding['release']==prepared['selected_release'] and binding['build']==prepared['selected_build']
        and prepared['plan_sha256']==digest(plan) and prepared['packet_sha256']==plan['packet_sha256']
        and prepared['source_commit']==source_commit and prepared['boots']==plan['boots'])
    expected=TARGET if prepared['selected_release']==plan['correction'] else BASE
    need(binding['predecessor']==expected)
    issued=release_stamp(binding['issued_at']);expiry=release_stamp(expires_at)
    need(issued<expiry<=issued+timedelta(seconds=30) and expiry<=release_stamp(plan['expires_at']))
    if require_fresh:need(issued<=now<expiry)

def validate_decision(record,prepared,plan,binding,generations,source_commit,expires_at):
    from release_transaction import digest
    need(type(record) is dict and set(record)==DECISION_KEYS
        and record['schema']=='careertrellis.app-release-decision/v1'
        and record['authorization_id']==prepared['authorization_id']
        and record['prepared_certificate_sha256']==digest(prepared)
        and record['plan_sha256']==prepared['plan_sha256'] and record['packet_sha256']==plan['packet_sha256']
        and record['source_commit']==source_commit and record['outcome']==prepared['outcome']
        and record['selected_release']==prepared['selected_release'] and record['selected_build']==prepared['selected_build']
        and record['boots']==plan['boots'] and record['readiness_nonce']==binding['nonce']
        and record['readiness_binding_sha256']==digest(binding)
        and record['readiness_issued_at']==binding['issued_at'] and record['readiness_expires_at']==expires_at
        and record['generation_sha256']==generations and set(generations)=={'web','db'})
    for value in [*generations.values(),record['db_intent_sha256']]:
        need(type(value) is str and re.fullmatch('[0-9a-f]{64}',value))
    need(release_stamp(binding['issued_at'])<=release_stamp(record['committed_at'])<release_stamp(expires_at))
    fds=record['db_fd_identities']
    need(type(fds) is list and len(fds)==3)
    expected=['/var/lib/careertrellis/paired-maintenance/db/control.lock',
        '/run/lock/careertrellis-maintenance.lock','/run/lock/careertrellis-pgbackrest-repository.lock']
    for value,path in zip(fds,expected):
        need(type(value) is dict and set(value)=={'fd','path','dev','ino'} and value['path']==path
             and all(type(value[k]) is int and value[k]>=0 for k in ('fd','dev','ino')))
    need(len({v['fd'] for v in fds})==3)

def validate_db_release_ack(ack,record):
    from release_transaction import digest
    need(type(ack) is dict and set(ack)=={'role','result','decision_sha256','closed_fds'}
         and ack['role']=='db' and ack['result']==record['outcome']
         and ack['decision_sha256']==digest(record)
         and ack['closed_fds']==list(reversed(record['db_fd_identities'])))
