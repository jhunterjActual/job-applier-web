"""Synthetic scheduling, durable-intent and startup-interlock controls."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

try:
    from .common import Journal, Policy, Refused, canonical, digest, validate_gate
except ImportError:
    from common import Journal, Policy, Refused, canonical, digest, validate_gate


UTC = dt.timezone.utc
RELEASE = 'a' * 40
OPS = 'b' * 40
BOOT = '11111111-2222-3333-4444-555555555555'
OTHER_BOOT = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
GIB = 1024 ** 3


def policy_data(**changes):
    value = {
        'schema': 'careertrellis.paired-maintenance-policy/v1',
        'enabled': True, 'owner': 'Synthetic maintenance owner',
        'valid_from': '2026-10-01T00:00:00Z',
        'valid_until': '2026-12-30T00:00:00Z',
        'application_release': RELEASE, 'application_build': '20261017.96',
        'ops_release': OPS, 'schema_revision': '20261005_0023',
        'timezone': 'America/New_York', 'weekday': 6,
        'start_hour': 3, 'start_minute': 0, 'start_window_minutes': 30,
        'minimum_capture_age_seconds': 12 * 3600,
        'max_capture_objects': 128, 'max_capture_bytes': 32 * 1024 ** 2,
        'minimum_free_bytes': 5 * GIB,
        'minimum_onedrive_reserve_bytes': 64 * 1024 ** 2,
        'max_database_bytes': 256 * 1024 ** 2,
        'planned_stop_budget_seconds': 1800, 'backup_wait_seconds': 7200,
        'source_readonly_attested': True, 'no_external_writers_attested': True,
        'retention_policy': {'full': 4, 'differential': 14, 'archive': 4, 'expire_auto': True},
        'approved_egress_ipv4_cidr': '192.0.2.1/32',
    }
    value.update(changes)
    return value


def instant(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def marker(policy, **changes):
    value = {'schema': 'careertrellis.paired-maintenance-interlock/v1',
             'policy_sha256': policy.digest, 'boot_id': BOOT,
             'release': RELEASE, 'phase': 'idle'}
    value.update(changes)
    return value


class CanonicalTests(unittest.TestCase):
    def test_key_order_and_termination_are_stable(self):
        self.assertEqual(b'{"a":1,"b":2}\n', canonical({'b': 2, 'a': 1}))
        self.assertEqual(digest({'a': 1, 'b': 2}), digest({'b': 2, 'a': 1}))

    def test_ambiguous_or_non_json_values_refuse(self):
        values = ([1], {1: 'numeric key'}, {'nested': {1: True}},
                  {'x': float('nan')}, {'x': float('inf')}, {'x': b'bytes'})
        for value in values:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(Refused):
                canonical(value)


class PolicyTests(unittest.TestCase):
    def test_normal_weekly_window_and_exclusive_end(self):
        policy = Policy(policy_data())
        self.assertEqual('20261101', policy.require_new_start(instant('2026-11-01T08:00:00Z')))
        self.assertEqual('20261101', policy.require_new_start(instant('2026-11-01T08:29:59Z')))
        for value in ('2026-11-01T07:59:59Z', '2026-11-01T08:30:00Z', '2026-11-02T08:00:00Z'):
            with self.subTest(value=value), self.assertRaises(Refused):
                policy.require_new_start(instant(value))

    def test_dst_changes_utc_time_without_changing_approved_local_hour(self):
        policy = Policy(policy_data())
        self.assertEqual('20261025', policy.require_new_start(instant('2026-10-25T07:00:00Z')))
        self.assertEqual('20261101', policy.require_new_start(instant('2026-11-01T08:00:00Z')))
        with self.assertRaises(Refused):
            policy.require_new_start(instant('2026-11-01T07:00:00Z'))

    def test_spring_transition_three_am_is_unambiguous(self):
        policy = Policy(policy_data(valid_from='2026-03-01T00:00:00Z', valid_until='2026-05-30T00:00:00Z'))
        self.assertEqual('20260308', policy.require_new_start(instant('2026-03-08T07:00:00Z')))

    def test_ambiguous_fall_window_is_not_run_twice(self):
        policy = Policy(policy_data(start_hour=1))
        for value in ('2026-11-01T05:00:00Z', '2026-11-01T06:00:00Z'):
            with self.subTest(value=value), self.assertRaises(Refused):
                policy.require_new_start(instant(value))

    def test_missing_spring_window_and_transition_spanning_window_refuse(self):
        values = ((2, 30, 30, '2026-03-08T07:00:00Z'),
                  (1, 30, 120, '2026-03-08T06:45:00Z'))
        for hour, minute, duration, now in values:
            policy = Policy(policy_data(valid_from='2026-03-01T00:00:00Z',
                valid_until='2026-05-30T00:00:00Z', start_hour=hour,
                start_minute=minute, start_window_minutes=duration))
            with self.subTest(hour=hour), self.assertRaises(Refused):
                policy.require_new_start(instant(now))

    def test_disabled_or_missing_custody_refuses_new_work(self):
        for changes in ({'enabled': False}, {'source_readonly_attested': False},
                        {'no_external_writers_attested': False}):
            policy = Policy(policy_data(**changes))
            with self.subTest(changes=changes), self.assertRaises(Refused):
                policy.require_new_start(instant('2026-11-01T08:00:00Z'))

    def test_expiry_and_future_validity_are_enforced(self):
        policy = Policy(policy_data())
        for value in ('2026-09-27T07:00:00Z', '2027-01-03T08:00:00Z'):
            with self.subTest(value=value), self.assertRaises(Refused):
                policy.require_new_start(instant(value))

    def test_due_uses_exact_capture_age_and_refuses_future(self):
        policy = Policy(policy_data())
        now = instant('2026-11-01T08:00:00Z')
        minimum = dt.timedelta(seconds=policy.data['minimum_capture_age_seconds'])
        self.assertFalse(policy.due(now - minimum + dt.timedelta(seconds=1), now))
        self.assertTrue(policy.due(now - minimum, now))
        self.assertTrue(policy.due('2026-10-25T08:00:00Z', now))
        with self.assertRaises(Refused):
            policy.due(now + dt.timedelta(seconds=1), now)

    def test_naive_and_non_utc_clock_refuse(self):
        policy = Policy(policy_data())
        for value in (dt.datetime(2026, 11, 1, 3),
                      dt.datetime(2026, 11, 1, 3, tzinfo=dt.timezone(dt.timedelta(hours=-5)))):
            with self.subTest(value=value), self.assertRaises(Refused):
                policy.require_new_start(value)

    def test_unknown_fields_malformed_limits_and_egress_refuse(self):
        changes = ({'unexpected': True}, {'weekday': True}, {'start_window_minutes': 0},
                   {'minimum_free_bytes': GIB}, {'source_readonly_attested': 'true'},
                   {'approved_egress_ipv4_cidr': '192.0.2.0/24'},
                   {'approved_egress_ipv4_cidr': '2001:db8::1/128'},
                   {'application_release': 'a' * 39}, {'application_build': 'unknown'},
                   {'schema_revision': 'latest'}, {'valid_until': '2027-10-01T00:00:00Z'},
                   {'valid_from': '2026-10-01T00:00:00'},
                   {'start_hour': 23, 'start_minute': 50, 'start_window_minutes': 30},
                   {'retention_policy': {'full': True, 'differential': 14, 'archive': 4, 'expire_auto': True}})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(Refused):
                Policy(policy_data(**change))

    def test_optional_artifacts_are_fixed_absolute_public_paths_and_hashes(self):
        policy = Policy(policy_data(artifact_hashes={'web': {'/usr/local/sbin/fixed': 'c' * 64}},
            manifest_revision='d' * 40, policy_label='weekly-staging',
            source_custody_reviewed_at='2026-10-01T00:00:00Z'))
        self.assertEqual('20261101', policy.require_new_start(instant('2026-11-01T08:00:00Z')))
        for artifacts in ({'other': {}}, {'web': {'../escape': 'c' * 64}},
                          {'db': {'/a/../b': 'c' * 64}}, {'db': {'/fixed': 'bad'}},
                          {'web': {'/fixed\nname': 'c' * 64}}):
            with self.subTest(artifacts=artifacts), self.assertRaises(Refused):
                Policy(policy_data(artifact_hashes=artifacts))

    def test_future_custody_is_not_current_attestation(self):
        policy = Policy(policy_data(source_custody_reviewed_at='2026-11-02T00:00:00Z'))
        with self.assertRaises(Refused):
            policy.require_new_start(instant('2026-11-01T08:00:00Z'))

    def test_caller_cannot_mutate_a_validated_policy(self):
        original = policy_data()
        policy = Policy(original)
        expected = policy.digest
        original['enabled'] = False
        exposed = policy.data
        exposed['application_release'] = 'f' * 40
        exposed['retention_policy']['full'] = 99
        self.assertEqual(RELEASE, policy.data['application_release'])
        self.assertEqual(4, policy.data['retention_policy']['full'])
        self.assertEqual(expected, policy.digest)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='paired-common-test-',
                                                     dir=Path(__file__).resolve().parent)
        self.root = Path(self.temporary.name)
        self.assertEqual(Path(__file__).resolve().parent, self.root.resolve().parent)
        self.journal = Journal(self.root, test_only=True)

    def tearDown(self):
        self.assertEqual(Path(__file__).resolve().parent, self.root.resolve().parent)
        self.assertTrue(self.root.name.startswith('paired-common-test-'))
        self.temporary.cleanup()

    def test_immutable_intent_is_idempotent_only_for_identical_canonical_bytes(self):
        self.assertFalse(self.journal.exists('backup-intent.json'))
        first = self.journal.write_once('backup-intent.json', {'b': 2, 'a': 1})
        metadata = (self.root / 'backup-intent.json').stat()
        self.assertEqual(first, self.journal.write_once('backup-intent.json', {'a': 1, 'b': 2}))
        self.assertEqual(metadata.st_ino, (self.root / 'backup-intent.json').stat().st_ino)
        with self.assertRaises(Refused):
            self.journal.write_once('backup-intent.json', {'a': 3})
        self.assertEqual({'a': 1, 'b': 2}, self.journal.read('backup-intent.json'))

    def test_replace_is_only_for_status_and_does_not_modify_intents(self):
        self.journal.write_once('backup-intent.json', {'attempt': 1})
        self.journal.replace('status.json', {'phase': 'started'})
        self.journal.replace('status.json', {'phase': 'terminal'})
        self.assertEqual({'phase': 'terminal'}, self.journal.read('status.json'))
        with self.assertRaises(Refused):
            self.journal.replace('backup-intent.json', {'attempt': 2})
        self.assertEqual({'attempt': 1}, self.journal.read('backup-intent.json'))
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_invalid_paths_are_refused_without_creating_files(self):
        for name in ('../escape.json', '/absolute.json', '.hidden.json',
                     'bad.JSON', 'bad_name.json', 'bad..json', 'nested/x.json'):
            with self.subTest(name=name), self.assertRaises(Refused):
                self.journal.write_once(name, {'value': 1})
        self.assertEqual([], list(self.root.iterdir()))

    def test_corrupt_duplicate_key_and_nonfinite_records_refuse(self):
        for raw in (b'{', b'{"a":1,"a":2}', b'{"x":NaN}', b'[]', b'\xff'):
            (self.root / 'status.json').write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(Refused):
                self.journal.read('status.json')

    def test_partial_intent_is_preserved_and_never_replayed(self):
        actual_write = os.write
        calls = []
        def interrupted(descriptor, raw):
            calls.append(True)
            if len(calls) == 1:
                return actual_write(descriptor, raw[:3])
            raise OSError('synthetic interruption')
        with patch(Journal.__module__ + '.os.write', side_effect=interrupted), self.assertRaises(Refused):
            self.journal.write_once('backup-intent.json', {'attempt': 1})
        self.assertEqual(b'{"a', (self.root / 'backup-intent.json').read_bytes())
        with self.assertRaises(Refused):
            self.journal.write_once('backup-intent.json', {'attempt': 1})
        self.assertEqual(b'{"a', (self.root / 'backup-intent.json').read_bytes())

    def test_replace_failure_keeps_original_and_removes_only_own_temporary(self):
        self.journal.replace('status.json', {'phase': 'original'})
        with patch(Journal.__module__ + '.os.replace', side_effect=OSError('synthetic failure')), self.assertRaises(Refused):
            self.journal.replace('status.json', {'phase': 'new'})
        self.assertEqual({'phase': 'original'}, self.journal.read('status.json'))
        self.assertEqual(['status.json'], [value.name for value in self.root.iterdir()])

    def test_oversized_payload_and_existing_record_are_refused(self):
        with self.assertRaises(Refused):
            self.journal.write_once('status.json', {'value': 'x' * Journal.MAX_RECORD_BYTES})
        self.assertFalse(self.journal.exists('status.json'))
        (self.root / 'status.json').write_bytes(b'x' * (Journal.MAX_RECORD_BYTES + 1))
        with self.assertRaises(Refused):
            self.journal.read('status.json')

    def test_symlink_and_hardlink_metadata_are_rejected_before_read(self):
        self.journal.write_once('status.json', {'phase': 'safe'})
        actual_lstat = Path.lstat
        for mode, links in ((stat.S_IFLNK | 0o600, 1), (stat.S_IFREG | 0o600, 2)):
            def metadata(path, *, follow_symlinks=False):
                original = actual_lstat(path)
                if path == self.root / 'status.json':
                    values = list(original)
                    values[0] = mode
                    values[3] = links
                    return os.stat_result(values)
                return original
            with self.subTest(mode=mode, links=links), patch.object(Path, 'lstat', metadata), self.assertRaises(Refused):
                self.journal.read('status.json')

    def test_missing_root_and_missing_record_are_not_created(self):
        with self.assertRaises(Refused):
            Journal(self.root / 'missing', test_only=True)
        with self.assertRaises(Refused):
            self.journal.read('missing.json')
        self.assertEqual([], list(self.root.iterdir()))

    def test_replaced_journal_root_is_refused_before_a_new_intent(self):
        actual_lstat = Path.lstat
        def changed(path, *, follow_symlinks=False):
            original = actual_lstat(path)
            if path == self.root:
                values = list(original)
                values[1] = original.st_ino + 1
                return os.stat_result(values)
            return original
        with patch.object(Path, 'lstat', changed), self.assertRaises(Refused):
            self.journal.write_once('backup-intent.json', {'attempt': 1})
        self.assertEqual([], list(self.root.iterdir()))

    def test_binary_record_bytes_are_not_platform_newline_translated(self):
        self.journal.write_once('backup-intent.json', {'attempt': 1})
        self.assertEqual(canonical({'attempt': 1}), (self.root / 'backup-intent.json').read_bytes())


class StartupGateTests(unittest.TestCase):
    def test_current_idle_and_bound_restore_permit_allow_start(self):
        policy = Policy(policy_data())
        self.assertTrue(validate_gate(policy, marker(policy), BOOT, RELEASE))
        self.assertTrue(validate_gate(policy, marker(policy, phase='restore_allowed',
                                                    transaction='c' * 32), BOOT, RELEASE))

    def test_blocked_or_missing_state_fails_closed_even_when_disabled(self):
        for enabled in (True, False):
            policy = Policy(policy_data(enabled=enabled))
            self.assertFalse(validate_gate(policy, None, BOOT, RELEASE))
            self.assertFalse(validate_gate(policy, marker(policy, phase='blocked'), BOOT, RELEASE))
            self.assertTrue(validate_gate(policy, marker(policy), BOOT, RELEASE))

    def test_stale_boot_or_release_or_policy_cannot_authorize_start(self):
        policy = Policy(policy_data())
        for changes in ({'boot_id': OTHER_BOOT}, {'release': 'c' * 40},
                        {'policy_sha256': 'd' * 64}, {'schema': 'unknown'}):
            with self.subTest(changes=changes):
                self.assertFalse(validate_gate(policy, marker(policy, **changes), BOOT, RELEASE))
        self.assertFalse(validate_gate(policy, marker(policy), 'not-a-boot', RELEASE))
        self.assertFalse(validate_gate(policy, marker(policy), BOOT, 'c' * 40))

    def test_restore_marker_needs_transaction_and_unknown_phases_refuse(self):
        policy = Policy(policy_data())
        for changes in ({'phase': 'restore_allowed'}, {'phase': 'restore_allowed', 'transaction': '../x'},
                        {'phase': 'running'}, {'unexpected': True}, {'updated_at': 'not-a-time'}):
            with self.subTest(changes=changes):
                self.assertFalse(validate_gate(policy, marker(policy, **changes), BOOT, RELEASE))

    def test_expired_new_work_policy_does_not_block_proven_safe_restoration(self):
        policy = Policy(policy_data())
        with self.assertRaises(Refused):
            policy.require_new_start(instant('2027-01-03T08:00:00Z'))
        self.assertTrue(validate_gate(policy, marker(policy, phase='restore_allowed',
                                                    transaction='c' * 32), BOOT, RELEASE))


if __name__ == '__main__':
    unittest.main()
