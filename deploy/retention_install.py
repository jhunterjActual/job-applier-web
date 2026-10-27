"""Finite, fixed-path retention package installation; no application deployment.

An operator stages a reviewed public packet and a root-private short-lived plan.
No plan, review or acceptance receipt is manufactured here. Every original byte
and timer state is retained before exchange. Interruption is a permanent fence;
there is no automatic rollback, retry, cleanup, secret entry or provider call.
"""
import ast
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import sys

BASE = Path('/usr/local/libexec/careertrellis')
PLAN = Path('/etc/careertrellis/onedrive-retention-install/plan.json')
PACKETS = Path('/var/lib/careertrellis/retention-install-packets')
EVIDENCE = Path('/var/backups/careertrellis/onedrive-retention-install')
POLICY = Path('/etc/careertrellis/paired-maintenance/policy.json')
PAIR_PUBLIC = Path('/var/lib/careertrellis/paired-maintenance/public-policy.json')
ONE_POLICY = Path('/etc/careertrellis/onedrive-retention/policy.json')
ONE_PUBLIC = Path('/var/lib/careertrellis/onedrive-retention-public/policy.json')
ACCEPTED = ONE_POLICY.parent/'accepted-baseline.json'
PAIR_REVISION = '493bf14a3bae2cb2b36f3a57191602b72ea6e3d3'
PAIR_MANIFEST = '7a5122cb39b34137d9b2539d0bf60c0e3720e2854ebe8dcc30f1f1b18d092fd5'
PAIR_POLICY = '3dcd50ba5bbc6fdd3270a83c7dd255c35f86c18f9da5d0613a55c8a4be94c880'
EXPIRY = dt.datetime(2027,1,24,tzinfo=dt.timezone.utc)
HOSTS = {'web':'careertrellis-staging-web-1','db':'job-applier-db-staging-01'}
TIMERS = {'web':('careertrellis-paired-maintenance.timer','careertrellis-paired-recovery.timer'),
          'db':('careertrellis-pgbackrest-full.timer','careertrellis-pgbackrest-differential.timer',
                'careertrellis-pgbackrest-onedrive.timer')}
UNITS = {'web':('careertrellis-onedrive-retention.service','careertrellis-onedrive-retention.timer',
               'careertrellis-backup-health.service','careertrellis-backup-health.timer'),
         'db':('careertrellis-backup-health.service','careertrellis-backup-health.timer')}
HELPERS = {'web':{'/usr/local/sbin/careertrellis-mirror-generated-objects':0o755,
                 '/usr/local/libexec/careertrellis/backup_health.py':0o644,
                 '/usr/local/sbin/careertrellis-backup-health':0o755},
           'db':{'/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive':0o755,
                 '/usr/local/libexec/careertrellis/backup_health.py':0o644,
                 '/usr/local/sbin/careertrellis-backup-health':0o755}}
LOCKS = {'web':('/var/lib/careertrellis/paired-maintenance/web/control.lock',
                 '/run/lock/careertrellis-maintenance.lock'),
         'db':('/var/lib/careertrellis/paired-maintenance/db/control.lock',
               '/run/lock/careertrellis-maintenance.lock',
               '/run/lock/careertrellis-pgbackrest-repository.lock')}
ENV = {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1'}
HOOKS = ('ExecCondition','ExecStartPre','ExecStartPost','ExecStop','ExecStopPost')
READINESS = frozenset(('host_identity_fingerprint','egress','release_and_version','authority_pending_recovery_lease',
    'canonical_locks','unit_states','postgres_alembic_rls','pgbackrest_label_wal','provider_target_restrictions',
    'queues','disk_inodes','disposable_ports_sockets','accepted_receipt_chain','shared_window_released'))
MAX = 2*1024**2


class Refused(RuntimeError): pass


def need(value):
    if not value: raise Refused('retention_install_requires_reconciliation')


def canonical(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')


def sha(raw): return hashlib.sha256(raw).hexdigest()


def hex_value(value,n=64): return type(value) is str and re.fullmatch('[0-9a-f]{%d}'%n,value) is not None


def decode(raw):
    def pairs(items):
        result={}
        for key,value in items:
            need(key not in result);result[key]=value
        return result
    try:
        value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
        need(canonical(value)==raw);return value
    except Exception:raise Refused('retention_install_requires_reconciliation') from None


def timestamp(value):
    need(type(value) is str)
    try:value=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except Exception:raise Refused('retention_install_requires_reconciliation') from None
    need(value.utcoffset()==dt.timedelta(0));return value


def identity(meta):
    return (meta.st_dev,meta.st_ino,meta.st_uid,meta.st_gid,meta.st_mode,
            meta.st_nlink,meta.st_size,meta.st_mtime_ns,meta.st_ctime_ns)


@contextmanager
def directory(path,mode=None):
    path=Path(path);need(path.is_absolute() and '..' not in path.parts)
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        current=Path('/')
        for part in path.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child;current/=part;meta=os.fstat(fd)
            need(meta.st_uid==meta.st_gid==0 and stat.S_ISDIR(meta.st_mode)
                 and (stat.S_IMODE(meta.st_mode)==0o1777 if current==Path('/run/lock')
                      else not meta.st_mode&0o022))
        if mode is not None:need(stat.S_IMODE(os.fstat(fd).st_mode)==mode)
        yield fd
    finally:os.close(fd)


def read(path,mode=0o600,limit=MAX):
    path=Path(path)
    with directory(path.parent) as parent:
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        try:
            before=os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_uid==before.st_gid==0
                 and stat.S_IMODE(before.st_mode)==mode and before.st_nlink==1
                 and 0<=before.st_size<=limit)
            parts=[];remaining=before.st_size
            while remaining:
                raw=os.read(fd,min(65536,remaining));need(bool(raw));parts.append(raw);remaining-=len(raw)
            need(not os.read(fd,1) and identity(os.fstat(fd))==identity(before)
                 and identity(os.stat(path.name,dir_fd=parent,follow_symlinks=False))==identity(before))
            return b''.join(parts)
        finally:os.close(fd)


def write_once(path,raw,mode=0o600):
    path=Path(path);need(type(raw) is bytes and len(raw)<=MAX)
    with directory(path.parent) as parent:
        fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=parent)
        try:
            os.fchown(fd,0,0);os.fchmod(fd,mode);view=memoryview(raw)
            while view:
                count=os.write(fd,view);need(count>0);view=view[count:]
            os.fsync(fd)
        finally:os.close(fd)
        os.fsync(parent)
    need(read(path,mode)==raw)


def mkdir(path,mode=0o700):
    path=Path(path)
    with directory(path.parent) as parent:
        os.mkdir(path.name,mode,dir_fd=parent);os.fsync(parent)
    with directory(path,mode):pass


def command(args,timeout=15):
    try:
        result=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,env=ENV,timeout=timeout,check=False)
        need(result.returncode==0 and len(result.stdout)<=MAX);return result.stdout
    except Exception:raise Refused('retention_install_requires_reconciliation') from None


def show(unit,fields):
    need(re.fullmatch(r'careertrellis-[A-Za-z0-9@_.-]+\.(?:service|timer)',unit))
    raw=command(['/usr/bin/systemctl','show',unit,'--property='+','.join(fields)])
    result={}
    for row in raw.decode('ascii').splitlines():
        key,sep,value=row.partition('=');need(sep and key in fields and key not in result);result[key]=value
    need(set(result)==set(fields));return result


def empty_group(unit):
    root=Path('/sys/fs/cgroup/system.slice')/unit
    try:meta=root.lstat()
    except FileNotFoundError:return True
    need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==0 and not meta.st_mode&0o022)
    import itertools
    files=list(itertools.islice(root.rglob('cgroup.procs'),65))
    need(0<len(files)<=64 and root/'cgroup.procs' in files)
    for path in files:
        meta=path.lstat();need(stat.S_ISREG(meta.st_mode) and meta.st_uid==0 and not meta.st_mode&0o022)
        raw=path.read_bytes();need(len(raw)<=65536)
        need(all(re.fullmatch(rb'[1-9][0-9]*',item) for item in raw.split()))
        if raw.split():return False
    return True


def owner(plan):
    unit='careertrellis-retention-install-'+plan['transaction']+'.service'
    need(Path('/proc/self/cgroup').read_text().strip()=='0::/system.slice/'+unit)
    fields=('LoadState','ActiveState','SubState','MainPID','ControlPID','Job','Type','Restart',
        'RemainAfterExit','PrivateNetwork','KillMode','SendSIGKILL','RuntimeMaxUSec',
        'TimeoutStartUSec','TimeoutStopUSec','InvocationID')
    state=show(unit,fields)
    expected={'LoadState':'loaded','ActiveState':'active','SubState':'running','MainPID':str(os.getpid()),
        'ControlPID':'0','Job':'','Type':'exec','Restart':'no','RemainAfterExit':'no','PrivateNetwork':'yes',
        'KillMode':'control-group','SendSIGKILL':'yes','RuntimeMaxUSec':'4min',
        'TimeoutStartUSec':'30s','TimeoutStopUSec':'1min 30s'}
    need(all(state[key]==value for key,value in expected.items()) and hex_value(state['InvocationID'],32))
    group=Path('/sys/fs/cgroup/system.slice')/unit
    with directory(group):pass
    members=set()
    import itertools
    groups=list(itertools.islice(group.rglob('cgroup.procs'),65))
    need(0<len(groups)<=64 and group/'cgroup.procs' in groups)
    for path in groups:
        raw=path.read_bytes();need(len(raw)<=65536)
        need(all(re.fullmatch(rb'[1-9][0-9]*',item) for item in raw.split()))
        members.update(int(item) for item in raw.split())
    need(members=={os.getpid()})
    object_path='/org/freedesktop/systemd1/unit/'+''.join(
        char if char.isascii() and char.isalnum() else '_%02x'%ord(char) for char in unit)
    for hook in HOOKS:
        need(command(['/usr/bin/busctl','get-property','org.freedesktop.systemd1',object_path,
            'org.freedesktop.systemd1.Service',hook])==b'a(sasbttttuii) 0\n')
    return {'unit':unit,'pid':os.getpid(), 'start_ticks':int(Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19]),
        'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'invocation_id':state['InvocationID']}


def require_installer_source(plan):
    """The separately staged installer is explicitly bound by the reviewed plan."""
    expected=PACKETS/plan['transaction']/'retention_install.py'
    need(Path(__file__).absolute()==expected and hex_value(plan['installer_sha256'])
         and plan['installer_sha256']!='0'*64)
    need(sha(read(expected,0o644))==plan['installer_sha256'])


def validate_plan(value,now):
    need(type(value) is dict and set(value)=={'schema','transaction','role','revision','created_at','expires_at',
        'packet_sha256','installer_sha256','baseline','paired_policy','retention_policy','files','reviews'})
    need(value['schema']=='careertrellis.retention-install-plan/v1' and hex_value(value['transaction'],32)
         and value['role'] in HOSTS and hex_value(value['revision'],40) and value['revision']!='0'*40
         and hex_value(value['packet_sha256']) and hex_value(value['installer_sha256'])
         and value['installer_sha256']!='0'*64)
    made,expires=timestamp(value['created_at']),timestamp(value['expires_at'])
    need(made<=now<expires and dt.timedelta(0)<expires-made<=dt.timedelta(minutes=15))
    baseline=value['baseline']
    need(type(baseline) is dict and set(baseline)=={'application_current','application_previous','paired_revision',
        'paired_manifest_sha256','paired_policy_sha256','files','unit_dropins','timer_sources','service_sources'})
    need(baseline['paired_revision']==PAIR_REVISION and baseline['paired_manifest_sha256']==PAIR_MANIFEST
         and baseline['paired_policy_sha256']==PAIR_POLICY
         and all(hex_value(baseline[key],40) for key in ('application_current','application_previous')))
    need(type(value['files']) is dict and type(baseline['files']) is dict)
    allowed=dict(HELPERS[value['role']]);allowed.update({'/etc/systemd/system/'+name:0o644 for name in UNITS[value['role']]})
    need(set(value['files'])<=set(allowed) and set(baseline['files'])==set(value['files']))
    if value['role']=='web':
        required={'/etc/systemd/system/careertrellis-onedrive-retention.service',
                  '/etc/systemd/system/careertrellis-onedrive-retention.timer'}
        need(required<=set(value['files']) and all(baseline['files'][path] is None for path in required))
    for path,row in value['files'].items():
        need(type(row) is dict and set(row)=={'source','sha256','mode'}
             and row['source']=='extras/'+Path(path).name and row['mode']==allowed[path]
             and hex_value(row['sha256']) and (baseline['files'][path] is None or hex_value(baseline['files'][path])))
    need(type(baseline['unit_dropins']) is dict)
    timer_names=set(TIMERS[value['role']])|{'careertrellis-backup-health.timer'}
    need(type(baseline['timer_sources']) is dict and set(baseline['timer_sources'])==timer_names
         and all(digest is None or hex_value(digest) for digest in baseline['timer_sources'].values()))
    service_names={'careertrellis-paired-maintenance.service'} if value['role']=='web' else set()
    need(type(baseline['service_sources']) is dict and set(baseline['service_sources'])==service_names
         and all(hex_value(digest) for digest in baseline['service_sources'].values()))
    for path,digest in baseline['unit_dropins'].items():
        path=PurePosixPath(path)
        need(str(path.parent.parent)=='/etc/systemd/system' and path.parent.name.endswith('.d')
             and path.parent.name[:-2] in set(UNITS[value['role']])|timer_names|service_names
             and re.fullmatch(r'[A-Za-z0-9_.-]+\.conf',path.name) and hex_value(digest))
    paired,one=value['paired_policy'],value['retention_policy']
    need(type(paired) is dict and paired.get('schema')=='careertrellis.paired-maintenance-policy/v1'
         and paired.get('manifest_revision')==value['revision'] and paired.get('enabled') is True
         and paired.get('application_release')==baseline['application_current']
         and timestamp(paired['valid_until'])<=EXPIRY)
    need(type(one) is dict and one.get('schema')=='careertrellis.onedrive-retention-policy/v1'
         and one.get('artifact_revision')==value['revision'] and one.get('enabled') is True
         and one.get('application_release')==baseline['application_current']
         and one.get('paired_policy_sha256')==sha(canonical(paired))
         and now<timestamp(one['valid_until'])<=timestamp(paired['valid_until'])
         and one.get('reserve_percent')==20 and one.get('peak_multiplier')==4
         and one.get('keep_verified')==2 and one.get('grace_seconds')==172800
         and one.get('namespace_budget_bytes')==512*1024**2)
    need(type(value['reviews']) is dict and set(value['reviews'])=={'source_review','native_controls','readiness'}
         and all(hex_value(digest) for digest in value['reviews'].values()))
    return value


def package(folder,schema,revision):
    with directory(folder,0o755) as fd:names=set(os.listdir(fd))
    manifest_raw=read(folder/'manifest.json',0o644,65536);manifest=decode(manifest_raw)
    need(type(manifest) is dict and set(manifest)=={'schema','revision','files'}
         and manifest['schema']==schema and manifest['revision']==revision and type(manifest['files']) is dict
         and names==set(manifest['files'])|{'manifest.json'} and len(names)<=65)
    result={'manifest.json':manifest_raw}
    for name,digest in manifest['files'].items():
        need(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\.py',name) and hex_value(digest))
        raw=read(folder/name,0o644);need(sha(raw)==digest and b'\r' not in raw);ast.parse(raw);result[name]=raw
    # Manifest and bootstrap's literal inventory must describe exactly the same
    # bytes, without executing any source from the incoming packet.
    tree=ast.parse(result['entry.py']);inventories=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='FILES' for target in node.targets):
            value=node.value
            if isinstance(value,ast.Call):
                need(isinstance(value.func,ast.Name) and value.func.id=='frozenset' and len(value.args)==1 and not value.keywords)
                value=value.args[0]
            inventories.append(ast.literal_eval(value))
    need(len(inventories)==1 and len(set(inventories[0]))==len(inventories[0])
         and set(inventories[0])==set(manifest['files']))
    need(sum(map(len,result.values()))<=64*1024**2)
    return result


def terminal(unit):
    state=show(unit,('LoadState','ActiveState','MainPID','ControlPID','Job'))
    need(state['LoadState'] in ('loaded','not-found') and state['ActiveState'] in ('inactive','failed')
         and state['MainPID']==state['ControlPID']=='0' and not state['Job'] and empty_group(unit))


def idle(plan):
    need(not os.path.lexists('/run/careertrellis-hosted-activation.lease'))
    role=plan['role'];patterns=['careertrellis-paired-*.service','careertrellis-onedrive-bundle-*.service',
        'careertrellis-pgbackrest-*.service','careertrellis-backup-health.service',
        'careertrellis-onedrive-retention.service']
    raw=command(['/usr/bin/systemctl','list-units','--all','--plain','--no-legend',*patterns])
    names=[]
    for row in raw.decode('ascii').splitlines():
        parts=row.split();need(parts and re.fullmatch(r'careertrellis-[A-Za-z0-9@_.-]+\.service',parts[0]));names.append(parts[0])
    need(len(names)<=10000)
    for name in names:terminal(name)
    parent=Path('/sys/fs/cgroup/system.slice')
    for pattern in patterns:
        for path in parent.glob(pattern):
            need(path.name in names or empty_group(path.name))
    if role=='web':
        for name,key in (('current','application_current'),('previous','application_previous')):
            link=Path('/opt/careertrellis/staging')/name
            need(link.is_symlink() and link.resolve(strict=True)==Path('/opt/careertrellis/staging/releases')/plan['baseline'][key])
    need(command(['/usr/local/sbin/careertrellis-authority','status']).strip()==b'postgres-r2-authoritative')


def require_first_attempt():
    """Any earlier first-install attempt is a permanent reconciliation fence.

    In particular, a stopped timer after a failed attempt must never become a
    different transaction's supposedly original baseline. Review material is
    the only permitted pre-existing subtree; no history is removed or retried.
    """
    import itertools
    with directory(EVIDENCE,0o700) as fd:
        with os.scandir(fd) as rows:names=[row.name for row in itertools.islice(rows,10001)]
        need(len(names)<=10000)
    for name in names:
        if re.fullmatch('[0-9a-f]{32}',name):
            raise Refused('retention_install_prior_attempt_requires_reconciliation')
        need(name=='reviews')
        with directory(EVIDENCE/name,0o700):pass


@contextmanager
def locked(plan):
    import fcntl
    fds=[]
    try:
        for name in LOCKS[plan['role']]:
            path=Path(name)
            with directory(path.parent):pass
            fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK);fds.append((fd,path))
            meta=os.fstat(fd)
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid==meta.st_gid==0 and meta.st_nlink==1
                 and stat.S_IMODE(meta.st_mode)==0o600 and meta.st_size==0)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            need(identity(meta)==identity(path.lstat()))
        yield
        for fd,path in fds:need(identity(os.fstat(fd))==identity(path.lstat()))
    finally:
        for fd,_path in reversed(fds):os.close(fd)


def preflight(plan):
    require_first_attempt()
    idle(plan)
    for path,mode in ((ONE_POLICY.parent,0o700),(ONE_PUBLIC.parent,0o755),
                      (Path('/var/lib/careertrellis/onedrive-retention'),0o700)):
        if os.path.lexists(path):
            with directory(path,mode) as fd:need(not os.listdir(fd))
    for name in ('.paired_maintenance.retention-'+plan['transaction'],
                 '.onedrive_retention.retention-'+plan['transaction'],
                 '.paired-maintenance-before-retention-'+plan['transaction']):
        need(not os.path.lexists(BASE/name))
    old_manifest=read(BASE/'paired_maintenance'/'manifest.json',0o644,65536)
    need(sha(old_manifest)==PAIR_MANIFEST)
    old_pair=package(BASE/'paired_maintenance','careertrellis.paired-maintenance-install/v1',PAIR_REVISION)
    # The fixed accepted 493 manifest authenticates this pure policy validator.
    # Incoming package code is not executed during preparation.
    namespace={'__name__':'retention_install_verified_policy','__file__':str(BASE/'paired_maintenance'/'common.py')}
    exec(compile(old_pair['common.py'],namespace['__file__'],'exec'),namespace)
    validated=namespace['Policy'](plan['paired_policy'])
    need(validated.data==plan['paired_policy'])
    old_policy=read(POLICY);need(sha(old_policy)==PAIR_POLICY and read(PAIR_PUBLIC,0o644)==old_policy)
    old_document=decode(old_policy)
    allowed_policy_changes={'manifest_revision','artifact_hashes','policy_label','source_custody_reviewed_at'}
    for key in set(old_document)|set(plan['paired_policy']):
        if key not in allowed_policy_changes:need(old_document.get(key)==plan['paired_policy'].get(key))
    old_pins=old_document.get('artifact_hashes',{});new_pins=plan['paired_policy'].get('artifact_hashes',{})
    need(type(new_pins) is dict and set(new_pins)==set(old_pins))
    for role,values in old_pins.items():
        need(type(values) is dict and set(new_pins[role])==set(values))
        for path,digest in values.items():
            if path in plan['files'] and role==plan['role']:
                need(new_pins[role][path]==plan['files'][path]['sha256'])
            elif new_pins[role][path]!=digest:
                # The same reviewed policy is installed on both hosts. Remote
                # role bytes are not claimed verified by this local installer.
                need(role!=plan['role'] and path in HELPERS[role] and hex_value(new_pins[role][path]))
    need(not os.path.lexists(BASE/'onedrive_retention') and not os.path.lexists(ACCEPTED))
    need(not os.path.lexists(ONE_POLICY) and not os.path.lexists(ONE_PUBLIC))
    packet=PACKETS/plan['transaction']
    with directory(packet,0o700):pass
    packet_raw=read(packet/'source-packet.json',0o644,65536)
    need(sha(packet_raw)==plan['packet_sha256']);summary=decode(packet_raw)
    need(summary.get('revision')==plan['revision'] and summary.get('installation_performed') is False
         and summary.get('native_acceptance') is False)
    incoming={}
    for name,schema in (('paired_maintenance','careertrellis.paired-maintenance-install/v1'),
                        ('onedrive_retention','careertrellis.onedrive-retention-install/v1')):
        incoming[name]=package(packet/name,schema,plan['revision'])
        need(summary['manifests'][name]==sha(incoming[name]['manifest.json']))
    extras={};old_extras={}
    for target,row in plan['files'].items():
        extras[target]=read(packet/row['source'],0o644);need(sha(extras[target])==row['sha256'])
        expected=plan['baseline']['files'][target]
        if expected is None:need(not os.path.lexists(target))
        else:
            old_extras[target]=read(target,row['mode']);need(sha(old_extras[target])==expected)
        if target.startswith('/etc/systemd/system/'):
            name=Path(target).name
            for suffix in ('.d',''):
                need(not os.path.lexists('/run/systemd/system/'+name+suffix))
            dropin=Path(target+'.d');actual={}
            if os.path.lexists(dropin):
                with directory(dropin) as fd:names=os.listdir(fd)
                need(len(names)<=64)
                for filename in names:
                    need(re.fullmatch(r'[A-Za-z0-9_.-]+\.conf',filename))
                    actual[str(dropin/filename)]=sha(read(dropin/filename,0o644))
            expected={path:digest for path,digest in plan['baseline']['unit_dropins'].items()
                      if Path(path).parent==dropin}
            need(actual==expected)
    reviews={}
    subject=sha(canonical({key:value for key,value in plan.items() if key!='reviews'}))
    for kind,digest in plan['reviews'].items():
        raw=read(EVIDENCE/'reviews'/plan['transaction']/(kind+'.json'))
        need(sha(raw)==digest);review=decode(raw)
        if kind=='readiness':
            need(review.get('schema')=='careertrellis.retention-install-readiness/v1'
                 and review.get('result')=='pass' and review.get('role')==plan['role']
                 and review.get('transaction')==plan['transaction']
                 and review.get('packet_sha256')==plan['packet_sha256']
                 and review.get('application_current')==plan['baseline']['application_current']
                 and review.get('application_previous')==plan['baseline']['application_previous']
                 and type(review.get('checks')) is dict and set(review['checks'])==READINESS
                 and all(value is True for value in review['checks'].values())
                 and dt.timedelta(0)<=dt.datetime.now(dt.timezone.utc)-timestamp(review['observed_at'])<=dt.timedelta(minutes=5))
            reviews[kind]=raw;continue
        need(review.get('schema')=='careertrellis.retention-install-review/v1' and review.get('result')=='pass'
             and review.get('kind')==kind and review.get('revision')==plan['revision']
             and review.get('role')==plan['role'] and review.get('packet_sha256')==plan['packet_sha256']
             and review.get('subject_sha256')==subject
             and dt.timedelta(0)<=dt.datetime.now(dt.timezone.utc)-timestamp(review['reviewed_at'])<=dt.timedelta(hours=48))
        reviews[kind]=raw
    status_path=Path('/var/lib/careertrellis/paired-maintenance')/plan['role']/'status.json'
    old_status=read(status_path);status=decode(old_status)
    need(status.get('schema')=='careertrellis.paired-maintenance-status/v1'
         and status.get('policy_sha256')==PAIR_POLICY and status.get('phase')=='idle'
         and status.get('result')=='ok' and status.get('services_restored') is True
         and status.get('boot_id')==Path('/proc/sys/kernel/random/boot_id').read_text().strip()
         and status.get('release')==plan['baseline']['application_current'])
    marker_path=Path('/var/lib/careertrellis/paired-maintenance/writer-interlock.json')
    marker=read(marker_path,0o644) if plan['role']=='web' else None
    if marker is not None:
        value=decode(marker);need(value.get('policy_sha256')==PAIR_POLICY and value.get('phase')=='idle'
            and value.get('release')==plan['baseline']['application_current'])
    timers={}
    for name,digest in plan['baseline']['service_sources'].items():
        target=Path('/etc/systemd/system')/name
        need(sha(read(target,0o644))==digest)
        state=show(name,('LoadState','FragmentPath','DropInPaths','NeedDaemonReload'))
        need(state['LoadState']=='loaded' and state['FragmentPath']==str(target) and state['NeedDaemonReload']=='no')
        expected={path:value for path,value in plan['baseline']['unit_dropins'].items()
                  if PurePosixPath(path).parent==PurePosixPath(str(target)+'.d')}
        actual={};folder=Path(str(target)+'.d')
        if os.path.lexists(folder):
            with directory(folder) as fd:names=os.listdir(fd)
            need(len(names)<=64)
            for filename in names:
                need(re.fullmatch(r'[A-Za-z0-9_.-]+\.conf',filename))
                actual[str(folder/filename)]=sha(read(folder/filename,0o644))
        need(actual==expected and set(state['DropInPaths'].split())==set(expected))
        for prefix in ('/run/systemd/system','/run/systemd/system.control','/etc/systemd/system.control'):
            for suffix in ('','.d'):need(not os.path.lexists(prefix+'/'+name+suffix))
    for name in sorted(set(TIMERS[plan['role']])|{'careertrellis-backup-health.timer'}):
        state=show(name,('LoadState','ActiveState','UnitFileState','Job','NextElapseUSecRealtime','FragmentPath','DropInPaths'))
        need(state['ActiveState'] in ('active','inactive') and state['Job']==''
             and state['UnitFileState'] in ('enabled','disabled','static','enabled-runtime',''))
        expected=plan['baseline']['timer_sources'][name]
        target=Path('/etc/systemd/system')/name
        if expected is None:
            need(state['LoadState']=='not-found' and state['FragmentPath']=='' and state['DropInPaths']==''
                 and state['UnitFileState']=='' and state['ActiveState']=='inactive' and not os.path.lexists(target))
        else:
            need(state['LoadState']=='loaded' and state['FragmentPath']==str(target)
                 and sha(read(target,0o644))==expected)
            expected_dropins={path:digest for path,digest in plan['baseline']['unit_dropins'].items()
                              if PurePosixPath(path).parent==PurePosixPath(str(target)+'.d')}
            actual={}
            dropin=Path(str(target)+'.d')
            if os.path.lexists(dropin):
                with directory(dropin) as fd:names=os.listdir(fd)
                need(len(names)<=64)
                for filename in names:
                    need(re.fullmatch(r'[A-Za-z0-9_.-]+\.conf',filename))
                    actual[str(dropin/filename)]=sha(read(dropin/filename,0o644))
            need(actual==expected_dropins and set(state['DropInPaths'].split())==set(expected_dropins))
        for suffix in ('','.d'):
            need(not os.path.lexists('/run/systemd/system/'+name+suffix))
        timers[name]=state
    if plan['role']=='web':
        state=show('careertrellis-onedrive-retention.timer',('LoadState','ActiveState','UnitFileState','Job'))
        need(state=={'LoadState':'not-found','ActiveState':'inactive','UnitFileState':'','Job':''})
    peak=sum(len(raw) for files in incoming.values() for raw in files.values())*3
    peak+=sum(map(len,extras.values()))*3+sum(map(len,old_extras.values()))*2+1024**2
    free=os.statvfs(BASE);need(free.f_bavail*free.f_frsize>=5*1024**3+peak and free.f_favail>=512)
    return {'old_pair':old_pair,'old_policy':old_policy,'incoming':incoming,'extras':extras,'old_extras':old_extras,
        'status_path':status_path,'old_status':old_status,'marker_path':marker_path,'old_marker':marker,'timers':timers,
        'conservative_peak_bytes':peak}


def replace(path,raw,mode,transaction):
    path=Path(path);temporary=path.parent/('.'+path.name+'.retention-'+transaction)
    write_once(temporary,raw,mode)
    os.replace(temporary,path)
    with directory(path.parent) as fd:os.fsync(fd)
    need(read(path,mode)==raw)


def initialize_state(role):
    """Create the exact empty first-install census, never repair existing state.

    The caller's preflight has already rejected a nonempty new namespace. Only
    the role directory created by this invocation may be populated here.
    """
    need(role in HOSTS)
    root=Path('/var/lib/careertrellis/onedrive-retention')/role
    with directory(root,0o700) as fd:need(not os.listdir(fd))
    write_once(root/'control.lock',b'')
    if role=='db':
        mkdir(root/'catalog')
        for name in ('accepted','intents','complete','holds','tombstones','tombstone-intents','tombstone-complete'):
            mkdir(root/'catalog'/name)
        write_once(root/'catalog'/'catalog.lock',b'')
        mkdir(root/'retirements')
        mkdir(root/'schedule')


def restore_prior_timers(plan,previous,root):
    """First-phase installation preserves every existing backup schedule.

    Retiring the legacy mirror is a separate transition after two verified
    replacements; installation never performs that transition implicitly.
    """
    need(set(previous)==set(TIMERS[plan['role']])|{'careertrellis-backup-health.timer'})
    restored={}
    for name,before in previous.items():
        current=show(name,('ActiveState','UnitFileState','Job'))
        expected_file_state=before['UnitFileState']
        if before['LoadState']=='not-found':
            need(before['ActiveState']=='inactive' and current['UnitFileState'] in ('disabled','static'))
            expected_file_state=current['UnitFileState']
        need(current['UnitFileState']==expected_file_state and current['Job']=='')
        if before['ActiveState']=='active':command(['/usr/bin/systemctl','start',name],timeout=30)
        after=show(name,('ActiveState','UnitFileState','Job'))
        need(after['ActiveState']==before['ActiveState'] and after['UnitFileState']==expected_file_state
             and after['Job']=='');restored[name]=after
    write_once(root/'timer-restoration.json',canonical({'schema':'careertrellis.retention-timer-restoration/v1',
        'states':restored,'old_mirror_timer_held':False,'legacy_timer_transition_performed':False,
        'retention_timer_activated':False}))
    return restored


def seal_after_restoration(plan,previous,root,complete):
    """Publish capability only after every prior timer state is restored.

    Restoration failure leaves only the original intent/draft artifacts. The
    accepted baseline is the last durable publication, after the final seal;
    no consumer can authenticate an earlier incomplete attempt.
    """
    restore_prior_timers(plan,previous,root)
    restoration_raw=read(root/'timer-restoration.json')
    restoration=decode(restoration_raw)
    need(restoration.get('schema')=='careertrellis.retention-timer-restoration/v1'
         and restoration.get('old_mirror_timer_held') is False
         and restoration.get('legacy_timer_transition_performed') is False
         and restoration.get('retention_timer_activated') is False)
    complete=dict(complete,completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  timer_restoration_sha256=sha(restoration_raw),prior_timer_states_restored=True)
    write_once(root/'complete.json',canonical(complete))
    baseline={'schema':'careertrellis.onedrive-retention-accepted-baseline/v1',
        'standalone_revision':plan['revision'],'standalone_manifest_sha256':complete['standalone_manifest_sha256'],
        'paired_revision':plan['revision'],'paired_manifest_sha256':complete['paired_manifest_sha256'],
        'paired_policy_sha256':sha(canonical(plan['paired_policy'])),
        'application_release':plan['baseline']['application_current'],
        'accepted_at':complete['completed_at'],'valid_until':plan['retention_policy']['valid_until'],
        'acceptance_receipts':{'ops':sha(canonical(complete)),
            'native_controls':plan['reviews']['native_controls'],'independent_review':plan['reviews']['source_review']}}
    write_once(root/'sealed.json',canonical({'schema':'careertrellis.retention-install-seal/v1',
        'accepted_baseline_sha256':sha(canonical(baseline)),'install_receipt_sha256':sha(canonical(complete)),
        'recurring_activation_performed':False}))
    write_once(ACCEPTED,canonical(baseline))
    return complete


def install():
    need(os.name=='posix' and os.geteuid()==os.getegid()==0)
    raw=read(PLAN);plan=validate_plan(decode(raw),dt.datetime.now(dt.timezone.utc))
    require_installer_source(plan)
    need(socket.gethostname()==HOSTS[plan['role']]);birth=owner(plan)
    root=EVIDENCE/plan['transaction']
    need(not os.path.lexists(root))
    with locked(plan):
        observed=preflight(plan)
        mkdir(root)
        write_once(root/'intent.json',canonical({'schema':'careertrellis.retention-install-intent/v1',
            'plan_sha256':sha(raw),'owner':birth,'revision':plan['revision'],'role':plan['role'],
            'timer_states':observed['timers'],'conservative_peak_bytes':observed['conservative_peak_bytes']}))
        write_once(root/'plan.json',raw)
        write_once(root/'old-paired-policy.json',observed['old_policy'])
        write_once(root/'old-status.json',observed['old_status'])
        if observed['old_marker'] is not None:write_once(root/'old-writer-interlock.json',observed['old_marker'])
        for index,(target,data) in enumerate(sorted(observed['old_extras'].items())):
            write_once(root/('old-extra-%02d'%index),data)
        write_once(root/'old-extra-map.json',canonical({target:{'file':'old-extra-%02d'%index,'sha256':sha(data)}
            for index,(target,data) in enumerate(sorted(observed['old_extras'].items()))}))
        # No restoration on exception: exact stopped state and original bytes
        # remain journaled for independently verified recovery.
        for name,state in observed['timers'].items():
            if state['ActiveState']=='active':
                write_once(root/('stop-'+name+'.json'),canonical({'unit':name,'before':state}))
                command(['/usr/bin/systemctl','stop',name],timeout=30)
        idle(plan);need(read(PLAN)==raw)
        for path,mode in ((ONE_POLICY.parent,0o700),(ONE_PUBLIC.parent,0o755),
                          (Path('/var/lib/careertrellis/onedrive-retention'),0o700),
                          (Path('/var/lib/careertrellis/onedrive-retention')/plan['role'],0o700)):
            if not os.path.lexists(path):mkdir(path,mode)
            with directory(path,mode):pass
        initialize_state(plan['role'])
        for name,files in observed['incoming'].items():
            stage=BASE/('.'+name+'.retention-'+plan['transaction']);mkdir(stage,0o755)
            for filename,data in files.items():write_once(stage/filename,data,0o644)
            package(stage,'careertrellis.'+('onedrive-retention' if name=='onedrive_retention' else 'paired-maintenance')+'-install/v1',plan['revision'])
        # Rename within the installation filesystem; old package remains intact
        # under an exact retained rollback path. There is no deletion.
        rollback=BASE/('.paired-maintenance-before-retention-'+plan['transaction'])
        need(not os.path.lexists(rollback))
        write_once(root/'package-exchange-intent.json',canonical({'rollback':str(rollback),
            'old_manifest_sha256':PAIR_MANIFEST,'new_revision':plan['revision']}))
        need(read(BASE/'paired_maintenance'/'manifest.json',0o644)==observed['old_pair']['manifest.json']
             and read(POLICY)==observed['old_policy'] and read(PAIR_PUBLIC,0o644)==observed['old_policy']
             and read(observed['status_path'])==observed['old_status']
             and not os.path.lexists(BASE/'onedrive_retention'))
        os.rename(BASE/'paired_maintenance',rollback)
        os.rename(BASE/('.paired_maintenance.retention-'+plan['transaction']),BASE/'paired_maintenance')
        os.rename(BASE/('.onedrive_retention.retention-'+plan['transaction']),BASE/'onedrive_retention')
        with directory(BASE) as fd:os.fsync(fd)
        for target,data in observed['extras'].items():
            if target in observed['old_extras']:
                need(read(target,plan['files'][target]['mode'])==observed['old_extras'][target])
            else:need(not os.path.lexists(target))
            replace(target,data,plan['files'][target]['mode'],plan['transaction'])
        for target,mode,data in ((POLICY,0o600,canonical(plan['paired_policy'])),
            (PAIR_PUBLIC,0o644,canonical(plan['paired_policy'])),(ONE_POLICY,0o600,canonical(plan['retention_policy'])),
            (ONE_PUBLIC,0o644,canonical(plan['retention_policy']))):
            with directory(target.parent):pass
            replace(target,data,mode,plan['transaction'])
        new_status=decode(observed['old_status']);new_status['policy_sha256']=sha(canonical(plan['paired_policy']))
        replace(observed['status_path'],canonical(new_status),0o600,plan['transaction'])
        if observed['old_marker'] is not None:
            marker=decode(observed['old_marker']);marker['policy_sha256']=new_status['policy_sha256']
            replace(observed['marker_path'],canonical(marker),0o644,plan['transaction'])
        command(['/usr/bin/systemctl','daemon-reload'],timeout=30)
        if plan['role']=='web':
            retained=show('careertrellis-onedrive-retention.timer',('ActiveState','UnitFileState','Job'))
            need(retained['ActiveState']=='inactive' and retained['UnitFileState']=='disabled' and retained['Job']=='')
        # Installed verification is source-only; no provider or repository walk.
        revision=command(['/usr/bin/python3','-I','-B',str(BASE/'onedrive_retention'/'entry.py'),'verify-install'])
        verified=json.loads(revision)
        need(type(verified) is dict and verified.get('result')=='source_verified'
             and verified.get('revision')==plan['revision'] and verified.get('native_actions') is False)
        for name,files in observed['incoming'].items():
            for filename,data in files.items():need(read(BASE/name/filename,0o644)==data)
        idle(plan);need(read(PLAN)==raw)
        complete={'schema':'careertrellis.retention-install/v1','result':'installed_timers_held',
            'revision':plan['revision'],'role':plan['role'],'transaction':plan['transaction'],
            'plan_sha256':sha(raw),'source_packet_sha256':plan['packet_sha256'],
            'owner':birth,'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'paired_manifest_sha256':sha(observed['incoming']['paired_maintenance']['manifest.json']),
            'standalone_manifest_sha256':sha(observed['incoming']['onedrive_retention']['manifest.json']),
            'application_pointers_changed':False,'application_services_restarted':False,
            'provider_calls':0,'backup_operations':0,'retention_timer_activated':False,
            'legacy_timer_transition_performed':False,
            'rollback_package':str(rollback)}
        write_once(root/'installed-draft.json',canonical(complete))
        complete=seal_after_restoration(plan,observed['timers'],root,complete)
        return {'result':'installed_timers_held','revision':plan['revision'],
            'receipt_sha256':sha(canonical(complete)),'recurring_activation_performed':False}


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    try:
        need(args==['install']);value=install();print(canonical(value).decode('ascii'),end='');return 0
    except BaseException:
        print('{"result":"refused","category":"retention_install_requires_reconciliation","automatic_retry":false}')
        return 1


if __name__=='__main__':raise SystemExit(main())
