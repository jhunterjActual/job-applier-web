"""Offline orchestration contracts; these tests never execute PostgreSQL."""
import copy
import unittest

from deploy.onedrive_retention import local_restore as recovery
from deploy.onedrive_retention import local_restore_plan as plans


def plan():
    return plans.plan('20261026-155056F', 'a' * 64, 'b' * 64, 'c' * 32, 20000000)


class Fake:
    mode = 'offline-test'

    def __init__(self, failed=None, uncertain_event=None):
        self.failed = failed
        self.uncertain_event = uncertain_event
        self.events, self.commands, self.calls = [], [], []
        self.released = False
        self.retained = False

    def protected_preflight(self, value):
        self.calls.append('preflight')
        return self.failed != 'preflight'

    def acquire_locks(self, paths):
        self.calls.append(('locks', paths))
        return tuple(paths)

    def locked_preflight(self, value):
        self.calls.append('locked_preflight')
        return self.failed != 'locked_preflight'

    def create_exclusive_journal(self, value, digest):
        self.calls.append('journal')
        if self.failed == 'existing_journal':
            raise RuntimeError('private prior intent')
        return 'exclusive-test-journal'

    def journal_event(self, journal, event):
        self.events.append(event)
        if (event['phase'], event['state']) == self.uncertain_event:
            raise RuntimeError('private append outcome unknown')
        return plans.digest(event)

    def verify_hydrated_repository(self, value):
        self.calls.append('verify_repository')
        return self.failed != 'repository'

    def dispatch(self, phase, command, timeout):
        self.commands.append((phase, command, timeout))
        if self.failed == phase:
            raise RuntimeError('PRIVATE provider or data information')
        return {'exit_code': 0}

    def validate_local_info(self, value):
        return self.failed != 'local_info'

    def prepare_isolated_cluster(self, value):
        return self.failed != 'prepare'

    def owned_postmaster(self, value):
        result = {'pid': 123, 'start_ticks': 456,
                  'boot_id': '01234567-89ab-cdef-0123-456789abcdef',
                  'process_group': 123, 'data_root': value['data_root'],
                  'socket_root': value['socket_root']}
        if self.failed == 'identity':
            result['data_root'] = '/var/lib/postgresql/18/main'
        return result

    def inspect_postmaster(self, identity, value):
        return 'unknown' if self.failed == 'ownership' else 'owned_alive'

    def verify_no_tcp_listener(self, port):
        return self.failed != 'tcp_listener'

    def validate_promotion_probe(self, identity, value):
        return self.failed != 'promotion'

    def verify_terminal(self, identity, value):
        return self.failed != 'terminal'

    def release_locks(self, token):
        self.released = True
        if self.failed == 'release':
            raise RuntimeError('unknown fence release')

    def retain_fences(self, token, identity):
        self.retained = True


class LocalRestoreTests(unittest.TestCase):
    def test_default_native_boundary_refuses_before_io(self):
        with self.assertRaisesRegex(recovery.ExecutionRefused,
                                    '^native_local_bundle_restore_unsupported$'):
            recovery.execute(plan())
        fake = Fake(); fake.mode = 'native'
        with self.assertRaises(recovery.ExecutionRefused):
            recovery.execute(plan(), fake)
        self.assertEqual(fake.calls, [])

    def test_simulation_never_claims_native_acceptance(self):
        value = plan(); before = copy.deepcopy(value); fake = Fake()
        result = recovery.execute(value, fake)
        self.assertEqual(value, before)
        self.assertEqual(result['result'], 'simulated_complete')
        self.assertFalse(result['native_restore_accepted'])
        self.assertFalse(result['execution_authorized'])
        self.assertFalse(result['cleanup_performed'])
        self.assertTrue(fake.released)
        self.assertFalse(fake.retained)
        self.assertEqual(fake.calls[1], ('locks', list(plans.LOCKS)))
        self.assertEqual([item[0] for item in fake.commands],
                         ['info', 'restore', 'start', 'probe', 'stop', 'checksums'])
        for phase, command, timeout in fake.commands:
            self.assertEqual(command, value[phase])
            self.assertEqual(timeout, value['timeouts_seconds'][phase])
        self.assertEqual([event['sequence'] for event in fake.events],
                         list(range(len(fake.events))))
        self.assertTrue(all(event['native_restore_accepted'] is False for event in fake.events))

    def test_every_command_has_prior_intent_dispatch_and_terminal(self):
        fake = Fake(); recovery.execute(plan(), fake)
        for phase, _, _ in fake.commands:
            self.assertEqual([item['state'] for item in fake.events if item['phase'] == phase],
                             ['intent', 'dispatch', 'terminal'])

    def test_tampered_commands_paths_or_extra_fields_refuse_before_adapter(self):
        for mutate in (lambda value: value['restore'].append('--repo1-type=s3'),
                       lambda value: value.update(data_root='/var/lib/postgresql/18/main'),
                       lambda value: value.update(extra=True)):
            value = plan(); mutate(value); fake = Fake()
            with self.assertRaises(recovery.ExecutionRefused):
                recovery.execute(value, fake)
            self.assertEqual(fake.calls, [])

    def test_prestart_failures_release_locks_without_start_or_cleanup(self):
        for phase in ('preflight', 'locked_preflight', 'existing_journal',
                      'repository', 'info', 'local_info', 'restore', 'prepare'):
            with self.subTest(phase=phase):
                fake = Fake(failed=phase)
                with self.assertRaisesRegex(recovery.ExecutionRefused,
                                            '^local_bundle_restore_failed_retained$'):
                    recovery.execute(plan(), fake)
                self.assertNotIn('start', [item[0] for item in fake.commands])
                self.assertFalse(fake.retained)
                self.assertEqual(fake.released, phase != 'preflight')

    def test_uncertain_or_alive_postmaster_retains_fences_never_retries(self):
        for phase in ('start', 'identity', 'ownership', 'tcp_listener', 'probe',
                      'promotion', 'stop', 'terminal'):
            with self.subTest(phase=phase):
                fake = Fake(failed=phase)
                with self.assertRaises(recovery.ExecutionRefused) as caught:
                    recovery.execute(plan(), fake)
                self.assertNotIn('PRIVATE', str(caught.exception))
                self.assertTrue(fake.retained)
                self.assertFalse(fake.released)
                dispatched = [item[0] for item in fake.commands]
                self.assertEqual(dispatched.count('start'), 1)
                self.assertLessEqual(dispatched.count('stop'), 1)
                self.assertNotIn('checksums', dispatched)
                if phase not in ('stop', 'terminal'):
                    self.assertNotIn('stop', dispatched)

    def test_journal_failure_before_dispatch_does_not_execute_or_retry_append(self):
        fake = Fake(uncertain_event=('restore', 'dispatch'))
        with self.assertRaises(recovery.ExecutionRefused):
            recovery.execute(plan(), fake)
        self.assertEqual([item[0] for item in fake.commands], ['info'])
        self.assertEqual(fake.events[-1]['phase'], 'restore')
        self.assertEqual(fake.events[-1]['state'], 'dispatch')
        self.assertTrue(fake.released)

    def test_journal_failure_after_start_retains_unknown_process_and_fences(self):
        fake = Fake(uncertain_event=('start', 'terminal'))
        with self.assertRaises(recovery.ExecutionRefused):
            recovery.execute(plan(), fake)
        self.assertTrue(fake.retained)
        self.assertFalse(fake.released)
        self.assertEqual([item[0] for item in fake.commands], ['info', 'restore', 'start'])
        self.assertEqual(fake.events[-1]['state'], 'terminal')

    def test_offline_checksum_failure_retains_data_but_releases_terminal_fences(self):
        fake = Fake(failed='checksums')
        with self.assertRaises(recovery.ExecutionRefused):
            recovery.execute(plan(), fake)
        self.assertTrue(fake.released)
        self.assertFalse(fake.retained)
        self.assertEqual(fake.events[-1]['state'], 'failed_retained')

    def test_unknown_fence_release_never_returns_success(self):
        fake = Fake(failed='release')
        with self.assertRaisesRegex(recovery.ExecutionRefused,
                                    '^local_bundle_restore_fence_release_unknown$'):
            recovery.execute(plan(), fake)


if __name__ == '__main__':
    unittest.main()
