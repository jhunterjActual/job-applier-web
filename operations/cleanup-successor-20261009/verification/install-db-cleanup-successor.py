"""Prepared additive DB-only installer. No activation, credentials or WEB actions.

Arguments: successor revision, archive SHA256, reviewed manifest SHA256.
The immutable upload is /var/tmp/careertrellis-cleanup-<revision>.tar and its
reviewed manifest is /var/tmp/careertrellis-cleanup-<revision>.manifest.json.
Existing/incomplete targets refuse replay and remain for attended reconciliation.
"""
import hashlib
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

RELEASES = Path('/opt/careertrellis/staging/releases')
FLOOR = 5 * 1024**3
LIMIT = 32 * 1024**2
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
       'CAREERTRELLIS_CONFIG_READ_ONLY': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
KINDS = ('admission-purge', 'admission-purge-monitor', 'admission-reporter',
         'totp-cleanup', 'totp-cleanup-monitor')
MODULES = ('admission_operations', 'admission_purge', 'admission_reporter',
           'admission_alert_contract', 'admission_healthchecks', 'identity_entry',
           'identity_evidence', 'identity_operator', 'session_authorization',
           'totp_cleanup', 'totp_cleanup_operations', 'totp_cleanup_alert_contract',
           'monitoring_rebind')
REQUIRED = {*(f'backend/{name}.py' for name in MODULES),
            *(f'deploy/scripts/manage-{name}.sh' for name in
              ('admission-purge', 'admission-reporter', 'totp-cleanup', 'monitoring-rebind')),
            *(f'deploy/systemd/careertrellis-{name}@.{suffix}' for name in KINDS
              for suffix in ('service', 'timer'))}
TESTS = ('test_admission_purge', 'test_totp_cleanup_operations',
         'test_admission_reporter', 'test_admission_alert_contract',
         'test_admission_healthchecks', 'test_monitoring_rebind')


@contextmanager
def host_locks():
    import fcntl
    fds = []
    try:
        for name, mode in (('/run', 0o755), ('/run/lock', 0o1777)):
            item = Path(name).lstat()
            require(stat.S_ISDIR(item.st_mode) and item.st_uid == 0
                    and item.st_gid == 0 and stat.S_IMODE(item.st_mode) == mode)
        for name in ('careertrellis-maintenance.lock', 'careertrellis-postgres-migration.lock'):
            path = Path('/run/lock') / name
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            fds.append(fd)
            item = os.fstat(fd); visible = path.lstat()
            require(stat.S_ISREG(item.st_mode) and item.st_uid == item.st_gid == 0
                    and item.st_nlink == 1 and stat.S_IMODE(item.st_mode) == 0o600
                    and (visible.st_dev, visible.st_ino) == (item.st_dev, item.st_ino))
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def continuity():
            for name, fd in zip(('careertrellis-maintenance.lock', 'careertrellis-postgres-migration.lock'), fds):
                item = os.fstat(fd); visible = (Path('/run/lock') / name).lstat()
                require(item.st_nlink == 1 and (visible.st_dev, visible.st_ino) == (item.st_dev, item.st_ino))
        yield continuity
        continuity()
    finally:
        for fd in reversed(fds):
            os.close(fd)


class Refused(RuntimeError):
    pass


def require(value):
    if not value:
        raise Refused('installation_guard_refused')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out)
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs)


def protected(path, owners=(0,), limit=LIMIT):
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in owners
                and not info.st_mode & 0o022)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid in owners
                and info.st_nlink == 1 and not info.st_mode & 0o022
                and 0 < info.st_size <= limit)
        raw = os.read(fd, limit + 1)
        require(len(raw) == info.st_size)
        return raw
    finally:
        os.close(fd)


def uploaded(path, expected, limit):
    # /var/tmp is a fixed sticky directory, not a protected ancestor tree.
    for parent in (Path('/'), Path('/var')):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    info = Path('/var/tmp').lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o1777 and path.parent == Path('/var/tmp'))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and not info.st_mode & 0o022 and 0 < info.st_size <= limit)
        raw = os.read(fd, limit + 1)
        require(len(raw) == info.st_size and digest(raw) == expected)
        return raw
    finally:
        os.close(fd)


def archive_files(raw, manifest):
    require(isinstance(manifest, dict) and 1 <= len(manifest) <= 256
            and REQUIRED <= set(manifest)
            and all('backend/' + name + '.py' in manifest for name in TESTS))
    result = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for member in archive:
            name = member.name
            path = PurePosixPath(name)
            require(member.isfile() and not member.issym() and not member.islnk()
                    and 0 <= member.size <= 1024**2 and not member.mode & 0o022
                    and not path.is_absolute() and path.as_posix() == name
                    and '\\' not in name and ':' not in name
                    and all(part not in ('', '.', '..', 'venv', 'data', '__pycache__')
                            and not part.startswith('.') for part in path.parts)
                    and name.startswith(('backend/', 'deploy/scripts/', 'deploy/systemd/'))
                    and name.endswith(('.py', '.sh', '.service', '.timer'))
                    and name not in result and name in manifest
                    and re.fullmatch('[0-9a-f]{64}', manifest[name]))
            payload = archive.extractfile(member).read(1024**2 + 1)
            require(len(payload) == member.size and digest(payload) == manifest[name])
            result[name] = payload
    require(set(result) == set(manifest) and sum(map(len, result.values())) <= LIMIT)
    return result


def command(argv, data=None, timeout=10, env=None):
    proc = subprocess.run(argv, input=data, capture_output=True, env=env or ENV, timeout=timeout)
    require(proc.returncode == 0 and len(proc.stdout) <= 65536)
    return proc.stdout


def validate_database(value):
    require(value['database'] == 'careertrellis' and value['user'] == 'postgres'
            and 180000 <= int(value['version']) < 190000
            and value['data'] == '/var/lib/postgresql/18/main'
            and value['heads'] == ['20261005_0023'] and len(value['policies']) == 1)
    policy = value['policies'][0]
    require(policy['minimum_session_version'] == 2 and policy['maintenance'] is False
            and policy['signup_open'] is True and policy['adapter_ready'] is True)
    require(set(value['roles']) == {'careertrellis_runtime', 'careertrellis_worker',
                                  'careertrellis_erasure_worker', 'careertrellis_admission_broker'}
            and all(role['rolcanlogin'] is True for role in value['roles'].values()))


def snapshot():
    import pwd
    owner = pwd.getpwnam('postgres').pw_uid
    base = Path('/etc/postgresql/18/main')
    files = {name: digest(protected(base / name, owners=(0, owner)))
             for name in ('postgresql.conf', 'pg_hba.conf', 'pg_ident.conf')}
    state = command(['/usr/bin/systemctl', 'show', 'postgresql@18-main.service',
                     '--property=ActiveState,SubState,MainPID,NRestarts,Job,FragmentPath,DropInPaths,ExecStart'])
    props = dict(line.split('=', 1) for line in state.decode().splitlines())
    require(props['ActiveState'] == 'active' and props['SubState'] == 'running'
            and int(props['MainPID']) > 0 and props['Job'] == '')
    for name in (props['FragmentPath'], *props['DropInPaths'].split()):
        require(name.startswith(('/etc/systemd/system/', '/usr/lib/systemd/system/',
                                 '/lib/systemd/system/')))
        # Resolve the public /lib -> /usr/lib system alias, then enforce owners.
        path = Path(name).resolve(strict=True)
        require(str(path).startswith(('/etc/systemd/system/', '/usr/lib/systemd/system/')))
        files['unit:' + name] = digest(protected(path))
    query = """BEGIN READ ONLY;
SET LOCAL statement_timeout='3s'; SET LOCAL lock_timeout='250ms';
SET LOCAL transaction_timeout='5s'; SET LOCAL search_path=pg_catalog;
SELECT json_build_object('database',current_database(),'user',session_user,
 'version',current_setting('server_version_num'),'data',current_setting('data_directory'),
 'heads',(SELECT json_agg(version_num ORDER BY version_num) FROM careertrellis.alembic_version),
 'policies',(SELECT json_agg(p) FROM careertrellis_admission.policy p),
 'roles',(SELECT json_object_agg(rolname,json_build_object('rolcanlogin',rolcanlogin,
 'rolsuper',rolsuper,'rolcreatedb',rolcreatedb,'rolcreaterole',rolcreaterole,
 'rolinherit',rolinherit,'rolreplication',rolreplication,'rolbypassrls',rolbypassrls))
 FROM pg_roles WHERE rolname IN ('careertrellis_runtime','careertrellis_worker',
 'careertrellis_erasure_worker','careertrellis_admission_broker')));
ROLLBACK;
"""
    raw = command(['/usr/sbin/runuser', '-u', 'postgres', '--', '/usr/bin/psql',
                   '-X', '-qAt', '-w', '-v', 'ON_ERROR_STOP=1', '-h', '/var/run/postgresql',
                   '-p', '5432', '-d', 'careertrellis'], query.encode(), timeout=7,
                  env={**ENV, 'PGCONNECT_TIMEOUT': '2', 'PGOPTIONS': '-c default_transaction_read_only=on'})
    value = strict_json(raw)
    validate_database(value)
    return {'configuration': files, 'service': state.decode(), 'database': value}


def record(path, value):
    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw); stream.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(path.parent)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_files(target, manifest):
    for name, expected in manifest.items():
        require(digest(protected(target / name)) == expected)


def install_locked(revision, archive_hash, manifest_hash, continuity):
    require(os.geteuid() == 0 and os.uname().nodename == 'job-applier-db-staging-01')
    require(re.fullmatch('[0-9a-f]{40}', revision)
            and all(re.fullmatch('[0-9a-f]{64}', value) for value in (archive_hash, manifest_hash)))
    for parent in (RELEASES, *RELEASES.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    target = RELEASES / revision
    require(not target.exists() and not target.is_symlink())
    prefix = '/var/tmp/careertrellis-cleanup-' + revision
    raw = uploaded(Path(prefix + '.tar'), archive_hash, LIMIT)
    manifest = strict_json(uploaded(Path(prefix + '.manifest.json'), manifest_hash, 65536))
    files = archive_files(raw, manifest)
    require(shutil.disk_usage(RELEASES).free >= FLOOR + sum(map(len, files.values())) + 16 * 1024**2)
    before = snapshot()
    target.mkdir(mode=0o755)  # Exclusive durable failure boundary: never adopt an old target.
    sync_directory(RELEASES)
    evidence = target / 'handoff-installation'
    evidence.mkdir(mode=0o700)
    record(evidence / 'started.json', {'commit': revision, 'archive_sha256': archive_hash,
           'manifest_sha256': manifest_hash, 'phase': 'started', 'host_role': 'db'})
    for name, payload in files.items():
        path = target / name
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o755 if name.endswith('.sh') else 0o644)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    verify_files(target, manifest)
    for path in sorted((p for p in target.rglob('*') if p.is_dir()),
                       key=lambda p: len(p.parts), reverse=True):
        sync_directory(path)
    sync_directory(target)
    record(evidence / 'source-manifest.json', manifest)
    program = "import sys,unittest; sys.path.insert(0,sys.argv[1]); s=unittest.defaultTestLoader.loadTestsFromNames(" + repr(TESTS) + "); r=unittest.TextTestRunner().run(s); sys.exit(not r.wasSuccessful() or bool(r.skipped) or r.testsRun==0)"
    result = subprocess.run(['/usr/bin/python3', '-I', '-S', '-B', '-c', program, str(target / 'backend')],
                            capture_output=True, env=ENV, timeout=180)
    record(evidence / 'native-tests.json', {'returncode': result.returncode,
           'stdout': result.stdout.decode(errors='replace'), 'stderr': result.stderr.decode(errors='replace')})
    require(result.returncode == 0)
    for name in files:
        if name.endswith('.sh'):
            command(['/bin/bash', '-n', str(target / name)])
    verify_files(target, manifest)
    require(snapshot() == before and shutil.disk_usage(RELEASES).free >= FLOOR)
    continuity()
    record(evidence / 'complete.json', {'commit': revision, 'archive_sha256': archive_hash,
           'manifest_sha256': manifest_hash, 'source_files': len(manifest),
           'phase': 'installed_not_activated', 'host_role': 'db', 'system_python_only': True,
           'native_tests': 'passed_no_skips', 'database_configuration_and_service_unchanged': True,
           'completed_at': datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'outcome': 'installed_not_activated', 'commit': revision,
                      'source_files': len(manifest)}))


def install(revision, archive_hash, manifest_hash):
    require(os.geteuid() == 0 and os.uname().nodename == 'job-applier-db-staging-01')
    with host_locks() as continuity:
        previous_umask = os.umask(0o022)
        try:
            install_locked(revision, archive_hash, manifest_hash, continuity)
        finally:
            os.umask(previous_umask)


if __name__ == '__main__':
    try:
        require(len(sys.argv) == 4)
        install(*sys.argv[1:])
    except Exception:
        print('{"outcome":"installation_unconfirmed_reconcile_no_retry"}')
        raise SystemExit(1)
