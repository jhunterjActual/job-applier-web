"""Authenticated schedule witnesses and explicit bundle-health evaluation.

Schedule inspection is provider-free and never acquires an operation lock.
Database storage inspection requires an already-owned canonical repository FD.
Busy/unknown state is not successful health evidence and clears no old alerts.
No legacy backup receipt, timer transition or installed policy is written here.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import uuid
import shlex
import importlib.util
import sys
import configparser

from . import baseline,bundle,catalog,coordinator,entry,hydrate,local_restore_plan as plans,native_restore

SCHEMA='careertrellis.onedrive-retention-schedule/v1'
STATUS='careertrellis.onedrive-schedule-status/v1'
WITNESS=coordinator.STATE/'db'/'schedule'/'witness.json'
DATABASE_HEALTH=coordinator.STATE/'db'/'health'/'current.json'
CORE=Path('/usr/local/libexec/careertrellis/backup_health.py')
MONITOR_SCHEMA='careertrellis.onedrive-native-monitor-evidence/v2'
INSTALL_PLAN=Path('/etc/careertrellis/onedrive-retention-install/plan.json')
INSTALL_ROOT=Path('/var/backups/careertrellis/onedrive-retention-install')
TIMERS={'careertrellis-paired-maintenance.timer':('careertrellis-paired-maintenance.service',8*86400),
        'careertrellis-onedrive-retention.timer':('careertrellis-onedrive-retention.service',3*86400)}
COMMANDS={'careertrellis-paired-maintenance.service':['/usr/bin/python3','-I','-B',
              '/usr/local/libexec/careertrellis/paired_maintenance/entry.py','web-run'],
          'careertrellis-onedrive-retention.service':['/usr/bin/python3','-I','-B',
              '/usr/local/libexec/careertrellis/onedrive_retention/entry.py','scheduled-run']}
FIELDS=('LoadState','ActiveState','SubState','UnitFileState','Job','FragmentPath','DropInPaths','NeedDaemonReload')
BINDINGS=frozenset(('standalone_manifest_sha256','paired_manifest_sha256','paired_policy_sha256','standalone_policy_sha256'))
MAX_WITNESS_AGE=1800
MAX_BACKUP_AGE=36*3600
NAMESPACE_BUDGET=512*1024**2


class HealthRefused(ValueError):pass


def need(value):
    if not value:raise HealthRefused('retention_health_evidence_unavailable')


def sha(raw):return hashlib.sha256(raw).hexdigest()


def keys(value,names):need(type(value) is dict and set(value)==set(names))


def timestamp(value):
    try:return catalog.timestamp(value)
    except Exception:raise HealthRefused('retention_health_evidence_unavailable') from None


def validate_schedule(value,bindings,revision,release,now):
    """Pure claim validation. Authentication comes from fixed native factories."""
    keys(value,('schema','result','observed_at','artifact_revision','application_release',
        'baseline_bindings','web_install_receipt_sha256','units'))
    need(value['schema']==SCHEMA and value['result']=='pass'
         and value['artifact_revision']==revision and value['application_release']==release
         and value['baseline_bindings']==bindings and set(bindings)==BINDINGS
         and all(catalog.hex_value(digest) for digest in bindings.values())
         and catalog.hex_value(revision,40) and catalog.hex_value(release,40)
         and catalog.hex_value(value['web_install_receipt_sha256']))
    observed=timestamp(value['observed_at'])
    need(type(now) is dt.datetime and now.utcoffset()==dt.timedelta(0)
         and 0<=(now-observed).total_seconds()<=MAX_WITNESS_AGE)
    keys(value['units'],TIMERS)
    now_us=int(now.timestamp()*1000000)
    for name,(target,maximum) in TIMERS.items():
        unit=value['units'][name]
        keys(unit,('source_sha256','dropins','load_state','active_state','sub_state','enabled_state',
            'target_unit','next_trigger_epoch_us','source_loaded','service'))
        need(catalog.hex_value(unit['source_sha256']) and type(unit['dropins']) is dict
             and unit['load_state']=='loaded' and unit['active_state']=='active'
             and unit['sub_state']=='waiting' and unit['enabled_state']=='enabled'
             and unit['source_loaded'] is True
             and unit['target_unit']==target and type(unit['next_trigger_epoch_us']) is int
             and now_us<unit['next_trigger_epoch_us']<=int(observed.timestamp()*1000000)+maximum*1000000)
        for path,digest in unit['dropins'].items():
            path=PurePosixPath(path)
            need(path.parent==PurePosixPath('/etc/systemd/system/'+name+'.d')
                 and re.fullmatch(r'[A-Za-z0-9_.-]+\.conf',path.name) and catalog.hex_value(digest))
        service=unit['service']
        keys(service,('source_sha256','dropins','source_loaded','load_state','result','profile',
                      'executable','argv','command_supported'))
        need(catalog.hex_value(service['source_sha256']) and type(service['dropins']) is dict
             and service['source_loaded'] is True and service['load_state']=='loaded'
             and service['result']=='success' and service['executable']=='/usr/bin/python3'
             and service['argv']==COMMANDS[target] and service['command_supported'] is True)
        profile=service['profile'];keys(profile,('Type','Restart','RemainAfterExit','User','Group'))
        need(profile['Type'] in ('oneshot','exec') and profile['Restart']=='no'
             and profile['RemainAfterExit']=='no' and profile['User'] in ('','root') and profile['Group'] in ('','root'))
        for path,digest in service['dropins'].items():
            path=PurePosixPath(path)
            need(path.parent==PurePosixPath('/etc/systemd/system/'+target+'.d')
                 and re.fullmatch(r'[A-Za-z0-9_.-]+\.conf',path.name) and catalog.hex_value(digest))
    need(min(unit['next_trigger_epoch_us'] for unit in value['units'].values())<=
        int(observed.timestamp()*1000000)+MAX_BACKUP_AGE*1000000)
    return value


def _context(role):
    need(os.name=='posix' and os.geteuid()==os.getegid()==0
         and socket.gethostname()=={'web':'careertrellis-staging-web-1','db':'job-applier-db-staging-01'}[role]
         and Path(__file__).absolute()==entry.ROOT/'health.py')
    revision=entry.verify_install();context=entry._native_dependencies(revision,role)
    need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role==role)
    return context


def _document(path,mode=0o600):
    raw,value=entry._protected_document(path,mode)
    need(bundle.canonical(value)==raw);return raw,value


def _installation(context):
    """The current fixed plan is only a pointer; accepted receipt authenticates it."""
    plan_raw,plan=_document(INSTALL_PLAN)
    need(type(plan) is dict and catalog.hex_value(plan.get('transaction'),32)
         and plan.get('role')==context.role and plan.get('revision')==context.revision)
    root=INSTALL_ROOT/plan['transaction']
    complete_raw,complete=_document(root/'complete.json')
    archived_raw,archived=_document(root/'plan.json')
    _seal_raw,seal=_document(root/'sealed.json')
    restore_raw,restored=_document(root/'timer-restoration.json')
    need(archived_raw==plan_raw and archived==plan
         and complete.get('schema')=='careertrellis.retention-install/v1'
         and complete.get('result')=='installed_timers_held' and complete.get('role')==context.role
         and complete.get('transaction')==plan['transaction'] and complete.get('revision')==context.revision
         and complete.get('plan_sha256')==sha(plan_raw)
         and complete.get('source_packet_sha256')==plan.get('packet_sha256')
         and sha(complete_raw)==context.document['acceptance_receipts']['ops']
         and complete.get('standalone_manifest_sha256')==context.bindings['standalone_manifest_sha256']
         and complete.get('paired_manifest_sha256')==context.bindings['paired_manifest_sha256']
         and complete.get('prior_timer_states_restored') is True
         and complete.get('timer_restoration_sha256')==sha(restore_raw)
         and seal.get('schema')=='careertrellis.retention-install-seal/v1'
         and seal.get('install_receipt_sha256')==sha(complete_raw)
         and seal.get('accepted_baseline_sha256')==sha(bundle.canonical(context.document))
         and restored.get('schema')=='careertrellis.retention-timer-restoration/v1')
    return plan,sha(complete_raw)


def _unit_path(name):
    need(name in TIMERS or name in COMMANDS)
    return '/org/freedesktop/systemd1/unit/'+''.join(
        char if char.isascii() and char.isalnum() else '_%02x'%ord(char) for char in name)


def _property(name,field):
    need(field in ('NextElapseUSecRealtime','Unit'))
    raw=hydrate.command(['/usr/bin/busctl','get-property','org.freedesktop.systemd1',_unit_path(name),
        'org.freedesktop.systemd1.Timer',field],output=True,timeout=10)
    if field=='NextElapseUSecRealtime':
        need(re.fullmatch(rb't [0-9]+\n',raw));value=int(raw[2:]);need(0<value<2**64-1);return value
    need(raw.startswith(b's ') and raw.endswith(b'\n'))
    try:value=json.loads(raw[2:])
    except Exception:raise HealthRefused('retention_health_evidence_unavailable') from None
    need(type(value) is str and value==TIMERS[name][0]);return value


def _show(name,fields=FIELDS):
    need(name in TIMERS or name in COMMANDS)
    raw=hydrate.command(['/usr/bin/systemctl','show',name,'--property='+','.join(fields)],output=True,timeout=10)
    result={}
    for row in raw.decode('ascii').splitlines():
        key,separator,value=row.partition('=')
        need(separator and key in fields and key not in result);result[key]=value
    keys(result,fields);return result


def require_service_commands():
    """Pure parser check, never dispatch. Unsupported future scheduling refuses."""
    try:
        from . import execution
        command=entry.parse_command(['scheduled-run'])
        need(type(command) is dict and command.get('operation')=='scheduled-run' and command.get('role')=='web'
             and execution.supports_operation(command['operation']) is True)
    except Exception:raise HealthRefused('retention_health_evidence_unavailable') from None


def validate_exec_start(raw,target):
    """Strict typed, one-command D-Bus array; no shell evaluation or prefix match."""
    need(target in COMMANDS and type(raw) is bytes and 0<len(raw)<=65536)
    try:tokens=shlex.split(raw.decode('ascii'),posix=True)
    except Exception:raise HealthRefused('retention_health_evidence_unavailable') from None
    argv=COMMANDS[target];prefix=['a(sasbttttuii)','1','/usr/bin/python3',str(len(argv)),*argv,'false']
    need(tokens[:len(prefix)]==prefix and len(tokens)==len(prefix)+7
         and all(re.fullmatch('[0-9]+',token) and int(token)<2**64 for token in tokens[len(prefix):]))
    return {'executable':'/usr/bin/python3','argv':list(argv)}


def expected_service_dropins(target,plan):
    source=PurePosixPath('/etc/systemd/system')/target
    expected={path:digest for path,digest in plan['baseline']['unit_dropins'].items()
              if PurePosixPath(path).parent==PurePosixPath(str(source)+'.d')}
    fixed='/etc/systemd/system/careertrellis-paired-maintenance.service.d/70-onedrive-retention-paths.conf'
    if target=='careertrellis-paired-maintenance.service':
        row=plan['files'].get(fixed)
        need(type(row) is dict and row.get('sha256')==sha(b'[Service]\nReadWritePaths=/var/lib/careertrellis/onedrive-retention /var/lib/careertrellis/onedrive-retention-public\n')
             and fixed not in expected)
        expected[fixed]=row['sha256']
    return expected


def _service(target,plan):
    need(target in COMMANDS)
    if target=='careertrellis-onedrive-retention.service':require_service_commands()
    source=Path('/etc/systemd/system')/target
    expected=(plan['baseline']['service_sources'][target] if target=='careertrellis-paired-maintenance.service'
              else plan['files'][str(source)]['sha256'])
    raw=hydrate.protected(source,0o644)[0];need(sha(raw)==expected)
    fields=tuple(dict.fromkeys((*FIELDS,'Result','Type','Restart','RemainAfterExit','User','Group')))
    before=_show(target,fields)
    need(before['LoadState']=='loaded' and before['FragmentPath']==str(source) and before['NeedDaemonReload']=='no'
         and before['ActiveState'] in ('inactive','active','activating') and before['Result']=='success'
         and before['Type'] in ('oneshot','exec') and before['Restart']=='no' and before['RemainAfterExit']=='no'
         and before['User'] in ('','root') and before['Group'] in ('','root'))
    expected_dropins=expected_service_dropins(target,plan)
    actual={};folder=Path(str(source)+'.d')
    if os.path.lexists(folder):
        meta=folder.lstat();need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0 and not meta.st_mode&0o022)
        names=os.listdir(folder);need(len(names)<=64)
        for name in names:
            need(re.fullmatch(r'[A-Za-z0-9_.-]+\.conf',name))
            actual[str(folder/name)]=sha(hydrate.protected(folder/name,0o644)[0])
    need(actual==expected_dropins and set(before['DropInPaths'].split())==set(actual))
    for prefix in ('/run/systemd/system','/run/systemd/system.control','/etc/systemd/system.control'):
        for suffix in ('','.d'):need(not os.path.lexists(prefix+'/'+target+suffix))
    command=validate_exec_start(hydrate.command(['/usr/bin/busctl','get-property','org.freedesktop.systemd1',
        _unit_path(target),'org.freedesktop.systemd1.Service','ExecStart'],output=True,timeout=10),target)
    if os.path.lexists(folder):
        need(set(os.listdir(folder))=={Path(path).name for path in actual})
    else:need(not actual)
    for path,digest in actual.items():need(sha(hydrate.protected(Path(path),0o644)[0])==digest)
    need(_show(target,fields)==before and hydrate.protected(source,0o644)[0]==raw)
    return dict(command,source_sha256=sha(raw),dropins=actual,source_loaded=True,load_state='loaded',
        result='success',profile={key:before[key] for key in ('Type','Restart','RemainAfterExit','User','Group')},
        command_supported=True)


def _timer(name,plan):
    source=Path('/etc/systemd/system')/name
    expected=(plan['baseline']['timer_sources'][name] if name=='careertrellis-paired-maintenance.timer'
              else plan['files'][str(source)]['sha256'])
    need(catalog.hex_value(expected))
    raw=hydrate.protected(source,0o644)[0]
    need(sha(raw)==expected)
    before=_show(name)
    need(before['FragmentPath']==str(source) and before['Job']==''
         and before['LoadState']=='loaded' and before['ActiveState']=='active'
         and before['SubState']=='waiting' and before['UnitFileState']=='enabled' and before['NeedDaemonReload']=='no')
    expected_dropins={path:digest for path,digest in plan['baseline']['unit_dropins'].items()
                     if PurePosixPath(path).parent==PurePosixPath(str(source)+'.d')}
    actual={};folder=Path(str(source)+'.d')
    if os.path.lexists(folder):
        meta=folder.lstat();need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0 and not meta.st_mode&0o022)
        names=os.listdir(folder);need(len(names)<=64)
        for filename in names:
            need(re.fullmatch(r'[A-Za-z0-9_.-]+\.conf',filename))
            actual[str(folder/filename)]=sha(hydrate.protected(folder/filename,0o644)[0])
    need(actual==expected_dropins and set(before['DropInPaths'].split())==set(actual))
    for prefix in ('/run/systemd/system','/run/systemd/system.control','/etc/systemd/system.control'):
        for suffix in ('','.d'):need(not os.path.lexists(prefix+'/'+name+suffix))
    target=_property(name,'Unit');next_trigger=_property(name,'NextElapseUSecRealtime')
    service=_service(target,plan)
    if os.path.lexists(folder):
        meta=folder.lstat();need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0 and not meta.st_mode&0o022)
        need(set(os.listdir(folder))=={Path(path).name for path in actual})
    else:need(not actual)
    for path,digest in actual.items():need(sha(hydrate.protected(Path(path),0o644)[0])==digest)
    need(_show(name)==before and hydrate.protected(source,0o644)[0]==raw)
    return {'source_sha256':sha(raw),'dropins':actual,'load_state':before['LoadState'],
        'active_state':before['ActiveState'],'sub_state':before['SubState'],'enabled_state':before['UnitFileState'],
        'target_unit':target,'next_trigger_epoch_us':next_trigger,'source_loaded':True,'service':service}


def validate_daily_calendar(raw,policy):
    """Bind the reviewed timer source to the accepted policy's actual slot."""
    need(type(raw) is bytes and 0<len(raw)<=65536 and type(policy) is coordinator.Policy)
    parser=configparser.ConfigParser(interpolation=None,strict=True)
    try:parser.read_string(raw.decode('utf-8'))
    except (ValueError,UnicodeError,configparser.Error):
        raise HealthRefused('retention_schedule_calendar_mismatch') from None
    expected=coordinator.policy_calendar(policy)
    need(not parser.defaults() and parser.has_section('Timer')
         and parser['Timer'].get('OnCalendar')==expected)
    return expected


def collect_web_schedule():
    """Actual WEB timer/source observation. No provider, lock or status mutation."""
    context=_context('web');plan,installed=_installation(context)
    _raw,document=_document(coordinator.PRIVATE_POLICY)
    active=coordinator.Policy(document)
    need(active.digest==context.bindings['standalone_policy_sha256'])
    timer=Path('/etc/systemd/system/careertrellis-onedrive-retention.timer')
    raw=hydrate.protected(timer,0o644)[0]
    need(sha(raw)==plan['files'][str(timer)]['sha256'])
    validate_daily_calendar(raw,active)
    units={name:_timer(name,plan) for name in TIMERS}
    value={'schema':SCHEMA,'result':'pass','observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'artifact_revision':context.revision,'application_release':context.document['application_release'],
        'baseline_bindings':dict(context.bindings),'web_install_receipt_sha256':installed,'units':units}
    validate_schedule(value,context.bindings,context.revision,context.document['application_release'],
                      dt.datetime.now(dt.timezone.utc))
    need(baseline.native_context().bindings==context.bindings)
    return value


def schedule_status():
    """Fixed DB witness check; no fallback to a disabled legacy timer."""
    result={'schema':STATUS,'result':'not_ready','paired_full_ready':False,'retention_ready':False,
            'authenticated':False,'provider_calls':0,'locks_acquired':0}
    try:
        context=_context('db');_installation(context)
        require_service_commands()
        _raw,value=_document(WITNESS)
        validate_schedule(value,context.bindings,context.revision,context.document['application_release'],
                          dt.datetime.now(dt.timezone.utc))
        need(baseline.native_context().bindings==context.bindings)
        result.update(result='ready',paired_full_ready=True,retention_ready=True,authenticated=True)
    except Exception:pass
    return result


def store_schedule(value):
    """Only the authenticated paired endpoint calls this fixed DB receiver.

    This is a schedule delivery action, not a progress observer. It acquires
    only its private schedule-control lock and never a repository lock.
    """
    context=_context('db');_installation(context)
    require_service_commands()
    now=dt.datetime.now(dt.timezone.utc)
    validate_schedule(value,context.bindings,context.revision,context.document['application_release'],now)
    need((now-timestamp(value['observed_at'])).total_seconds()<=120)
    with coordinator.control(WITNESS.parent):
        if os.path.lexists(WITNESS):
            previous_raw,previous=_document(WITNESS)
            need(timestamp(previous['observed_at'])<=timestamp(value['observed_at']))
            if previous['observed_at']==value['observed_at']:
                need(previous_raw==bundle.canonical(value))
                return {'result':'stored','witness_sha256':sha(previous_raw)}
        need(baseline.native_context().bindings==context.bindings)
        coordinator.replace_private(WITNESS,value)
        raw,saved=_document(WITNESS)
        need(saved==value and baseline.native_context().bindings==context.bindings)
        return {'result':'stored','witness_sha256':sha(raw)}


def run_web_health():
    """Deliver the real witness over the existing verified forced-command client."""
    value=collect_web_schedule()
    from paired_maintenance import entry as paired_entry
    from paired_maintenance.client import Client
    paired_entry.verify_install();policy=paired_entry.load_policy()
    need(policy.digest==value['baseline_bindings']['paired_policy_sha256'])
    answer=Client(policy,uuid.uuid4().hex).call('retention_schedule',{'witness':value})
    keys(answer,('result','witness_sha256'))
    need(answer['result']=='stored' and answer['witness_sha256']==sha(bundle.canonical(value)))
    return {'schema':'careertrellis.onedrive-web-health/v1','result':'schedule_delivered',
            'witness_sha256':answer['witness_sha256'],'provider_calls':0}


def monitor_mode():
    """Absent selector AND intent mean legacy; partial/invalid state refuses."""
    from . import activation
    selected=activation.routing_state()
    if selected is None:return {'schema':'careertrellis.onedrive-monitor-mode/v1','result':'legacy'}
    context=_context('db');_installation(context)
    activation.require_activated(context)
    return {'schema':'careertrellis.onedrive-monitor-mode/v1','result':'bundle'}


def publish_database_health(result,context):
    """Persist an actual native census before the legacy wrapper owns its lock."""
    from . import activation
    selected=activation.routing_state()
    if selected is not None:activation.require_activated(context)
    document=dict(result,baseline_bindings=dict(context.bindings),artifact_revision=context.revision,
        application_release=context.document['application_release'],
        activation_sha256=selected.digest if selected is not None else None)
    with coordinator.control(DATABASE_HEALTH.parent):
        need(baseline.native_context().bindings==context.bindings)
        coordinator.replace_private(DATABASE_HEALTH,document)
        _raw,saved=_document(DATABASE_HEALTH);need(saved==document)
    return document


def native_monitor_evidence(expected_label,*,unavailable=False):
    """Independently authenticate selection and fresh pre-lock native evidence.

    The caller cannot select a healthy strategy by flag alone. The unavailable
    path can only report an alert, and never reuses an earlier healthy census.
    No provider traversal or canonical-lock manipulation happens here.
    """
    from . import activation
    context=_context('db');_installation(context)
    selected=activation.routing_state();need(selected is not None)
    activation.require_activated(context);plans._label(expected_label)
    now=dt.datetime.now(dt.timezone.utc)
    evidence={'schema':MONITOR_SCHEMA,'result':'unavailable','observed_at':now.isoformat(),
        'backup_label':None,'restore_age_seconds':None,'recovery_point_age_seconds':None,'accepted_bundle_count':0,'health_sha256':None}
    if unavailable:return evidence
    raw,document=_document(DATABASE_HEALTH)
    need(document.get('schema')=='careertrellis.onedrive-retention-health/v2'
         and document.get('authenticated') is True and document.get('baseline_bindings')==context.bindings
         and document.get('artifact_revision')==context.revision
         and document.get('application_release')==context.document['application_release']
         and document.get('activation_sha256')==selected.digest
         and document.get('result') in ('ok','warning','urgent','alert')
         and type(document.get('accepted_bundle_count')) is int and document['accepted_bundle_count']>=2
         and 0<=(now-timestamp(document['observed_at'])).total_seconds()<=300)
    validate_cached_health(document)
    plans._label(document['backup_label'])
    age=int((now-timestamp(document['latest_restore_completed_at'])).total_seconds());need(age>=0)
    recovery_age=int((now-timestamp(document['backup_completed_at'])).total_seconds())
    need(recovery_age>=age>=0)
    evidence.update(result=document['result'],observed_at=document['observed_at'],backup_label=document['backup_label'],
        restore_age_seconds=age,recovery_point_age_seconds=recovery_age,
        accepted_bundle_count=document['accepted_bundle_count'],health_sha256=sha(raw))
    need(baseline.native_context().bindings==context.bindings)
    return evidence


def evaluate_core(raw,*,unavailable=False):
    """Use actual bundle health while preserving R2, WAL and R2-restore checks."""
    context=_context('db');plan,_receipt=_installation(context)
    expected=plan['files'].get(str(CORE),{}).get('sha256');need(catalog.hex_value(expected))
    source=hydrate.protected(CORE,0o644,limit=2*1024**2)[0];need(sha(source)==expected)
    spec=importlib.util.spec_from_file_location('_retention_bound_health_core',CORE)
    need(spec is not None);module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    try:
        exec(compile(source,str(CORE),'exec'),module.__dict__)
        need(type(raw) is bytes and 0<len(raw)<=65536)
        snapshot=module.validate_snapshot(module._strict_json_bytes(raw,module.MAX_DOCUMENT_BYTES))
        replacement=native_monitor_evidence(snapshot['latest_backup_label'],unavailable=unavailable)
        status=module.evaluate_backup_health(snapshot,receipt_root=Path('/var/backups/careertrellis/backup-health/operations'),
                                             bundle_replacement=replacement)
        module.write_status(status,root=Path('/var/lib/careertrellis/backup-health/database'))
        return status
    finally:sys.modules.pop(spec.name,None)


def evaluate(value,now):
    """Pure bounded health arithmetic; supplied evidence is not authenticated."""
    keys(value,('observed_at','quota','namespace_bytes','catalog','latest_restore','schedule_ready'))
    need(type(now) is dt.datetime and now.utcoffset()==dt.timedelta(0)
         and 0<=(now-timestamp(value['observed_at'])).total_seconds()<=900
         and type(value['schedule_ready']) is bool)
    quota=value['quota'];keys(quota,('total','free','observed_at'))
    need(type(quota['total']) is int and quota['total']>0 and type(quota['free']) is int
         and 0<=quota['free']<=quota['total']
         and 0<=(now-timestamp(quota['observed_at'])).total_seconds()<=900
         and type(value['namespace_bytes']) is int and value['namespace_bytes']>=0)
    snapshot=value['catalog'];catalog.keys(snapshot,('schema','observed_at','entries','holds'))
    need(snapshot['schema']=='careertrellis.onedrive-bundle-catalog/v1' and type(snapshot['entries']) is list)
    from . import retire
    retire.prepare(snapshot,now.isoformat())
    entries=snapshot['entries'];reasons=[]
    if not value['schedule_ready']:reasons.append('replacement_schedule_not_ready')
    if len(entries)<2:reasons.append('two_verified_replacements_required')
    native=value['latest_restore']
    latest=next((item for item in entries if native is not None and item['archive_sha256']==native['archive_sha256']),None)
    if latest is None:need(native is None);reasons.append('no_verified_bundle')
    else:
        keys(native,('archive_sha256','backup_label','completed_at','native_restore_accepted','checksums_verified',
                     'receipt_sha256','terminal_proof_sha256','backup_completed_at'))
        need(native['archive_sha256']==latest['archive_sha256'] and native['native_restore_accepted'] is True
             and native['checksums_verified'] is True
             and native['receipt_sha256']==latest['native_restore_receipt_sha256']
             and catalog.hex_value(native['terminal_proof_sha256'])
             and timestamp(native['backup_completed_at'])<=timestamp(native['completed_at'])<=now)
        plans._label(native['backup_label'])
        if (now-timestamp(native['backup_completed_at'])).total_seconds()>MAX_BACKUP_AGE:
            reasons.append('recovery_point_stale')
    namespace=value['namespace_bytes']
    if namespace>NAMESPACE_BUDGET:reasons.append('bundle_namespace_budget_exceeded')
    used=quota['total']-quota['free'];urgent=used*100>=quota['total']*85
    warning=used*100>=quota['total']*80
    if urgent:reasons.append('account_usage_urgent')
    elif warning:reasons.append('account_usage_warning')
    severity='alert' if any(reason not in ('account_usage_warning','account_usage_urgent') for reason in reasons) else (
        'urgent' if urgent else 'warning' if warning else 'ok')
    return {'schema':'careertrellis.onedrive-retention-health/v2','result':severity,'reasons':sorted(reasons),
        'observed_at':now.isoformat(),'accepted_bundle_count':len(entries),'namespace_bytes':namespace,
        'namespace_budget_bytes':NAMESPACE_BUDGET,'account_total_bytes':quota['total'],
        'account_free_bytes':quota['free'],'backup_label':native['backup_label'] if native else None,
        'latest_archive_sha256':native['archive_sha256'] if native else None,
        'latest_restore_completed_at':native['completed_at'] if native else None,
        'backup_completed_at':native['backup_completed_at'] if native else None,
        'recovery_point_age_seconds':int((now-timestamp(native['backup_completed_at'])).total_seconds()) if native else None,
        'current_remote_bytes_reverified':False,'authenticated':False,'alerts_cleared':False}


def database_health(repository_lock_fd):
    """Native census under the caller's already-owned canonical repository lock."""
    from . import guard,execution
    context=_context('db');_installation(context)
    files=catalog.NativeFiles(repository_lock_fd)
    observed=guard.native_catalog_inventory(repository_lock_fd)
    need(observed['authenticated'] is True)
    store=catalog.NativeReadStore(repository_lock_fd)
    snapshot=store.snapshot(observed['inventory'])
    need(snapshot==observed['catalog'])
    native=None
    if snapshot['entries']:
        with store.io.locked():records=store.records()
        points={item['id']:native_restore.accepted_backup_completed_at(records[item['id']]) for item in snapshot['entries']}
        latest=max(snapshot['entries'],key=lambda item:(timestamp(points[item['id']]),timestamp(item['accepted_at']),item['id']))
        record=records[latest['id']]
        receipt=record['evidence']['native'];tx=record['evidence']['download']['transaction']
        attempt=receipt['attempt'];root=Path(plans.RESTORE_ROOT)/(tx+('' if attempt==1 else '-%02d'%attempt))
        raw=native_restore.protected(root/'complete.json',maximum=131072);need(sha(raw)==latest['native_restore_receipt_sha256'])
        proof_raw=native_restore.protected(root/'unit-terminal-proof.json',maximum=131072)
        need(sha(proof_raw)==record['evidence']['terminal_proof_sha256'])
        ownership=native_restore.decode(native_restore.protected(root/'ownership.json',maximum=131072))
        stop=native_restore.decode(native_restore.protected(root/'unit-stop-intent.json',maximum=131072))
        proof=native_restore.decode(proof_raw)
        native_restore.validate_terminal_proof(proof,receipt,ownership['owner'],stop_intent=stop)
        execution.terminal(proof['unit'])
        native={key:receipt[key] for key in ('archive_sha256','backup_label','completed_at',
            'native_restore_accepted','checksums_verified')}
        native.update(receipt_sha256=sha(raw),terminal_proof_sha256=sha(proof_raw),backup_completed_at=points[latest['id']])
    value={'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'quota':observed['quota'],
        'namespace_bytes':observed['namespace_bytes'],'catalog':snapshot,'latest_restore':native,
        'schedule_ready':schedule_status()['result']=='ready'}
    result=evaluate(value,dt.datetime.now(dt.timezone.utc))
    files.require_repository_lock();need(baseline.native_context().bindings==context.bindings)
    result['authenticated']=True
    return publish_database_health(result,context)


def validate_cached_health(value):
    """Strict v2 historical-cache structure only; never authenticate freshness."""
    keys(value,('schema','result','reasons','observed_at','accepted_bundle_count','namespace_bytes',
        'namespace_budget_bytes','account_total_bytes','account_free_bytes','backup_label','latest_archive_sha256',
        'latest_restore_completed_at','backup_completed_at','recovery_point_age_seconds','current_remote_bytes_reverified',
        'authenticated','alerts_cleared','baseline_bindings','artifact_revision','application_release','activation_sha256'))
    need(value['schema']=='careertrellis.onedrive-retention-health/v2' and value['result'] in ('ok','warning','urgent','alert')
        and value['authenticated'] is True and value['current_remote_bytes_reverified'] is False and value['alerts_cleared'] is False)
    keys(value['baseline_bindings'],BINDINGS)
    need(all(catalog.hex_value(sha) for sha in value['baseline_bindings'].values())
        and catalog.hex_value(value['artifact_revision'],40) and catalog.hex_value(value['application_release'],40)
        and (value['activation_sha256'] is None or catalog.hex_value(value['activation_sha256'])))
    for field in ('accepted_bundle_count','namespace_bytes','account_total_bytes','account_free_bytes'):
        need(type(value[field]) is int and value[field]>=0)
    need(value['account_total_bytes']>0 and value['account_free_bytes']<=value['account_total_bytes']
        and type(value['namespace_budget_bytes']) is int and value['namespace_budget_bytes']==NAMESPACE_BUDGET)
    observed=timestamp(value['observed_at']);reasons=value['reasons']
    allowed={'replacement_schedule_not_ready','two_verified_replacements_required','no_verified_bundle',
        'recovery_point_stale','bundle_namespace_budget_exceeded','account_usage_urgent','account_usage_warning'}
    need(type(reasons) is list and all(type(reason) is str and reason in allowed for reason in reasons)
        and reasons==sorted(set(reasons)))
    expected={'replacement_schedule_not_ready'} if 'replacement_schedule_not_ready' in reasons else set()
    count=value['accepted_bundle_count']
    if count<2:expected.add('two_verified_replacements_required')
    if value['backup_label'] is None:
        need(all(value[field] is None for field in ('latest_archive_sha256','latest_restore_completed_at',
            'backup_completed_at','recovery_point_age_seconds')) and count==0)
        expected.add('no_verified_bundle')
    else:
        need(count>0)
        plans._label(value['backup_label']);need(catalog.hex_value(value['latest_archive_sha256']))
        point=timestamp(value['backup_completed_at']);completed=timestamp(value['latest_restore_completed_at'])
        need(point<=completed<=observed and type(value['recovery_point_age_seconds']) is int
            and value['recovery_point_age_seconds']==int((observed-point).total_seconds()))
        # The displayed integer truncates fractions; use actual timestamp age.
        if (observed-point).total_seconds()>MAX_BACKUP_AGE:expected.add('recovery_point_stale')
    if value['namespace_bytes']>value['namespace_budget_bytes']:expected.add('bundle_namespace_budget_exceeded')
    used=value['account_total_bytes']-value['account_free_bytes']
    urgent=used*100>=value['account_total_bytes']*85
    warning=used*100>=value['account_total_bytes']*80
    if urgent:expected.add('account_usage_urgent')
    elif warning:expected.add('account_usage_warning')
    need(reasons==sorted(expected))
    severity=('alert' if expected-{'account_usage_warning','account_usage_urgent'} else
        'urgent' if urgent else 'warning' if warning else 'ok')
    need(value['result']==severity)
    return value
