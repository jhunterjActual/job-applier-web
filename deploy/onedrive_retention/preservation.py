"""Failed-capture consistency and one exact native historical preservation path.

Supplied fixture observations remain unauthenticated and cannot clear a guard,
authorize another attempt, accept a backup, or authorize cleanup. The fixed
source94 collector authenticates originals and fresh terminal custody under all
existing canonical fences, then writes a preservation receipt once. Historical
manual units use that explicit reviewed binding, never a guessed suffix.
"""
import datetime as dt
import hashlib
import json
import re
from pathlib import PurePosixPath
from pathlib import Path
import os
import stat
import socket
import itertools

HISTORICAL_TRANSACTION = '6a77c0bc801a4a8ca672abdf690a6301'
HISTORICAL_ARCHIVE_SHA256 = '8710e766360cb4c5faf092ef6ad00b0fe3215fbf6597e88563dbed11d6c40e8f'
HISTORICAL_GENERATION_SHA256 = '3ddb9f969243977175fc7c0758d5e7c3ec6ff77c53e89abb1c38dca9ddba2457'
HISTORICAL_PLAN_SHA256 = 'a38c82024bf796e2f61f8890828a13a6215e5b7c67b484f4181a03e9f7bfb6d0'
HISTORICAL_DOWNLOAD_SHA256 = 'f3af3790d17b0a461642a147c2383e691ba0601ad292a61bf6d0c16471051b74'
HISTORICAL_SOURCE = '94b6328efdefea6f0f7d08a17dbba4b01cb032bc'
HISTORICAL_ROOT = Path('/var/lib/careertrellis-onedrive-bundle-acceptance') / HISTORICAL_TRANSACTION
HISTORICAL_INPUT = Path('/var/lib/careertrellis/onedrive-bundles') / HISTORICAL_TRANSACTION
HISTORICAL_EVIDENCE = Path('/var/backups/careertrellis/onedrive-policy-activation-20261026')
HISTORICAL_RECEIPT = HISTORICAL_EVIDENCE / 'native-info-preservation.json'
HISTORICAL_UNIT = 'careertrellis-onedrive-bundle-restore-' + HISTORICAL_TRANSACTION + '.service'
HISTORICAL_LOCKS = ('/var/lib/careertrellis/paired-maintenance/db/control.lock',
                    '/run/lock/careertrellis-maintenance.lock',
                    '/run/lock/careertrellis-pgbackrest-repository.lock')
_INHERITED_SEAL = object()


class PreservationRefused(ValueError):
    pass


def need(value):
    if not value:
        raise PreservationRefused('failed_capture_preservation_refused')


def _read(raw):
    need(type(raw) is bytes and 0 < len(raw) <= 262144)
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result)
            result[key] = value
        return result
    return json.loads(raw.decode('ascii'), object_pairs_hook=pairs,
                      parse_constant=lambda _: need(False))


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _shape(value, keys):
    need(type(value) is dict and set(value) == set(keys.split()))


def _hex(value, count):
    need(type(value) is str and re.fullmatch('[0-9a-f]{%d}' % count, value))


def _time(value):
    need(type(value) is str and value.endswith('Z'))
    result = dt.datetime.fromisoformat(value[:-1] + '+00:00')
    need(result.utcoffset() == dt.timedelta(0))
    return result


def validate_preservation(raw_proof, raw_ownership, raw_intent, raw_info_dispatch,
                          raw_terminal, raw_archive_receipt, now):
    """Validate explicit fixture evidence; never authenticate caller assertions.

    Raw byte hashes bind the complete original records. Terminal observations must
    be freshly collected independently of the failed owner; this function cannot
    establish that provenance. A future installed collector must hold canonical
    fences and authenticate originals and complete unit bindings before use.
    """
    try:
        raws = (raw_ownership, raw_intent, raw_info_dispatch, raw_terminal, raw_archive_receipt)
        ownership, intent, dispatch, terminal, archive = [_read(raw) for raw in raws]
        proof = _read(raw_proof)
        _shape(proof, 'schema transaction attempt hashes held_resources')
        need(proof['schema'] == 'careertrellis.failed-capture-preservation/v1')
        _hex(proof['transaction'], 32)
        _hex(proof['attempt'], 32)
        need(proof['hashes'] == dict(zip(('ownership', 'intent', 'info_dispatch', 'terminal', 'archive_receipt'),
                                       map(_hash, raws))))
        _shape(ownership, 'transaction attempt owner units data_root')
        _shape(ownership['owner'], 'pid start_ticks boot_id')
        owner = ownership['owner']
        need(all(type(owner[key]) is int and owner[key] > 0 for key in ('pid', 'start_ticks')))
        need(type(owner['boot_id']) is str and re.fullmatch(
            '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', owner['boot_id']))
        units = ownership['units']
        need(type(units) is list and 0 < len(units) <= 32 and len(set(units)) == len(units))
        need(all(type(unit) is str and re.fullmatch('[A-Za-z0-9_.@-]+\\.service', unit) for unit in units))
        root = ownership['data_root']
        need(type(root) is str and PurePosixPath(root).parent == PurePosixPath(
            '/var/lib/careertrellis-onedrive-bundle-acceptance') and PurePosixPath(root).name == proof['attempt'])
        _shape(intent, 'transaction attempt ownership_sha256 operation')
        need(intent['ownership_sha256'] == _hash(raw_ownership) and intent['operation'] == 'info-only')
        _shape(dispatch, 'transaction attempt intent_sha256 owner phases exit_code')
        need(dispatch['intent_sha256'] == _hash(raw_intent) and dispatch['owner'] == owner
             and dispatch['phases'] == ['info'] and type(dispatch['exit_code']) is int and dispatch['exit_code'] != 0)
        _shape(terminal, 'transaction attempt ownership_sha256 dispatch_sha256 observed_at observer owner_absence units data')
        need(terminal['ownership_sha256'] == _hash(raw_ownership) and terminal['dispatch_sha256'] == _hash(raw_info_dispatch))
        _shape(terminal['observer'], 'pid start_ticks boot_id')
        observer = terminal['observer']
        need(observer['boot_id'] == owner['boot_id'] and type(observer['pid']) is int and observer['pid'] > 0
             and type(observer['start_ticks']) is int and observer['start_ticks'] > 0 and observer['pid'] != owner['pid'])
        _shape(terminal['owner_absence'], 'identity proc_state')
        need(terminal['owner_absence'] == {'identity': owner, 'proc_state': 'absent'})
        need(type(now) is dt.datetime and now.utcoffset() == dt.timedelta(0)
             and 0 <= (now - _time(terminal['observed_at'])).total_seconds() <= 120)
        need(type(terminal['units']) is dict and set(terminal['units']) == set(units))
        for state in terminal['units'].values():
            need(type(state) is dict and type(state.get('main_pid')) is int
                 and type(state.get('control_pid')) is int)
            need(state == {'active_state': 'inactive', 'main_pid': 0, 'control_pid': 0,
                           'job': '', 'cgroup_members': [], 'cgroup_state': 'absent'})
        need(terminal['data'] == {'root': root, 'entries': [], 'mounts': [], 'socket_entries': []})
        _shape(archive, 'transaction attempt resources')
        resources = archive['resources']
        need(type(resources) is list and 0 < len(resources) <= 32 and proof['held_resources'] == resources)
        paths = set()
        for resource in resources:
            _shape(resource, 'path sha256 bytes role')
            path = resource['path']
            need(type(path) is str and path and len(path) <= 1024 and '\\' not in path
                 and all(ord(char) >= 32 for char in path) and '..' not in path.split('/') and path not in paths)
            need(path.startswith('/var/lib/careertrellis/') or path.startswith('onedrive:job-applier-staging/postgresql-bundles/'))
            _hex(resource['sha256'], 64)
            need(type(resource['bytes']) is int and resource['bytes'] > 0
                 and resource['role'] in ('encrypted-archive', 'downloaded-encrypted-archive'))
            paths.add(path)
        need(set(item['role'] for item in resources) == {'encrypted-archive', 'downloaded-encrypted-archive'})
        need({(item['sha256'], item['bytes']) for item in resources if item['role'] == 'encrypted-archive'}
             == {(item['sha256'], item['bytes']) for item in resources if item['role'] == 'downloaded-encrypted-archive'})
        for record in (ownership, intent, dispatch, terminal, archive):
            need(record['transaction'] == proof['transaction'] and record['attempt'] == proof['attempt'])
        return {'result': 'consistent_pre_restore_failure_held', 'authenticated': False,
                'backup_accepted': False, 'guard_clearance_permitted': False,
                'retry_permitted': False, 'cleanup_permitted': False,
                'transaction': proof['transaction'], 'attempt': proof['attempt'],
                'proof_sha256': _hash(raw_proof), 'held_resource_count': len(resources),
                'held_budget_bytes': sum(item['bytes'] for item in resources),
                'native_collection_available': False}
    except Exception:
        raise PreservationRefused('failed_capture_preservation_refused') from None


def require_owned_lock(path, descriptor=None):
    """Prove this PID owns the existing exact canonical FLOCK inode."""
    need(str(path) in HISTORICAL_LOCKS or str(path) == '/run/lock/careertrellis-maintenance.lock')
    path = Path(path)
    for parent in path.parents:
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0)
        need(stat.S_IMODE(meta.st_mode) == 0o1777 if parent == Path('/run/lock')
             else not meta.st_mode & 0o022)
    meta = path.lstat()
    expected_mode = (0o644 if str(path) == '/run/lock/careertrellis-maintenance.lock'
                     and socket.gethostname() == 'careertrellis-staging-web-1' else 0o600)
    need(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0
         and stat.S_IMODE(meta.st_mode) == expected_mode and meta.st_nlink == 1 and meta.st_size == 0)
    if descriptor is not None:
        need(type(descriptor) is int and descriptor >= 0)
        opened = os.fstat(descriptor)
        need((opened.st_dev,opened.st_ino) == (meta.st_dev,meta.st_ino))
    with open('/proc/locks','rb') as stream: raw = stream.read(2*1024**2+1)
    need(len(raw) <= 2*1024**2)
    expected = (os.major(meta.st_dev),os.minor(meta.st_dev),meta.st_ino)
    found = False
    for line in raw.decode('ascii').splitlines():
        fields = line.split()
        for index, field in enumerate(fields):
            match = re.fullmatch('([0-9a-f]+):([0-9a-f]+):([0-9]+)',field)
            if match is None: continue
            observed = tuple(int(value,16 if part < 2 else 10) for part,value in enumerate(match.groups()))
            if observed == expected:
                need(index > 0 and fields[index-1] == str(os.getpid())
                     and 'FLOCK' in fields and 'WRITE' in fields and '->' not in fields)
                found = True
    need(found and (path.lstat().st_dev,path.lstat().st_ino) == (meta.st_dev,meta.st_ino))
    return {'device':meta.st_dev,'inode':meta.st_ino,'owner_pid':os.getpid()}


def _historical_read(path, maximum=131072):
    from . import native_restore
    raw = native_restore.protected(path,maximum=maximum)
    value = _read(raw)
    return raw,value


def _terminal_birth(value):
    from . import native_restore
    need(type(value) is dict and type(value.get('pid')) is int and value['pid'] > 1
         and type(value.get('start_ticks')) is int and value['start_ticks'] > 0
         and type(value.get('boot_id')) is str and re.fullmatch(
             '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',value['boot_id']))
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if value['boot_id'] != boot: return 'previous_boot_terminal'
    try: current = native_restore.process_identity(value['pid'])
    except (FileNotFoundError,ProcessLookupError): return 'absent'
    need(current['start_ticks'] != value['start_ticks'])
    return 'reused_terminal'


def _historical_state():
    """Authenticate the one fixed INFO-only failure without a provider call."""
    from . import bundle, execution, native_restore as native
    root,input_root = HISTORICAL_ROOT,HISTORICAL_INPUT
    raw_ownership,ownership = _historical_read(root/'ownership.json')
    need(ownership.get('schema') == 'careertrellis.native-local-restore-ownership/v1'
         and ownership.get('plan_sha256') == HISTORICAL_PLAN_SHA256)
    plan = ownership['plan']
    need(hashlib.sha256(bundle.canonical(plan)).hexdigest() == HISTORICAL_PLAN_SHA256
         and plan.get('transaction') == HISTORICAL_TRANSACTION
         and plan.get('archive_sha256') == HISTORICAL_ARCHIVE_SHA256
         and plan.get('generation_sha256') == HISTORICAL_GENERATION_SHA256
         and plan.get('attempt_root') == str(root)
         and ('owner_unit' not in plan or plan['owner_unit'] == HISTORICAL_UNIT))
    owner = ownership['owner']; catalog_owner = dict(owner)
    from . import catalog
    catalog.process_identity(catalog_owner)
    meta = root.lstat(); original = ownership['root_identity']
    need(type(original) is list and len(original) >= 2 and original[:2] == [meta.st_dev,meta.st_ino])
    raw_intent,intent = _historical_read(input_root/'native-restore-intent.json')
    need(intent.get('schema') == 'careertrellis.native-local-restore-intent/v1'
         and intent.get('owner') == owner and intent.get('plan_sha256') == HISTORICAL_PLAN_SHA256)
    raw_phase,phase = _historical_read(root/'info-intent.json')
    need(phase.get('owner') == owner and phase.get('plan_sha256') == HISTORICAL_PLAN_SHA256
         and phase.get('phase') == 'info')
    raw_dispatch,dispatch = _historical_read(root/'info-dispatch.json')
    raw_terminal,terminal = _historical_read(root/'info-terminal.json')
    catalog.process_identity(dispatch)
    need(type(terminal.get('exit_code')) is int and terminal['exit_code'] == 31
         and terminal == dict(dispatch,exit_code=31))
    raw_outer,outer = _historical_read(HISTORICAL_EVIDENCE/'native-dispatch-intent.json')
    raw_ack,ack = _historical_read(HISTORICAL_EVIDENCE/'native-dispatch-result.json')
    need(outer.get('schema') == 'careertrellis.native-restore-dispatch/v1'
         and outer.get('transaction') == HISTORICAL_TRANSACTION and outer.get('unit') == HISTORICAL_UNIT
         and outer.get('revision') == HISTORICAL_SOURCE and outer.get('plan_sha256') == HISTORICAL_PLAN_SHA256
         and outer.get('provider_calls') == 0 and outer.get('application_changes') is False
         and ack == dict(outer,result='dispatched'))
    download_raw,download = _historical_read(input_root/'downloaded-receipt.json')
    need(_hash(download_raw) == HISTORICAL_DOWNLOAD_SHA256
         and ownership.get('download_receipt_sha256') == HISTORICAL_DOWNLOAD_SHA256
         and download.get('transaction') == HISTORICAL_TRANSACTION
         and download.get('archive_sha256') == HISTORICAL_ARCHIVE_SHA256
         and download.get('generation_sha256') == HISTORICAL_GENERATION_SHA256
         and download.get('native_restore_accepted') is False
         and type(download.get('archive_bytes')) is int and download['archive_bytes'] > 0)
    for name in ('restore','start','probe','stop','checksums'):
        for suffix in ('intent','dispatch','terminal'):
            need(not os.path.lexists(root/(name+'-'+suffix+'.json')))
    need(not any(os.path.lexists(root/name) for name in
        ('complete.json','postmaster.json','data/postmaster.pid')))
    account = native.postgres_account()
    for directory_name in ('data','socket','tablespaces','pgbackrest-lock','pgbackrest-spool','pgbackrest-includes'):
        path = root/directory_name
        # Exact historical plan coordinates, never a guessed sibling directory.
        keys = {'data':'data_root','socket':'socket_root','tablespaces':'tablespace_root',
                'pgbackrest-lock':'lock_root','pgbackrest-spool':'spool_root','pgbackrest-includes':'include_root'}
        need(plan.get(keys[directory_name]) == str(path))
        native.directory(path,task_root=root,postgres_uid=account.pw_uid)
        need(not list(itertools.islice(path.iterdir(),1)))
    # A mount may cover an apparently empty directory. Check the complete mount
    # census without querying either the live database or its data directory.
    with open('/proc/self/mountinfo','rb') as stream: mounts = stream.read(2*1024**2+1)
    need(len(mounts) <= 2*1024**2)
    for line in mounts.decode('ascii').splitlines():
        fields = line.split(); need(len(fields) >= 6)
        mount = fields[4]
        need(mount != str(root) and not mount.startswith(str(root)+'/'))
    owner_state,child_state = _terminal_birth(owner),_terminal_birth(dispatch)
    execution.terminal(HISTORICAL_UNIT)
    unit_state = execution.show(HISTORICAL_UNIT)
    resources = []
    for name in ('repository.tar','downloaded-repository.tar'):
        path = input_root/name
        with bundle._directory(input_root) as directory_fd:
            meta = os.stat(name,dir_fd=directory_fd,follow_symlinks=False)
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                 and stat.S_IMODE(meta.st_mode) == 0o600 and meta.st_nlink == 1
                 and meta.st_size == download['archive_bytes'])
            with bundle._source(directory_fd,name,bundle._identity(meta)) as stream:
                need(bundle._hash(stream,meta.st_size) == HISTORICAL_ARCHIVE_SHA256)
        resources.append({'path':str(path),'bytes':meta.st_size,'sha256':HISTORICAL_ARCHIVE_SHA256})
    hashes = {key:_hash(raw) for key,raw in (
        ('ownership',raw_ownership),('intent',raw_intent),('info_intent',raw_phase),
        ('info_dispatch',raw_dispatch),('info_terminal',raw_terminal),
        ('outer_dispatch',raw_outer),('outer_ack',raw_ack),('download',download_raw))}
    return {'schema':'careertrellis.historical-info-preservation/v1',
        'transaction':HISTORICAL_TRANSACTION,'source_revision':HISTORICAL_SOURCE,
        'archive_sha256':HISTORICAL_ARCHIVE_SHA256,'generation_sha256':HISTORICAL_GENERATION_SHA256,
        'original_plan_sha256':HISTORICAL_PLAN_SHA256,'hashes':hashes,
        'owner':owner,'owner_terminal':owner_state,'info_child':dispatch,'info_child_terminal':child_state,
        'unit':HISTORICAL_UNIT,'unit_state':unit_state,'data_empty':True,'socket_empty':True,
        'no_restore_dispatch':True,'no_mounts':True,'resources':resources,
        'held_local_bytes':sum(item['bytes'] for item in resources),
        'held_remote_bytes':download['archive_bytes'],'provider_calls':0,
        'backup_accepted':False,'retry_authorized':False,'cleanup_authorized':False,
        'observed_at':dt.datetime.now(dt.timezone.utc).isoformat()}


def collect_historical_info_preservation(lock_fds):
    """One explicit durable preservation write; no retry/cleanup permission."""
    from . import entry, native_restore
    need(os.name == 'posix' and os.geteuid() == os.getegid() == 0
         and socket.gethostname() == 'job-applier-db-staging-01'
         and Path(__file__).absolute() == entry.ROOT/'preservation.py')
    entry.verify_install()
    need(type(lock_fds) is dict and set(lock_fds) == set(HISTORICAL_LOCKS))
    for path in HISTORICAL_LOCKS: require_owned_lock(path,lock_fds[path])
    value = _historical_state()
    for path in HISTORICAL_LOCKS: require_owned_lock(path,lock_fds[path])
    native_restore.exclusive(HISTORICAL_RECEIPT,
                             (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode('ascii'))
    return {'result':'historical_info_failure_preserved','receipt_sha256':_hash(
                native_restore.protected(HISTORICAL_RECEIPT,maximum=262144)),
            'backup_accepted':False,'retry_authorized':False,'cleanup_authorized':False,'provider_calls':0}


def read_historical_info_preservation():
    """Freshly revalidate immutable originals; never refresh or overwrite receipt."""
    raw,saved = _historical_read(HISTORICAL_RECEIPT,262144)
    fresh = _historical_state()
    compared = ('schema','transaction','source_revision','archive_sha256','generation_sha256',
                'original_plan_sha256','hashes','owner','info_child','resources',
                'held_local_bytes','held_remote_bytes','backup_accepted','retry_authorized','cleanup_authorized')
    need(all(saved.get(key) == fresh[key] for key in compared))
    return {'receipt_sha256':_hash(raw),'fresh_state':fresh,'growth_authorized':False,
            'backup_accepted':False,'retry_authorized':False,'cleanup_authorized':False}


class InheritedObjectReservation:
    __slots__ = ('parent','release','backup_label','maintenance_fd','recovery_fd','identity','_seal')
    def __init__(self,parent,release,label,maintenance_fd,recovery_fd,identity,seal=None):
        need(seal is _INHERITED_SEAL)
        self.parent,self.release,self.backup_label = parent,release,label
        self.maintenance_fd,self.recovery_fd,self.identity,self._seal = maintenance_fd,recovery_fd,identity,seal
    @property
    def authenticated(self): return self._seal is _INHERITED_SEAL


def _root_process(pid):
    from . import native_restore
    identity = native_restore.process_identity(pid)
    raw = (Path('/proc')/str(pid)/'status').read_bytes(); need(len(raw) <= 65536)
    fields = [line.split() for line in raw.splitlines() if line.startswith(b'Uid:')]
    need(len(fields) == 1 and fields[0][1:] == [b'0']*4)
    return identity


def _kernel_lock_line(path):
    meta = Path(path).lstat(); expected = (os.major(meta.st_dev),os.minor(meta.st_dev),meta.st_ino)
    with open('/proc/locks','rb') as stream: raw = stream.read(2*1024**2+1)
    need(len(raw) <= 2*1024**2)
    rows = []
    for line in raw.decode('ascii').splitlines():
        fields = line.split()
        if '->' in fields: continue
        for index,field in enumerate(fields):
            match = re.fullmatch('([0-9a-f]+):([0-9a-f]+):([0-9]+)',field)
            if match is None: continue
            key = tuple(int(value,16 if part < 2 else 10) for part,value in enumerate(match.groups()))
            if key == expected:
                need(index > 0 and 'FLOCK' in fields and 'WRITE' in fields
                     and fields[index-1].isdigit())
                rows.append((int(fields[index-1]),line))
    need(len(rows) == 1); return rows[0]


def fdinfo_lock(raw,device,inode):
    """Strict Linux fdinfo FLOCK/OFD witness; missing or ambiguous is unknown."""
    need(type(raw) is bytes and 0 < len(raw) <= 65536)
    rows = [line.partition(b':')[2].split() for line in raw.splitlines() if line.startswith(b'lock:')]
    need(len(rows) == 1)
    fields = rows[0]
    need(len(fields) == 8 and re.fullmatch(rb'[0-9]+:',fields[0])
         and fields[1:4] == [b'FLOCK',b'ADVISORY',b'WRITE']
         and re.fullmatch(rb'[0-9]+',fields[4]) and fields[6:] == [b'0',b'EOF'])
    match = re.fullmatch(rb'([0-9a-f]+):([0-9a-f]+):([0-9]+)',fields[5])
    need(match is not None and tuple(int(value,16 if part < 2 else 10)
        for part,value in enumerate(match.groups())) == (os.major(device),os.minor(device),inode))
    return tuple(field.decode('ascii') for field in fields[1:])


def prove_same_description(path,fd,parent_pid):
    """Kernel mechanism only; does not authenticate a path, owner or invocation."""
    import fcntl
    need(os.name == 'posix' and type(fd) is int and fd >= 0 and type(parent_pid) is int and parent_pid > 1)
    target = Path(path); meta = target.lstat(); opened = os.fstat(fd)
    need(stat.S_ISREG(meta.st_mode) and (meta.st_dev,meta.st_ino) == (opened.st_dev,opened.st_ino))
    parent_fd = os.stat(Path('/proc')/str(parent_pid)/'fd'/str(fd))
    need((parent_fd.st_dev,parent_fd.st_ino) == (meta.st_dev,meta.st_ino))
    owner,before = _kernel_lock_line(path)
    def witness(pid):
        raw = (Path('/proc')/str(pid)/'fdinfo'/str(fd)).read_bytes()
        return fdinfo_lock(raw,meta.st_dev,meta.st_ino)
    parent_witness = witness(parent_pid)
    need(witness(os.getpid()) == parent_witness)
    # The util-linux flock child that created the lock may already have exited.
    # The live operational owner is the verified shell parent holding this OFD.
    # fdinfo proves both descriptors refer to that locked OFD; a separate open
    # must still block. Never infer inheritance merely from the shared inode.
    independent = os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        fresh = os.fstat(independent)
        need((fresh.st_dev,fresh.st_ino) == (meta.st_dev,meta.st_ino))
        try: fcntl.flock(independent,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: raise PreservationRefused('inherited_open_description_not_proved')
    finally: os.close(independent)
    # Re-locking an inherited descriptor is reentrant. A separately opened FD
    # cannot acquire this held flock. Never LOCK_UN: that would release the
    # parent's actual lock and destroy the reservation.
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    need(_kernel_lock_line(path) == (owner,before)
         and witness(parent_pid) == witness(os.getpid()) == parent_witness)
    now = target.lstat()
    need((now.st_dev,now.st_ino) == (meta.st_dev,meta.st_ino))
    return True


def _inherited_lock(path,fd,parent):
    """Fixed path + verified live operational parent + same-OFD kernel proof."""
    need(socket.gethostname() == 'careertrellis-staging-web-1')
    expected_modes = {'/run/lock/careertrellis-maintenance.lock':0o644,
                      '/run/lock/careertrellis-generated-object-recovery.lock':0o600}
    need(path in expected_modes and type(fd) is int and fd in (8,9,10))
    target = Path(path); meta = target.lstat()
    for ancestor in target.parents:
        parent_meta = ancestor.lstat()
        need(stat.S_ISDIR(parent_meta.st_mode) and parent_meta.st_uid == 0)
        need(stat.S_IMODE(parent_meta.st_mode) == 0o1777 if ancestor == Path('/run/lock')
             else not parent_meta.st_mode & 0o022)
    need(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0 and meta.st_nlink == 1
         and meta.st_size == 0 and stat.S_IMODE(meta.st_mode) == expected_modes[path]
         and _root_process(parent['pid']) == parent)
    need(prove_same_description(path,fd,parent['pid']) is True
         and _root_process(parent['pid']) == parent)
    now = target.lstat(); need((now.st_dev,now.st_ino) == (meta.st_dev,meta.st_ino))
    return {'device':meta.st_dev,'inode':meta.st_ino,'owner_pid':os.getpid()}


def inherited_object_reservation(release,backup_label,maintenance_fd=8,recovery_fd=9):
    """Fixed native object-helper parent, source pin, invocation and both FDs."""
    from . import baseline, entry, hydrate, native_restore
    need(os.name == 'posix' and os.geteuid() == os.getegid() == 0
         and socket.gethostname() == 'careertrellis-staging-web-1'
         and Path(__file__).absolute() == entry.ROOT/'preservation.py'
         and type(release) is str and re.fullmatch('[0-9a-f]{40}',release)
         and type(backup_label) is str and re.fullmatch('[0-9]{8}-[0-9]{6}F',backup_label)
         and maintenance_fd in (8,10) and recovery_fd == 9)
    context = baseline.native_context(); need(context.authenticated and context.role == 'web')
    from paired_maintenance import entry as paired_entry,runtime
    paired_entry.verify_install()
    need(Path(runtime.__file__).absolute() == entry.PAIRED_ROOT/'runtime.py')
    helper = '/usr/local/sbin/careertrellis-mirror-generated-objects'
    need(hashlib.sha256(hydrate.protected(helper,0o755)[0]).hexdigest() == runtime.PINS['web'][helper])
    parent = _root_process(os.getppid())
    exe = os.readlink(Path('/proc')/str(parent['pid'])/'exe')
    need(exe in ('/usr/bin/bash','/bin/bash'))
    executable = Path(exe).resolve(); meta = executable.lstat()
    need(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022)
    raw = (Path('/proc')/str(parent['pid'])/'cmdline').read_bytes(); need(len(raw) <= 32768)
    args = raw.split(b'\0'); need(args[-1] == b''); args.pop()
    need(len(args) >= 2 and args[1].decode('ascii') == helper)
    invocation = [part.decode('ascii') for part in args[2:]]
    expected = ['/opt/careertrellis/staging/releases/'+release,backup_label,*runtime.ACKS]
    need(invocation == expected or invocation == ['publish-captured',*expected])
    first = _inherited_lock('/run/lock/careertrellis-maintenance.lock',maintenance_fd,parent)
    _inherited_lock('/run/lock/careertrellis-generated-object-recovery.lock',recovery_fd,parent)
    need(_root_process(parent['pid']) == parent)
    return InheritedObjectReservation(parent,release,backup_label,maintenance_fd,recovery_fd,first,_INHERITED_SEAL)
