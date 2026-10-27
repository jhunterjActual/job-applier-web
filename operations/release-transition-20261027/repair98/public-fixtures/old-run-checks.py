"""Synthetic checks inside the newly reviewed no-network transient test unit.

No staging credentials/configuration or provider client is supplied. This script
must not be invoked in the live application service or outside its fixed sandbox.
"""
import argparse
import ast
import compileall
from datetime import datetime,timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

TARGET='56c8b137fd6c6e778a9f09e3b04b925edea6270e'
CORRECTION='f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
BUILDS={TARGET:'20261026.97',CORRECTION:'20261026.98'}
WORK=Path('/opt/careertrellis/staging/release-preparation-work-20261026')
BROWSER='/var/lib/careertrellis/shared/ms-playwright'
NATIVE_TESTS={
 'authority_native':'test_p0_regressions.DependencyLockTests.test_hosted_authority_marker_is_one_way_and_runtime_checks_fail_closed',
 'systemd_native':'test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables'}

def need(value):
    if not value:raise RuntimeError('synthetic_preparation_check_failed')

READONLY_FAILURE_CODES=frozenset(('RO_API_UNAVAILABLE','RO_NOT_REGULAR','RO_FDINFO_SIZE',
    'RO_FDINFO_ID','RO_MOUNTINFO_SIZE','RO_MOUNT_ROW','RO_MOUNT_FORMAT','RO_MOUNT_RW',
    'RO_VFS_RW','RO_MOUNT_CHANGED','RO_VFS_CHANGED','RO_INODE_CHANGED'))

class ReadonlyProofRefused(RuntimeError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)

def readonly_need(value,code):
    if not value:raise ReadonlyProofRefused(code)

def opened_mount_id(fd):
    with Path('/proc/self/fdinfo/'+str(fd)).open('rb') as stream:raw=stream.read(4097)
    readonly_need(len(raw)<=4096,'RO_FDINFO_SIZE')
    values=[line for line in raw.splitlines() if line.startswith(b'mnt_id:')]
    readonly_need(len(values)==1,'RO_FDINFO_ID')
    match=re.fullmatch(rb'mnt_id:\s*([1-9][0-9]{0,19})\s*',values[0])
    readonly_need(match is not None,'RO_FDINFO_ID')
    return int(match.group(1))

def readonly_mount_row(raw,mount_id):
    readonly_need(len(raw)<=1024*1024,'RO_MOUNTINFO_SIZE')
    rows=[line.split() for line in raw.splitlines() if line.split() and line.split()[0]==str(mount_id).encode('ascii')]
    readonly_need(len(rows)==1,'RO_MOUNT_ROW');row=rows[0]
    readonly_need(len(row)>=10 and b'-' in row[6:],'RO_MOUNT_FORMAT')
    separator=row.index(b'-',6)
    readonly_need(separator>=6 and len(row)>=separator+4,'RO_MOUNT_FORMAT')
    options=row[5].split(b',')
    # Per-mount options, not the shared superblock's options after '-'. A
    # read-only bind may legitimately share a read-write underlying superblock.
    readonly_need(b'ro' in options and b'rw' not in options,'RO_MOUNT_RW')

def verify_opened_readonly(path):
    readonly_need(callable(getattr(os,'fstatvfs',None)) and hasattr(os,'ST_RDONLY')
                  and hasattr(os,'O_NOFOLLOW'),'RO_API_UNAVAILABLE')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd);readonly_need(stat.S_ISREG(before.st_mode),'RO_NOT_REGULAR')
        mount_id=opened_mount_id(fd)
        readonly_need(bool(os.fstatvfs(fd).f_flag&os.ST_RDONLY),'RO_VFS_RW')
        with Path('/proc/self/mountinfo').open('rb') as stream:raw=stream.read(1024*1024+1)
        readonly_mount_row(raw,mount_id)
        readonly_need(opened_mount_id(fd)==mount_id,'RO_MOUNT_CHANGED')
        readonly_need(bool(os.fstatvfs(fd).f_flag&os.ST_RDONLY),'RO_VFS_CHANGED')
        after=os.fstat(fd)
        readonly_need((before.st_dev,before.st_ino,before.st_mode)==(after.st_dev,after.st_ino,after.st_mode),'RO_INODE_CHANGED')
        return {'mount_id':mount_id,'fstatvfs_readonly':True,'mountinfo_readonly':True}
    finally:os.close(fd)
def isolated_env(work):
    # Clear inherited values rather than trying to enumerate rotating credentials.
    value={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8',
       'HOME':str(work),'TMPDIR':str(work),'TEMP':str(work),'TMP':str(work),
       'CAREERTRELLIS_ENVIRONMENT':'local','CAREERTRELLIS_DATA_STORE':'sqlite',
       'CAREERTRELLIS_DATA_DIR':str(work/'synthetic-data'),'CAREERTRELLIS_ALLOWED_HOSTS':'',
       'CAREERTRELLIS_TRUSTED_PROXY_TOKEN':'','PLAYWRIGHT_BROWSERS_PATH':str(work/'browsers'),
       'PYTHONDONTWRITEBYTECODE':'1'}
    return value
def require_readonly_bind(alias,leaf):
    need(leaf.is_file() and not leaf.is_symlink() and leaf.is_relative_to(alias))
    verify_opened_readonly(leaf)
    # Opening without TRUNC/CREAT and never writing changes no file content.
    try:fd=os.open(leaf,os.O_WRONLY|os.O_NOFOLLOW)
    except OSError as error:need(error.errno in (errno.EROFS,errno.EACCES,errno.EPERM))
    else:os.close(fd);raise RuntimeError('browser_bind_was_write_openable')

class RecordedResult(unittest.TextTestResult):
    def __init__(self,*a,**k):super().__init__(*a,**k);self.success_ids=[]
    def addSuccess(self,test):self.success_ids.append(test.id());super().addSuccess(test)

def run_suite(cwd,names):
    old=os.getcwd();prior=list(sys.path)
    try:
        os.chdir(cwd);sys.path.insert(0,str(cwd))
        suite=unittest.defaultTestLoader.loadTestsFromNames(names)
        result=unittest.TextTestRunner(verbosity=1,resultclass=RecordedResult).run(suite)
        need(result.wasSuccessful())
        return {'tests':result.testsRun,'modules':names,'skipped':[t.id() for t,_ in result.skipped],
                'success_ids':[x for x in result.success_ids if x in NATIVE_TESTS.values()],
                'failures':len(result.failures),'errors':len(result.errors)}
    finally:os.chdir(old);sys.path[:]=prior

def modules_for(release,revision):
    source=(release/'deploy/scripts/deploy-release.sh').read_text()
    lines=[line.strip() for line in source.splitlines() if './venv/bin/python -m unittest ' in line]
    need(len(lines)==1)
    names=[x.removesuffix('.py') for x in lines[0].split(' -m unittest ',1)[1].split()]
    # Existing maintained preparation union plus focused application changes.
    names+=['test_ai_contracts','test_claude_adapter','test_claude_integration','test_claude_storage',
       'test_claude_workflows','test_claude_search_failures','test_claude_browser','test_claude_recovery',
       'test_role_queries','test_role_plan_browser','test_title_compatibility_audit','test_managed_search_reporting']
    if revision==TARGET:names+=['test_claude_cache','test_smartrecruiters_metadata','test_pdf_wrapping',
                              'test_job_import_browser','test_provider_credential_links_browser']
    need(all(x.startswith('test_') and x.replace('_','').isalnum() for x in names))
    need('test_match_api' not in names)
    need({'test_p0_regressions','test_object_store','test_object_import','test_stored_objects',
          'test_serper_budget'}<=set(names))
    return list(dict.fromkeys(names))

def startup(release,work,build):
    env=isolated_env(work)
    with socket.socket() as listener,tempfile.TemporaryFile(mode='w+b') as log:
        listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        proc=subprocess.Popen([sys.executable,'-B','-m','uvicorn','app:app','--fd',str(listener.fileno())],
            pass_fds=(listener.fileno(),),cwd=release/'backend',env=env,
            stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+60
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while True:
                need(proc.poll() is None)
                try:
                    with opener.open('http://127.0.0.1:'+str(port)+'/api/version',timeout=2) as r:
                        need(r.status==200 and json.load(r)=={'build':build})
                    break
                except (urllib.error.URLError,TimeoutError):
                    need(time.monotonic()<deadline);time.sleep(.2)
            with opener.open('http://127.0.0.1:'+str(port)+'/',timeout=5) as r:
                need(r.status==200 and r.read()==(release/'backend/static/index.html').read_bytes())
        finally:
            proc.terminate()
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait(timeout=5)
                raise RuntimeError('owned_synthetic_app_required_forced_termination')
        log.seek(0);raw=log.read(65537);need(len(raw)<=65536)
        need(proc.returncode==-signal.SIGTERM and b'Application shutdown complete.' in raw
             and ('Finished server process ['+str(proc.pid)+']').encode() in raw
             and b'Traceback' not in raw and b'ERROR:' not in raw)
        # Synthetic service log remains private to the test; only safe aggregates.
        return {'api_build':build,'dashboard_match':True,'owned_process_stopped':True,
                'exit_signal':'SIGTERM','log_sha256':hashlib.sha256(raw).hexdigest()}

def main():
    p=argparse.ArgumentParser();p.add_argument('--release',type=Path,required=True)
    p.add_argument('--revision',choices=tuple(BUILDS),required=True);p.add_argument('--work',type=Path,required=True)
    a=p.parse_args();release=a.release;work=a.work
    need(sys.platform=='linux' and os.geteuid()==65534 and sys.flags.isolated and sys.flags.dont_write_bytecode)
    status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    need(status.get('NoNewPrivs','').strip()=='1' and int(status.get('CapEff','-1').strip(),16)==0)
    need(release==Path('/opt/careertrellis/staging/releases')/a.revision and work==WORK/a.revision)
    need(Path('/proc/self/ns/net').is_symlink() and {x.name for x in Path('/sys/class/net').iterdir()}=={'lo'})
    need(work.is_dir() and not work.is_symlink())
    verify_opened_readonly(release/'backend/version.py')
    alias=work/'browsers'
    leaf=next((p for p in alias.rglob('*') if p.is_file() and not p.is_symlink()),None)
    need(leaf is not None);require_readonly_bind(alias,leaf)
    # Read-only mount proof opens no write and changes no source byte.
    try:fd=os.open(release/'backend/version.py',os.O_WRONLY|os.O_NOFOLLOW)
    except OSError as error:need(error.errno in (errno.EROFS,errno.EACCES,errno.EPERM))
    else:os.close(fd);raise RuntimeError('release_not_readonly_in_test_namespace')
    need(not os.access('/etc/careertrellis/provider-secrets.env',os.R_OK))
    os.environ.clear();os.environ.update(isolated_env(work));tempfile.tempdir=str(work)
    # Refuse unexpected ambient/old-release import paths introduced by .pth.
    for value in sys.path:
        if not value:continue
        resolved=Path(value).resolve()
        need(resolved==work or resolved.is_relative_to(release/'backend/venv')
             or str(resolved).startswith('/usr/lib/python3.12') or str(resolved).startswith('/usr/lib/python312'))
    started=datetime.now(timezone.utc).isoformat();checks={};details={}
    backend=run_suite(release/'backend',modules_for(release,a.revision));details['backend']=backend
    checks.update(p0=True,objects=True,ai=True)
    for name,test in NATIVE_TESTS.items():need(test in backend['success_ids']);checks[name]=True
    if a.revision==TARGET:
        checks['cache']=True;checks['serper_preparation']=True
        details['summary']=run_suite(release/'scripts',['test_ai_usage_summary','test_claude_cache_summary','test_study_readiness'])
        checks['summaries']=True
        details['evaluation']=run_suite(release/'scripts/value_evaluation',[
            'test_budget_guard','test_ct_budget_transport','test_campaign_state','test_study_upstream',
            'test_study_native_corrections','test_campaign_v2','test_native_cost_envelope',
            'test_campaign_transport','test_ct_campaign_opener','test_blind_packets','test_study_ct'])
        checks['evaluation']=True
    cache=work/'compile-cache';cache.mkdir();sys.pycache_prefix=str(cache)
    need(compileall.compile_dir(str(release/'backend'),quiet=1,
         rx=__import__('re').compile(r'[/\\]venv[/\\]')))
    checks['syntax']=True
    details['startup']=startup(release,work,BUILDS[a.revision]);checks['runtime_isolation']=True
    result={'revision':a.revision,'build':BUILDS[a.revision],'checks':checks,'details':details,
            'started_utc':started,'ended_utc':datetime.now(timezone.utc).isoformat(),
            'network_namespace_loopback_only':True,'provider_requests':0}
    # No activation receipt: this is unprivileged synthetic evidence for the root
    # preparation owner to verify against source and the immutable runtime tree.
    path=work/'checks.json'
    with path.open('x',encoding='utf8') as f:json.dump(result,f,sort_keys=True);f.flush();os.fsync(f.fileno())
    print(json.dumps({'result':'synthetic_checks_passed','revision':a.revision,
                      'counts':{k:v['tests'] for k,v in details.items() if isinstance(v,dict) and 'tests' in v}}))

if __name__=='__main__':
    try:main()
    except BaseException as error:
        code=getattr(error,'code',None)
        print(json.dumps({'result':'synthetic_checks_failed','failure_code':code if code in READONLY_FAILURE_CODES else 'CHECK_FAILED',
                          'details_retained_in_private_log':True}))
        raise SystemExit(1)
