"""One supplemental synthetic run with one pinned test fixture correction.

Never overwrites original native evidence, prepared sources or canonical success.
Successful output requires separate operator acceptance; it is not activation.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import types

RELEASES=Path('/opt/careertrellis/staging/releases')
TOOLS=Path('/var/lib/careertrellis/verification-tools-ea2d7f1508a28a6d0cdf10dfd080336294ebdf13')
OPERATIONS='bbbc4298b2ca80df107e64d2673d6c1f243c5e41'
TRANSFER='backend/test_identity_plan_transfer.py'
OLD_SHA='cf650044026a744fd28e8f10b8130c26ef3ecfa4a20e6acb839b930ec96eaa81'
NEW_SHA='3e16cb148e7fdb1955da342257707a5ed65f231ec937a38d8ecedaa5fd32e50b'
ATTEMPT='transfer-fixture-reconciliation-v1'
PROFILES={
 'main':('450b4b0c7e6fdb83c9f71620fc63fdf4718631c4',
  '2868a4477a4f8e49e1cb233634b9f9a17fa3cd8ec6859499bd1725737c938027',
  'schema21','ba674c16a058226a9ffeab226adfad6408d6c43bd78398d13a0a5fc4c9f614d7',
  '671e5794171c68475a9b1c97d646108afe3178fb382c13fd36bd4d2852ac5985',76),
 'fallback':('8540990edd9e661aac67aabf81c1cd22dd7e5e6d',
  '6546d3cb0901fba1bf07ed1c98aa16464cbcc4a3a652999b0c89cae0b17bf5c2',
  'fallback79','256dde2bf178db430dbf6b1a64c73619b3df468997afcf6c8a9e251958f55802',
  '89707c01d67defc1fece8d12d9af994f752c2d7d816177268b605d649ac10d3f',69)}
ERRORS=tuple('test_identity_plan_transfer.TransferTests.'+name for name in (
 'test_attended_import_rechecks_database_and_emits_only_fixed_result',
 'test_cancelled_confirmation_cannot_publish_or_disclose_input',
 'test_database_drift_after_confirmation_cannot_publish',
 'test_export_import_exact_retry_and_no_other_files',
 'test_failed_database_guard_prevents_import',
 'test_input_changed_during_confirmation_is_rejected_before_import',
 'test_interrupted_archive_then_retry_publishes_selection_last',
 'test_malformed_extra_payload_b_operation_and_stale_revision_refused',
 'test_successor_import_advances_same_p_and_refuses_old_transfer'))


def require(value):
    if not value: raise RuntimeError('Supplemental verification refused.')


def sha(raw): return hashlib.sha256(raw).hexdigest()


def protected(path):
    for parent in path.parents:
        info=parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1
            and not info.st_mode&0o022 and info.st_size<=65536)
        with os.fdopen(fd,'rb',closefd=False) as stream: raw=stream.read(65537)
        require(len(raw)==info.st_size)
        return raw
    finally: os.close(fd)


def verifier(kind):
    profile=PROFILES[kind]
    path=TOOLS/('verify-prepared-'+profile[2]+'-application.py')
    raw=protected(path); require(sha(raw)==profile[3])
    module=types.ModuleType('pinned_native_verifier'); module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module


def validate_failure(value,policy):
    require(type(value) is dict and set(value)=={'returncode','result'} and value['returncode']==1)
    result=value['result']
    require(type(result) is dict and set(result)=={'tests','failures','errors','skipped','failed_tests','error_tests'})
    require(result['tests']==880 and result['failures']==0 and result['errors']==9
        and result['failed_tests']==[] and sorted(result['error_tests'])==sorted(ERRORS)
        and len(policy['allowed_skips'])==76
        and sorted(result['skipped'])==sorted(policy['allowed_skips']))


def prior_evidence(util):
    evidence=RELEASES/PROFILES['main'][0]/'schema21-application-preparation'
    require({p.name for p in evidence.glob('native-*')}=={'native-tests-started.json','native-tests-result.json'})
    raw={name:util.protected(evidence/name,modes=(0o600,))
        for name in ('native-tests-started.json','native-tests-result.json','reviewed-test-policy.json')}
    require(sha(raw['reviewed-test-policy.json'])==PROFILES['main'][4])
    validate_failure(json.loads(raw['native-tests-result.json']),json.loads(raw['reviewed-test-policy.json']))
    started=json.loads(raw['native-tests-started.json'])
    require(started['application']==PROFILES['main'][0]
        and started['phase']=='synthetic_native_tests_started'
        and started['fixture']=='/var/tmp/careertrellis-schema21-rehearsal-jgjyxn3d')
    return raw


def repair_bytes(old,new):
    require(sha(old)==OLD_SHA and sha(new)==NEW_SHA)
    return new


def claim(evidence):
    attempt=evidence/ATTEMPT
    attempt.mkdir(mode=0o700)
    directory_fd=os.open(evidence,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)
    return attempt


def verify(kind):
    require(os.geteuid()==0 and os.uname().nodename=='careertrellis-staging-web-1')
    commit,digest,prefix,_,policy_sha,skip_count=PROFILES[kind]
    v=verifier(kind); util=v.load_helper(); rules=v.contract()
    release=RELEASES/commit; evidence=release/(prefix+'-application-preparation')
    original=prior_evidence(util)
    if kind=='fallback': require(not any(evidence.glob('native-*')))
    complete_raw=util.protected(evidence/'complete.json',modes=(0o600,))
    complete=json.loads(complete_raw)
    require(complete['application']==commit and complete['phase']=='private_runtime_prepared_not_activated'
        and complete['ops']==OPERATIONS and complete['runtime_ops']==v.OPS
        and complete['archive_sha256']==digest and complete['build']==rules.BUILD and complete['schema']==rules.SCHEMA)
    policy_raw=util.protected(evidence/'reviewed-test-policy.json',modes=(0o600,))
    require(sha(policy_raw)==policy_sha)
    policy=rules.policy(json.loads(policy_raw),commit,OPERATIONS,digest)
    require(len(policy['allowed_skips'])==skip_count)
    manifest_raw=util.protected(evidence/'source-manifest.json',modes=(0o600,))
    manifest=json.loads(manifest_raw); files={}
    for name,expected in manifest.items():
        relative=Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts and str(relative)==name)
        raw=util.protected(release/relative,modes=(0o640,0o750))
        require(sha(raw)==expected); files[name]=(raw,False)
    rules.source(files)
    repair=protected(Path(__file__).with_name('test_identity_plan_transfer.py'))
    repair_bytes(files[TRANSFER][0],repair)
    before=rules.snapshot(util); runtime_before=util.runtime_manifest(release/'backend/venv')
    free=shutil.disk_usage('/var/tmp').free
    peak=sum(len(value[0]) for value in files.values())+len(repair)+512*1024*1024
    require(free-peak>=5*1024*1024*1024)
    # Exclusive permanent claim: an interrupted attempt is never automatically replayed.
    attempt=claim(evidence)
    disclosure={'application':commit,'operations':OPERATIONS,'archive_sha256':digest,
        'build':rules.BUILD,'schema':rules.SCHEMA,'policy_sha256':policy_sha,
        'test_only_repair':{'path':TRANSFER,'original_sha256':OLD_SHA,'replacement_sha256':NEW_SHA},
        'original_main_failure':{'application':PROFILES['main'][0],
            'evidence_sha256':{name:sha(raw) for name,raw in original.items()},
            'tests':880,'errors':9,'error_tests':list(ERRORS),'skips':76},
        'original_fallback_attempt':'absent' if kind=='fallback' else 'not_evaluated',
        'exact_archive_tests_passed':False,'activation_gate_accepted':False}
    util.record(attempt/'started.json',{**disclosure,'phase':'supplemental_test_repair_started',
        'free_before':free,'planned_additional_bytes':peak,'reserved_free_floor':5*1024*1024*1024})
    fixture=Path(tempfile.mkdtemp(prefix='careertrellis-transfer-reconciliation-'+kind+'-',dir='/var/tmp'))
    import pwd
    account=pwd.getpwnam('careertrellis'); fixture.chmod(0o750); os.chown(fixture,0,account.pw_gid)
    util.record(attempt/'fixture.json',{'fixture':str(fixture)})
    source=fixture/'source'; source.mkdir(mode=0o700); os.chown(source,account.pw_uid,account.pw_gid)
    data=fixture/'data'; data.mkdir(mode=0o700); os.chown(data,account.pw_uid,account.pw_gid)
    for name in ('uploads','output'):
        path=data/name; path.mkdir(mode=0o700); os.chown(path,account.pw_uid,account.pw_gid)
    for name,(raw,_) in files.items():
        target=source/name; target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream: stream.write(repair if name==TRANSFER else raw)
        target.chmod(stat.S_IMODE((release/name).stat().st_mode)); os.chown(target,account.pw_uid,account.pw_gid)
    for path in source.rglob('*'):
        if path.is_dir(): path.chmod(0o700); os.chown(path,account.pw_uid,account.pw_gid)
    require(all(sha((source/name).read_bytes())==(NEW_SHA if name==TRANSFER else expected)
        for name,expected in manifest.items()))
    env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','HOME':str(data),
        'PYTHONDONTWRITEBYTECODE':'1','CAREERTRELLIS_CONFIG_READ_ONLY':'1',
        'CAREERTRELLIS_ENVIRONMENT':'local','CAREERTRELLIS_DATA_DIR':str(data),
        'PLAYWRIGHT_BROWSERS_PATH':'/var/lib/careertrellis/shared/ms-playwright'}
    command=['/usr/bin/unshare','--net','--','/usr/bin/python3','-I','-S','-B','-c',v.NAMESPACE_BOOTSTRAP,
        str(release/'backend/venv/bin/python'),str(source),'TESTS='+repr(rules.TESTS)+'\n'+v.RUNNER]
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
        cwd=source/'backend',start_new_session=True)
    try: output,error=process.communicate(timeout=600)
    except BaseException:
        try: os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait(timeout=10); raise
    lines=output.decode('utf-8').splitlines(); result=json.loads(lines[-1]) if lines else None
    util.record(attempt/'result.json',{'returncode':process.returncode,'result':result})
    require(process.returncode==0); rules.result(result,policy)
    if kind=='main': require(result['tests']==881)
    require(util.protected(evidence/'reviewed-test-policy.json',modes=(0o600,))==policy_raw
        and util.protected(evidence/'complete.json',modes=(0o600,))==complete_raw
        and util.protected(evidence/'source-manifest.json',modes=(0o600,))==manifest_raw)
    require(prior_evidence(util)==original and rules.snapshot(util)==before
        and util.runtime_manifest(release/'backend/venv')==runtime_before)
    require(all(sha(util.protected(release/name,modes=(0o640,0o750)))==expected
        for name,expected in manifest.items()))
    if kind=='fallback': require(not any(evidence.glob('native-*')))
    util.record(attempt/'supplemental-tests.json',{**disclosure,'phase':'supplemental_test_repair_passed',
        'result':result,'private_network':True,'service_user':'careertrellis','fixture':str(fixture),
        'prepared_source_and_runtime_unchanged':True,'provider_and_database_acceptance':False,
        'browser_acceptance':'isolated_synthetic_browser'})
    return 'supplemental_test_repair_passed_operator_acceptance_required'


def main():
    locks=[]
    try:
        require(len(sys.argv)==2 and sys.argv[1] in PROFILES)
        import fcntl
        for name in ('careertrellis-maintenance.lock','careertrellis-postgres-migration.lock','careertrellis-provider-secret-rotation.lock'):
            fd=os.open(Path('/run/lock')/name,os.O_RDWR|os.O_NOFOLLOW); locks.append(fd)
            info=os.fstat(fd); require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        print(json.dumps({'outcome':verify(sys.argv[1])})); return 0
    except Exception:
        print('{"outcome":"supplemental_verification_incomplete_preserve_attempt_no_retry"}'); return 1
    finally:
        for fd in reversed(locks): os.close(fd)


if __name__=='__main__': sys.exit(main())
