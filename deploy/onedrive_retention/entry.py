"""Stdlib-only installed-byte bootstrap and explicitly fenced native entry.

No package code is imported until the exact installed namespace is verified.
Native execution requires the protected accepted baseline. The injectable
routing contract is OFFLINE ONLY and cannot establish native acceptance.
"""
import datetime as dt
import hashlib
import importlib
import importlib.machinery
import itertools
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import types

ROOT = Path('/usr/local/libexec/careertrellis/onedrive_retention')
PAIRED_ROOT = Path('/usr/local/libexec/careertrellis/paired_maintenance')
SCHEMA = 'careertrellis.onedrive-retention-install/v1'
FILES = frozenset(('activation.py', 'activation_readiness.py', 'activation_actor.py', 'activation_controller.py',
    'activation_transition.py', 'activation_finalize.py', 'activation_web.py', 'pre_growth_native.py', 'expiry_capability.py',
    'baseline.py', 'builder.py', 'bundle.py', 'capture_plan.py', 'catalog.py', 'collector.py', 'common_io.py',
    'coordinator.py', 'endpoint.py', 'entry.py', 'execution.py', 'guard.py', 'health.py', 'hydrate.py',
    'identity_collect.py', 'local_restore_plan.py', 'local_restore.py',
    'native_restore.py', 'policy.py', 'publish.py', 'retire.py', 'upgrade_493.py', 'graph_delete.py', 'preservation.py'))
PAIRED_FILES = frozenset(('__init__.py', 'common.py', 'runtime.py', 'provider.py',
    'client.py', 'ssh_gate.py', 'db_control.py', 'web_control.py', 'db_host.py', 'host.py', 'entry.py'))
MAX_FILE = 2 * 1024**2
MAX_MANIFEST = 65536
MAX_TOTAL = 64 * 1024**2
HEX = re.compile(r'[0-9a-f]{64}\Z')
REVISION = re.compile(r'[0-9a-f]{40}\Z')
TX = re.compile(r'[0-9a-f]{32}\Z')
STAGES = ('hydrate', 'publish', 'restore', 'prepare', 'retire')


class EntryRefused(ValueError):
    pass


def need(value):
    if not value: raise EntryRefused('retention_entry_refused')


def unavailable():
    raise EntryRefused('retention_native_integration_unavailable')


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def decode(raw, limit):
    need(type(raw) is bytes and len(raw) <= limit)
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value); value[key] = item
        return value
    try:
        value = json.loads(raw.decode('ascii'), object_pairs_hook=pairs,
            parse_constant=lambda _: need(False))
        need(canonical(value) == raw)
        return value
    except (UnicodeError, ValueError, TypeError):
        raise EntryRefused('retention_entry_refused') from None


def identity(meta):
    return (meta.st_dev, meta.st_ino, meta.st_mode, meta.st_uid, meta.st_gid,
            meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


def file_metadata(meta, limit):
    need(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0
         and stat.S_IMODE(meta.st_mode) == 0o644 and meta.st_nlink == 1
         and 0 <= meta.st_size <= limit)


def read_source(reader, name, limit):
    before = reader.metadata(name); file_metadata(before, limit)
    raw, opened = reader.read(name, limit)
    after = reader.metadata(name)
    need(type(raw) is bytes and len(raw) == before.st_size
         and identity(before) == identity(opened) == identity(after))
    return raw


def verify_package(reader, files, schema):
    """Internal metadata/hash check; a supplied reader is NOT authentication."""
    parents = reader.parents()
    need(parents)
    parent_identities = [identity(meta) for meta in parents]
    for index, meta in enumerate(parents):
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0
             and not meta.st_mode & 0o022)
        if index == 0: need(stat.S_IMODE(meta.st_mode) == 0o755)
    expected = files | {'manifest.json'}
    names = reader.names()
    need(type(names) is list and len(names) == len(expected) and set(names) == expected)
    manifest_raw = read_source(reader, 'manifest.json', MAX_MANIFEST)
    manifest = decode(manifest_raw, MAX_MANIFEST)
    need(type(manifest) is dict and set(manifest) == {'schema', 'revision', 'files'}
         and manifest['schema'] == schema and type(manifest['revision']) is str
         and REVISION.fullmatch(manifest['revision']) and manifest['revision'] != '0'*40
         and type(manifest['files']) is dict and set(manifest['files']) == files)
    total = len(manifest_raw)
    for name in sorted(files):
        digest = manifest['files'][name]
        need(type(digest) is str and HEX.fullmatch(digest))
        raw = read_source(reader, name, MAX_FILE); total += len(raw)
        need(total <= MAX_TOTAL and hashlib.sha256(raw).hexdigest() == digest)
    need(read_source(reader, 'manifest.json', MAX_MANIFEST) == manifest_raw
         and reader.names() == names
         and [identity(meta) for meta in reader.parents()] == parent_identities)
    return manifest


class _InstalledPackage:
    root = ROOT
    def __enter__(self):
        need(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'))
        for parent in (self.root, *self.root.parents):
            meta = parent.lstat()
            need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                 and not meta.st_mode & 0o022)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            need(identity(os.fstat(self.fd)) == identity(self.root.lstat()))
        except BaseException:
            os.close(self.fd); raise
        return self
    def __exit__(self, *_): os.close(self.fd)
    def parents(self):
        need(identity(os.fstat(self.fd)) == identity(self.root.lstat()))
        return [parent.lstat() for parent in (self.root, *self.root.parents)]
    def metadata(self, name): return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
    def names(self):
        with os.scandir(self.fd) as entries:
            return sorted(item.name for item in itertools.islice(entries, 64))
    def read(self, name, limit):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            before = os.fstat(fd); file_metadata(before, limit)
            raw = bytearray()
            while len(raw) <= limit:
                block = os.read(fd, min(65536, limit+1-len(raw)))
                if not block: break
                raw.extend(block)
            need(len(raw) <= limit and identity(os.fstat(fd)) == identity(before))
            return bytes(raw), before
        finally: os.close(fd)


class _PairedPackage(_InstalledPackage):
    root = PAIRED_ROOT


def verify_fixture(reader):
    """Unauthenticated OFFLINE fixture verification; never native acceptance."""
    try:
        value = verify_package(reader, FILES, SCHEMA)
        return {'schema': 'careertrellis.retention-install-fixture/v1', 'revision': value['revision'],
                'authenticated': False, 'native_actions': False}
    except Exception:
        raise EntryRefused('retention_entry_refused') from None


def verify_install():
    """Verify only the fixed protected installed source; returns its revision."""
    try:
        need(Path(__file__).absolute() == ROOT / 'entry.py')
        with _InstalledPackage() as reader:
            return verify_package(reader, FILES, SCHEMA)['revision']
    except Exception:
        raise EntryRefused('retention_entry_refused') from None


def _load(name):
    """A fixed namespace prevents import fallback to an unrelated package."""
    need(name+'.py' in FILES); verify_install()
    sys.dont_write_bytecode = True
    package = sys.modules.get('onedrive_retention')
    if package is None:
        package = types.ModuleType('onedrive_retention')
        package.__package__ = 'onedrive_retention'; package.__path__ = [str(ROOT)]
        package.__spec__ = importlib.machinery.ModuleSpec('onedrive_retention', None, is_package=True)
        package.__spec__.submodule_search_locations = [str(ROOT)]
        sys.modules['onedrive_retention'] = package
    need(list(package.__path__) == [str(ROOT)])
    module = importlib.import_module('onedrive_retention.'+name)
    need(Path(module.__file__).absolute() == ROOT / (name+'.py'))
    return module


def _paired_namespace():
    """Create only the exact already-verified paired namespace under -I."""
    with _PairedPackage() as reader:
        verify_package(reader, PAIRED_FILES, 'careertrellis.paired-maintenance-install/v1')
    package = sys.modules.get('paired_maintenance')
    if package is None:
        package = types.ModuleType('paired_maintenance')
        package.__package__ = 'paired_maintenance'; package.__path__ = [str(PAIRED_ROOT)]
        package.__spec__ = importlib.machinery.ModuleSpec('paired_maintenance',None,is_package=True)
        package.__spec__.submodule_search_locations = [str(PAIRED_ROOT)]
        sys.modules['paired_maintenance'] = package
    need(list(package.__path__) == [str(PAIRED_ROOT)])
    return package


def _protected_document(path, mode):
    for parent in path.parents:
        meta = parent.lstat()
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0 and not meta.st_mode & 0o022)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
             and stat.S_IMODE(before.st_mode) == mode and before.st_nlink == 1
             and before.st_size <= MAX_MANIFEST)
        raw = os.read(fd, MAX_MANIFEST+1)
        need(len(raw) == before.st_size and identity(os.fstat(fd)) == identity(before)
             and identity(path.lstat()) == identity(before))
        return raw, decode(raw, MAX_MANIFEST)
    finally: os.close(fd)


def _native_dependencies(revision, role):
    need(os.name == 'posix' and os.geteuid() == os.getegid() == 0
         and socket.gethostname() == {'web':'careertrellis-staging-web-1','db':'job-applier-db-staging-01'}[role])
    coordinator = _load('coordinator')
    private, document = _protected_document(Path('/etc/careertrellis/onedrive-retention/policy.json'), 0o600)
    public, _ = _protected_document(Path('/var/lib/careertrellis/onedrive-retention-public/policy.json'), 0o644)
    need(private == public)
    policy = coordinator.Policy(document)
    need(policy.data['artifact_revision'] == revision)
    policy.require_active(dt.datetime.now(dt.timezone.utc))
    pair_raw, paired_policy = _protected_document(Path('/etc/careertrellis/paired-maintenance/policy.json'), 0o600)
    pair_public, _ = _protected_document(Path('/var/lib/careertrellis/paired-maintenance/public-policy.json'), 0o644)
    with _PairedPackage() as reader:
        paired_manifest = verify_package(reader, PAIRED_FILES, 'careertrellis.paired-maintenance-install/v1')
    _paired_namespace()
    need(pair_raw == pair_public and hashlib.sha256(pair_raw).hexdigest() == policy.data['paired_policy_sha256']
         and paired_policy.get('manifest_revision') == paired_manifest['revision']
         and paired_policy.get('application_release') == policy.data['application_release']
         and paired_policy.get('enabled') is True)
    baseline = _load('baseline')
    context = baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated is True
         and context.revision == revision and context.role == role
         and context.document['application_release'] == policy.data['application_release']
         and context.document['paired_policy_sha256'] == policy.data['paired_policy_sha256'])
    return context


def verify_prepared_dependencies(revision):
    """Read-only local dependencies before accepted-baseline publication.

    Unlike operational dispatch this needs no accepted baseline. It verifies
    the fixed installed packages/policies and all role-specific helper and
    unit pins, and cannot call health, a provider, a worker or a repository.
    """
    need(os.name=='posix' and os.geteuid()==os.getegid()==0)
    role={'careertrellis-staging-web-1':'web','job-applier-db-staging-01':'db'}.get(socket.gethostname())
    need(role in ('web','db') and verify_install()==revision)
    _paired_namespace()
    paired_entry=importlib.import_module('paired_maintenance.entry')
    runtime=importlib.import_module('paired_maintenance.runtime')
    need(paired_entry.verify_install()==revision)
    paired=paired_entry.load_policy()
    private,document=_protected_document(Path('/etc/careertrellis/onedrive-retention/policy.json'),0o600)
    public,_=_protected_document(Path('/var/lib/careertrellis/onedrive-retention-public/policy.json'),0o644)
    one=_load('coordinator').Policy(document)
    need(private==public and one.data['artifact_revision']==revision
         and one.data['paired_policy_sha256']==paired.digest
         and one.data['application_release']==paired.data['application_release'])
    one.require_active(dt.datetime.now(dt.timezone.utc))
    runtime.preflight_runtime(paired,role).verify_sources()
    need(verify_install()==revision and paired_entry.load_policy().digest==paired.digest
         and _protected_document(Path('/etc/careertrellis/onedrive-retention/policy.json'),0o600)[0]==private)
    return {'schema':'careertrellis.retention-prepared-dependencies/v1','result':'dependencies_verified',
        'revision':revision,'role':role,'native_actions':False,'provider_calls':0,
        'application_services_restarted':False,'native_restore_accepted':False}


WEB_ACTIVATION_COMMANDS={'activation-web-dispatch':'dispatch','activation-web-run':'run',
    'activation-web-status':'status','activation-web-verify':'verify_terminal'}


def parse_command(argv):
    need(type(argv) is list and all(type(item) is str for item in argv))
    if argv == ['verify-install']:
        return {'operation':'verify-install', 'role':'read-only', 'stage':None, 'transaction':None, 'attempt':None}
    if argv == ['verify-prepared-install']:
        return {'operation':'verify-prepared-install','role':'read-only','stage':None,'transaction':None,'attempt':None}
    if argv == ['preserve-info-only']:
        return {'operation':'preserve-info-only','role':'db','stage':None,'transaction':None,'attempt':None}
    if argv == ['scheduled-run']:
        return {'operation':'scheduled-run','role':'web','stage':None,'transaction':None,'attempt':None}
    if argv == ['legacy-pg-routing']:
        return {'operation':'legacy-pg-routing','role':'db','stage':None,'transaction':None,'attempt':None}
    if argv == ['activation-evidence-db']:
        return {'operation':'activation-evidence-db','role':'db','stage':None,'transaction':None,'attempt':None}
    if argv == ['monitor-mode']:
        return {'operation':'monitor-mode','role':'db','stage':None,'transaction':None,'attempt':None}
    if argv in (['evaluate-core'],['evaluate-core','--bundle-unavailable']):
        return {'operation':'evaluate-core','role':'db','stage':None,'transaction':None,'attempt':None,
                'bundle_unavailable':len(argv)==2}
    if len(argv) == 9 and argv[0] == 'legacy-object-guard' and argv[1] == '--release' \
            and argv[3] == '--backup-label' and argv[5] == '--maintenance-fd' and argv[7] == '--recovery-fd':
        need(REVISION.fullmatch(argv[2]) and re.fullmatch('[0-9]{8}-[0-9]{6}F',argv[4])
             and argv[6] in ('8','10') and argv[8] == '9')
        return {'operation':'legacy-object-guard','role':'web','stage':None,'transaction':None,'attempt':None,
                'release':argv[2],'backup_label':argv[4],'maintenance_fd':int(argv[6]),'recovery_fd':9}
    if len(argv) == 1 and argv[0] in ('health-web','health-db','schedule-check'):
        return {'operation':argv[0],'role':'web' if argv[0]=='health-web' else 'db',
                'stage':None,'transaction':None,'attempt':None}
    if len(argv) == 4 and argv[0] == 'db-stage' and argv[1] in STAGES and argv[2] == '--transaction':
        need(TX.fullmatch(argv[3]))
        return {'operation':'db-stage','role':'db','stage':argv[1],'transaction':argv[3],'attempt':1}
    if len(argv) == 3 and argv[0] == 'web-run' and argv[1] == '--transaction':
        need(TX.fullmatch(argv[2]))
        return {'operation':'web-run','role':'web','stage':None,'transaction':argv[2],'attempt':1}
    if len(argv)==3 and argv[0]=='activation-actor' and argv[1]=='--transaction':
        need(TX.fullmatch(argv[2]) and argv[2]!='0'*32)
        return {'operation':'activation-actor','role':'db','stage':None,'transaction':argv[2],'attempt':None}
    if len(argv)==3 and argv[0] in WEB_ACTIVATION_COMMANDS and argv[1]=='--transaction':
        need(TX.fullmatch(argv[2]) and argv[2]!='0'*32)
        return {'operation':argv[0],'role':'web','stage':None,'transaction':argv[2],'attempt':None}
    if len(argv)==3 and argv[0] in ('pre-growth-owner','pre-growth-watch') and argv[1]=='--transaction':
        need(TX.fullmatch(argv[2]) and argv[2]!='0'*32)
        return {'operation':argv[0],'role':'db','stage':None,'transaction':argv[2],'attempt':None}
    if (len(argv)==7 and argv[0]=='pre-growth-identity' and argv[1]=='--transaction'
            and argv[3]=='--source-transaction' and argv[5]=='--verification'):
        need(TX.fullmatch(argv[2]) and argv[2]!='0'*32 and TX.fullmatch(argv[4]) and argv[4]!='0'*32
            and re.fullmatch('[1-9][0-9]?',argv[6]))
        return {'operation':argv[0],'role':'db','stage':None,'transaction':argv[2],
            'source_transaction':argv[4],'attempt':int(argv[6])}
    if (len(argv) == 5 and argv[0] == 'web-run' and argv[1] == '--transaction'
            and argv[3] == '--expected-backup-label'):
        need(TX.fullmatch(argv[2]) and re.fullmatch(r'[0-9]{8}-[0-9]{6}F(?:_[0-9]{8}-[0-9]{6}[DI])?',argv[4]))
        return {'operation':'web-run','role':'web','stage':None,'transaction':argv[2],'attempt':1,
                'expected_backup_label':argv[4]}
    if len(argv) == 5 and argv[0] == 'collect-native' and argv[1] == '--transaction' and argv[3] == '--attempt':
        need(TX.fullmatch(argv[2]) and re.fullmatch('[1-9]',argv[4]))
        return {'operation':'collect-native','role':'db','stage':'restore','transaction':argv[2],'attempt':int(argv[4])}
    if len(argv) == 4 and argv[0] == 'collect-stage' and argv[1] in ('hydrate','publish','prepare','retire') and argv[2] == '--transaction':
        need(TX.fullmatch(argv[3]))
        return {'operation':'collect-stage','role':'db','stage':argv[1],'transaction':argv[3],'attempt':1}
    if len(argv) == 5 and argv[0] == 'collect-identity' and argv[1] == '--transaction' and argv[3] == '--verification':
        need(TX.fullmatch(argv[2]) and re.fullmatch('[1-9][0-9]?',argv[4]))
        return {'operation':'collect-identity','role':'db','stage':'retire','transaction':argv[2],'attempt':int(argv[4])}
    raise EntryRefused('retention_entry_refused')


def validate_bindings(value, now):
    """Typed OFFLINE binding contract, not authentication of supplied facts."""
    need(type(value) is dict and set(value) == {'schema','install_revision','policy_revision',
        'application_release','policy_application_release','paired_application_release',
        'paired_policy_sha256','policy_paired_policy_sha256','baseline_authenticated','policy_enabled','valid_until'})
    need(value['schema'] == 'careertrellis.retention-entry-bindings/v1'
         and type(value['install_revision']) is str and REVISION.fullmatch(value['install_revision'])
         and value['install_revision'] != '0'*40 and value['policy_revision'] == value['install_revision']
         and type(value['application_release']) is str and REVISION.fullmatch(value['application_release'])
         and value['application_release'] == value['policy_application_release'] == value['paired_application_release']
         and type(value['paired_policy_sha256']) is str and HEX.fullmatch(value['paired_policy_sha256'])
         and value['policy_paired_policy_sha256'] == value['paired_policy_sha256']
         and value['baseline_authenticated'] is True and value['policy_enabled'] is True)
    try:
        expiry = dt.datetime.fromisoformat(value['valid_until'].replace('Z','+00:00'))
        need(expiry.utcoffset() == now.utcoffset() == dt.timedelta(0)
             and now < expiry <= dt.datetime(2027,1,24,tzinfo=dt.timezone.utc))
    except (ValueError,TypeError,AttributeError): raise EntryRefused('retention_entry_refused') from None


def route(argv, backend=None):
    """Authenticate native installation before loading any execution module."""
    command = parse_command(argv)
    if backend is None:
        revision = verify_install()
        if command['operation'] == 'verify-install':
            return {'schema':'careertrellis.retention-source-verification/v1','revision':revision,
                    'result':'source_verified','native_actions':False,'native_restore_accepted':False}
        if command['operation'] == 'verify-prepared-install':return verify_prepared_dependencies(revision)
        if command['operation'] == 'preserve-info-only':
            # This explicit metadata-preservation operation is available after
            # verified inactive source installation. It cannot authorize a
            # backup, retry or cleanup and does not need an enabled policy.
            owner=_load('hydrate').Native()
            with owner.locked():
                return _load('preservation').collect_historical_info_preservation(owner.canonical_fds)
        if command['operation'] == 'monitor-mode':
            need(os.geteuid() == os.getegid() == 0 and socket.gethostname() == 'job-applier-db-staging-01')
            return _load('health').monitor_mode()
        if command['operation'] in ('legacy-object-guard','legacy-pg-routing'):
            need(os.geteuid() == os.getegid() == 0 and socket.gethostname() == {
                'web':'careertrellis-staging-web-1','db':'job-applier-db-staging-01'}[command['role']])
            selected = _load('activation').routing_state()
            if selected is None:
                return {'result':'legacy_strategy_unselected','native_actions':False,
                        'provider_writes':0,'deletion_authorized':False}
            if command['operation'] == 'legacy-pg-routing':
                return {'result':'legacy_pg_mirror_retired','native_actions':False,
                        'provider_writes':0,'deletion_authorized':False}
        _native_dependencies(revision, command['role'])
        if command['operation']=='activation-actor':
            # Parsing alone is not runtime promotion. The closed source
            # inventory must separately admit every reviewed native component.
            need({'activation_actor.py','activation_controller.py','activation_transition.py'}<=FILES)
            return _load('activation_actor').run(command['transaction'])
        if command['operation'] in WEB_ACTIVATION_COMMANDS:
            need('activation_web.py' in FILES)
            action=WEB_ACTIVATION_COMMANDS[command['operation']]
            return getattr(_load('activation_web'),action)(command['transaction'])
        if command['operation'] in ('pre-growth-owner','pre-growth-watch','pre-growth-identity'):
            need('pre_growth_native.py' in FILES)
        return _load('execution').run_command(command)
    need(getattr(backend,'mode',None) == 'offline' and command['operation'] != 'verify-install')
    validate_bindings(backend.bindings(), backend.now())
    backend.dispatch(dict(command))
    return {'schema':'careertrellis.retention-entry-offline/v1','result':'offline_contract_exercised',
            'execution_mode':'offline','native_actions':False,'native_restore_accepted':False,'command':command}


def retention_idle(transaction, stage):
    need(type(transaction) is str and TX.fullmatch(transaction) and stage in STAGES)
    return _load('execution').idle(transaction, stage)


def expiry_capability_idle(repository_fd):
    """Only the actual held canonical repository FD reaches native renewal."""
    need(type(repository_fd) is int and repository_fd>=0)
    os.fstat(repository_fd)
    _load('preservation').require_owned_lock('/run/lock/careertrellis-pgbackrest-repository.lock',repository_fd)
    return _load('execution').expiry_capability_idle(repository_fd)


def observe_retention_stage(transaction, stage, policy_sha256):
    need(type(transaction) is str and TX.fullmatch(transaction) and stage in STAGES
         and type(policy_sha256) is str and HEX.fullmatch(policy_sha256))
    return _load('execution').observe(transaction, stage, policy_sha256)


def dispatch_native_with_collector(transaction):
    need(type(transaction) is str and TX.fullmatch(transaction))
    return _load('execution').dispatch_restore(transaction)


def identity_reverification_idle(transaction, verification):
    need(type(transaction) is str and TX.fullmatch(transaction)
         and type(verification) is int and 1 <= verification <= 99)
    return _load('execution').idle(transaction, 'retire', identity_verification=verification)


def main(argv=None):
    try:
        arguments=list(sys.argv[1:] if argv is None else argv)
        result = route(arguments)
        print(json.dumps(result,sort_keys=True))
        if arguments == ['schedule-check']:return 0 if result.get('result') == 'ready' else 1
        if arguments == ['health-db']:return 0 if result.get('result') == 'ok' else 1
        if arguments == ['legacy-pg-routing']:
            return 0 if result.get('result') == 'legacy_strategy_unselected' else 1
        return 0
    except BaseException:
        print('{"result":"refused","category":"retention_entry_native_dependencies_unavailable","native_actions":false}')
        return 1


if __name__ == '__main__': raise SystemExit(main())
