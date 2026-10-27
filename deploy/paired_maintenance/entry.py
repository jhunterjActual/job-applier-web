"""Fixed host entry points, protected bootstrap and private/public policy binding."""
import datetime as dt
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys

INSTALL_ROOT = Path('/usr/local/libexec/careertrellis/paired_maintenance')
ROOT = Path('/var/lib/careertrellis/paired-maintenance')
PRIVATE_POLICY = Path('/etc/careertrellis/paired-maintenance/policy.json')
PUBLIC_POLICY = ROOT / 'public-policy.json'
PUBLIC_MARKER = ROOT / 'writer-interlock.json'
FILES = {'__init__.py', 'common.py', 'runtime.py', 'provider.py', 'client.py',
         'ssh_gate.py', 'db_control.py', 'web_control.py', 'db_host.py', 'host.py', 'entry.py'}
WEB_HOST = 'careertrellis-staging-web-1'
DB_HOST = 'job-applier-db-staging-01'
RESPONSE_SCHEMA = 'careertrellis.paired-maintenance-response/v1'


class EntryRefused(RuntimeError):
    pass


def _need(value):
    if not value:
        raise EntryRefused('entry_refused')


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result)
            result[key] = value
        return result
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
    _need(type(value) is dict)
    return value


def _protected_read(path, modes, maximum=131072):
    path = Path(path)
    for parent in path.parents:
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    before = os.lstat(path)
    _need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
          and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) in modes
          and 0 < before.st_size <= maximum)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                         | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        opened = os.fstat(descriptor)
        _need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino))
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.lstat(path)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                                 info.st_gid, info.st_nlink, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns)
        _need(identity(before) == identity(opened) == identity(after) and len(raw) == before.st_size)
        return raw
    finally:
        os.close(descriptor)


def verify_install():
    """Only stdlib code executes before installed package byte verification."""
    info = os.lstat(INSTALL_ROOT)
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
          and stat.S_IMODE(info.st_mode) == 0o755)
    manifest = _decode(_protected_read(INSTALL_ROOT / 'manifest.json', {0o644}))
    _need(set(manifest) == {'schema', 'revision', 'files'}
          and manifest['schema'] == 'careertrellis.paired-maintenance-install/v1'
          and isinstance(manifest['revision'], str)
          and re.fullmatch('[0-9a-f]{40}', manifest['revision'])
          and type(manifest['files']) is dict and set(manifest['files']) == FILES)
    for name in sorted(FILES):
        expected = manifest['files'][name]
        _need(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected))
        raw = _protected_read(INSTALL_ROOT / name, {0o644}, 2 * 1024**2)
        _need(hashlib.sha256(raw).hexdigest() == expected)
    return manifest['revision']


def _module(name):
    package = __package__ or 'paired_maintenance'
    return importlib.import_module(package + '.' + name)


def load_policy():
    common = _module('common')
    private = common.Policy(_decode(_protected_read(PRIVATE_POLICY, {0o600, 0o644})))
    public = common.Policy(_decode(_protected_read(PUBLIC_POLICY, {0o644})))
    _need(private.digest == public.digest)
    revision = private.data.get('manifest_revision')
    if revision is not None:
        _need(revision == verify_install())
    return private


def _boot():
    path = Path('/proc/sys/kernel/random/boot_id')
    for parent in path.parents:
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    info = os.lstat(path)
    _need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    with path.open('rb') as stream:
        raw = stream.read(128)
    value = raw.decode('ascii').strip()
    _need(re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value))
    return value


def _release():
    # The mutable pointer is read, never moved by this operational bundle.
    pointer = Path('/opt/careertrellis/staging/current')
    for parent in pointer.parents:
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    info = os.lstat(pointer)
    _need(stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0)
    current = pointer.resolve(strict=True)
    _need(current.parent == Path('/opt/careertrellis/staging/releases')
          and re.fullmatch('[0-9a-f]{40}', current.name))
    return current.name


def writer_gate():
    try:
        common = _module('common')
        policy = common.Policy(_decode(_protected_read(PUBLIC_POLICY, {0o644})))
        marker = _decode(_protected_read(PUBLIC_MARKER, {0o644}))
        return 0 if common.validate_gate(policy, marker, _boot(), _release()) else 1
    except Exception:
        return 1


class NoStore:
    def exists(self, name):
        return False

    def read(self, name):
        raise EntryRefused('entry_refused')

    def write_once(self, name, value):
        raise EntryRefused('entry_refused')


def _control_lock():
    import fcntl
    path = ROOT / 'db' / 'control.lock'
    for parent in path.parents:
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        _need(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
              and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _transaction_store(policy, request):
    common = _module('common')
    path = ROOT / 'db' / 'runs' / request['transaction']
    try:
        os.lstat(path)
    except FileNotFoundError:
        if request['operation'] in _module('db_control').READ_ONLY:
            return NoStore()
        _need(request['operation'] == 'begin')
        policy.require_new_start(dt.datetime.now(dt.timezone.utc))
        # Backend claims the weekly slot and rejects other active transactions
        # before dispatch; entry creates only the empty, bounded journal root.
        parent = path.parent
        info = os.lstat(parent)
        _need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
              and stat.S_IMODE(info.st_mode) == 0o700)
        os.mkdir(path, 0o700)
    return common.Journal(path)


def db_endpoint(raw):
    request = {'transaction': '0' * 32, 'policy_sha256': '0' * 64}
    common = _module('common')
    try:
        _need(os.geteuid() == 0 and socket.gethostname() == DB_HOST)
        request = _module('db_control').validate_request(_module('client').decode(raw, 16384))
        policy = load_policy()
        _need(request['policy_sha256'] == policy.digest)
        if 'release' in request['payload']:
            _need(request['payload']['release'] == policy.data['application_release'])
        if (request['operation'] in _module('db_control').RETENTION
                or request['operation'] in (_module('db_control').RETENTION_PREFLIGHT,
                                            _module('db_control').RETENTION_CAPACITY,
                                            _module('db_control').RETENTION_SCHEDULE)):
            # Authenticate both installed namespaces and both policy bindings
            # before loading operational retention code. Status takes no paired
            # control lock and never creates a transaction store.
            from onedrive_retention import entry as retention_entry
            revision = retention_entry.verify_install()
            retention_entry._native_dependencies(revision, 'db')
            endpoint = retention_entry._load('endpoint')
            coordinator = retention_entry._load('coordinator')
            retention_policy = coordinator.Policy(retention_entry._protected_document(
                coordinator.PRIVATE_POLICY, 0o600)[1])
            _need(retention_policy.data['paired_policy_sha256'] == policy.digest)
            if request['operation'] == _module('db_control').RETENTION_SCHEDULE:
                payload=retention_entry._load('health').store_schedule(request['payload']['witness'])
            elif request['operation'] in (_module('db_control').RETENTION_PREFLIGHT,
                                        _module('db_control').RETENTION_CAPACITY):
                _need(request['payload']['retention_policy_sha256'] == retention_policy.digest)
                # One-shot admission collection, never a progress request. It
                # owns the exact existing repository fence while inspecting
                # histories; a running repository operation makes it refuse.
                guard = retention_entry._load('guard')
                with _module('provider').repository_lock() as (fd, _identity):
                    payload = guard.native_history_snapshot(request['transaction'],
                        request['payload']['stage'], fd)
                    if request['operation'] == _module('db_control').RETENTION_CAPACITY:
                        collected = guard.collect_provider_facts(fd)
                        _need(collected.authenticated is True)
                        facts = collected.value
                        guard._namespace_owners(facts, fd)
                        payload = {'schema':'careertrellis.retention-capacity-preflight/v1',
                            'history':payload,'quota':facts['quota'],
                            'namespace_bytes':facts['namespace_bytes'],
                            'account_scope':facts['account_scope'],
                            'observed_at':facts['observed_at'],
                            'baseline_bindings':facts['baseline_bindings']}
            else:
                value = endpoint.from_paired_request(request)
                payload = endpoint.Endpoint(retention_policy, endpoint.NativeDB(retention_policy)).handle(value)
            _need(type(payload) is dict and len(common.canonical(payload)) <= 60000)
            return common.canonical({'schema': RESPONSE_SCHEMA, 'transaction': request['transaction'],
                                     'policy_sha256': request['policy_sha256'], 'payload': payload})
        descriptor = _control_lock()
        try:
            store = _transaction_store(policy, request)
            backend = _module('db_host').DBBackend(policy, request['transaction'], store)
            payload = _module('db_control').DBControl(policy, store, backend).handle(request)
            _need(type(payload) is dict and len(common.canonical(payload)) <= 60000)
        finally:
            os.close(descriptor)
    except Exception:
        payload = {'state': 'unknown', 'operator_required': True}
    return common.canonical({'schema': RESPONSE_SCHEMA, 'transaction': request['transaction'],
                             'policy_sha256': request['policy_sha256'], 'payload': payload})


def _inspect():
    role = 'web' if socket.gethostname() == WEB_HOST else 'db'
    value = _decode(_protected_read(ROOT / role / 'status.json', {0o600}))
    db_states = {'idle', 'starting', 'fenced', 'backup_running', 'pair_ready', 'sealed',
                 'released', 'abort_wait_backup', 'abort_ready', 'aborted', 'mirroring',
                 'health_verifying', 'complete', 'failed', 'operator_required', 'unknown'}
    if role == 'db' and 'state' in value:
        # Rich private DB state never leaves the host. Only these fixed fields
        # are inspectable; future internal fields are deliberately discarded.
        _need(isinstance(value['state'], str) and value['state'] in db_states)
        projected = {'schema': 'careertrellis.paired-maintenance-inspection/v1', 'state': value['state']}
        for key, pattern in (('transaction', '[0-9a-f]{32}'), ('boot_id',
                            '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')):
            if key in value:
                _need(isinstance(value[key], str) and re.fullmatch(pattern, value[key]))
                projected[key] = value[key]
        if 'operator_required' in value:
            _need(type(value['operator_required']) is bool)
            projected['operator_required'] = value['operator_required']
        return _module('common').canonical(projected)
    required = {'schema', 'policy_sha256', 'boot_id', 'release', 'phase', 'result',
                'observed_at', 'services_restored'}
    _need(set(value) in (required, required | {'transaction'})
          and value.get('schema') == 'careertrellis.paired-maintenance-status/v1')
    for key, length in (('policy_sha256', 64), ('release', 40)):
        _need(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{%d}' % length, value[key]))
    _need(isinstance(value['boot_id'], str) and re.fullmatch(
        '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value['boot_id']))
    _need(type(value['services_restored']) is bool)
    _need(value['result'] in {'ok', 'running', 'failed', 'operator_required', 'attention', 'blocked'})
    phases = {'idle', 'preflight', 'db_fenced', 'quiescing', 'backup', 'capture',
              'restoring', 'publishing', 'mirroring', 'verifying', 'complete',
              'aborting', 'recovery', 'failed', 'operator_required', 'blocked'}
    if role == 'db':
        phases |= db_states
    _need(isinstance(value['phase'], str) and value['phase'] in phases)
    if 'transaction' in value:
        _need(isinstance(value['transaction'], str) and re.fullmatch('[0-9a-f]{32}', value['transaction']))
    timestamp = value['observed_at']
    _need(isinstance(timestamp, str) and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', timestamp))
    dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    return _module('common').canonical(value)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ['writer-gate']:
        return writer_gate()
    try:
        if args == ['ssh-gate']:
            raw = sys.stdin.buffer.read(16385)
            sys.stdout.buffer.write(_module('ssh_gate').run(os.environ, raw))
            return 0
        _need(os.geteuid() == 0)
        if args == ['db-endpoint']:
            sys.stdout.buffer.write(db_endpoint(sys.stdin.buffer.read(16385)))
            return 0
        _need(socket.gethostname() in (WEB_HOST, DB_HOST))
        if args == ['inspect']:
            sys.stdout.buffer.write(_inspect())
            return 0
        policy = load_policy()
        if args == ['web-bootstrap']:
            _need(socket.gethostname() == WEB_HOST)
            _module('host').bootstrap_web(policy)
        elif args in (['web-run'], ['web-recover']):
            _need(socket.gethostname() == WEB_HOST)
            _module('host').run_web(policy, recovery_only=args == ['web-recover'])
        elif len(args) == 2 and args[0] in ('db-worker', 'db-recover'):
            _need(socket.gethostname() == DB_HOST and re.fullmatch('[0-9a-f]{32}', args[1]))
            _module('db_host').db_worker(policy, args[1], recovery_only=args[0] == 'db-recover')
        else:
            raise EntryRefused('entry_refused')
        return 0
    except Exception:
        # No exception text, host data or diagnostic output crosses this boundary.
        return 1


if __name__ == '__main__':
    try:
        verify_install()
        sys.path.insert(0, str(INSTALL_ROOT.parent))
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1) from None
