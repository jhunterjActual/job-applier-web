"""One-shot, additive web/DB tools installation. No application activation.

Run reviewed bytes as root with exact ops commit and archive SHA256. Refuses an
existing destination, including an incomplete attempt; never cleans it up/retries.
"""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import pwd
import shutil
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

RELEASES=Path('/opt/careertrellis/staging/releases')
CURRENT='cafad5b48a579adc80695519f0f8b7d39f621cd5'
BASELINE_BUILD='20260928.78'
SCHEMA='20260929_0021'
EVIDENCE='schema21-coordinator-installation'
RUNTIME='4a7bf3c46d3b401616c7bfaefacb22c7c7ff30ab'
SERVICES=('careertrellis-web.service','careertrellis-worker.service','careertrellis-erasure-worker.service')
FILES=('staging.env','auth-v2.env','admission-web.json','postgres-runtime.env','postgres-migration.env',
    'object-storage.env','search-worker.env','postgres-worker.env','provider-secrets.env',
    'account-erasure-worker.env','postgres-erasure-worker.env','account-erasure-object-storage.env')
ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','CAREERTRELLIS_CONFIG_READ_ONLY':'1','PYTHONDONTWRITEBYTECODE':'1'}
# Identity-only installation; monitoring remains pinned to its separate release.
DB_TESTS=('test_identity_plan_packet','test_identity_plan_store','test_identity_plan_operator',
    'test_identity_plan_transfer','test_identity_receipt_transfer','test_identity_entry',
    'test_schema16_coordination','test_schema21_coordination','test_identity_selection_barrier',
    'test_schema21_coordinator_installation')
WEB_TESTS=(*DB_TESTS,'test_identity_transition_operator','test_identity_web_handoff',
    'test_identity_evidence','test_identity_configuration','test_identity_plan_supersession',
    'test_identity_operator','test_owner_recovery','test_owner_recovery_bridge',
    'test_owner_recovery_bridge_installer')
REQUIRED=('identity_web_handoff','identity_configuration','identity_entry','identity_selection_barrier','identity_operator','identity_evidence',
    'session_v2_transition','session_authorization','totp_configuration','identity_plan_operator','identity_plan_packet',
    'identity_plan_store','identity_plan_supersession','identity_plan_transfer','identity_receipt_transfer',
    'identity_transition_operator','owner_recovery','owner_recovery_bridge')
REQUIRED_FILES=('deploy/scripts/manage-admission-operations.sh',
    'deploy/scripts/install-schema21-coordinator.py')


def bindings(commit,digest,application):
    require(type(commit) is str and re.fullmatch('[0-9a-f]{40}',commit)
        and type(application) is str and re.fullmatch('[0-9a-f]{40}',application)
        and type(digest) is str and re.fullmatch('[0-9a-f]{64}',digest))
    require(commit not in (application,CURRENT,RUNTIME) and application not in (CURRENT,RUNTIME))


def baseline(current,version):
    import ast
    require(current==RELEASES/CURRENT)
    tree=ast.parse(version)
    values=[node.value for node in tree.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='APP_BUILD' for target in node.targets)]
    require(len(values)==1 and ast.literal_eval(values[0])==BASELINE_BUILD)


def source_inventory(manifest):
    require(all('backend/'+name+'.py' in manifest for name in (*REQUIRED,*WEB_TESTS))
        and all(name in manifest for name in REQUIRED_FILES)
        and not any(name.startswith('backend/migrations/versions/') for name in manifest))

def require(value):
    if not value: raise RuntimeError('Installation gate failed.')


def command(argv,*,data=None,timeout=30,cwd=None):
    result=subprocess.run(argv,input=data,capture_output=True,env=ENV,timeout=timeout,cwd=cwd)
    require(result.returncode==0)
    return result.stdout


def protected(path,*,modes=(0o600,0o640,0o644,0o755),owners=(0,)):
    for parent in path.parents:
        item=parent.lstat()
        require(stat.S_ISDIR(item.st_mode) and item.st_uid in owners and not item.st_mode&0o022)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        item=os.fstat(fd)
        require(stat.S_ISREG(item.st_mode) and item.st_uid in owners and item.st_nlink==1
            and stat.S_IMODE(item.st_mode) in modes and item.st_size<=1048576)
        return os.read(fd,1048577)
    finally: os.close(fd)


def snapshot(side='web'):
    if side=='db': return database_snapshot()
    require(side=='web')
    baseline(Path('/opt/careertrellis/staging/current').resolve(),
        protected(RELEASES/CURRENT/'backend/version.py'))
    require(not Path('/run/careertrellis-hosted-activation.lease').exists())
    require(command(['/usr/local/sbin/careertrellis-authority','status']).strip()==b'postgres-r2-authoritative')
    files={str(Path('/etc/careertrellis')/name):protected(Path('/etc/careertrellis')/name,modes=(0o600,)) for name in FILES}
    require(re.search(rb'(?m)^CAREERTRELLIS_AUTH_V2_ENABLED=0\r?$',files['/etc/careertrellis/auth-v2.env']))
    for name in SERVICES:
        for suffix in ('','.d/hosted-authority.conf'):
            path=Path('/etc/systemd/system')/(name+suffix); files[str(path)]=protected(path)
    files['authority_helper']=protected(Path('/usr/local/sbin/careertrellis-authority'))
    state={name:command(['/usr/bin/systemctl','show',name,'--property=ActiveState','--property=MainPID','--property=NRestarts']) for name in SERVICES}
    require(all(b'ActiveState=active\n' in value for value in state.values()))
    return {'files':files,'services':state,'current':str(Path('/opt/careertrellis/staging/current').resolve()),
        'previous':str(Path('/opt/careertrellis/staging/previous').resolve())}


def database_snapshot():
    owners=(0,pwd.getpwnam('postgres').pw_uid)
    base=Path('/etc/postgresql/18/main')
    files={name:protected(base/name,owners=owners) for name in ('postgresql.conf','pg_hba.conf','pg_ident.conf')}
    state=command(['/usr/bin/systemctl','show','postgresql@18-main.service',
        '--property=ActiveState','--property=MainPID','--property=NRestarts'])
    require(b'ActiveState=active\n' in state)
    query="SELECT json_build_object('database',current_database(),'user',session_user," \
        "'version',current_setting('server_version_num'),'data',current_setting('data_directory')," \
        "'head',(SELECT version_num FROM careertrellis.alembic_version)," \
        "'policy',(SELECT row_to_json(p) FROM careertrellis_admission.policy p)," \
        "'roles',(SELECT json_object_agg(rolname,rolcanlogin) FROM pg_roles WHERE rolname IN " \
        "('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker')));"
    raw=command(['/usr/sbin/runuser','-u','postgres','--','/usr/bin/psql','-X','-A','-t',
        '-v','ON_ERROR_STOP=1','-d','careertrellis','-c',query])
    value=json.loads(raw)
    require(value['database']=='careertrellis' and value['user']=='postgres' and
        180000<=int(value['version'])<190000 and value['data']=='/var/lib/postgresql/18/main' and
        value['head']=='20260907_0014' and value['policy']['minimum_session_version']==1 and
        value['policy']['maintenance'] is False and value['policy']['signup_open'] is False and
        value['roles']==dict.fromkeys(('careertrellis_runtime','careertrellis_worker',
            'careertrellis_erasure_worker','careertrellis_admission_broker'),True))
    return {'files':files,'services':state,'database':value}


def native_tests(target,evidence,side,python):
    names=WEB_TESTS if side=='web' else DB_TESTS
    tests="import sys,unittest; sys.path.insert(0,sys.argv[1]); suite=unittest.defaultTestLoader.loadTestsFromNames("+repr(names)+"); result=unittest.TextTestRunner().run(suite); sys.exit(not result.wasSuccessful() or bool(result.skipped))"
    result=subprocess.run([str(python),'-I','-B',*(['-S'] if side=='db' else []),'-c',tests,str(target/'backend')],
        capture_output=True,env=ENV,timeout=180)
    record(evidence/'native-tests.json',{'returncode':result.returncode,'stdout':result.stdout.decode(),'stderr':result.stderr.decode()})
    require(result.returncode==0)


def record(path,value):
    raw=(json.dumps(value,sort_keys=True)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        os.write(fd,raw); os.fsync(fd)
    finally: os.close(fd)


def runtime_manifest(root,relocated=None):
    result={}
    for path in [root,*root.rglob('*')]:
        item=path.lstat(); relative=path.relative_to(root).as_posix()
        require(item.st_uid==0)
        if path.is_symlink():
            resolved=path.resolve()
            require(resolved.is_relative_to(root) or (path.parent==root/'bin' and
                path.name in ('python','python3','python3.12') and resolved==Path('/usr/bin/python3').resolve()))
            require(not resolved.is_relative_to(root) or not Path(os.readlink(path)).is_absolute())
            result[relative]=('link',os.readlink(path))
        else:
            require(not item.st_mode&0o022 and (path.is_file() or path.is_dir()))
            if path.is_file():
                content=path.read_bytes()
                if relocated is not None and (path.parent==root/'bin' or path==root/'pyvenv.cfg') and not content.startswith(b'\x7fELF'):
                    content=content.replace(str(root).encode(),str(relocated).encode())
                result[relative]=('file',hashlib.sha256(content).hexdigest())
    return result


def install(commit,digest,application):
    hosts={'careertrellis-staging-web-1':'web','job-applier-db-staging-01':'db'}
    require(os.geteuid()==0 and os.uname().nodename in hosts)
    side=hosts[os.uname().nodename]
    bindings(commit,digest,application)
    target=RELEASES/commit
    require(not target.exists() and not target.is_symlink())
    archive=Path('/var/tmp')/('careertrellis-schema21-coordinator-'+commit+'.tar')
    fd=os.open(archive,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        item=os.fstat(fd)
        require(stat.S_ISREG(item.st_mode) and item.st_uid==0 and stat.S_IMODE(item.st_mode)==0o600
            and item.st_nlink==1 and 0<item.st_size<100000000)
        with os.fdopen(fd,'rb',closefd=False) as stream: raw=stream.read(100000000)
    finally: os.close(fd)
    require(hashlib.sha256(raw).hexdigest()==digest)
    runtime=RELEASES/RUNTIME/'backend/venv'
    free=shutil.disk_usage(RELEASES).free
    if side=='web':
        require(runtime.is_dir() and not runtime.is_symlink())
        runtime_before=runtime_manifest(runtime)
        expected_runtime=runtime_manifest(runtime,target/'backend/venv')
    before=snapshot(side)
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
        members=source.getmembers(); names=set(); manifest={}; source_bytes=0
        for member in members:
            path=Path(member.name)
            require(not path.is_absolute() and '..' not in path.parts and member.name not in names
                and '\\' not in member.name and ':' not in member.name
                and str(path)==member.name.rstrip('/')
                and (member.isdir() or member.isfile()) and not member.mode&0o022)
            require(not any(part in ('.git','.codex','venv','data','__pycache__',EVIDENCE) or
                (part.startswith('.env') and member.name!='backend/.env.example') for part in path.parts))
            names.add(member.name)
            if member.isfile():
                source_bytes+=member.size
                require(0<=member.size<=10000000 and source_bytes<=100000000)
                manifest[member.name]=hashlib.sha256(source.extractfile(member).read()).hexdigest()
        require(all(not any(str(parent) in manifest for parent in Path(name).parents) for name in manifest))
        source_inventory(manifest)
        runtime_bytes=sum(path.lstat().st_size for path in runtime.rglob('*')
            if stat.S_ISREG(path.lstat().st_mode)) if side=='web' else 0
        peak=source_bytes+runtime_bytes+512*1024*1024
        require(free-peak>=5*1024*1024*1024)
        target.mkdir(mode=0o755)
        evidence=target/EVIDENCE; evidence.mkdir(mode=0o700)
        record(evidence/'started.json',{'commit':commit,'archive_sha256':digest,'host_role':side,
            'application':application,'schema':SCHEMA,'baseline_application':CURRENT,
            'application_preparation_verified':False,'migration_performed':False,'activation_performed':False,
            'free_before':free,'planned_additional_bytes':peak,
            'runtime_source':RUNTIME if side=='web' else '/usr/bin/python3','phase':'started'})
        source.extractall(target,filter='data')
    for path in target.rglob('*'):
        require(not path.is_symlink())
        if path==evidence or evidence in path.parents: continue
        path.chmod(0o755 if path.is_dir() or (path.stat().st_mode&0o111) else 0o644)
    launcher=target/'deploy/scripts/manage-admission-operations.sh'; launcher.chmod(0o755)
    for name in REQUIRED_FILES:
        if name.endswith('.sh'): (target/name).chmod(0o755)
    for name,expected in manifest.items(): require(hashlib.sha256((target/name).read_bytes()).hexdigest()==expected)
    record(evidence/'source-manifest.json',manifest)
    if side=='db':
        smoke="import sys; sys.path.insert(0,sys.argv[1]); import identity_plan_transfer,identity_receipt_transfer,session_v2_transition,totp_configuration; from identity_entry import SecureTerminal"
        command(['/usr/bin/python3','-I','-S','-B','-c',smoke,str(target/'backend')])
        native_tests(target,evidence,side,'/usr/bin/python3')
        command(['/bin/bash','-n',str(launcher)])
        for name,expected in manifest.items(): require(hashlib.sha256((target/name).read_bytes()).hexdigest()==expected)
        require(snapshot(side)==before)
        record(evidence/'complete.json',{'commit':commit,'archive_sha256':digest,'source_files':len(manifest),
            'application':application,'schema':SCHEMA,'baseline_application':CURRENT,
            'phase':'installed_not_activated','scope':'schema21-identity-only','host_role':'db','native_tests':'passed_no_skips',
            'application_preparation_verified':False,'migration_performed':False,'activation_performed':False,
            'system_python_only':True,'database_configuration_and_service_unchanged':True,
            'completed_at':datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'outcome':'installed_not_activated','commit':commit,'source_files':len(manifest)}))
        return
    copied=target/'backend/venv'
    shutil.copytree(runtime,copied,symlinks=True)
    # Preserve the private dependency copy, while relocating launcher shebangs
    # and venv metadata. Never edit the source runtime or resolve file symlinks.
    for path in [copied/'pyvenv.cfg',*(copied/'bin').iterdir()]:
        if not path.is_file() or path.is_symlink(): continue
        content=path.read_bytes()
        if content.startswith(b'\x7fELF'): continue
        changed=content.replace(str(runtime).encode(),str(copied).encode())
        if changed!=content: path.write_bytes(changed)
    require(runtime_manifest(copied)==expected_runtime and runtime_manifest(runtime)==runtime_before)
    metadata="import importlib.metadata,json; print(json.dumps(sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions())))"
    require(command([str(runtime/'bin/python'),'-I','-B','-c',metadata])==command([str(copied/'bin/python'),'-I','-B','-c',metadata]))
    command([str(copied/'bin/python'),'-I','-B','-m','pip','check'])
    smoke="import sys; from pathlib import Path; root=Path(sys.argv[1]); assert Path(sys.prefix)==root; sys.path.insert(0,str(root.parent)); import identity_web_handoff as h, identity_configuration, identity_entry, identity_operator, identity_evidence, session_v2_transition, account_session_candidate, cryptography, sqlalchemy, httpx, workos; assert all(Path(m.__file__).is_relative_to(root) for m in (cryptography,sqlalchemy,httpx,workos)); h.inventory(); h.validate_files(h.read_files()); print('installed_imports_and_read_only_compatibility=passed')"
    command([str(copied/'bin/python'),'-I','-B','-c',smoke,str(copied)])
    native_tests(target,evidence,side,copied/'bin/python')
    command(['/bin/bash','-n',str(launcher)])
    for name,expected in manifest.items(): require(hashlib.sha256((target/name).read_bytes()).hexdigest()==expected)
    require(snapshot()==before)
    record(evidence/'complete.json',{'commit':commit,'archive_sha256':digest,'source_files':len(manifest),
        'application':application,'schema':SCHEMA,'baseline_application':CURRENT,
        'application_preparation_verified':False,'migration_performed':False,'activation_performed':False,
        'runtime_source':RUNTIME,'phase':'installed_not_activated','scope':'schema21-identity-only',
        'native_tests':'passed_no_skips','private_runtime_bytes_verified':True,'application_unchanged':True,'provider_configuration_unchanged':True,
        'service_pids_and_restarts_unchanged':True,'completed_at':datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'outcome':'installed_not_activated','commit':commit,'source_files':len(manifest)}))


def main():
    locks=[]
    try:
        require(len(sys.argv)==4)
        host=os.uname().nodename
        require(host in ('careertrellis-staging-web-1','job-applier-db-staging-01'))
        names=('careertrellis-maintenance.lock','careertrellis-postgres-migration.lock','careertrellis-admission-operation.lock')
        if host=='careertrellis-staging-web-1': names+=('careertrellis-provider-secret-rotation.lock',)
        for name in names:
            path=Path('/run/lock')/name
            fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW); locks.append(fd)
            item=os.fstat(fd); require(stat.S_ISREG(item.st_mode) and item.st_uid==0 and item.st_nlink==1)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        install(*sys.argv[1:])
    except Exception:
        print('{"outcome":"installation_incomplete_inspect_exact_new_bundle_do_not_retry"}')
        return 1
    finally:
        for fd in reversed(locks): os.close(fd)
    return 0


if __name__=='__main__': sys.exit(main())
