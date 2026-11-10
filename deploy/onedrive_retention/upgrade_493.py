"""493-baseline upgrade model; native application is deliberately unavailable.

Preparation performs protected reads only. Filesystem-backed simulations require
an explicit disposable test root. The old unlimited-owner proposal was not
accepted; native application needs a separately reviewed transaction against the
peer's accepted baseline. No restore probe authorizes a shared package exchange.
"""
import argparse
import ast
from contextlib import contextmanager
from dataclasses import dataclass
import datetime as dt
import hashlib
import importlib
import importlib.util
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid


PACKAGE = '/usr/local/libexec/careertrellis/paired_maintenance'
STANDALONE = '/usr/local/libexec/careertrellis/onedrive_retention'
PARENT = '/usr/local/libexec/careertrellis'
PRIVATE = '/etc/careertrellis/paired-maintenance'
STATE = '/var/lib/careertrellis/paired-maintenance'
CORE = PARENT + '/backup_health.py'
CLAIM = PRIVATE + '/onedrive-493-upgrade-intent.json'
RECEIPT = PRIVATE + '/onedrive-493-upgrade-receipt.json'
BACKUP = '/var/backups/careertrellis/onedrive-493-upgrade'
PAIR_STAGE = PARENT + '/.paired-onedrive-493-stage'
ONE_STAGE = PARENT + '/.onedrive-493-stage'
RELEASE = '7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS = '4095417909a8b13ad252be9420e422af3662bef5'
PAIR_FILES = frozenset(('__init__.py', 'common.py', 'runtime.py', 'provider.py',
    'client.py', 'ssh_gate.py', 'db_control.py', 'web_control.py', 'db_host.py', 'host.py', 'entry.py'))
ONE_FILES = frozenset(('__init__.py', 'bundle.py', 'capture_plan.py', 'hydrate.py',
    'local_restore_plan.py', 'local_restore.py', 'native_restore.py', 'policy.py',
    'publish.py', 'coordinator.py', 'endpoint.py', 'capacity_guard.py', 'entry.py',
    'catalog.py', 'retire.py', 'common_io.py', 'builder.py', 'upgrade_493.py'))
HELPERS = {'web': frozenset((CORE, '/usr/local/sbin/careertrellis-mirror-generated-objects')),
           'db': frozenset((CORE, '/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive'))}
NEW_UNITS = {'web': frozenset(('careertrellis-onedrive-retention.service',
                              'careertrellis-onedrive-retention.timer')), 'db': frozenset()}
TIMERS = {'web': ('careertrellis-paired-recovery.timer', 'careertrellis-paired-maintenance.timer'),
          'db': ('careertrellis-pgbackrest-full.timer', 'careertrellis-pgbackrest-differential.timer',
                 'careertrellis-pgbackrest-onedrive.timer')}
TIMER_PINS = {
    'careertrellis-paired-maintenance.timer': 'c4430798cb9a53a0ca151bbeb90dcacc25e415306fa154108bb3d7560b417b3c',
    'careertrellis-paired-recovery.timer': '4ed6f9b4c26f741d05dc9dc1f8173e2c2fd6662727b6ba7b6dfa0140bc25f96e',
    'careertrellis-pgbackrest-full.timer': '186668a9043b06bb1572928d4b27eb8da3e2242f45880455ec46339233d47192',
    'careertrellis-pgbackrest-differential.timer': 'bbd2f30e70f1ed84e6345e50b0b2cff3c724424804f62ad572fcca12dec976be',
    'careertrellis-pgbackrest-onedrive.timer': '971adbb5154f6b9d0259db5069f4d07692f4d7c489be14609997bf0fac14a5dd'}
MAX_FILE = 2 * 1024 ** 2
MAX_TOTAL = 64 * 1024 ** 2
RUN_FILES = frozenset(('abort-dispatch-intent.json', 'abort-intent.json', 'abort.json',
    'aborted-restored.json', 'backup-intent.json', 'backup-invocation.json', 'begin-intent.json',
    'capture-dispatch-intent.json', 'capture-result.json', 'capture.json', 'complete-intent.json',
    'complete-result.json', 'complete.json', 'db-begin-intent.json', 'db-health-accepted.json',
    'db-mirror-accepted.json', 'db-released.json', 'health-dispatch-intent.json', 'health-dispatched.json',
    'health-intent.json', 'health-unit-dispatch.json', 'health-unit-intent.json', 'intent.json',
    'mirror-intent.json', 'mirror-unit-intent.json', 'mirror-unit-dispatch.json', 'normal-reports-accepted.json',
    'peer-identity.json', 'policy.json', 'prestart-aborted.json', 'prestart-original-status.json',
    'public-policy.json', 'publication-result.json', 'publish-dispatch-intent.json', 'publish-intent.json',
    'quiesced.json', 'quiesced-result.json', 'recovery-dispatch-intent.json', 'recovery-failed.json',
    'recovery-restore-intent.json', 'restore-dispatch-intent.json', 'restore-intent.json', 'restored.json',
    'start-intent.json', 'status.json', 'stop-intent.json', 'verified-generation.json',
    'worker-dispatch.json', 'worker-started.json', 'writer-interlock.json', 'writers-restored.json'))
UPGRADE_SECONDS = 120
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
       'PYTHONDONTWRITEBYTECODE': '1'}


class UpgradeRefused(RuntimeError):
    """A fixed sanitized refusal; no private provider/configuration values."""


def need(value, code='onedrive_493_upgrade_refused'):
    if not value:
        raise UpgradeRefused(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def sha(raw): return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result); result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: need(False))


def stamp(value):
    need(type(value) is str and len(value) <= 48)
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(parsed.utcoffset() == dt.timedelta(0)); return parsed


def hexadecimal(value, length):
    return type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None


@dataclass(frozen=True)
class Baseline:
    revision: str
    manifest_sha256: str
    policy_sha256: str
    web_core: str
    db_core: str


FIXED = Baseline('493bf14a3bae2cb2b36f3a57191602b72ea6e3d3',
    '7a5122cb39b34137d9b2539d0bf60c0e3720e2854ebe8dcc30f1f1b18d092fd5',
    '3dcd50ba5bbc6fdd3270a83c7dd255c35f86c18f9da5d0613a55c8a4be94c880',
    'bb2434763298845c0b44fcc5793fdc7f3d4521174e3691e60cf283f5d8fc8f6c',
    '8d2facf6d71bf985c1c0ffe8b6a661fc9a9699548b6bb81163cd787f53ba97fa')


def validate_packet(packet):
    need(type(packet) is dict and set(packet) == {'manifest', 'paired', 'standalone', 'helpers', 'units'})
    manifest = packet['manifest']
    need(type(manifest) is dict and set(manifest) == {'schema', 'revision', 'paired', 'standalone', 'helpers', 'units'}
         and manifest['schema'] == 'careertrellis.onedrive-493-upgrade-packet/v1'
         and hexadecimal(manifest['revision'], 40) and manifest['revision'] != FIXED.revision)
    need(type(packet['paired']) is dict and set(packet['paired']) == PAIR_FILES)
    need(type(packet['standalone']) is dict
         and {'policy.py', 'capacity_guard.py', 'entry.py', 'coordinator.py', 'endpoint.py'} <= set(packet['standalone']) <= ONE_FILES)
    need(type(packet['helpers']) is dict and set(packet['helpers']) == {'web', 'db'}
         and type(packet['units']) is dict and set(packet['units']) == {'web', 'db'})
    need(type(manifest['helpers']) is dict and set(manifest['helpers']) == {'web', 'db'}
         and type(manifest['units']) is dict and set(manifest['units']) == {'web', 'db'})
    sections = [('paired', packet['paired'], manifest['paired']),
                ('standalone', packet['standalone'], manifest['standalone'])]
    for role in ('web', 'db'):
        need(type(packet['helpers'][role]) is dict and set(packet['helpers'][role]) <= HELPERS[role])
        need(type(packet['units'][role]) is dict and set(packet['units'][role]) <= NEW_UNITS[role])
        sections.extend((('helpers', packet['helpers'][role], manifest['helpers'][role]),
                         ('units', packet['units'][role], manifest['units'][role])))
    total = 0
    for section, values, hashes in sections:
        need(type(hashes) is dict and set(values) == set(hashes))
        for name, raw in values.items():
            need(type(raw) is bytes and 0 < len(raw) <= MAX_FILE
                 and hexadecimal(hashes[name], 64) and sha(raw) == hashes[name])
            total += len(raw); need(total <= MAX_TOTAL)
            if section in ('paired', 'standalone'):
                ast.parse(raw, filename='reviewed_module.py')
    need(type(manifest['helpers']) is dict and set(manifest['helpers']) == {'web', 'db'}
         and type(manifest['units']) is dict and set(manifest['units']) == {'web', 'db'})
    return manifest


def _policy(old, new, revision, now):
    need(type(old) is dict and type(new) is dict)
    need(old.get('schema') == 'careertrellis.paired-maintenance-policy/v1'
         and old.get('enabled') is True and old.get('source_readonly_attested') is True
         and old.get('no_external_writers_attested') is True)
    fixed = {'application_release': RELEASE, 'timezone': 'America/New_York', 'weekday': 6,
             'start_hour': 3, 'start_minute': 0, 'start_window_minutes': 60,
             'valid_until': '2027-01-24T00:00:00Z', 'minimum_onedrive_reserve_bytes': 64 * 1024 ** 2}
    need(all(old.get(key) == value for key, value in fixed.items()))
    need(stamp(old['valid_from']) <= now < stamp(old['valid_until']))
    mutable = {'manifest_revision', 'artifact_hashes'}
    need({key: value for key, value in old.items() if key not in mutable}
         == {key: value for key, value in new.items() if key not in mutable})
    need(new.get('manifest_revision') == revision)
    pins = new.get('artifact_hashes', {})
    need(type(pins) is dict and set(pins) <= {'web', 'db'})
    for role, values in pins.items():
        need(type(values) is dict and set(values) <= HELPERS[role])
        need(all(hexadecimal(value, 64) for value in values.values()))


def _manifest(values, revision, schema):
    return canonical({'schema': schema, 'revision': revision,
                      'files': {name: sha(raw) for name, raw in sorted(values.items())}})


def prepare(packet, new_policy, target, *, baseline=FIXED, now=None):
    """Read-only exact baseline preparation; no timer, lock or file mutation."""
    need(target.test_only or baseline is FIXED)
    now = now or dt.datetime.now(dt.timezone.utc)
    need(now.utcoffset() == dt.timedelta(0) and target.role in ('web', 'db'))
    manifest = validate_packet(packet)
    for name in (CLAIM, RECEIPT, BACKUP, PAIR_STAGE, ONE_STAGE, STANDALONE): target.absent(name)
    old_raw = target.read(PRIVATE + '/policy.json', 0o600)
    need(sha(old_raw) == baseline.policy_sha256)
    old = decode(old_raw)
    need(old.get('manifest_revision') == baseline.revision)
    _policy(old, new_policy, manifest['revision'], now)
    for role, values in new_policy.get('artifact_hashes', {}).items():
        previous = old.get('artifact_hashes', {}).get(role, {})
        need(set(previous) <= set(values))
        for path, expected in values.items():
            if path in packet['helpers'][role]: need(expected == sha(packet['helpers'][role][path]))
            else: need(path in previous and expected == previous[path])
    need(target.read(STATE + '/public-policy.json', 0o644) == old_raw)
    old_manifest_raw = target.read(PACKAGE + '/manifest.json', 0o644)
    need(sha(old_manifest_raw) == baseline.manifest_sha256)
    old_manifest = decode(old_manifest_raw)
    need(set(old_manifest) == {'schema', 'revision', 'files'}
         and old_manifest['schema'] == 'careertrellis.paired-maintenance-install/v1'
         and old_manifest['revision'] == baseline.revision and set(old_manifest['files']) == PAIR_FILES)
    need(target.names(PACKAGE) == PAIR_FILES | {'manifest.json'})
    for name, expected in old_manifest['files'].items():
        need(hexadecimal(expected, 64) and sha(target.read(PACKAGE + '/' + name, 0o644)) == expected)
    expected_core = baseline.web_core if target.role == 'web' else baseline.db_core
    need(sha(target.read(CORE, 0o644)) == expected_core)
    old_helpers = {}
    for path in packet['helpers'][target.role]:
        old_helpers[path] = target.read(path, 0o644 if path == CORE else 0o755)
    # Existing optional units may never be overwritten by this first install.
    for name in packet['units'][target.role]:
        target.absent('/etc/systemd/system/' + name)
        target.absent('/etc/systemd/system/' + name + '.d')
        target.absent('/run/systemd/system/' + name)
        target.absent('/run/systemd/system/' + name + '.d')
    status = decode(target.read(STATE + '/' + target.role + '/status.json', 0o600))
    need(set(status) == {'schema', 'policy_sha256', 'boot_id', 'release', 'phase', 'result',
                         'observed_at', 'services_restored'}
         and status['schema'] == 'careertrellis.paired-maintenance-status/v1'
         and status['policy_sha256'] == baseline.policy_sha256 and status['boot_id'] == target.boot
         and status['release'] == RELEASE and status['phase'] == 'idle' and status['result'] == 'ok'
         and status['services_restored'] is True and stamp(status['observed_at']) <= now)
    marker = None
    if target.role == 'web':
        marker = decode(target.read(STATE + '/writer-interlock.json', 0o644))
        need(marker == {'schema': 'careertrellis.paired-maintenance-interlock/v1',
            'policy_sha256': baseline.policy_sha256, 'boot_id': target.boot, 'release': RELEASE, 'phase': 'idle'})
    observation = target.observe(old, baseline.revision, now)
    need(observation['pointers'] == ({'current': RELEASE, 'previous': PREVIOUS}
                                    if target.role == 'web' else {'current': None, 'previous': None}))
    timers = target.timers()
    need(set(timers) == set(TIMERS[target.role]))
    for name, state in timers.items():
        need(set(state) == {'active', 'enabled', 'next_seconds', 'source_sha256'}
             and type(state['active']) is bool and state['enabled'] in ('enabled', 'disabled', 'static', 'enabled-runtime')
             and hexadecimal(state['source_sha256'], 64))
        if state['active'] and name != 'careertrellis-paired-recovery.timer':
            need(type(state['next_seconds']) in (int, float) and state['next_seconds'] > 900,
                 'scheduled_backup_too_close')
    need(type(observation['history']) is dict and observation['history']
         and all(type(name) is str and hexadecimal(value, 64) for name, value in observation['history'].items()))
    target.exchange_ready()
    peak = sum(len(raw) for raw in packet['paired'].values()) * 3
    peak += sum(len(raw) for raw in packet['standalone'].values()) * 2
    peak += (sum(len(raw) for raw in old_helpers.values())
             + sum(len(raw) for raw in packet['helpers'][target.role].values())) * 3
    peak += sum(len(raw) for raw in packet['units'][target.role].values()) * 2
    peak += len(canonical(new_policy)) * 4 + 1024 ** 2
    target.capacity(int(old['minimum_free_bytes']) + peak, 256)
    result = {'schema': 'careertrellis.onedrive-493-upgrade-plan/v1', 'result': 'prepared',
        'role': target.role, 'boot_id': target.boot, 'old_revision': baseline.revision,
        'new_revision': manifest['revision'], 'packet_sha256': sha(canonical(manifest)),
        'old_policy_sha256': baseline.policy_sha256, 'new_policy_sha256': sha(canonical(new_policy)),
        'old_helpers_sha256': {path: sha(raw) for path, raw in sorted(old_helpers.items())},
        'new_helpers_sha256': {path: sha(raw) for path, raw in sorted(packet['helpers'][target.role].items())},
        'observation': observation, 'timers': timers, 'maximum_upgrade_seconds': UPGRADE_SECONDS,
        'recovery_observer_short_pause_exception': target.role == 'web',
        'conservative_peak_bytes': peak, 'activation_performed': False,
        'historical_receipts_modified': False, 'retention_policy_created': False}
    return result, old_raw, old_manifest_raw, old_helpers, status, marker


def apply(packet, new_policy, target, *, approved=False, baseline=FIXED, now=None):
    """Upgrade under existing fences; never restart writers or source jobs."""
    need(target.test_only is True, 'native_upgrade_requires_accepted_baseline')
    need(approved is True, 'explicit_upgrade_approval_required')
    owner = target.require_owner()
    need(type(owner) is dict and set(owner) == {'pid', 'start_ticks', 'boot_id', 'unit', 'invocation_id'}
         and type(owner['pid']) is int and owner['pid'] > 1
         and type(owner['start_ticks']) is int and owner['start_ticks'] > 0
         and owner['boot_id'] == target.boot and hexadecimal(owner['invocation_id'], 32))
    with target.fences():
        prepared, old_raw, old_manifest_raw, old_helpers, status, marker = prepare(
            packet, new_policy, target, baseline=baseline, now=now)
        prepared = dict(prepared, owner=owner)
        started = time.monotonic()
        target.write(CLAIM, canonical(prepared), 0o600)
        try:
            target.arm_containment()
            target.mkdir(BACKUP, 0o700)
            snapshots = {'private-policy.json': old_raw, 'public-policy.json': old_raw,
                         'old-manifest.json': old_manifest_raw,
                         'old-status.json': target.read(STATE + '/' + target.role + '/status.json', 0o600)}
            if marker is not None: snapshots['old-writer-interlock.json'] = canonical(marker)
            for index, path in enumerate(sorted(old_helpers)):
                snapshots['helper-%02d' % index] = old_helpers[path]
            for name, raw in snapshots.items(): target.write(BACKUP + '/' + name, raw, 0o600)
            target.write(BACKUP + '/bindings.json', canonical(prepared), 0o600)
            target.write(BACKUP + '/packet-manifest.json', canonical(packet['manifest']), 0o600)
            target.mkdir(PAIR_STAGE, 0o755); target.mkdir(ONE_STAGE, 0o755)
            for name, raw in packet['paired'].items(): target.write(PAIR_STAGE + '/' + name, raw, 0o644)
            target.write(PAIR_STAGE + '/manifest.json', _manifest(packet['paired'],
                prepared['new_revision'], 'careertrellis.paired-maintenance-install/v1'), 0o644)
            for name, raw in packet['standalone'].items(): target.write(ONE_STAGE + '/' + name, raw, 0o644)
            target.write(ONE_STAGE + '/manifest.json', _manifest(packet['standalone'],
                prepared['new_revision'], 'careertrellis.onedrive-retention-install/v1'), 0o644)
            target.pause_timers(prepared['timers'])
            paused_at = time.monotonic()
            need(target.observe(decode(old_raw), baseline.revision, now or dt.datetime.now(dt.timezone.utc))
                 == prepared['observation'], 'baseline_changed_before_exchange')
            need(time.monotonic() - started < UPGRADE_SECONDS, 'upgrade_budget_exceeded')
            # Namespace is absent: publish it before new paired imports can use it.
            target.publish_absent(ONE_STAGE, STANDALONE)
            target.exchange(PAIR_STAGE, PACKAGE)
            target.retain_package(PAIR_STAGE, BACKUP + '/paired-package')
            for path, raw in packet['helpers'][target.role].items():
                target.replace(path, raw, 0o644 if path == CORE else 0o755)
            for name, raw in packet['units'][target.role].items():
                target.write('/etc/systemd/system/' + name, raw, 0o644)
            target.replace(PRIVATE + '/policy.json', canonical(new_policy), 0o600)
            target.replace(STATE + '/public-policy.json', canonical(new_policy), 0o644)
            # This is an idle binding projection, never a new capture/freshness.
            next_status = dict(status, policy_sha256=prepared['new_policy_sha256'])
            target.replace(STATE + '/' + target.role + '/status.json', canonical(next_status), 0o600)
            if marker is not None:
                target.replace(STATE + '/writer-interlock.json',
                    canonical(dict(marker, policy_sha256=prepared['new_policy_sha256'])), 0o644)
            for folder, values in ((PACKAGE, packet['paired']), (STANDALONE, packet['standalone'])):
                for name, raw in values.items(): need(target.read(folder + '/' + name, 0o644) == raw)
                schema = ('careertrellis.paired-maintenance-install/v1' if folder == PACKAGE
                          else 'careertrellis.onedrive-retention-install/v1')
                need(target.names(folder) == set(values) | {'manifest.json'}
                     and target.read(folder + '/manifest.json', 0o644)
                     == _manifest(values, prepared['new_revision'], schema))
            for path, raw in packet['helpers'][target.role].items():
                need(target.read(path, 0o644 if path == CORE else 0o755) == raw)
            need(target.read(PRIVATE + '/policy.json', 0o600) == canonical(new_policy)
                 and target.read(STATE + '/public-policy.json', 0o644) == canonical(new_policy))
            after = target.observe(new_policy, prepared['new_revision'], dt.datetime.now(dt.timezone.utc))
            need(after == prepared['observation'], 'protected_runtime_or_history_changed')
            if packet['units'][target.role]: target.reload_new_units(packet['units'][target.role])
            need(time.monotonic() - started < UPGRADE_SECONDS, 'upgrade_budget_exceeded')
            target.restore_timers(prepared['timers'])  # Recovery observer restores first.
            restored = target.timers()
            need(all(restored[name]['active'] == value['active']
                     and restored[name]['enabled'] == value['enabled']
                     and restored[name]['source_sha256'] == value['source_sha256']
                     for name, value in prepared['timers'].items()), 'timer_restoration_unconfirmed')
            result = dict(prepared, result='upgraded_enabled_pair_retention_unarmed',
                installed_bytes_verified=True, protected_runtime_unchanged=True, timers_restored=True,
                recovery_observer_pause_seconds=round(time.monotonic() - paused_at, 3),
                elapsed_seconds=round(time.monotonic() - started, 3), retained_previous=BACKUP)
            target.write(RECEIPT, canonical(result), 0o600)
            target.disarm_containment()
            return result
        except BaseException:
            target.contain('upgrade_requires_reconciliation')
            raise UpgradeRefused('upgrade_unconfirmed_no_retry') from None


class Target:
    """Protected filesystem boundary; test roots never become native authority."""
    def __init__(self, root='/', *, test_only=False, role=None, boot=None):
        self.root = Path(root).absolute(); self.test_only = test_only
        if test_only:
            need(self.root != Path(self.root.anchor), 'test_root_is_filesystem_root')
            need(role in ('web', 'db')); self.role, self.boot = role, boot
        else:
            need(sys.platform == 'linux' and self.root == Path('/')
                 and os.geteuid() == os.getegid() == 0)
            hosts = {'careertrellis-staging-web-1': 'web', 'job-applier-db-staging-01': 'db'}
            need(socket.gethostname() in hosts); self.role = hosts[socket.gethostname()]
            self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        need(type(self.boot) is str and str(uuid.UUID(self.boot)) == self.boot)
        self.fds, self._signals = [], {}

    def path(self, name):
        need(type(name) is str and name.startswith('/') and '\\' not in name
             and all(part not in ('', '.', '..') for part in name.split('/')[1:]))
        return self.root.joinpath(*PurePosixPath(name).parts[1:])

    def directory(self, name, mode=None, sticky=False):
        path = self.path(name)
        for candidate in (path, *path.parents):
            if not candidate.is_relative_to(self.root): break
            meta = candidate.lstat()
            need(stat.S_ISDIR(meta.st_mode) and not getattr(meta, 'st_file_attributes', 0) & 0x400)
            if not self.test_only:
                need(meta.st_uid == meta.st_gid == 0)
                need(stat.S_IMODE(meta.st_mode) == 0o1777 if sticky and str(candidate) == '/run/lock'
                     else not meta.st_mode & 0o022)
        if mode is not None and not self.test_only: need(stat.S_IMODE(path.lstat().st_mode) == mode)

    def read(self, name, mode=0o644, limit=MAX_FILE):
        path = self.path(name); self.directory(str(PurePosixPath(name).parent))
        before = path.lstat()
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit
             and not getattr(before, 'st_file_attributes', 0) & 0x400)
        if not self.test_only:
            need(before.st_uid == before.st_gid == 0 and stat.S_IMODE(before.st_mode) == mode)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
                     | getattr(os, 'O_BINARY', 0))
        try:
            # Python 3.12 Windows lstat/fstat expose differing ctime semantics.
            # Only synthetic test roots omit that field; Linux/native never does.
            identity = lambda meta: (meta.st_dev, meta.st_ino, meta.st_mode, meta.st_uid,
                meta.st_gid, meta.st_nlink, meta.st_size, meta.st_mtime_ns) + (
                    () if self.test_only and os.name == 'nt' else (meta.st_ctime_ns,))
            need(identity(os.fstat(fd)) == identity(before))
            parts, remaining = [], before.st_size
            while remaining:
                part = os.read(fd, min(65536, remaining)); need(bool(part))
                parts.append(part); remaining -= len(part)
            need(not os.read(fd, 1) and identity(os.fstat(fd)) == identity(before)
                 and identity(path.lstat()) == identity(before))
            return b''.join(parts)
        finally: os.close(fd)

    def names(self, name):
        self.directory(name)
        values = list(itertools.islice(self.path(name).iterdir(), 1025)); need(len(values) <= 1024)
        return {item.name for item in values}

    def absent(self, name): need(not os.path.lexists(self.path(name)))

    def fsync(self, name):
        if self.test_only and os.name == 'nt': return
        fd = os.open(self.path(name), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)

    def mkdir(self, name, mode):
        self.directory(str(PurePosixPath(name).parent)); self.path(name).mkdir(mode=mode)
        if not self.test_only:
            os.chown(self.path(name), 0, 0); os.chmod(self.path(name), mode)
        self.fsync(str(PurePosixPath(name).parent))

    def write(self, name, raw, mode):
        self.directory(str(PurePosixPath(name).parent))
        fd = os.open(self.path(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), mode)
        try:
            if not self.test_only: os.fchown(fd, 0, 0); os.fchmod(fd, mode)
            view = memoryview(raw)
            while view:
                count = os.write(fd, view); need(count > 0); view = view[count:]
            os.fsync(fd)
        finally: os.close(fd)
        self.fsync(str(PurePosixPath(name).parent)); need(self.read(name, mode) == raw)

    def replace(self, name, raw, mode):
        self.read(name, mode)
        parent = str(PurePosixPath(name).parent)
        temporary = parent + '/.493-upgrade-' + uuid.uuid4().hex
        self.write(temporary, raw, mode); os.replace(self.path(temporary), self.path(name))
        self.fsync(parent); need(self.read(name, mode) == raw)

    def exchange_ready(self):
        if self.test_only: return
        import ctypes
        need(hasattr(ctypes.CDLL(None), 'renameat2'))
        self.directory(PARENT); self.directory('/var/backups/careertrellis')
        need(self.path(PARENT).stat().st_dev == self.path('/var/backups/careertrellis').stat().st_dev)

    def exchange(self, first, second):
        self.exchange_ready()
        if self.test_only:
            temporary = first + '-test-exchange'; self.absent(temporary)
            os.rename(self.path(first), self.path(temporary)); os.rename(self.path(second), self.path(first))
            os.rename(self.path(temporary), self.path(second))
        else:
            import ctypes
            function = ctypes.CDLL(None, use_errno=True).renameat2
            function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            need(function(-100, os.fsencode(self.path(first)), -100, os.fsencode(self.path(second)), 2) == 0)
        self.fsync(PARENT)

    def publish_absent(self, first, second):
        self.absent(second)
        if self.test_only: os.rename(self.path(first), self.path(second))
        else:
            import ctypes
            function = ctypes.CDLL(None, use_errno=True).renameat2
            function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            need(function(-100, os.fsencode(self.path(first)), -100, os.fsencode(self.path(second)), 1) == 0)
        self.fsync(PARENT)

    def retain_package(self, first, second):
        self.absent(second); os.chmod(self.path(first), 0o700)
        os.rename(self.path(first), self.path(second)); self.fsync(PARENT); self.fsync(BACKUP)

    def capacity(self, minimum, inodes):
        import shutil
        for name in (PARENT, PRIVATE, STATE, '/var/backups/careertrellis'):
            need(shutil.disk_usage(self.path(name)).free >= minimum)
            if not self.test_only: need(os.statvfs(self.path(name)).f_favail >= inodes)

    def _run(self, args, timeout=15):
        import selectors
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, env=ENV, pass_fds=tuple(self.fds)) as child:
            try:
                output = bytearray(); deadline = time.monotonic() + timeout
                selector = selectors.DefaultSelector()
                try:
                    selector.register(child.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic(); need(remaining > 0)
                        need(selector.select(remaining))
                        raw = os.read(child.stdout.fileno(), min(65536, 262145 - len(output)))
                        if not raw: break
                        output.extend(raw); need(len(output) <= 262144)
                finally: selector.close(); child.stdout.close()
                need(child.wait(timeout=max(.01, deadline - time.monotonic())) == 0)
                return bytes(output)
            finally:
                if child.poll() is None: child.kill(); child.wait()

    def state(self, name, fields):
        need(re.fullmatch(r'careertrellis-[a-z0-9@_.-]+\.(?:service|timer)|postgresql@18-main\.service|caddy\.service', name))
        raw = self._run(['/usr/bin/systemctl', 'show', name, '--property=' + ','.join(fields)])
        pairs = [line.split('=', 1) for line in raw.decode('ascii').splitlines()]
        need(all(len(pair) == 2 for pair in pairs) and len(pairs) == len(dict(pairs)))
        value = dict(pairs); need(set(value) == set(fields)); return value

    def _module(self, revision):
        sys.dont_write_bytecode = True
        prefix = '_verified_upgrade_' + revision
        if prefix not in sys.modules:
            specification = importlib.util.spec_from_file_location(prefix, self.path(PACKAGE + '/__init__.py'),
                submodule_search_locations=[str(self.path(PACKAGE))])
            module = importlib.util.module_from_spec(specification); sys.modules[prefix] = module
            specification.loader.exec_module(module)
        parent = str(self.path(PARENT))
        if parent not in sys.path: sys.path.insert(0, parent)
        return importlib.import_module(prefix + '.common'), importlib.import_module(prefix + '.runtime')

    def _history(self):
        base = STATE + '/' + self.role
        result = {}; total = 0; complete = 0; runs = {}
        for branch in ('runs', 'slots', 'policy-transitions'):
            root = base + '/' + branch
            if not os.path.lexists(self.path(root)): continue
            self.directory(root, 0o700)
            paths = list(itertools.islice(self.path(root).rglob('*'), 1025)); need(len(paths) <= 1024)
            for path in paths:
                relative = path.relative_to(self.path(root)); need(len(relative.parts) <= 3)
                meta = path.lstat()
                if stat.S_ISDIR(meta.st_mode):
                    need(branch != 'slots' and len(relative.parts) == 1
                         and hexadecimal(relative.name, 32 if branch == 'runs' else 64))
                    self.directory(root + '/' + relative.as_posix(), 0o700); continue
                if branch == 'slots':
                    need(len(relative.parts) == 1 and re.fullmatch(r'[0-9]{8}(?:-[0-9a-f]{64})?\.json', relative.name))
                elif branch == 'policy-transitions':
                    need(len(relative.parts) == 2 and hexadecimal(relative.parts[0], 64)
                         and relative.name in ('intent.json', 'success.json', 'old-policy.json', 'old-status.json'))
                else:
                    need(len(relative.parts) == 2 and hexadecimal(relative.parts[0], 32)
                         and relative.name in RUN_FILES, 'unknown_history_file')
                name = root + '/' + relative.as_posix()
                raw = self.read(name, 0o600); total += len(raw); need(total <= 32 * 1024 ** 2)
                value = decode(raw); need(type(value) is dict)
                result[name] = sha(raw)
                if branch == 'runs': runs.setdefault(relative.parts[0], {})[relative.name] = value
                if branch == 'runs' and relative.name in ('complete.json', 'complete-result.json'):
                    need(len(relative.parts) == 2 and hexadecimal(relative.parts[0], 32)
                         and value.get('transaction') == relative.parts[0]
                         and hexadecimal(value.get('policy_sha256'), 64))
                    if self.role == 'web': need(value.get('services_restored') is True)
                    complete += 1
        for transaction, values in runs.items():
            terminal = next((values[name] for name in ('complete.json', 'complete-result.json',
                'aborted-restored.json', 'prestart-aborted.json') if name in values), None)
            need(type(terminal) is dict and terminal.get('transaction') == transaction
                 and hexadecimal(terminal.get('policy_sha256'), 64), 'unfinished_history_requires_reconciliation')
            if 'prestart-aborted.json' in values:
                proof = values['prestart-aborted.json']
                need(proof.get('no_dispatch') is True and proof.get('services_unchanged') is True
                     and proof.get('services_restored') is True and proof.get('restoration_performed') is False)
            elif self.role == 'web': need(terminal.get('services_restored') is True)
            else:
                worker = values.get('status.json', {})
                need('complete-result.json' in values and worker.get('state') == 'complete'
                     and worker.get('transaction') == transaction and worker.get('services_restored') is True
                     and worker.get('policy_sha256') == terminal['policy_sha256'])
        need(complete > 0, 'accepted_terminal_history_missing')
        return result

    def _locks(self):
        return ((STATE + '/' + self.role + '/control.lock', 0o600),
                ('/run/lock/careertrellis-maintenance.lock', 0o644 if self.role == 'web' else 0o600),
                ('/run/lock/careertrellis-generated-object-recovery.lock' if self.role == 'web'
                 else '/run/lock/careertrellis-pgbackrest-repository.lock', 0o600))

    def lock_observation(self):
        metadata = {}
        extra = ('careertrellis-postgres-migration.lock', 'careertrellis-sqlite-import.lock',
                 'careertrellis-provider-secret-rotation.lock')
        names = list(self._locks()) + [('/run/lock/' + name, None) for name in extra]
        for name, mode in names:
            if mode is None and not os.path.lexists(self.path(name)): continue
            self.directory(str(PurePosixPath(name).parent), sticky=True)
            meta = self.path(name).lstat()
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                 and meta.st_nlink == 1 and (mode is None or stat.S_IMODE(meta.st_mode) == mode))
            metadata[(os.major(meta.st_dev), os.minor(meta.st_dev), meta.st_ino)] = name
        raw = Path('/proc/locks').read_bytes(); need(len(raw) <= MAX_FILE)
        for line in raw.decode('ascii').splitlines():
            fields = line.split()
            for index, field in enumerate(fields):
                match = re.fullmatch('([0-9a-f]+):([0-9a-f]+):([0-9]+)', field)
                if match and tuple(int(value, 16 if part < 2 else 10)
                                   for part, value in enumerate(match.groups())) in metadata:
                    need(self.fds and fields[index - 1] == str(os.getpid()), 'operation_lock_busy')

    def observe(self, policy, revision, now):
        self.lock_observation()
        self.absent('/run/careertrellis-hosted-activation.lease')
        common, runtime = self._module(revision)
        accepted = common.Policy(policy); observed = runtime.preflight_runtime(accepted, self.role)
        observed.verify_sources()
        if self.role == 'web': observed.health()
        else:
            facts = observed.database()
            need(facts['schema'] == policy['schema_revision'] and facts['policy_rows'] == 1
                 and facts['maintenance'] is False and facts['signup_open'] is True
                 and all(facts[key] == 0 for key in ('search_queue', 'active_searches', 'erasure_queue', 'prepared')))
        busy = (('careertrellis-paired-maintenance.service', 'careertrellis-paired-recovery.service')
                if self.role == 'web' else runtime.BACKUP_UNITS)
        for name in busy: observed.terminal(name)
        raw = self._run(['/usr/bin/systemctl', 'list-units', '--all', '--no-legend', '--plain',
            '--no-pager', 'careertrellis-paired-db@*.service', 'careertrellis-paired-db-recovery@*.service',
            'careertrellis-onedrive-bundle-*.service'])
        rows = raw.decode('ascii').splitlines(); need(len(rows) <= 128)
        for line in rows:
            name = line.split()[0]
            need(re.fullmatch(r'careertrellis-(?:paired-db(?:-recovery)?@|onedrive-bundle-(?:hydrate|publish|restore)-)[0-9a-f]{32}\.service', name))
            observed.terminal(name)
        units = {}
        names = (*runtime.SERVICES, 'caddy.service') if self.role == 'web' else ('postgresql@18-main.service',)
        for name in names:
            value = self.state(name, ('ActiveState', 'SubState', 'MainPID', 'ControlPID', 'Job', 'InvocationID'))
            need(value['ActiveState'] == 'active' and value['SubState'] == 'running'
                 and value['MainPID'].isdigit() and int(value['MainPID']) > 0
                 and value['ControlPID'] == '0' and not value['Job'] and hexadecimal(value['InvocationID'], 32))
            units[name] = value
        if self.role == 'web':
            name = 'careertrellis-paired-bootstrap.service'
            bootstrap = self.state(name, ('ActiveState', 'SubState', 'RemainAfterExit', 'MainPID',
                'ControlPID', 'Job', 'Result', 'ExecMainStatus', 'FragmentPath', 'DropInPaths',
                'NeedDaemonReload', 'InvocationID'))
            need(bootstrap['ActiveState'] == 'active' and bootstrap['SubState'] == 'exited'
                 and bootstrap['RemainAfterExit'] == 'yes' and bootstrap['MainPID'] == bootstrap['ControlPID'] == '0'
                 and not bootstrap['Job'] and bootstrap['Result'] == 'success' and bootstrap['ExecMainStatus'] == '0'
                 and bootstrap['FragmentPath'] == '/etc/systemd/system/' + name and not bootstrap['DropInPaths']
                 and bootstrap['NeedDaemonReload'] == 'no' and hexadecimal(bootstrap['InvocationID'], 32))
            units[name] = bootstrap
        pointers = {'current': None, 'previous': None}
        for name in pointers:
            path = self.path('/opt/careertrellis/staging/' + name)
            if self.role == 'db': need(not os.path.lexists(path)); continue
            need(path.is_symlink()); resolved = path.resolve(strict=True)
            need(resolved.parent == self.path('/opt/careertrellis/staging/releases') and hexadecimal(resolved.name, 40))
            pointers[name] = resolved.name
        return {'pointers': pointers, 'units': units, 'history': self._history(), 'locks_idle': True}

    def timers(self):
        result = {}
        for name in TIMERS[self.role]:
            values = self.state(name, ('LoadState', 'ActiveState', 'Job', 'UnitFileState',
                'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'NextElapseUSecRealtime'))
            need(values['LoadState'] == 'loaded' and values['ActiveState'] in ('active', 'inactive')
                 and not values['Job'] and values['FragmentPath'] == '/etc/systemd/system/' + name
                 and not values['DropInPaths'] and values['NeedDaemonReload'] == 'no')
            deadline = None
            if values['ActiveState'] == 'active' and name != 'careertrellis-paired-recovery.timer':
                try: parsed = dt.datetime.strptime(values['NextElapseUSecRealtime'], '%a %Y-%m-%d %H:%M:%S UTC')
                except ValueError: raise UpgradeRefused('timer_deadline_unproved') from None
                deadline = (parsed.replace(tzinfo=dt.timezone.utc) - dt.datetime.now(dt.timezone.utc)).total_seconds()
            source = sha(self.read('/etc/systemd/system/' + name, 0o644)); need(source == TIMER_PINS[name])
            result[name] = {'active': values['ActiveState'] == 'active', 'enabled': values['UnitFileState'],
                'next_seconds': deadline, 'source_sha256': source}
        return result

    def pause_timers(self, snapshot):
        for name in TIMERS[self.role]:
            if snapshot[name]['active']: self._run(['/usr/bin/systemctl', 'stop', name])
        need(all(not value['active'] for value in self.timers().values()))

    def restore_timers(self, snapshot):
        for name in TIMERS[self.role]:
            if snapshot[name]['active']: self._run(['/usr/bin/systemctl', 'start', name])

    def reload_new_units(self, units):
        need(set(units) <= NEW_UNITS[self.role]); self._run(['/usr/bin/systemctl', 'daemon-reload'])
        for name in units:
            fields = ('ActiveState', 'Job', 'NeedDaemonReload', 'FragmentPath', 'DropInPaths', 'UnitFileState')
            if name.endswith('.service'): fields += ('MainPID', 'ControlPID')
            value = self.state(name, fields)
            need(value['ActiveState'] == 'inactive' and not value['Job'] and value['NeedDaemonReload'] == 'no'
                 and value['FragmentPath'] == '/etc/systemd/system/' + name and not value['DropInPaths']
                 and value['UnitFileState'] in ('disabled', 'static'))
            if name.endswith('.service'): need(value['MainPID'] == value['ControlPID'] == '0')

    def require_owner(self):
        # The old unlimited-owner proposal was not accepted. A shared package
        # exchange needs its own reviewed recovery contract against the peer's
        # accepted baseline; disposable restore probes do not authorize it.
        raise UpgradeRefused('native_upgrade_requires_accepted_baseline')

    @contextmanager
    def fences(self):
        import fcntl
        try:
            for name, mode in self._locks():
                self.directory(str(PurePosixPath(name).parent), sticky=True)
                before = self.path(name).lstat()
                need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
                     and before.st_nlink == 1 and before.st_size == 0 and stat.S_IMODE(before.st_mode) == mode)
                fd = os.open(self.path(name), os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK); self.fds.append(fd)
                need((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (before.st_dev, before.st_ino))
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            for fd in reversed(self.fds): os.close(fd)
            self.fds = []

    def arm_containment(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            self._signals[sig] = signal.signal(sig, lambda _sig, _frame: self.contain('operator_signal'))

    def disarm_containment(self):
        for sig, previous in self._signals.items(): signal.signal(sig, previous)
        self._signals = {}

    def contain(self, reason):
        for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, signal.SIG_IGN)
        try: self.write(BACKUP + '/operator-required.json', canonical({'reason': reason,
            'pid': os.getpid(), 'boot_id': self.boot}), 0o600)
        except Exception: pass
        while True: time.sleep(60)


def load_packet(root, expected_sha256, target):
    """Load only fixed public payload paths beneath the caller's private packet."""
    need(type(root) is str and hexadecimal(expected_sha256, 64))
    target.directory(root, 0o700)
    raw = target.read(root + '/packet.json', 0o600, MAX_FILE)
    need(sha(raw) == expected_sha256)
    manifest = decode(raw); need(raw == canonical(manifest))
    packet = {'manifest': manifest, 'paired': {}, 'standalone': {}, 'helpers': {'web': {}, 'db': {}}, 'units': {'web': {}, 'db': {}}}
    need(type(manifest) is dict and set(manifest) == {'schema', 'revision', 'paired', 'standalone', 'helpers', 'units'})
    for section, allowed in (('paired', PAIR_FILES), ('standalone', ONE_FILES)):
        need(type(manifest[section]) is dict and set(manifest[section]) <= allowed)
        for name in manifest[section]: packet[section][name] = target.read(root + '/' + section + '/' + name, 0o644)
    for section, allowed in (('helpers', HELPERS), ('units', NEW_UNITS)):
        need(type(manifest[section]) is dict and set(manifest[section]) == {'web', 'db'})
        for role in ('web', 'db'):
            need(type(manifest[section][role]) is dict and set(manifest[section][role]) <= allowed[role])
            for name in manifest[section][role]:
                leaf = PurePosixPath(name).name
                packet[section][role][name] = target.read(root + '/' + section + '/' + role + '/' + leaf, 0o644)
    validate_packet(packet)
    new_policy = decode(target.read(root + '/new-paired-policy.json', 0o600))
    return packet, new_policy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet-root', required=True)
    parser.add_argument('--packet-sha256', required=True)
    parser.add_argument('--approved-upgrade', action='store_true')
    args = parser.parse_args(argv)
    try:
        target = Target(); packet, policy = load_packet(args.packet_root, args.packet_sha256, target)
        if args.approved_upgrade: value = apply(packet, policy, target, approved=True)
        else: value = prepare(packet, policy, target)[0]
        print(json.dumps(value, sort_keys=True)); return 0
    except BaseException:
        print('{"result":"onedrive_493_upgrade_refused_or_unconfirmed_no_retry"}'); return 1


if __name__ == '__main__': raise SystemExit(main())
