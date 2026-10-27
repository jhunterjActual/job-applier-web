"""Fixed-host native recovery of a verified downloaded encrypted repository.

No provider calls, application changes, deletion, replay or credential prompts.
Run only as the dedicated finite native owner unit. Ambiguous execution leaves
durable incomplete evidence; systemd terminates only that disposable cgroup.
No new attempt or acceptance may rely on flock availability as terminal proof.
Raw subprocess output is either privately parsed or discarded, never published.
"""
import configparser
import datetime as dt
import hashlib
import json
import itertools
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import subprocess
import time

from . import bundle, local_restore_plan as plans, common_io


ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C',
       'PGCONNECT_TIMEOUT': '5', 'PYTHONDONTWRITEBYTECODE': '1'}
PREFIX = 'onedrive:job-applier-staging/postgresql-bundles/'
ISOLATED_AUTO = b'# Isolated disposable recovery; settings are supplied by the reviewed start command.\n'
START_SECONDS, RUNTIME_SECONDS, STOP_SECONDS = 30, 7200, 90
OWNER_SCALAR_FIELDS = ('MainPID', 'Type', 'Restart', 'RemainAfterExit', 'KillMode', 'SendSIGKILL',
    'TimeoutStartUSec', 'TimeoutStopUSec', 'RuntimeMaxUSec', 'PrivateNetwork')
OWNER_HOOK_FIELDS = ('ExecCondition', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost')
OWNER_FIELDS = OWNER_SCALAR_FIELDS + OWNER_HOOK_FIELDS


class NativeRefused(ValueError):
    """A fixed sanitized category, never private configuration or output."""


def postgres_account():
    import pwd
    return pwd.getpwnam('postgres')


def need(value, code='native_local_restore_refused'):
    if not value:
        raise NativeRefused(code)


def identity(meta):
    return (meta.st_dev, meta.st_ino, meta.st_uid, meta.st_gid, meta.st_mode,
            meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


def directory(path, *, task_root=None, postgres_uid=None):
    path = Path(path)
    need(path.is_absolute())
    for parent in (path, *path.parents):
        meta = parent.lstat()
        allowed = (0, postgres_uid) if task_root and parent.is_relative_to(task_root) else (0,)
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid in allowed
             and not meta.st_mode & 0o022, 'unsafe_directory')


def protected(path, *, maximum, modes=(0o600,), uids=(0,), gids=(0,), task_root=None):
    path = Path(path)
    directory(path.parent, task_root=task_root,
              postgres_uid=postgres_account().pw_uid if task_root else None)
    before = path.lstat()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        need(identity(opened) == identity(before) and stat.S_ISREG(opened.st_mode)
             and opened.st_nlink == 1 and opened.st_uid in uids and opened.st_gid in gids
             and stat.S_IMODE(opened.st_mode) in modes and opened.st_size <= maximum,
             'unsafe_file')
        chunks, remaining = [], opened.st_size
        while remaining:
            raw = os.read(fd, min(65536, remaining)); need(bool(raw))
            chunks.append(raw); remaining -= len(raw)
        need(not os.read(fd, 1) and identity(os.fstat(fd)) == identity(opened)
             and identity(path.lstat()) == identity(opened), 'file_changed')
        return b''.join(chunks)
    finally:
        os.close(fd)


def exclusive(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, 0, 0); os.fchmod(fd, 0o600)
        remaining = memoryview(raw)
        while remaining:
            count = os.write(fd, remaining); need(count > 0); remaining = remaining[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate_json_key'); result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: need(False, 'nonfinite_json'))


def configuration(raw):
    """Validate protected existing config without emitting any values/digests."""
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(raw.decode('utf-8'))
    except Exception:
        raise NativeRefused('private_configuration_refused') from None
    need(not parser.defaults() and set(parser.sections()) == {'global', 'careertrellis'},
         'private_configuration_refused')
    need(dict(parser['careertrellis']) == {'pg1-path': '/var/lib/postgresql/18/main',
                                          'pg1-port': '5432'}, 'private_configuration_refused')
    allowed = {'repo1-type', 'repo1-path', 'repo1-cipher-type', 'repo1-cipher-pass',
               'repo1-retention-full', 'repo1-retention-diff', 'repo1-retention-archive',
               'repo1-retention-archive-type', 'repo1-retention-full-type',
               'repo1-storage-verify-tls', 'repo1-s3-bucket', 'repo1-s3-endpoint',
               'repo1-s3-region', 'repo1-s3-key', 'repo1-s3-key-secret',
               'repo1-s3-key-type', 'repo1-s3-uri-style', 'repo1-s3-token',
               'start-fast', 'process-max', 'compress-type', 'compress-level', 'log-level-console',
               'log-level-file', 'log-path', 'archive-async', 'spool-path',
               'repo1-bundle', 'repo1-block'}
    need(set(parser['global']) <= allowed, 'private_configuration_unsupported_key')
    if 'compress-level' in parser['global']:
        level = parser['global']['compress-level']
        need(re.fullmatch(r'-?(?:[0-9]|[1-9][0-9])', level) is not None
             and -5 <= int(level) <= 22, 'private_configuration_compression_refused')
    need(parser['global'].get('repo1-cipher-type') == 'aes-256-cbc'
         and bool(parser['global'].get('repo1-cipher-pass')), 'encrypted_repository_required')


def validate_download(receipt, manifest, plan):
    keys = {'schema', 'result', 'transaction', 'backup_label', 'generation_sha256',
            'archive_sha256', 'archive_bytes', 'manifest_sha256', 'native_restore_accepted',
            'completed_at', 'destination', 'quota_after'}
    need(type(receipt) is dict and set(receipt) == keys, 'download_receipt_refused')
    need(receipt['schema'] == 'careertrellis.onedrive-bundle-download/v1'
         and receipt['result'] == 'download_verified'
         and receipt['native_restore_accepted'] is False, 'download_receipt_refused')
    for field in ('transaction', 'backup_label', 'generation_sha256', 'archive_sha256'):
        need(receipt[field] == plan[field], 'download_binding_mismatch')
    need(receipt['destination'] == PREFIX + plan['archive_sha256'] + '/repository.tar',
         'download_destination_mismatch')
    need(type(receipt['archive_bytes']) is int and 0 < receipt['archive_bytes'] <= bundle.MAX_BYTES)
    need(receipt['manifest_sha256'] == hashlib.sha256(bundle.canonical(manifest)).hexdigest())
    bundle.validate_manifest(manifest)
    need(manifest['backup_label'] == plan['backup_label']
         and manifest['generation_sha256'] == plan['generation_sha256'])
    quota = receipt['quota_after']
    need(type(quota) is dict and set(quota) == {'total', 'free'}
         and type(quota['total']) is int and type(quota['free']) is int
         and 0 <= quota['free'] <= quota['total'] and quota['total'] > 0)
    completed = dt.datetime.fromisoformat(receipt['completed_at'].replace('Z', '+00:00'))
    need(completed.utcoffset() == dt.timedelta(0)
         and completed <= dt.datetime.now(dt.timezone.utc), 'download_timestamp_refused')


def process_identity(pid):
    need(type(pid) is int and pid > 1)
    process = Path('/proc') / str(pid)
    fields = (process / 'stat').read_text().rsplit(') ', 1)[1].split()
    return {'pid': pid, 'start_ticks': int(fields[19]), 'process_group': int(fields[2]),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def span_microseconds(value):
    """Parse bounded systemctl duration output without accepting infinity."""
    need(type(value) is str and len(value) <= 64, 'owner_unit_containment_refused')
    tokens = re.findall(r'([0-9]+)(us|ms|min|s|h)', value)
    need(tokens and ' '.join(number + unit for number, unit in tokens) == value,
         'owner_unit_containment_refused')
    factors = {'us': 1, 'ms': 1000, 's': 1000000, 'min': 60000000, 'h': 3600000000}
    return sum(int(number) * factors[unit] for number, unit in tokens)


def validate_owner_unit(value, pid):
    need(type(value) is dict and set(value) == set(OWNER_FIELDS),
        'owner_unit_containment_refused')
    need(value['MainPID'] == str(pid) and value['Type'] == 'exec' and value['Restart'] == 'no'
         and value['RemainAfterExit'] == 'yes'
         and value['KillMode'] == 'control-group' and value['SendSIGKILL'] == 'yes'
         and value['PrivateNetwork'] == 'yes'
         and all(value[key] == '' for key in OWNER_HOOK_FIELDS)
         and span_microseconds(value['TimeoutStartUSec']) == START_SECONDS * 1000000
         and span_microseconds(value['TimeoutStopUSec']) == STOP_SECONDS * 1000000
         and span_microseconds(value['RuntimeMaxUSec']) == RUNTIME_SECONDS * 1000000,
         'owner_unit_containment_refused')
    return value


def validate_terminal_proof(value, receipt, owner, now=None, *, stop_intent=None):
    """Validate external collector evidence; an owner complete file is insufficient.

    The caller separately verifies protected canonical receipt/proof/ownership
    files. Success is witnessed while RemainAfterExit retains actual metadata;
    only then may a separate collector stop the already empty exact unit.
    Missing collector intent/proof refuses. GC defaults never prove success.
    No process, unit, provider or filesystem action is performed here.
    """
    need(type(value) is dict and set(value) == {'schema', 'transaction', 'attempt', 'unit',
        'owner', 'receipt_sha256', 'plan_sha256', 'unit_state', 'owner_terminal', 'cgroup_empty',
        'postmaster_terminal', 'checksums_verified', 'failure_markers_absent', 'observed_at',
        'stopped_unit_state', 'stop_intent_sha256'},
        'native_terminal_proof_refused')
    need(type(stop_intent) is dict and set(stop_intent) == {'schema', 'transaction', 'attempt',
        'unit', 'owner', 'receipt_sha256', 'unit_state', 'observed_at'}, 'native_terminal_proof_refused')
    need(value['schema'] == 'careertrellis.onedrive-native-terminal/v1'
         and type(owner) is dict and set(owner) == {'pid', 'start_ticks', 'process_group', 'boot_id'}
         and value['owner'] == owner, 'native_terminal_proof_refused')
    need(all(type(owner[key]) is int and owner[key] > 0 for key in ('pid', 'start_ticks', 'process_group'))
         and type(owner['boot_id']) is str
         and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', owner['boot_id']),
         'native_terminal_proof_refused')
    need(type(receipt) is dict and receipt.get('schema') == 'careertrellis.onedrive-native-local-restore/v1'
         and receipt.get('result') == 'verified' and receipt.get('native_restore_accepted') is True
         and receipt.get('postmaster_terminal') is True and receipt.get('checksums_verified') is True,
         'native_terminal_proof_refused')
    tx, attempt = value['transaction'], value['attempt']
    need(type(tx) is str and re.fullmatch('[0-9a-f]{32}', tx)
         and type(attempt) is int and 1 <= attempt <= 9
         and receipt.get('transaction') == tx and receipt.get('attempt') == attempt,
         'native_terminal_proof_refused')
    suffix = '' if attempt == 1 else '-%02d' % attempt
    unit = 'careertrellis-onedrive-bundle-restore-' + tx + suffix + '.service'
    need(value['unit'] == unit
         and value['receipt_sha256'] == hashlib.sha256(plans.canonical(receipt)).hexdigest()
         and type(value['plan_sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['plan_sha256'])
         and value['plan_sha256'] == receipt.get('plan_sha256'), 'native_terminal_proof_refused')
    need(all(value[key] is True for key in ('owner_terminal', 'cgroup_empty', 'postmaster_terminal',
         'checksums_verified', 'failure_markers_absent')), 'native_terminal_proof_refused')
    state = value['unit_state']
    need(type(state) is dict and set(state) == {'LoadState', 'ActiveState', 'SubState', 'RemainAfterExit',
        'MainPID', 'ControlPID', 'Job', 'Result', 'ExecMainStatus', 'ExecMainPID', 'InvocationID', 'ControlGroup'},
        'native_terminal_proof_refused')
    need(state['LoadState'] == 'loaded' and state['ActiveState'] == 'active'
         and state['SubState'] == 'exited' and state['RemainAfterExit'] == 'yes'
         and state['MainPID'] == state['ControlPID'] == '0' and state['Job'] == ''
         and state['Result'] == 'success' and state['ExecMainStatus'] == '0'
         and state['ExecMainPID'] == str(owner['pid'])
         and type(state['InvocationID']) is str and re.fullmatch('[0-9a-f]{32}', state['InvocationID'])
         and state['ControlGroup'] in ('', '/system.slice/' + unit), 'native_terminal_proof_refused')
    need(stop_intent['schema'] == 'careertrellis.onedrive-native-stop-intent/v1'
         and all(stop_intent[key] == value[key] for key in
                 ('transaction', 'attempt', 'unit', 'owner', 'receipt_sha256', 'unit_state'))
         and value['stop_intent_sha256'] == hashlib.sha256(plans.canonical(stop_intent)).hexdigest(),
         'native_terminal_proof_refused')
    stopped = value['stopped_unit_state']
    need(type(stopped) is dict and set(stopped) == {'LoadState', 'ActiveState', 'SubState',
         'MainPID', 'ControlPID', 'Job', 'ControlGroup'}, 'native_terminal_proof_refused')
    need(stopped['LoadState'] in ('loaded', 'not-found') and stopped['ActiveState'] == 'inactive'
         and stopped['SubState'] == 'dead' and stopped['MainPID'] == stopped['ControlPID'] == '0'
         and stopped['Job'] == '' and stopped['ControlGroup'] in ('', '/system.slice/' + unit),
         'native_terminal_proof_refused')
    try:
        observed = dt.datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
        completed = dt.datetime.fromisoformat(receipt['completed_at'].replace('Z', '+00:00'))
        stop_at = dt.datetime.fromisoformat(stop_intent['observed_at'].replace('Z', '+00:00'))
        current = dt.datetime.now(dt.timezone.utc) if now is None else now
        need(observed.utcoffset() == completed.utcoffset() == stop_at.utcoffset()
             == current.utcoffset() == dt.timedelta(0)
             and completed <= stop_at <= observed <= current, 'native_terminal_proof_refused')
    except (KeyError, TypeError, ValueError, AttributeError):
        raise NativeRefused('native_terminal_proof_refused') from None
    return value


def validate_control(fields):
    """Validate PG18 backup control metadata, not infer process liveness from it.

    An online backup may legitimately retain an 'in production' control state.
    Actual offline custody is established separately from the private data root,
    absent PID file, empty owner cgroup and required recovery signal.
    """
    need(fields.get('Database cluster state') in ('shut down', 'shut down in recovery', 'in production')
         and fields.get('Data page checksum version') == '1'
         and fields.get('Database system identifier', '').isdigit()
         and int(fields['Database system identifier']) > 0, 'restored_control_refused')


def prior_paths(plan):
    prior = plan['attempt'] - 1
    suffix = '' if prior == 1 else '-%02d' % prior
    root = Path(plans.RESTORE_ROOT) / (plan['transaction'] + suffix)
    return root, ('careertrellis-onedrive-bundle-restore-' + plan['transaction'] + suffix + '.service'), \
        Path(plan['transaction_root']) / ('native-restore' + suffix + '-intent.json')


def validate_reconciliation(raw, ownership_raw, intent_raw, plan, now=None):
    """Bind a fresh, terminal pre-restore attempt; never authorize a replay.

    Historical plans are hashed exactly as recorded, not reconstructed using a
    newer command set. Only failures before any restore dispatch qualify here.
    Failures after restore/start need a separately reviewed recovery contract.
    """
    plans.validate_plan(plan)
    need(plan['attempt'] > 1, 'reconciliation_not_applicable')
    proof = decode(raw); ownership = decode(ownership_raw); intent = decode(intent_raw)
    expected = {'schema', 'transaction', 'prior_attempt', 'next_attempt', 'archive_sha256',
                'prior_plan_sha256', 'prior_owner', 'prior_ownership_sha256', 'prior_intent_sha256',
                'prior_terminal', 'prior_cgroup_empty', 'no_restore_dispatch', 'no_postmaster', 'observed_at'}
    need(type(proof) is dict and set(proof) == expected, 'prior_reconciliation_refused')
    need(proof['schema'] == 'careertrellis.onedrive-native-reconciliation/v1'
         and proof['transaction'] == plan['transaction'] and type(proof['prior_attempt']) is int
         and proof['prior_attempt'] == plan['attempt'] - 1 and type(proof['next_attempt']) is int
         and proof['next_attempt'] == plan['attempt'] and proof['archive_sha256'] == plan['archive_sha256'],
         'prior_reconciliation_binding_mismatch')
    for field in ('prior_terminal', 'prior_cgroup_empty', 'no_restore_dispatch', 'no_postmaster'):
        need(proof[field] is True, 'prior_attempt_not_terminal')
    need(proof['prior_ownership_sha256'] == hashlib.sha256(ownership_raw).hexdigest()
         and proof['prior_intent_sha256'] == hashlib.sha256(intent_raw).hexdigest(),
         'prior_evidence_digest_mismatch')
    need(type(ownership) is dict and set(ownership) == {'schema', 'owner', 'plan', 'plan_sha256',
         'root_identity', 'download_receipt_sha256', 'web_preflight_sha256'}, 'prior_ownership_refused')
    need(ownership['schema'] == 'careertrellis.native-local-restore-ownership/v1')
    old_plan = ownership['plan']; old_owner = ownership['owner']
    need(type(old_plan) is dict and old_plan.get('execution_authorized') is False
         and old_plan.get('attempt', 1) == proof['prior_attempt'])
    old_root, _unit, _intent = prior_paths(plan)
    need(old_plan.get('attempt_root') == old_root.as_posix()
         and old_plan.get('data_root') == (old_root / 'data').as_posix())
    for field in ('transaction', 'archive_sha256', 'generation_sha256', 'backup_label', 'transaction_root'):
        need(old_plan.get(field) == plan[field], 'prior_plan_binding_mismatch')
    need(proof['prior_plan_sha256'] == ownership['plan_sha256'] == plans.digest(old_plan),
         'prior_plan_digest_mismatch')
    need(type(old_owner) is dict and set(old_owner) == {'pid', 'start_ticks', 'process_group', 'boot_id'}
         and old_owner == proof['prior_owner'], 'prior_owner_binding_mismatch')
    need(all(type(old_owner[field]) is int and old_owner[field] > 0
             for field in ('pid', 'start_ticks', 'process_group'))
         and type(old_owner['boot_id']) is str and re.fullmatch(
             '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', old_owner['boot_id']))
    need(type(intent) is dict and set(intent) in (
        {'schema', 'owner', 'plan_sha256', 'observed_at'},
        {'schema', 'owner', 'plan_sha256', 'observed_at', 'attempt'}))
    need(intent['schema'] == 'careertrellis.native-local-restore-intent/v1'
         and intent['owner'] == old_owner and intent.get('attempt', 1) == proof['prior_attempt']
         and intent['plan_sha256'] == proof['prior_plan_sha256'], 'prior_intent_binding_mismatch')
    need(type(ownership['root_identity']) is list and len(ownership['root_identity']) >= 2
         and all(type(item) is int for item in ownership['root_identity'][:2]))
    try:
        observed = dt.datetime.fromisoformat(proof['observed_at'].replace('Z', '+00:00'))
        current = now if now is not None else dt.datetime.now(dt.timezone.utc)
        need(observed.utcoffset() == dt.timedelta(0)
             and 0 <= (current - observed).total_seconds() <= 900, 'prior_reconciliation_stale')
    except (ValueError, TypeError, AttributeError):
        raise NativeRefused('prior_reconciliation_stale') from None
    return {'proof_sha256': hashlib.sha256(raw).hexdigest(), 'prior_owner': old_owner,
            'prior_root_identity': ownership['root_identity'][:2]}


class Native:
    def __init__(self, plan, web_preflight):
        plans.validate_plan(plan)
        need(hasattr(os, 'geteuid') and os.geteuid() == 0
             and hasattr(os, 'getegid') and os.getegid() == 0 and socket.gethostname() == plan['role'],
             'native_host_refused')
        self.plan = decode(plans.canonical(plan)); self.root = Path(plan['attempt_root'])
        self.transaction = Path(plan['transaction_root']); self.fds = []
        self.postmaster = None; self.possible_postmaster = False; self.journal_created = False
        self.prepared = False
        self.old_signals = {}
        self.old_umask = None
        self.account = postgres_account(); self.started = time.monotonic()
        self.owner = process_identity(os.getpid())
        self.web_path = Path(web_preflight)
        need(self.web_path == Path(plan['web_preflight_path']), 'web_preflight_path_refused')
        self.unit = plan['owner_unit']
        self.group = '/system.slice/' + self.unit
        self.reconciliation = None

    def event(self, name, value):
        need(re.fullmatch('[a-z0-9-]+', name))
        exclusive(self.root / (name + '.json'), plans.canonical(value))

    def capture(self, argv, maximum=131072, timeout=30):
        return self.dispatch(argv, None, timeout, maximum)

    def configuration_unchanged(self):
        if not hasattr(self, 'config'): return
        try:
            need(protected(Path(plans.CONFIG), maximum=131072, modes=(0o640,),
                           gids=(self.account.pw_gid,)) == self.config, 'private_configuration_changed')
            if self.prepared:
                includes = Path(self.plan['include_root']); directory(includes)
                meta = includes.lstat()
                need(meta.st_uid == 0 and meta.st_gid == self.account.pw_gid
                     and stat.S_IMODE(meta.st_mode) == 0o750 and not list(includes.iterdir()),
                     'private_configuration_changed')
        except Exception:
            raise NativeRefused('private_configuration_changed') from None

    def record_failure(self, phase):
        """Best-effort marker supplements the already durable incomplete intent."""
        self.failed = True
        try:
            self.event('contained-' + phase, {'schema': 'careertrellis.bundle-restore-contained/v1',
                       'owner': self.owner, 'phase': phase, 'operator_required': True,
                       'native_restore_accepted': False, 'automatic_retry': False,
                       'cleanup_performed': False, 'termination_profile': 'finite-control-group',
                       'observed_at': dt.datetime.now(dt.timezone.utc).isoformat()})
        except Exception:
            pass

    def contain(self, phase):
        self.record_failure(phase)
        # Fixed entry must exit nonzero. systemd owns cgroup-wide SIGTERM then
        # SIGKILL after 90s, including children that started new sessions. Never
        # kill guessed PIDs, clean partial data or convert timeout into success.
        # Kernel D-state may outlive SIGKILL: later dispatch still needs exact
        # terminal/cgroup proof and the durable incomplete-attempt guard.
        raise NativeRefused('native_restore_requires_reconciliation')

    def dispatch(self, argv, phase, timeout, maximum=0):
        proc = None
        self.configuration_unchanged()
        if phase:
            need(phase in ('info', 'restore', 'start', 'probe', 'stop', 'checksums')
                 and argv == self.plan[phase], 'unexpected_command')
            self.event(phase + '-intent', {'owner': self.owner, 'plan_sha256': plans.digest(self.plan),
                       'phase': phase, 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat()})
        try:
            if phase == 'start': self.possible_postmaster = True
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if maximum else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=dict(ENV), close_fds=True,
                pass_fds=tuple(self.fds), start_new_session=True)
            try:
                observed = process_identity(proc.pid)
            except (OSError, IndexError):
                need(proc.poll() is not None, 'child_identity_unknown')
                observed = {'pid': proc.pid, 'boot_id': self.owner['boot_id'],
                            'identity_unavailable_child_terminal': True}
            if phase: self.event(phase + '-dispatch', observed)
            data = bytearray(); deadline = time.monotonic() + timeout
            if maximum:
                selector = selectors.DefaultSelector()
                try:
                    selector.register(proc.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic(); need(remaining > 0, 'child_over_budget')
                        need(selector.select(remaining), 'child_over_budget')
                        raw = os.read(proc.stdout.fileno(), min(65536, maximum + 1 - len(data)))
                        if not raw: break
                        data.extend(raw); need(len(data) <= maximum, 'child_output_bound')
                finally:
                    selector.close(); proc.stdout.close()
            code = proc.wait(timeout=max(.01, deadline - time.monotonic()))
            if phase: self.event(phase + '-terminal', dict(observed, exit_code=code))
            need(code == 0, 'child_failed')
            return bytes(data)
        except BaseException:
            if proc is not None and (proc.poll() is None or self.possible_postmaster):
                self.contain(phase or 'observation')
            raise NativeRefused('native_command_refused') from None

    def lock(self):
        import fcntl
        for name in self.plan['locks_in_order']:
            path = Path(name)
            for parent in path.parents:
                meta = parent.lstat()
                need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                     and (stat.S_IMODE(meta.st_mode) == 0o1777 if str(parent) == '/run/lock'
                          else not meta.st_mode & 0o022), 'unsafe_lock_parent')
            before = path.lstat()
            need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
                 and before.st_nlink == 1 and before.st_size == 0
                 and stat.S_IMODE(before.st_mode) == 0o600, 'unsafe_lock')
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            self.fds.append(fd); need(identity(os.fstat(fd)) == identity(before))
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def group_members(self):
        root = Path('/sys/fs/cgroup') / self.group.lstrip('/')
        need(root.is_dir() and not root.is_symlink(), 'owner_cgroup_refused')
        meta = root.lstat()
        need(meta.st_uid == 0 and not meta.st_mode & 0o022, 'owner_cgroup_permissions_refused')
        groups = list(itertools.islice(root.rglob('cgroup.procs'), 65)); need(len(groups) <= 64)
        members = set()
        for path in groups:
            meta = path.lstat()
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022,
                 'owner_cgroup_permissions_refused')
            raw = path.read_text(); need(len(raw) <= 65536)
            members.update(int(value) for value in raw.split())
        return members

    def observe(self):
        need(Path('/proc/self/cgroup').read_text().strip() == '0::' + self.group,
             'dedicated_owner_unit_required')
        unit = common_io.read_unit_properties(self.unit, OWNER_FIELDS, self.capture)
        validate_owner_unit(unit, os.getpid())
        need(self.group_members() == {os.getpid()}, 'owner_group_not_exclusive')
        for name in self.plan['idle_units']:
            raw = self.capture(['/usr/bin/systemctl', 'show', name,
                '--property=ActiveState,MainPID,ControlPID,Job,ControlGroup'])
            state = dict(line.split('=', 1) for line in raw.decode().splitlines())
            need(state.get('ActiveState') in ('inactive', 'failed')
                 and state.get('MainPID') == state.get('ControlPID') == '0'
                 and state.get('Job') == '', 'backup_operation_active')
            group = state.get('ControlGroup')
            if group:
                need(group == '/system.slice/' + name)
                root = Path('/sys/fs/cgroup') / group.lstrip('/')
                if root.exists():
                    procs = list(itertools.islice(root.rglob('cgroup.procs'), 65)); need(len(procs) <= 64)
                    need(all(not path.read_text().strip() for path in procs), 'backup_group_not_empty')
        need(not self.capture(['/usr/bin/ss', '-H', '-ltn', 'sport = :55432']).strip(),
             'restore_port_occupied')
        need(b'.s.PGSQL.55432' not in self.capture(['/usr/bin/ss', '-H', '-lx']),
             'restore_socket_occupied')
        need(not os.path.lexists('/run/careertrellis-hosted-activation.lease'), 'activation_lease_present')
        for path in (Path('/tmp/.s.PGSQL.55432'), Path('/var/run/postgresql/.s.PGSQL.55432')):
            need(not os.path.lexists(path), 'restore_socket_occupied')
        space_root = self.root.parent if os.path.lexists(self.root.parent) else self.root.parent.parent
        meta = os.statvfs(space_root)
        need(meta.f_bavail * meta.f_frsize >= self.plan['minimum_free_bytes']
             and meta.f_favail >= self.plan['minimum_free_inodes'], 'restore_capacity_shortfall')

    def preflight(self):
        directory(self.transaction)
        need(stat.S_IMODE(self.transaction.lstat().st_mode) == 0o700, 'transaction_not_private')
        need(not os.path.lexists(self.root), 'restore_attempt_already_exists')
        need(protected(Path(self.plan['native_plan_path']), maximum=131072) == plans.canonical(self.plan),
             'native_plan_file_mismatch')
        directory(self.root.parent.parent)
        if os.path.lexists(self.root.parent):
            directory(self.root.parent)
            meta = self.root.parent.lstat()
            need(meta.st_uid == 0 and meta.st_gid == self.account.pw_gid
                 and stat.S_IMODE(meta.st_mode) == 0o710, 'acceptance_parent_refused')
        for executable in ('/usr/bin/setpriv', '/usr/bin/pgbackrest', '/usr/bin/psql',
                           '/usr/bin/systemctl', '/usr/bin/ss', plans.PG + 'postgres',
                           plans.PG + 'pg_ctl', plans.PG + 'pg_checksums', plans.PG + 'pg_controldata'):
            path = Path(executable); directory(path.parent); meta = path.stat()
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022,
                 'native_executable_refused')
        config_path = Path(plans.CONFIG)
        self.config = protected(config_path, maximum=131072, modes=(0o640,), gids=(self.account.pw_gid,))
        configuration(self.config)
        self.reconcile_prior_attempt()
        includes = Path('/etc/pgbackrest/conf.d')
        if os.path.lexists(includes): directory(includes); need(not list(includes.iterdir()), 'configuration_includes_present')
        self.web_raw = protected(self.web_path, maximum=16384)
        web = decode(self.web_raw)
        need(type(web) is dict and set(web) == {'schema', 'host', 'authority', 'pending_phase',
             'activation_lease_absent', 'services_healthy', 'release_binding_verified',
             'release', 'observed_at'}, 'web_preflight_schema_refused')
        need(web.get('schema') == 'careertrellis.onedrive-restore-web-preflight/v1'
             and web.get('host') == 'careertrellis-staging-web-1'
             and web.get('authority') == 'postgres-r2-authoritative'
             and web.get('pending_phase') == 'activated' and web.get('activation_lease_absent') is True
             and web.get('services_healthy') is True and web.get('release_binding_verified') is True
             and re.fullmatch('[0-9a-f]{40}', web.get('release', '')), 'web_preflight_refused')
        observed = dt.datetime.fromisoformat(web['observed_at'].replace('Z', '+00:00'))
        need(observed.utcoffset() == dt.timedelta(0)
             and 0 <= (dt.datetime.now(dt.timezone.utc) - observed).total_seconds() <= 300,
             'web_preflight_stale')
        self.web = web
        self.manifest_raw = protected(self.transaction / 'downloaded-manifest.json', maximum=bundle.MAX_MANIFEST)
        self.manifest = bundle._decode(self.manifest_raw)
        self.download_raw = protected(self.transaction / 'downloaded-receipt.json', maximum=16384)
        validate_download(decode(self.download_raw), self.manifest, self.plan)
        self.verify_repository()

    def reconcile_prior_attempt(self):
        if self.plan['attempt'] == 1: return
        old_root, old_unit, old_intent = prior_paths(self.plan)
        proof = protected(Path(self.plan['prior_reconciliation_path']), maximum=16384)
        ownership = protected(old_root / 'ownership.json', maximum=131072)
        intent = protected(old_intent, maximum=16384)
        checked = validate_reconciliation(proof, ownership, intent, self.plan)
        meta = old_root.lstat()
        need([meta.st_dev, meta.st_ino] == checked['prior_root_identity'], 'prior_root_identity_changed')
        prior_owner = checked['prior_owner']
        if prior_owner['boot_id'] == self.owner['boot_id']:
            try:
                current = process_identity(prior_owner['pid'])
                need(current['start_ticks'] != prior_owner['start_ticks'], 'prior_owner_still_alive')
            except (FileNotFoundError, ProcessLookupError):
                pass
        for name in ('restore-intent.json', 'restore-dispatch.json', 'restore-terminal.json',
                     'start-intent.json', 'start-dispatch.json', 'postmaster.json', 'data/postmaster.pid'):
            need(not os.path.lexists(old_root / name), 'prior_restore_requires_separate_recovery')
        old_data = old_root / 'data'; directory(old_data, task_root=old_root, postgres_uid=self.account.pw_uid)
        need(not list(itertools.islice(old_data.iterdir(), 1)), 'prior_data_not_empty')
        raw = self.capture(['/usr/bin/systemctl', 'show', old_unit,
                            '--property=ActiveState,MainPID,ControlPID,Job,ControlGroup'])
        state = dict(line.split('=', 1) for line in raw.decode().splitlines())
        need(state.get('ActiveState') in ('inactive', 'failed') and state.get('MainPID') == '0'
             and state.get('ControlPID') == '0' and state.get('Job') == '', 'prior_unit_not_terminal')
        group = '/system.slice/' + old_unit
        need(state.get('ControlGroup') in ('', group), 'prior_cgroup_mismatch')
        cgroup = Path('/sys/fs/cgroup') / group.lstrip('/')
        if cgroup.exists():
            files = list(itertools.islice(cgroup.rglob('cgroup.procs'), 65)); need(len(files) <= 64)
            need(all(not path.read_text().strip() for path in files), 'prior_cgroup_not_empty')
        self.reconciliation = checked['proof_sha256']

    def verify_repository(self):
        need(protected(self.transaction / 'downloaded-manifest.json', maximum=bundle.MAX_MANIFEST) == self.manifest_raw)
        need(protected(self.transaction / 'downloaded-receipt.json', maximum=16384) == self.download_raw)
        with bundle._directory(self.transaction) as root:
            archive = 'downloaded-repository.tar'
            meta = os.stat(archive, dir_fd=root, follow_symlinks=False)
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                 and meta.st_nlink == 1 and stat.S_IMODE(meta.st_mode) == 0o600
                 and meta.st_size == decode(self.download_raw)['archive_bytes'])
            with bundle._source(root, archive, bundle._identity(meta)) as stream:
                need(bundle._hash(stream, meta.st_size) == self.plan['archive_sha256'], 'archive_digest_mismatch')
        self.verify_tree(self.plan['repository_input_root'])
        if os.path.lexists(self.plan['repository_root']):
            self.verify_tree(self.plan['repository_root'])

    def verify_tree(self, path):
        with bundle._directory(path) as root:
            scanned = bundle._scan(root)
            need(set(scanned) == set(self.manifest['files']), 'repository_inventory_mismatch')
            root_device = os.fstat(root).st_dev
            for name, entry in self.manifest['files'].items():
                need(scanned[name][0] == root_device and scanned[name][4] == entry['size'])
                with bundle._source(root, name, scanned[name]) as stream:
                    need(bundle._hash(stream, entry['size']) == entry['sha256'], 'repository_digest_mismatch')
            need(bundle._scan(root) == scanned, 'repository_changed')

    def prepare_permissions(self):
        # A new, separately owned acceptance root receives a verified encrypted
        # copy. Never widen the existing private hydration/download namespace.
        exclusive(Path(self.plan['native_intent_path']), plans.canonical({
            'schema': 'careertrellis.native-local-restore-intent/v1', 'owner': self.owner,
            'plan_sha256': plans.digest(self.plan), 'attempt': self.plan['attempt'],
            'observed_at': dt.datetime.now(dt.timezone.utc).isoformat()}))
        if not os.path.lexists(self.root.parent):
            self.root.parent.mkdir(mode=0o700)
            os.chown(self.root.parent, 0, self.account.pw_gid); os.chmod(self.root.parent, 0o710)
            fsync_directory(self.root.parent)
            fsync_directory(self.root.parent.parent)
        directory(self.root.parent)
        parent_meta = self.root.parent.lstat()
        need(parent_meta.st_uid == 0 and parent_meta.st_gid == self.account.pw_gid
             and stat.S_IMODE(parent_meta.st_mode) == 0o710, 'acceptance_parent_refused')
        self.root.mkdir(mode=0o700); self.journal_created = True
        fsync_directory(self.root)
        fsync_directory(self.root.parent)
        self.event('ownership', {'schema': 'careertrellis.native-local-restore-ownership/v1',
            'owner': self.owner, 'plan': self.plan, 'plan_sha256': plans.digest(self.plan),
            'root_identity': list(identity(self.root.lstat())),
            'download_receipt_sha256': hashlib.sha256(self.download_raw).hexdigest(),
            'web_preflight_sha256': hashlib.sha256(self.web_raw).hexdigest()})
        if self.reconciliation is not None:
            self.event('prior-reconciliation', {'schema': 'careertrellis.native-local-restore-prior-proof/v1',
                'attempt': self.plan['attempt'], 'proof_sha256': self.reconciliation})
        root = Path(self.plan['repository_root']); root.mkdir(mode=0o700)
        fsync_directory(root)
        fsync_directory(self.root)
        with bundle._directory(self.plan['repository_input_root']) as source, bundle._directory(root) as target:
            scanned = bundle._scan(source)
            need(set(scanned) == set(self.manifest['files']))
            for name, entry in sorted(self.manifest['files'].items()):
                with bundle._source(source, name, scanned[name]) as stream:
                    with bundle._parent(target, name, create=True) as (parent, leaf):
                        fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                        with os.fdopen(fd, 'wb') as output:
                            digest = hashlib.sha256(); remaining = entry['size']
                            while remaining:
                                raw = stream.read(min(65536, remaining)); need(bool(raw))
                                output.write(raw); digest.update(raw); remaining -= len(raw)
                            need(not stream.read(1) and digest.hexdigest() == entry['sha256'],
                                 'repository_copy_digest_mismatch')
                            output.flush(); os.fsync(output.fileno())
                        os.fsync(parent)
            need(bundle._scan(source) == scanned, 'repository_source_changed')
        paths = list(itertools.islice(root.rglob('*'), 200001)); need(len(paths) <= 200000)
        for path in (root, *paths):
            meta = path.lstat(); os.chown(path, 0, self.account.pw_gid, follow_symlinks=False)
            os.chmod(path, 0o750 if stat.S_ISDIR(meta.st_mode) else 0o640, follow_symlinks=False)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW |
                         (os.O_DIRECTORY if stat.S_ISDIR(meta.st_mode) else 0))
            try: os.fsync(fd)
            finally: os.close(fd)
        os.chown(self.root, 0, self.account.pw_gid); os.chmod(self.root, 0o750)
        for name in ('data_root', 'socket_root', 'tablespace_root', 'lock_root', 'spool_root'):
            path = Path(self.plan[name]); path.mkdir(mode=0o700)
            os.chown(path, self.account.pw_uid, self.account.pw_gid); os.chmod(path, 0o700)
            fsync_directory(path)
        includes = Path(self.plan['include_root']); includes.mkdir(mode=0o700)
        os.chown(includes, 0, self.account.pw_gid); os.chmod(includes, 0o750)
        fsync_directory(includes)
        fsync_directory(self.root)
        self.prepared = True

    def local_info(self, raw):
        info = decode(raw); need(type(info) is list and len(info) == 1)
        stanza = info[0]
        need(stanza.get('name') == 'careertrellis' and stanza.get('status', {}).get('code') == 0)
        backups = stanza.get('backup'); need(type(backups) is list)
        for label in (self.plan['backup_label'], self.plan['full_ancestor_label']):
            selected = [row for row in backups if row.get('label') == label]
            need(len(selected) == 1, 'local_backup_label_missing')
            size = selected[0].get('info', {}).get('size')
            need(type(size) is int and 0 < size <= self.plan['maximum_database_bytes'])
            need(selected[0].get('error', False) is False, 'local_backup_unhealthy')

    def verify_restored_tree(self):
        data = Path(self.plan['data_root']); directory(data, task_root=self.root, postgres_uid=self.account.pw_uid)
        device = data.lstat().st_dev
        paths = list(itertools.islice(data.rglob('*'), 200001)); need(len(paths) <= 200000)
        tablespaces = Path(self.plan['tablespace_root'])
        # Current staging has no non-default tablespaces. Refuse them rather
        # than claiming unimplemented traversal/reference checks are sufficient.
        need(not list(tablespaces.iterdir()), 'nondefault_tablespaces_unsupported')
        for path in paths:
            meta = path.lstat()
            need(meta.st_dev == device and meta.st_uid == self.account.pw_uid,
                 'restored_mount_or_owner_refused')
            need(not meta.st_mode & 0o022 and (stat.S_ISDIR(meta.st_mode) or
                 (stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1)), 'restored_file_refused')

    def isolated_configuration(self):
        self.verify_restored_tree()
        data = Path(self.plan['data_root'])
        need(not os.path.lexists(data / 'postmaster.pid')
             and self.group_members() == {os.getpid()}, 'restored_cluster_not_offline')
        need(not os.path.lexists(data / 'standby.signal') and os.path.lexists(data / 'recovery.signal'),
             'restore_recovery_state_refused')
        control = data / 'global/pg_control'
        protected(control, maximum=65536, modes=(0o600,), uids=(self.account.pw_uid,),
                  gids=(self.account.pw_gid,), task_root=self.root)
        need(protected(data / 'PG_VERSION', maximum=16, modes=(0o600,),
                       uids=(self.account.pw_uid,), gids=(self.account.pw_gid,),
                       task_root=self.root).strip() == b'18', 'restored_version_refused')
        control_data = self.capture(['/usr/bin/setpriv', '--reuid=postgres', '--regid=postgres',
            '--init-groups', '--reset-env', '--', plans.PG + 'pg_controldata', '--pgdata=' + str(data)])
        fields = dict(line.split(':', 1) for line in control_data.decode('ascii').splitlines() if ':' in line)
        fields = {key.strip(): value.strip() for key, value in fields.items()}
        validate_control(fields)
        self.replace_disposable_auto(data)
        for name, content in (('postgresql.bundle.conf', self.plan['config_template']),
                              ('pg_hba.bundle.conf', self.plan['hba']),
                              ('pg_ident.bundle.conf', self.plan['ident_template'])):
            exclusive(data / name, content.encode('ascii')); os.chown(data / name, self.account.pw_uid, self.account.pw_gid)

    def replace_disposable_auto(self, data):
        """Preserve private config bytes, then replace only this offline copy.

        Called after actual PG18/control verification. Original settings may
        contain connection secrets: preserve them as protected configuration,
        never parse, publish or fingerprint them in operational receipts.
        """
        need(data == Path(self.plan['data_root']) and not self.possible_postmaster
             and not os.path.lexists(data / 'postmaster.pid')
             and self.group_members() == {os.getpid()}, 'restored_cluster_not_offline')
        original = data / 'postgresql.auto.conf'
        before = original.lstat()
        raw = protected(original, maximum=65536, modes=(0o600,), uids=(self.account.pw_uid,),
                        gids=(self.account.pw_gid,), task_root=self.root)
        need(identity(original.lstat()) == identity(before), 'restored_auto_configuration_changed')
        exclusive(self.root / '.restored-auto-conf.original', raw)
        with bundle._directory(data) as directory_fd:
            need(identity(os.stat(original.name, dir_fd=directory_fd, follow_symlinks=False)) == identity(before),
                 'restored_auto_configuration_changed')
            temporary = '.postgresql.auto.conf.native'
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
            try:
                os.fchown(fd, self.account.pw_uid, self.account.pw_gid); os.fchmod(fd, 0o600)
                offset = 0
                while offset < len(ISOLATED_AUTO):
                    count = os.write(fd, ISOLATED_AUTO[offset:]); need(count > 0); offset += count
                os.fsync(fd)
            finally:
                os.close(fd)
            need(identity(os.stat(original.name, dir_fd=directory_fd, follow_symlinks=False)) == identity(before),
                 'restored_auto_configuration_changed')
            os.replace(temporary, original.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
        need(protected(original, maximum=65536, modes=(0o600,), uids=(self.account.pw_uid,),
                       gids=(self.account.pw_gid,), task_root=self.root) == ISOLATED_AUTO,
             'isolated_auto_configuration_refused')

    def owned_postmaster(self):
        data = Path(self.plan['data_root'])
        raw = protected(data / 'postmaster.pid', maximum=4096, modes=(0o600,),
                        uids=(self.account.pw_uid,), gids=(self.account.pw_gid,), task_root=self.root)
        values = raw.decode('ascii').splitlines()
        need(len(values) >= 6 and values[1] == self.plan['data_root']
             and values[3] == str(plans.PORT) and values[4] == self.plan['socket_root'])
        pid = int(values[0]); found = process_identity(pid)
        if self.postmaster is not None: need(found == self.postmaster, 'postmaster_identity_changed')
        process = Path('/proc') / str(pid)
        need(process.stat().st_uid == self.account.pw_uid
             and os.readlink(process / 'exe') == plans.PG + 'postgres'
             and (process / 'cgroup').read_text().strip() == '0::' + self.group,
             'postmaster_not_owned')
        args = (process / 'cmdline').read_bytes().split(b'\0')
        need(b'-D' in args and args[args.index(b'-D') + 1] == self.plan['data_root'].encode('ascii'))
        self.postmaster = found; return found

    def wait_terminal(self):
        """Observe only the original identity; never repeat stop or signal."""
        deadline = time.monotonic() + 30
        while True:
            original_terminal = False
            try:
                current = process_identity(self.postmaster['pid'])
                original_terminal = current != self.postmaster
            except (FileNotFoundError, ProcessLookupError):
                original_terminal = True
            if original_terminal and self.group_members() == {os.getpid()}:
                need(not os.path.lexists(Path(self.plan['data_root']) / 'postmaster.pid')
                     and not os.path.lexists(Path(self.plan['socket_root']) / '.s.PGSQL.55432')
                     and not os.path.lexists(Path(self.plan['socket_root']) / '.s.PGSQL.55432.lock'),
                     'postmaster_files_not_terminal')
                return
            need(time.monotonic() < deadline, 'postmaster_not_terminal')
            time.sleep(.25)

    def restore(self):
        self.preflight(); self.lock(); self.observe()
        from . import guard
        guard.native_check('bundle_restore', self.plan['transaction'], self.plan['attempt'], self.fds[-1])
        self.verify_repository()
        self.reconcile_prior_attempt()
        self.old_umask = os.umask(0o077)
        self.prepare_permissions(); self.verify_repository()
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.old_signals[sig] = signal.signal(sig, lambda _sig, _frame: self.contain('operator-signal'))
        self.local_info(self.dispatch(self.plan['info'], 'info', 60, 2 * 1024 ** 2))
        self.dispatch(self.plan['restore'], 'restore', 3600)
        self.isolated_configuration(); self.verify_repository()
        self.dispatch(self.plan['start'], 'start', 120)
        self.event('postmaster', self.owned_postmaster())
        need(not self.capture(['/usr/bin/ss', '-H', '-ltn', 'sport = :55432']).strip())
        need(self.dispatch(self.plan['probe'], 'probe', 5, 4096).strip() == b'ok', 'promotion_probe_failed')
        self.owned_postmaster()
        self.dispatch(self.plan['stop'], 'stop', 90)
        self.wait_terminal()
        need(not self.capture(['/usr/bin/ss', '-H', '-ltn', 'sport = :55432']).strip())
        self.possible_postmaster = False
        self.verify_restored_tree()
        self.dispatch(self.plan['checksums'], 'checksums', 600)
        self.verify_repository()
        self.configuration_unchanged()
        need(not getattr(self, 'failed', False), 'native_attempt_already_failed')
        receipt = {'schema': 'careertrellis.onedrive-native-local-restore/v1', 'result': 'verified',
            'transaction': self.plan['transaction'], 'attempt': self.plan['attempt'],
            'archive_sha256': self.plan['archive_sha256'],
            'generation_sha256': self.plan['generation_sha256'], 'backup_label': self.plan['backup_label'],
            'plan_sha256': plans.digest(self.plan), 'download_receipt_sha256': hashlib.sha256(self.download_raw).hexdigest(),
            'prior_reconciliation_sha256': self.reconciliation,
            'native_restore_accepted': True, 'postmaster_terminal': True, 'checksums_verified': True,
            'cleanup_performed': False, 'elapsed_seconds': round(time.monotonic() - self.started, 3),
            'completed_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        self.event('complete', receipt)
        return receipt

    def close(self):
        if self.old_umask is not None: os.umask(self.old_umask); self.old_umask = None
        for sig, previous in self.old_signals.items(): signal.signal(sig, previous)
        self.old_signals = {}
        for fd in reversed(self.fds): os.close(fd)
        self.fds = []


def execute(plan, web_preflight):
    native = None
    try:
        native = Native(plan, web_preflight)
        return native.restore()
    except NativeRefused:
        if native is not None and native.possible_postmaster: native.contain('failure')
        raise
    except BaseException:
        if native is not None and native.possible_postmaster: native.contain('failure')
        raise NativeRefused('native_restore_requires_reconciliation') from None
    finally:
        if native is not None:
            try:
                try:
                    native.configuration_unchanged()
                except BaseException:
                    native.record_failure('final-configuration')
                    raise NativeRefused('native_restore_requires_reconciliation') from None
            finally:
                native.close()
