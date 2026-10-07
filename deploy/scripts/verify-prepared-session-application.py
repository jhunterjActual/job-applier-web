"""Native synthetic tests in a private source copy and network namespace.

Never loads production configuration or runs a migration. Retains its exact
synthetic fixture and receipt; no source release writes except protected evidence.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile

RELEASES=Path('/opt/careertrellis/staging/releases')
FIXTURES=Path('/var/tmp')
PROC=Path('/proc')
OPS='45e33567bc9e23746ea5ba8f929e14b180288576'
HELPER_SHA='bd3ef69ce61703d8063cf1ba90340f32c5ab56ee4b6936adf0d2f7ba01ae945b'
TESTS=('test_p0_regressions','test_object_store','test_object_import','test_stored_objects',
    'test_session_epoch','test_session_epoch_http.SessionEpochHTTPTests','test_provider_session_candidate',
    'test_signup_http','test_totp_configuration','test_totp_enrollment','test_totp_http')
NAMESPACE_BOOTSTRAP="""import os,socket,subprocess,sys
assert [name for _,name in socket.if_nameindex()]==['lo']
subprocess.run(['/usr/sbin/ip','link','set','dev','lo','up'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
assert [name for _,name in socket.if_nameindex()]==['lo']
names=('PATH','LANG','HOME','PYTHONDONTWRITEBYTECODE','CAREERTRELLIS_CONFIG_READ_ONLY','CAREERTRELLIS_ENVIRONMENT','CAREERTRELLIS_DATA_DIR','PLAYWRIGHT_BROWSERS_PATH')
environment=[name+'='+os.environ[name] for name in names]
os.execv('/usr/sbin/runuser',['runuser','-u','careertrellis','--','/usr/bin/env','-i',*environment,sys.argv[1],'-I','-B','-c',sys.argv[3],sys.argv[2]])
"""
RUNNER="""import io,json,os,sys,unittest
from pathlib import Path
root=Path(sys.argv[1]); sys.path.insert(0,str(root/'backend'))
assert Path.home()==root.parent/'data'
assert Path(os.environ['CAREERTRELLIS_DATA_DIR'])==root.parent/'data'
assert os.environ['PLAYWRIGHT_BROWSERS_PATH']=='/var/lib/careertrellis/shared/ms-playwright'
import version
assert version.APP_BUILD=='20260916.74'
suite=unittest.defaultTestLoader.loadTestsFromNames(TESTS)
result=unittest.TextTestRunner(stream=io.StringIO()).run(suite)
print(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
 'skipped':[test.id() for test,reason in result.skipped],
 'failed_tests':[test.id() for test,detail in result.failures],
 'error_tests':[test.id() for test,detail in result.errors]}))
sys.exit(not result.wasSuccessful())
"""


def require(value):
    if not value: raise RuntimeError('Native preparation verification failed.')


def load_helper():
    path=RELEASES/OPS/'deploy/scripts/install-handoff-ops.py'
    for item in (path,*path.parents):
        info=item.lstat(); require(not item.is_symlink() and info.st_uid==0 and not info.st_mode&0o022)
    require(path.stat().st_nlink==1 and hashlib.sha256(path.read_bytes()).hexdigest()==HELPER_SHA)
    spec=importlib.util.spec_from_file_location('installed_native_utilities',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def verify(commit,util):
    require(os.geteuid()==0 and os.uname().nodename=='careertrellis-staging-web-1'
        and re.fullmatch('[0-9a-f]{40}',commit))
    release=RELEASES/commit; evidence=release/'application-preparation'
    complete=json.loads(util.protected(evidence/'complete.json',modes=(0o600,)))
    require(complete['application']==commit and complete['phase']=='private_runtime_prepared_not_activated'
        and complete['ops']==OPS and complete['build']=='20260916.74' and complete['schema']=='20260915_0016')
    receipt=evidence/'native-application-tests.json'
    started=evidence/'native-tests-started.json'
    require(not any(evidence.glob('native-*')))
    manifest=json.loads(util.protected(evidence/'source-manifest.json',modes=(0o600,)))
    before=util.snapshot(); runtime_before=util.runtime_manifest(release/'backend/venv')
    # Recheck the actual fixture filesystem at execution time; preparation can
    # have happened earlier or on a distinct mount. No fixture/marker yet exists.
    source_bytes=0
    for name,digest in manifest.items():
        relative=Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts and str(relative)==name)
        raw=util.protected(release/relative,modes=(0o640,0o750))
        require(hashlib.sha256(raw).hexdigest()==digest)
        source_bytes+=len(raw)
    fixture_free=shutil.disk_usage(FIXTURES).free
    fixture_peak=source_bytes+512*1024*1024
    require(fixture_free-fixture_peak>=3000000000)
    fixture=Path(tempfile.mkdtemp(prefix='careertrellis-application-rehearsal-',dir=FIXTURES))
    account=pwd.getpwnam('careertrellis'); fixture.chmod(0o750); os.chown(fixture,0,account.pw_gid)
    source=fixture/'source'; source.mkdir(mode=0o700); os.chown(source,account.pw_uid,account.pw_gid)
    data=fixture/'data'; data.mkdir(mode=0o700); os.chown(data,account.pw_uid,account.pw_gid)
    for name in ('uploads','output'):
        directory=data/name; directory.mkdir(mode=0o700); os.chown(directory,account.pw_uid,account.pw_gid)
    util.record(started,{'application':commit,'fixture':str(fixture),'phase':'synthetic_native_tests_started',
        'storage_budget':{'free_before':fixture_free,'planned_additional_bytes':fixture_peak,
            'reserved_free_floor':3000000000}})
    for name,digest in manifest.items():
        relative=Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts and str(relative)==name)
        raw=util.protected(release/relative,modes=(0o640,0o750))
        require(hashlib.sha256(raw).hexdigest()==digest)
        target=source/relative; target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream: stream.write(raw)
        target.chmod(stat.S_IMODE((release/relative).stat().st_mode)); os.chown(target,account.pw_uid,account.pw_gid)
    for path in source.rglob('*'):
        if path.is_dir(): path.chmod(0o700); os.chown(path,account.pw_uid,account.pw_gid)
    env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','HOME':str(data),
        'PYTHONDONTWRITEBYTECODE':'1','CAREERTRELLIS_CONFIG_READ_ONLY':'1',
        'CAREERTRELLIS_ENVIRONMENT':'local','CAREERTRELLIS_DATA_DIR':str(data),
        'PLAYWRIGHT_BROWSERS_PATH':'/var/lib/careertrellis/shared/ms-playwright'}
    command=['/usr/bin/unshare','--net','--','/usr/bin/python3','-I','-S','-B','-c',NAMESPACE_BOOTSTRAP,
        str(release/'backend/venv/bin/python'),str(source),'TESTS='+repr(TESTS)+'\n'+RUNNER]
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
        cwd=source/'backend',start_new_session=True)
    try: output,error=process.communicate(timeout=180)
    except BaseException:
        try: os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=10)
        raise
    # Keep raw diagnostics out of the receipt even though all data is synthetic.
    lines=output.decode('utf-8').splitlines()
    result=json.loads(lines[-1]) if lines else None
    util.record(evidence/'native-tests-result.json',{'returncode':process.returncode,'result':result})
    require(process.returncode==0 and isinstance(result,dict) and result['tests']>=400
        and result['failures']==0 and result['errors']==0)
    require(result['skipped']==['test_p0_regressions.DependencyLockTests.test_batch_launcher_discovers_bootstrap_python_with_quoted_paths'])
    require(util.snapshot()==before and util.runtime_manifest(release/'backend/venv')==runtime_before)
    require(all(hashlib.sha256(util.protected(release/name,modes=(0o640,0o750))).hexdigest()==digest
        for name,digest in manifest.items()))
    util.record(receipt,{'application':commit,'phase':'native_synthetic_tests_passed','result':result,
        'private_network':True,'service_user':'careertrellis','fixture':str(fixture),
        'provider_and_database_acceptance':False,'browser_acceptance':'isolated_synthetic_browser'})
    return 'native_synthetic_tests_passed'


def main():
    locks=[]
    try:
        require(len(sys.argv)==2); util=load_helper()
        import fcntl
        for name in ('careertrellis-maintenance.lock','careertrellis-postgres-migration.lock','careertrellis-provider-secret-rotation.lock'):
            fd=os.open(Path('/run/lock')/name,os.O_RDWR|os.O_NOFOLLOW); locks.append(fd)
            info=os.fstat(fd); require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        print(json.dumps({'outcome':verify(sys.argv[1],util)})); return 0
    except Exception:
        print('{"outcome":"native_verification_incomplete_retain_exact_attempt_do_not_retry"}'); return 1
    finally:
        for fd in reversed(locks): os.close(fd)


if __name__=='__main__': sys.exit(main())
