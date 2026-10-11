#!/usr/bin/python3
"""Bounded weekly ciphertext restore rehearsal. Never deletes retained runs."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

CAPTURES = Path('/var/lib/careertrellis/generated-object-captures')
STATE = Path('/var/lib/careertrellis/object-prefix-rehearsals')
RELEASES = Path('/opt/careertrellis/staging/releases')
CORE = Path('/usr/local/libexec/careertrellis/generated_object_recovery.py')
WRAPPER = Path('/usr/local/sbin/careertrellis-test-generated-object-prefix-restore')
TARGET_ENV = '/etc/careertrellis/generated-object-restore-20260817.env'
MAX_RUNS = 12
MAX_RESERVED = 256 * 1024**2
MAX_SNAPSHOT = 32 * 1024**2
MAX_AGE = 35 * 86400
MIN_INTERVAL = 86400
IDENTITY = re.compile(r'[0-9a-f]{40}-[0-9]{8}-[0-9]{6}F')
RUN = re.compile(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}')


def require(condition):
    if not condition:
        raise RuntimeError('rehearsal preflight failed')


def protected(path, mode=None, group=None, directory=False):
    for ancestor in [*reversed(path.parents), path]:
        st = ancestor.lstat()
        require(not stat.S_ISLNK(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022)
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode))
    if not directory:
        require(st.st_nlink == 1)
    require(mode is None or stat.S_IMODE(st.st_mode) == mode)
    require(group is None or st.st_gid == group)


def read(path, mode=0o600, group=0, limit=8192):
    protected(path, mode, group)
    require(path.stat().st_size <= limit)
    return path.read_bytes()


def validate_seal(seal, raw, parser, now):
    manifest = parser(json.loads(raw))
    require(raw == manifest.serialized())
    start, end = seal.get('capture_started_epoch'), seal.get('captured_epoch')
    require(type(start) is int and type(end) is int and 0 < start <= end <= now)
    expected = dict(manifest.sanitized_summary(), authority='postgres-r2-authoritative',
                    capture_started_epoch=start, captured_epoch=end)
    require(seal == expected and 0 < manifest.ciphertext_bytes <= MAX_SNAPSHOT)
    require(manifest.snapshot_id == manifest.release + '-' + manifest.backup_label)
    return seal


def choose_latest(items):
    require(bool(items))
    items = sorted(items, key=lambda value: value['captured_epoch'], reverse=True)
    require(len(items) == 1 or items[0]['captured_epoch'] != items[1]['captured_epoch'])
    return items[0]


def select_capture(parser, now, group):
    protected(CAPTURES, directory=True)
    candidates = []
    entries = list(CAPTURES.iterdir())
    require(len(entries) <= 128)
    for path in entries:
        require(IDENTITY.fullmatch(path.name) is not None)
        protected(path, 0o750, group, directory=True)
        seal = json.loads(read(path / 'seal.json'))
        # Old valid captures remain retained; never silently ignore malformed/future seals.
        raw = read(path / 'marker' / 'snapshot-manifest.json', 0o640, group, 32*1024**2)
        validated = validate_seal(seal, raw, parser, now)
        require(validated['snapshot_id'] == path.name)
        candidates.append(validated)
    selected = choose_latest(candidates)
    require(now - selected['capture_started_epoch'] <= MAX_AGE)
    protected(RELEASES / selected['release'], directory=True)
    return selected


def write_new(path, value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def journal_summary(now):
    runs, reserved, latest = 0, 0, 0
    for path in STATE.iterdir():
        if path.name == 'coordinator.lock':
            continue
        require(RUN.fullmatch(path.name) is not None)
        protected(path, 0o700, 0, directory=True)
        require({p.name for p in path.iterdir()} <= {'intent.json', 'result.json'})
        intent = json.loads(read(path / 'intent.json'))
        require(intent['prefix'] == 'rehearsals/' + path.name)
        size = intent['reserved_bytes']
        require(type(size) is int and 0 < size <= MAX_SNAPSHOT)
        reserved += size
        runs += 1
        if (path / 'result.json').exists():
            result = json.loads(read(path / 'result.json'))
            require(result['prefix'] == intent['prefix'])
            ended = result['completed_epoch']
            require(type(ended) is int and 0 < ended <= now)
            if result['result'] == 'verified' and result['wrapper_exit_code'] == 0:
                latest = max(latest, ended)
    return runs, reserved, latest


def capacity(runs, reserved, size, free):
    require(runs < MAX_RUNS and reserved + size <= MAX_RESERVED)
    require(free > 3*1024**3 + 2*size + 64*1024**2)


def verified_output(output, selected, prefix, code):
    return (code == 0 and all(output.get(k) == selected[k] for k in
        ('release', 'backup_label', 'manifest_sha256', 'snapshot_id'))
        and output.get('target_prefix') == prefix
        and output.get('result') == 'disposable_r2_restore_verified_and_left_intact')


def main():
    import fcntl
    import grp
    require(os.geteuid() == 0 and len(sys.argv) == 1)
    os.umask(0o077)
    protected(STATE, 0o700, 0, directory=True)
    lock = os.open(STATE / 'coordinator.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    protected(STATE / 'coordinator.lock', 0o600, 0)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = int(time.time())
        runs, reserved, latest = journal_summary(now)
        if latest and now - latest < MIN_INTERVAL:
            print('{"result":"recent_verified_run_skipped"}')
            return
        protected(CORE, 0o644, 0)
        protected(WRAPPER, 0o755, 0)
        protected(Path(TARGET_ENV), 0o600, 0)
        spec = importlib.util.spec_from_file_location('generated_object_recovery', CORE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        group = grp.getgrnam('careertrellis-object-recovery').gr_gid
        selected = select_capture(module._manifest_from_document, now, group)
        capacity(runs, reserved, selected['ciphertext_bytes'], shutil.disk_usage(STATE).free)
        name = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex
        prefix = 'rehearsals/' + name
        run = STATE / name
        run.mkdir(mode=0o700)
        write_new(run / 'intent.json', {'prefix': prefix, 'started_epoch': now,
            'reserved_bytes': selected['ciphertext_bytes'], 'release': selected['release'],
            'backup_label': selected['backup_label'], 'manifest_sha256': selected['manifest_sha256']})
        arguments = [str(WRAPPER), str(RELEASES / selected['release']), selected['backup_label'],
            selected['manifest_sha256'], TARGET_ENV,
            'I_HAVE_VERIFIED_THE_R2_PREFIX_IS_DISPOSABLE_AND_EMPTY',
            'I_UNDERSTAND_THE_DISPOSABLE_R2_TARGET_WILL_BE_LEFT_INTACT', prefix]
        # No raw transfer diagnostics enter the journal. Wrapper publishes canonical health evidence.
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=3600,
                                env={'PATH':'/usr/local/bin:/usr/bin:/bin', 'LANG':'C.UTF-8'})
        output = json.loads(result.stdout) if result.returncode == 0 else {}
        valid = verified_output(output, selected, prefix, result.returncode)
        receipt = {'prefix': prefix, 'completed_epoch': int(time.time()),
                   'result': 'verified' if valid else 'failed', 'wrapper_exit_code': result.returncode}
        write_new(run / 'result.json', receipt)
        require(valid)
        print(json.dumps(receipt, sort_keys=True))
    finally:
        os.close(lock)


def publish_failure(started):
    core = Path('/usr/local/libexec/careertrellis/backup_health.py')
    receipts = Path('/var/backups/careertrellis/backup-health/operations')
    protected(core, 0o644, 0)
    protected(receipts, 0o700, 0, directory=True)
    ended = int(time.time())
    require(ended >= started)
    stamp = lambda value: datetime.datetime.fromtimestamp(value, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    receipt = {'schema':'careertrellis.backup-health-operation/v1',
        'operation':'generated_object_restore', 'result':'failed',
        'started_at':stamp(started), 'completed_at':stamp(ended),
        'elapsed_seconds':ended-started, 'object_count':0, 'byte_count':0,
        'manifest_verified':False, 'ciphertext_verified':False, 'target_left_intact':False}
    subprocess.run(['/usr/bin/python3', '-B', str(core), 'publish', '--receipt-root', str(receipts)],
        input=json.dumps(receipt), text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=20, check=True, env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'})


if __name__ == '__main__':
    started = int(time.time())
    try:
        main()
    except Exception:
        try:
            publish_failure(started)
        except Exception:
            print('Failure health receipt could not be published.', file=sys.stderr)
        print('Object prefix rehearsal failed; inspect protected run state.', file=sys.stderr)
        sys.exit(1)
