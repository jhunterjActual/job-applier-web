"""External, bounded native-unit terminal collector. No providers or PG commands.

The fixed installed entry must dispatch this separate unit upfront. Status polls
must never launch it. A missing install guard, collector unit or proof refuses;
existing journals are never resumed. Partial files and failed data are retained.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time

from . import coordinator as c, hydrate, native_restore as native, local_restore_plan as plans, common_io

WAIT_SECONDS = 7290
STOP_WAIT_SECONDS = 120
COLLECTOR_RUNTIME_SECONDS = 7500
SNAPSHOT_FIELDS = ('LoadState', 'ActiveState', 'SubState', 'RemainAfterExit', 'MainPID',
    'ControlPID', 'Job', 'Result', 'ExecMainStatus', 'ExecMainPID', 'InvocationID', 'ControlGroup')
PROFILE_FIELDS = native.OWNER_FIELDS
SHOW_FIELDS = tuple(dict.fromkeys((*SNAPSHOT_FIELDS, *PROFILE_FIELDS)))
STOPPED_FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'Job', 'ControlGroup')
STOP_QUERY_FIELDS = tuple(dict.fromkeys((*STOPPED_FIELDS, 'Result', 'ExecMainStatus', 'InvocationID')))


class CollectorRefused(ValueError):
    pass


def need(value):
    if not value: raise CollectorRefused('native_collector_requires_reconciliation')


def coordinates(transaction, attempt):
    need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}', transaction)
         and type(attempt) is int and 1 <= attempt <= 9)
    suffix = '' if attempt == 1 else '-%02d' % attempt
    return {'owner_unit': 'careertrellis-onedrive-bundle-restore-' + transaction + suffix + '.service',
        'collector_unit': 'careertrellis-onedrive-bundle-collector-' + transaction + suffix + '.service',
        'attempt_root': Path(plans.RESTORE_ROOT) / (transaction + suffix),
        'journal_root': c.STATE / 'db' / transaction / ('collector' + suffix)}


def retained_success(state, owner, unit):
    """Actual retained metadata is mandatory; GC defaults cannot prove success."""
    need(type(state) is dict and set(state) == set(SHOW_FIELDS))
    native.validate_owner_unit({key: state[key] for key in PROFILE_FIELDS}, 0)
    need(state['LoadState'] == 'loaded' and state['ActiveState'] == 'active'
         and state['SubState'] == 'exited' and state['MainPID'] == state['ControlPID'] == '0'
         and state['Job'] == '' and state['Result'] == 'success' and state['ExecMainStatus'] == '0'
         and state['ExecMainPID'] == str(owner['pid'])
         and all(state[key] == '' for key in native.OWNER_HOOK_FIELDS)
         and type(state['InvocationID']) is str and re.fullmatch('[0-9a-f]{32}', state['InvocationID'])
         and state['ControlGroup'] in ('', '/system.slice/' + unit))
    return {key: state[key] for key in SNAPSHOT_FIELDS}


def make_proof(observation, stop_intent, stopped, observed_at):
    receipt = observation['receipt']
    return {'schema': 'careertrellis.onedrive-native-terminal/v1',
        'transaction': receipt['transaction'], 'attempt': receipt['attempt'],
        'unit': stop_intent['unit'], 'owner': observation['owner'],
        'receipt_sha256': hashlib.sha256(plans.canonical(receipt)).hexdigest(),
        'plan_sha256': receipt['plan_sha256'], 'unit_state': stop_intent['unit_state'],
        'owner_terminal': True, 'cgroup_empty': True, 'postmaster_terminal': True,
        'checksums_verified': True, 'failure_markers_absent': True,
        'stop_intent_sha256': hashlib.sha256(plans.canonical(stop_intent)).hexdigest(),
        'stopped_unit_state': stopped, 'observed_at': observed_at}


def collect(transaction, attempt=1, backend=None):
    coordinates(transaction, attempt)
    backend = Native(transaction, attempt) if backend is None else backend
    try:
        backend.preflight()
        backend.begin(transaction, attempt)  # Existing journal refuses before mutation.
    except BaseException:
        if getattr(backend, 'owned_journal', False): backend.failed()
        raise CollectorRefused('native_collector_requires_reconciliation') from None
    try:
        deadline = backend.clock() + WAIT_SECONDS
        while True:
            need(backend.clock() < deadline)
            observation = backend.observe_owner()
            if observation is not None: break
            backend.pause()
        need(observation['receipt'].get('transaction') == transaction
             and type(observation['receipt'].get('attempt')) is int
             and observation['receipt']['attempt'] == attempt)
        stop_intent = backend.stop_intent(observation)
        backend.stop_empty_owner(observation)
        stopped = backend.wait_stopped(observation, STOP_WAIT_SECONDS)
        proof = make_proof(observation, stop_intent, stopped, backend.now().isoformat())
        native.validate_terminal_proof(proof, observation['receipt'], observation['owner'], stop_intent=stop_intent)
        backend.finish(proof)
        backend.commit(proof, observation)  # Final durable mutation/acceptance marker.
        return proof
    except BaseException:
        backend.failed()
        raise CollectorRefused('native_collector_requires_reconciliation') from None


class Native:
    def __init__(self, transaction, attempt=1):
        self.transaction, self.attempt = transaction, attempt
        self.paths = coordinates(transaction, attempt)
        self.root = self.paths['attempt_root']; self.journal = self.paths['journal_root']
        self.owner_unit = self.paths['owner_unit']; self.collector_unit = self.paths['collector_unit']
        self.seen_invocation = None; self.owned_journal = False

    def clock(self): return time.monotonic()
    def now(self): return dt.datetime.now(dt.timezone.utc)
    def pause(self): time.sleep(1)

    def show(self, unit, fields=SHOW_FIELDS):
        return common_io.read_unit_properties(unit, fields,
            lambda args: hydrate.command(args, output=True, timeout=10))

    def group_members(self, unit):
        path = Path('/sys/fs/cgroup/system.slice') / unit
        if not os.path.lexists(path): return set()
        need(stat.S_ISDIR(path.lstat().st_mode) and path.lstat().st_uid == 0
             and not path.lstat().st_mode & 0o022)
        import itertools
        groups = list(itertools.islice(path.rglob('cgroup.procs'), 65))
        need(0 < len(groups) <= 64 and path / 'cgroup.procs' in groups)
        members = set()
        for item in groups:
            meta = item.lstat()
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022)
            raw = item.read_text(); need(len(raw) <= 65536)
            members.update(int(value) for value in raw.split())
        return members

    def group_empty(self, unit): return not self.group_members(unit)

    def terminal_identity(self, original):
        need(type(original) is dict and set(original) == {'pid', 'start_ticks', 'process_group', 'boot_id'}
             and all(type(original[key]) is int and original[key] > 0 for key in ('pid', 'start_ticks', 'process_group'))
             and original['boot_id'] == self.owner['boot_id'])
        try: current = native.process_identity(original['pid'])
        except (FileNotFoundError, ProcessLookupError): return True
        return (current['boot_id'], current['start_ticks']) != (original['boot_id'], original['start_ticks'])

    def installed(self):
        from . import entry  # No unverified/default replacement implementation.
        need(entry.verify_install() == self.policy.data['artifact_revision'])
        c.native_policy_binding(self.policy)

    def preflight(self):
        need(sys.platform == 'linux' and os.geteuid() == os.getegid() == 0
             and socket.gethostname() == 'job-applier-db-staging-01')
        from . import entry
        revision = entry.verify_install()
        self.policy = c.Policy(c.read_private(c.PRIVATE_POLICY))
        need(revision == self.policy.data['artifact_revision'])
        self.policy.require_active(self.now()); c.native_policy_binding(self.policy)
        need(Path('/proc/self/cgroup').read_text().strip() == '0::/system.slice/' + self.collector_unit
             and self.collector_unit != self.owner_unit)
        self.owner = native.process_identity(os.getpid())
        state = self.show(self.collector_unit)
        need(state['LoadState'] == 'loaded' and state['ActiveState'] == 'active' and state['SubState'] == 'running'
             and state['MainPID'] == str(os.getpid()) and state['ControlPID'] == '0' and state['Job'] == ''
             and state['Type'] == 'exec' and state['Restart'] == 'no' and state['RemainAfterExit'] == 'no'
             and state['PrivateNetwork'] == 'yes' and state['KillMode'] == 'control-group'
             and state['SendSIGKILL'] == 'yes'
             and all(state[key] == '' for key in native.OWNER_HOOK_FIELDS)
             and native.span_microseconds(state['TimeoutStartUSec']) == 30*1000000
             and native.span_microseconds(state['TimeoutStopUSec']) == 90*1000000
             and native.span_microseconds(state['RuntimeMaxUSec']) == COLLECTOR_RUNTIME_SECONDS*1000000
             and state['InvocationID'] == os.environ.get('INVOCATION_ID')
             and re.fullmatch('[0-9a-f]{32}', state['InvocationID']))
        need(self.group_members(self.collector_unit) == {os.getpid()})
        self.started = self.clock()

    def begin(self, transaction, attempt):
        need(transaction == self.transaction and attempt == self.attempt)
        c.directory(self.journal.parent)
        self.journal.mkdir(mode=0o700); c.fsync_directory(self.journal.parent)
        self.owned_journal = True
        hydrate.exclusive(self.journal / 'intent.json', plans.canonical({
            'schema': 'careertrellis.onedrive-native-collector/v1', 'transaction': transaction,
            'attempt': attempt, 'owner_unit': self.owner_unit, 'collector_unit': self.collector_unit,
            'collector_owner': self.owner, 'retention_policy_sha256': self.policy.digest,
            'artifact_revision': self.policy.data['artifact_revision'], 'observed_at': self.now().isoformat()}))

    def document(self, name):
        raw = native.protected(self.root / name, maximum=131072)
        value = native.decode(raw); need(plans.canonical(value) == raw)
        return value

    def evidence(self):
        ownership, receipt = self.document('ownership.json'), self.document('complete.json')
        plan = ownership['plan']; plans.validate_plan(plan)
        need(ownership['schema'] == 'careertrellis.native-local-restore-ownership/v1'
             and plan['transaction'] == self.transaction and type(plan['attempt']) is int
             and plan['attempt'] == self.attempt and plan['owner_unit'] == self.owner_unit
             and Path(plan['attempt_root']) == self.root
             and ownership['plan_sha256'] == plans.digest(plan) == receipt.get('plan_sha256'))
        meta = self.root.lstat(); need(list(native.identity(meta)[:2]) == ownership['root_identity'][:2])
        need(receipt.get('schema') == 'careertrellis.onedrive-native-local-restore/v1'
             and receipt.get('result') == 'verified' and receipt.get('transaction') == self.transaction
             and type(receipt.get('attempt')) is int and receipt['attempt'] == self.attempt
             and all(receipt.get(key) is True for key in ('native_restore_accepted', 'postmaster_terminal', 'checksums_verified'))
             and all(receipt.get(key) == plan[key] for key in ('archive_sha256', 'generation_sha256', 'backup_label'))
             and receipt.get('download_receipt_sha256') == ownership['download_receipt_sha256'])
        entries = list(self.root.iterdir()); need(len(entries) <= 1000)
        need(not any(path.name.startswith(('contained-', 'failure-')) or path.name in
             ('failed.json', 'failure.json', 'unconfirmed.json') for path in entries))
        need(not os.path.lexists(Path(plan['data_root']) / 'postmaster.pid'))
        need(self.terminal_identity(ownership['owner']))
        postmaster = self.document('postmaster.json')
        need(self.terminal_identity(postmaster) and self.group_empty(self.owner_unit))
        return ownership['owner'], receipt

    def observe_owner(self):
        state = self.show(self.owner_unit)
        if state['LoadState'] == 'not-found':
            need(self.seen_invocation is None and self.clock() - self.started < 30)
            return None
        need(state['LoadState'] == 'loaded' and state['Result'] == 'success')
        invocation = state['InvocationID']
        need(type(invocation) is str and re.fullmatch('[0-9a-f]{32}', invocation))
        if self.seen_invocation is None: self.seen_invocation = invocation
        need(self.seen_invocation == invocation)
        if state['ActiveState'] == 'activating':
            need(self.clock() - self.started < 30); return None
        need(state['ActiveState'] == 'active')
        if state['SubState'] == 'running': return None
        need(state['SubState'] == 'exited')
        owner, receipt = self.evidence()
        snapshot = retained_success(state, owner, self.owner_unit)
        return {'owner': owner, 'receipt': receipt, 'snapshot': snapshot}

    def stop_intent(self, observation):
        self.installed()
        intent = {'schema': 'careertrellis.onedrive-native-stop-intent/v1',
            'transaction': self.transaction, 'attempt': self.attempt, 'unit': self.owner_unit,
            'owner': observation['owner'], 'receipt_sha256': hashlib.sha256(plans.canonical(observation['receipt'])).hexdigest(),
            'unit_state': observation['snapshot'], 'observed_at': self.now().isoformat()}
        hydrate.exclusive(self.root / 'unit-stop-intent.json', plans.canonical(intent))
        return intent

    def stop_empty_owner(self, observation):
        # This explicit end action, not a status poll, serializes against all
        # authorized counter dispatches. Invocation/PIDs/group are rechecked.
        with c.control(c.STATE / 'db'):
            self.installed()
            need(self.observe_owner() == observation)
            hydrate.command(['/usr/bin/systemctl', 'stop', self.owner_unit], timeout=STOP_WAIT_SECONDS)

    def wait_stopped(self, observation, seconds):
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            state = self.show(self.owner_unit, STOP_QUERY_FIELDS)
            need(state['ControlGroup'] in ('', '/system.slice/' + self.owner_unit))
            if state['LoadState'] == 'loaded':
                need(state['InvocationID'] == observation['snapshot']['InvocationID']
                     and state['Result'] == 'success' and state['ExecMainStatus'] == '0')
            else: need(state['LoadState'] == 'not-found')
            if (state['ActiveState'] == 'inactive' and state['SubState'] == 'dead'
                    and state['MainPID'] == state['ControlPID'] == '0' and state['Job'] == ''
                    and self.group_empty(self.owner_unit)):
                owner, receipt = self.evidence()
                need(owner == observation['owner'] and receipt == observation['receipt'])
                return {key: state[key] for key in STOPPED_FIELDS}
            self.pause()
        raise CollectorRefused('native_collector_requires_reconciliation')

    def commit(self, proof, observation):
        self.installed()
        owner, receipt = self.evidence()
        need(owner == observation['owner'] and receipt == observation['receipt'])
        native.validate_terminal_proof(proof, receipt, owner, stop_intent=self.document('unit-stop-intent.json'))
        hydrate.exclusive(self.root / 'unit-terminal-proof.json', plans.canonical(proof))

    def finish(self, proof):
        hydrate.exclusive(self.journal / 'proof-ready.json', plans.canonical({
            'schema': 'careertrellis.onedrive-native-collector/v1', 'result': 'terminal_proof_ready',
            'proof_sha256': hashlib.sha256(plans.canonical(proof)).hexdigest(), 'observed_at': self.now().isoformat()}))

    def failed(self):
        if self.owned_journal:
            try:
                hydrate.exclusive(self.journal / 'failure.json', plans.canonical({
                    'schema': 'careertrellis.onedrive-native-collector/v1', 'result': 'failed',
                    'category': 'native_collector_requires_reconciliation', 'automatic_retry': False,
                    'observed_at': self.now().isoformat()}))
            except Exception: pass
