"""Additive WEB installation only; never enroll, issue, consume or activate.

Public arguments: install|inspect COMMIT ARCHIVE_SHA MANIFEST_SHA LAUNCHER_SHA
RUNTIME_TREE_SHA. The upload name and all destinations are fixed. inspect is
read-only reconciliation, never a continuation. Interrupted attempts remain.
"""
from contextlib import contextmanager
import hashlib
import importlib.abc
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import resource
import shutil
import stat
import subprocess
import sys
import tarfile

OPS='bbbc4298b2ca80df107e64d2673d6c1f243c5e41'
APPLICATION='cafad5b48a579adc80695519f0f8b7d39f621cd5'
BASE=Path('/opt/careertrellis/owner-recovery-reserve')
LIBRARY=Path('/opt/careertrellis/staging/releases')/OPS
RUNTIME=LIBRARY/'backend/venv'
RUNTIME_SOURCE=LIBRARY.parent/'4a7bf3c46d3b401616c7bfaefacb22c7c7ff30ab'/'backend/venv'
PYTHON=RUNTIME/'bin/python'
CURRENT=Path('/opt/careertrellis/staging/current')
STATE=Path('/var/lib/careertrellis-owner-recovery-reserve')
PRIMARY=Path('/var/lib/careertrellis-owner-recovery')
WRAPPER=Path('/usr/local/sbin/careertrellis-owner-recovery-reserve')
MODULES=('owner_recovery_reserve','owner_recovery_reserve_host','owner_recovery_reserve_cli',
    'owner_recovery_reserve_relay','owner_recovery_reserve_operator')
NAMES={*('backend/'+name+'.py' for name in MODULES),'source-manifest.json',WRAPPER.name}
LIMIT=1048576
ERROR='Reserve installation refused or incomplete; retain all evidence and original state.'

def require(value):
    if not value:raise ValueError(ERROR)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def strict(raw):
    def pairs(items):
        result={}
        for key,value in items:require(key not in result);result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError(ERROR)))
def exists(path):return path.exists() or path.is_symlink()
def parents(path):
    require(path.is_absolute())
    for parent in path.parents:
        item=parent.lstat()
        require(stat.S_ISDIR(item.st_mode) and item.st_uid==0 and not item.st_mode&0o022)
def protected(path,limit=LIMIT,mode=None):
    parents(path);return read_file(path,limit,mode)
def read_file(path,limit=LIMIT,mode=None):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        item=os.fstat(fd)
        require(stat.S_ISREG(item.st_mode) and item.st_uid==0 and item.st_nlink==1
            and not item.st_mode&0o022 and item.st_size<=limit
            and (mode is None or stat.S_IMODE(item.st_mode)==mode))
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(limit+1)
        require(len(raw)<=limit);return raw
    finally:os.close(fd)
def sync(directory):
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
def write(path,raw,mode=0o600):
    parents(path);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as stream:
        os.fchmod(stream.fileno(),mode);stream.write(raw);stream.flush();os.fsync(stream.fileno())
    sync(path.parent);require(protected(path,mode=mode)==raw)
def directory(path):
    parents(path);path.mkdir(mode=0o700)
    os.chmod(path,0o700,follow_symlinks=False);sync(path.parent)
    item=path.lstat();require(stat.S_ISDIR(item.st_mode) and item.st_uid==0 and stat.S_IMODE(item.st_mode)==0o700)
def pins(values):
    require(len(values)==5 and re.fullmatch('[a-f0-9]{40}',values[0]) and values[0]!=OPS
        and all(re.fullmatch('[a-f0-9]{64}',item) for item in values[1:]))
    return {'commit':values[0],'archive':values[1],'manifest':values[2],'launcher':values[3],'runtime':values[4]}
def marker(commit):return Path('/var/lib')/('careertrellis-owner-reserve-install-'+commit+'.json')
def upload(commit):return Path('/var/tmp')/('careertrellis-owner-reserve-'+commit+'.tar')
def uploaded(commit):
    path=upload(commit);parents(path.parent);item=path.parent.lstat()
    # Fixed sticky upload directory: another uid cannot replace this root-owned
    # single-link 0600 inode. No general writable-ancestry exception is allowed.
    require(path.parent==Path('/var/tmp') and stat.S_ISDIR(item.st_mode) and item.st_uid==0
        and stat.S_IMODE(item.st_mode)==0o1777)
    return read_file(path,mode=0o600)
def launcher(commit):
    return ('#!/bin/sh\nset -eu\nexec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 '
        'CAREERTRELLIS_CONFIG_READ_ONLY=1 PYTHONDONTWRITEBYTECODE=1 '+str(PYTHON)+' -I -B '+
        str(BASE/commit/'backend/owner_recovery_reserve_cli.py')+' "$@"\n').encode()
def unpack(raw,expected):
    require(len(raw)<=LIMIT and digest(raw)==expected['archive'])
    files={};total=0
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for item in archive:
            require(item.name in NAMES and item.name not in files and item.isfile()
                and item.type==tarfile.REGTYPE and item.mode==0o600 and item.uid==item.gid==0
                and not item.pax_headers and 0<item.size<=LIMIT//2)
            total+=item.size;require(total<=LIMIT)
            value=archive.extractfile(item).read(item.size+1)
            require(len(value)==item.size);files[item.name]=value
    require(set(files)==NAMES and digest(files['source-manifest.json'])==expected['manifest']
        and digest(files[WRAPPER.name])==expected['launcher'] and files[WRAPPER.name]==launcher(expected['commit']))
    manifest=strict(files['source-manifest.json'])
    require(type(manifest) is dict and set(manifest)=={'version','operations','wrapper','library'}
        and type(manifest['version']) is int and manifest['version']==1 and manifest['operations']==OPS
        and type(manifest['wrapper']) is dict and set(manifest['wrapper'])==NAMES-{'source-manifest.json',WRAPPER.name}
        and type(manifest['library']) is dict and manifest['library'])
    for name,sha in manifest['wrapper'].items():require(digest(files[name])==sha)
    for name,sha in manifest['library'].items():
        require(type(name) is str and re.fullmatch(r'(backend/[a-z0-9_]+\.py|deploy/systemd/[A-Za-z0-9_.@/-]+)',name)
            and '..' not in Path(name).parts and type(sha) is str and re.fullmatch('[a-f0-9]{64}',sha))
    require({'backend/owner_recovery.py','backend/identity_plan_store.py','backend/identity_plan_packet.py'}<=set(manifest['library']))
    return files,manifest
def verify_library(manifest):
    for name,sha in manifest['library'].items():require(digest(protected(LIBRARY/name))==sha)
def runtime_manifest(root,relocated=None):
    """Same relocation comparison as immutable bbbc installer; bounded reads."""
    parents(root);require(root.is_dir() and not root.is_symlink())
    result={};count=0;total=0
    for path in [root,*root.rglob('*')]:
        count+=1;require(count<=100000)
        item=path.lstat();relative=path.relative_to(root).as_posix();require(item.st_uid==0)
        if path.is_symlink():
            resolved=path.resolve(strict=True)
            require(resolved.is_relative_to(root) or (path.parent==root/'bin' and
                path.name in ('python','python3','python3.12') and resolved==Path('/usr/bin/python3').resolve(strict=True)))
            require(not resolved.is_relative_to(root) or not Path(os.readlink(path)).is_absolute())
            parents(resolved);result[relative]=('link',os.readlink(path))
        else:
            require(not item.st_mode&0o022 and (stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode)))
            if stat.S_ISREG(item.st_mode):
                total+=item.st_size;require(total<=2*1024**3)
                content=protected(path,limit=256*1024**2)
                if relocated is not None and (path.parent==root/'bin' or path==root/'pyvenv.cfg') and not content.startswith(b'\x7fELF'):
                    content=content.replace(str(root).encode(),str(relocated).encode())
                result[relative]=('file',digest(content))
    return result
def verify_runtime(expected):
    # The caller's hash adds freshness; it cannot replace retained provenance.
    require(runtime_manifest(RUNTIME_SOURCE,RUNTIME)==runtime_manifest(RUNTIME))
    require(runtime_digest()==expected['runtime'])
def runtime_digest():
    """Public digest of approved installed runtime tree, including modes/links."""
    parents(RUNTIME);entries={};total=0
    for path in [RUNTIME,*RUNTIME.rglob('*')]:
        require(len(entries)<100000);item=path.lstat();name=path.relative_to(RUNTIME).as_posix()
        require(item.st_uid==0)
        if stat.S_ISLNK(item.st_mode):
            target=path.resolve(strict=True)
            require((target.is_relative_to(RUNTIME) and not Path(os.readlink(path)).is_absolute()) or
                (path.parent==RUNTIME/'bin' and path.name in ('python','python3','python3.12')
                 and target==Path('/usr/bin/python3').resolve(strict=True)))
            parents(target);entries[name]=['link',os.readlink(path)]
        elif stat.S_ISDIR(item.st_mode):
            require(not item.st_mode&0o022);entries[name]=['dir',stat.S_IMODE(item.st_mode)]
        else:
            require(stat.S_ISREG(item.st_mode) and not item.st_mode&0o022 and item.st_nlink==1)
            total+=item.st_size;require(total<=2*1024**3)
            entries[name]=['file',stat.S_IMODE(item.st_mode),digest(protected(path,limit=256*1024**2))]
    return digest(encoded(entries))

@contextmanager
def locked():
    import fcntl
    descriptors=[]
    try:
        paths=[PRIMARY/'lock',*(Path('/run/lock')/name for name in ('careertrellis-maintenance.lock',
            'careertrellis-postgres-migration.lock','careertrellis-admission-operation.lock',
            'careertrellis-provider-secret-rotation.lock'))]
        for path in paths:
            lock_parents(path);fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK);descriptors.append(fd)
            allowed=(0o600,) if path==PRIMARY/'lock' else (0o600,0o644)
            item=os.fstat(fd);require(stat.S_ISREG(item.st_mode) and item.st_uid==0 and item.st_nlink==1
                and stat.S_IMODE(item.st_mode) in allowed and item.st_size==0)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        for fd in reversed(descriptors):os.close(fd)

def lock_parents(path):
    if path.parent==Path('/run/lock'):
        parents(path.parent);item=path.parent.lstat()
        require(stat.S_ISDIR(item.st_mode) and item.st_uid==0 and
            (not item.st_mode&0o022 or stat.S_IMODE(item.st_mode)==0o1777))
    else:parents(path)

def load_library(manifest):
    """Fresh isolated worker only: source bytes, never project pyc or imports."""
    mapping={Path(name).stem:(LIBRARY/name,sha) for name,sha in manifest['library'].items() if name.startswith('backend/')}
    require(all(name not in sys.modules for name in mapping))
    class Loader(importlib.abc.Loader):
        def create_module(self,spec):return None
        def exec_module(self,module):
            path,sha=mapping[module.__name__];raw=protected(path);require(digest(raw)==sha)
            exec(compile(raw,str(path),'exec'),module.__dict__)
    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if fullname in mapping:return importlib.util.spec_from_file_location(fullname,mapping[fullname][0],loader=Loader())
            candidate=LIBRARY/'backend'/fullname.split('.')[0]
            require(not exists(candidate) and not exists(candidate.with_suffix('.py')))
            return None
    sys.meta_path.insert(0,Finder())

def snapshot():
    import owner_recovery as recovery
    import identity_plan_store as plans
    import identity_plan_packet as packet
    import identity_web_handoff as handoff
    from sqlalchemy import create_engine,text
    from sqlalchemy.pool import NullPool
    parents(CURRENT);item=CURRENT.lstat()
    require(stat.S_ISLNK(item.st_mode) and item.st_uid==0 and CURRENT.resolve(strict=True)==LIBRARY.parent/APPLICATION)
    parents(PRIMARY);primary_info=PRIMARY.lstat()
    require(stat.S_ISDIR(primary_info.st_mode) and primary_info.st_uid==0 and stat.S_IMODE(primary_info.st_mode)==0o700)
    primary_files={}
    for path in PRIMARY.iterdir():
        require(len(primary_files)<32);primary_files[path.name]=protected(path,mode=0o600)
    require('credential.json' in primary_files and 'lock' in primary_files)
    primary=recovery.Store().read()
    require(primary is not None and primary['version']==1 and primary['state']=='active'
        and primary['binding']==recovery.owner_snapshot())
    engine=create_engine(handoff.ops.migration_connection_url(handoff.operator.DATABASE),
        poolclass=NullPool,echo=False,hide_parameters=True,connect_args={'connect_timeout':5})
    try:
        with handoff.operator.secure_connection(engine) as connection:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
            connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
            require(connection.scalar(text("SELECT to_regclass('careertrellis_admission.identity_transitions') IS NULL")) is True)
    finally:engine.dispose()
    selected,pair=plans.load(activation=True)
    original=packet.validate(protected(plans.archive(recovery.strict_json(selected)['digest'])))
    require(original['version']==1 and original['switch']==pair and pair['provider']['operations']==OPS
        and pair['provider']['source_application']==APPLICATION and len(pair['provider']['transitions'])==1)
    # Private byte comparisons stay in memory, never in public receipts.
    services={name:handoff.ops.command(['/usr/bin/systemctl','show',name,
        '--property=ActiveState,SubState,MainPID,NRestarts','--no-pager']) for name in handoff.ops.SERVICES}
    return primary_files,selected,handoff.read_files(),os.readlink(CURRENT),services,{
        name:protected(Path('/usr/local/sbin')/name) for name in
        ('careertrellis-owner-recovery','careertrellis-owner-recovery-bridge')}

def inspect(expected):
    path=marker(expected['commit']);target=BASE/expected['commit']
    if not exists(path):
        require(not any(exists(item) for item in (target,STATE,WRAPPER)))
        return {'outcome':'no_install_attempt','authorization':False}
    require(strict(protected(path,mode=0o600))=={'version':1,'pins':expected,'phase':'started'})
    present=[]
    for name in sorted(NAMES- {WRAPPER.name}):
        location=target/name
        if exists(location):present.append(name)
    complete=exists(target/'installation.json')
    if complete:
        require(strict(protected(target/'installation.json',mode=0o600))=={'version':1,'pins':expected,'outcome':'installed_not_enrolled'})
        files,manifest=unpack(uploaded(expected['commit']),expected)
        for name in NAMES-{WRAPPER.name}:require(protected(target/name,mode=0o600)==files[name])
        require(protected(WRAPPER,mode=0o755)==files[WRAPPER.name])
    return {'outcome':'recorded_installation' if complete else 'incomplete_retained',
        'files_present':present,'launcher_present':exists(WRAPPER),'authorization':False}

def smoke(expected):
    """Fresh process avoids reusing modules from the installer's preflight."""
    script=Path(__file__).absolute();sha=digest(protected(script))
    values=[expected[name] for name in ('commit','archive','manifest','launcher','runtime')]
    result=subprocess.run([str(PYTHON),'-I','-B',str(script),'_probe',*values,sha],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=60,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8',
            'CAREERTRELLIS_CONFIG_READ_ONLY':'1','PYTHONDONTWRITEBYTECODE':'1'})
    require(result.returncode==0 and result.stdout==b'{"outcome":"source_loader_verified"}\n')

def probe(expected):
    target=BASE/expected['commit']
    require(digest(protected(target/'source-manifest.json',mode=0o600))==expected['manifest'])
    manifest=strict(protected(target/'source-manifest.json',mode=0o600))
    verify_library(manifest)
    for name,sha in manifest['wrapper'].items():require(digest(protected(target/name,mode=0o600))==sha)
    path=target/'backend/owner_recovery_reserve_cli.py'
    raw=protected(path,mode=0o600)
    namespace={'__name__':'reserve_install_probe','__file__':str(path)}
    exec(compile(raw,str(path),'exec'),namespace)
    verify=namespace['load_verified']()
    import importlib
    for name in MODULES:importlib.import_module(name)
    require(verify() is True)
    return {'outcome':'source_loader_verified'}

def install(expected):
    files,manifest=unpack(uploaded(expected['commit']),expected)
    verify_library(manifest);verify_runtime(expected)
    target=BASE/expected['commit']
    with locked():
        before=snapshot()
        require(all(not exists(path) for path in (marker(expected['commit']),target,STATE,WRAPPER)))
        if exists(BASE):
            parents(BASE);item=BASE.lstat();require(stat.S_ISDIR(item.st_mode) and item.st_uid==0 and stat.S_IMODE(item.st_mode)==0o700)
        require(all(shutil.disk_usage(path).free>=5*1024**3+2*LIMIT for path in ('/opt','/var/lib','/usr/local')))
        # First mutation; an interrupted marker is never replayed or replaced.
        write(marker(expected['commit']),encoded({'version':1,'pins':expected,'phase':'started'}))
        if not exists(BASE):directory(BASE)
        directory(target);directory(target/'backend');directory(STATE);directory(STATE/'convergence')
        for name in sorted(NAMES-{WRAPPER.name}):write(target/name,files[name])
        verify_library(manifest);require(snapshot()==before);verify_runtime(expected)
        for name in NAMES-{WRAPPER.name}:require(protected(target/name,mode=0o600)==files[name])
        # Compile every approved source without importing a live action.
        for name in MODULES:compile(files['backend/'+name+'.py'],str(target/'backend'/(name+'.py')),'exec')
        smoke(expected)
        require(snapshot()==before)
        write(WRAPPER,files[WRAPPER.name],0o755)  # sole publication, always last
        require(snapshot()==before)
        write(target/'installation.json',encoded({'version':1,'pins':expected,'outcome':'installed_not_enrolled'}))
    return {'outcome':'installed_not_enrolled','authorization':False,'files':7}

def main(argv=None):
    try:
        resource.setrlimit(resource.RLIMIT_CORE,(0,0));args=list(sys.argv[1:] if argv is None else argv)
        require(os.geteuid()==0 and os.uname().nodename=='careertrellis-staging-web-1')
        require(len(args) in (6,7) and args[0] in ('install','inspect','_worker','_probe'))
        expected=pins(args[1:6])
        if args[0]=='inspect':require(len(args)==6);result=inspect(expected)
        elif args[0] in ('_worker','_probe'):
            require(len(args)==7 and digest(protected(Path(__file__).absolute()))==args[6]
                and sys.flags.isolated and sys.dont_write_bytecode and os.environ.get('CAREERTRELLIS_CONFIG_READ_ONLY')=='1')
            verify_runtime(expected)
            if args[0]=='_probe':result=probe(expected)
            else:
                files,manifest=unpack(uploaded(expected['commit']),expected)
                verify_library(manifest);load_library(manifest);result=install(expected)
        else:
            require(len(args)==6);script=Path(__file__).absolute();script_sha=digest(protected(script))
            files,manifest=unpack(uploaded(expected['commit']),expected)
            verify_library(manifest);verify_runtime(expected)
            completed=subprocess.run([str(PYTHON),'-I','-B',str(script),'_worker',*args[1:],script_sha],
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=240,
                env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8',
                    'CAREERTRELLIS_CONFIG_READ_ONLY':'1','PYTHONDONTWRITEBYTECODE':'1'})
            require(completed.returncode==0 and len(completed.stdout)<=4096)
            result=strict(completed.stdout);require(result=={'outcome':'installed_not_enrolled','authorization':False,'files':7})
        print(encoded(result).decode());return 0
    except BaseException:
        print(ERROR,file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
