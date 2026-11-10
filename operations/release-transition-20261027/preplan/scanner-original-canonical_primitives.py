"""Review candidate, not deployment permission. Default: read-only preflight.

Never runs migrations, installs helpers, writes authority, restores configuration,
or retargets current to an older release. Before any pointer switch, a refusal
restarts the unchanged baseline only. After a switch, a caught failure attempts
ONLY the separately reviewed NEW forward-correction release. An interruption or
failed correction requires inspection; the claim is never replayed.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request

BASE = '7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS = '4095417909a8b13ad252be9420e422af3662bef5'
ROOT = Path('/opt/careertrellis/staging')
REPOSITORY = '/srv/careertrellis/job-applier-web'
SERVICES = ('careertrellis-web', 'careertrellis-worker', 'careertrellis-erasure-worker')
MARKERS = ('/run/careertrellis-hosted-activation.lease',
           '/var/lib/careertrellis/pilot-auth-acceptance/active.json')
LOCKS = ('/run/lock/careertrellis-maintenance.lock',
         '/run/lock/careertrellis-pilot-auth-acceptance.lock')
ACTIVE = ('preparing', 'queued', 'running', 'cancel_requested')
STATE = ROOT / 'role-family-release-coordinator-20261017-v2'
_RECOVERING = False


class Refused(RuntimeError):
    pass


def require(ok):
    if not ok:
        raise Refused('Guard refused; inspect privately; do not replay.')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def controlled_signals():
    """Restore caller handlers; repeated HUP/TERM cannot interrupt recovery.

    Each recovery operation already has its own bound. SIGKILL/power loss remain
    outside Python recovery and always require manual inspection.
    """
    previous = {}
    def interrupted(signum, frame):
        if not _RECOVERING:
            raise Refused('Coordinator interrupted; inspect retained state.')
    try:
        for name in ('SIGHUP', 'SIGTERM'):
            if hasattr(signal, name):
                number = getattr(signal, name)
                previous[number] = signal.getsignal(number)
                signal.signal(number, interrupted)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def fresh(value, seconds):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(stamp.tzinfo is not None)
    require(0 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= seconds)


def validate_manifest(m):
    require(type(m) is dict and m.get('version') == 1)
    require(m.get('base') == BASE and m.get('previous') == PREVIOUS)
    for key in ('target', 'recovery'):
        require(isinstance(m.get(key), str) and re.fullmatch('[0-9a-f]{40}', m[key]))
        require(m[key] not in (BASE, PREVIOUS))
    require(m['target'] != m['recovery'])
    for key in ('base_build', 'target_build', 'recovery_build'):
        require(isinstance(m.get(key), str) and re.fullmatch(r'\d{8}\.\d+', m[key]))
    require(m['base_build'] == '20261016.94')
    require(m['target_build'] != m['recovery_build'])
    for key in ('probe_sha256', 'decoder_sha256', 'target_diff_sha256', 'recovery_diff_sha256'):
        require(isinstance(m.get(key), str) and re.fullmatch('[0-9a-f]{64}', m[key]))
    require(m.get('probe_path') == str(STATE.parent / 'title-release-reviewed-probe-20261010.py'))
    require(m.get('reviewed_new_forward_correction') is True)
    require(m.get('reviewed_single_producer_host') is True)
    proof = m['db_proof']
    require(proof['host'] == 'job-applier-db-staging-01')
    require(proof['target'] == m['target'] and proof['recovery'] == m['recovery'])
    require(proof['result'] == 'ok' and proof['exit_code'] == 0 and proof['failure_codes'] == [])
    fresh(proof['checked_at'], 600)
    fresh(proof['evaluated_at'], 10800)


def validate_probe(p, m, revision, stage):
    # Fixed privacy-safe contract. The separate implementation must be reviewed,
    # read-only and globally visible (RLS bypass verified) BEFORE supplying it.
    require(p['version'] == 1 and p['revision'] == revision and p['stage'] == stage)
    fresh(p['observed_at'], 30)
    for key in ('read_only', 'identity_bound', 'configuration_stable', 'policy_open_beta'):
        require(p[key] is True)
    require(p['schema'] == '20261005_0023')
    require(p['policy'] == {'maintenance': False, 'signup_open': True, 'phase': 'beta',
                            'cap': 25, 'minimum_session_version': 2})
    if stage in ('stopped', 'recovery'):
        require(p['known_producers_stopped'] is True)
    health = p['object_backup']
    require(health['exit_code'] == 0 and health['result'] == 'ok' and health['failure_codes'] == [])
    fresh(health['evaluated_at'], 10800)


def validate_audit(p, m, nonce, since):
    require(type(p.get('audit_version')) is int and p['audit_version'] == 2)
    require(p['request_nonce'] == nonce and p['target'] == m['target'] and p['recovery'] == m['recovery'])
    require(p['host'] == 'job-applier-db-staging-01')
    fresh(p['observed_at_utc'], 120)
    stamp = datetime.fromisoformat(p['observed_at_utc'].replace('Z', '+00:00'))
    require(stamp >= since)
    meta = p['metadata']
    require(meta['database_expected'] is True and meta['global_visibility'] is True)
    require(meta['read_only'] == 'on' and meta['isolation'] == 'repeatable read')
    require(meta['schema_revision'] == '20261005_0023')
    require(type(p['active_search_rows']) is int and p['active_search_rows'] == 0)
    require(type(p['active_erasure_rows']) is int and p['active_erasure_rows'] == 0)
    for key in ('due_saved_search_rows', 'near_due_saved_search_rows'):
        require(type(p[key]) is int and p[key] == 0)
    required = {'saved_searches': ('total_rows', 'marker_rows', 'exact_prefix_rows', 'oversized_rows',
                                'enabled_marker_rows', 'scheduled_marker_rows'),
                'search_runs': ('total_rows', 'marker_rows', 'exact_prefix_rows', 'oversized_rows',
                                'active_marker_rows', 'terminal_marker_rows', 'unknown_status_rows',
                                'approved_terminal_envelope_rows')}
    for table, fields in required.items():
        row = p['tables'][table]
        require(all(type(row.get(k)) is int and row[k] >= 0 for k in fields))
        allowed = {'marker_rows', 'exact_prefix_rows', 'terminal_marker_rows',
                   'approved_terminal_envelope_rows'} if table == 'search_runs' else set()
        require(not allowed or row['marker_rows'] in (0, 1))
        require(all(row[k] == row['marker_rows'] for k in allowed))
        require(all(row[k] == 0 for k in fields if k != 'total_rows' and k not in allowed))
        require(row['total_rows'] >= row['marker_rows'])


def coordinate(host, m, activate=False):
    global _RECOVERING
    validate_manifest(m)
    with host.locks():
        host.baseline(m)
        preserved = host.snapshot()
        host.source(BASE, None, m['base_build'])
        host.authority(BASE)
        for key in ('target', 'recovery'):
            host.source(m[key], m['decoder_sha256'], m[key + '_build'], m[key + '_diff_sha256'])
            host.authority(m[key])
            validate_probe(host.probe(m, m[key], 'preflight'), m, m[key], 'preflight')
        host.health(m['base_build'])
        # Read-only preflight requires a fresh, global DB-host snapshot. It is not
        # substituted for the second receipt obtained after producers are stopped.
        host.audit(m, 'preflight')
        require(host.snapshot() == preserved)
        if not activate:
            return {'status': 'preflight_passed_no_changes', 'activation_authorized': False}
        host.claim(m)
        try:
            host.stop()
            host.markers_clear()
            validate_manifest(m)  # Backup proof cannot expire during preparation.
            for key in ('target', 'recovery'):
                host.source(m[key], m['decoder_sha256'], m[key + '_build'], m[key + '_diff_sha256'])
                host.authority(m[key])
                validate_probe(host.probe(m, m[key], 'stopped'), m, m[key], 'stopped')
            require(host.snapshot() == preserved)
            host.phase('stopped')
            host.audit(m, 'stopped')
            host.markers_clear()
            require(host.snapshot() == preserved)
            host.point(m['target'], expected=BASE)
            host.phase('target_selected')
            host.start(m['target_build'])
            require(host.snapshot() == preserved)
            validate_probe(host.probe(m, m['target'], 'running'), m, m['target'], 'running')
            host.phase('complete')
            return {'status': 'target_healthy', 'configuration_unchanged': True}
        except BaseException:
            # A refused audit must never introduce new decoding behavior. Restart
            # unchanged BASE only when the pointer is still exactly BASE. This is
            # not a rollback: no pointer write occurs. Unknown pointers are contained.
            previous_recovery = _RECOVERING
            _RECOVERING = True
            try:
                host.stop()
                host.markers_clear()
                require(host.snapshot() == preserved)
                current = host.pointer('current')
                if current == BASE:
                    host.source(BASE, None, m['base_build'])
                    host.authority(BASE)
                    host.start(m['base_build'])
                    require(host.snapshot() == preserved)
                    host.phase('aborted_before_switch')
                    raise PreSwitchAbort()
                require(current == m['target'])
                validate_manifest(m)
                host.source(m['recovery'], m['decoder_sha256'], m['recovery_build'], m['recovery_diff_sha256'])
                host.authority(m['recovery'])
                validate_probe(host.probe(m, m['recovery'], 'recovery'), m, m['recovery'], 'recovery')
                host.point(m['recovery'], expected=(BASE, m['target']))
                host.phase('correction_selected')
                host.start(m['recovery_build'])
                require(host.snapshot() == preserved)
                validate_probe(host.probe(m, m['recovery'], 'running'), m, m['recovery'], 'running')
                host.phase('corrected')
            except PreSwitchAbort:
                pass
            except BaseException:
                # Even if the second stop fails, continue attempting all services.
                host.contain()
                host.phase('contained_inspection_required')
            finally:
                _RECOVERING = previous_recovery
            raise Refused('Activation failed; inspect claim and service state; no replay.') from None


class PreSwitchAbort(Exception):
    """Internal sentinel after verified restart of an unchanged current pointer."""


class LinuxHost:
    def run(self, *args, timeout=60):
        p = subprocess.run(args, capture_output=True, timeout=timeout,
                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                                'PYTHONDONTWRITEBYTECODE': '1'})
        require(p.returncode == 0)
        return p.stdout

    def protected(self, path):
        path = Path(path)
        require(path.is_absolute())
        for parent in path.parents:
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and not info.st_mode & 0o022 and 0 <= info.st_size <= 64 * 1024**2)
            fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
                      'st_size', 'st_mtime_ns', 'st_ctime_ns')
            before = tuple(getattr(info, key) for key in fields)
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                # Allocate for the observed file, not the maximum allowed file.
                # The extra byte detects growth; exact length detects truncation.
                raw = stream.read(info.st_size + 1)
            require(len(raw) == info.st_size)
            after = os.fstat(fd)
            require(tuple(getattr(after, key) for key in fields) == before)
            # Reject replacement/unlink/symlink changes at the original name too.
            named = path.lstat()
            require(tuple(getattr(named, key) for key in fields) == before)
            return raw
        finally:
            os.close(fd)

    @contextmanager
    def locks(self):
        import fcntl
        require(os.geteuid() == 0 and socket.gethostname() == 'careertrellis-staging-web-1')
        descriptors = []
        try:
            for name in LOCKS:
                fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW)
                descriptors.append(fd)
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                        and not info.st_mode & 0o022)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.markers_clear()
            yield
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def markers_clear(self):
        require(not any(os.path.lexists(p) for p in MARKERS))

    def pointer(self, name):
        p = ROOT / name
        require(p.is_symlink() and p.lstat().st_uid == 0)
        resolved = p.resolve(strict=True)
        require(resolved.parent == ROOT / 'releases')
        return resolved.name

    def baseline(self, m):
        require(not os.path.lexists(STATE))
        require(not os.path.lexists(ROOT / 'role-family-release-coordinator-20261017-initial'))
        require(self.pointer('current') == BASE and self.pointer('previous') == PREVIOUS)
        require(shutil.disk_usage(ROOT).free >= 5 * 1024**3)
        require(self.run('/usr/local/sbin/careertrellis-authority', 'status').decode().strip()
                == 'postgres-r2-authoritative')
        require(all(self.state(s)['ActiveState'] == 'active' for s in SERVICES))
        require(self.state('caddy')['ActiveState'] == 'active')

    def source(self, revision, decoder, build, reviewed_diff=None):
        release = ROOT / 'releases' / revision
        # Git archive names and bytes are checked without extraction or writes.
        require(self.run('git', '-C', REPOSITORY, 'rev-parse', revision + '^{commit}').decode().strip() == revision)
        archive = self.run('git', '-C', REPOSITORY, 'archive', revision)
        require(len(archive) <= 128 * 1024**2)
        expected_files = set()
        with tarfile.open(fileobj=io.BytesIO(archive)) as entries:
            for entry in entries:
                name = PurePosixPath(entry.name)
                require(not name.is_absolute() and '..' not in name.parts)
                if entry.isdir():
                    continue
                require(entry.isfile())
                expected_files.add(entry.name)
                require(self.protected(release / entry.name) == entries.extractfile(entry).read())
        # A matching tracked tree alone does not exclude injected importable code.
        # Prepared venv and Python caches are separate reviewed build inputs.
        for folder, dirs, files in os.walk(release, followlinks=False):
            relative = Path(folder).relative_to(release)
            dirs[:] = [d for d in dirs if str(relative / d).replace(os.sep, '/') != 'backend/venv'
                       and d != '__pycache__']
            for directory in dirs:
                require(not (Path(folder) / directory).is_symlink())
            for name in files:
                path = Path(folder) / name
                rel = path.relative_to(release).as_posix()
                require(rel in expected_files)
        require(not self.run('git', '-C', REPOSITORY, 'diff', BASE, revision, '--',
                             'backend/migrations', 'deploy/postgres'))
        if decoder is not None:
            require(digest(self.protected(release / 'backend/role_queries.py')) == decoder)
        version = self.protected(release / 'backend/version.py').decode('utf-8')
        require(re.search(r"(?m)^APP_BUILD\s*=\s*['\"]" + re.escape(build) + r"['\"]", version))
        # Approved squash/catch-up history need not preserve ancestry. Bind the
        # actual complete reviewed source delta; do not fabricate a merge parent.
        if revision != BASE:
            require(reviewed_diff is not None)
            delta = self.run('git', '-C', REPOSITORY, '-c', 'core.abbrev=40', 'diff', '--binary', '--no-ext-diff',
                             '--no-textconv', BASE, revision, '--')
            require(digest(delta) == reviewed_diff)

    def authority(self, revision):
        self.run('/usr/local/sbin/careertrellis-authority', 'assert-release-startable',
                 str(ROOT / 'releases' / revision))

    def probe(self, m, revision, stage):
        require(digest(self.protected(m['probe_path'])) == m['probe_sha256'])
        return json.loads(self.run(str(ROOT / 'releases' / revision / 'backend/venv/bin/python'),
                                   '-I', '-B', m['probe_path'], '--revision', revision,
                                   '--stage', stage, timeout=45))

    def audit(self, m, stage):
        # Persistently invoked coordinator owns stdin. No shell/source payload is
        # accepted. A separate authorized DB-host read-only probe returns this one
        # nonce-bound JSON receipt while both local locks remain held.
        nonce = secrets.token_hex(32)
        since = datetime.now(timezone.utc)
        print(json.dumps({'phase': 'db_audit_requested', 'stage': stage,
                          'request_nonce': nonce, 'target': m['target'], 'recovery': m['recovery'],
                          'requested_at': since.isoformat(), 'timeout_seconds': 120}), flush=True)
        deadline, raw = time.monotonic() + 120, bytearray()
        while b'\n' not in raw:
            remaining = deadline - time.monotonic()
            require(remaining > 0)
            require(bool(select.select([sys.stdin.fileno()], [], [], remaining)[0]))
            block = os.read(sys.stdin.fileno(), 4096)
            require(bool(block))
            raw.extend(block)
            require(len(raw) <= 16384)
        line, trailing = bytes(raw).split(b'\n', 1)
        require(not trailing.strip())
        validate_audit(json.loads(line), m, nonce, since)
        if stage == 'stopped':
            require(all(self.state(s)['ActiveState'] == 'inactive' for s in SERVICES))

    def snapshot(self):
        paths = [Path('/etc/caddy/Caddyfile'), Path('/etc/caddy/careertrellis-staging.env')]
        paths += list(Path('/etc/careertrellis').rglob('*'))
        paths += list(Path('/usr/local/sbin').glob('careertrellis-*'))
        paths += list(Path('/usr/local/libexec/careertrellis').rglob('*'))
        for pattern in ('careertrellis-*.service', 'careertrellis-*.timer',
                        'careertrellis-*.service.d/*.conf', 'caddy.service.d/*.conf'):
            paths += list(Path('/etc/systemd/system').glob(pattern))
        # Digests are process memory ONLY. No secret-derived fingerprint receipt.
        return {str(p): digest(self.protected(p)) for p in sorted(set(paths))
                if not p.is_dir() and '__pycache__' not in p.parts}

    def state(self, service):
        return dict(line.split('=', 1) for line in self.run('systemctl', 'show', service,
                    '--property=ActiveState,SubState,Result,ExecMainStatus,NRestarts,MainPID').decode().splitlines())

    def health(self, build):
        for suffix in ('live', 'ready'):
            with urllib.request.urlopen('http://127.0.0.1:8001/health/' + suffix, timeout=10) as r:
                value = json.load(r)
                require(r.status == 200 and value['build'] == build and
                        value['status'] == ('ok' if suffix == 'live' else 'ready'))

    def stop(self):
        errors = []
        for service in SERVICES:
            try:
                self.run('systemctl', 'stop', service)
                require(self.state(service)['ActiveState'] == 'inactive')
            except BaseException:
                errors.append(service)
        require(not errors)

    def contain(self):
        try:
            self.stop()
        except BaseException:
            pass  # Receipt explicitly requires manual inspection, not health claims.

    def start(self, build):
        for service in reversed(SERVICES):
            self.run('systemctl', 'start', service)
        deadline, stable, last = time.monotonic() + 60, None, None
        while time.monotonic() < deadline:
            try:
                self.health(build)
                states = [self.state(s) for s in SERVICES]
                require(all(s['ActiveState'] == 'active' and s['NRestarts'] == '0'
                            and int(s['MainPID']) > 0 for s in states))
                heartbeat = json.loads(Path('/var/lib/careertrellis/worker/worker-heartbeat.json').read_text())
                require(heartbeat['build'] == build and heartbeat['pid'] == int(states[1]['MainPID']))
                fresh(heartbeat['updated_at'], 30)
                pids = tuple(s['MainPID'] for s in states)
                if stable is None or pids != last:
                    stable, last = time.monotonic(), pids
                if time.monotonic() - stable >= 20:
                    return
            except Exception:
                stable = None
            time.sleep(1)
        raise Refused('Stable service verification failed.')

    def sync(self, path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def phase(self, name):
        fd = os.open(STATE / (name + '.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump({'phase': name, 'observed_at': datetime.now(timezone.utc).isoformat(),
                       **self.revisions}, output)
            output.flush()
            os.fsync(output.fileno())
        self.sync(STATE)

    def claim(self, m):
        self.revisions = {k: m[k] for k in ('base', 'previous', 'target', 'recovery')}
        STATE.mkdir(mode=0o700)
        self.sync(STATE.parent)
        self.phase('claimed')

    def point(self, revision, expected):
        require(revision not in (BASE, PREVIOUS))
        expected = (expected,) if isinstance(expected, str) else expected
        require(self.pointer('current') in expected and self.pointer('previous') == PREVIOUS)
        temporary = ROOT / ('.title-forward-' + revision)
        require(not os.path.lexists(temporary))
        os.symlink(str(ROOT / 'releases' / revision), temporary)
        self.sync(ROOT)
        os.replace(temporary, ROOT / 'current')
        self.sync(ROOT)
        # Previous deliberately remains historical; generic rollback is forbidden.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    host = LinuxHost()
    try:
        manifest = json.loads(host.protected(Path(args.manifest)))
        with controlled_signals():
            result = coordinate(host, manifest, args.activate)
        print(json.dumps(result, sort_keys=True))
        return 0
    except BaseException:
        print('{"status":"refused_or_failed_inspection_required","no_replay":true}')
        return 1


