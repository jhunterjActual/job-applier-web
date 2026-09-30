"""Additive reviewed bridge install. Never migrate, issue or consume a code."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
from contextlib import contextmanager

BASE = Path('/opt/careertrellis/owner-recovery-bridge')
WRAPPER = Path('/usr/local/sbin/careertrellis-owner-recovery-bridge')
PREVIOUS = 'd070512b55ba22a8750d393cdcb37a45e725f4fe'
TESTS = ('test_owner_recovery', 'test_owner_recovery_bridge', 'test_identity_entry',
         'test_identity_evidence', 'test_identity_operator', 'test_owner_recovery_bridge_installer')


def require(value):
    if not value: raise ValueError('Bridge installation refused; retain existing state.')


def write(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def launcher(python, target):
    return ('#!/bin/sh\nset -eu\nexec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin '
        'LANG=C.UTF-8 CAREERTRELLIS_CONFIG_READ_ONLY=1 ' + str(python) + ' -I -B '
        + str(target / 'backend/owner_recovery_bridge_cli.py') + ' "$@"\n').encode()


def publish_launcher(raw, previous):
    if previous is None:
        write(WRAPPER, raw, 0o755)
        return
    # Caller holds the credential lock; never unlink the working launcher first.
    require(WRAPPER.read_bytes() == previous)
    pending = WRAPPER.with_name(WRAPPER.name + '.upgrade')
    write(pending, raw, 0o755)
    require(WRAPPER.read_bytes() == previous)
    os.replace(pending, WRAPPER)
    fd = os.open(WRAPPER.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


@contextmanager
def transition_guard():
    import fcntl
    locks = []
    def unselected():
        for name in ('identity-selected-plan.json', 'provider-transition.json', 'session-v2-transition.json'):
            path = Path('/etc/careertrellis') / name
            require(not path.exists() and not path.is_symlink())
    try:
        for name in ('careertrellis-maintenance.lock', 'careertrellis-postgres-migration.lock',
                     'careertrellis-provider-secret-rotation.lock'):
            fd = os.open(Path('/run/lock') / name, os.O_RDWR | os.O_NOFOLLOW)
            locks.append(fd); info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        unselected()
        yield
        unselected()
    finally:
        for fd in reversed(locks): os.close(fd)


def install(commit, digest, *, upgrade=False):
    require(os.geteuid() == 0 and os.uname().nodename == 'careertrellis-staging-web-1')
    require(re.fullmatch('[0-9a-f]{40}', commit) and re.fullmatch('[0-9a-f]{64}', digest))
    target = BASE / commit
    require(not target.exists() and not target.is_symlink() and commit != PREVIOUS)
    if not upgrade: require(not WRAPPER.exists() and not WRAPPER.is_symlink())
    require(shutil.disk_usage('/opt').free > 3_100_000_000)
    incoming = Path('/var/tmp') / ('careertrellis-owner-bridge-' + commit + '.tar')
    fd = os.open(incoming, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size < 20_000_000)
        with os.fdopen(fd, 'rb', closefd=False) as stream: raw = stream.read(20_000_001)
    finally: os.close(fd)
    require(hashlib.sha256(raw).hexdigest() == digest)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        manifest = {}; names = set()
        for item in archive.getmembers():
            path = Path(item.name)
            require(not path.is_absolute() and '..' not in path.parts and item.name not in names
                and (item.isfile() or item.isdir()) and not item.mode & 0o022)
            require(not any(part in ('.git', '.codex', 'venv', 'data', '__pycache__') or
                (part.startswith('.env') and item.name != 'backend/.env.example') for part in path.parts))
            names.add(item.name)
            if item.isfile(): manifest[item.name] = hashlib.sha256(archive.extractfile(item).read()).hexdigest()
        require(all('backend/' + name + '.py' in manifest for name in (*TESTS,
            'owner_recovery_bridge_cli', 'owner_recovery_bridge', 'identity_evidence', 'identity_selection_barrier','identity_operator')))
        require('source-manifest.json' not in names and 'installation.json' not in names)
        for parent in (BASE.parent, *BASE.parent.parents):
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        if not BASE.exists(): BASE.mkdir(mode=0o700)
        info = BASE.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        target.mkdir(mode=0o700)
        archive.extractall(target, filter='data')
    for path in target.rglob('*'):
        require(not path.is_symlink())
        path.chmod(0o700 if path.is_dir() else 0o600)
    for name, expected in manifest.items():
        require(hashlib.sha256((target / name).read_bytes()).hexdigest() == expected)
    write(target / 'source-manifest.json', json.dumps(manifest, sort_keys=True).encode())
    # This helper is part of the verified immutable archive, never imported from
    # the upload directory. Its historical installer entry point is not called.
    spec = importlib.util.spec_from_file_location('credential_install_checks',
        target / 'deploy/scripts/install-owner-recovery.py')
    checks = importlib.util.module_from_spec(spec); spec.loader.exec_module(checks)
    checks.trusted_runtime()
    before = checks.baseline()
    old_wrapper = checks.protected_bytes(checks.WRAPPER, 0o755)
    checks.command([str(checks.PYTHON), '-I', '-B',
        str(target / 'backend/owner_recovery_bridge_cli.py'), 'preactivation-check'])
    with checks.upgrade_lock(), transition_guard():
        state_before = checks.state_snapshot()
        previous = checks.protected_bytes(WRAPPER, 0o755) if upgrade else None
        if upgrade: require(previous == launcher(checks.PYTHON, BASE / PREVIOUS))
        test_code = ('import sys,unittest; sys.path.insert(0,sys.argv[1]); '
            'r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromNames(' + repr(TESTS) + ')); '
            'sys.exit(not r.wasSuccessful() or bool(r.skipped))')
        checks.command([str(checks.PYTHON), '-I', '-B', '-c', test_code, str(target / 'backend')])
        require(checks.state_snapshot() == state_before and checks.baseline() == before
            and checks.protected_bytes(checks.WRAPPER, 0o755) == old_wrapper)
        launch = launcher(checks.PYTHON, target)
        checks.safe_parents(WRAPPER)
        if upgrade: require(checks.protected_bytes(WRAPPER, 0o755) == previous)
        publish_launcher(launch, previous)
    checks.command([str(WRAPPER), 'preactivation-check'])
    require(checks.baseline() == before and checks.protected_bytes(checks.WRAPPER, 0o755) == old_wrapper)
    with checks.upgrade_lock(): require(checks.state_snapshot() == state_before)
    write(target / 'installation.json', json.dumps({'commit': commit, 'archive_sha256': digest,
        'outcome': 'installed_preactivation_only', 'native_tests': 'passed_without_skips',
        'application_and_credential_unchanged': True}, sort_keys=True).encode())
    print('bridge_installed_preactivation_only; application_and_credential_unchanged')


if __name__ == '__main__':
    try:
        require(len(sys.argv) == 3 or (len(sys.argv) == 4 and sys.argv[3] == '--upgrade'))
        install(*sys.argv[1:3], upgrade=len(sys.argv) == 4)
    except BaseException:
        print('Bridge installation incomplete or refused; retain artifacts and existing credential.', file=sys.stderr)
        raise SystemExit(2)
