# Runtime validation/fence primitives adapted from the accepted October25 guardian.
# Host actions remain in reviewed callers; this module performs none on import.
"""Native validation primitives for the policy-bound paired coordinator.

No action occurs on import. Host adapters own dispatch and durable recovery;
unknown outcomes never authorize replay, publication or unsafe service resume.
The original guarded primitives are retained beneath the standing-policy adapter.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.request
import urllib.error

PROCESS_USERS = {'careertrellis-web.service': 'careertrellis',
                 'careertrellis-worker.service': 'careertrellis-worker',
                 'careertrellis-erasure-worker.service': 'careertrellis-erasure-worker'}
PROCESS_IDENTITY_PROBE = '''import os,sys
def main():
    try:
        pid,uid,gid=int(sys.argv[1]),int(sys.argv[2]),int(sys.argv[3])
        expected=sys.argv[4]
        if not (0<pid<2147483648 and uid>0 and gid>0 and os.geteuid()==uid and os.getegid()==gid): return 1
        base='/proc/'+str(pid)
        def started():
            with open(base+'/stat','r') as stream: return int(stream.read().rsplit(')',1)[1].split()[19])
        before=started()
        if before<=0 or os.stat(base).st_uid!=uid or os.readlink(base+'/cwd')!=expected: return 1
        return 0 if started()==before else 1
    except Exception: return 1
raise SystemExit(main())
'''


def process_identity_args(unit, pid, release):
    import pwd
    import grp
    require(unit in PROCESS_USERS and re.fullmatch('[1-9][0-9]{0,9}', str(pid))
            and 0 < int(pid) < 2147483648 and re.fullmatch('[0-9a-f]{40}', release), 'process_release')
    uid = pwd.getpwnam(PROCESS_USERS[unit]).pw_uid
    gid = grp.getgrnam('careertrellis').gr_gid
    require(uid > 0 and gid > 0, 'process_release')
    return ['/usr/bin/setpriv', '--reuid=' + str(uid), '--regid=' + str(gid),
            '--clear-groups', '--reset-env', '--', '/usr/bin/python3', '-I', '-B',
            '-c', PROCESS_IDENTITY_PROBE, str(pid), str(uid), str(gid),
            '/opt/careertrellis/staging/releases/' + release + '/backend']

RELEASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
OPS='98c58a180c87d637fdb87c58388a0e482b03beac'
BUILD='20261017.96'
CHILD_PATH='/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
BASE=Path('/var/lib/careertrellis/object-backup-20261025')
MAINTENANCE=Path('/run/lock/careertrellis-maintenance.lock')
RECOVERY=Path('/run/lock/careertrellis-generated-object-recovery.lock')
HELPER='/usr/local/sbin/careertrellis-mirror-generated-objects'
CORE='/usr/local/libexec/careertrellis/backup_health.py'
FULL='careertrellis-pgbackrest-full.service'
SERVICES=('careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service')
BACKUP_UNITS=tuple('careertrellis-pgbackrest-'+n+'.service' for n in ('full','differential','onedrive','inventory','restore'))
ACKS=('I_HAVE_VERIFIED_SOURCE_R2_CREDENTIAL_IS_READ_ONLY','I_UNDERSTAND_THIS_APPENDS_AN_IMMUTABLE_ONEDRIVE_SNAPSHOT')
UNIT_PINS={'careertrellis-web.service': ('8bde08ffeace51f2d86da9b8b96db8d819280f406889254a97a86153a842a571', '1a756de26856195ce0b7a63a63c4080dd9ff2c502a815f6ba41a876155302ff0'), 'careertrellis-worker.service': ('189fadce24d4baac3d9139973647ea14cb6848256acf11598d6728baa031a367', '5f5c4939910e660535ace2d016a551d572799b3ba381ba84cd8339e35077bedb'), 'careertrellis-erasure-worker.service': ('9c8f9f2ae32804691423119074776532871abd3db73d69bc4e63f7bcad8d845d', '5f5c4939910e660535ace2d016a551d572799b3ba381ba84cd8339e35077bedb'), 'careertrellis-pgbackrest-full.service': ('63ee2e6c6430d80c50364fd1560c489ae3ea8d221f45e8ae92f11e0d2c29e9bd', None)}
PINS={
 'web':{CORE:'293212836a6c3df380ea4e8b77fff92c6be4383ad08fbb9a26adfc12c89010e8',
 '/usr/local/sbin/careertrellis-backup-health':'e7b51cf999481554e3fc68c9cc4159a81dd19cb38464de13b1c9304db09361af',
 HELPER:'d61b8ad7575f23b290b5eca764cbeea08ea5ca104d3559a533194b1fd95e28c0',
 '/usr/local/libexec/careertrellis/generated_object_recovery.py':'866a054532b2a8a7def685168d7783eaf2dbdf80701191dd922b646f7aa77d30',
 '/usr/local/libexec/careertrellis/maintenance-lock.sh':'d6e448f41dd3d6932bce4c0e87a9aa676b4de6e15111aa53aca3a576f40d7cc1',
 '/usr/local/sbin/careertrellis-authority':'07399780faffbc8a761b558f3d116e0fd7d0cf2d1bb0226de987c5455ad45206'},
 'db':{'/usr/local/sbin/careertrellis-backup-health':'58bc6e0e408e02830691b34fbebc71a3919a69834df5950c8fae8228db66bf41',
 '/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive':'630de3092529f165ee305601dc52ad6b89b912e90294dc42ab09ec419de552ca',
 '/usr/local/sbin/careertrellis-run-pgbackrest-backup':'70d95eafce97e6bae0ba6cf28b61c9d764a7b82ce9bd4fb67843bedf642ec25e',
 CORE:'293212836a6c3df380ea4e8b77fff92c6be4383ad08fbb9a26adfc12c89010e8',
 '/opt/careertrellis/staging/releases/'+OPS+'/backend/admission_operations.py':'47fc77170563d7c2067de1f88907d398db386b331fc8969f891654d35244d582',
 '/opt/careertrellis/staging/releases/'+OPS+'/backend/admission_purge.py':'f0759546d8c9de5025b59c00e48fd533594af3efb8605b76d094bd3e91ea54b2',
 '/opt/careertrellis/staging/releases/'+OPS+'/backend/totp_cleanup_operations.py':'31f02a03181d7705b6493111c61aeb33245abe799042fa87c90cb929861dd954',
 '/opt/careertrellis/staging/releases/'+OPS+'/backend/totp_cleanup.py':'8f4c24f926e314f22c5ee66097547ba2316faa42f55702bbc7d9501f1171d471'}}

class Refused(RuntimeError): pass
class StartupPending(Refused): pass

def helper_dependencies(path=CHILD_PATH):
    """Read-only exact child-PATH dependency guard before any writer stop."""
    require(shutil.which('rclone',path=path)=='/usr/local/bin/rclone','rclone_resolution')
    binary=Path('/usr/local/bin/rclone')
    for parent in binary.parents:
        s=parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'rclone_parent')
    s=binary.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_mode&0o111 and os.access(binary,os.X_OK),'rclone_metadata')
    for name in ('awk','date','flock','grep','mktemp','realpath','rm','runuser','sed','stat','systemctl','bash'):
        require(shutil.which(name,path=path) is not None,'helper_dependency_missing')

def record_recovery_failure(r,exc):
    allowed={'Refused','StartupPending','HTTPError','URLError','FileNotFoundError','TimeoutError','TimeoutExpired','PermissionError','ValueError','OSError'}
    phases={'await_operator_request','await_backup_terminal','await_web_recovered','await_db_recovery_safe','restore_web_health','await_db_recovery_release'}
    phase=getattr(r,'recovery_phase','unknown')
    r.emit('recovery-failed.json',phase=phase if phase in phases else 'unknown',
        error_class=type(exc).__name__ if type(exc).__name__ in allowed else 'OtherError',
        reason='recovery_refused_fence_retained')

for _name,_hash in (
 ('deploy/scripts/manage-admission-purge.sh','d48596d04cc9696449f2aa7034f077a4341f95b16a2925fc91cbf7933f5c2363'),
 ('deploy/scripts/manage-totp-cleanup.sh','256ec8183f0a6cc21f50a8ecc601fe1b6146ed3d7dc63b7beb643d3b45a709b1')):
    PINS['db']['/opt/careertrellis/staging/releases/'+OPS+'/'+_name]=_hash
PINS['db'].update({
 '/etc/systemd/system/careertrellis-admission-purge@.service':'fe26678673fbd3d768374d693741c8cd169545df8cafddbeefe30ba8c61e6237',
 '/etc/systemd/system/careertrellis-totp-cleanup@.service':'71ecea1cbfe770544424376ff236741b6852b2b201c57095daf60ae685cd627d'})

def require(value, reason):
    if not value: raise Refused(reason)

def encoded(value): return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()

def digest(value): return hashlib.sha256(encoded(value)).hexdigest()

def validate_heartbeat(value,current_pid,now,prior_pid=None):
    require(set(value)=={'build','pid','status','updated_at'} and value['build']==BUILD and value['status']=='idle','heartbeat_binding')
    require(type(value['pid']) is int and value['pid']>0,'heartbeat_binding')
    require(value['pid']==current_pid or (prior_pid is not None and value['pid']==prior_pid),'heartbeat_binding')
    age=now-dt.datetime.fromisoformat(value['updated_at']).timestamp()
    require(-5<=age<=120,'heartbeat_stale')
    if value['pid']!=current_pid: raise StartupPending('heartbeat_prior_pid')

def check_http_health():
    """Hosted public build/readiness plus private version authentication boundary.

    ec29 deliberately protects /api/version. This operator sends no session,
    proxy credential or alternate Host, and never changes application policy.
    """
    for route,expected in (('live','ok'),('ready','ready')):
        with urllib.request.urlopen('http://127.0.0.1:8001/health/'+route,timeout=5) as response:
            require(response.status==200 and json.loads(response.read(2048))=={'status':expected,'build':BUILD},'health_route')
    version_url='http://127.0.0.1:8001/api/version'
    try:
        with urllib.request.urlopen(version_url,timeout=5):
            raise Refused('private_version_auth_boundary_changed')
    except urllib.error.HTTPError as exc:
        try: require(exc.code==401 and exc.geturl()==version_url,'private_version_auth_boundary_changed')
        finally: exc.close()

def validate_envelope(value, transaction, sender, now, expected=None, max_age=90):
    require(value.get('transaction')==transaction and value.get('sender')==sender,'wrong_message_boundary')
    require(value.get('release')==RELEASE,'wrong_release')
    require(type(value.get('issued')) in (int,float) and 0<=now-value['issued']<=max_age,'stale_message')
    require(re.fullmatch('[0-9a-f]{32}',value.get('invocation','')),'invalid_invocation')
    require(re.fullmatch('[0-9a-f-]{36}',value.get('boot','')),'invalid_boot')
    if expected:
        require(all(value[k]==expected[k] for k in ('transaction','sender','release','boot','invocation')),'guardian_changed')
    return value

def validate_database(value):
    require(value.get('schema')=='20261005_0023' and value.get('policy_rows')==1,'schema_or_policy_changed')
    require(value.get('maintenance') is False and value.get('signup_open') is True,'policy_changed')
    require(all(value.get(k)==0 for k in ('search_queue','active_searches','erasure_queue','clients','prepared')),'writers_not_quiescent')

def validate_pair(pair, quiesced, now):
    require(re.fullmatch('[0-9]{8}-[0-9]{6}F',pair.get('backup_label','')),'not_full_backup')
    parse=lambda v:dt.datetime.fromisoformat(v.replace('Z','+00:00')).timestamp()
    require(quiesced<=parse(pair['started_at'])<=parse(pair['completed_at'])<=now,'backup_outside_window')
    require(re.fullmatch('[0-9a-f]{64}',pair.get('receipt_sha256','')),'invalid_receipt_hash')

def validate_capture(value, label, quiesced, now):
    require(value.get('result')=='pass' and value.get('manifest_verified') is True,'capture_unverified')
    require(value.get('release')==RELEASE and value.get('backup_label')==label,'capture_binding')
    require(value.get('snapshot_id')==RELEASE+'-'+label and value.get('authority')=='postgres-r2-authoritative','capture_identity')
    require(re.fullmatch('[0-9a-f]{64}',value.get('manifest_sha256','')),'capture_manifest')
    require(type(value.get('object_count')) is int and 0<=value['object_count']<=MAX_CAPTURE_OBJECTS,'capture_count')
    require(type(value.get('ciphertext_bytes')) is int and 0<=value['ciphertext_bytes']<=MAX_CAPTURE_BYTES,'capture_bytes')
    require(all(type(value.get(k)) is int for k in ('capture_started_epoch','captured_epoch')),'capture_times')
    require(int(quiesced)<=value['capture_started_epoch']<=value['captured_epoch']<=int(now),'capture_interval')

class Journal:
    def __init__(self, root): self.root=Path(root)
    def write(self,name,value):
        require(re.fullmatch(r'[a-z][a-z0-9-]*\.json',name),'invalid_record')
        path=self.root/name
        # Exclusive durable intent: interruption after creation consumes the attempt.
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            raw=encoded(value)
            require(os.write(fd,raw)==len(raw),'short_write'); os.fsync(fd)
        finally: os.close(fd)
        if os.name!='nt':
            fd=os.open(self.root,os.O_DIRECTORY|os.O_NOFOLLOW)
            try: os.fsync(fd)
            finally: os.close(fd)
    def read(self,name): return json.loads(protected(self.root/name,65536,(0o600,)))
    def exists(self,name): return (self.root/name).exists() or (self.root/name).is_symlink()

def protected(path,limit,modes=(0o644,0o755,0o600)):
    path=Path(path)
    for parent in path.parents:
        info=parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'unsafe_parent')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode) in modes and 0<s.st_size<=limit,'unsafe_file')
        raw=os.read(fd,limit+1); t=os.fstat(fd)
        require(len(raw)==s.st_size and (s.st_size,s.st_mtime_ns,s.st_ctime_ns)==(t.st_size,t.st_mtime_ns,t.st_ctime_ns),'file_changed')
        return raw
    finally: os.close(fd)

class Fence:
    def __init__(self,path,mode): self.path=Path(path); self.mode=mode; self.fd=None
    def acquire(self):
        import fcntl
        # /run/lock is intentionally root sticky 1777; pins protect the leaf.
        for path,mode in ((Path('/run'),0o755),(Path('/run/lock'),0o1777)):
            s=path.lstat(); require(stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and stat.S_IMODE(s.st_mode)==mode,'lock_parent')
        prior=self.path.lstat()
        self.fd=os.open(self.path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            s=os.fstat(self.fd)
            require((s.st_dev,s.st_ino)==(prior.st_dev,prior.st_ino) and stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==self.mode,'lock_identity')
            fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.identity=(s.st_dev,s.st_ino)
        except BaseException:
            self.close()
            raise
        return self
    def check(self):
        import fcntl
        require(self.fd is not None,'fence_missing')
        s=self.path.lstat(); f=os.fstat(self.fd)
        require(self.identity==(s.st_dev,s.st_ino)==(f.st_dev,f.st_ino) and s.st_nlink==1,'fence_replaced')
        require(stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0
                and stat.S_IMODE(s.st_mode)==self.mode,'fence_metadata_changed')
        fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    def inherit10(self):
        require(self.fd!=10,'reserved_fd_in_use')
        try: os.fstat(10)
        except OSError as exc:
            require(exc.errno==9,'reserved_fd_check')
        else: raise Refused('reserved_fd_in_use')
        os.dup2(self.fd,10); os.close(self.fd); self.fd=10
    def close(self):
        if self.fd is not None: os.close(self.fd); self.fd=None

class Runtime:
    def __init__(self,role,transaction):
        self.role=role; self.transaction=transaction; self.journal=Journal(BASE/transaction)
        self.started=time.monotonic(); self.fence=None; self.child=None; self.group=None; self.reconciling=False
        self.boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.invocation=os.environ.get('INVOCATION_ID','')
        require(re.fullmatch('[0-9a-f]{32}',self.invocation),'systemd_guardian_required')
    def identity(self):
        return {'transaction':self.transaction,'sender':self.role,'release':RELEASE,'boot':self.boot,'invocation':self.invocation,'issued':time.time()}
    def emit(self,name,**fields):
        self.journal.write(name,dict(self.identity(),**fields))
    def check(self):
        require(self.reconciling or time.monotonic()-self.started<6000,'guardian_deadline')
        if self.fence: self.fence.check()
    def wait(self,name,seconds):
        deadline=time.monotonic()+seconds
        while not self.journal.exists(name):
            self.check(); require(time.monotonic()<deadline,'mailbox_deadline'); time.sleep(1)
        self.check(); return self.journal.read(name)
    def run(self,args,timeout=15,inherit=False):
        self.check()
        env={'PATH':CHILD_PATH,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
        if inherit: env['CAREERTRELLIS_MAINTENANCE_LOCK_FD']='10'
        self.child=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
            pass_fds=(10,) if inherit else (),start_new_session=True)
        self.group=self.child.pid
        try:
            out,_=self.child.communicate(timeout=timeout)
            require(self.child.returncode==0 and len(out)<=262144,'command_failed')
            require(not self.live_group(),'child_descendants_live')
            self.child=None; self.group=None
            self.check(); return out
        except BaseException:
            # Reap only the owned subprocess group. Never kill backup unit/DB.
            for sig,wait in ((signal.SIGTERM,15),(signal.SIGKILL,15)):
                if self.live_group():
                    try: os.killpg(self.group,sig)
                    except ProcessLookupError: pass
                try: self.child.communicate(timeout=wait)
                except subprocess.TimeoutExpired: continue
                if not self.live_group(): break
            raise
    def live_group(self):
        if self.group is None: return False
        for p in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields=p.read_text().rsplit(')',1)[1].split()
                if int(fields[2])==self.group and fields[0]!='Z': return True
            except (FileNotFoundError,ProcessLookupError): pass
        return False
    def state(self,unit):
        raw=self.run(['/usr/bin/systemctl','show',unit,'--property=LoadState,ActiveState,SubState,MainPID,ControlPID,Job,InvocationID,Result,ExecMainStatus,ControlGroup,FragmentPath,DropInPaths,ExecMainStartTimestampMonotonic,NeedDaemonReload,ExecStart,WorkingDirectory'])
        return dict(line.split('=',1) for line in raw.decode().splitlines())
    def unit_binding(self,unit):
        s=self.state(unit); fragment='/etc/systemd/system/'+unit
        require(s['LoadState']=='loaded' and s['NeedDaemonReload']=='no' and s['FragmentPath']==fragment,'unit_binding')
        expected,dropin=UNIT_PINS[unit]
        require(hashlib.sha256(protected(fragment,65536)).hexdigest()==expected,'unit_source_drift')
        drop=fragment+'.d/hosted-authority.conf'
        require(s['DropInPaths']==(drop if dropin else ''),'unit_dropin_drift')
        if dropin: require(hashlib.sha256(protected(drop,65536)).hexdigest()==dropin,'dropin_source_drift')
        if unit==FULL:
            require('argv[]=/usr/local/sbin/careertrellis-run-pgbackrest-backup full_backup ;' in s['ExecStart'],'backup_exec_drift')
        else:
            args={'careertrellis-web.service':'-m uvicorn app:app','careertrellis-worker.service':'worker.py ;','careertrellis-erasure-worker.service':'account_erasure_worker.py ;'}[unit]
            require('argv[]=/opt/careertrellis/staging/current/backend/venv/bin/python '+args in s['ExecStart'],'writer_exec_drift')
            require(s['WorkingDirectory']=='/opt/careertrellis/staging/current/backend','writer_directory_drift')
    def cleanup_bindings(self):
        names=self.run(['/usr/bin/systemctl','list-units','--all','--plain','--no-legend','--no-pager','careertrellis-admission-purge@*.service','careertrellis-totp-cleanup@*.service']).decode()
        expected={'careertrellis-'+kind+'@'+OPS+'.service' for kind in ('admission-purge','totp-cleanup')}
        require({line.split()[0] for line in names.splitlines() if line.strip()}==expected,'cleanup_instance_drift')
        for kind in ('admission-purge','totp-cleanup'):
            s=self.state('careertrellis-'+kind+'@'+OPS+'.service')
            require(s['FragmentPath']=='/etc/systemd/system/careertrellis-'+kind+'@.service' and not s['DropInPaths'] and s['NeedDaemonReload']=='no','cleanup_unit_drift')
            require('argv[]=/bin/bash /opt/careertrellis/staging/releases/'+OPS+'/deploy/scripts/manage-'+kind+'.sh run ;' in s['ExecStart'],'cleanup_exec_drift')
    def terminal(self,unit):
        s=self.state(unit)
        require(s['ActiveState'] in ('inactive','failed') and s['MainPID']=='0' and s['ControlPID']=='0' and not s['Job'],'unit_not_terminal')
        group=s.get('ControlGroup','')
        if group:
            require(group.startswith('/system.slice/') and '..' not in group,'cgroup_path')
            p=Path('/sys/fs/cgroup')/group.lstrip('/')
            if p.exists():
                for procs in p.rglob('cgroup.procs'):
                    require(not procs.read_text().strip(),'cgroup_not_empty')
        return s
    def authority(self):
        require(Path('/opt/careertrellis/staging/current').resolve()==Path('/opt/careertrellis/staging/releases')/RELEASE,'release_changed')
        require(self.run(['/usr/local/sbin/careertrellis-authority','status']).strip()==b'postgres-r2-authoritative','authority_changed')
        require(not Path('/run/careertrellis-hosted-activation.lease').exists(),'activation_lease')
    def stopped(self):
        self.authority()
        for unit in SERVICES: require(self.terminal(unit)['ActiveState']=='inactive','writer_failed_not_stopped')
    def health(self):
        self.authority()
        for unit in SERVICES:
            s=self.state(unit)
            require(s['ActiveState']=='active' and s['SubState']=='running' and not s['Job'],'service_not_ready')
            self.process_release(unit, s)
        check_http_health()
        import pwd
        import grp
        for unit,path,user in ((SERVICES[1],'/var/lib/careertrellis/worker/worker-heartbeat.json','careertrellis-worker'),(SERVICES[2],'/var/lib/careertrellis/account-erasure/account-erasure-worker-heartbeat.json','careertrellis-erasure-worker')):
            try: fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            except FileNotFoundError: raise StartupPending('heartbeat_missing') from None
            try:
                s=os.fstat(fd)
                require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_uid==pwd.getpwnam(user).pw_uid and s.st_gid==grp.getgrnam('careertrellis').gr_gid and stat.S_IMODE(s.st_mode)==0o600 and s.st_size<=1024,'heartbeat_file')
                value=json.loads(os.read(fd,1025))
            finally: os.close(fd)
            validate_heartbeat(value,int(self.state(unit)['MainPID']),time.time(),getattr(self,'startup_prior_pids',{}).get(unit))
    def process_release(self, unit, before=None):
        before = self.state(unit) if before is None else before
        require(before.get('ActiveState') == 'active' and before.get('SubState') == 'running'
                and not before.get('Job') and re.fullmatch('[0-9a-f]{32}', before.get('InvocationID', '')),
                'process_release')
        require(self.run(process_identity_args(unit, before['MainPID'], RELEASE)) == b'', 'process_release')
        after = self.state(unit)
        require(after.get('ActiveState') == 'active' and after.get('SubState') == 'running'
                and not after.get('Job') and all(after.get(key) == before.get(key)
                for key in ('MainPID', 'InvocationID')), 'process_release')
    def database(self):
        sql="""SET log_statement='none'; SET log_min_error_statement='panic'; SET log_parameter_max_length=0; SET log_parameter_max_length_on_error=0;
BEGIN READ ONLY; SET LOCAL statement_timeout='3s'; SET LOCAL lock_timeout='250ms'; SET LOCAL transaction_timeout='5s'; SET LOCAL search_path=pg_catalog;
SELECT json_build_object('schema',(SELECT version_num FROM careertrellis.alembic_version),'policy_rows',(SELECT count(*) FROM careertrellis_admission.policy),'maintenance',(SELECT maintenance FROM careertrellis_admission.policy),'signup_open',(SELECT signup_open FROM careertrellis_admission.policy),
'search_queue',(SELECT count(*) FROM careertrellis.search_run_claim_queue),'active_searches',(SELECT count(*) FROM careertrellis.search_runs WHERE status IN ('preparing','queued','running','cancel_requested')),'erasure_queue',(SELECT count(*) FROM careertrellis.account_erasure_claim_queue),
'clients',(SELECT count(*) FROM pg_stat_activity WHERE datname='careertrellis' AND pid<>pg_backend_pid() AND backend_type='client backend'), 'prepared',(SELECT count(*) FROM pg_prepared_xacts WHERE database='careertrellis'),'database_bytes',pg_database_size(current_database())); ROLLBACK;"""
        p=subprocess.run(['/usr/sbin/runuser','-u','postgres','--','/usr/bin/psql','-X','-qAt','-w','-v','ON_ERROR_STOP=1','-h','/var/run/postgresql','-p','5432','-d','careertrellis'],input=sql.encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,
            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','PGCONNECT_TIMEOUT':'2','PGOPTIONS':'-c default_transaction_read_only=on'})
        require(p.returncode==0 and len(p.stdout)<4096,'database_observation_failed')
        result=json.loads(p.stdout); validate_database(result); self.check(); return result


def restore_web(r, recovery=False):
    require(not r.live_group(),'children_unresolved')
    r.authority()
    for unit in SERVICES:
        if recovery and r.state(unit)['ActiveState']=='active':
            s=r.state(unit)
            r.process_release(unit, s)
        else: r.terminal(unit)
    probe=Fence(RECOVERY,0o600).acquire(); probe.close()
    prior=r.journal.read('stop-intent.json')['original_pids']
    require(set(prior)==set(SERVICES) and all(type(pid) is int and pid>0 for pid in prior.values()),'prior_pid_record')
    r.emit('recovery-restore-intent.json' if recovery else 'restore-intent.json')
    r.run(['/usr/bin/systemctl','start',*SERVICES],timeout=120)
    r.startup_prior_pids=prior
    deadline=time.monotonic()+120
    try:
        while True:
            require(time.monotonic()<deadline,'restore_health_failed')
            try:
                r.health()
                require(time.monotonic()+5<deadline,'restore_health_failed')
                time.sleep(5); r.health()
                require(time.monotonic()<deadline,'restore_health_failed')
                return
            except StartupPending: pass
            except (urllib.error.URLError,TimeoutError): pass
            except Refused as exc:
                if str(exc) not in ('service_not_ready','heartbeat_stale'): raise
            require(time.monotonic()<deadline,'restore_health_failed'); time.sleep(2)
    finally:
        del r.startup_prior_pids


# These adapters retain the accepted fencing, subprocess and runtime-health
# checks, while binding every new run to an explicit installed standing policy.
try:
    from .common import Journal as DurableJournal, Refused as PolicyRefused
except ImportError:
    from common import Journal as DurableJournal, Refused as PolicyRefused

STATE_ROOT = Path('/var/lib/careertrellis/paired-maintenance')
INSTALL_ROOT = Path('/usr/local/libexec/careertrellis/paired_maintenance')
POLICY_PATH = Path('/etc/careertrellis/paired-maintenance/policy.json')
PUBLIC_POLICY = STATE_ROOT / 'public-policy.json'
PUBLIC_MARKER = STATE_ROOT / 'writer-interlock.json'
GATE_DROPIN = ('[Unit]\nWants=careertrellis-paired-bootstrap.service\n'
              'After=careertrellis-paired-bootstrap.service\n\n'
              '[Service]\nExecCondition=/usr/bin/python3 -I -B '
              '/usr/local/libexec/careertrellis/paired_maintenance/entry.py writer-gate\n')
GATE_DROPIN_SHA256 = hashlib.sha256(GATE_DROPIN.encode()).hexdigest()
MAX_CAPTURE_OBJECTS = 128
MAX_CAPTURE_BYTES = 32 * 1024 ** 2
_BASE_PINS = {role: dict(values) for role, values in PINS.items()}
_BASE_OPS = OPS
_AcceptedRuntime = Runtime


class _JournalAdapter(DurableJournal):
    def write(self, name, value):
        return self.write_once(name, value)


def configure(policy, role):
    global RELEASE, OPS, BUILD, BASE, PINS, MAX_CAPTURE_OBJECTS, MAX_CAPTURE_BYTES
    require(role in ('web', 'db'), 'runtime_role')
    data = policy.data
    RELEASE, OPS, BUILD = data['application_release'], data['ops_release'], data['application_build']
    BASE = STATE_ROOT / role / 'runs'
    MAX_CAPTURE_OBJECTS = data['max_capture_objects']
    MAX_CAPTURE_BYTES = data['max_capture_bytes']
    PINS = {key: {path.replace(_BASE_OPS, OPS): value for path, value in values.items()}
            for key, values in _BASE_PINS.items()}
    PINS['web'][CORE] = '293212836a6c3df380ea4e8b77fff92c6be4383ad08fbb9a26adfc12c89010e8'
    for key, values in data.get('artifact_hashes', {}).items():
        for path, expected in values.items():
            require(path in PINS[key], 'unknown_artifact_pin')
            PINS[key][path] = expected


def preflight_runtime(policy, role):
    """Read-only runtime facade; no transaction directory or worker identity."""
    configure(policy, role)
    require(os.geteuid() == 0 and os.uname().nodename == {
        'web': 'careertrellis-staging-web-1', 'db': 'job-applier-db-staging-01'}[role], 'host_refused')
    value = object.__new__(HostRuntime)
    value.policy, value.role, value.transaction = policy, role, '0' * 32
    value.journal = None
    value.started, value.fence, value.child, value.group = time.monotonic(), None, None, None
    value.reconciling = True
    value.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    value.invocation = ''
    return value


class HostRuntime(_AcceptedRuntime):
    def __init__(self, policy, role, transaction, worker=True):
        configure(policy, role)
        require(os.geteuid() == 0 and os.uname().nodename == {
            'web': 'careertrellis-staging-web-1', 'db': 'job-applier-db-staging-01'}[role], 'host_refused')
        require(re.fullmatch('[0-9a-f]{32}', transaction), 'transaction_refused')
        self.policy = policy
        self.role = role
        self.transaction = transaction
        self.journal = _JournalAdapter(BASE / transaction)
        self.started = time.monotonic()
        self.fence = None
        self.child = None
        self.group = None
        self.reconciling = not worker
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.invocation = os.environ.get('INVOCATION_ID', '')
        if worker:
            require(re.fullmatch('[0-9a-f]{32}', self.invocation), 'systemd_worker_required')

    def check(self):
        # Phase-specific deadlines must never release a live operation's fence.
        # An over-budget backup is observed and escalated, never killed/replayed.
        if self.fence:
            self.fence.check()

    def emit(self, name, **fields):
        if self.journal.exists(name):
            prior = self.journal.read(name)
            require(prior.get('transaction') == self.transaction and prior.get('release') == RELEASE
                    and prior.get('sender') == self.role and prior.get('boot') == self.boot,
                    'prior_event_binding')
            require(all(prior.get(key) == value for key, value in fields.items()), 'prior_event_changed')
            return prior
        value = dict(self.identity(), **fields)
        self.journal.write(name, value)
        return value

    def verify_sources(self):
        for path, expected in PINS[self.role].items():
            require(hashlib.sha256(protected(path, 1048576)).hexdigest() == expected, 'source_drift')
        for unit in SERVICES if self.role == 'web' else (FULL,):
            self.unit_binding(unit)
        if self.role == 'db':
            self.cleanup_bindings()
        else:
            helper_dependencies()
        require(shutil.disk_usage('/var/lib').free >= self.policy.data['minimum_free_bytes'], 'space_floor')

    def unit_binding(self, unit):
        if unit == FULL:
            return super().unit_binding(unit)
        s = self.state(unit)
        fragment, authority_drop = UNIT_PINS[unit]
        source = '/etc/systemd/system/' + unit
        authority = source + '.d/hosted-authority.conf'
        gate = source + '.d/80-paired-maintenance.conf'
        require(s['FragmentPath'] == source and s['NeedDaemonReload'] == 'no', 'writer_unit_drift')
        require(set(s['DropInPaths'].split()) == {authority, gate}, 'writer_dropin_drift')
        require(hashlib.sha256(protected(source, 65536)).hexdigest() == fragment, 'writer_fragment_drift')
        require(hashlib.sha256(protected(authority, 65536)).hexdigest() == authority_drop, 'authority_dropin_drift')
        require(hashlib.sha256(protected(gate, 65536)).hexdigest() == GATE_DROPIN_SHA256, 'gate_dropin_drift')
        args = {'careertrellis-web.service': '-m uvicorn app:app',
                'careertrellis-worker.service': 'worker.py ;',
                'careertrellis-erasure-worker.service': 'account_erasure_worker.py ;'}[unit]
        require('argv[]=/opt/careertrellis/staging/current/backend/venv/bin/python ' + args in s['ExecStart'], 'writer_exec_drift')
        require(s['WorkingDirectory'] == '/opt/careertrellis/staging/current/backend', 'writer_directory_drift')

    def wait_peer(self, client, wanted, timeout, on_wait=None):
        deadline = time.monotonic() + timeout
        while True:
            self.check()
            value = client.call('status', {})
            require(value.get('boot_id') is None or isinstance(value.get('boot_id'), str), 'peer_boot_shape')
            require(not value.get('operator_required'), 'peer_operator_required')
            if value.get('state') in wanted:
                self.check()
                return value
            require(value.get('state') not in ('failed', 'aborted', 'unknown'), 'peer_failed')
            require(time.monotonic() < deadline, 'peer_wait_expired')
            if on_wait:
                on_wait(value)
            time.sleep(5)

    def global_status(self, phase, result='running', services_restored=False):
        value = {'schema': 'careertrellis.paired-maintenance-status/v1',
                 'policy_sha256': self.policy.digest, 'boot_id': self.boot, 'release': RELEASE,
                 'phase': phase, 'result': result, 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'services_restored': services_restored, 'transaction': self.transaction}
        DurableJournal(STATE_ROOT / self.role).replace('status.json', value)

    def status(self, state, **fields):
        value = {'state': state, 'transaction': self.transaction, 'policy_sha256': self.policy.digest,
                 'release': RELEASE, 'boot_id': self.boot, 'worker_invocation': self.invocation,
                 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(), **fields}
        self.journal.replace('status.json', value)
        return value
