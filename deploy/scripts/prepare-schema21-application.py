"""Additive application source/private-runtime preparation; never activate.

Uses the independently installed 45e33567 helper utilities. No package install,
shared-browser write, provider call, migration or application service action.
An existing or incomplete destination is always refused and retained.
"""
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
from datetime import datetime, timezone

OPS='45e33567bc9e23746ea5ba8f929e14b180288576'
HELPER_SHA='bd3ef69ce61703d8063cf1ba90340f32c5ab56ee4b6936adf0d2f7ba01ae945b'
RELEASES=Path('/opt/careertrellis/staging/releases')
BROWSERS=Path('/var/lib/careertrellis/shared/ms-playwright')
TRANSFER=Path('/var/tmp')
REQUIRED=('backend/version.py','backend/requirements.in','backend/requirements.txt',
    'backend/dependency_lock.py','backend/app.py','backend/session_authorization.py',
    'backend/migrations/versions/20260909_0015_identity_sessions.py',
    'backend/migrations/versions/20260909_0015_identity_sessions.sql',
    'backend/migrations/versions/20260915_0016_totp_enrollment.py',
    'backend/migrations/versions/20260915_0016_totp_enrollment.sql',
    'backend/totp_configuration.py','backend/totp_enrollment.py','backend/workos_totp.py',
    'backend/totp_postgres.py','backend/totp_http.py','backend/totp_cleanup.py','backend/admission_operations.py',
    'backend/static/totp-enrollment.js',
    'backend/test_totp_enrollment.py','backend/test_totp_configuration.py','backend/test_totp_http.py',
    'backend/test_totp_enrollment_postgres.py','backend/test_totp_combined.py',
    'backend/test_identity_session_postgres.py','backend/test_provider_session_postgres.py',
    'backend/test_provider_session_candidate.py','backend/test_signup_http.py',
    'backend/test_admission_lifecycle.py','backend/signup_prototype/integration_fixture.py',
    'backend/signup_http.py','backend/account_session_candidate.py','backend/workos_transport_candidate.py',
    'backend/test_p0_regressions.py','backend/test_object_store.py','backend/test_object_import.py',
    'backend/test_stored_objects.py','backend/test_session_epoch.py','backend/test_session_epoch_http.py')

SPACE_FLOOR=5*1024*1024*1024
REHEARSAL_ALLOWANCE=512*1024*1024


def contract():
    path=Path(__file__).with_name('schema21-application-contract.py')
    for parent in path.parents:
        info=parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022)
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1
            and not info.st_mode&0o022 and info.st_size<=65536)
        with os.fdopen(descriptor,'rb',closefd=False) as stream: raw=stream.read(65537)
        require(hashlib.sha256(raw).hexdigest()==
            'fc15bb3c6a0fc9a7636bfc46c86fee26f98f5f43cc5d46c5119ef492857a8692')
        import types
        module=types.ModuleType('schema21_preparation_contract')
        exec(compile(raw,str(path),'exec'),module.__dict__)
        return module
    finally: os.close(descriptor)


def storage_budget(files,runtime,free):
    # Keep the accepted source/rollback runtime. Budget our private copy, a second
    # source copy for native tests and bounded test output before creating target.
    source_bytes=sum(len(content) for content,_ in files.values())
    runtime_bytes=sum(path.lstat().st_size for path in runtime.rglob('*')
        if stat.S_ISREG(path.lstat().st_mode))
    peak=runtime_bytes+2*source_bytes+REHEARSAL_ALLOWANCE
    require(free-peak>=SPACE_FLOOR)
    return {'free_before':free,'source_bytes':source_bytes,'runtime_copy_bytes':runtime_bytes,
        'planned_additional_bytes':peak,'reserved_free_floor':SPACE_FLOOR}


def require(value):
    if not value: raise RuntimeError('Schema21 application preparation gate failed.')


def helper():
    path=RELEASES/OPS/'deploy/scripts/install-handoff-ops.py'
    for item in (path,*path.parents):
        info=item.lstat()
        require(not item.is_symlink() and info.st_uid==0 and not info.st_mode&0o022)
    require(path.stat().st_nlink==1 and hashlib.sha256(path.read_bytes()).hexdigest()==HELPER_SHA)
    spec=importlib.util.spec_from_file_location('installed_preparation_utilities',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    complete=json.loads(module.protected(RELEASES/OPS/'handoff-installation/complete.json',modes=(0o600,)))
    require(complete['commit']==OPS and complete['phase']=='installed_not_activated'
        and complete['private_runtime_bytes_verified'] is True)
    return module


def archive_contents(raw,digest):
    require(hashlib.sha256(raw).hexdigest()==digest)
    files={}; names=set(); total=0
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for item in archive:
            path=PurePosixPath(item.name)
            require(not path.is_absolute() and '..' not in path.parts and '\\' not in item.name and ':' not in item.name
                and str(path)==item.name.rstrip('/') and item.name not in names
                and (item.isdir() or item.isfile()) and not item.mode&0o022)
            require(not any(part in ('.git','.codex','venv','data','__pycache__','schema21-application-preparation')
                or (part.startswith('.env') and item.name!='backend/.env.example') for part in path.parts))
            names.add(item.name)
            if item.isfile():
                total+=item.size; require(0<=item.size<=10000000 and total<=100000000)
                files[item.name]=(archive.extractfile(item).read(),bool(item.mode&0o111))
    require(all(name in files for name in REQUIRED))
    require(b'APP_BUILD = "20260930.80"' in files['backend/version.py'][0])
    for name in files:
        require(not any(str(parent) in files for parent in PurePosixPath(name).parents))
    contract().source(files)
    return files


def browser_manifest(root):
    result={}
    for path in (root,*root.rglob('*')):
        info=path.lstat(); require(info.st_uid==0)
        name=path.relative_to(root).as_posix()
        if path.is_symlink():
            require(path.resolve().is_relative_to(root))
            result[name]=('link',os.readlink(path),info.st_gid)
        elif path.is_file():
            require(not info.st_mode&0o022)
            result[name]=('file',hashlib.sha256(path.read_bytes()).hexdigest(),stat.S_IMODE(info.st_mode),info.st_gid)
        else:
            require(path.is_dir() and not info.st_mode&0o022)
            result[name]=('directory',stat.S_IMODE(info.st_mode),info.st_gid)
    require(len(result)>1)
    return result


def prepare(commit,digest,operations,util):
    import pwd
    require(os.geteuid()==0 and os.uname().nodename=='careertrellis-staging-web-1')
    require(re.fullmatch('[0-9a-f]{40}',commit) and re.fullmatch('[0-9a-f]{40}',operations) and re.fullmatch('[0-9a-f]{64}',digest))
    target=RELEASES/commit
    require(not target.exists() and not target.is_symlink())
    archive=TRANSFER/('careertrellis-application-'+commit+'.tar')
    fd=os.open(archive,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1
            and stat.S_IMODE(info.st_mode)==0o600 and info.st_size<=100000000)
        with os.fdopen(fd,'rb',closefd=False) as stream: raw=stream.read(100000001)
    finally: os.close(fd)
    files=archive_contents(raw,digest)
    runtime=RELEASES/OPS/'backend/venv'; copied=target/'backend/venv'
    for name in ('requirements.in','requirements.txt','dependency_lock.py'):
        require(files['backend/'+name][0]==util.protected(RELEASES/OPS/'backend'/name))
    before=contract().snapshot(util); browsers=browser_manifest(BROWSERS)
    original=util.runtime_manifest(runtime); expected=util.runtime_manifest(runtime,copied)
    storage=storage_budget(files,runtime,shutil.disk_usage(RELEASES).free)
    fixture_free=shutil.disk_usage(TRANSFER).free
    fixture_peak=storage['source_bytes']+REHEARSAL_ALLOWANCE
    require(fixture_free-fixture_peak>=SPACE_FLOOR)
    storage.update(fixture_free_before=fixture_free,fixture_additional_bytes=fixture_peak)
    group=pwd.getpwnam('careertrellis').pw_gid
    target.mkdir(mode=0o750); target.chmod(0o750); os.chown(target,0,group)
    evidence=target/'schema21-application-preparation'; evidence.mkdir(mode=0o700)
    util.record(evidence/'started.json',{'application':commit,'archive_sha256':digest,'ops':operations,'runtime_ops':OPS,'phase':'started',
        'storage_budget':storage})
    for name,(content,executable) in files.items():
        path=target/name; path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream: stream.write(content)
        path.chmod(0o750 if executable else 0o640); os.chown(path,0,group)
    for path in target.rglob('*'):
        if path==evidence or evidence in path.parents: continue
        if path.is_dir(): path.chmod(0o750); os.chown(path,0,group)
    shutil.copytree(runtime,copied,symlinks=True)
    for path in [copied/'pyvenv.cfg',*(copied/'bin').iterdir()]:
        if not path.is_file() or path.is_symlink(): continue
        content=path.read_bytes()
        if not content.startswith(b'\x7fELF'):
            changed=content.replace(str(runtime).encode(),str(copied).encode())
            if changed!=content: path.write_bytes(changed)
    for path in (copied,*copied.rglob('*')): os.chown(path,0,group,follow_symlinks=False)
    require(util.runtime_manifest(copied)==expected and util.runtime_manifest(runtime)==original)
    python=copied/'bin/python'
    util.command([str(python),'-I','-B','-m','pip','check'])
    check="import sys; from pathlib import Path; root=Path(sys.argv[1]); assert Path(sys.prefix)==root/'venv'; sys.path.insert(0,str(root)); import dependency_lock; assert dependency_lock.environment_matches_lock(root/'requirements.txt'); import session_authorization,account_session_candidate,signup_http,totp_configuration,totp_enrollment,workos_totp,totp_postgres,totp_http,factor_runtime,factor_removal,factor_removal_http,factor_removal_postgres,identity_selection_barrier; assert not signup_http.enabled(); assert (root/'static/totp-enrollment.js').read_bytes(); print('private_imports_and_lock=passed')"
    util.command(['/usr/sbin/runuser','-u','careertrellis','--',str(python),'-I','-B','-c',check,str(target/'backend')])
    manifest={name:hashlib.sha256(content).hexdigest() for name,(content,_) in files.items()}
    require(all(hashlib.sha256((target/name).read_bytes()).hexdigest()==digest for name,digest in manifest.items()))
    require(contract().snapshot(util)==before and browser_manifest(BROWSERS)==browsers and util.runtime_manifest(runtime)==original)
    util.record(evidence/'source-manifest.json',manifest)
    util.record(evidence/'complete.json',{'application':commit,'build':'20260930.80','schema':'20260929_0021','archive_sha256':digest,
        'ops':operations,'runtime_ops':OPS,'operations_installation_verified':False,'source_files':len(files),'phase':'private_runtime_prepared_not_activated',
        'source_runtime_sha256':hashlib.sha256(json.dumps(original,sort_keys=True).encode()).hexdigest(),
        'browser_manifest_sha256':hashlib.sha256(json.dumps(browsers,sort_keys=True).encode()).hexdigest(),
        'application_and_shared_state_unchanged':True,'service_user_imports_and_lock':True,
        'native_application_tests':'pending','migration_performed':False,'activation_performed':False,
        'provider_or_database_acceptance':False,'completed_at':datetime.now(timezone.utc).isoformat()})
    return 'private_runtime_prepared_not_activated'


def main():
    locks=[]
    try:
        require(len(sys.argv)==4)
        util=helper()
        import fcntl
        for name in ('careertrellis-maintenance.lock','careertrellis-postgres-migration.lock','careertrellis-provider-secret-rotation.lock'):
            fd=os.open(Path('/run/lock')/name,os.O_RDWR|os.O_NOFOLLOW); locks.append(fd)
            info=os.fstat(fd); require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        print(json.dumps({'outcome':prepare(*sys.argv[1:],util)})); return 0
    except Exception:
        print('{"outcome":"preparation_incomplete_retain_exact_attempt_do_not_retry"}'); return 1
    finally:
        for fd in reversed(locks): os.close(fd)


if __name__=='__main__': sys.exit(main())
