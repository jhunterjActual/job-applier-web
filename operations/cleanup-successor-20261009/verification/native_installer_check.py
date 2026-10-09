"""Linux root-only isolated chroot fixture; no host locks, SQL, units or network.

Each fork chroots into a fresh synthetic tree before calling the installer.
Systemd/psql/native-suite subprocess responses are fixtures, not live acceptance.
Actual Linux flock, nofollow, permissions, exclusive creation and fsync run.
"""
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
assert hashlib.sha256((HERE / 'install-db-cleanup-successor.py').read_bytes()).hexdigest() == 'cd9ab8ee757b7c666763ea45de649ef7e631f78bc2354dff272e34272ca38a4d', 'reviewed installer hash differs'
spec = importlib.util.spec_from_file_location('fixture_installer', HERE / 'install-db-cleanup-successor.py')
installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)
REVISION = '1' * 40
LOCK_NAMES = ('careertrellis-maintenance.lock', 'careertrellis-postgres-migration.lock')


def fixture(root):
    for path, mode in (('run', 0o755), ('run/lock', 0o1777), ('var', 0o755),
                       ('var/tmp', 0o1777), ('opt/careertrellis/staging/releases', 0o755),
                       ('etc/postgresql/18/main', 0o755), ('etc/systemd/system', 0o755)):
        target = root / path; target.mkdir(parents=True, exist_ok=True); target.chmod(mode)
    (root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/bash\npostgres:x:1234:1234:Postgres:/nonexistent:/bin/false\n')
    for name in LOCK_NAMES:
        path = root / 'run/lock' / name; path.write_bytes(b''); path.chmod(0o600)
    for name in ('postgresql.conf', 'pg_hba.conf', 'pg_ident.conf'):
        (root / 'etc/postgresql/18/main' / name).write_text('# synthetic config\n')
    (root / 'etc/systemd/system/postgresql@18-main.service').write_text('[Service]\nExecStart=/fixture\n')
    files = {name: b'# fixture\n' for name in installer.REQUIRED |
             {'backend/' + name + '.py' for name in installer.TESTS}}
    manifest = {name: installer.digest(raw) for name, raw in files.items()}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, raw in files.items():
            info = tarfile.TarInfo(name); info.mode = 0o644; info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    raw = stream.getvalue(); encoded = json.dumps(manifest).encode()
    prefix = root / 'var/tmp' / ('careertrellis-cleanup-' + REVISION)
    Path(str(prefix) + '.tar').write_bytes(raw)
    Path(str(prefix) + '.manifest.json').write_bytes(encoded)
    return installer.digest(raw), installer.digest(encoded)


def baseline():
    return {'database': 'careertrellis', 'user': 'postgres', 'version': '180001',
            'data': '/var/lib/postgresql/18/main', 'heads': ['20261005_0023'],
            'policies': [{'minimum_session_version': 2, 'maintenance': False,
                          'signup_open': True, 'adapter_ready': True}],
            'roles': {name: {'rolcanlogin': True} for name in
                      ('careertrellis_runtime', 'careertrellis_worker',
                       'careertrellis_erasure_worker', 'careertrellis_admission_broker')}}


def scenario(name, hashes):
    target = installer.RELEASES / REVISION
    calls = {'query': 0, 'suite': 0}
    def fake_command(argv, data=None, **kwargs):
        if argv[0] == '/usr/bin/systemctl':
            return b'ActiveState=active\nSubState=running\nMainPID=123\nNRestarts=0\nJob=\nFragmentPath=/etc/systemd/system/postgresql@18-main.service\nDropInPaths=\nExecStart=/fixture\n'
        if argv[0] == '/usr/sbin/runuser':
            calls['query'] += 1
            value = baseline()
            if name == 'changed_policy' and calls['query'] == 2:
                value['policies'][0]['signup_open'] = False
            assert b'BEGIN READ ONLY' in data and '-w' in argv
            return json.dumps(value).encode()
        assert argv[0:2] == ['/bin/bash', '-n']
        return b''
    def fake_suite(*args, **kwargs):
        calls['suite'] += 1
        # Prove both real locks remain exclusive during native suite execution.
        for lock_name in LOCK_NAMES:
            fd = os.open('/run/lock/' + lock_name, os.O_RDWR)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError('installer fence not held')
            finally:
                os.close(fd)
        if name == 'unit_failure':
            return SimpleNamespace(returncode=1, stdout=b'', stderr=b'fixture test failure')
        return SimpleNamespace(returncode=0, stdout=b'fixture success', stderr=b'')
    held = None
    if name == 'existing_target':
        target.mkdir()
    if name == 'lock_busy':
        held = os.open('/run/lock/' + LOCK_NAMES[1], os.O_RDWR)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if name == 'lock_symlink':
        path = Path('/run/lock/' + LOCK_NAMES[0]); path.unlink(); path.symlink_to('/etc/passwd')
    if name == 'lock_mode':
        Path('/run/lock/' + LOCK_NAMES[0]).chmod(0o644)
    disk_calls = 0
    def disk(_):
        nonlocal disk_calls
        disk_calls += 1
        free = 10 * installer.FLOOR
        if name == 'low_space' or (name == 'space_dropped' and disk_calls > 1):
            free = installer.FLOOR - 1
        return SimpleNamespace(free=free)
    old_umask = os.umask(0o077)
    try:
        with patch.object(installer.os, 'uname', return_value=SimpleNamespace(nodename='job-applier-db-staging-01')), \
             patch.object(installer, 'command', side_effect=fake_command), \
             patch.object(installer.subprocess, 'run', side_effect=fake_suite), \
             patch.object(installer.shutil, 'disk_usage', side_effect=disk), contextlib.redirect_stdout(io.StringIO()):
            refused = False
            try:
                installer.install(REVISION, *hashes)
            except (installer.Refused, OSError):
                refused = True
        assert refused == (name != 'success'), (name, refused)
        restored_umask = os.umask(0o077)
        assert restored_umask == 0o077
        complete = target / 'handoff-installation/complete.json'
        assert complete.exists() == (name == 'success')
        if name == 'success':
            document = json.loads(complete.read_text())
            assert document['host_role'] == 'db' and document['phase'] == 'installed_not_activated'
            assert document['system_python_only'] and document['native_tests'] == 'passed_no_skips'
            assert stat.S_IMODE((target/'backend/admission_purge.py').stat().st_mode) == 0o644
            assert stat.S_IMODE((target/'deploy/scripts/manage-admission-purge.sh').stat().st_mode) == 0o755
            assert stat.S_IMODE(complete.stat().st_mode) == 0o600
        if name in ('low_space', 'lock_busy', 'lock_symlink', 'lock_mode'):
            assert not target.exists()
        if held is not None:
            os.close(held); held = None
        if name != 'lock_symlink':
            for lock_name in LOCK_NAMES:
                fd = os.open('/run/lock/' + lock_name, os.O_RDWR)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
    finally:
        if held is not None:
            os.close(held)
        os.umask(old_umask)


def main():
    assert sys.platform == 'linux' and os.geteuid() == 0
    results = []
    for name in ('success', 'existing_target', 'lock_busy', 'lock_symlink', 'lock_mode',
                 'low_space', 'space_dropped', 'changed_policy', 'unit_failure'):
        root = Path(tempfile.mkdtemp(prefix='ct-db-installer-synthetic-'))
        try:
            hashes = fixture(root)
            pid = os.fork()
            if pid == 0:
                try:
                    os.chroot(root); os.chdir('/')
                    scenario(name, hashes)
                    os._exit(0)
                except BaseException as error:
                    print(json.dumps({'fixture': name, 'error_class': type(error).__name__}), flush=True)
                    os._exit(1)
            _, status = os.waitpid(pid, 0)
            assert os.waitstatus_to_exitcode(status) == 0, name
            results.append(name)
        finally:
            assert root.parent == Path(tempfile.gettempdir()) and root.name.startswith('ct-db-installer-synthetic-')
            shutil.rmtree(root)
    print(json.dumps({'result': 'pass', 'native_synthetic_cases': results,
                      'host_actions': False, 'network': False,
                      'limits': 'SQL/systemd/native-suite responses are fixtures; actual Linux filesystem/flock/chroot semantics tested.'}))


if __name__ == '__main__':
    main()
