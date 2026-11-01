"""Closed fixed repair98 ledger contract. Import performs no host operations."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import urllib.request

REV='f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS='4095417909a8b13ad252be9420e422af3662bef5'
OLD_OPS='291d1b4b58bb17448395012408556090bea5ede1'
ATTEMPT_ID='repair98-fixture-g1-20261027'
RELEASE=Path('/opt/careertrellis/staging/releases')/REV
DOMAIN=Path('/var/backups/careertrellis/preparation98-repair-20261027/repair98-fixture-g1')
STAGE=Path('/opt/careertrellis/staging/preparation98-repair-source-20261027/repair98-fixture-g1')
WORK=Path('/opt/careertrellis/staging/preparation98-repair-work-20261027/repair98-fixture-g1')
CODE=Path('/opt/careertrellis/staging/preparation98-repair-code-20261027/repair98-fixture-g1')
REPOSITORY='/srv/careertrellis/job-applier-web'
SOURCE='operations/release-transition-20261027/repair98/'
UNIT='careertrellis-preparation98-repair-fixture-g1-20261027'
CHECKS={'p0','objects','ai','syntax','authority_native','systemd_native','runtime_isolation'}
SOURCE_FILES={'repair_contract.py','repair_coordinator.py','repair_owner.py','repair_seal.py','repair_preflight.py','fixture_relocation.py','run_checks_repair98.py'}

SCHEMAS={
 'source-manifest.json':('careertrellis.repair98-source-manifest/v1',{'schema','repair_ops_commit','files'}),
 'repair-plan.json':('careertrellis.repair98-plan/v1',{'schema','attempt_id','release','build','baseline','previous','repair_ops_commit','source_manifest_sha256','old_failure_chain_sha256','full_live_preflight_sha256','retained_runtime_manifest_sha256','source_archive_sha256','source_diff_sha256','decision_at','expires_at'}),
 'repair-decision.json':('careertrellis.repair98-decision/v1',{'schema','attempt_id','decision_at','expires_at','repair_plan_sha256','source_manifest_sha256','full_live_preflight_sha256'}),
 'full-preflight.json':('careertrellis.repair98-preflight/v1',{'schema','attempt_id','observed_at','boot_id','baseline','previous','live_build','authority','source_archive_sha256','source_diff_sha256','runtime_manifest_sha256','browser_manifest_sha256','source_manifest_sha256','old_failure_chain_sha256','capacity','units','locks_clear','result'}),
 'candidate.json':('careertrellis.repair98-candidate/v1',{'schema','attempt_id','release','build','repair_ops_commit','repair_plan_sha256','repair_decision_sha256','source_manifest_sha256','old_failure_chain_sha256','full_live_preflight_sha256','checks_sha256','unit_terminal_sha256','runtime_before_sha256','runtime_after_sha256','browser_before_sha256','browser_after_sha256','completed_utc','result'}),
 'owner.json':('careertrellis.repair98-owner/v1',{'schema','attempt_id','pid','start_ticks','boot_id','repair_plan_sha256','repair_decision_sha256','started_at'}),
 'coordinator.json':('careertrellis.repair98-coordinator/v1',{'schema','attempt_id','pid','start_ticks','boot_id','owner_pid','started_at'}),
 'child-spawn.json':('careertrellis.repair98-child-spawn/v1',{'schema','attempt_id','pid','start_ticks','boot_id','owner_pid','repair_plan_sha256','repair_decision_sha256','source_manifest_sha256'}),
 'test-client.json':('careertrellis.repair98-test-client/v1',{'schema','attempt_id','pid','start_ticks','boot_id','unit','unit_intent_sha256'}),
 'unit-intent.json':('careertrellis.repair98-unit-intent/v1',{'schema','attempt_id','unit','argv','sandbox_properties','runner_sha256','qualifier_sha256','repair_plan_sha256','repair_decision_sha256','source_manifest_sha256','boot_id','coordinator_pid','coordinator_start_ticks','created_at','description_binding'}),
 'unit-terminal.json':('careertrellis.repair98-unit-terminal/v1',{'schema','attempt_id','unit','execution','after','cgroup_after','client_pid','client_start_ticks','client_exit','boot_id','observed_at','unit_intent_sha256','test_client_sha256'}),
 'owner-terminal.json':('careertrellis.repair98-owner-terminal/v1',{'schema','attempt_id','owner','coordinator','coordinator_exit','boot_id','repair_plan_sha256','repair_decision_sha256','candidate_sha256','checks_sha256','unit_terminal_sha256','runtime_after_sha256','source_manifest_sha256','captures','ended_at','owner_self_exit_claim','child_spawn_sha256'}),
 'postterminal-owner-proof.json':('careertrellis.repair98-postterminal-proof/v1',{'schema','attempt_id','observed_at','boot_id','owner_terminal_sha256','candidate_sha256','owner_state','coordinator_state','unit','cgroup_after','runtime_manifest_sha256','browser_manifest_sha256','original_deadline','source_manifest_sha256'}),
}

def need(value):
    if not value:raise RuntimeError('CLOSED_REPAIR98_CONTRACT')
def utc():return dt.datetime.now(dt.timezone.utc)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def pairs(items):
    value={}
    for k,v in items:need(k not in value);value[k]=v
    return value
def decode(raw):return json.loads(raw.decode('utf8'),object_pairs_hook=pairs)
def closed(name,value):
    schema,keys=SCHEMAS[name];need(type(value) is dict and set(value)==keys and value['schema']==schema)
    if 'attempt_id' in value:need(value['attempt_id']==ATTEMPT_ID)
    return value
def parents(path):
    for parent in path.parents:
        st=parent.lstat();need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022)
def read(path,limit=16777216):
    parents(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd);need(stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_nlink==1 and not st.st_mode&0o022 and st.st_size<=limit)
        identity=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_mode,st.st_uid)
        with os.fdopen(os.dup(fd),'rb') as stream:raw=stream.read(st.st_size+1)
        after=os.fstat(fd);leaf=path.lstat()
        need(len(raw)==st.st_size and identity==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_mode,after.st_uid)
            and identity==(leaf.st_dev,leaf.st_ino,leaf.st_size,leaf.st_mtime_ns,leaf.st_mode,leaf.st_uid));return raw
    finally:os.close(fd)
def create(path,value):
    parents(path);raw=value if type(value) is bytes else canonical(value)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(os.dup(fd),'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    finally:os.close(fd)
    sync_dir(path.parent);need(read(path)==raw);return sha(raw)
def sync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
def record(name,value):closed(name,value);return create(DOMAIN/name,value)
def boot():
    fd=os.open('/proc/sys/kernel/random/boot_id',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        raw=os.read(fd,4096);need(re.fullmatch(rb'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\n?',raw));return raw.decode().strip()
    finally:os.close(fd)
def birth(pid):
    need(type(pid) is int and pid>1)
    with Path('/proc',str(pid),'stat').open('rb') as stream:raw=stream.read(16385)
    need(len(raw)<=16384);return raw.rsplit(b')',1)[1].split()[19].decode()
def absent_original(value,current_boot):
    need(value['boot_id']==current_boot and type(value['pid']) is int and type(value['start_ticks']) is str)
    try:actual=birth(value['pid'])
    except FileNotFoundError:return 'absent'
    need(actual!=value['start_ticks']);return 'pid_reused_original_absent'
def bootstrap():
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname()=='careertrellis-staging-web-1'
        and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)
def run(args,limit=16777216):
    p=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,timeout=60,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','GIT_OPTIONAL_LOCKS':'0'})
    need(p.returncode==0 and len(p.stdout)<=limit);return p.stdout
def original_helpers():
    raw=run(['git','-C',REPOSITORY,'cat-file','blob',OLD_OPS+':operations/release-transition-20261026/preparation/prepare_release.py'])
    need(sha(raw)=='a0d3db047dda8062969ba7005054ab147dd2ccae6efab5a9b4789b2137b852b3')
    ns={'__name__':'immutable_291d_primitives'};exec(compile(raw,'verified291d','exec'),ns);return ns
def load_controls(plan_sha,decision_sha):
    raw={n:read(STAGE/n) for n in ('source-manifest.json','repair-plan.json','repair-decision.json','full-preflight.json','old-failure-chain.json','baseline-provenance.json')}
    plan=closed('repair-plan.json',decode(raw['repair-plan.json']));decision=closed('repair-decision.json',decode(raw['repair-decision.json']))
    manifest=closed('source-manifest.json',decode(raw['source-manifest.json']));preflight=closed('full-preflight.json',decode(raw['full-preflight.json']))
    need(sha(raw['repair-plan.json'])==plan_sha and sha(raw['repair-decision.json'])==decision_sha
        and decision['repair_plan_sha256']==plan_sha and decision['source_manifest_sha256']==plan['source_manifest_sha256']==sha(raw['source-manifest.json'])
        and decision['full_live_preflight_sha256']==plan['full_live_preflight_sha256']==sha(raw['full-preflight.json'])
        and sha(raw['old-failure-chain.json'])==plan['old_failure_chain_sha256']
        and sha(raw['baseline-provenance.json'])=='b4c21424441e63b45d484c2ff063afae394c745fd0a235c7210c11beecb05ee7')
    need(plan['release']==REV and plan['build']=='20261026.98' and plan['baseline']==BASE and plan['previous']==PREVIOUS
        and manifest['repair_ops_commit']==plan['repair_ops_commit']!=OLD_OPS and re.fullmatch('[0-9a-f]{40}',plan['repair_ops_commit']))
    start=dt.datetime.fromisoformat(plan['decision_at']);deadline=dt.datetime.fromisoformat(plan['expires_at'])
    observed=dt.datetime.fromisoformat(preflight['observed_at'])
    need(observed<=start<=utc()<deadline<=start+dt.timedelta(minutes=55) and (start-observed).total_seconds()<=300
        and decision['decision_at']==plan['decision_at'] and decision['expires_at']==plan['expires_at'])
    need(preflight['boot_id']==boot() and preflight['result']=='pass' and preflight['baseline']==BASE
        and preflight['previous']==PREVIOUS and preflight['live_build']=='20261017.96'
        and preflight['authority']=='postgres-r2-authoritative' and preflight['locks_clear'] is True
        and preflight['source_manifest_sha256']==plan['source_manifest_sha256']
        and preflight['old_failure_chain_sha256']==plan['old_failure_chain_sha256']
        and preflight['runtime_manifest_sha256']==plan['retained_runtime_manifest_sha256']
        and preflight['source_archive_sha256']==plan['source_archive_sha256']=='67b392f0bd1c66b3391aa6e4c7f18b90b0a17fe7da72a4b5c32e0da594afb327'
        and preflight['source_diff_sha256']==plan['source_diff_sha256']=='ad8f04dbf0afeeabb9ec1d3557cc5e709b4ebc0488490424950df83843beda12')
    need(set(manifest['files'])==SOURCE_FILES)
    for name,expected in manifest['files'].items():
        need(set(expected)=={'bytes','sha256'})
        source=read(STAGE/name);need(len(source)==expected['bytes'] and sha(source)==expected['sha256']
            and source==run(['git','-C',REPOSITORY,'cat-file','blob',plan['repair_ops_commit']+':'+SOURCE+name]))
    return plan,decision,manifest,preflight,raw

def validate_source_manifest(raw):
    value=closed('source-manifest.json',decode(raw));need(set(value['files'])==SOURCE_FILES
        and re.fullmatch('[0-9a-f]{40}',value['repair_ops_commit']) and value['repair_ops_commit']!=OLD_OPS)
    for name,spec in value['files'].items():
        need(set(spec)=={'bytes','sha256'} and type(spec['bytes']) is int and 0<spec['bytes']<=1048576
            and re.fullmatch('[0-9a-f]{64}',spec['sha256']))
        source=read(STAGE/name);need(len(source)==spec['bytes'] and sha(source)==spec['sha256']
            and source==run(['git','-C',REPOSITORY,'cat-file','blob',value['repair_ops_commit']+':'+SOURCE+name]))
    return value

def authenticate_old_failure(raw):
    value=decode(raw);need(set(value)=={'schema','release','old_plan_sha256','files'}
        and value['schema']=='careertrellis.repair98-old-failure-chain/v1' and value['release']==REV
        and value['old_plan_sha256']=='82d42d67294c24feb13cd31ba17f0369ca220137ae3fd6471d401340be009a0e')
    old=Path('/var/backups/careertrellis/app-release-preparation-20261026')/(REV+'-attempt')
    stage=Path('/opt/careertrellis/staging/pr98-117-release-preparation-20261026')
    paths={'intent':old/'intent.json','failed':old/'failed-inspection-required.json','dispatch':stage/'dispatch-terminal.json',
        'log':old/'synthetic-tests.log','classifier02':STAGE/'classifier02.json'}
    need(set(value['files'])==set(paths))
    observed={name:read(path,1048576) for name,path in paths.items()}
    need(all(sha(data)==value['files'][name] for name,data in observed.items())
        and sha(observed['log'])=='b494486137d1a4383c9181b10cb043dcbbc704c1847a176ceb0db7e9e8b2425c')
    # classifier02.json is a FUTURE reviewed exact copy, not an established host input.
    need(sha(observed['classifier02'])=='d49e8c5ab6dc7e40f782d9524e8748635fb41b8e8e83b6a8a87215e844edd134')
    failure=decode(observed['dispatch']);intent=decode(observed['intent']);classification=decode(observed['classifier02'])
    need(failure['exit_code']==1 and failure['revision']==REV and failure['plan_sha256']==value['old_plan_sha256']
        and intent['revision']==REV and intent['plan_sha256']==value['old_plan_sha256']
        and classification['result']=='TERMINAL_FAILURE_CLASSIFIED' and classification['accepted'] is False
        and classification['classification']['numeric_errno']==[30]
        and classification['classification']['public_failed_test_ids']==['test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables'])
    current=boot()
    owner=decode(read(stage/'dispatch-owner.json'));child=decode(read(stage/'dispatch-client.json'))
    absent_original({'pid':owner['pid'],'start_ticks':owner['start_ticks'],'boot_id':owner['boot_id']},current)
    absent_original({'pid':child['pid'],'start_ticks':child['start_ticks'],'boot_id':owner['boot_id']},current)
    old_unit='careertrellis-release-prep-'+REV[:12]+'-20261026'
    result=subprocess.run(['systemctl','show',old_unit,'--property=LoadState','--value'],stdin=subprocess.DEVNULL,capture_output=True,timeout=15)
    need(result.returncode in (0,1,4) and result.stdout.strip()==b'not-found'
        and not Path('/sys/fs/cgroup/system.slice/'+old_unit+'.service').exists())
    return value

def locks_clear(allow_self=False):
    paths=['/run/lock/careertrellis-'+name+'.lock' for name in ('maintenance','pilot-auth-acceptance','postgres-migration',
        'sqlite-import','provider-secret-rotation','generated-object-recovery')]
    with Path('/proc/locks').open('rb') as stream:raw=stream.read(1048577)
    need(len(raw)<=1048576)
    for name in paths:
        path=Path(name);s=path.lstat();need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022)
        for line in raw.decode().splitlines():
            items=line.split()
            for i,item in enumerate(items):
                if re.fullmatch('[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9]+',item):
                    major,minor,inode=item.split(':')
                    if (int(major,16),int(minor,16),int(inode))==(os.major(s.st_dev),os.minor(s.st_dev),s.st_ino):
                        need(allow_self and name in paths[:2] and int(items[i-1])==os.getpid())
    return True

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def observe_retained(source_manifest_sha,old_failure_sha,ns,allow_self_locks=False):
    new_state=unit_state(UNIT);new_group=cgroup_state(UNIT,new_state)
    need(new_state.get('LoadState')=='not-found' and new_state.get('MainPID','0') in ('','0')
        and new_state.get('ControlPID','0') in ('','0') and not new_state.get('Job')
        and new_group['processes']==0 and new_group['populated']==0)
    need(ns['pointer']('current')==BASE and ns['pointer']('previous')==PREVIOUS)
    authenticate_old_failure(read(STAGE/'old-failure-chain.json'))
    need(sha(read(STAGE/'old-failure-chain.json'))==old_failure_sha);locks_clear(allow_self_locks)
    helper=Path('/usr/local/sbin/careertrellis-authority')
    need(sha(read(helper))=='07399780faffbc8a761b558f3d116e0fd7d0cf2d1bb0226de987c5455ad45206'
        and run([str(helper),'status'],4096).strip()==b'postgres-r2-authoritative'
        and not os.path.lexists('/run/careertrellis-hosted-activation.lease'))
    def authority_fields(name):
        text=read(Path('/etc/careertrellis/authority')/name,65536).decode()
        rows=[line.split('=',1) for line in text.splitlines() if '=' in line]
        need(len({row[0] for row in rows})==len(rows));return dict(rows)
    pending=authority_fields('pending-cutover');recovery=authority_fields('post-import-recovery')
    historical='e7ae1d6c0e8b5bf39e5babe3ef871f1414f5c3bd'
    flags=('postgres_mirror_verified','postgres_restore_verified','postgres_bad_checksums_zero','generated_object_mirror_verified',
        'generated_object_restore_verified','restored_database_object_reconciliation_verified','provider_keyring_recovery_verified',
        'object_keyring_recovery_verified','decrypt_every_object_verified','reconciliation_verified')
    need(pending.get('phase')=='activated' and pending.get('release')==historical and recovery.get('release')==historical
        and all(recovery.get(key)=='true' for key in flags))
    units={}
    for unit in ('careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service'):
        values=dict(line.split('=',1) for line in run(['systemctl','show',unit,'--property=ActiveState,SubState,MainPID,ControlPID,Job'],16384).decode().splitlines())
        need(values['ActiveState']=='active' and values['SubState']=='running' and int(values['MainPID'])>0 and values['ControlPID']=='0' and not values['Job'])
        units[unit]={'active_running':True,'main_pid':int(values['MainPID'])}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    for endpoint,status in [('live','ok'),('ready','ready')]:
        with opener.open('http://127.0.0.1:8001/health/'+endpoint,timeout=15) as response:
            body=response.read(4097);need(len(body)<=4096);v=decode(body)
            need(response.status==200 and v.get('build')=='20261017.96' and v.get('status')==status)
    source=run(['git','-C',REPOSITORY,'archive',REV]);need(sha(source)=='67b392f0bd1c66b3391aa6e4c7f18b90b0a17fe7da72a4b5c32e0da594afb327')
    expected=ns['archive_files'](source);ns['verify_source'](RELEASE,expected)
    ns['verify_source'](Path('/opt/careertrellis/staging/releases')/BASE,ns['archive_files'](run(['git','-C',REPOSITORY,'archive',BASE])))
    difference=run(['git','-C',REPOSITORY,'-c','core.abbrev=40','diff','--binary','--no-ext-diff','--no-textconv',BASE,REV,'--'])
    need(sha(difference)=='ad8f04dbf0afeeabb9ec1d3557cc5e709b4ebc0488490424950df83843beda12')
    provenance_raw=read(STAGE/'baseline-provenance.json');need(sha(provenance_raw)=='b4c21424441e63b45d484c2ff063afae394c745fd0a235c7210c11beecb05ee7')
    provenance=decode(provenance_raw);ns['validate_provenance'](provenance);ns['accepted_provenance']()
    browser=ns['inventory'](ns['BROWSER']);packages=ns['inventory'](Path('/opt/careertrellis/staging/releases')/BASE/'backend/venv/lib/python3.12/site-packages')
    need(browser==provenance['browser'] and packages==provenance['site_packages'])
    need(ns['inventory'](RELEASE/'backend/venv/lib/python3.12/site-packages')==packages
        and ns['inventory'](RELEASE/'backend/venv/lib64/python3.12/site-packages')==packages)
    interpreter=Path(provenance['interpreter']['path']);need(sha(ns['protected'](interpreter))==provenance['interpreter']['sha256'])
    for name in ('python','python3','python3.12'):
        need(sha(ns['protected'](RELEASE/'backend/venv/bin'/name))==provenance['interpreter']['sha256'])
    runtime=ns['runtime_manifest'](RELEASE)
    need(runtime==ns['runtime_manifest'](RELEASE) and browser==ns['inventory'](ns['BROWSER'])
        and packages==ns['inventory'](Path('/opt/careertrellis/staging/releases')/BASE/'backend/venv/lib/python3.12/site-packages'))
    fs=os.statvfs(Path('/opt/careertrellis/staging'));free=fs.f_bavail*fs.f_frsize
    memory=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines());available=int(memory['MemAvailable'].split()[0])*1024
    need(free>=6442450944 and fs.f_favail>=10000 and available>=805306368)
    proof={'schema':'careertrellis.repair98-preflight/v1','attempt_id':ATTEMPT_ID,'observed_at':utc().isoformat(),'boot_id':boot(),
        'baseline':BASE,'previous':PREVIOUS,'live_build':'20261017.96','authority':'postgres-r2-authoritative',
        'source_archive_sha256':sha(source),'source_diff_sha256':sha(difference),'runtime_manifest_sha256':sha(canonical(runtime)),
        'browser_manifest_sha256':sha(canonical(browser)),'source_manifest_sha256':source_manifest_sha,'old_failure_chain_sha256':old_failure_sha,
        'capacity':{'available_bytes':free,'available_inodes':fs.f_favail,'memory_available':available},'units':units,'locks_clear':True,'result':'pass'}
    return {'runtime':runtime,'browser':browser,'proof':proof}

def authenticate_retained(plan,preflight,ns,allow_self_locks=True):
    live=observe_retained(plan['source_manifest_sha256'],plan['old_failure_chain_sha256'],ns,allow_self_locks)
    need(live['proof']['boot_id']==preflight['boot_id'] and live['proof']['runtime_manifest_sha256']==plan['retained_runtime_manifest_sha256']
        and live['proof']['browser_manifest_sha256']==preflight['browser_manifest_sha256'])
    need(utc()<dt.datetime.fromisoformat(plan['expires_at']))
    return live

def prepare_new_ledger_parent():
    parents(DOMAIN.parent)
    if not os.path.lexists(DOMAIN.parent):DOMAIN.parent.mkdir(mode=0o700);sync_dir(DOMAIN.parent.parent)
    s=DOMAIN.parent.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o700)

def prepare_new_code_and_work(manifest):
    import grp
    gid=grp.getgrnam('careertrellis').gr_gid
    for root,mode in ((CODE,0o750),(WORK,0o700)):
        parents(root.parent)
        if not os.path.lexists(root.parent):root.parent.mkdir(mode=0o750);os.chown(root.parent,0,gid);sync_dir(root.parent.parent)
        need(not os.path.lexists(root));root.mkdir(mode=mode);os.chown(root,0 if root==CODE else 65534,gid if root==CODE else 65534);sync_dir(root.parent)
    for name in ('run_checks_repair98.py','fixture_relocation.py'):
        raw=read(STAGE/name);need(sha(raw)==manifest['files'][name]['sha256'])
        create(CODE/name,raw);os.chmod(CODE/name,0o640);os.chown(CODE/name,0,gid);sync_dir(CODE)
        need(read(CODE/name)==raw)
    (WORK/'browsers').mkdir(mode=0o755);os.chown(WORK/'browsers',65534,65534);sync_dir(WORK)

def read_unprivileged_checks(path):
    need(path==WORK/'repair-checks.json');s=path.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid==65534 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=262144)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        fields=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
        identity=tuple(getattr(s,key) for key in fields)
        need(tuple(getattr(os.fstat(fd),key) for key in fields)==identity)
        with os.fdopen(os.dup(fd),'rb') as stream:raw=stream.read(s.st_size+1)
        need(len(raw)==s.st_size and tuple(getattr(os.fstat(fd),key) for key in fields)==identity
            and tuple(getattr(path.lstat(),key) for key in fields)==identity);return raw
    finally:os.close(fd)

def validate_repair_checks(raw,plan,manifest):
    value=decode(raw)
    keys={'schema','repair_ops_commit','runner_sha256','qualifier_sha256','old_failure_chain_sha256','retained_runtime_manifest_sha256','qualification',
        'revision','build','checks','details','started_utc','ended_utc','network_namespace_loopback_only','provider_requests'}
    need(type(value) is dict and set(value)==keys and value['schema']=='careertrellis.preparation98-repair-checks/v1'
        and value['repair_ops_commit']==plan['repair_ops_commit'] and value['revision']==REV and value['build']=='20261026.98'
        and value['runner_sha256']==manifest['files']['run_checks_repair98.py']['sha256']
        and value['qualifier_sha256']==manifest['files']['fixture_relocation.py']['sha256']
        and value['old_failure_chain_sha256']==plan['old_failure_chain_sha256']
        and value['retained_runtime_manifest_sha256']==plan['retained_runtime_manifest_sha256']
        and set(value['checks'])==CHECKS and all(x is True for x in value['checks'].values())
        and value['network_namespace_loopback_only'] is True and type(value['provider_requests']) is int and value['provider_requests']==0)
    need(value['qualification']=='exact-one fixture-root relocation; original app/test bytes unchanged')
    backend=value['details']['backend'];need(type(backend['tests']) is int and backend['tests']==899
        and type(backend['failures']) is int and backend['failures']==0 and type(backend['errors']) is int and backend['errors']==0)
    q=backend['fixture_qualification']
    qkeys={'schema','test_id','calls','constructor_returned','method_returned','binding_restored','analyzer_returncodes',
        'serial_suite_verified','runtime_code_verified','fixture_ancestors_verified','module_sha256','method_source_sha256','method_ast_sha256','qualification'}
    need(type(q) is dict and set(q)==qkeys and q['schema']=='careertrellis.exact-fixture-relocation/v1'
        and q['qualification']=='one TemporaryDirectory dir relocated; unchanged body and real positive/negative analyzer calls')
    need(q['calls']==1 and type(q['calls']) is int and q['test_id']=='test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables'
        and all(q[k] is True for k in ('constructor_returned','method_returned','binding_restored','serial_suite_verified','runtime_code_verified','fixture_ancestors_verified'))
        and q['module_sha256']=='341e728a03f4f76bfa04c64aec612fae9fd87dc831266e547d9f42c7f0a42ab4'
        and q['method_source_sha256']=='584aec03af3f6347fbce3f10f56b449deeea0925f3365e6f28a5d3cfcbd85cac'
        and q['method_ast_sha256']=='ad2abbb01e5d212e3045b604105c2dc788281d56d64735887a827caad841af73'
        and type(q['analyzer_returncodes']) is list and len(q['analyzer_returncodes'])==2
        and all(type(code) is int for code in q['analyzer_returncodes']) and q['analyzer_returncodes'][0]==0 and 1<=q['analyzer_returncodes'][1]<=255)
    return value

ACCEPTANCE_KEYS={'schema','result','release','build','baseline','previous','repair_attempt_id','repair_ops_commit',
    'source_manifest_sha256','repair_plan_sha256','repair_decision_sha256','candidate_sha256','source_archive_sha256',
    'source_diff_sha256','runner_sha256','qualifier_sha256','old_failure_chain_sha256','full_live_preflight_sha256',
    'retained_runtime_before_sha256','retained_runtime_after_sha256','runtime_manifest','runtime_manifest_sha256',
    'fixture_qualification','checks','checks_sha256','unit_terminal_sha256','owner_terminal_sha256','postterminal_proof_sha256','completed_utc','activated'}

def build_acceptance(plan,manifest,candidate,checks,candidate_sha,owner_sha,postterminal_sha):
    need(candidate['result']=='candidate_only_not_accepted' and candidate['runtime_before_sha256']==candidate['runtime_after_sha256']
        and candidate['browser_before_sha256']==candidate['browser_after_sha256'] and utc()<dt.datetime.fromisoformat(plan['expires_at']))
    value={'schema':'careertrellis.preparation98-repair-acceptance/v1','result':'qualified_prepared_inactive','release':REV,'build':'20261026.98',
        'baseline':BASE,'previous':PREVIOUS,'repair_attempt_id':ATTEMPT_ID,'repair_ops_commit':plan['repair_ops_commit'],
        'source_manifest_sha256':plan['source_manifest_sha256'],'repair_plan_sha256':candidate['repair_plan_sha256'],
        'repair_decision_sha256':candidate['repair_decision_sha256'],'candidate_sha256':candidate_sha,
        'source_archive_sha256':plan['source_archive_sha256'],'source_diff_sha256':plan['source_diff_sha256'],
        'runner_sha256':manifest['files']['run_checks_repair98.py']['sha256'],'qualifier_sha256':manifest['files']['fixture_relocation.py']['sha256'],
        'old_failure_chain_sha256':plan['old_failure_chain_sha256'],'full_live_preflight_sha256':plan['full_live_preflight_sha256'],
        'retained_runtime_before_sha256':candidate['runtime_before_sha256'],'retained_runtime_after_sha256':candidate['runtime_after_sha256'],
        'runtime_manifest':str(DOMAIN/'runtime-manifest.json'),'runtime_manifest_sha256':candidate['runtime_after_sha256'],
        'fixture_qualification':checks['details']['backend']['fixture_qualification'],'checks':checks['checks'],
        'checks_sha256':candidate['checks_sha256'],'unit_terminal_sha256':candidate['unit_terminal_sha256'],
        'owner_terminal_sha256':owner_sha,'postterminal_proof_sha256':postterminal_sha,'completed_utc':utc().isoformat(),'activated':False}
    need(set(value)==ACCEPTANCE_KEYS);return value

def validate_candidate(candidate,plan,plan_sha,decision_sha):
    closed('candidate.json',candidate)
    need(candidate['release']==REV and candidate['build']=='20261026.98' and candidate['repair_ops_commit']==plan['repair_ops_commit']
        and candidate['repair_plan_sha256']==plan_sha and candidate['repair_decision_sha256']==decision_sha
        and candidate['source_manifest_sha256']==plan['source_manifest_sha256']
        and candidate['old_failure_chain_sha256']==plan['old_failure_chain_sha256']
        and candidate['full_live_preflight_sha256']==plan['full_live_preflight_sha256']
        and candidate['result']=='candidate_only_not_accepted' and candidate['runtime_before_sha256']==candidate['runtime_after_sha256']
        and candidate['browser_before_sha256']==candidate['browser_after_sha256'])
    completed=dt.datetime.fromisoformat(candidate['completed_utc'])
    need(dt.datetime.fromisoformat(plan['decision_at'])<=completed<=utc()<dt.datetime.fromisoformat(plan['expires_at']))

def expected_unit_command(plan,manifest,ns):
    return ['systemd-run','--unit='+UNIT,'--wait','--pipe','--collect',*ns['sandbox_properties'](RELEASE,WORK,ns['BROWSER']),
        str(RELEASE/'backend/venv/bin/python'),'-I','-B',str(CODE/'run_checks_repair98.py'),
        '--release',str(RELEASE),'--revision',REV,'--work',str(WORK),'--repair-ops-commit',plan['repair_ops_commit'],
        '--approved-runner-sha256',manifest['files']['run_checks_repair98.py']['sha256'],
        '--approved-failure-chain-sha256',plan['old_failure_chain_sha256'],
        '--retained-runtime-manifest-sha256',plan['retained_runtime_manifest_sha256']]

def validate_browser_binding(candidate,preflight,live):
    expected=sha(canonical(live['browser']))
    need(candidate['browser_before_sha256']==candidate['browser_after_sha256']==expected==preflight['browser_manifest_sha256'])

def validate_unit_terminal(value,current_boot,plan,manifest,plan_sha,decision_sha,ns):
    closed('unit-terminal.json',value)
    need(value['unit']==UNIT and value['boot_id']==current_boot and type(value['client_exit']) is int and value['client_exit']==0
        and type(value['client_pid']) is int and value['client_pid']>1 and re.fullmatch('[0-9]+',value['client_start_ticks']))
    intent_raw=read(DOMAIN/'unit-intent.json');intent=closed('unit-intent.json',decode(intent_raw))
    need(sha(intent_raw)==value['unit_intent_sha256'] and intent['unit']==UNIT and intent['boot_id']==current_boot)
    coordinator=closed('coordinator.json',decode(read(DOMAIN/'coordinator.json')))
    need(intent['repair_plan_sha256']==plan_sha and intent['repair_decision_sha256']==decision_sha
        and intent['source_manifest_sha256']==plan['source_manifest_sha256']
        and intent['runner_sha256']==manifest['files']['run_checks_repair98.py']['sha256']
        and intent['qualifier_sha256']==manifest['files']['fixture_relocation.py']['sha256']
        and intent['coordinator_pid']==coordinator['pid'] and intent['coordinator_start_ticks']==coordinator['start_ticks']
        and coordinator['boot_id']==current_boot and intent['description_binding']=='sha256-of-this-record'
        and intent['argv']==expected_unit_command(plan,manifest,ns)
        and intent['sandbox_properties']==ns['sandbox_properties'](RELEASE,WORK,ns['BROWSER']))
    created=dt.datetime.fromisoformat(intent['created_at'])
    need(dt.datetime.fromisoformat(plan['decision_at'])<=created<=dt.datetime.fromisoformat(value['observed_at'])
        <=dt.datetime.fromisoformat(plan['expires_at']))
    client_raw=read(DOMAIN/'test-client.json');client=closed('test-client.json',decode(client_raw))
    need(sha(client_raw)==value['test_client_sha256'] and client['pid']==value['client_pid']
        and client['start_ticks']==value['client_start_ticks'] and client['boot_id']==current_boot
        and client['unit']==UNIT and client['unit_intent_sha256']==value['unit_intent_sha256'])
    execution=value['execution'];state=execution['state'];after=value['after']
    need(execution['unit']==UNIT and state['LoadState']=='loaded' and state['ActiveState']=='active' and state['SubState']=='exited'
        and state['MainPID']=='0' and state['ControlPID']=='0' and not state['Job'] and state['Result']=='success'
        and state['ExecMainStatus']=='0' and re.fullmatch('[0-9a-f]{32}',execution['invocation_id'])
        and state['InvocationID']==execution['invocation_id'] and state['Description']=='careertrellis-repair98:'+value['unit_intent_sha256']
        and execution['cgroup']['processes']==0 and execution['cgroup']['populated']==0
        and after.get('LoadState') in ('loaded','not-found') and after.get('MainPID','0') in ('','0')
        and after.get('ControlPID','0') in ('','0') and not after.get('Job')
        and value['cgroup_after']['processes']==0 and value['cgroup_after']['populated']==0)
    if after['LoadState']=='loaded':need(after['ActiveState']=='inactive' and after['SubState']=='dead'
        and after['Result']=='success' and after['ExecMainStatus']=='0' and after['InvocationID']==execution['invocation_id'])
    absent_original({'pid':value['client_pid'],'start_ticks':value['client_start_ticks'],'boot_id':value['boot_id']},current_boot)

def validate_owner_terminal(value,candidate,candidate_raw,plan,plan_sha,decision_sha,current_boot):
    closed('owner-terminal.json',value)
    need(type(value['coordinator_exit']) is int and value['coordinator_exit']==0 and value['owner_self_exit_claim'] is False
        and value['boot_id']==current_boot and value['repair_plan_sha256']==plan_sha and value['repair_decision_sha256']==decision_sha
        and value['candidate_sha256']==sha(candidate_raw) and value['checks_sha256']==candidate['checks_sha256']
        and value['unit_terminal_sha256']==candidate['unit_terminal_sha256'] and value['runtime_after_sha256']==candidate['runtime_after_sha256']
        and value['source_manifest_sha256']==plan['source_manifest_sha256'])
    owner=closed('owner.json',value['owner']);coordinator=closed('coordinator.json',value['coordinator'])
    need(owner==decode(read(DOMAIN/'owner.json')) and coordinator==decode(read(DOMAIN/'coordinator.json'))
        and coordinator['owner_pid']==owner['pid'] and owner['repair_plan_sha256']==plan_sha and owner['repair_decision_sha256']==decision_sha
        and owner['boot_id']==coordinator['boot_id']==current_boot)
    spawn_raw=read(DOMAIN/'child-spawn.json');spawn=closed('child-spawn.json',decode(spawn_raw))
    need(sha(spawn_raw)==value['child_spawn_sha256'] and spawn['pid']==coordinator['pid']
        and spawn['start_ticks']==coordinator['start_ticks'] and spawn['boot_id']==current_boot and spawn['owner_pid']==owner['pid']
        and spawn['repair_plan_sha256']==plan_sha and spawn['repair_decision_sha256']==decision_sha
        and spawn['source_manifest_sha256']==plan['source_manifest_sha256'])
    need(set(value['captures'])=={'stdout','stderr'})
    for capture in value['captures'].values():
        need(set(capture)=={'retained_bytes','discarded_bytes','eof','error'} and capture['eof'] is True and capture['error'] is False
            and type(capture['retained_bytes']) is int and 0<=capture['retained_bytes']<=1048576
            and type(capture['discarded_bytes']) is int and capture['discarded_bytes']>=0)
    return absent_original(owner,current_boot),absent_original(coordinator,current_boot)

def final_seal_guard(owner_terminal,candidate,candidate_raw,plan,plan_sha,decision_sha,current_boot):
    need(boot()==current_boot and utc()<dt.datetime.fromisoformat(plan['expires_at']))
    states=validate_owner_terminal(owner_terminal,candidate,candidate_raw,plan,plan_sha,decision_sha,current_boot)
    require_unit_absent();state=unit_state(UNIT);group=cgroup_state(UNIT,state)
    need(state.get('LoadState')=='not-found' and not state.get('Job') and group['processes']==0 and group['populated']==0)
    return states,state,group

# Reviewed291d lifecycle copied unchanged except exact NEW fixed unit allowlist.
import time
def unit_state(unit):
    need(re.fullmatch(re.escape(UNIT),unit))
    keys={'LoadState','ActiveState','SubState','MainPID','ControlPID','Job','ControlGroup','Result','ExecMainStatus','InvocationID','Description'}
    result=subprocess.run(['systemctl','show',unit,'--property='+','.join(sorted(keys))],
        stdin=subprocess.DEVNULL,capture_output=True,timeout=15,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
    need(result.returncode in (0,1,4) and len(result.stdout)<=16384)
    state={}
    for line in result.stdout.decode('utf8').splitlines():
        key,separator,value=line.partition('=');need(separator and key in keys and key not in state);state[key]=value
    need(state.get('LoadState') in ('loaded','not-found'));return state

def cgroup_state(unit,state):
    expected='/system.slice/'+unit+'.service'
    need(state.get('ControlGroup','') in ('',expected))
    path=Path('/sys/fs/cgroup'+expected)
    if not path.exists():return {'path':expected,'exists':False,'processes':0,'populated':0}
    need(path.is_dir() and not path.is_symlink())
    with (path/'cgroup.procs').open('rb') as f:raw=f.read(16385)
    need(len(raw)<=16384);processes=raw.splitlines()
    with (path/'cgroup.events').open('rb') as f:raw=f.read(4097)
    need(len(raw)<=4096);events=dict(line.split() for line in raw.splitlines())
    need(events.get(b'populated') in (b'0',b'1'))
    return {'path':expected,'exists':True,'processes':len(processes),'populated':int(events[b'populated'])}

def execution_terminal(unit,state,group):
    need(state.get('LoadState')=='loaded' and state.get('ActiveState')=='active'
         and state.get('SubState')=='exited' and state.get('MainPID')=='0' and state.get('ControlPID')=='0'
         and not state.get('Job') and state.get('Result')=='success' and state.get('ExecMainStatus')=='0'
         and re.fullmatch('[0-9a-f]{32}',state.get('InvocationID','')))
    need(group['processes']==0 and group['populated']==0)
    return {'unit':unit,'invocation_id':state['InvocationID'],'state':state,'cgroup':group}

def wait_execution_terminal(unit,client,timeout):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=unit_state(unit)
        if state.get('ActiveState')=='active' and state.get('SubState')=='exited':
            group=cgroup_state(unit,state)
            if group['processes']==0 and group['populated']==0:return execution_terminal(unit,state,group)
        need(client.poll() in (None,0) and state.get('ActiveState')!='failed')
        time.sleep(1)
    need(False)

def collect_empty_unit(unit,proof,client):
    # Refuse drift/reuse/new jobs before the only stop. All actual task processes
    # must already have exited; this operation only releases the empty test unit.
    state=unit_state(unit);group=cgroup_state(unit,state)
    current=execution_terminal(unit,state,group)
    need(current['invocation_id']==proof['invocation_id'] and state['Description']==proof['state']['Description'])
    result=subprocess.run(['systemctl','stop',unit],stdin=subprocess.DEVNULL,capture_output=True,
        timeout=15,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
    need(result.returncode==0)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        after=unit_state(unit);group=cgroup_state(unit,after)
        need(after.get('MainPID','0') in ('','0') and after.get('ControlPID','0') in ('','0') and not after.get('Job'))
        gone=after.get('LoadState')=='not-found'
        inactive=after.get('ActiveState')=='inactive' and after.get('SubState')=='dead'
        if inactive and not gone:need(after.get('Result')=='success' and after.get('ExecMainStatus')=='0'
                          and after.get('InvocationID')==proof['invocation_id'])
        if (gone or inactive) and group['processes']==0 and group['populated']==0:
            need(client.wait(timeout=15)==0)
            return {'execution':proof,'after':after,'cgroup_after':group,'client_pid':client.pid,'client_exit':0}
        time.sleep(.2)
    need(False)

def require_unit_absent():
    state=unit_state(UNIT);group=cgroup_state(UNIT,state)
    need(state.get('LoadState')=='not-found' and state.get('MainPID','0') in ('','0')
        and state.get('ControlPID','0') in ('','0') and not state.get('Job')
        and group['processes']==0 and group['populated']==0)

def wait_owned_execution(client,intent_hash,timeout):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=unit_state(UNIT)
        need(client.poll() in (None,0) and state.get('ActiveState')!='failed')
        if state.get('LoadState')=='loaded':
            need(state.get('Description')=='careertrellis-repair98:'+intent_hash)
            if state.get('ActiveState')=='active' and state.get('SubState')=='exited':
                group=cgroup_state(UNIT,state)
                if group['processes']==0 and group['populated']==0:return execution_terminal(UNIT,state,group)
        time.sleep(1)
    need(False)
