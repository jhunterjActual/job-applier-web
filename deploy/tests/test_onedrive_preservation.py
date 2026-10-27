"""Synthetic only: no fixture is native proof or installed guard clearance."""
import copy
import datetime as dt
import hashlib
import json
import unittest

from deploy.onedrive_retention import preservation as p


def raw(value):
    return json.dumps(value, sort_keys=True).encode('ascii')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def fixture():
    binding = {'transaction': 'a' * 32, 'attempt': 'b' * 32}
    owner = {'pid': 123, 'start_ticks': 456, 'boot_id': '01234567-89ab-cdef-0123-456789abcdef'}
    root = '/var/lib/careertrellis-onedrive-bundle-acceptance/' + binding['attempt']
    unit = 'historical-manually-bound-publish.service'
    ownership = dict(binding, owner=owner, units=[unit], data_root=root)
    intent = dict(binding, ownership_sha256=sha(raw(ownership)), operation='info-only')
    dispatch = dict(binding, intent_sha256=sha(raw(intent)), owner=owner, phases=['info'], exit_code=1)
    terminal = dict(binding, ownership_sha256=sha(raw(ownership)), dispatch_sha256=sha(raw(dispatch)),
        observed_at='2026-10-27T12:00:00Z', observer=dict(owner, pid=321, start_ticks=999),
        owner_absence={'identity': owner, 'proc_state': 'absent'},
        units={unit: {'active_state': 'inactive', 'main_pid': 0, 'control_pid': 0,
                      'job': '', 'cgroup_members': [], 'cgroup_state': 'absent'}},
        data={'root': root, 'entries': [], 'mounts': [], 'socket_entries': []})
    resources = [{'path': prefix + '/ciphertext.tar', 'sha256': 'c' * 64, 'bytes': 40, 'role': role}
                 for prefix, role in [('/var/lib/careertrellis/bundles', 'downloaded-encrypted-archive'),
                    ('onedrive:job-applier-staging/postgresql-bundles/generation', 'encrypted-archive')]]
    archive = dict(binding, resources=resources)
    return [ownership, intent, dispatch, terminal, archive]


def arguments(records):
    encoded = list(map(raw, records))
    proof = dict(transaction=records[0]['transaction'], attempt=records[0]['attempt'],
        schema='careertrellis.failed-capture-preservation/v1', held_resources=copy.deepcopy(records[4]['resources']),
        hashes=dict(zip(('ownership', 'intent', 'info_dispatch', 'terminal', 'archive_receipt'), map(sha, encoded))))
    return [raw(proof)] + encoded + [dt.datetime(2026, 10, 27, 12, 0, 30, tzinfo=dt.timezone.utc)]


class PreservationTests(unittest.TestCase):
    def test_consistency_is_never_authority(self):
        result = p.validate_preservation(*arguments(fixture()))
        self.assertEqual(result['held_budget_bytes'], 80)
        for field in ('authenticated', 'backup_accepted', 'guard_clearance_permitted',
                      'retry_permitted', 'cleanup_permitted', 'native_collection_available'):
            self.assertIs(result[field], False)

    def refused(self, change):
        records = fixture()
        change(records)
        with self.assertRaisesRegex(p.PreservationRefused, '^failed_capture_preservation_refused$'):
            p.validate_preservation(*arguments(records))

    def test_any_restore_or_start_refused(self):
        for phase in ('restore', 'start'):
            self.refused(lambda r: r[2]['phases'].append(phase))

    def test_unknown_or_nonterminal_owner_refused(self):
        self.refused(lambda r: r[3]['owner_absence'].update(proc_state='unknown'))
        self.refused(lambda r: r[3]['observer'].update(pid=123))
        self.refused(lambda r: r[3]['observer'].update(boot_id='other'))

    def test_nonempty_data_or_unbound_units_refused(self):
        self.refused(lambda r: r[3]['data']['entries'].append('restored-data'))
        self.refused(lambda r: r[3]['units'].clear())
        self.refused(lambda r: next(iter(r[3]['units'].values()))['cgroup_members'].append(456))

    def test_fresh_terminal_required(self):
        self.refused(lambda r: r[3].update(observed_at='2026-10-27T11:00:00Z'))
        self.refused(lambda r: r[3].update(observed_at='2026-10-27T12:01:00Z'))

    def test_changed_raw_original_refused(self):
        args = arguments(fixture())
        args[1] += b' '
        with self.assertRaises(p.PreservationRefused):
            p.validate_preservation(*args)

    def test_all_archives_held_and_counted(self):
        args = arguments(fixture())
        proof = json.loads(args[0])
        proof['held_resources'].pop()
        args[0] = raw(proof)
        with self.assertRaises(p.PreservationRefused):
            p.validate_preservation(*args)
        self.refused(lambda r: r[4]['resources'][0].update(bytes=True))

    def test_duplicate_keys_and_successful_info_refused(self):
        args = arguments(fixture())
        args[0] = b'{"transaction":1,"transaction":2}'
        with self.assertRaises(p.PreservationRefused):
            p.validate_preservation(*args)
        self.refused(lambda r: r[2].update(exit_code=0))


if __name__ == '__main__':
    unittest.main()
