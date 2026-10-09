"""Explicit existing-v2 monitoring successor; no database, ping or timer start.

Run only under separately granted installation/configuration authority. Original
bytes stay in exclusive root-only evidence. Interrupted publication is held and
can only reconcile the same before/after bytes forward, never roll back.
"""
from contextlib import ExitStack
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

import admission_operations as ops
import admission_purge as purge
import admission_reporter as reporter
import totp_cleanup_operations as cleanup

RELEASES = Path('/opt/careertrellis/staging/releases')
UNITS = Path('/etc/systemd/system')
OLD_KINDS = ('admission-purge', 'admission-purge-monitor', 'admission-reporter',
    'totp-cleanup', 'totp-cleanup-monitor')
NEW_KINDS = OLD_KINDS
OLD_UNITS = tuple('careertrellis-'+kind+'@.'+suffix for kind in OLD_KINDS for suffix in ('service','timer'))
NEW_UNITS = tuple('careertrellis-'+kind+'@.'+suffix for kind in NEW_KINDS for suffix in ('service','timer'))
COMMON_FILES = tuple('backend/'+name+'.py' for name in ('admission_operations','admission_purge',
    'admission_reporter','admission_alert_contract','admission_healthchecks','identity_entry',
    'identity_evidence','identity_operator','session_authorization')) + (
    'deploy/scripts/manage-admission-purge.sh','deploy/scripts/manage-admission-reporter.sh')
NEW_FILES = (*COMMON_FILES, *(('backend/'+name+'.py') for name in (
    'totp_cleanup','totp_cleanup_operations','totp_cleanup_alert_contract','monitoring_rebind')),
    'deploy/scripts/manage-totp-cleanup.sh','deploy/scripts/manage-monitoring-rebind.sh')


def revision(value):
    ops.require(isinstance(value,str) and re.fullmatch('[0-9a-f]{40}',value))
    return value


def directory(path):
    ops.protected_parents(path/'sentinel')
    ops.require(stat.S_IMODE(path.stat().st_mode)==0o700)


def sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def create_directory(path):
    ops.protected_parents(path)
    path.mkdir(mode=0o700)
    directory(path); sync_directory(path.parent)


def optional(path, *, mode=0o600):
    return ops.read_protected(path,modes=(mode,)) if path.exists() or path.is_symlink() else None


def installed(source, *, successor):
    release=RELEASES/revision(source)
    complete=ops.strict_json(ops.read_protected(release/'handoff-installation/complete.json'))
    ops.require(complete.get('commit')==source and complete.get('phase')=='installed_not_activated'
        and complete.get('host_role')=='db' and complete.get('system_python_only') is True
        and complete.get('native_tests')=='passed_no_skips')
    manifest=ops.strict_json(ops.read_protected(release/'handoff-installation/source-manifest.json',limit=1048576))
    ops.require(isinstance(manifest,dict))
    names=(*NEW_FILES,*('deploy/systemd/'+n for n in NEW_UNITS))
    result={}
    for name in names:
        raw=ops.read_protected(release/name,modes=(0o644,0o750,0o755),limit=131072)
        ops.require(hashlib.sha256(raw).hexdigest()==manifest.get(name))
        result[name]=raw
    return result


def terminal(old, new):
    """No stop/disable side effects: operator must arrange the approved hold."""
    for source, kinds in ((old,OLD_KINDS),(new,NEW_KINDS)):
        for kind in kinds:
            for suffix in ('service','timer'):
                unit='careertrellis-'+kind+'@'+source+'.'+suffix
                raw=purge.command(['/usr/bin/systemctl','show','--all',unit,
                    '--property=LoadState,ActiveState,SubState,UnitFileState,Job'+
                    (',MainPID,ControlPID,ControlGroup' if suffix=='service' else '')]).decode()
                props=dict(line.split('=',1) for line in raw.splitlines())
                fields={'LoadState','ActiveState','SubState','UnitFileState','Job'}
                terminal_states = (('inactive','dead'),('failed','failed')) if suffix=='service' else (('inactive','dead'),)
                ops.require(fields <= set(props) <= fields|{'MainPID','ControlPID','ControlGroup'}
                    and props['LoadState'] in ('loaded','not-found')
                    and (props['ActiveState'],props['SubState']) in terminal_states
                    and props['Job']=='')
                if suffix=='service' and props['LoadState']=='loaded':
                    ops.require(props.get('MainPID')=='0' and props.get('ControlPID')=='0')
                    group=props.get('ControlGroup')
                    ops.require(isinstance(group,str))
                    if group:
                        ops.require(group.startswith('/') and '..' not in Path(group).parts)
                        events=(Path('/sys/fs/cgroup')/group.lstrip('/')/'cgroup.events').read_text()
                        ops.require(dict(line.split() for line in events.splitlines()).get('populated')=='0')
                if suffix=='timer':
                    ops.require(props['UnitFileState'] in ('disabled','')
                        and not (UNITS/'timers.target.wants'/unit).exists()
                        and not (UNITS/'timers.target.wants'/unit).is_symlink())


def target_paths():
    return {'admission-state':(purge.STATE/'state.json',0o600),
        'admission-health':(purge.STATE/'health.json',0o600),
        'reporter-config':(reporter.CONFIG,0o600),
        'cleanup-state':(cleanup.STATE/'state.json',0o600),
        'cleanup-health':(cleanup.STATE/'health.json',0o600),
        **{name:(UNITS/name,0o644) for name in NEW_UNITS}}


def transform(state_raw, health_raw, config_raw, cleanup_raw, cleanup_health_raw, old, new, now):
    revision(old); revision(new); ops.require(old!=new and purge.integer(now))
    state=purge.validate(ops.strict_json(state_raw),old,now)
    ops.require(state['schema']==purge.SCHEMA_V2 and (state['last_attempt'] is None
        or state['last_attempt']['result']!='running'))
    ops.require(purge.validate_health(ops.strict_json(health_raw),old,now)['schema']==purge.SCHEMA_V2)
    config=reporter.settings(config_raw,old); ops.require(config['version']==2)
    scrub=cleanup.validate(ops.strict_json(cleanup_raw),old,now)
    ops.require(scrub['last_attempt'] is None or scrub['last_attempt']['result']!='running')
    ops.require(cleanup.validate_health(ops.strict_json(cleanup_health_raw),old,now)['schema']==scrub['schema'])
    # History is preserved verbatim in the archive and structurally in the new
    # state. No historical success is credited to the successor source.
    state={**state,'schema':purge.SCHEMA_V2,'source':new,'source_bound_at':now,'source_success':None}
    health=purge.health(state,now)
    health['issues'] += ['purge_timer_unavailable','purge-monitor_timer_unavailable']
    health['result']='alert'
    scrub={**scrub,'schema':cleanup.SCHEMA_V2,'source':new,'source_bound_at':now,'source_success':None}
    scrub_health=cleanup.health(scrub,now)
    scrub_health['issues'] += ['cleanup_timer_unavailable','cleanup-monitor_timer_unavailable']
    return {'admission-state':ops.encoded(state),'admission-health':ops.encoded(health),
        'reporter-config':ops.encoded({**config,'version':2,'source':new}),
        'cleanup-state':ops.encoded(scrub),'cleanup-health':ops.encoded(scrub_health)}


def snapshot(old_files):
    paths=target_paths(); result={name:optional(path,mode=mode) for name,(path,mode) in paths.items()}
    ops.require(all(raw is not None for raw in result.values()))
    directory(cleanup.STATE)
    for name in NEW_UNITS:
        expected=old_files.get('deploy/systemd/'+name)
        ops.require(result[name]==expected)
    return result


def stage(old,new,now,old_files,new_files):
    ops.require(not purge.rebind_pending())
    before=snapshot(old_files)
    after=transform(before['admission-state'],before['admission-health'],before['reporter-config'],
        before['cleanup-state'],before['cleanup-health'],old,new,now)
    after.update({name:new_files['deploy/systemd/'+name] for name in NEW_UNITS})
    archive=purge.STATE/'successors'
    if not archive.exists(): create_directory(archive)
    directory(archive)
    evidence=archive/new
    create_directory(evidence)  # Exclusive; incomplete staging is never adopted.
    plan={'version':1,'previous':old,'successor':new,'created_at':now,
        'absent_before':sorted(name for name,raw in before.items() if raw is None)}
    ops.write_protected(evidence/'plan.json',ops.encoded(plan))
    for name in target_paths():
        if before[name] is not None: ops.write_protected(evidence/(name+'.before'),before[name])
        ops.write_protected(evidence/(name+'.after'),after[name])
    previous_report=optional(purge.STATE/'reporter.json')
    if previous_report is not None: ops.write_protected(evidence/'previous-reporter.json',previous_report)
    # Seal only after all exact originals/targets are durable and unchanged.
    ops.require(before=={name:optional(path,mode=mode) for name,(path,mode) in target_paths().items()})
    ops.write_protected(evidence/'ready.json',ops.encoded(plan))
    return evidence


def load(evidence,old,new,now,new_files):
    directory(evidence)
    raw=ops.read_protected(evidence/'ready.json')
    ops.require(raw==ops.read_protected(evidence/'plan.json'))
    plan=ops.strict_json(raw)
    expected_absent=[]
    ops.require(isinstance(plan,dict) and set(plan)=={'version','previous','successor','created_at','absent_before'}
        and type(plan['version']) is int and plan['version']==1 and plan['previous']==old and plan['successor']==new
        and purge.integer(plan['created_at']) and plan['created_at']<=now and plan['absent_before']==expected_absent)
    before={name:None if name in plan['absent_before'] else ops.read_protected(evidence/(name+'.before')) for name in target_paths()}
    after={name:ops.read_protected(evidence/(name+'.after')) for name in target_paths()}
    derived=transform(before['admission-state'],before['admission-health'],before['reporter-config'],
        before['cleanup-state'],before['cleanup-health'],old,new,plan['created_at'])
    derived.update({name:new_files['deploy/systemd/'+name] for name in NEW_UNITS})
    ops.require(after==derived)
    return raw,before,after


def publish(evidence,old,new,now,new_files):
    raw,before,after=load(evidence,old,new,now,new_files)
    pending=purge.STATE/'rebind-pending.json'
    marker=optional(pending)
    ops.require(marker is None or marker==raw)
    # Inspect every path first; no partial overwrite when any drift is visible.
    for name,(path,mode) in target_paths().items():
        ops.require(optional(path,mode=mode) in (before[name],after[name]))
    terminal(old,new)
    if marker is None: ops.write_protected(pending,raw)
    directory(cleanup.STATE)
    for name,(path,mode) in target_paths().items():
        current=optional(path,mode=mode)
        ops.require(current in (before[name],after[name]))
        if current!=after[name]: ops.write_protected(path,after[name],previous=current,mode=mode)
    # Reload reads published units; never starts/enables/stops any service.
    purge.command(['/usr/bin/systemctl','daemon-reload'])
    terminal(old,new)
    ops.require(all(optional(path,mode=mode)==after[name] for name,(path,mode) in target_paths().items()))
    complete=ops.encoded({'version':1,'previous':old,'successor':new,'phase':'published_inactive_unverified'})
    completion=evidence/'complete.json'
    if completion.exists(): ops.require(ops.read_protected(completion)==complete)
    else: ops.write_protected(completion,complete)
    ops.require(ops.read_protected(pending)==raw)
    pending.unlink(); sync_directory(pending.parent)
    return 'published_inactive_unverified'


def execute(old,new,*,reconcile=False,clock=time.time):
    revision(old); revision(new); ops.require(old!=new)
    directory(purge.STATE)
    with ExitStack() as locks:
        for name in ('rebind.lock','purge.lock','monitor.lock','reporter.lock'):
            locks.enter_context(purge.state_lock(purge.STATE/name))
        directory(cleanup.STATE)
        for name in ('cleanup.lock','monitor.lock'):
            locks.enter_context(purge.state_lock(cleanup.STATE/name))
        locks.enter_context(ops.host_locks())
        old_files,new_files=installed(old,successor=False),installed(new,successor=True)
        terminal(old,new)
        now=math.floor(clock())
        evidence=purge.STATE/'successors'/new
        if not reconcile: evidence=stage(old,new,now,old_files,new_files)
        else:
            # Reconciliation cannot grant a new plan or adopt an unsealed attempt.
            raw,before,_=load(evidence,old,new,now,new_files)
            for name in OLD_UNITS: ops.require(before[name]==old_files['deploy/systemd/'+name])
        return publish(evidence,old,new,now,new_files)


def main(argv=None):
    try:
        args=sys.argv[1:] if argv is None else argv
        ops.require(len(args)==2 and args[0] in ('apply','reconcile') and os.geteuid()==0
            and os.uname().nodename=='job-applier-db-staging-01')
        source=Path(__file__).absolute(); release=source.parent.parent
        ops.require(release.parent==RELEASES)
        outcome=execute(revision(args[1]),revision(release.name),reconcile=args[0]=='reconcile')
        print('monitoring_handoff='+outcome)
        return 0
    except BaseException:
        print('Monitoring handoff incomplete; retain evidence and keep timers held.',file=sys.stderr)
        return 2


if __name__=='__main__': raise SystemExit(main())
