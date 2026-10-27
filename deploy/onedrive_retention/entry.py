"""Stdlib-only installed-byte bootstrap and explicitly fenced native entry.

No package code is imported until the exact installed namespace is verified.
Native baseline, persistent-idle, observation and dispatch integration remain
unavailable. The injectable routing contract is OFFLINE ONLY and its results
cannot establish native acceptance or authorize catalog retirement.
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
FILES = frozenset(('baseline.py', 'builder.py', 'bundle.py', 'capture_plan.py', 'catalog.py', 'collector.py', 'common_io.py',
    'coordinator.py', 'endpoint.py', 'entry.py', 'guard.py', 'hydrate.py',
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
STAGES = ('hydrate', 'publish', 'restore', 'retire')


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


def parse_command(argv):
    need(type(argv) is list and all(type(item) is str for item in argv))
    if argv == ['verify-install']:
        return {'operation':'verify-install', 'role':'read-only', 'stage':None, 'transaction':None, 'attempt':None}
    if len(argv) == 4 and argv[0] == 'db-stage' and argv[1] in STAGES and argv[2] == '--transaction':
        need(TX.fullmatch(argv[3]))
        return {'operation':'db-stage','role':'db','stage':argv[1],'transaction':argv[3],'attempt':1}
    if len(argv) == 3 and argv[0] == 'web-run' and argv[1] == '--transaction':
        need(TX.fullmatch(argv[2]))
        return {'operation':'web-run','role':'web','stage':None,'transaction':argv[2],'attempt':1}
    if len(argv) == 5 and argv[0] == 'collect-native' and argv[1] == '--transaction' and argv[3] == '--attempt':
        need(TX.fullmatch(argv[2]) and re.fullmatch('[1-9]',argv[4]))
        return {'operation':'collect-native','role':'db','stage':'restore','transaction':argv[2],'attempt':int(argv[4])}
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
    """Only an explicitly OFFLINE injected adapter can exercise dispatch today."""
    command = parse_command(argv)
    if backend is None:
        revision = verify_install()
        if command['operation'] == 'verify-install':
            return {'schema':'careertrellis.retention-source-verification/v1','revision':revision,
                    'result':'source_verified','native_actions':False,'native_restore_accepted':False}
        _native_dependencies(revision, command['role'])
        unavailable()
    need(getattr(backend,'mode',None) == 'offline' and command['operation'] != 'verify-install')
    validate_bindings(backend.bindings(), backend.now())
    backend.dispatch(dict(command))
    return {'schema':'careertrellis.retention-entry-offline/v1','result':'offline_contract_exercised',
            'execution_mode':'offline','native_actions':False,'native_restore_accepted':False,'command':command}


def retention_idle(transaction, stage):
    need(type(transaction) is str and TX.fullmatch(transaction) and stage in STAGES)
    unavailable()


def observe_retention_stage(transaction, stage, policy_sha256):
    need(type(transaction) is str and TX.fullmatch(transaction) and stage in STAGES
         and type(policy_sha256) is str and HEX.fullmatch(policy_sha256))
    unavailable()


def dispatch_native_with_collector(transaction):
    need(type(transaction) is str and TX.fullmatch(transaction))
    unavailable()


def identity_reverification_idle(transaction, verification):
    need(type(transaction) is str and TX.fullmatch(transaction)
         and type(verification) is int and 1 <= verification <= 99)
    unavailable()


def main(argv=None):
    try:
        result = route(list(sys.argv[1:] if argv is None else argv))
        print(json.dumps(result,sort_keys=True)); return 0
    except BaseException:
        print('{"result":"refused","category":"retention_entry_native_dependencies_unavailable","native_actions":false}')
        return 1


if __name__ == '__main__': raise SystemExit(main())
