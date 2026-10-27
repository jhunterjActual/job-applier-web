"""Synthetic WEB host-adapter checks; no native command or protected path I/O."""
import copy
import datetime as dt
import threading
import types
import unittest
import urllib.error
from unittest.mock import Mock, patch

from . import host, runtime
from .common import Refused, canonical, digest
from .web_control import WebControl

TX, RELEASE, HASH = 'b' * 32, 'a' * 40, 'c' * 64
BOOT = '11111111-2222-3333-4444-555555555555'


class Store:
    def __init__(self): self.values = {}
    def exists(self, name): return name in self.values
    def read(self, name): return copy.deepcopy(self.values[name])
    def write_once(self, name, value):
        if name in self.values and self.values[name] != value:
            raise Refused('journal_collision')
        self.values[name] = copy.deepcopy(value)
    def replace(self, name, value): self.values[name] = copy.deepcopy(value)


def backend():
    value = object.__new__(host.WebBackend)
    value.policy = types.SimpleNamespace(digest=HASH, data={
        'application_release': RELEASE, 'max_capture_objects': 128,
        'max_capture_bytes': 32 * 1024**2, 'minimum_onedrive_reserve_bytes': 64,
        'approved_egress_ipv4_cidr': '192.0.2.1/32', 'planned_stop_budget_seconds': 1800})
    value.transaction, value.store = TX, Store()
    value.r = Mock(boot=BOOT, invocation='d' * 32, started=0, fence=None)
    value.client = Mock()
    value.last_peer = None
    value.restored = True
    value.heartbeat_failure = None
    value.status_lock = threading.RLock()
    value.stop_heartbeat = threading.Event()
    value.current_status = ('preflight', 'running', True)
    return value


def capture():
    return {'result': 'pass', 'manifest_verified': True, 'release': RELEASE,
            'backup_label': '20261026-001723F', 'snapshot_id': RELEASE + '-20261026-001723F',
            'authority': 'postgres-r2-authoritative', 'object_count': 2, 'ciphertext_bytes': 100,
            'manifest_sha256': 'e' * 64, 'capture_started_epoch': 101, 'captured_epoch': 102}


def released_backend():
    policy = backend().policy
    policy.data['backup_wait_seconds'] = 7200
    store = Store()
    store.write_once('stop-intent.json', {'writers': {unit: 10 for unit in runtime.SERVICES}})
    store.write_once('start-intent.json', {'transaction': TX, 'policy_sha256': HASH,
        'identity': {'release': RELEASE, 'web_boot': BOOT, 'web_invocation': 'a' * 32}})
    store.write_once('capture-result.json', capture())
    store.write_once('writers-restored.json', {'services_restored': True, 'capture_sha256': digest(capture())})
    store.write_once('db-released.json', {'services_restored': True})
    store.write_once('peer-identity.json', {'transaction': TX, 'policy_sha256': HASH,
        'release': RELEASE, 'boot_id': BOOT, 'worker_invocation': 'f' * 32})
    native = Mock(boot=BOOT, invocation='d' * 32, fence=None)
    native.live_group.return_value = False
    with patch.object(runtime, 'HostRuntime', return_value=native), patch.object(host, 'Client'):
        value = host.WebBackend(policy, TX, store)
    return value


class HostTests(unittest.TestCase):
    def test_lost_restored_ack_reconciles_original_released_or_aborted_peer(self):
        for state in ('released', 'aborted'):
            value = released_backend()
            del value.store.values['db-released.json']
            identity = value.store.read('start-intent.json')['identity']
            value.store.write_once('db-begin-intent.json', identity)
            if state == 'aborted':
                del value.store.values['capture-result.json']
                del value.store.values['writers-restored.json']
                value.store.write_once('restore-dispatch-intent.json', {'restore_original_writers': True})
            terminal = dict(value.store.read('peer-identity.json'), state=state)
            value.r.wait_peer.return_value = terminal
            fence = Mock(fd=10)
            def acquire(): value.r.fence = fence
            root = Store()
            with self.subTest(state=state), patch.object(host, 'Journal', return_value=root), patch.object(host, 'public_marker') as marker, patch.object(value, 'acquire_fence', side_effect=acquire), patch.object(value, 'restore_writers') as restore, patch.object(value, 'capture') as recapture, patch.object(value, 'publish') as republish, patch.object(host.provider, 'web_facts') as providers:
                result = WebControl(value.policy, value.store, value).run(TX, recovery_only=True)
                self.assertTrue(result['services_restored'])
                self.assertEqual(result['state'], 'operator_required' if state == 'released' else 'aborted_restored')
                value.r.wait_peer.assert_called_once()
                self.assertEqual(value.r.wait_peer.call_args.args[1], {'abort_ready', 'released', 'aborted'})
                value.client.call.assert_called_once_with('abort', identity)
                self.assertIsNone(value.r.fence)
                fence.close.assert_called_once()
                marker.assert_called_once_with(value.policy, BOOT, 'idle')
                restore.assert_not_called()
                recapture.assert_not_called()
                republish.assert_not_called()
                providers.assert_not_called()
                value.r.run.assert_not_called()
                self.assertEqual(value.store.exists('db-released.json'), state == 'released')
                self.assertEqual(value.store.exists('aborted-restored.json'), state == 'aborted')
                self.assertEqual(root.read('status.json')['result'], 'failed')

    def test_terminal_peer_reconciliation_rejects_changed_identity_or_unhealthy_workers(self):
        for fault in ('boot', 'operator', 'health', 'restore_intent'):
            value = released_backend()
            del value.store.values['db-released.json']
            terminal = dict(value.store.read('peer-identity.json'), state='released')
            if fault == 'boot': terminal['boot_id'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
            if fault == 'operator': terminal['operator_required'] = True
            if fault == 'health': value.r.health.side_effect = RuntimeError('unhealthy')
            if fault == 'restore_intent': terminal['state'] = 'aborted'
            with self.subTest(fault=fault), patch.object(host, 'public_marker') as marker:
                with self.assertRaises((Refused, KeyError, RuntimeError)):
                    value.reconcile_terminal_peer(terminal)
                self.assertFalse(value.restored)
                self.assertFalse(value.store.exists('db-released.json'))
                marker.assert_not_called()
                value.client.call.assert_not_called()
                value.r.run.assert_not_called()

    def test_unknown_stopped_attempt_retains_fence_during_recovery_only_wait(self):
        value = backend()
        value.restored = False
        fence = Mock(fd=10)
        value.r.fence = fence
        controller = Mock()
        calls = []
        def run(transaction, recovery_only):
            calls.append((transaction, recovery_only))
            if len(calls) == 1:
                return {'state': 'operator_required', 'services_restored': False}
            self.assertTrue(recovery_only)
            self.assertIs(value.r.fence, fence)
            value.restored = True
            value.r.fence = None  # Safe controller release after DB terminal proof.
            return {'state': 'aborted_restored', 'services_restored': True}
        controller.run.side_effect = run
        def wait(seconds):
            self.assertEqual(seconds, 60)
            self.assertIs(value.r.fence, fence)
            self.assertEqual(fence.fd, 10)
            self.assertFalse(value.stop_heartbeat.is_set())
            fence.close.assert_not_called()
        with patch.object(host, 'public_marker') as marker, patch.object(value, 'status') as status, patch.object(host.time, 'sleep', side_effect=wait):
            result = host._run_controller(controller, value, TX, recovery_only=False)
            self.assertEqual(result['state'], 'aborted_restored')
            self.assertEqual(calls, [(TX, False), (TX, True)])
            marker.assert_called_once_with(value.policy, BOOT, 'blocked', TX)
            status.assert_called_once_with('operator_required', 'failed', False)
            self.assertEqual(value.r.check.call_count, 2)
            value.client.call.assert_not_called()

    def test_online_or_other_terminal_results_do_not_enter_fault_loop(self):
        for state, restored in [('operator_required', True), ('complete', True),
                                ('aborted_restored', True), ('skipped', False)]:
            value = backend()
            value.restored = restored
            value.r.fence = Mock(fd=10)
            controller = Mock()
            expected = {'state': state, 'services_restored': restored}
            controller.run.return_value = expected
            with patch.object(host.time, 'sleep') as wait, patch.object(host, 'public_marker') as marker:
                self.assertEqual(host._run_controller(controller, value, TX, True), expected)
                controller.run.assert_called_once_with(TX, recovery_only=True)
                wait.assert_not_called()
                marker.assert_not_called()

    def test_failed_fence_acquisition_does_not_spin_on_another_owner(self):
        value = backend()
        value.restored = False
        value.r.fence = None
        controller = Mock()
        controller.run.return_value = {'state': 'operator_required', 'services_restored': False}
        with patch.object(host.time, 'sleep') as wait, patch.object(host, 'public_marker') as marker:
            self.assertEqual(host._run_controller(controller, value, TX, True)['state'], 'operator_required')
            controller.run.assert_called_once_with(TX, recovery_only=True)
            wait.assert_not_called()
            marker.assert_called_once_with(value.policy, BOOT, 'blocked', TX)
    def test_online_recovery_proves_released_writers_without_replaying_any_action(self):
        value = released_backend()
        self.assertFalse(value.restored)
        root = Store()
        with patch.object(host, 'Journal', return_value=root), patch.object(host, 'public_marker') as marker, patch.object(value, 'restore_writers') as restore, patch.object(value, 'capture') as recapture, patch.object(value, 'publish') as republish, patch.object(host.provider, 'web_facts') as providers:
            result = WebControl(value.policy, value.store, value).run(TX, recovery_only=True)
            self.assertEqual(result, {'state': 'operator_required', 'services_restored': True})
            self.assertTrue(value.restored)
            marker.assert_called_once_with(value.policy, BOOT, 'idle')
            value.r.health.assert_called_once()
            value.r.run.assert_not_called()
            value.client.call.assert_not_called()
            restore.assert_not_called()
            recapture.assert_not_called()
            republish.assert_not_called()
            providers.assert_not_called()
            self.assertTrue(root.read('status.json')['services_restored'])

    def test_invalid_online_recovery_proofs_never_unlock_writers(self):
        def change_start(store): store.values['start-intent.json']['identity']['web_boot'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        def change_digest(store): store.values['writers-restored.json']['capture_sha256'] = 'a' * 64
        def change_peer(store): store.values['peer-identity.json']['policy_sha256'] = 'a' * 64
        def change_capture(store): store.values['capture-result.json']['object_count'] = 129
        def change_release(store): store.values['db-released.json']['services_restored'] = False
        def remove_release(store): del store.values['db-released.json']
        for corrupt in (change_start, change_digest, change_peer, change_capture, change_release, remove_release):
            value = released_backend()
            corrupt(value.store)
            with self.subTest(corrupt=corrupt.__name__), patch.object(host, 'public_marker') as marker:
                with self.assertRaises(Refused): value.clear_writer_block()
                self.assertFalse(value.restored)
                marker.assert_not_called()
                value.r.health.assert_not_called()
                value.client.call.assert_not_called()

    def test_current_worker_health_failure_cannot_unlock_released_recovery(self):
        value = released_backend()
        value.r.health.side_effect = RuntimeError('synthetic unhealthy worker')
        with patch.object(host, 'public_marker') as marker:
            with self.assertRaises(RuntimeError): value.clear_writer_block()
            self.assertFalse(value.restored)
            marker.assert_not_called()
            value.client.call.assert_not_called()
            value.r.run.assert_not_called()
    def test_peer_boot_and_original_invocation_are_immutable(self):
        value = backend()
        peer = {'state': 'fenced', 'transaction': TX, 'policy_sha256': HASH,
                'release': RELEASE, 'boot_id': BOOT, 'worker_invocation': 'f' * 32}
        value._bind_peer(peer)
        value._bind_peer(dict(peer, state='sealed'))
        for key, changed in [('boot_id', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'),
                             ('worker_invocation', 'a' * 32), ('transaction', 'a' * 32),
                             ('policy_sha256', 'a' * 64), ('release', 'b' * 40)]:
            with self.assertRaises(Refused):
                value._bind_peer(dict(peer, **{key: changed}))

    def test_preflight_provider_reads_hold_both_canonical_locks(self):
        value = backend()
        root = Store()
        held = []
        def fence(path, mode):
            result = Mock()
            result.acquire.side_effect = lambda: (held.append(path), result)[1]
            result.close.side_effect = lambda: held.remove(path)
            return result
        def facts():
            self.assertEqual(set(held), {runtime.MAINTENANCE, runtime.RECOVERY})
            return {'source_count': 2, 'source_bytes': 100, 'onedrive_free_bytes': 200,
                    'egress_ipv4_cidr': '192.0.2.1/32'}
        value.client.call.return_value = {'ready': True}
        value.r.run.return_value = canonical({'operation': 'generated_object_mirror', 'result': 'ok', 'started_at': '2026-10-25T00:00:00Z'})
        with patch.object(host, 'Journal', return_value=root), patch.object(runtime, 'Fence', side_effect=fence), patch.object(host.provider, 'web_facts', side_effect=facts):
            self.assertTrue(value.preflight()['ready'])
        self.assertEqual(held, [])

    def test_fresh_skip_is_durable_idle_success_without_transaction(self):
        value = backend()
        root = Store()
        with patch.object(host, 'Journal', return_value=root):
            value.status('fresh', 'skipped')
        self.assertEqual(root.read('status.json')['phase'], 'idle')
        self.assertEqual(root.read('status.json')['result'], 'ok')
        self.assertNotIn('transaction', root.read('status.json'))
        self.assertTrue(value.store.read('complete.json')['services_restored'])
        value.client.call.assert_not_called()

    def test_capture_dispatch_is_single_attempt_and_exactly_bound(self):
        value = backend()
        value.r.started = -50000  # Provider preflight time is outside stop budget.
        value.restored = False
        value.store.write_once('quiesced-result.json', {'quiesced_at': 100,
            'stop_started_monotonic': 5, 'boot_id': BOOT})
        value.r.run.return_value = canonical(capture())
        pair = {'backup_label': capture()['backup_label'], 'started_at': '1970-01-01T00:01:40Z',
                'completed_at': '1970-01-01T00:01:41Z', 'receipt_sha256': 'f' * 64}
        with patch.object(value, 'status'), patch.object(host.time, 'time', return_value=103), patch.object(host.time, 'monotonic', return_value=10), patch.object(runtime, 'RELEASE', RELEASE):
            self.assertEqual(value.capture(pair), capture())
            self.assertTrue(value.store.exists('capture-dispatch-intent.json'))
            args = value.r.run.call_args.args[0]
            self.assertEqual(args[:2], [runtime.HELPER, 'capture'])
            self.assertTrue(value.r.run.call_args.kwargs['inherit'])
            self.assertEqual(value.r.run.call_args.kwargs['timeout'], 1795)
            with self.assertRaises(Refused):
                value.capture(pair)
        self.assertEqual(value.r.run.call_count, 1)

    def test_stop_clock_covers_stop_command_and_is_bound_to_boot(self):
        value = backend()
        value.store.write_once('stop-intent.json', {'writers': {unit: 10 for unit in runtime.SERVICES}})
        with patch.object(value, 'status'), patch.object(host.time, 'monotonic', return_value=500), patch.object(host.time, 'time', return_value=1000):
            self.assertEqual(value.stop_writers(), 1000)
        self.assertEqual(value.store.read('quiesced-result.json'), {'quiesced_at': 1000,
            'stop_started_monotonic': 500, 'boot_id': BOOT})
        value.r.run.assert_called_once_with(['/usr/bin/systemctl', 'stop', *runtime.SERVICES], timeout=120)

    def test_exhausted_stop_budget_or_changed_boot_never_dispatches_capture(self):
        for started, boot in [(0, BOOT), (1900, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')]:
            value = backend()
            value.store.write_once('quiesced-result.json', {'quiesced_at': 100,
                'stop_started_monotonic': started, 'boot_id': boot})
            with patch.object(host.time, 'monotonic', return_value=1901), self.assertRaises(Refused):
                value.capture({})
            value.r.run.assert_not_called()
            self.assertFalse(value.store.exists('capture-dispatch-intent.json'))

    def test_capture_unknown_does_not_retry_helper(self):
        value = backend()
        value.store.write_once('quiesced-result.json', {'quiesced_at': 100,
            'stop_started_monotonic': 5, 'boot_id': BOOT})
        value.r.run.side_effect = RuntimeError('PRIVATE')
        pair = {'backup_label': capture()['backup_label'], 'started_at': '1970-01-01T00:01:40Z',
                'completed_at': '1970-01-01T00:01:41Z', 'receipt_sha256': 'f' * 64}
        with patch.object(value, 'status'), patch.object(host.time, 'time', return_value=103), patch.object(host.time, 'monotonic', return_value=10):
            with self.assertRaises(RuntimeError): value.capture(pair)
            with self.assertRaises(Refused): value.capture(pair)
        self.assertEqual(value.r.run.call_count, 1)

    def test_publication_requires_release_and_sealed_exact_manifest(self):
        value = backend()
        value.store.write_once('db-released.json', {'services_restored': True})
        published = {key: capture()[key] for key in ('authority', 'backup_label', 'manifest_sha256', 'release', 'snapshot_id')}
        published['result'] = 'onedrive_snapshot_created'
        value.r.run.return_value = canonical(published)
        with patch.object(value, 'status'):
            self.assertEqual(value.publish(capture()), published)
            self.assertEqual(value.r.run.call_args.args[0][1], 'publish-captured')
            with self.assertRaises(Refused): value.publish(capture())
        self.assertEqual(value.r.run.call_count, 1)
        changed = backend()
        changed.store.write_once('db-released.json', {'services_restored': True})
        changed.r.run.return_value = canonical(dict(published, manifest_sha256='a' * 64))
        with patch.object(changed, 'status'), self.assertRaises(Refused): changed.publish(capture())

    def test_restore_before_stop_has_no_writer_dispatch(self):
        value = backend()
        value.last_peer = {'state': 'abort_ready'}
        value.r.live_group.return_value = False
        value.restore_writers(recovery=True)
        value.r.health.assert_called_once()
        value.r.run.assert_not_called()

    def test_restore_retries_transient_connection_errors_within_deadline(self):
        for transient in (urllib.error.URLError('synthetic refusal'), TimeoutError()):
            value = backend()
            value.restored = False
            value.last_peer = {'state': 'sealed'}
            value.store.write_once('stop-intent.json', {'writers': {unit: index + 10 for index, unit in enumerate(runtime.SERVICES)}})
            value.r.live_group.return_value = False
            value.r.health.side_effect = [transient, None, None]
            value.r.state.return_value = {'ActiveState': 'inactive'}
            with patch.object(runtime, 'Fence') as fence, patch.object(host.time, 'monotonic', return_value=10), patch.object(host.time, 'sleep'):
                fence.return_value.acquire.return_value = fence.return_value
                value.restore_writers()
            self.assertTrue(value.restored)
            self.assertEqual(value.r.health.call_count, 3)
            value.r.run.assert_called_once_with(['/usr/bin/systemctl', 'start', *runtime.SERVICES], timeout=120)

    def test_restore_transient_errors_do_not_extend_deadline(self):
        value = backend()
        value.restored = False
        value.last_peer = {'state': 'sealed'}
        value.store.write_once('stop-intent.json', {'writers': {unit: index + 10 for index, unit in enumerate(runtime.SERVICES)}})
        value.r.live_group.return_value = False
        value.r.health.side_effect = urllib.error.URLError('synthetic refusal')
        value.r.state.return_value = {'ActiveState': 'inactive'}
        with patch.object(runtime, 'Fence') as fence, patch.object(host.time, 'monotonic', side_effect=[10, 11, 131]), patch.object(host.time, 'sleep'):
            fence.return_value.acquire.return_value = fence.return_value
            with self.assertRaises(Refused):
                value.restore_writers()
        self.assertFalse(value.restored)
        self.assertEqual(value.r.run.call_count, 1)
        self.assertFalse(hasattr(value.r, 'startup_prior_pids'))

    def test_recovery_checks_each_already_active_process_with_same_uid_adapter(self):
        value = backend()
        value.restored = False
        value.last_peer = {'state': 'abort_ready'}
        value.store.write_once('stop-intent.json', {'writers': {unit: index + 10 for index, unit in enumerate(runtime.SERVICES)}})
        value.r.live_group.return_value = False
        value.r.state.return_value = {'ActiveState': 'active'}
        with patch.object(runtime, 'Fence') as fence, patch.object(host.time, 'monotonic', return_value=10), patch.object(host.time, 'sleep'):
            fence.return_value.acquire.return_value = fence.return_value
            value.restore_writers(recovery=True)
        self.assertEqual([call.args for call in value.r.process_release.call_args_list],
                         [(unit, {'ActiveState': 'active'}) for unit in runtime.SERVICES])

    def test_restore_permission_requires_sealed_or_aborted_peer(self):
        value = backend()
        for phase in ['fenced', 'pair_ready', 'unknown']:
            value.last_peer = {'state': phase}
            with patch.object(host, 'public_marker') as marker, self.assertRaises(Refused):
                value.allow_writer_restore()
            marker.assert_not_called()
        value.last_peer = {'state': 'sealed'}
        with patch.object(host, 'public_marker') as marker, patch.object(value, 'status'):
            value.allow_writer_restore()
            marker.assert_called_once_with(value.policy, BOOT, 'restore_allowed', TX)

    def test_report_acceptance_uses_exact_monitor_and_bounded_provider_ack(self):
        value = backend()
        invocation = 'a' * 32
        value.r.run.return_value = (host.REPORTER + '\n').encode()
        value.r.terminal.return_value = {'Result': 'success', 'ExecMainStatus': '0', 'InvocationID': 'f' * 32}
        events = [(100, invocation, {'schema': 'careertrellis.backup-health-status/v1',
            'surface': 'objects', 'result': 'ok', 'failure_codes': []})]
        report = [(110, 'f' * 32, {'observation': 'healthy', 'provider_acceptance': 'accepted'})]
        with patch.object(value, '_events', side_effect=[events, report]): value._report_accepted(99, invocation)
        with patch.object(value, '_events', side_effect=[events, [(230, 'f' * 32, report[0][2])]]), self.assertRaises(Refused):
            value._report_accepted(99, invocation)
        with patch.object(value, '_events', return_value=[(100, 'b' * 32, events[0][2])]), self.assertRaises(Refused):
            value._report_accepted(99, invocation)

    def test_running_verification_monitor_is_not_premature_complete(self):
        value = backend()
        root = Store()
        with patch.object(host, 'Journal', return_value=root): value.status('verifying', services_restored=True)
        status = root.read('status.json')
        self.assertEqual((status['phase'], status['result']), ('verifying', 'running'))
        self.assertEqual(status['transaction'], TX)
        self.assertTrue(status['services_restored'])

    def test_reboot_active_transaction_blocks_without_replay(self):
        value = backend()
        root = Store()
        root.replace('status.json', {'phase': 'capture', 'policy_sha256': HASH, 'transaction': TX,
                                     'boot_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'})
        facade = Mock(boot=BOOT)
        with patch.object(host, '_control_lock', return_value=123), patch.object(host.os, 'close'), patch.object(host.runtime, 'preflight_runtime', return_value=facade), patch.object(host, 'Journal', return_value=root), patch.object(host, 'public_marker') as marker, patch.object(host, 'WebBackend') as factory:
            result = host.run_web(value.policy, recovery_only=True)
            self.assertEqual(result['state'], 'operator_required')
            marker.assert_called_once_with(value.policy, BOOT, 'blocked', TX)
            factory.assert_not_called()
            facade.global_status.assert_called_once_with('operator_required', 'failed', False)

    def test_idle_reboot_rebinds_only_after_source_authority_proofs(self):
        value = backend()
        root = Store()
        root.replace('status.json', {'phase': 'idle', 'policy_sha256': HASH, 'release': RELEASE, 'services_restored': True})
        facade = Mock(boot=BOOT)
        with patch.object(host, '_control_lock', return_value=123), patch.object(host.os, 'close'), patch.object(host.runtime, 'preflight_runtime', return_value=facade), patch.object(host, 'Journal', return_value=root), patch.object(host, 'public_marker') as marker, patch.object(host, 'WebBackend') as factory:
            self.assertEqual(host.run_web(value.policy, recovery_only=True)['state'], 'idle')
            facade.verify_sources.assert_called_once()
            facade.authority.assert_called_once()
            facade.health.assert_not_called()
            facade.run.assert_not_called()
            factory.assert_not_called()
            marker.assert_called_once_with(value.policy, BOOT, 'idle')
            self.assertNotIn('transaction', root.read('status.json'))

    def test_bootstrap_refuses_unknown_or_unresolved_durable_state(self):
        value = backend()
        for prior in [{}, {'phase': 'capture', 'policy_sha256': HASH, 'transaction': TX, 'release': RELEASE}]:
            root = Store()
            if prior: root.replace('status.json', prior)
            facade = Mock(boot=BOOT)
            with patch.object(host, '_control_lock', return_value=123), patch.object(host.os, 'close'), patch.object(host.runtime, 'preflight_runtime', return_value=facade), patch.object(host, 'Journal', return_value=root), patch.object(host, 'public_marker') as marker:
                with self.assertRaisesRegex(Refused, '^bootstrap_unresolved$'):
                    host.bootstrap_web(value.policy)
                self.assertEqual(marker.call_args.args[2], 'blocked')
                facade.run.assert_not_called()
                facade.health.assert_not_called()
                facade.global_status.assert_called_once_with('operator_required', 'failed', False)

    def test_bootstrap_does_not_replace_gate_after_source_drift(self):
        value = backend()
        root = Store()
        root.replace('status.json', {'phase': 'idle', 'policy_sha256': HASH, 'release': RELEASE, 'services_restored': True})
        facade = Mock(boot=BOOT)
        facade.verify_sources.side_effect = RuntimeError('PRIVATE')
        with patch.object(host, '_control_lock', return_value=123), patch.object(host.os, 'close'), patch.object(host.runtime, 'preflight_runtime', return_value=facade), patch.object(host, 'Journal', return_value=root), patch.object(host, 'public_marker') as marker:
            with self.assertRaisesRegex(Refused, '^bootstrap_unresolved$'):
                host.bootstrap_web(value.policy)
            marker.assert_called_once_with(value.policy, BOOT, 'blocked', '0' * 32)
            facade.authority.assert_not_called()

    def test_safely_aborted_bootstrap_preserves_failure_alert(self):
        value = backend()
        root, transaction = Store(), Store()
        root.replace('status.json', {'phase': 'failed', 'policy_sha256': HASH,
            'release': RELEASE, 'transaction': TX, 'services_restored': True})
        transaction.write_once('aborted-restored.json', {'transaction': TX,
            'policy_sha256': HASH, 'services_restored': True})
        facade = Mock(boot=BOOT)
        def journal(path): return root if path == host.ROLE_ROOT else transaction
        with patch.object(host, '_control_lock', return_value=123), patch.object(host.os, 'close'), patch.object(host.runtime, 'preflight_runtime', return_value=facade), patch.object(host, 'Journal', side_effect=journal), patch.object(host, 'public_marker') as marker:
            self.assertEqual(host.bootstrap_web(value.policy)['state'], 'aborted_restored')
            marker.assert_called_once_with(value.policy, BOOT, 'idle')
            status = root.read('status.json')
            self.assertEqual((status['phase'], status['result']), ('failed', 'failed'))
            self.assertEqual(status['transaction'], TX)
            self.assertTrue(status['services_restored'])

    def test_preclaim_window_failure_alerts_and_bootstrap_preserves_it(self):
        value = backend()
        value.policy.require_new_start = Mock(side_effect=Refused('policy_expired'))
        root = Store()
        root.replace('status.json', {'phase': 'idle', 'policy_sha256': HASH,
            'release': RELEASE, 'services_restored': True})
        facade = Mock(boot=BOOT)
        with patch.object(host, '_control_lock', return_value=123), patch.object(host.os, 'close'), patch.object(host.runtime, 'preflight_runtime', return_value=facade), patch.object(host, 'Journal', return_value=root), patch.object(host, 'public_marker') as marker, patch.object(host.os, 'mkdir') as mkdir:
            with self.assertRaisesRegex(Refused, '^policy_expired$'):
                host.run_web(value.policy)
            status = root.read('status.json')
            self.assertEqual((status['phase'], status['result'], status['services_restored']), ('preflight', 'failed', True))
            self.assertNotIn('transaction', status)
            mkdir.assert_not_called()
            marker.assert_not_called()
            self.assertEqual(host.bootstrap_web(value.policy)['state'], 'prestart_failed')
            self.assertEqual(root.read('status.json')['phase'], 'preflight')
            self.assertEqual(root.read('status.json')['result'], 'failed')
            self.assertNotIn('transaction', root.read('status.json'))

    def test_missing_prior_proof_never_becomes_safe_prestart_or_creates_run(self):
        value = backend()
        value.policy.require_new_start = Mock()
        root = Store()
        facade = Mock(boot=BOOT)
        with patch.object(host, '_control_lock', return_value=123), patch.object(host.os, 'close'), patch.object(host.runtime, 'preflight_runtime', return_value=facade), patch.object(host, 'Journal', return_value=root), patch.object(host, 'public_marker') as marker, patch.object(host.os, 'mkdir') as mkdir:
            with self.assertRaisesRegex(Refused, '^prior_state_unverified$'):
                host.run_web(value.policy)
            value.policy.require_new_start.assert_not_called()
            mkdir.assert_not_called()
            marker.assert_called_once_with(value.policy, BOOT, 'blocked', None)
            status = root.read('status.json')
            self.assertEqual(status['phase'], 'operator_required')
            self.assertFalse(status['services_restored'])
            self.assertNotIn('transaction', status)

    def test_prestart_failure_does_not_block_a_future_valid_attempt(self):
        value = backend()
        value.policy.require_new_start = Mock(side_effect=Refused('new_window_reached'))
        root = Store()
        root.replace('status.json', {'schema': 'careertrellis.paired-maintenance-status/v1',
            'phase': 'preflight', 'result': 'failed', 'policy_sha256': HASH, 'boot_id': BOOT,
            'release': RELEASE, 'observed_at': '2026-10-26T00:00:00Z', 'services_restored': True})
        facade = Mock(boot=BOOT)
        with patch.object(host, '_control_lock', return_value=123), patch.object(host.os, 'close'), patch.object(host.runtime, 'preflight_runtime', return_value=facade), patch.object(host, 'Journal', return_value=root):
            with self.assertRaisesRegex(Refused, '^new_window_reached$'):
                host.run_web(value.policy)
            value.policy.require_new_start.assert_called_once()

    def test_partial_slot_claim_is_not_misclassified_as_no_claim(self):
        value = backend()
        value.policy.require_new_start = Mock(return_value='20261026')
        root = Store()
        root.replace('status.json', {'phase': 'idle', 'policy_sha256': HASH,
            'release': RELEASE, 'services_restored': True})
        slots = Store()
        slots.write_once = Mock(side_effect=Refused('journal_io'))
        facade = Mock(boot=BOOT)
        def journal(path): return slots if path == host.ROLE_ROOT / 'slots' else root
        with patch.object(host, '_control_lock', return_value=123), patch.object(host.os, 'close'), patch.object(host.runtime, 'preflight_runtime', return_value=facade), patch.object(host, 'Journal', side_effect=journal), patch.object(host.os, 'mkdir') as mkdir:
            with self.assertRaisesRegex(Refused, '^journal_io$'):
                host.run_web(value.policy)
            facade.global_status.assert_called_once_with('blocked', 'failed', True)
            self.assertEqual(len(facade.transaction), 32)
            self.assertEqual(root.read('status.json')['phase'], 'idle')
            mkdir.assert_not_called()


class InitialControlContentionTests(unittest.TestCase):
    def policy(self):
        value = backend().policy
        value.require_new_start = Mock(return_value='20261026')
        return value

    def test_short_recovery_race_waits_before_any_claim_then_acquires(self):
        policy = self.policy()
        with patch.object(host, '_control_lock', side_effect=[BlockingIOError(), BlockingIOError(), 123]) as lock, \
             patch.object(host.time, 'monotonic', side_effect=[0, .1, .3]), \
             patch.object(host.time, 'sleep') as sleep, patch.object(host, '_record_control_timeout') as record, \
             patch.object(host, 'Journal') as journal, patch.object(host, 'public_marker') as marker:
            self.assertEqual(123, host._initial_control_lock(policy))
            self.assertEqual(3, lock.call_count)
            self.assertEqual(2, sleep.call_count)
            self.assertEqual(2, policy.require_new_start.call_count)
            record.assert_not_called(); journal.assert_not_called(); marker.assert_not_called()

    def test_recovery_busy_is_benign_without_state_fence_or_run_creation(self):
        policy = self.policy()
        with patch.object(host, '_control_lock', side_effect=BlockingIOError()), \
             patch.object(host.time, 'sleep') as sleep, patch.object(host, '_record_control_timeout') as record, \
             patch.object(host, 'Journal') as journal, patch.object(host.runtime, 'preflight_runtime') as runtime, \
             patch.object(host, 'WebBackend') as worker, patch.object(host, 'public_marker') as marker:
            self.assertEqual({'state': 'control_busy', 'services_restored': False}, host.run_web(policy, recovery_only=True))
            sleep.assert_not_called(); record.assert_not_called(); journal.assert_not_called()
            runtime.assert_not_called(); worker.assert_not_called(); marker.assert_not_called()
            policy.require_new_start.assert_not_called()

    def test_timeout_records_only_diagnostic_never_owner_state_or_claim(self):
        policy = self.policy()
        with patch.object(host, '_control_lock', side_effect=BlockingIOError()), \
             patch.object(host.time, 'monotonic', side_effect=[0, 31]), \
             patch.object(host, '_record_control_timeout') as record, patch.object(host, 'Journal') as journal, \
             patch.object(host.runtime, 'preflight_runtime') as runtime, patch.object(host, 'WebBackend') as worker:
            with self.assertRaisesRegex(Refused, '^initial_control_lock_timeout$'): host.run_web(policy)
            record.assert_called_once_with(policy, 'control_lock_timeout')
            journal.assert_not_called(); runtime.assert_not_called(); worker.assert_not_called()

    def test_expired_start_window_ends_wait_without_claim_or_retry(self):
        policy = self.policy(); policy.require_new_start.side_effect = Refused('outside_start_window')
        with patch.object(host, '_control_lock', side_effect=BlockingIOError()) as lock, \
             patch.object(host.time, 'monotonic', side_effect=[0, 1]), patch.object(host.time, 'sleep') as sleep, \
             patch.object(host, '_record_control_timeout') as record:
            with self.assertRaisesRegex(Refused, '^initial_control_window_closed$'): host._initial_control_lock(policy)
            self.assertEqual(1, lock.call_count); sleep.assert_not_called()
            record.assert_called_once_with(policy, 'policy_start_refused')

    def test_metadata_or_permission_failure_never_retried_as_contention(self):
        with patch.object(host, '_control_lock', side_effect=Refused('metadata')) as lock, \
             patch.object(host.time, 'sleep') as sleep, patch.object(host, '_record_control_timeout') as record:
            with self.assertRaisesRegex(Refused, '^metadata$'): host._initial_control_lock(self.policy())
            self.assertEqual(1, lock.call_count); sleep.assert_not_called(); record.assert_not_called()

    def test_timeout_marker_bound_immutable_and_preserves_owner_status(self):
        root = Store(); owner = {'phase': 'recovery', 'transaction': TX}
        root.replace('status.json', owner)
        policy = self.policy(); invocation = 'e' * 32
        with patch.dict(host.os.environ, {'INVOCATION_ID': invocation}), \
             patch.object(host.Path, 'read_text', return_value=BOOT), patch.object(host, 'Journal', return_value=root):
            host._record_control_timeout(policy, 'control_lock_timeout')
            before = copy.deepcopy(root.values)
            host._record_control_timeout(policy, 'control_lock_timeout')
        self.assertEqual(before, root.values)
        self.assertEqual(owner, root.read('status.json'))
        marker = root.read('lock-timeout-' + invocation + '.json')
        self.assertEqual({'schema', 'policy_sha256', 'boot_id', 'release', 'invocation', 'observed_at', 'result', 'reason'}, set(marker))
        self.assertEqual((HASH, BOOT, RELEASE, invocation), tuple(marker[key] for key in ('policy_sha256', 'boot_id', 'release', 'invocation')))

    def generation_ready(self):
        value = backend(); captured = capture()
        value.store.write_once('capture-result.json', captured)
        publication = {key: captured[key] for key in ('authority', 'backup_label', 'manifest_sha256', 'release', 'snapshot_id')}
        value.store.write_once('publication-result.json', dict(publication, result='onedrive_snapshot_created'))
        value.store.write_once('writers-restored.json', {'services_restored': True, 'capture_sha256': digest(captured)})
        value.store.write_once('db-released.json', {'services_restored': True})
        value.store.write_once('db-mirror-accepted.json', {'state': 'mirror_complete', 'transaction': TX,
            'policy_sha256': HASH, 'backup_label': captured['backup_label']})
        value.store.write_once('db-health-accepted.json', {'state': 'health_complete', 'transaction': TX,
            'policy_sha256': HASH, 'normal_report_accepted': True})
        return value

    def test_verified_generation_emits_bound_proof_before_final_report_idle_never_rewrites(self):
        value = self.generation_ready(); root = Store()
        with patch.object(host, 'Journal', return_value=root), patch.object(value, '_replace_generation_projection') as projection:
            value._record_verified_generation()
            proof = root.read('verified-generation-' + TX + '.json')
            value.status('idle', 'ok', True)
            self.assertEqual(proof, root.read('verified-generation-' + TX + '.json'))
            self.assertEqual(1, projection.call_count)
            self.assertEqual('idle', root.read('status.json')['phase'])
        self.assertFalse(value.store.exists('complete.json'))
        self.assertEqual((HASH, BOOT, RELEASE, TX), tuple(proof[key] for key in ('policy_sha256', 'boot_id', 'release', 'transaction')))

    def test_generation_requires_paired_publication_restore_and_db_acceptance(self):
        changes = [('publication-result.json', 'manifest_sha256', 'f' * 64),
                   ('writers-restored.json', 'capture_sha256', 'f' * 64),
                   ('db-released.json', 'services_restored', False),
                   ('db-mirror-accepted.json', 'backup_label', '20261025-000000F'),
                   ('db-health-accepted.json', 'normal_report_accepted', False)]
        for filename, field, changed in changes:
            value = self.generation_ready(); root = Store()
            value.store.values[filename][field] = changed
            with self.subTest(file=filename), patch.object(host, 'Journal', return_value=root), \
                 patch.object(value, '_replace_generation_projection') as projection:
                with self.assertRaises(Refused): value._record_verified_generation()
                projection.assert_not_called()
            self.assertEqual({}, root.values)

    def test_complete_status_itself_does_not_invent_generation_or_monitor_proof(self):
        value = backend(); root = Store()
        with patch.object(host, 'Journal', return_value=root), patch.object(value, '_record_verified_generation') as record:
            value.status('complete', 'ok', True)
            record.assert_not_called()
        self.assertFalse(any(name.startswith('verified-generation') for name in root.values))


class PrestartAbortTests(unittest.TestCase):
    def fixture(self):
        value=backend();value.policy.data['backup_wait_seconds']=7200
        value.r.child=None;value.r.live_group.return_value=False
        value.r.state.return_value={'ActiveState':'active','SubState':'running','ControlPID':'0',
            'MainPID':'123','Job':'','InvocationID':'f'*32,'ExecMainStartTimestampMonotonic':'100'}
        original={'state':'blocked','transaction':TX,'policy_sha256':HASH,'release':RELEASE,
            'boot_id':BOOT,'worker_invocation':'d'*32,'observed_at':'2026-10-26T00:00:00Z',
            'result':'failed','services_restored':True}
        value.store.replace('status.json',original)
        slots=Store();slots.write_once('20261026.json',{'transaction':TX,'policy_sha256':HASH})
        root=Store()
        def journal(path):
            if path==host.ROLE_ROOT/'slots':return slots
            if path==host.ROLE_ROOT/'runs'/TX:return value.store
            return root
        return value,original,slots,root,journal

    def test_failed_preflight_proves_non_dispatch_consumes_same_policy_and_new_policy_gets_new_key(self):
        value,original,slots,root,journal=self.fixture()
        value.policy.require_new_start=Mock(return_value='20261026')
        value.preflight=Mock(side_effect=Refused('synthetic_preflight_failure'))
        with patch.object(host,'Journal',side_effect=journal),\
             patch.object(host,'_journal_names',side_effect=lambda store,maximum=512:set(store.values)),\
             patch.object(runtime,'protected',return_value=canonical(value.policy.data)):
            result=WebControl(value.policy,value.store,value).run(TX)
            self.assertEqual(result['state'],'prestart_aborted')
            self.assertFalse(result['restoration_performed'])
            self.assertEqual(value.store.read('status.json'),original)
            self.assertEqual(value.store.read('prestart-original-status.json'),original)
            self.assertEqual(set(value.store.values),{'status.json','prestart-original-status.json','prestart-aborted.json'})
            self.assertTrue(host._terminal_prior(value.policy,root.read('status.json')))
            value.client.call.assert_not_called()
            facade=Mock(boot=BOOT)
            with patch.object(host,'_initial_control_lock',return_value=123),patch.object(host.os,'close'),\
                 patch.object(runtime,'preflight_runtime',return_value=facade),patch.object(host,'WebBackend') as factory:
                self.assertEqual(host.run_web(value.policy)['state'],'slot_consumed')
                factory.assert_not_called()
            new=types.SimpleNamespace(digest='e'*64,data=copy.deepcopy(value.policy.data),
                require_new_start=Mock(return_value='20261026'))
            # This exact new-policy idle projection is published only by the
            # separately tested explicit policy-transition helper.
            root.replace('status.json',dict(root.read('status.json'),policy_sha256=new.digest,phase='idle',result='ok'))
            worker=Mock(current_status=('preflight','running',True))
            with patch.object(host,'_initial_control_lock',return_value=123),patch.object(host.os,'close'),\
                 patch.object(runtime,'preflight_runtime',return_value=facade),patch.object(host,'WebBackend',return_value=worker),\
                 patch.object(host.os,'mkdir'),patch.object(host,'_run_controller',return_value={'state':'prepared_new_policy'}):
                self.assertEqual(host.run_web(new)['state'],'prepared_new_policy')
            self.assertTrue(slots.exists('20261026-'+new.digest+'.json'))
            self.assertEqual(slots.read('20261026.json'),{'transaction':TX,'policy_sha256':HASH})

    def test_any_dispatch_record_or_live_fence_refuses_closure_without_new_proof(self):
        for fault in ('db-begin-intent.json','stop-intent.json','fence'):
            value,original,slots,root,journal=self.fixture()
            if fault=='fence':value.r.fence=object()
            else:value.store.write_once(fault,{'unknown':True})
            with self.subTest(fault=fault),patch.object(host,'Journal',side_effect=journal),\
                 patch.object(host,'_journal_names',side_effect=lambda store,maximum=512:set(store.values)):
                with self.assertRaises(Refused):value.prove_prestart_terminal(TX)
            self.assertFalse(value.store.exists('prestart-aborted.json'))


if __name__ == '__main__': unittest.main()
