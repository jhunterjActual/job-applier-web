"""Fault-injected WEB orchestration contracts; no operating-system adapter."""
import copy
import unittest

try:
    from .common import Refused, canonical
    from .web_control import WebControl
    from .common import digest
    from .test_db_control import Store, capture, IDENTITY, RELEASE, TX, HASH
except ImportError:
    from common import Refused, canonical
    from web_control import WebControl
    from common import digest
    from test_db_control import Store, capture, IDENTITY, RELEASE, TX, HASH


class Policy:
    digest = HASH
    data = {'application_release': RELEASE, 'backup_wait_seconds': 7200}
    def __init__(self): self.calls = 0; self.allowed = True; self.fresh = False; self.close_after_begin = False
    def require_new_start(self, now):
        self.calls += 1
        if not self.allowed or (self.close_after_begin and self.calls > 1):
            raise Refused('outside_start_window')
    def due(self, captured, now): return not self.fresh


class Backend:
    def __init__(self, store):
        self.store = store; self.events = []; self.fail = None; self.failed = False
        self.peer_state = None; self.safe = False; self.stopped = False
        self.blocked = False; self.fenced = False; self.identity_value = copy.deepcopy(IDENTITY)
        self.ready = True; self.publications = 0; self.begins = 0; self.restores = 0
        self.store.write_once('status.json',{'state':'preflight'})
    def event(self, name):
        self.events.append(name)
        if self.fail == name and not self.failed:
            self.failed = True
            raise RuntimeError('PRIVATE PROVIDER ERROR MUST NOT ESCAPE')
    def now(self): return 100
    def identity(self): return copy.deepcopy(self.identity_value)
    def preflight(self):
        self.event('preflight')
        return {'ready': self.ready, 'last_capture_started_at': '2026-10-01T00:00:00Z'}
    def prove_prestart_terminal(self,transaction):
        assert self.store.exists('status.json')
        assert not any(self.store.exists(name) for name in ('start-intent.json','db-begin-intent.json','stop-intent.json'))
        self.event('prove_prestart')
        self.store.write_once('prestart-original-status.json',{'state':'blocked'})
        value={'schema':'careertrellis.paired-maintenance-prestart-abort/v1',
            'transaction':transaction,'policy_sha256':HASH,'release':RELEASE,
            'boot_id':IDENTITY['web_boot'],'slot_claim_name':'20261026.json',
            'original_status_sha256':digest({'state':'blocked'}),'no_dispatch':True,
            'services_unchanged':True,'services_restored':True,'restoration_performed':False,
            'verified_at':'2026-10-26T00:00:00Z','verifier_invocation':IDENTITY['web_invocation']}
        self.store.write_once('prestart-aborted.json',value)
        return value
    def acquire_fence(self): self.fenced = True; self.event('acquire_fence')
    def release_fence(self): self.fenced = False; self.event('release_fence')
    def snapshot_writers(self): self.event('snapshot'); return {'web': 10, 'search': 11, 'erasure': 12}
    def block_writers(self):
        assert self.store.exists('stop-intent.json')
        self.blocked = True; self.event('block_writers')
    def stop_writers(self): self.stopped = True; self.event('stop_writers'); return 90
    def capture(self, pair):
        assert self.stopped and pair['backup_label'] == '20261026-001723F'
        self.event('capture'); return capture()
    def allow_writer_restore(self): assert self.safe; self.event('allow_writer_restore')
    def restore_writers(self, recovery=False):
        assert self.safe
        # With no stop-intent the real adapter must only verify existing health.
        if self.store.exists('stop-intent.json'):
            self.stopped = False; self.restores += 1
        self.event('restore_recovery' if recovery else 'restore_writers')
    def clear_writer_block(self): self.blocked = False; self.event('clear_writer_block')
    def publish(self, value):
        assert self.store.exists('publish-intent.json') and not self.stopped
        self.publications += 1; self.event('publish')
        return dict(value, result='onedrive_snapshot_created')
    def peer(self, op, payload):
        if op == 'begin':
            assert self.store.exists('db-begin-intent.json')
            self.begins += 1; self.peer_state = 'fenced'
        elif op == 'quiesced': self.peer_state = 'pair_ready'
        elif op == 'capture': self.peer_state = 'sealed'
        elif op == 'abort': self.peer_state = 'abort_ready'
        elif op == 'restored': self.peer_state = 'aborted' if self.peer_state == 'abort_ready' else 'released'
        self.event('peer_' + op)
        return {'state': self.peer_state}
    def wait_peer(self, states, timeout):
        desired = self.peer_state
        assert desired in states
        self.event('wait_' + desired)
        assert self.peer_state == desired
        if desired in ('sealed', 'abort_ready'): self.safe = True
        return {'state': desired, 'pair': {'backup_label': '20261026-001723F'}}
    def wait_mirror(self): self.event('wait_mirror')
    def wait_health(self): self.event('wait_health')
    def normal_reports(self): self.event('normal_reports')
    def status(self, phase, result='running', services_restored=False): self.events.append('status_' + phase)


class WebControlTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(); self.policy = Policy(); self.backend = Backend(self.store)
        self.control = WebControl(self.policy, self.store, self.backend)

    def native_backend(self):
        strategy={'mode':'native-bundle-v2','retention_policy_sha256':'a'*64,'activation_sha256':'b'*64}
        prior=self.backend.preflight
        self.backend.preflight=lambda:dict(prior(),backup_strategy=copy.deepcopy(strategy))
        self.backend.bundle_calls=0
        def run(value,selected):
            self.assertEqual(selected,strategy)
            self.assertFalse(self.backend.stopped or self.backend.fenced or self.backend.blocked)
            self.assertTrue(self.store.exists('db-released.json') and self.store.exists('publication-result.json'))
            self.backend.bundle_calls+=1;self.backend.event('native_bundle')
            return {'schema':'careertrellis.paired-native-bundle-accepted/v1','state':'native_bundle_complete',
                'transaction':TX,'policy_sha256':HASH,'retention_policy_sha256':'a'*64,
                'activation_sha256':'b'*64,'backup_label':value['backup_label'],'native_restore_accepted':True,
                'archive_sha256':'c'*64,'native_receipt_sha256':'d'*64,'restore_status_sha256':'e'*64,
                'bundle_completion_sha256':'f'*64}
        self.backend.run_native_bundle=run
        return run

    def test_native_bundle_replaces_only_legacy_db_mirror_after_publication(self):
        self.native_backend()
        self.assertEqual(self.control.run(TX)['state'],'complete')
        self.assertEqual(self.backend.bundle_calls,1)
        self.assertNotIn('peer_mirror',self.backend.events);self.assertNotIn('wait_mirror',self.backend.events)
        self.assertLess(self.backend.events.index('publish'),self.backend.events.index('native_bundle'))
        self.assertLess(self.backend.events.index('native_bundle'),self.backend.events.index('peer_health'))
        self.assertIn('normal_reports',self.backend.events)
        self.assertFalse(self.store.exists('db-mirror-accepted.json'))
        self.assertTrue(self.store.exists('db-bundle-accepted.json'))
        self.assertEqual(self.store.read('start-intent.json')['backup_strategy']['mode'],'native-bundle-v2')
        self.control.run(TX);self.assertEqual(self.backend.bundle_calls,1)

    def test_wrong_bundle_label_or_non_native_result_never_reaches_health(self):
        for key,bad in (('backup_label','20261027-001723F'),('native_restore_accepted',False),
                        ('retention_policy_sha256','f'*64),('activation_sha256','f'*64)):
            with self.subTest(key=key):
                self.setUp();original=self.native_backend()
                self.backend.run_native_bundle=lambda *args:dict(original(*args),**{key:bad})
                self.assertEqual(self.control.run(TX)['state'],'operator_required')
                self.assertNotIn('peer_health',self.backend.events)
                self.assertFalse(self.store.exists('complete.json'))

    def test_native_dispatch_ambiguity_is_never_replayed_or_fallback_mirrored(self):
        self.native_backend();self.backend.fail='native_bundle'
        self.assertEqual(self.control.run(TX)['state'],'operator_required')
        self.assertEqual(self.backend.bundle_calls,1)
        self.control.run(TX,recovery_only=True)
        self.assertEqual(self.backend.bundle_calls,1);self.assertNotIn('peer_mirror',self.backend.events)

    def test_native_health_failure_preserves_evidence_and_withholds_completion(self):
        self.native_backend();self.backend.fail='wait_health'
        self.assertEqual(self.control.run(TX)['state'],'operator_required')
        self.assertTrue(self.store.exists('db-bundle-accepted.json'))
        self.assertFalse(self.store.exists('complete.json'));self.assertNotIn('normal_reports',self.backend.events)

    def test_invalid_strategy_refuses_before_new_transaction_or_writer_stop(self):
        prior=self.backend.preflight
        self.backend.preflight=lambda:dict(prior(),backup_strategy={'mode':'native-bundle-v2'})
        self.assertEqual(self.control.run(TX)['state'],'prestart_aborted')
        self.assertFalse(self.store.exists('start-intent.json'))
        self.assertFalse(self.store.exists('stop-intent.json'))

    def test_success_and_complete_replay_no_backend_actions(self):
        result = self.control.run(TX)
        self.assertEqual('complete', result['state'])
        self.assertEqual(1, self.backend.begins); self.assertEqual(1, self.backend.publications)
        self.assertFalse(self.backend.fenced or self.backend.blocked or self.backend.stopped)
        events = list(self.backend.events)
        self.assertEqual(result, self.control.run(TX))
        self.assertEqual(events, self.backend.events)

    def test_outside_window_not_ready_or_fresh_never_stops(self):
        for setting in ('outside', 'blocked', 'fresh'):
            with self.subTest(setting=setting):
                self.setUp()
                if setting == 'outside': self.policy.allowed = False
                elif setting == 'blocked': self.backend.ready = False
                else: self.policy.fresh = True
                self.assertEqual('skipped' if setting=='fresh' else 'prestart_aborted', self.control.run(TX)['state'])
                self.assertFalse(self.store.exists('stop-intent.json'))
                self.assertEqual(0, self.backend.begins)

    def test_window_closes_after_db_begin_aborts_without_stop(self):
        self.policy.close_after_begin = True
        self.assertEqual('aborted_restored', self.control.run(TX)['state'])
        self.assertFalse(self.store.exists('stop-intent.json'))
        self.assertEqual(0, self.backend.restores)
        self.assertEqual(1, self.backend.begins)

    def test_fault_after_each_mutation_never_replays_backup_capture_or_publication(self):
        failures = ('acquire_fence', 'peer_begin', 'snapshot', 'block_writers', 'stop_writers',
                    'peer_quiesced', 'capture', 'peer_capture', 'allow_writer_restore',
                    'restore_writers', 'peer_restored', 'release_fence', 'clear_writer_block',
                    'publish', 'peer_mirror', 'wait_mirror', 'peer_health', 'wait_health', 'normal_reports')
        for fault in failures:
            with self.subTest(fault=fault):
                self.setUp(); self.backend.fail = fault
                result = self.control.run(TX)
                self.assertIn(result['state'], ('aborted_restored', 'operator_required', 'skipped'))
                self.assertNotIn('PRIVATE', str(result))
                self.assertLessEqual(self.backend.begins, 1)
                self.assertLessEqual(self.backend.publications, 1)
                before = (self.backend.begins, self.backend.publications, self.backend.events.count('capture'))
                WebControl(self.policy, self.store, self.backend).run(TX, recovery_only=True)
                self.assertEqual(before, (self.backend.begins, self.backend.publications, self.backend.events.count('capture')))

    def test_no_restore_until_db_proves_abort_ready(self):
        self.backend.fail = 'capture'
        original_wait = self.backend.wait_peer
        def wait(states, timeout):
            if 'abort_ready' in states: raise TimeoutError('PRIVATE')
            return original_wait(states, timeout)
        self.backend.wait_peer = wait
        self.assertEqual('operator_required', self.control.run(TX)['state'])
        self.assertTrue(self.backend.stopped and self.backend.blocked and self.backend.fenced)
        self.assertEqual(0, self.backend.restores)

    def test_normal_restoration_waits_for_sealed(self):
        self.control.run(TX)
        events = self.backend.events
        self.assertLess(events.index('wait_sealed'), events.index('allow_writer_restore'))
        self.assertLess(events.index('restore_writers'), events.index('peer_restored'))

    def test_db_released_failure_does_not_abort_active_online_work(self):
        self.backend.fail = 'peer_mirror'
        result = self.control.run(TX)
        self.assertTrue(result['services_restored'])
        self.assertEqual('operator_required', result['state'])
        self.assertNotIn('peer_abort', self.backend.events)
        self.assertEqual(1, self.backend.publications)

    def test_existing_start_recovers_never_begins_again_even_outside_window(self):
        self.store.write_once('start-intent.json', {'transaction': TX, 'policy_sha256': HASH, 'identity': IDENTITY})
        self.store.write_once('db-begin-intent.json', IDENTITY)
        self.policy.allowed = False
        self.assertEqual('aborted_restored', self.control.run(TX)['state'])
        self.assertEqual(0, self.backend.begins)
        self.assertFalse(self.store.exists('publish-intent.json'))

    def test_boot_or_release_drift_keeps_existing_block(self):
        for key, value in (('web_boot', '00000000-0000-0000-0000-000000000000'), ('release', 'f' * 40)):
            with self.subTest(key=key):
                self.setUp()
                self.store.write_once('start-intent.json', {'transaction': TX, 'policy_sha256': HASH, 'identity': IDENTITY})
                self.backend.blocked = True; self.backend.identity_value[key] = value
                self.assertEqual('operator_required', self.control.run(TX, True)['state'])
                self.assertTrue(self.backend.blocked)
                self.assertNotIn('peer_abort', self.backend.events)

    def test_changed_recovery_invocation_uses_original_identity(self):
        self.store.write_once('start-intent.json', {'transaction': TX, 'policy_sha256': HASH, 'identity': IDENTITY})
        self.store.write_once('db-begin-intent.json', IDENTITY)
        self.backend.identity_value['web_invocation'] = 'f' * 32
        original = self.backend.peer
        def peer(op, payload):
            if op in ('abort', 'restored'): self.assertEqual(IDENTITY['web_invocation'], payload['web_invocation'])
            return original(op, payload)
        self.backend.peer = peer
        self.assertEqual('aborted_restored', self.control.run(TX, True)['state'])

    def test_unknown_recovery_does_not_create_transaction(self):
        self.store.values.clear()
        self.assertEqual('operator_required', self.control.run(TX, True)['state'])
        self.assertEqual({}, self.store.values)

    def test_preflight_failure_closes_without_any_dispatch_or_restoration(self):
        self.backend.fail='preflight'
        result=self.control.run(TX)
        self.assertEqual(result['state'],'prestart_aborted')
        self.assertFalse(result['restoration_performed'])
        self.assertEqual(self.backend.begins,0)
        self.assertEqual(self.backend.restores,0)
        self.assertFalse(self.store.exists('start-intent.json'))
        self.assertTrue(self.store.exists('prestart-aborted.json'))
        WebControl(self.policy,self.store,self.backend).run(TX,True)
        self.assertEqual(self.backend.begins,0)

    def test_missing_required_policy_is_not_disguised_as_success(self):
        self.policy.data = {}
        with self.assertRaises(KeyError): WebControl(self.policy, self.store, self.backend)


if __name__ == '__main__': unittest.main()
