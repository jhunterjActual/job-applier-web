"""Exact inactive release preparation; never activates an application.

Draft source only. Execution requires separately reviewed provenance/plan hashes
and primary approval. No apt, pip installation, download, shared-browser write,
installed helper/unit/policy/timer modification, or application service operation.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time

BASE='7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS='4095417909a8b13ad252be9420e422af3662bef5'
TARGET='56c8b137fd6c6e778a9f09e3b04b925edea6270e'
CORRECTION='f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
ROOT=Path('/opt/careertrellis/staging')
REPOSITORY='/srv/careertrellis/job-applier-web'
EVIDENCE=Path('/var/backups/careertrellis/app-release-preparation-20261026')
WORK=ROOT/'release-preparation-work-20261026'
BROWSER=Path('/var/lib/careertrellis/shared/ms-playwright')
SOURCE=Path('operations/release-transition-20261026/preparation')
STAGE=ROOT/'pr98-117-release-preparation-20261026'
ACCEPTED_STAGE=ROOT/'title-offweb-import-7a4d3f077c4d'
ACCEPTED={
 'manifest.json':'3f98375baed2cf3c3f0fc657eb3e7e1c985c6ed4ac90a686207fdb359234903a',
 'success.json':'5398ddb66edcbe7d1a4c2a443352a8672eabd3f6a64278c36a757536d0f8152a',
 'venv-provenance.json':'82ab88e5d3f76a21ae3ee8d4fc51cd64a6d24c09faae33edbda4067d9526af63'}
PROVENANCE_SHA='b4c21424441e63b45d484c2ff063afae394c745fd0a235c7210c11beecb05ee7'
SPECS={
 TARGET:{'build':'20261026.97','name':'application-56c8b137-source.tar','bytes':9707520,
         'sha256':'767c0bbcff86c410d4fecfcb3275b09c486d0a0d20c20047a3dd6909cac3306a'},
 CORRECTION:{'build':'20261026.98','name':'correction-f70417d4-source.tar','bytes':9072640,
         'sha256':'67b392f0bd1c66b3391aa6e4c7f18b90b0a17fe7da72a4b5c32e0da594afb327'}}
FLOOR=5*1024**3
MAX_FILE=64*1024**2
MAX_RUNTIME_FILE=1024**3

class Refused(RuntimeError):pass
def need(value):
    if not value:raise Refused('preparation_refused_inspect_retained_attempt_no_replay')
def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def stamp():return datetime.now(timezone.utc).isoformat()
def decode(raw):
    def pairs(values):
        d={}
        for k,v in values:need(k not in d);d[k]=v
        return d
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
def safe_name(name):
    need(type(name) is str)
    p=PurePosixPath(name)
    need(name not in ('','.') and name==p.as_posix() and not p.is_absolute() and '..' not in p.parts
         and '\\' not in name and all(ord(c)>=32 and ord(c)!=127 for c in name))
    return p
def hash_file(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def protected_hash(path):
    p=Path(path);protected(p.parent,directory=True);s=p.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022
         and s.st_size<=MAX_RUNTIME_FILE)
    fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
    before=tuple(getattr(s,k) for k in fields)
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        need(tuple(getattr(os.fstat(fd),k) for k in fields)==before)
        with os.fdopen(fd,'rb',closefd=False) as f:value=hashlib.file_digest(f,'sha256').hexdigest()
        need(tuple(getattr(os.fstat(fd),k) for k in fields)==before
             and tuple(getattr(p.lstat(),k) for k in fields)==before)
        return value
    finally:os.close(fd)
def protected(path,limit=MAX_FILE,directory=False):
    p=Path(path);need(p.is_absolute())
    for parent in p.parents:
        s=parent.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022)
    s=p.lstat()
    if directory:
        need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022);return p
    need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022
         and s.st_size<=limit)
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
    try:
        before=tuple(getattr(s,k) for k in fields)
        need(tuple(getattr(os.fstat(fd),k) for k in fields)==before)
        with os.fdopen(fd,'rb',closefd=False) as f:raw=f.read(s.st_size+1)
        need(len(raw)==s.st_size and tuple(getattr(os.fstat(fd),k) for k in fields)==before
             and tuple(getattr(p.lstat(),k) for k in fields)==before)
        return raw
    finally:os.close(fd)
def sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
def create(path,raw,mode=0o600):
    protected(Path(path).parent,directory=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb',closefd=False) as f:f.write(raw);f.flush();os.fsync(fd)
    finally:os.close(fd)
    sync(Path(path).parent)
def namespace(path,mode,gid,label):
    marker=canonical({'schema':'careertrellis.app-preparation-namespace/v1','owner':'release-preparation-20261026','purpose':label})
    if not os.path.lexists(path):
        protected(path.parent,directory=True);path.mkdir(mode=mode);os.chmod(path,mode);os.chown(path,0,gid);sync(path.parent)
        create(path/'namespace.json',marker)
    else:
        protected(path,directory=True);s=path.lstat()
        need(s.st_gid==gid and stat.S_IMODE(s.st_mode)==mode and protected(path/'namespace.json')==marker)
def run(args,timeout=60):
    p=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,timeout=timeout,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
    need(p.returncode==0);return p.stdout
def archive_files(raw):
    need(len(raw)<=128*1024**2)
    result={};seen=set()
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for item in archive:
            safe_name(item.name);need(item.name not in seen);seen.add(item.name)
            need(len(seen)<=12000 and (item.isdir() or item.isfile()))
            if item.isdir():continue
            need(0<=item.size<=MAX_FILE)
            body=archive.extractfile(item).read(item.size+1);need(len(body)==item.size)
            result[item.name]={'sha256':sha(body),'bytes':item.size,'executable':bool(item.mode&0o111)}
    need(result and 'backend/requirements.txt' in result and 'backend/requirements.in' in result)
    return result
def validate_plan(plan,revision):
    need(type(plan) is dict and set(plan)=={'schema','base','previous','revision','build','ops_commit',
         'source_sha256','provenance_sha256','source_diff_sha256','expires_at'})
    need(revision in SPECS and plan['schema']=='careertrellis.app-preparation-plan/v1'
         and plan['base']==BASE and plan['previous']==PREVIOUS and plan['revision']==revision
         and plan['build']==SPECS[revision]['build'] and plan['source_sha256']==SPECS[revision]['sha256'])
    need(re.fullmatch('[0-9a-f]{40}',plan['ops_commit']) is not None)
    for k in ('provenance_sha256','source_diff_sha256'):
        need(re.fullmatch('[0-9a-f]{64}',plan[k]) is not None and plan[k]!='0'*64)
    need(plan['provenance_sha256']==PROVENANCE_SHA)
    expiry=datetime.fromisoformat(plan['expires_at'].replace('Z','+00:00'))
    need(expiry.tzinfo is not None and 0<(expiry-datetime.now(timezone.utc)).total_seconds()<=3600)
def validate_provenance(value):
    need(type(value) is dict and set(value)=={'schema','baseline','requirements','interpreter',
         'site_packages','browser','platform','reviewed_at','accepted_venv_provenance'})
    need(value['schema']=='careertrellis.existing-runtime-provenance/v1' and value['baseline']==BASE)
    need(set(value['requirements'])=={'backend/requirements.in','backend/requirements.txt'})
    need(set(value['interpreter'])=={'path','sha256','python_version'})
    need(value['interpreter']['path']=='/usr/bin/python3.12'
         and re.fullmatch(r'3\.12\.\d+',value['interpreter']['python_version']))
    need(value['platform']['system']=='Linux' and value['platform']['machine']=='x86_64')
    receipt=value['accepted_venv_provenance']
    need(type(receipt) is dict and set(receipt)=={'path','sha256'})
    path=PurePosixPath(receipt['path'])
    need(path.is_absolute() and '..' not in path.parts and str(path).startswith(('/opt/careertrellis/staging/','/var/backups/careertrellis/'))
         and path.name=='venv-provenance.json' and re.fullmatch('[0-9a-f]{64}',receipt['sha256']))
    need(receipt=={'path':str(ACCEPTED_STAGE/'venv-provenance.json'),'sha256':ACCEPTED['venv-provenance.json']})
    for key in ('site_packages','browser'):
        need(type(value[key]) is dict and value[key])
        for name,item in value[key].items():
            safe_name(name);need(set(item)=={'sha256','bytes','executable'}
                and re.fullmatch('[0-9a-f]{64}',item['sha256']) and type(item['bytes']) is int
                and 0<=item['bytes']<=MAX_RUNTIME_FILE and type(item['executable']) is bool)
    reviewed=datetime.fromisoformat(value['reviewed_at'].replace('Z','+00:00'))
    need(reviewed.tzinfo is not None and 0<=(datetime.now(timezone.utc)-reviewed).total_seconds()<=86400)
def inventory(root,allow_symlinks=False):
    result={}
    for folder,dirs,files in os.walk(root,followlinks=False):
        for name in dirs:need(not (Path(folder)/name).is_symlink())
        for name in files:
            p=Path(folder)/name;s=p.lstat()
            need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022)
            need(s.st_size<=MAX_RUNTIME_FILE)
            result[p.relative_to(root).as_posix()]={'sha256':protected_hash(p),
                'bytes':s.st_size,'executable':bool(s.st_mode&0o111)}
    return result
def needed_bytes(source,provenance):
    # Two copies cover lib64 materialization as well as normal lib site-packages;
    # another full copy is retained as failure/verification margin, plus test/log.
    packages=sum(x['bytes'] for x in provenance['site_packages'].values())
    return sum(x['bytes'] for x in source.values())+packages*3+1024**3
def validate_capacity(free,peak):
    need(type(free) is int and type(peak) is int and peak>0 and free-peak>=FLOOR)
def verify_reused_packages(packages,accepted):
    prefix='backend/venv/lib/python3.12/site-packages/'
    need({k[len(prefix):]:v for k,v in accepted.items() if k.startswith(prefix)}
         =={k:v['sha256'] for k,v in packages.items()})
def verify_source(root,expected):
    actual={}
    for folder,dirs,files in os.walk(root,followlinks=False):
        rel=Path(folder).relative_to(root)
        dirs[:]=[d for d in dirs if (rel/d).as_posix()!='backend/venv' and d!='__pycache__']
        need(all(not (Path(folder)/d).is_symlink() for d in dirs))
        for name in files:
            p=Path(folder)/name;relative=p.relative_to(root).as_posix();need(relative in expected)
            need(sha(protected(p))==expected[relative]['sha256']);actual[relative]=True
    need(set(actual)==set(expected))
def extract(raw,root,expected,gid):
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for item in archive:
            parts=safe_name(item.name).parts;p=root.joinpath(*parts)
            if item.isdir():p.mkdir(mode=0o750,parents=True,exist_ok=True);os.chown(p,0,gid);continue
            need(item.isfile() and item.name in expected)
            p.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
            body=archive.extractfile(item).read(item.size+1);need(sha(body)==expected[item.name]['sha256'])
            mode=0o750 if item.mode&0o111 or item.name.startswith('deploy/scripts/') and item.name.endswith('.sh') else 0o640
            create(p,body,mode);os.chown(p,0,gid)
    for folder,dirs,_ in os.walk(root):
        os.chown(folder,0,gid);os.chmod(folder,0o750)
def copy_packages(source,destination,expected,gid):
    need(inventory(source)==expected);destination.mkdir(mode=0o750,parents=True,exist_ok=True)
    for name,item in expected.items():
        p=destination.joinpath(*safe_name(name).parts);p.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
        original=source/name;need(protected_hash(original)==item['sha256'])
        # Bounded streaming copy; preserve the source fingerprint before/after.
        mode=0o750 if item['executable'] else 0o640
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            os.fchmod(fd,0o600)
            with original.open('rb') as inp,os.fdopen(fd,'wb',closefd=False) as out:
                shutil.copyfileobj(inp,out,1024*1024);out.flush();os.fsync(fd)
        finally:os.close(fd)
        sync(p.parent)
        need(protected_hash(original)==item['sha256']==protected_hash(p))
        os.chown(p,0,gid);os.chmod(p,mode)
    need(inventory(source)==expected and inventory(destination)==expected)
    for folder,_,_ in os.walk(destination):os.chown(folder,0,gid);os.chmod(folder,0o750)
def materialize_lib64(venv):
    alias=venv/'lib64'
    if alias.is_symlink():
        need(os.readlink(alias)=='lib' and alias.resolve()==venv/'lib')
        # This is the verified new venv's own alias, never a baseline/shared path.
        alias.unlink();shutil.copytree(venv/'lib',alias,symlinks=False)
    else:need(not alias.exists() or alias.is_dir())
    need(not alias.is_symlink())
def runtime_manifest(release):
    result={}
    for root in (release/'backend/venv',):
        for name,item in inventory(root).items():result['backend/venv/'+name]=item['sha256']
    for cache in release.rglob('__pycache__'):
        if 'venv' in cache.relative_to(release).parts:continue
        need(not cache.is_symlink())
        for p in cache.iterdir():result[p.relative_to(release).as_posix()]=sha(protected(p))
    return result
@contextmanager
def locks():
    import fcntl
    fds=[]
    try:
        for name in ('/run/lock/careertrellis-maintenance.lock','/run/lock/careertrellis-pilot-auth-acceptance.lock'):
            p=Path(name);s=p.lstat();need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022)
            fd=os.open(p,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK);fds.append((fd,p,s.st_dev,s.st_ino))
            need((os.fstat(fd).st_dev,os.fstat(fd).st_ino)==(s.st_dev,s.st_ino));fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
        for fd,p,dev,ino in fds:need((p.lstat().st_dev,p.lstat().st_ino)==(dev,ino))
    finally:
        for fd,*_ in reversed(fds):os.close(fd)
def pointer(name):
    p=ROOT/name;s=p.lstat();need(stat.S_ISLNK(s.st_mode) and s.st_uid==0)
    resolved=p.resolve(strict=True);need(resolved.parent==ROOT/'releases');return resolved.name
def bootstrap(stage,plan):
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname()=='careertrellis-staging-web-1'
         and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)
    for name in ('prepare_release.py','run_checks.py'):
        raw=protected(stage/name)
        committed=run(['git','-C',REPOSITORY,'cat-file','blob',plan['ops_commit']+':'+(SOURCE/name).as_posix()])
        need(raw==committed)
def execute(stage,revision,plan_digest):
    import grp
    need(stage==STAGE);plan=decode(protected(stage/(revision+'-plan.json')))
    validate_plan(plan,revision);need(digest_plan(plan)==plan_digest);bootstrap(stage,plan)
    source_raw=protected(stage/SPECS[revision]['name'],limit=128*1024**2)
    need(len(source_raw)==SPECS[revision]['bytes'] and sha(source_raw)==SPECS[revision]['sha256'])
    expected=archive_files(source_raw)
    need(expected==archive_files(run(['git','-C',REPOSITORY,'archive',revision],timeout=60)))
    provenance_raw=protected(stage/'baseline-provenance.json',limit=16*1024**2)
    need(sha(provenance_raw)==plan['provenance_sha256']);provenance=decode(provenance_raw);validate_provenance(provenance)
    need(sha(run(['git','-C',REPOSITORY,'-c','core.abbrev=40','diff','--binary','--no-ext-diff','--no-textconv',BASE,revision,'--']))==plan['source_diff_sha256'])
    baseline=ROOT/'releases'/BASE
    for name,wanted in provenance['requirements'].items():
        need(sha(protected(baseline/name))==wanted==expected[name]['sha256'])
    interpreter=Path(provenance['interpreter']['path']);need(sha(protected(interpreter))==provenance['interpreter']['sha256'])
    observed=decode(run([str(interpreter),'-I','-S','-B','-c',
        'import json,platform;print(json.dumps({"python":platform.python_version(),"system":platform.system(),"machine":platform.machine()}))']))
    need(observed=={'python':provenance['interpreter']['python_version'],**provenance['platform']})
    packages=baseline/'backend/venv/lib/python3.12/site-packages'
    need(inventory(packages)==provenance['site_packages'] and inventory(BROWSER)==provenance['browser'])
    verify_reused_packages(provenance['site_packages'],accepted_provenance())
    peak=needed_bytes(expected,provenance);validate_capacity(shutil.disk_usage(ROOT).free,peak)
    need(os.statvfs(ROOT).f_favail>=len(expected)+len(provenance['site_packages'])*3+4096)
    memory={line.split(':',1)[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
            if line.startswith('MemAvailable:')}
    need(memory.get('MemAvailable',0)>=768*1024**2)
    with locks():
        need(pointer('current')==BASE and pointer('previous')==PREVIOUS)
        protected(ROOT/'releases',directory=True)
        for path in (ROOT/'releases'/revision,EVIDENCE/(revision+'-attempt'),EVIDENCE/(revision+'.json')):
            need(not os.path.lexists(path))
        namespace(EVIDENCE,0o700,0,'protected preparation receipts')
        attempt=EVIDENCE/(revision+'-attempt');attempt.mkdir(mode=0o700);sync(EVIDENCE)
        # The exclusive journal namespace/attempt reserves ownership first;
        # durable intent precedes all application/preparation payload mutation.
        create(attempt/'intent.json',canonical({'schema':'careertrellis.app-preparation-intent/v1',
            'revision':revision,'build':plan['build'],'plan_sha256':plan_digest,'ops_commit':plan['ops_commit'],
            'started_utc':stamp(),'owner_pid':os.getpid(),'peak_reservation_bytes':peak}))
        release=ROOT/'releases'/revision;gid=grp.getgrnam('careertrellis').gr_gid
        release.mkdir(mode=0o750);os.chown(release,0,gid);sync(release.parent)
        try:
            extract(source_raw,release,expected,gid);verify_source(release,expected)
            venv=release/'backend/venv'
            run([str(interpreter),'-I','-S','-B','-m','venv','--without-pip','--copies',str(venv)],timeout=60)
            copy_packages(packages,venv/'lib/python3.12/site-packages',provenance['site_packages'],gid)
            materialize_lib64(venv)
            for folder,_,files in os.walk(venv):
                os.chown(folder,0,gid);os.chmod(folder,0o750)
                for name in files:
                    p=Path(folder)/name;need(not p.is_symlink());os.chown(p,0,gid)
                    os.chmod(p,0o750 if p.stat().st_mode&0o111 else 0o640)
            need(inventory(packages)==provenance['site_packages'])
            checks=run_tests(stage,release,revision,attempt)
            verify_source(release,expected)
            need(inventory(BROWSER)==provenance['browser'])
            need(pointer('current')==BASE and pointer('previous')==PREVIOUS)
            need(shutil.disk_usage(ROOT).free>=FLOOR)
            manifest=runtime_manifest(release);manifest_raw=canonical(manifest)
            create(EVIDENCE/(revision+'-runtime.json'),manifest_raw)
            receipt={'schema':'careertrellis.app-preparation/v1','release':revision,'build':plan['build'],
                'source_diff_sha256':plan['source_diff_sha256'],'result':'pass',
                'runtime_manifest':str(EVIDENCE/(revision+'-runtime.json')),
                'runtime_manifest_sha256':sha(manifest_raw),'checks':checks}
            create(EVIDENCE/(revision+'.json'),canonical(receipt))
            create(attempt/'complete.json',canonical({'result':'prepared_inactive','ended_utc':stamp(),
                'receipt_sha256':sha(canonical(receipt)),'pointers_unchanged':True}))
            return {'result':'prepared_inactive','revision':revision,'receipt_sha256':sha(canonical(receipt))}
        except BaseException:
            try:create(attempt/'failed-inspection-required.json',canonical({'result':'inspection_required',
                'ended_utc':stamp(),'no_replay':True}))
            except BaseException:pass
            raise Refused('preparation_failed_retained_attempt_inspect_no_replay') from None
def digest_plan(plan):return sha(canonical(plan))
def sandbox_properties(release,work,browser_source):
    browser_alias=work/'browsers'
    return ['--property=Type=exec','--property=RemainAfterExit=yes','--property=Restart=no',
        '--property=User=nobody','--property=Group=nogroup',
        '--property=SupplementaryGroups=careertrellis','--property=PrivateNetwork=yes',
        '--property=PrivateTmp=yes','--property=ProtectSystem=strict','--property=ProtectHome=yes',
        '--property=NoNewPrivileges=yes','--property=RestrictSUIDSGID=yes',
        '--property=MemoryMax=671088640','--property=MemorySwapMax=0','--property=TasksMax=192',
        '--property=RuntimeMaxSec=1200','--property=UMask=0077',
        '--property=LimitFSIZE=67108864','--property=KillMode=control-group',
        '--property=ReadOnlyPaths='+str(release)+' '+str(browser_alias),
        '--property=BindReadOnlyPaths='+str(browser_source)+':'+str(browser_alias),
        '--property=ReadWritePaths='+str(work),
        '--property=InaccessiblePaths=/etc/careertrellis /etc/caddy /var/lib/careertrellis -/var/lib/postgresql /var/backups /var/log /srv -/run/postgresql']
def unit_state(unit):
    need(re.fullmatch(r'careertrellis-release-(?:prep-[0-9a-f]{12}-20261026|preparation-sandbox-probe-20261026)',unit))
    keys={'LoadState','ActiveState','SubState','MainPID','ControlPID','Job','ControlGroup','Result','ExecMainStatus','InvocationID'}
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
    need(current['invocation_id']==proof['invocation_id'])
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
def accepted_provenance():
    values={}
    for name,expected in ACCEPTED.items():
        raw=protected(ACCEPTED_STAGE/name,limit=16*1024**2);need(sha(raw)==expected);values[name]=decode(raw)
    manifest=values['manifest.json'];success=values['success.json']
    need(manifest['revision']==BASE and success['revision']==BASE
         and success['manifest_sha256']==ACCEPTED['manifest.json']
         and success['result']=='inactive_imported' and success['activation_performed'] is False
         and success['destination']==str(ROOT/'releases'/BASE))
    return values['venv-provenance.json']
def run_tests(stage,release,revision,attempt):
    work=WORK/revision
    import grp
    namespace(WORK,0o750,grp.getgrnam('careertrellis').gr_gid,'isolated synthetic preparation work')
    need(not os.path.lexists(work));work.mkdir(mode=0o700)
    check_source=protected(stage/'run_checks.py')
    # Make only this reviewed task copy accessible to the unprivileged sandbox.
    runner=work/'run_checks.py';create(runner,check_source,0o644)
    browser_alias=work/'browsers';browser_alias.mkdir(mode=0o755)
    os.chown(work,65534,65534)
    unit='careertrellis-release-prep-'+revision[:12]+'-20261026'
    absence=subprocess.run(['systemctl','show',unit,'--property=LoadState','--value'],
        stdin=subprocess.DEVNULL,capture_output=True,timeout=15,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
    need(absence.returncode in (0,1,4) and absence.stdout.strip()==b'not-found')
    command=['systemd-run','--unit='+unit,'--wait','--pipe','--collect',
        *sandbox_properties(release,work,BROWSER),str(release/'backend/venv/bin/python'),
        '-I','-B',str(runner),'--release',str(release),'--revision',revision,'--work',str(work)]
    create(attempt/'test-unit-intent.json',canonical({'unit':unit,'revision':revision,'started_utc':stamp()}))
    log=attempt/'synthetic-tests.log'
    fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb',closefd=False) as output:
            p=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'})
            create(attempt/'test-client.json',canonical({'unit':unit,'client_pid':p.pid,'owner_pid':os.getpid(),'started_utc':stamp()}))
            terminal=wait_execution_terminal(unit,p,1230)
            create(attempt/'test-unit-execution-terminal.json',canonical(terminal))
            collected=collect_empty_unit(unit,terminal,p)
            create(attempt/'test-unit-terminal.json',canonical(collected))
            output.flush();os.fsync(fd)
    except BaseException:
        try:
            state=unit_state(unit)
            create(attempt/'test-unit-inspection-required.json',canonical({'unit':unit,'owner_pid':os.getpid(),
                'client_pid':p.pid if 'p' in locals() else None,'client_exit':p.poll() if 'p' in locals() else None,
                'state':state,'cgroup':cgroup_state(unit,state),'no_replay':True,'observed_utc':stamp()}))
        except BaseException:pass
        raise
    finally:os.close(fd)
    need(p.returncode==0)
    proof=work/'checks.json';s=proof.lstat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid==65534 and s.st_nlink==1 and s.st_size<=131072)
    result=decode(proof.read_bytes());need(result['revision']==revision and result['build']==SPECS[revision]['build'])
    expected={'p0','objects','ai','syntax','authority_native','systemd_native','runtime_isolation'}
    if revision==TARGET:expected|={'cache','summaries','evaluation','serper_preparation'}
    need(set(result['checks'])==expected and all(v is True for v in result['checks'].values()))
    create(attempt/'tests.json',canonical(result))
    create(attempt/'retained-resources.json',canonical({'unit':unit,'work':str(work),
        'release':str(release),'retention':'Primary review after accepted activation/correction retention; no automatic deletion',
        'tests_log_sha256':hash_file(log),'finished_utc':stamp()}))
    return result['checks']
def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--revision',choices=tuple(SPECS),required=True)
    p.add_argument('--approved-plan-sha256',required=True);a=p.parse_args()
    try:result=execute(a.stage,a.revision,a.approved_plan_sha256)
    except BaseException:
        print('{"result":"refused_or_failed_inspect_retained_attempt","no_replay":true}');return 1
    print(json.dumps(result));return 0
if __name__=='__main__':raise SystemExit(main())
