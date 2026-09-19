"""Additive owner-credential utility installation; never issue a real code."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager

SOURCE = 'd68fb5caa4cc780f8c55266236907f228dd0a7037db61c286036f641b773d83b'
TEST = 'e8577a61e570f2f209fe181da1aa711accae6e61a83085025ee04c45f019bf3b'
PREVIOUS = '7b4eb93defc470a5d46ba58723af700d67d58f083444a2c0618b7d6afbdac9d4'
INCOMING = Path('/var/tmp/careertrellis-owner-recovery-' + SOURCE)
TARGET = Path('/opt/careertrellis/owner-recovery') / SOURCE
STATE = Path('/var/lib/careertrellis-owner-recovery')
WRAPPER = Path('/usr/local/sbin/careertrellis-owner-recovery')
RELEASE = Path('/opt/careertrellis/staging/releases/e0cd2e31cd5ac101c6cecd9a87c5e5c3562ea7bc')
OPS = Path('/opt/careertrellis/staging/releases/45e33567bc9e23746ea5ba8f929e14b180288576')
RUNTIME = OPS / 'backend/venv'
PYTHON = RUNTIME / 'bin/python'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}


def require(value):
    if not value:
        raise RuntimeError('Owner recovery installation refused; retain existing state.')


def safe_parents(path):
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def command(argv):
    result = subprocess.run(argv, capture_output=True, env=ENV, timeout=60)
    require(result.returncode == 0)
    return result.stdout


def trusted_runtime():
    # Inspect using system Python before executing any private-runtime code.
    safe_parents(RUNTIME)
    require(not RUNTIME.is_symlink())
    receipt = OPS / 'handoff-installation/complete.json'
    safe_parents(receipt)
    info = receipt.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600)
    value = json.loads(receipt.read_bytes())
    require(value['commit'] == OPS.name and value['private_runtime_bytes_verified'] is True)
    interpreter = Path('/usr/bin/python3').resolve()
    safe_parents(interpreter)
    info = interpreter.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    for path in (RUNTIME, *RUNTIME.rglob('*')):
        info = path.lstat()
        require(info.st_uid == 0)
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            require(resolved.is_relative_to(RUNTIME) or (path.parent == RUNTIME / 'bin'
                    and path.name in ('python', 'python3', 'python3.12') and resolved == interpreter))
        else:
            require((stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                    and not info.st_mode & 0o022)
    require('include-system-site-packages = false' in (RUNTIME / 'pyvenv.cfg').read_text())


def baseline():
    require(Path('/opt/careertrellis/staging/current').resolve() == RELEASE)
    require(not Path('/run/careertrellis-hosted-activation.lease').exists())
    service = command(['/usr/bin/systemctl', 'show', 'careertrellis-web.service',
                       '--property=ActiveState,MainPID,NRestarts,ExecMainStartTimestampMonotonic'])
    require(b'ActiveState=active' in service)
    files = []
    for name in ('staging.env', 'postgres-migration.env', 'postgres-runtime.env', 'auth-v2.env'):
        path = Path('/etc/careertrellis') / name
        safe_parents(path)
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600)
        files.append(hashlib.sha256(path.read_bytes()).digest())
    return service, files


def wrapper_bytes(target):
    return ('#!/bin/sh\nset -eu\nexec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin '
            'LANG=C.UTF-8 ' + str(PYTHON) + ' -I -B ' + str(target / 'owner_recovery.py') + ' "$@"\n').encode()


def protected_bytes(path, mode):
    safe_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == mode and info.st_size <= 65536)
        return os.read(fd, 65537)
    finally:
        os.close(fd)


def state_snapshot():
    safe_parents(STATE)
    info = STATE.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    return {p.name: hashlib.sha256(protected_bytes(p, 0o600)).digest() for p in STATE.iterdir()}


@contextmanager
def upgrade_lock():
    import fcntl
    state_snapshot()
    path = STATE / 'lock'
    protected_bytes(path, 0o600)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def install(upgrade=False):
    require(os.geteuid() == 0 and os.uname().nodename == 'careertrellis-staging-web-1')
    before = baseline()
    trusted_runtime()
    require(shutil.disk_usage('/opt').free > 3_000_000_000 + 10_000_000)
    require(not TARGET.exists() and not TARGET.is_symlink())
    old_wrapper = wrapper_bytes(TARGET.parent / PREVIOUS)
    if upgrade:
        state_before = state_snapshot()
        require(protected_bytes(WRAPPER, 0o755) == old_wrapper)
    else:
        require(not STATE.exists() and not STATE.is_symlink()
                and not WRAPPER.exists() and not WRAPPER.is_symlink())
    content = {}
    for name, digest in (('owner_recovery.py', SOURCE), ('test_owner_recovery.py', TEST)):
        fd = os.open(INCOMING / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 65536)
            raw = os.read(fd, 65537)
            require(hashlib.sha256(raw).hexdigest() == digest)
            content[name] = raw
        finally:
            os.close(fd)
    base = TARGET.parent
    safe_parents(base)
    if not base.exists():
        base.mkdir(mode=0o755)
    safe_parents(TARGET)
    TARGET.mkdir(mode=0o755)
    for name, raw in content.items():
        path = TARGET / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
    tests = ('import sys,unittest; sys.path.insert(0,' + repr(str(TARGET)) + '); '
             'suite=unittest.defaultTestLoader.loadTestsFromName("test_owner_recovery"); '
             'r=unittest.TextTestRunner().run(suite); '
             'sys.exit(0 if r.wasSuccessful() and r.testsRun==28 and not r.skipped else 1)')
    command([str(PYTHON), '-I', '-B', '-c', tests])
    result = command([str(PYTHON), '-I', '-B', str(TARGET / 'owner_recovery.py'), 'preflight'])
    require(result == b'active_owner_binding=verified; schema=0014; application_recovery=not_enabled\n')
    require(baseline() == before)
    safe_parents(STATE)
    if not upgrade:
        STATE.mkdir(mode=0o700)
    else:
        require(state_snapshot() == state_before)
    wrapper = wrapper_bytes(TARGET)
    safe_parents(WRAPPER)
    if upgrade:
        fd, temporary = tempfile.mkstemp(prefix='.owner-recovery-', dir=WRAPPER.parent)
        try:
            os.fchmod(fd, 0o755)
            with os.fdopen(fd, 'wb', closefd=False) as out:
                out.write(wrapper); out.flush(); os.fsync(fd)
            require(protected_bytes(WRAPPER, 0o755) == old_wrapper)
            os.replace(temporary, WRAPPER)
        finally:
            os.close(fd)
            if os.path.exists(temporary):
                os.unlink(temporary)
    else:
        fd = os.open(WRAPPER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
        with os.fdopen(fd, 'wb') as out:
            out.write(wrapper); out.flush(); os.fsync(out.fileno())
    for directory in (TARGET, STATE.parent, WRAPPER.parent):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    if upgrade:
        require(state_snapshot() == state_before)
    else:
        require(command([str(WRAPPER), 'status']) == b'owner_credential=not_enrolled\n')
    require(baseline() == before)
    print('owner_recovery_utility=installed; native_tests=28; owner_binding=verified; credential='
          + ('preserved' if upgrade else 'not_enrolled') + '; application=unchanged')


if __name__ == '__main__':
    try:
        require(os.geteuid() == 0 and os.uname().nodename == 'careertrellis-staging-web-1')
        require(len(sys.argv) == 2 and sys.argv[1] in ('--check', '--install', '--upgrade'))
        if sys.argv[1] == '--check':
            baseline()
            trusted_runtime()
            print('protected_operations_runtime=verified; application=unchanged')
        elif sys.argv[1] == '--upgrade':
            with upgrade_lock():
                install(upgrade=True)
        else:
            install()
    except Exception:
        print('Owner recovery installation did not complete. Retain evidence and inspect; do not overwrite.')
        raise SystemExit(2)
