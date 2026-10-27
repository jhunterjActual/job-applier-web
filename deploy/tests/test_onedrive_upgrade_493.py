"""Filesystem-backed upgrade tests; native systemd/atomic exchange need Linux proof."""
import copy
from contextlib import contextmanager
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'onedrive_retention' / 'upgrade_493.py'
SPEC = importlib.util.spec_from_file_location('onedrive_upgrade_493', SOURCE)
import sys
upgrade = importlib.util.module_from_spec(SPEC); sys.modules[SPEC.name] = upgrade
SPEC.loader.exec_module(upgrade)
NOW = dt.datetime(2026, 10, 27, tzinfo=dt.timezone.utc)
BOOT = '11111111-1111-4111-8111-111111111111'


class Contained(RuntimeError): pass


class Fake(upgrade.Target):
    def __init__(self, root, role):
        super().__init__(root, test_only=True, role=role, boot=BOOT)
        self.events = []; self.held = False; self.fail = None
        self.observation = {'pointers': {'current': upgrade.RELEASE, 'previous': upgrade.PREVIOUS}
            if role == 'web' else {'current': None, 'previous': None},
            'units': {'protected': {'MainPID': '123', 'InvocationID': 'a' * 32}},
            'history': {'accepted-complete.json': 'b' * 64}, 'locks_idle': True}
        self.timer_state = {name: {'active': True, 'enabled': 'enabled',
            'next_seconds': 60 if 'recovery' in name else 3600, 'source_sha256': 'c' * 64}
            for name in upgrade.TIMERS[role]}
        if role == 'db': self.timer_state[upgrade.TIMERS[role][0]].update(active=False, enabled='disabled')
    def event(self, name):
        self.events.append(name)
        if self.fail == name: raise RuntimeError('synthetic private failure')
    def observe(self, policy, revision, now):
        self.event('observe'); return copy.deepcopy(self.observation)
    def timers(self): return copy.deepcopy(self.timer_state)
    def capacity(self, minimum, inodes): self.event('capacity')
    def require_owner(self):
        self.event('owner')
        return {'pid': 1234, 'start_ticks': 5678, 'boot_id': BOOT,
                'unit': 'careertrellis-onedrive-493-upgrade-' + self.role + '.service', 'invocation_id': 'e' * 32}
    @contextmanager
    def fences(self):
        self.held = True; self.events.append('fences')
        try: yield
        finally:
            # Model native containment's non-returning owner instead of releasing.
            if 'contained' not in self.events: self.held = False; self.events.append('release')
    def arm_containment(self): self.events.append('armed')
    def disarm_containment(self): self.events.append('disarmed')
    def contain(self, reason): self.events.append('contained'); raise Contained(reason)
    def pause_timers(self, snapshot):
        self.event('pause')
        for value in self.timer_state.values(): value['active'] = False
    def restore_timers(self, snapshot):
        self.event('restore-timers'); self.timer_state = copy.deepcopy(snapshot)
    def publish_absent(self, first, second):
        self.event('publish-standalone'); super().publish_absent(first, second)
    def exchange(self, first, second): self.event('exchange'); super().exchange(first, second)
    def reload_new_units(self, units): self.event('reload')


def packet():
    pairs = {name: b'# reviewed paired fixture\n' for name in upgrade.PAIR_FILES}
    one = {name: b'# reviewed standalone fixture\n' for name in
           ('policy.py', 'capacity_guard.py', 'entry.py', 'coordinator.py', 'endpoint.py')}
    helpers = {'web': {upgrade.CORE: b'# new web health core\n'},
               'db': {'/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive': b'#!/bin/bash\ntrue\n'}}
    manifest = {'schema': 'careertrellis.onedrive-493-upgrade-packet/v1', 'revision': 'd' * 40,
        'paired': {name: upgrade.sha(raw) for name, raw in pairs.items()},
        'standalone': {name: upgrade.sha(raw) for name, raw in one.items()},
        'helpers': {role: {name: upgrade.sha(raw) for name, raw in values.items()} for role, values in helpers.items()},
        'units': {'web': {}, 'db': {}}}
    return {'manifest': manifest, 'paired': pairs, 'standalone': one,
            'helpers': helpers, 'units': {'web': {}, 'db': {}}}


def fixture(root, role):
    target = Fake(root, role)
    for path in (upgrade.PRIVATE, upgrade.STATE + '/' + role, upgrade.PACKAGE,
                 '/var/backups/careertrellis', '/usr/local/sbin'):
        target.path(path).mkdir(parents=True, exist_ok=True)
    old_files = {name: b'# old fixture\n' for name in upgrade.PAIR_FILES}
    old_manifest = upgrade._manifest(old_files, upgrade.FIXED.revision, 'careertrellis.paired-maintenance-install/v1')
    for name, raw in old_files.items(): target.write(upgrade.PACKAGE + '/' + name, raw, 0o644)
    target.write(upgrade.PACKAGE + '/manifest.json', old_manifest, 0o644)
    old_core = b'# original accepted core\n'; target.write(upgrade.CORE, old_core, 0o644)
    old = {'schema': 'careertrellis.paired-maintenance-policy/v1', 'enabled': True,
        'source_readonly_attested': True, 'no_external_writers_attested': True,
        'application_release': upgrade.RELEASE, 'timezone': 'America/New_York', 'weekday': 6,
        'start_hour': 3, 'start_minute': 0, 'start_window_minutes': 60,
        'valid_from': '2026-10-26T00:00:00Z', 'valid_until': '2027-01-24T00:00:00Z',
        'minimum_onedrive_reserve_bytes': 64 * 1024 ** 2, 'minimum_free_bytes': 5 * 1024 ** 3,
        'manifest_revision': upgrade.FIXED.revision,
        'artifact_hashes': {'web': {upgrade.CORE: upgrade.sha(old_core)}}}
    old_raw = upgrade.canonical(old)
    target.write(upgrade.PRIVATE + '/policy.json', old_raw, 0o600)
    target.write(upgrade.STATE + '/public-policy.json', old_raw, 0o644)
    baseline = upgrade.Baseline(upgrade.FIXED.revision, upgrade.sha(old_manifest), upgrade.sha(old_raw),
                               upgrade.sha(old_core), upgrade.sha(old_core))
    status = {'schema': 'careertrellis.paired-maintenance-status/v1', 'policy_sha256': baseline.policy_sha256,
        'boot_id': BOOT, 'release': upgrade.RELEASE, 'phase': 'idle', 'result': 'ok',
        'observed_at': '2026-10-26T20:12:00Z', 'services_restored': True}
    target.write(upgrade.STATE + '/' + role + '/status.json', upgrade.canonical(status), 0o600)
    if role == 'web': target.write(upgrade.STATE + '/writer-interlock.json', upgrade.canonical({
        'schema': 'careertrellis.paired-maintenance-interlock/v1', 'policy_sha256': baseline.policy_sha256,
        'boot_id': BOOT, 'release': upgrade.RELEASE, 'phase': 'idle'}), 0o644)
    payload = packet()
    if role == 'db': target.write('/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive', b'#!/bin/bash\nold\n', 0o755)
    new = copy.deepcopy(old); new['manifest_revision'] = payload['manifest']['revision']
    new['artifact_hashes']['web'][upgrade.CORE] = upgrade.sha(payload['helpers']['web'][upgrade.CORE])
    # A sentinel is deliberately outside every allowed read/mutation path.
    target.write(upgrade.PRIVATE + '/id_ed25519', b'never-read-fixture-secret', 0o600)
    return target, payload, new, baseline


def contents(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in Path(root).rglob('*') if path.is_file()}


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='upgrade493-test-', dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)

    def test_prepare_is_read_only_and_preserves_enabled_schedule(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        before = contents(self.temp.name)
        result = upgrade.prepare(data, policy, target, baseline=baseline, now=NOW)[0]
        self.assertEqual(before, contents(self.temp.name)); self.assertNotIn('fences', target.events)
        self.assertTrue(policy['enabled']); self.assertEqual(policy['valid_until'], '2027-01-24T00:00:00Z')
        self.assertFalse(result['activation_performed'])

    def test_apply_both_roles_preserves_timers_pids_history_and_old_package(self):
        for role in ('web', 'db'):
            root = Path(self.temp.name) / role; root.mkdir()
            target, data, policy, baseline = fixture(root, role)
            timers = target.timers(); observation = copy.deepcopy(target.observation)
            result = upgrade.apply(data, policy, target, approved=True, baseline=baseline, now=NOW)
            self.assertEqual(result['result'], 'upgraded_enabled_pair_retention_unarmed')
            self.assertEqual(target.timers(), timers); self.assertEqual(target.observation, observation)
            self.assertEqual(target.read(upgrade.BACKUP + '/paired-package/common.py'), b'# old fixture\n')
            self.assertEqual(target.read(upgrade.PRIVATE + '/id_ed25519', 0o600), b'never-read-fixture-secret')
            self.assertFalse(target.held); self.assertNotIn('reload', target.events)
            status = upgrade.decode(target.read(upgrade.STATE + '/' + role + '/status.json', 0o600))
            self.assertEqual(status['observed_at'], '2026-10-26T20:12:00Z')
            self.assertTrue(status['services_restored']); self.assertIn('restore-timers', target.events)

    def test_apply_without_explicit_authority_refuses_before_owner_or_files(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        before = contents(self.temp.name)
        with self.assertRaises(upgrade.UpgradeRefused):
            upgrade.apply(data, policy, target, baseline=baseline, now=NOW)
        self.assertEqual(before, contents(self.temp.name)); self.assertEqual(target.events, [])

    def test_schedule_scope_secret_path_and_payload_drift_refuse(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        variants = []
        changed = copy.deepcopy(policy); changed['enabled'] = False; variants.append((data, changed))
        changed = copy.deepcopy(policy); changed['valid_until'] = '2027-02-01T00:00:00Z'; variants.append((data, changed))
        changed = copy.deepcopy(data); changed['helpers']['web']['/etc/careertrellis/rclone.conf'] = b'secret'; variants.append((changed, policy))
        changed = copy.deepcopy(data); changed['paired']['common.py'] += b'# drift\n'; variants.append((changed, policy))
        changed = copy.deepcopy(data); changed['standalone']['../escape.py'] = b'x'; variants.append((changed, policy))
        before = contents(self.temp.name)
        for body, proposed in variants:
            with self.assertRaises(upgrade.UpgradeRefused):
                upgrade.prepare(body, proposed, target, baseline=baseline, now=NOW)
        self.assertEqual(before, contents(self.temp.name))

    def test_imminent_growth_timer_refuses_but_recovery_observer_exception_allowed(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        target.timer_state['careertrellis-paired-maintenance.timer']['next_seconds'] = 900
        with self.assertRaises(upgrade.UpgradeRefused): upgrade.prepare(data, policy, target, baseline=baseline, now=NOW)
        target.timer_state['careertrellis-paired-maintenance.timer']['next_seconds'] = 901
        self.assertTrue(upgrade.prepare(data, policy, target, baseline=baseline, now=NOW)[0]['recovery_observer_short_pause_exception'])

    def test_interruption_retains_claim_and_fences_never_replays_or_restores_unverified_timers(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web'); target.fail = 'exchange'
        with self.assertRaises(Contained): upgrade.apply(data, policy, target, approved=True, baseline=baseline, now=NOW)
        self.assertTrue(target.held); self.assertTrue(target.path(upgrade.CLAIM).exists())
        self.assertNotIn('restore-timers', target.events)
        self.assertNotIn('release', target.events)
        with self.assertRaises(upgrade.UpgradeRefused): upgrade.prepare(data, policy, target, baseline=baseline, now=NOW)

    def test_existing_standalone_or_unknown_package_file_refuses(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        target.write(upgrade.PACKAGE + '/unknown.py', b'# unknown\n', 0o644)
        with self.assertRaises(upgrade.UpgradeRefused): upgrade.prepare(data, policy, target, baseline=baseline, now=NOW)

    def test_changed_public_policy_or_core_refuses_before_intent(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        target.path(upgrade.STATE + '/public-policy.json').write_bytes(b'{}\n')
        with self.assertRaises(upgrade.UpgradeRefused):
            upgrade.apply(data, policy, target, approved=True, baseline=baseline, now=NOW)
        self.assertFalse(target.path(upgrade.CLAIM).exists()); self.assertFalse(target.held)

    def test_pid_change_after_exchange_contains_instead_of_restoring_timers(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        original = target.observe
        def drift(body, revision, now):
            value = original(body, revision, now)
            if revision != baseline.revision: value['units']['protected']['MainPID'] = '124'
            return value
        target.observe = drift
        with self.assertRaises(Contained):
            upgrade.apply(data, policy, target, approved=True, baseline=baseline, now=NOW)
        self.assertTrue(target.held); self.assertNotIn('restore-timers', target.events)
        self.assertFalse(target.path(upgrade.RECEIPT).exists())

    def test_incorrect_timer_restoration_never_gets_success_receipt(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        def wrong(snapshot): target.events.append('restore-timers')
        target.restore_timers = wrong
        with self.assertRaises(Contained):
            upgrade.apply(data, policy, target, approved=True, baseline=baseline, now=NOW)
        self.assertTrue(target.held); self.assertFalse(target.path(upgrade.RECEIPT).exists())

    def test_keys_and_provider_configuration_are_never_read(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        original = target.read
        def guarded(name, mode=0o644, limit=upgrade.MAX_FILE):
            self.assertNotIn('id_ed25519', name)
            self.assertNotIn('rclone.conf', name)
            self.assertFalse(name.endswith('.env'))
            return original(name, mode, limit)
        target.read = guarded
        result = upgrade.apply(data, policy, target, approved=True, baseline=baseline, now=NOW)
        self.assertTrue(result['installed_bytes_verified'])

    def test_history_only_reads_closed_nonsecret_journal_names_and_requires_terminal(self):
        target, data, policy, baseline = fixture(self.temp.name, 'web')
        transaction = 'f' * 32
        root = upgrade.STATE + '/web/runs/' + transaction
        target.path(root).mkdir(parents=True)
        target.write(root + '/complete.json', upgrade.canonical({'transaction': transaction,
            'policy_sha256': baseline.policy_sha256, 'services_restored': True}), 0o600)
        history = target._history()
        self.assertIn(root + '/complete.json', history)
        target.write(root + '/private-keys.json', b'do not read this fixture', 0o600)
        original = target.read
        def guarded(name, mode=0o644, limit=upgrade.MAX_FILE):
            self.assertNotIn('private-keys', name)
            return original(name, mode, limit)
        target.read = guarded
        with self.assertRaises(upgrade.UpgradeRefused): target._history()


class NativeUpgradeFenceTests(unittest.TestCase):
    def test_native_apply_refuses_before_owner_or_fences(self):
        class Unaccepted:
            test_only = False
            def require_owner(self): raise AssertionError('must not inspect an owner')
        with self.assertRaises(upgrade.UpgradeRefused):
            upgrade.apply({}, {}, Unaccepted(), approved=True)

    def test_test_target_cannot_map_the_real_filesystem_root(self):
        root = Path.cwd().anchor
        with self.assertRaises(upgrade.UpgradeRefused):
            upgrade.Target(root, test_only=True, role='db', boot=BOOT)


if __name__ == '__main__': unittest.main()
