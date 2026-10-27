"""Fresh read-only identity witness for one immutable OneDrive repository bundle.

Never infer identity around the original historical download: this collector
stats the current object, downloads it into a new exclusive numbered root,
hashes those bytes and stats it again. No upload, deletion or overwrite exists.
Default execution refuses. Native execution additionally requires the installed
entry/manifest, a bounded native owner, existing fences and idle operations.
"""
from contextlib import contextmanager
import configparser
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import selectors
import socket
import stat
import subprocess
import time

from . import bundle, capture_plan, catalog, hydrate, common_io


ROOT = Path('/var/lib/careertrellis/onedrive-bundles')
NAMESPACE = 'onedrive:job-applier-staging/postgresql-bundles'
MAX_OUTPUT = 65536
MAX_ELAPSED_SECONDS = 900
PROFILE_FIELDS = ('LoadState', 'Type', 'Restart', 'RemainAfterExit', 'ActiveState', 'SubState',
    'MainPID', 'ControlPID', 'Job', 'KillMode', 'SendSIGKILL', 'RuntimeMaxUSec',
    'TimeoutStartUSec', 'TimeoutStopUSec', 'InvocationID', 'ControlGroup',
    'ExecCondition', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost')
LOCKS = ('/var/lib/careertrellis/paired-maintenance/db/control.lock',
         '/run/lock/careertrellis-maintenance.lock',
         '/run/lock/careertrellis-pgbackrest-repository.lock')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
       'PYTHONDONTWRITEBYTECODE': '1'}


class IdentityRefused(ValueError): pass


def need(value, code='bundle_identity_reverification_refused'):
    if not value: raise IdentityRefused(code)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def timestamp(value):
    try: return catalog.timestamp(value)
    except Exception: raise IdentityRefused('bundle_identity_reverification_refused') from None


def receipt(raw, transaction, now):
    value = catalog.decode(raw)
    catalog.keys(value, ('schema', 'result', 'transaction', 'backup_label', 'generation_sha256',
        'archive_sha256', 'archive_bytes', 'manifest_sha256', 'native_restore_accepted',
        'completed_at', 'destination', 'quota_after'))
    need(value['schema'] == 'careertrellis.onedrive-bundle-download/v1'
         and value['result'] == 'download_verified' and value['native_restore_accepted'] is False
         and value['transaction'] == transaction and catalog.hex_value(value['archive_sha256'])
         and catalog.hex_value(value['generation_sha256']) and catalog.hex_value(value['manifest_sha256'])
         and type(value['archive_bytes']) is int and 0 < value['archive_bytes'] <= bundle.MAX_BYTES
         and value['destination'] == NAMESPACE + '/' + value['archive_sha256'] + '/repository.tar'
         and timestamp(value['completed_at']) <= now)
    return value


def stat_identity(raw, expected_size):
    """Strict rclone lsjson --stat file projection. No ID fallback is accepted.

    The local environment has no rclone executable. Fixtures cover this required
    ID/Path/Name/Size/IsDir contract; native capability still needs actual proof.
    """
    need(type(raw) is bytes and 0 < len(raw) <= MAX_OUTPUT)
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value); value[key] = item
        return value
    try: value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: need(False))
    except IdentityRefused: raise
    except Exception: raise IdentityRefused('bundle_stat_identity_refused') from None
    need(type(value) is dict and value.get('IsDir') is False
         and value.get('Path') == value.get('Name') == 'repository.tar'
         and type(value.get('Size')) is int and value['Size'] == expected_size
         and type(value.get('ID')) is str and 0 < len(value['ID']) <= 512
         and all(33 <= ord(char) < 127 for char in value['ID']), 'bundle_stat_identity_refused')
    return {'object_id': value['ID'], 'size': value['Size']}


def validate_unit_profile(value, pid, unit):
    """Only a finite exec owner can perform provider reads; oneshot is refused."""
    need(type(value) is dict and set(value) == set(PROFILE_FIELDS)
         and type(pid) is int and pid > 1
         and type(unit) is str and re.fullmatch(
             r'careertrellis-onedrive-bundle-identity-[0-9a-f]{32}-[0-9]{2}\.service', unit))
    expected = {'LoadState': 'loaded', 'Type': 'exec', 'Restart': 'no', 'RemainAfterExit': 'no',
        'ActiveState': 'active', 'SubState': 'running', 'MainPID': str(pid), 'ControlPID': '0',
        'Job': '', 'KillMode': 'control-group', 'SendSIGKILL': 'yes', 'RuntimeMaxUSec': '15min',
        'TimeoutStartUSec': '30s', 'TimeoutStopUSec': '30s', 'ControlGroup': '/system.slice/'+unit,
        'ExecCondition': '', 'ExecStartPre': '', 'ExecStartPost': '', 'ExecStop': '', 'ExecStopPost': ''}
    need(all(value.get(key) == expected_value for key, expected_value in expected.items())
         and catalog.hex_value(value['InvocationID'], 32), 'finite_identity_owner_profile_required')
    return value['InvocationID']


def collect(transaction, verification=1, backend=None):
    """One bounded fresh witness; partial outputs are always retained.

    An explicit offline backend returns native_identity_accepted=False. Default
    execution never constructs a native backend or contacts a provider.
    """
    need(catalog.hex_value(transaction, 32) and type(verification) is int and 1 <= verification <= 99)
    need(backend is not None, 'native_identity_collector_not_installed')
    need(getattr(backend, 'mode', None) in ('offline-test', 'native-installed'))
    need(backend.mode == 'offline-test' or type(backend) is Native,
         'caller_selected_native_adapter_refused')
    with backend.locked(transaction, verification):
        backend.preflight()
        raw = backend.download_receipt(transaction)
        observed = receipt(raw, transaction, backend.now())
        backend.begin(transaction, verification, observed, sha(raw))
        started = backend.monotonic()
        try:
            before = stat_identity(backend.stat_object(observed, 'before'), observed['archive_bytes'])
            backend.download(observed)
            digest, size = backend.downloaded_digest()
            need(digest == observed['archive_sha256'] and size == observed['archive_bytes'],
                 'current_object_download_mismatch')
            after = stat_identity(backend.stat_object(observed, 'after'), observed['archive_bytes'])
            need(before == after, 'current_object_identity_changed')
            now = backend.now(); elapsed = backend.monotonic() - started
            need(math.isfinite(elapsed) and 0 <= elapsed <= MAX_ELAPSED_SECONDS
                 and timestamp(observed['completed_at']) <= now,
                 'identity_witness_over_budget')
            backend.unchanged(raw)
            value = {'schema': 'careertrellis.onedrive-bundle-identity/v1',
                'observed_at': now.isoformat(), 'download_receipt_sha256': sha(raw),
                'path': observed['archive_sha256'] + '/repository.tar',
                'object_id': after['object_id'], 'size': size, 'sha256': digest,
                'identity_verified_before_after_download': True}
            backend.finish(value)
            return {'schema': 'careertrellis.onedrive-identity-collection/v1',
                'result': 'fresh_reverification_complete', 'verification': verification,
                'identity_receipt_sha256': sha(bundle.canonical(value)),
                'native_identity_accepted': type(backend) is Native,
                'original_download_identity_proven': False, 'provider_writes': 0,
                'deletions_performed': 0}
        except BaseException:
            try: backend.failed()
            except Exception: pass
            raise IdentityRefused('identity_reverification_requires_reconciliation') from None


class Native:
    """Fixed installed native boundary. Constructing it performs no operation."""
    mode = 'native-installed'
    def __init__(self):
        self.fds = []; self.config_fd = None; self.root = None; self.config = None
        self.lock_identities = []
        self.transaction = None; self.verification = None; self.expected = None

    def now(self): return dt.datetime.now(dt.timezone.utc)
    def monotonic(self): return time.monotonic()

    def command(self, arguments, phase, *, output=False, timeout=120):
        need(self.config_fd is not None and self.fds and self.root is not None and self.expected is not None)
        target = self.target(self.expected)
        if phase in ('stat-before', 'stat-after'):
            need(arguments == ['lsjson', target, '--stat', '--no-modtime', '--no-mimetype']
                 and output is True and timeout == 120)
        elif phase == 'download':
            need(arguments == ['copyto', target, str(self.root/'archive.tar'), '--immutable']
                 and output is False and timeout == MAX_ELAPSED_SECONDS)
        else: need(False, 'identity_command_refused')
        self._unchanged()
        if self.root is not None:
            hydrate.exclusive(self.root/(phase+'-intent.json'), bundle.canonical({
                'phase': phase, 'owner': self.owner, 'observed_at': self.now().isoformat()}))
        args = [capture_plan.BINARY, *arguments, '--config', '/proc/self/fd/'+str(self.config_fd),
            '--log-level', 'ERROR', '--stats', '0', '--retries', '1', '--low-level-retries', '1',
            '--contimeout', '15s', '--timeout', '45s', '--transfers', '1', '--checkers', '1']
        if phase == 'download':
            # copyto rejects filter flags such as --max-size. The exact before/
            # after stat, local byte count and SHA bind size; transfer is bounded
            # separately without repeating that known CLI incompatibility.
            args += ['--max-transfer', str(self.expected['archive_bytes'])+'B', '--cutoff-mode', 'HARD']
        with subprocess.Popen(args, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=ENV,
                pass_fds=(*self.fds, self.config_fd), start_new_session=True) as child:
            started = time.monotonic(); data = bytearray()
            try:
                ticks = int(Path('/proc/%d/stat' % child.pid).read_text().rsplit(') ', 1)[1].split()[19])
                hydrate.exclusive(self.root/(phase+'-child.json'), bundle.canonical({
                    'pid': child.pid, 'start_ticks': ticks, 'boot_id': self.owner['boot_id']}))
                if output:
                    selector = selectors.DefaultSelector()
                    try:
                        selector.register(child.stdout, selectors.EVENT_READ)
                        while True:
                            left = timeout-(time.monotonic()-started); need(left > 0)
                            need(selector.select(left))
                            raw = os.read(child.stdout.fileno(), min(65536, MAX_OUTPUT+1-len(data)))
                            if not raw: break
                            data.extend(raw); need(len(data) <= MAX_OUTPUT)
                    finally: selector.close(); child.stdout.close()
                code = child.wait(timeout=max(.01, timeout-(time.monotonic()-started)))
                need(code == 0)
            finally:
                if child.poll() is None: child.kill(); child.wait()
                hydrate.exclusive(self.root/(phase+'-terminal.json'), bundle.canonical({
                    'pid': child.pid, 'returncode': child.returncode,
                    'elapsed_seconds': round(time.monotonic()-started, 3)}))
        return bytes(data)

    def system(self, args): return hydrate.command(args, output=True, timeout=15)

    def unit_profile(self):
        return common_io.read_unit_properties(self.unit, PROFILE_FIELDS, self.system)

    def idle(self):
        units = list(hydrate.UNITS)
        raw = self.system(['/usr/bin/systemctl', 'list-units', '--all', '--plain', '--no-legend',
            '--no-pager', 'careertrellis-paired-db*.service', 'careertrellis-onedrive-bundle-*.service'])
        lines = raw.decode('ascii').splitlines(); need(len(lines) <= 128)
        for line in lines:
            unit = line.split()[0]
            need(re.fullmatch(r'careertrellis-(?:paired-db(?:-recovery)?@|onedrive-bundle-[a-z]+-)[0-9a-f]{32}(?:-[0-9]{2})?\.service', unit))
            if unit != self.unit: units.append(unit)
        for unit in units:
            raw = self.system(['/usr/bin/systemctl', 'show', unit,
                '--property=ActiveState,MainPID,ControlPID,Job,ControlGroup'])
            values = dict(line.split('=', 1) for line in raw.decode('ascii').splitlines())
            need(values.get('ActiveState') in ('inactive', 'failed')
                 and values.get('MainPID') == values.get('ControlPID') == '0' and not values.get('Job'))
            group = values.get('ControlGroup')
            if group:
                need(group == '/system.slice/'+unit)
                root = Path('/sys/fs/cgroup')/group.lstrip('/')
                if root.exists():
                    import itertools
                    files = list(itertools.islice(root.rglob('cgroup.procs'), 65)); need(len(files) <= 64)
                    need(all(not path.read_text().strip() for path in files))

    @contextmanager
    def locked(self, transaction, verification):
        need(catalog.hex_value(transaction, 32) and type(verification) is int and 1 <= verification <= 99)
        need(hasattr(os, 'geteuid') and hasattr(os, 'getegid') and os.geteuid() == os.getegid() == 0
             and socket.gethostname() == 'job-applier-db-staging-01', 'native_identity_host_refused')
        try:
            from . import entry
            package = Path('/usr/local/libexec/careertrellis/onedrive_retention')
            need(Path(__file__).resolve(strict=True) == package/'identity_collect.py'
                 and Path(entry.__file__).resolve(strict=True) == package/'entry.py')
            revision = entry.verify_install()
            manifest = json.loads(hydrate.protected(package/'manifest.json', 0o644)[0])
            need(catalog.hex_value(revision, 40) and manifest['revision'] == revision
                 and manifest['schema'] == 'careertrellis.onedrive-retention-install/v1'
                 and sha(hydrate.protected(package/'identity_collect.py', 0o644)[0])
                     == manifest['files']['identity_collect.py'], 'identity_entry_verification_required')
        except Exception: raise IdentityRefused('identity_entry_verification_required') from None
        self.transaction = transaction; self.verification = verification
        self.unit = 'careertrellis-onedrive-bundle-identity-'+transaction+'-%02d.service' % verification
        need(Path('/proc/self/cgroup').read_text().strip() == '0::/system.slice/'+self.unit)
        values = self.unit_profile()
        invocation = validate_unit_profile(values, os.getpid(), self.unit)
        self.owner = {'pid': os.getpid(), 'start_ticks': int(Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()[19]),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'unit': self.unit, 'invocation_id': invocation}
        self.idle()
        import fcntl
        try:
            for name in LOCKS:
                path = Path(name)
                for parent in path.parents:
                    meta = parent.lstat()
                    need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                         and (stat.S_IMODE(meta.st_mode) == 0o1777 if str(parent) == '/run/lock'
                              else not meta.st_mode & 0o022))
                before = path.lstat()
                need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
                     and before.st_nlink == 1 and before.st_size == 0 and stat.S_IMODE(before.st_mode) == 0o600)
                fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK); self.fds.append(fd)
                need((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (before.st_dev, before.st_ino))
                self.lock_identities.append((before.st_dev, before.st_ino, before.st_uid, before.st_gid,
                    before.st_mode, before.st_nlink, before.st_size))
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.idle()
            need(not os.path.lexists('/run/careertrellis-hosted-activation.lease'))
            yield
        finally:
            if self.config_fd is not None: os.close(self.config_fd); self.config_fd = None
            for fd in reversed(self.fds): os.close(fd)
            self.fds = []
            self.lock_identities = []

    def preflight(self):
        from . import entry
        idle_guard = getattr(entry, 'identity_reverification_idle', None)
        need(callable(idle_guard) and idle_guard(self.transaction, self.verification) is True,
             'persistent_identity_idle_guard_required')
        os.umask(0o077)
        binary, self.binary_identity = hydrate.protected(capture_plan.BINARY, limit=256*1024**2)
        need(sha(binary) == capture_plan.BINARY_SHA256)
        self.config = hydrate.protected(capture_plan.CONFIG, 0o600)
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(self.config[0].decode())
        need(not parser.defaults() and set(parser.sections()) == {'r2', 'onedrive'}
             and parser['onedrive'].get('type') == 'onedrive')
        for name, expected in hydrate.PINS.items():
            need(sha(hydrate.protected(hydrate.CORE/(name+'.py'))[0]) == expected)
        one = configparser.ConfigParser(interpolation=None); one['onedrive'] = dict(parser['onedrive'])
        output = io.StringIO(); one.write(output); raw = output.getvalue().encode()
        import fcntl
        self.config_fd = os.memfd_create('onedrive-identity-readonly', os.MFD_ALLOW_SEALING)
        need(os.write(self.config_fd, raw) == len(raw)); os.fchmod(self.config_fd, 0o400); os.lseek(self.config_fd, 0, 0)
        fcntl.fcntl(self.config_fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_GROW|fcntl.F_SEAL_WRITE)

    def download_receipt(self, transaction):
        self.source = ROOT/transaction
        with bundle._directory(self.source) as fd:
            meta = os.fstat(fd)
            need(meta.st_uid == meta.st_gid == 0 and stat.S_IMODE(meta.st_mode) == 0o700)
        need(not os.path.lexists(self.source/'bundle-object-identity.json'), 'identity_receipt_already_exists')
        raw = hydrate.protected(self.source/'downloaded-receipt.json', 0o600)[0]
        self.download_receipt_snapshot = raw
        return raw

    def begin(self, transaction, verification, observed, digest):
        self.expected = observed
        manifest_raw = hydrate.protected(self.source/'downloaded-manifest.json', 0o600)[0]
        manifest = catalog.decode(manifest_raw, bundle.MAX_MANIFEST); bundle.validate_manifest(manifest)
        need(sha(manifest_raw) == observed['manifest_sha256'] and manifest['backup_label'] == observed['backup_label']
             and manifest['generation_sha256'] == observed['generation_sha256'])
        import shutil
        need(shutil.disk_usage(self.source).free >= observed['archive_bytes'] + 5*1024**3)
        self.root = self.source/('identity-verification-%02d' % verification)
        self.root.mkdir(mode=0o700)
        directory = os.open(self.source, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
        hydrate.exclusive(self.root/'intent.json', bundle.canonical({'schema': 'careertrellis.onedrive-identity-intent/v1',
            'transaction': transaction, 'verification': verification, 'archive_sha256': observed['archive_sha256'],
            'archive_bytes': observed['archive_bytes'], 'download_receipt_sha256': digest,
            'owner': self.owner, 'started_at': self.now().isoformat()}))

    def target(self, observed): return NAMESPACE+'/'+observed['archive_sha256']+'/repository.tar'
    def stat_object(self, observed, phase):
        return self.command(['lsjson', self.target(observed), '--stat', '--no-modtime', '--no-mimetype'],
                            'stat-'+phase, output=True)
    def download(self, observed):
        need(not os.path.lexists(self.root/'archive.tar'))
        self.command(['copyto', self.target(observed), str(self.root/'archive.tar'), '--immutable'],
                     'download', timeout=MAX_ELAPSED_SECONDS)
    def downloaded_digest(self):
        path = self.root/'archive.tar'
        with bundle._directory(path.parent) as fd:
            meta = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                 and stat.S_IMODE(meta.st_mode) == 0o600 and meta.st_nlink == 1
                 and meta.st_size == self.expected['archive_bytes'])
            with bundle._source(fd, path.name, bundle._identity(meta)) as stream:
                return bundle._hash(stream, meta.st_size), meta.st_size
    def _unchanged(self):
        need(hydrate.protected(capture_plan.CONFIG, 0o600) == self.config)
        meta = Path(capture_plan.BINARY).lstat()
        need((meta.st_dev, meta.st_ino, meta.st_mode, meta.st_uid, meta.st_gid, meta.st_nlink,
              meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns) == self.binary_identity)
        for fd, name, expected in zip(self.fds, LOCKS, self.lock_identities):
            opened, path = os.fstat(fd), Path(name).lstat()
            identity = lambda meta: (meta.st_dev, meta.st_ino, meta.st_uid, meta.st_gid,
                meta.st_mode, meta.st_nlink, meta.st_size)
            need(identity(opened) == identity(path) == expected)
    def unchanged(self, raw):
        self._unchanged(); need(hydrate.protected(self.source/'downloaded-receipt.json', 0o600)[0] == raw)
        self.idle()
    def finish(self, value):
        raw = bundle.canonical(value)
        hydrate.exclusive(self.root/'receipt.json', raw)
        hydrate.exclusive(self.source/'bundle-object-identity.json', raw)
    def failed(self):
        if self.root is not None:
            hydrate.exclusive(self.root/'failed.json', bundle.canonical({'result': 'failed_requires_reconciliation',
                'provider_writes': 0, 'deletions_performed': 0, 'observed_at': self.now().isoformat()}))
