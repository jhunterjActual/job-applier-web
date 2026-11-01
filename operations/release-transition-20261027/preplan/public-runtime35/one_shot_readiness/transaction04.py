"""Readonly exact app04 participant custody; never executes the transaction.

Does not require a global terminal certificate before it can exist. Final core
policy/status mismatches remain genuine failures; this module cannot waive them.
"""
from datetime import datetime,timezone,timedelta
import os,re,stat,time
from pathlib import Path
from collector import need,decode,sha,canonical,ROOT,BASE,public_document,historical_terminal

PACKET=Path('/opt/careertrellis/staging/pr98-117-release-ops-20261026')
JOURNAL=Path('/var/backups/careertrellis/app-release-transition-20261026')
TARGET='bc68ea0dc253425a44e268452130becdf35d764e'
CORRECTION='f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
def digest(value):
    # App packet digest is compact UTF8 WITHOUT LF; policy digest remains
    # exact493 ASCII+LF in its separately authenticated common.Policy.
    import json
    return sha(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
def stamp(value):
    t=datetime.fromisoformat(value.replace('Z','+00:00'));need(t.tzinfo is not None);return t

def launch_argv(role,binding):
    need(role in ('web','db'))
    script=str(PACKET/('web_coordinator.py' if role=='web' else 'peer_server.py'))
    result=['/usr/bin/python3','-I','-S','-B',script,'--plan',str(PACKET/'plan.json'),
        '--approved-plan-sha256',binding['plan_sha256'],'--source-commit',binding['source_commit']]
    if role=='db':result+=['--role','db']
    return [x.encode() for x in result]

def validate_launch(cmd,role,binding):need(cmd==launch_argv(role,binding))

def actor(pid,expected_ticks,role,binding):
    need(type(pid) is int and pid>0)
    p=Path('/proc')/str(pid);before=(p/'stat').read_text().rsplit(')',1)[1].split()
    uid=[x.split()[1:] for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')]
    need(uid==[['0','0','0','0']] and before[0]!='Z' and int(before[19])==expected_ticks)
    cmd=(p/'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    # Exact retained04 drive.command launch shape, not membership of data args.
    validate_launch(cmd,role,binding)
    expected=Path('/usr/bin/python3').stat();executing=(p/'exe').stat()
    need(stat.S_ISREG(expected.st_mode) and expected.st_uid==0 and not expected.st_mode&0o022
         and (expected.st_dev,expected.st_ino)==(executing.st_dev,executing.st_ino))
    after=(p/'stat').read_text().rsplit(')',1)[1].split()
    need(after[0]!='Z' and (after[1],after[2],after[3],after[19])==(before[1],before[2],before[3],before[19]))
    return {'pid':pid,'start_ticks':expected_ticks,'boot':binding['boots'][role]}

def intent_bound(intent,plan,binding,role,pid,ticks,now,budget):
    need(set(intent)=={'schema','plan_sha256','role','boot','pid','parent_pid','process_start_ticks','packet_sha256',
         'base','target','correction','started_at','started_monotonic_us','deadline'})
    need(intent['schema']=='careertrellis.app-release-intent/v1' and intent['plan_sha256']==binding['plan_sha256']
        and intent['role']==role and intent['boot']==binding['boots'][role] and intent['pid']==pid
        and int(intent['process_start_ticks'])==ticks and intent['packet_sha256']==plan['packet_sha256']
        and intent['base']==BASE and intent['target']==TARGET and intent['correction']==CORRECTION
        and intent['deadline']==plan['expires_at'] and type(budget) is int and budget>0)
    start=stamp(intent['started_at']);expiry=stamp(intent['deadline'])
    need(start<expiry<=start+timedelta(hours=1) and start<=now<min(expiry,start+timedelta(seconds=budget)))
    need(type(intent['started_monotonic_us']) is int and 0<=time.monotonic_ns()//1000-intent['started_monotonic_us']<budget*1000000)
    return start,expiry

def collect(reader,role,binding,common):
    plan=reader.json(PACKET/'plan.json');need(digest(plan)==binding['plan_sha256']
        and plan['boots']==binding['boots'] and plan['base']==BASE and plan['target']==TARGET and plan['correction']==CORRECTION)
    manifest_raw=reader.read(PACKET/'packet_manifest.json');manifest=decode(manifest_raw)
    need(sha(manifest_raw)==plan['packet_sha256'] and manifest['schema']=='careertrellis.app-release-packet/v1'
        and manifest['source_commit']==binding['source_commit'])
    expected=public_document('transaction04-pins.json')
    # Every immutable executable source member is pinned to preserved03, not a
    # mutable packet/worktree or a caller-supplied verification boolean.
    for name,pin in expected['files'].items():
        actual=manifest['modules'].get(name[:-3]) if name[:-3] in manifest['modules'] else manifest['scripts'].get(name)
        need(actual==pin and sha(reader.read(PACKET/name,0o600))==pin)
    now=datetime.now(timezone.utc)
    need(now<stamp(plan['expires_at']))
    paths=[ROOT/role/'control.lock',Path('/run/lock/careertrellis-maintenance.lock'),
        Path('/run/lock/careertrellis-generated-object-recovery.lock' if role=='web' else '/run/lock/careertrellis-pgbackrest-repository.lock')]
    rows=Path('/proc/locks').read_text().splitlines();owners=[]
    for path in paths:
        s=path.lstat();need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022)
        key=f'{os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x}:{s.st_ino}'
        selected=[x.split() for x in rows if len(x.split())>=6 and x.split()[5].lower()==key.lower()]
        need(len(selected)==1 and selected[0][1:4]==['FLOCK','ADVISORY','WRITE']);owners.append(int(selected[0][4]))
    need(len(set(owners))==1);pid=owners[0]
    ticks=int((Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19])
    owner=actor(pid,ticks,role,binding)
    reader.checks.append(lambda:need(actor(pid,ticks,role,binding)==owner))
    def fences_still_owned():
        current=Path('/proc/locks').read_text().splitlines()
        for path in paths:
            s=path.lstat();key=f'{os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x}:{s.st_ino}'
            selected=[x.split() for x in current if len(x.split())>=6 and x.split()[5].lower()==key.lower()]
            need(len(selected)==1 and selected[0][1:4]==['FLOCK','ADVISORY','WRITE'] and int(selected[0][4])==pid)
    reader.checks.append(fences_still_owned)
    private=common.Policy(reader.json('/etc/careertrellis/paired-maintenance/policy.json'))
    public=common.Policy(reader.json(ROOT/'public-policy.json',0o644));need(private.digest==public.digest)
    need(private.data['enabled'] is True and private.data['manifest_revision']=='493bf14a3bae2cb2b36f3a57191602b72ea6e3d3'
        and private.data['schema_revision']=='20261005_0023'
        and stamp(private.data['valid_from'])<=now<stamp(private.data['valid_until']))
    if binding['phase']=='preflight':
        need(not os.path.lexists(JOURNAL) and private.digest==plan['old_policy_digests'][role])
        historical_terminal(reader,private,reader.json(ROOT/role/'status.json'),now)
        return {'scheduler_verified':True,'mode':'participant-preclaim','owner':owner,'policy_sha256':private.digest,
            'schema_revision':private.data['schema_revision']}
    intent=reader.json(JOURNAL/'intent.json')
    start,expiry=intent_bound(intent,plan,binding,role,pid,ticks,now,private.data['planned_stop_budget_seconds'])
    need(not os.path.lexists(JOURNAL/'contained-inspection-required.json'))
    # Exact original policy supplies the unchanged limits/scope. No observer or
    # recovery phase is allowed to invent a later start or a new outage budget.
    saved={}
    for i in range(4 if role=='web' else 3):
        value=reader.json(JOURNAL/('original-'+str(i)+'.json'))
        need(set(value)=={'path','raw_hex'} and value['path'] not in saved);saved[value['path']]=bytes.fromhex(value['raw_hex'])
    old=common.Policy(decode(saved['/etc/careertrellis/paired-maintenance/policy.json']))
    scope=lambda d:{k:v for k,v in d.items() if k not in ('application_release','application_build')}
    need(scope(old.data)==scope(private.data) and old.digest==plan['old_policy_digests'][role])
    historical_terminal(reader,old,decode(saved[str(ROOT/role/'status.json')]),now)
    selected=binding['release'];need(selected in (BASE,TARGET,CORRECTION) and binding['build']==plan['builds'][selected])
    predecessor=BASE
    if selected==CORRECTION:
        recovery=reader.json(JOURNAL/'recovery-started.json')
        need(set(recovery)=={'schema','plan_sha256','observed_at','predecessor'} and recovery['schema']=='careertrellis.app-recovery-epoch/v1'
            and recovery['plan_sha256']==binding['plan_sha256'] and recovery['predecessor']==TARGET
            and start<=stamp(recovery['observed_at'])<=now);predecessor=TARGET
    elif selected==BASE:
        recovery=reader.json(JOURNAL/'recovery-started.json')
        need(recovery['schema']=='careertrellis.app-recovery-epoch/v1' and recovery['plan_sha256']==binding['plan_sha256']
             and recovery['predecessor']==BASE and start<=stamp(recovery['observed_at'])<=now)
    else:need(not os.path.lexists(JOURNAL/'recovery-started.json'))
    need(binding['predecessor']==predecessor)
    actual=private.data['application_release']
    need(actual==(predecessor if binding['phase']=='stopped' else selected))
    expected_digest=plan['old_policy_digests'][role] if actual==BASE else plan['new_policy_digests'][actual][role]
    need(private.digest==expected_digest)
    status=reader.json(ROOT/role/'status.json')
    need(status['boot_id']==binding['boots'][role] and status['policy_sha256']==private.digest)
    if binding['phase']=='verifying':
        need(status['schema']=='careertrellis.paired-maintenance-status/v1' and status['release']==selected
            and status['phase']=='verifying' and status['result']=='running' and status['services_restored'] is True
            and status['observed_at']==intent['started_at'] and 'transaction' not in status)
        verifying=reader.json(JOURNAL/('verifying-'+selected+'-intent.json'))
        need(set(verifying)=={'schema','plan_sha256','release','role','original_started_at','original_started_monotonic_us','service_proof'}
            and verifying['schema']=='careertrellis.app-verifying-projection/v1'
            and verifying['plan_sha256']==binding['plan_sha256'] and verifying['release']==selected and verifying['role']==role
            and verifying['original_started_at']==intent['started_at']
            and verifying['original_started_monotonic_us']==intent['started_monotonic_us'])
        proof=verifying['service_proof']
        need(set(proof)=={'schema','plan_sha256','release','build','web_boot','units'} and proof['schema']=='careertrellis.app-stable-services/v1'
             and proof['plan_sha256']==binding['plan_sha256'] and proof['release']==selected and proof['build']==binding['build']
             and proof['web_boot']==binding['boots']['web'] and set(proof['units'])=={'careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service'})
        for name,v in proof['units'].items():
            need(set(v)=={'ActiveState','MainPID','NRestarts'} and v['ActiveState']=='active' and v['NRestarts']=='0'
                and type(v['MainPID']) is str and v['MainPID'].isdigit() and int(v['MainPID'])>0)
            if role=='web':
                unit=reader.unit(name);need(unit['MainPID']==v['MainPID'] and unit['NRestarts']=='0')
        if selected!=BASE:
            phase=reader.json(JOURNAL/('policy_selected-'+selected+'.json'))
            need(set(phase)=={'phase','release','observed_at'} and phase['phase']=='policy_selected' and phase['release']==selected
                and start<=stamp(phase['observed_at'])<=now)
    elif binding['phase']=='final':
        need(status['schema']=='careertrellis.paired-maintenance-status/v1' and status['release']==selected
            and status['phase']=='idle' and status['result']=='ok' and status['services_restored'] is True)
        outcome={BASE:'aborted_before_switch',TARGET:'target_healthy',CORRECTION:'corrected'}[selected]
        completion=reader.json(JOURNAL/('completion-'+selected+'-'+outcome+'-success.json'))
        need(completion=={'result':outcome,'release':selected,'role':role,'plan_sha256':binding['plan_sha256'],'services_restored':True})
    if role=='web':
        need(os.readlink('/opt/careertrellis/staging/current').rstrip('/').endswith('/'+actual))
        marker=reader.json(ROOT/'writer-interlock.json',0o644)
        need(marker['schema']=='careertrellis.paired-maintenance-interlock/v1' and marker['boot_id']==binding['boots']['web']
             and marker['policy_sha256']==private.digest and marker['release']==actual
             and marker['phase']==('blocked' if binding['phase']=='stopped' else 'idle'))
        for name in ('careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service'):
            unit=reader.unit(name)
            if binding['phase']=='stopped':need(unit['ActiveState']=='inactive' and unit['MainPID']==unit['ControlPID']=='0' and unit['Job']=='')
            else:need(unit['ActiveState']=='active' and unit['SubState']=='running' and unit['Result']=='success')
    # No global-terminal/local-completion requirement here: those records are
    # downstream of final readiness. Actual canonical core failures still win.
    return {'scheduler_verified':True,'mode':'owned-'+binding['phase'],'owner':owner,
        'policy_sha256':private.digest,'schema_revision':private.data['schema_revision'],
        'predecessor':predecessor,'selected':selected,'intent_started_at':intent['started_at'],'deadline':intent['deadline'],
        'service_proof_sha256':digest(proof) if binding['phase']=='verifying' else None}
