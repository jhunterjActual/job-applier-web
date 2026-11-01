"""First-install-only, disabled ops bundle preparation/application.

Application requires a separately approved invocation. No SSH identity, daemon
reload, unit enable/start/stop/restart, provider call or application release is
performed. Partial installation is retained for explicit reconciliation.
"""
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import tarfile
import uuid

try:
    from . import build_bundle as bundle
    from .common import Policy, canonical
except ImportError:
    import build_bundle as bundle
    from common import Policy, canonical


PACKAGE = '/usr/local/libexec/careertrellis/paired_maintenance'
STATE = '/var/lib/careertrellis/paired-maintenance'
PRIVATE = '/etc/careertrellis/paired-maintenance'
EVIDENCE = '/var/backups/careertrellis/paired-maintenance-install'
OLD_WEB_CORE = 'e143adbeea83df31d4fc4b5c1b25f0f4972dad7dbeef1b427688ca09e468ee91'
PARENTS = ('/usr/local/libexec/careertrellis', '/etc/systemd/system',
           '/var/lib/careertrellis', '/etc/careertrellis', '/var/backups/careertrellis')


class InstallRefused(RuntimeError): pass


def need(value):
    if not value: raise InstallRefused('install_refused')


def sha(raw): return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            need(key not in result); result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(InstallRefused('install_refused')))


def packet_files(packet, expected_sha):
    need(isinstance(expected_sha, str) and re.fullmatch('[0-9a-f]{64}', expected_sha) and expected_sha != '0' * 64)
    path = Path(packet) / 'paired-maintenance.tar.gz'
    meta = path.lstat()
    need(stat.S_ISREG(meta.st_mode) and not path.is_symlink() and meta.st_nlink == 1 and meta.st_size <= 64 * 1024**2)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                         | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        opened = os.fstat(descriptor)
        need((opened.st_dev, opened.st_ino) == (meta.st_dev, meta.st_ino))
        raw = os.read(descriptor, 64 * 1024**2 + 1)
        after = os.fstat(descriptor)
        need((opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
             == (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
    finally: os.close(descriptor)
    need(len(raw) == meta.st_size and sha(raw) == expected_sha)
    allowed = set(bundle.sources()) | {bundle.PACKAGE + 'manifest.json'}
    values = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r|gz') as archive:
        for member in archive:
            need(len(values) < len(allowed) and member.name in allowed and member.name not in values)
            path = PurePosixPath(member.name)
            need(not path.is_absolute() and '..' not in path.parts and '\\' not in member.name)
            need(member.isfile() and not member.pax_headers and member.mode == 0o644
                 and member.uid == member.gid == member.mtime == 0 and 0 < member.size <= bundle.MAX_FILE)
            value = archive.extractfile(member).read(bundle.MAX_FILE + 1)
            need(len(value) == member.size); values[member.name] = value
    need(set(values) == allowed)
    manifest = decode(values[bundle.PACKAGE + 'manifest.json'])
    need(set(manifest) == {'schema', 'revision', 'files'} and manifest['schema'] == 'careertrellis.paired-maintenance-install/v1')
    need(isinstance(manifest['revision'], str) and re.fullmatch('[0-9a-f]{40}', manifest['revision'])
         and manifest['revision'] != '0' * 40 and set(manifest['files']) == set(bundle.PACKAGE_FILES))
    for name, expected in manifest['files'].items():
        need(expected == sha(values[bundle.PACKAGE + name]))
    return values, manifest


class Target:
    def __init__(self, root=Path('/'), *, test_only=False, role=None, boot=None):
        need(type(test_only) is bool)
        self.root, self.test_only = Path(root).absolute(), test_only
        if test_only:
            need(role in ('web', 'db') and boot is not None)
            self.role, self.boot = role, boot
        else:
            need(self.root == Path('/').absolute() and os.geteuid() == 0)
            host = socket.gethostname()
            need(host in ('careertrellis-staging-web-1', 'job-applier-db-staging-01'))
            self.role = 'web' if host == 'careertrellis-staging-web-1' else 'db'
            self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        need(str(uuid.UUID(self.boot)) == self.boot)

    def path(self, absolute):
        value = PurePosixPath(absolute)
        need(value.is_absolute() and '..' not in value.parts)
        return self.root.joinpath(*value.parts[1:])

    def directory(self, absolute, mode=None):
        path = self.path(absolute)
        for candidate in (path, *path.parents):
            if not candidate.is_relative_to(self.root): break
            info = candidate.lstat()
            need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                 and not getattr(info, 'st_file_attributes', 0) & 0x400)
            if not self.test_only: need(info.st_uid == info.st_gid == 0 and not info.st_mode & 0o022)
        if mode is not None and not self.test_only: need(stat.S_IMODE(path.stat().st_mode) == mode)

    def read(self, absolute, mode=0o644, limit=2 * 1024**2):
        path = self.path(absolute); self.directory(str(PurePosixPath(absolute).parent))
        info = path.lstat()
        need(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_nlink == 1
             and not getattr(info, 'st_file_attributes', 0) & 0x400 and info.st_size <= limit)
        if not self.test_only: need(info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
        try:
            before = os.fstat(descriptor)
            need((before.st_dev, before.st_ino) == (info.st_dev, info.st_ino))
            raw = os.read(descriptor, limit + 1)
            after = os.fstat(descriptor)
            need(len(raw) == info.st_size and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                 == (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
            return raw
        finally: os.close(descriptor)

    def capacity(self, absolute):
        available = shutil.disk_usage(self.path(absolute)).free
        inodes = None if self.test_only and os.name == 'nt' else os.statvfs(self.path(absolute)).f_favail
        return {'free_bytes': available, 'free_inodes': inodes}

    def absent(self, absolute): need(not os.path.lexists(self.path(absolute)))

    def mkdir(self, absolute, mode):
        self.directory(str(PurePosixPath(absolute).parent))
        self.path(absolute).mkdir(mode=mode)
        if not self.test_only:
            os.chown(self.path(absolute), 0, 0); os.chmod(self.path(absolute), mode)
        self.fsync_dir(str(PurePosixPath(absolute).parent))

    def fsync_dir(self, absolute):
        if self.test_only and os.name == 'nt': return
        descriptor = os.open(self.path(absolute), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)

    def write(self, absolute, raw, mode):
        self.directory(str(PurePosixPath(absolute).parent))
        descriptor = os.open(self.path(absolute), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), mode)
        try:
            if not self.test_only:
                os.fchown(descriptor, 0, 0); os.fchmod(descriptor, mode)
            offset = 0
            while offset < len(raw):
                amount = os.write(descriptor, raw[offset:]); need(amount > 0); offset += amount
            os.fsync(descriptor)
        finally: os.close(descriptor)
        self.fsync_dir(str(PurePosixPath(absolute).parent))
        need(self.read(absolute, mode=mode) == raw)

    def pointers(self):
        result = {}
        base = '/opt/careertrellis/staging'
        if self.role == 'db':
            # DB hosts have no application release pointers. Preserve that
            # observed absence rather than inventing a second app deployment.
            for name in ('current', 'previous'):
                need(not os.path.lexists(self.path(base + '/' + name)))
                result[name] = None
            return result
        for name in ('current', 'previous'):
            path = self.path(base + '/' + name)
            need(path.is_symlink())
            resolved = path.resolve(strict=True)
            release_root = self.path(base + '/releases')
            need(resolved.parent == release_root and re.fullmatch('[0-9a-f]{40}', resolved.name))
            result[name] = resolved.name
        return result

    @contextmanager
    def fences(self):
        if self.test_only:
            yield
            return
        try:
            from .runtime import Fence
        except ImportError:
            from runtime import Fence
        held = []
        # Preserve the independently observed canonical lock contracts; installer
        # never repairs permissions to make a probe pass.
        names = (('careertrellis-maintenance.lock', 0o644 if self.role == 'web' else 0o600),
                 ('careertrellis-generated-object-recovery.lock' if self.role == 'web'
                  else 'careertrellis-pgbackrest-repository.lock', 0o600))
        try:
            for name, mode in names: held.append(Fence('/run/lock/' + name, mode).acquire())
            yield
            for fence in held: fence.check()
        finally:
            for fence in reversed(held): fence.close()


def prepare(packet, expected_sha, policy, target):
    need(isinstance(policy, Policy))
    data = policy.data
    need(all(data[key] is False for key in ('enabled', 'source_readonly_attested', 'no_external_writers_attested')))
    values, manifest = packet_files(packet, expected_sha)
    # New installed source and the pre-existing cleanup OPS revision are separate
    # domains. The source revision must be explicit; ops_release is not its alias.
    need(data.get('manifest_revision') == manifest['revision'])
    for path in PARENTS: target.directory(path)
    for path in (PACKAGE, STATE, PRIVATE): target.absent(path)
    selected = {name: raw for name, raw in values.items() if name.startswith(bundle.PACKAGE)
                or target.role in bundle.sources()[name][1]}
    # The generated manifest is selected by its package prefix, without relying
    # on role data supplied in an external bundle receipt.
    for name in selected:
        absolute = '/' + name
        if name.startswith(bundle.PACKAGE): continue
        if name == bundle.CORE:
            need(target.role == 'web' and sha(target.read(absolute)) == OLD_WEB_CORE)
            need(data.get('artifact_hashes', {}).get('web', {}).get(absolute) == sha(selected[name]))
        else:
            target.absent(absolute)
            parent = str(PurePosixPath(absolute).parent)
            if parent.endswith('.service.d'):
                if os.path.lexists(target.path(parent)): target.directory(parent)
            else: target.directory(parent)
    pointers = target.pointers()
    if target.role == 'web': need(pointers['current'] == data['application_release'])
    if os.path.lexists(target.path(EVIDENCE)):
        target.directory(EVIDENCE, 0o700)
        # Any prior claim or attempt is retained evidence, even if publication
        # never reached its final destination. Changing attempt IDs is not retry
        # authority for a partial first installation.
        need(next(target.path(EVIDENCE).iterdir(), None) is None)
    peak = sum(len(raw) for raw in selected.values()) * 2 + 262144
    capacity = {path: target.capacity(path) for path in PARENTS}
    need(all(value['free_bytes'] >= policy.data['minimum_free_bytes'] + peak
             and (value['free_inodes'] is None or value['free_inodes'] > len(selected) + 40) for value in capacity.values()))
    return {'schema': 'careertrellis.paired-maintenance-install-plan/v1', 'role': target.role,
        'revision': manifest['revision'], 'archive_sha256': expected_sha, 'policy_sha256': policy.digest,
        'application_pointers': pointers, 'capacity': capacity, 'conservative_peak_bytes': peak,
        'files': {('/' + name): {'sha256': sha(raw), 'size_bytes': len(raw), 'mode': '0644'}
            for name, raw in sorted(selected.items())}, 'activation_performed': False,
        'fence_verification': 'required_during_approved_apply'}, selected


def install(packet, expected_sha, policy, target, *, approved=False, attempt=None):
    need(approved is True)
    attempt = attempt or uuid.uuid4().hex
    need(isinstance(attempt, str) and re.fullmatch('[0-9a-f]{32}', attempt))
    stage = '/usr/local/libexec/careertrellis/.paired-maintenance-install-' + attempt
    evidence = EVIDENCE + '/' + attempt
    target.absent(stage); target.absent(evidence)
    with target.fences():
        plan, selected = prepare(packet, expected_sha, policy, target)
        if not os.path.lexists(target.path(EVIDENCE)): target.mkdir(EVIDENCE, 0o700)
        target.write(EVIDENCE + '/install-intent.json', canonical({'attempt': attempt,
            'archive_sha256': expected_sha, 'policy_sha256': policy.digest,
            'package_stage': stage, 'attempt_evidence': evidence}), 0o600)
        target.mkdir(evidence, 0o700)
        target.write(evidence + '/intent.json', canonical(plan), 0o600)
        try:
            target.mkdir(stage, 0o700)
            for name, raw in selected.items():
                if name.startswith(bundle.PACKAGE): target.write(stage + '/' + PurePosixPath(name).name, raw, 0o644)
            if not target.test_only: os.chmod(target.path(stage), 0o755)
            target.absent(PACKAGE)
            os.rename(target.path(stage), target.path(PACKAGE)); target.fsync_dir('/usr/local/libexec/careertrellis')
            target.mkdir(PRIVATE, 0o700)
            target.write(PRIVATE + '/policy.json', canonical(policy.data), 0o600)
            target.mkdir(STATE, 0o755)
            target.write(STATE + '/public-policy.json', canonical(policy.data), 0o644)
            marker = {'schema': 'careertrellis.paired-maintenance-interlock/v1', 'policy_sha256': policy.digest,
                'boot_id': target.boot, 'release': policy.data['application_release'], 'phase': 'idle'}
            target.write(STATE + '/writer-interlock.json', canonical(marker), 0o644)
            role = STATE + '/' + target.role
            target.mkdir(role, 0o700)
            for child in ('runs', 'slots'): target.mkdir(role + '/' + child, 0o700)
            target.write(role + '/control.lock', b'', 0o600)
            target.write(role + '/status.json', canonical({'schema': 'careertrellis.paired-maintenance-status/v1',
                'policy_sha256': policy.digest, 'boot_id': target.boot, 'release': policy.data['application_release'],
                'phase': 'idle', 'result': 'ok', 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'services_restored': True}), 0o600)
            # State/package exist before adding any writer interlock drop-ins.
            for name, raw in sorted(selected.items()):
                if name.startswith(bundle.PACKAGE) or name == bundle.CORE: continue
                destination = '/' + name
                parent = str(PurePosixPath(destination).parent)
                if not os.path.lexists(target.path(parent)): target.mkdir(parent, 0o755)
                target.write(destination, raw, 0o644)
            if target.role == 'web':
                destination = '/' + bundle.CORE
                old = target.read(destination); need(sha(old) == OLD_WEB_CORE)
                target.write(evidence + '/previous-backup-health.py', old, 0o600)
                temporary = '/usr/local/libexec/careertrellis/.backup_health-paired-' + attempt + '.py'
                target.absent(temporary); target.write(temporary, selected[bundle.CORE], 0o644)
                need(target.read(destination) == old)
                os.replace(target.path(temporary), target.path(destination))
                target.fsync_dir('/usr/local/libexec/careertrellis')
            need(target.pointers() == plan['application_pointers'])
            for destination, expected in plan['files'].items():
                need(sha(target.read(destination)) == expected['sha256'])
            receipt = dict(plan, result='installed_disabled', attempt=attempt, application_pointers_unchanged=True,
                timers_enabled=False, daemon_reloaded=False, services_changed=False,
                evidence_directory=evidence, new_state_paths=[STATE, PRIVATE],
                retained_previous_core=(evidence + '/previous-backup-health.py' if target.role == 'web' else None),
                retention='Preserve package, prior WEB core and all attempt evidence until explicit acceptance/rollback review')
            target.write(evidence + '/receipt.json', canonical(receipt), 0o600)
            return receipt
        except Exception:
            # Never blindly resume or remove partially installed safety material.
            try: target.write(evidence + '/unconfirmed.json', canonical({'result': 'install_unconfirmed_operator_required'}), 0o600)
            except Exception: pass
            raise InstallRefused('install_unconfirmed_operator_required') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--policy', required=True)
    parser.add_argument('--approved-install', action='store_true')
    args = parser.parse_args(argv)
    try:
        target = Target()
        # The external operator-prepared disabled policy must itself be protected.
        policy = Policy(decode(target.read(args.policy, mode=0o600, limit=131072)))
        if args.approved_install:
            result = install(args.packet, args.archive_sha256, policy, target, approved=True)
        else: result, _ = prepare(args.packet, args.archive_sha256, policy, target)
        print(json.dumps(result, sort_keys=True)); return 0
    except Exception:
        print('{"result":"install_refused_or_unconfirmed_no_retry"}'); return 1


if __name__ == '__main__': raise SystemExit(main())
