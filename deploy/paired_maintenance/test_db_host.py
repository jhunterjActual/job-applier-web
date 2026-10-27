"""Synthetic fixed-host adapter tests; no host, systemd or provider execution."""
import copy
import datetime as dt
import hashlib
import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch, Mock

try:
    from . import db_host as h
    from .test_db_control import Store, request, IDENTITY, RELEASE, TX, HASH, capture
except ImportError:
    import db_host as h
    from test_db_control import Store, request, IDENTITY, RELEASE, TX, HASH, capture


class Memory(Store):
    def replace(self, name, value): self.values[name] = copy.deepcopy(value)


def facts(clients=0):
    return {'schema': 'schema', 'policy_rows': 1, 'maintenance': False, 'signup_open': True,
        'search_queue': 0, 'active_searches': 0, 'erasure_queue': 0, 'prepared': 0,
        'clients': clients, 'database_bytes': 100}


def policy():
    return SimpleNamespace(digest=HASH, data={'application_release': RELEASE, 'schema_revision': 'schema',
        'max_database_bytes': 1000, 'minimum_onedrive_reserve_bytes': 100, 'backup_wait_seconds': 100,
        'max_capture_objects': 100, 'max_capture_bytes': 1000000,
        'retention_policy': {'full': 4, 'differential': 14, 'archive': 4, 'expire_auto': True}}, require_new_start=lambda now: '20261026')


class Fence:
    def __init__(self, *args): self.closed = False
    def acquire(self): return self
    def check(self): pass
    def close(self): self.closed = True


class Runtime:
    def __init__(self, store):
        self.journal = store; self.boot = 'boot'; self.invocation = '1' * 32
        self.fence = None; self.events = []; self.started = False; self.observations = 0
        self.backup_failed = False; self.changed = False
    def check(self): pass
    def verify_sources(self): self.events.append('verify')
    def state(self, unit):
        if unit == h.runtime.FULL:
            self.observations += 1
            active = self.started and self.observations <= 3
            return {'ActiveState': 'active' if active else 'inactive', 'MainPID': '50' if active else '0',
                'Job': '', 'ControlPID': '0', 'InvocationID': ('f' * 32 if self.changed else '2' * 32) if self.started else '0' * 32,
                'Result': 'exit-code' if self.backup_failed else 'success', 'ExecMainStatus': '1' if self.backup_failed else '0',
                'ExecMainStartTimestampMonotonic': str(10**18)}
        return {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0', 'Job': '',
                'InvocationID': '3' * 32, 'Result': 'success', 'ExecMainStatus': '0'}
    def terminal(self, unit):
        value = self.state(unit); h.need(h.terminal(value)); return value
    def run(self, args):
        self.events.append(tuple(args))
        if args[:3] == ['/usr/bin/systemctl', 'start', '--no-block']:
            self.started = True
            return b''
        if '--operation' in args:
            operation = args[args.index('--operation') + 1]
            return json.dumps({'operation': operation, 'result': 'ok', 'backup_label': '20261026-001723F',
                'mirrored_backup_label': '20261026-001723F', 'started_at': '2026-10-26T00:00:00Z',
                'completed_at': '2026-10-26T00:01:00Z'}).encode()
        raise AssertionError('Unexpected host operation')
    def status(self, state, **fields):
        self.events.append('state_' + state)
        value = dict(state=state, transaction=TX, policy_sha256=HASH, **fields)
        self.journal.replace('status.json', value)
        return value
    def global_status(self, *args, **kwargs): pass


class DBHostTests(unittest.TestCase):
    def setUp(self):
        self.store = Memory(); self.p = policy(); self.r = Runtime(self.store)
        self.store.write_once('begin-intent.json', request('begin', IDENTITY))
        self.store.write_once('quiesced.json', request('quiesced', dict(IDENTITY, quiesced_at=90, services_stopped=True)))
        self.store.write_once('capture.json', request('capture', dict(IDENTITY, capture=capture())))
        self.store.write_once('restored.json', request('restored', dict(IDENTITY, services_restored=True, capture_sha256=h.digest(capture()))))
        self.patches = [patch.object(h.runtime, 'Fence', Fence), patch.object(h.provider, 'database_facts', return_value=facts()),
                        patch.object(h.provider, 'retention_facts', return_value=copy.deepcopy(self.p.data['retention_policy'])),
                        patch.object(h.runtime, 'validate_pair'), patch.object(h.runtime, 'validate_capture'),
                        patch.object(h.time, 'sleep'), patch.object(h, 'pins'),
                        patch.object(h.runtime, 'protected', return_value=b'core'),
                        patch.dict(h.runtime.PINS['db'], {h.runtime.CORE: hashlib.sha256(b'core').hexdigest()})]
        for value in self.patches: value.start(); self.addCleanup(value.stop)

    def worker(self): return h.DBWorker(self.p, TX, self.r)

    def test_worker_pairs_one_backup_and_releases_only_after_restored(self):
        result = self.worker().run()
        self.assertEqual('released', result['state'])
        starts = [v for v in self.r.events if isinstance(v, tuple) and v[:3] == ('/usr/bin/systemctl', 'start', '--no-block')]
        self.assertEqual([('/usr/bin/systemctl', 'start', '--no-block', h.runtime.FULL)], starts)
        self.assertIn('state_sealed', self.r.events)
        self.assertIsNone(self.r.fence)

    def test_same_boot_recovery_never_starts_backup(self):
        self.store.write_once('worker-started.json', {'boot_id': 'boot', 'worker_invocation': '1' * 32})
        result = self.worker().run(recovery_only=True)
        self.assertEqual('aborted', result['state'])
        self.assertFalse(self.r.started)
        self.assertLess(self.r.events.index('state_abort_ready'), self.r.events.index('state_aborted'))

    def test_changed_boot_keeps_fence_and_requires_operator(self):
        self.store.write_once('worker-started.json', {'boot_id': 'OLD', 'worker_invocation': '1' * 32})
        result = self.worker().run(True)
        self.assertTrue(result['operator_required'])
        self.assertFalse(self.r.fence.closed)
        self.assertFalse(self.r.started)

    def test_unknown_backup_dispatch_is_not_retried_or_killed(self):
        self.store.write_once('worker-started.json', {'boot_id': 'boot', 'worker_invocation': '1' * 32})
        self.store.write_once('backup-intent.json', {'boot_id': 'boot', 'prior_invocation': '0' * 32, 'started_monotonic_us': 1})
        result = self.worker().run(True)
        self.assertTrue(result['operator_required'])
        self.assertFalse(self.r.started)
        self.assertFalse(any(isinstance(v, tuple) and 'kill' in v for v in self.r.events))

    def test_known_failed_backup_aborts_safely_without_killing(self):
        self.r.backup_failed = True
        result = self.worker().run()
        self.assertEqual('aborted', result['state'])
        self.assertIn('state_abort_ready', self.r.events)
        self.assertFalse(any(isinstance(v, tuple) and 'kill' in v for v in self.r.events))

    def test_clients_after_quiescence_refuse_backup_then_abort(self):
        with patch.object(h.provider, 'database_facts', return_value=facts(3)):
            result = self.worker().run()
        self.assertEqual('aborted', result['state'])
        self.assertFalse(self.r.started)

    def test_preflight_allows_runtime_clients_but_checks_policy_and_capacity(self):
        backend = object.__new__(h.DBBackend)
        backend.p = self.p; backend.policy = self.p; backend.r = self.r
        with patch.object(h.provider, 'database_facts', return_value=facts(3)), \
             patch.object(h.provider, 'db_provider_facts', return_value={'source_count': 1, 'source_bytes': 100, 'onedrive_free_bytes': 200}), \
             patch.object(h.provider, 'retention_facts', return_value=copy.deepcopy(self.p.data['retention_policy'])):
            self.assertTrue(backend.preflight()['ready'])
        wrong = facts(); wrong['schema'] = 'old'
        with self.assertRaises(h.Refused): h.facts_ok(self.p, wrong)

    def test_retention_drift_under_fence_never_advertises_fenced_or_starts_backup(self):
        changed = dict(self.p.data['retention_policy'], full=5)
        with patch.object(h.provider, 'retention_facts', return_value=changed):
            result = self.worker().run()
        self.assertEqual('aborted', result['state'])
        self.assertNotIn('state_fenced', self.r.events)
        self.assertFalse(self.r.started)
        self.assertLess(self.r.events.index('state_abort_ready'), self.r.events.index('state_aborted'))

    def test_status_absent_store_no_dispatch_or_directory_creation(self):
        backend = object.__new__(h.DBBackend); backend.transaction = TX; backend.store = Memory()
        self.assertTrue(backend.worker_status(TX)['operator_required'])
        self.assertEqual({}, backend.store.values)

    def test_mirror_binding_wrong_label_or_manifest_refuses_before_dispatch(self):
        backend = object.__new__(h.DBBackend)
        backend.transaction = TX; backend.store = self.store; backend.policy = self.p; backend.r = self.r
        self.store.replace('status.json', {'state': 'released'})
        with self.assertRaises(h.Refused):
            backend.start_mirror(TX, {'release': RELEASE, 'backup_label': '20261019-000000F', 'publication_manifest_sha256': 'e' * 64})
        self.assertFalse(self.r.started)

    def test_changed_original_mirror_invocation_refuses_canonical_acceptance(self):
        backend = object.__new__(h.DBBackend)
        backend.transaction = TX; backend.store = self.store; backend.r = self.r
        self.store.write_once('mirror-unit-dispatch.json', {'boot_id': 'boot', 'invocation': '9' * 32})
        with self.assertRaises(h.Refused): backend.mirror_status(TX)

    def test_complete_requires_canonical_mirror_and_health_report_proofs(self):
        backend = object.__new__(h.DBBackend)
        backend.transaction = TX; backend.store = self.store; backend.policy = self.p; backend.r = self.r
        payload = {'release': RELEASE, 'backup_label': '20261026-001723F', 'publication_manifest_sha256': 'e' * 64}
        self.store.write_once('mirror-intent.json', request('mirror', payload))
        self.store.replace('status.json', {'state': 'released', 'transaction': TX, 'worker_invocation': '7' * 32})
        with patch.object(backend, 'mirror_status', return_value={'state': 'mirror_complete'}), \
             patch.object(backend,'_native_retention',return_value=None), \
             patch.object(backend, 'health_status', side_effect=h.Refused('report_unproven')):
            with self.assertRaises(h.Refused): backend.complete(TX, payload)
        self.assertFalse(self.store.exists('complete-result.json'))
        with patch.object(backend, 'mirror_status', return_value={'state': 'mirror_complete'}), \
             patch.object(backend,'_native_retention',return_value=None), \
             patch.object(backend, 'health_status', return_value={'state': 'health_complete'}), \
             patch.object(backend, '_global'):
            backend.complete(TX, payload)
        self.assertEqual('complete', self.store.read('status.json')['state'])
        self.assertEqual('7' * 32, self.store.read('status.json')['worker_invocation'])

    def test_health_report_pending_is_bounded_and_status_readonly(self):
        backend = object.__new__(h.DBBackend)
        backend.transaction = TX; backend.store = self.store; backend.r = self.r
        self.store.write_once('health-unit-intent.json', {'issued_epoch': 100})
        self.store.write_once('health-unit-dispatch.json', {'invocation': '1' * 32})
        value = {'result': 'ok', 'failure_codes': [], 'evaluated_at': '1970-01-01T00:01:40Z'}
        before = copy.deepcopy(self.store.values)
        with patch.object(backend, '_unit_status', return_value={'ActiveState': 'inactive', 'MainPID': '0',
             'ControlPID': '0', 'Job': '', 'Result': 'success', 'ExecMainStatus': '0'}), \
             patch.object(self.r, 'run', return_value=json.dumps(value).encode()), \
             patch.object(backend, '_reporter_accepted', side_effect=h.Refused('reporter_pending')):
            with patch.object(h.time, 'time', return_value=150):
                self.assertEqual('health_running', backend.health_status(TX)['state'])
            with patch.object(h.time, 'time', return_value=221):
                with self.assertRaises(h.Refused): backend.health_status(TX)
        self.assertEqual(before, self.store.values)

    def test_worker_dispatch_lost_ack_consumes_slot_and_never_restarts(self):
        backend = object.__new__(h.DBBackend)
        backend.transaction = TX; backend.store = self.store; backend.policy = self.p; backend.r = self.r
        root = Memory(); slots = Memory()
        root.replace('status.json', {'phase': 'idle'})
        def journal(path): return slots if str(path).endswith('slots') else root
        with patch.object(h, 'Journal', side_effect=journal), \
             patch.object(self.r, 'run', side_effect=h.Refused('command_failed')):
            with self.assertRaises(h.Refused): backend.start_db_worker(TX)
            self.assertEqual(TX, slots.read('20261026-'+HASH+'.json')['transaction'])
            with self.assertRaises(h.Refused): backend.start_db_worker(TX)
        self.assertTrue(self.store.exists('worker-dispatch.json'))

    def test_unresolved_other_transaction_refuses_new_worker(self):
        backend = object.__new__(h.DBBackend)
        backend.transaction = TX; backend.store = self.store; backend.policy = self.p; backend.r = self.r
        root = Memory(); root.replace('status.json', {'phase': 'operator_required', 'transaction': 'f' * 32})
        with patch.object(h, 'Journal', return_value=root):
            with self.assertRaises(h.Refused): backend.start_db_worker(TX)
        self.assertFalse(self.store.exists('worker-dispatch.json'))

    def test_legacy_date_claim_requires_terminal_old_policy_before_new_namespace(self):
        backend=object.__new__(h.DBBackend)
        backend.transaction=TX;backend.store=self.store;backend.policy=self.p;backend.r=self.r
        root=Memory();root.replace('status.json',{'phase':'idle'})
        slots=Memory();old=Memory();old_tx='f'*32
        slots.write_once('20261026.json',{'transaction':old_tx,'policy_sha256':HASH})
        def journal(path):
            if path==h.ROLE_ROOT/'slots':return slots
            if path==h.ROLE_ROOT/'runs'/old_tx:return old
            return root
        with patch.object(h,'Journal',side_effect=journal):
            with self.assertRaises(h.Refused):backend.start_db_worker(TX)
            backend.policy=copy.deepcopy(self.p);backend.policy.digest='e'*64
            with self.assertRaises(KeyError):backend.start_db_worker(TX)
            old.write_once('complete-result.json',{'transaction':old_tx,'policy_sha256':HASH})
            old.replace('status.json',{'state':'complete','transaction':old_tx,'policy_sha256':HASH,
                'boot_id':self.r.boot,'release':RELEASE,'services_restored':True})
            backend.start_db_worker(TX)
        self.assertTrue(slots.exists('20261026-'+'e'*64+'.json'))
        self.assertEqual(slots.read('20261026.json'),{'transaction':old_tx,'policy_sha256':HASH})

    def test_changed_health_core_refused_before_execution(self):
        with patch.object(h.runtime, 'protected', return_value=b'changed'):
            with self.assertRaises(h.Refused): h.latest(self.r, 'full_backup')
        self.assertEqual([], self.r.events)


class NativeBundleDBTests(unittest.TestCase):
    def fixture(self):
        from deploy.onedrive_retention import endpoint as real_endpoint
        value=object.__new__(h.DBBackend);value.transaction=TX;value.policy=policy();value.store=Memory()
        value.r=Runtime(value.store)
        captured=capture()
        for name,record in {'begin-intent.json':request('begin',IDENTITY),
            'capture.json':request('capture',dict(IDENTITY,capture=captured)),
            'restored.json':request('restored',dict(IDENTITY,services_restored=True,capture_sha256=h.digest(captured))),
            'status.json':{'state':'released','transaction':TX,'policy_sha256':HASH,'release':RELEASE,
                           'worker_invocation':'7'*32}}.items():value.store.write_once(name,record)
        bindings={'paired_policy_sha256':HASH,'standalone_policy_sha256':'a'*64}
        context=SimpleNamespace(bindings=bindings,document={'application_release':RELEASE})
        selector=SimpleNamespace(digest='b'*64,bindings=bindings,role='db')
        facts={'archive_sha256':'c'*64,'archive_bytes':10240,'generation_sha256':'d'*64,
               'backup_label':captured['backup_label'],'native_restore_accepted':True}
        def status(stage,details):return {'schema':real_endpoint.RESPONSE,'capture_transaction':TX,
            'retention_policy_sha256':'a'*64,'stage':stage,'state':'complete','receipt_sha256':'e'*64,'facts':details}
        statuses={'restore':status('restore',facts),'retire':status('retire',{'catalog_sha256':'f'*64,'retired_count':0})}
        record={'entry':{'archive_sha256':'c'*64,'native_restore_receipt_sha256':'e'*64},
            'evidence':{'native':{'transaction':TX,'archive_sha256':'c'*64,'backup_label':captured['backup_label'],
                                  'native_restore_accepted':True},
                        'download':{'transaction':TX,'backup_label':captured['backup_label']}}}
        held=[False]
        class Catalog:
            def __init__(self,fd):self.io=self
            @contextmanager
            def locked(self):
                if not held[0]:raise AssertionError('catalog without repository fence')
                yield
            def records(self):return {'c'*64:copy.deepcopy(record)}
            def retired(self,records):return set()
            def require_repository_lock(self):
                if not held[0]:raise AssertionError('repository fence lost')
        @contextmanager
        def repository_lock():
            held[0]=True
            try:yield 7,(1,2)
            finally:held[0]=False
        adapter=SimpleNamespace(observe=lambda query:copy.deepcopy(statuses[query['payload']['stage']]))
        endpoint=SimpleNamespace(request=real_endpoint.request,validate_response=real_endpoint.validate_response,
                                 NativeDB=lambda policy:adapter)
        coordinator=SimpleNamespace(PRIVATE_POLICY=object(),Policy=lambda document:SimpleNamespace(digest='a'*64))
        activation=SimpleNamespace(require_activated=lambda ctx:selector)
        modules={'coordinator':coordinator,'endpoint':endpoint,'catalog':SimpleNamespace(NativeStore=Catalog),
                 'activation':activation}
        entry=SimpleNamespace(verify_install=lambda:'1'*40,_native_dependencies=lambda *args:context,
            _protected_document=lambda *args:(b'',{}),_load=lambda name:modules[name])
        selected=(entry,context,selector)
        payload={'release':RELEASE,'backup_label':captured['backup_label'],
                 'publication_manifest_sha256':captured['manifest_sha256']}
        return value,selected,repository_lock,statuses,record,payload

    def test_native_health_and_complete_use_real_named_bundle_record_without_legacy_mirror(self):
        value,selected,lock,statuses,record,payload=self.fixture()
        with patch.object(value,'_native_retention',return_value=selected), \
                patch.object(h.provider,'repository_lock',side_effect=lock), \
                patch.object(value,'mirror_status',side_effect=AssertionError('legacy mirror')), \
                patch.object(h,'pins'),patch.object(value,'_dispatch') as dispatch, \
                patch.object(value,'health_status',return_value={'state':'health_complete','normal_report_accepted':True}), \
                patch.object(value,'_global'):
            value.start_health(TX);dispatch.assert_called_once_with('health',h.HEALTH)
            accepted=value.store.read('bundle-accepted.json')
            self.assertEqual(accepted['backup_label'],payload['backup_label'])
            self.assertEqual(accepted['native_receipt_sha256'],'e'*64)
            self.assertFalse(value.store.exists('mirror-intent.json'))
            value.complete(TX,payload)
        self.assertEqual(value.store.read('status.json')['state'],'complete')
        self.assertTrue(value.store.exists('complete-result.json'))

    def test_wrong_transaction_label_or_receipt_never_dispatches_health_or_writes_acceptance(self):
        for mutate in (lambda statuses,record:statuses['restore'].update(capture_transaction='f'*32),
            lambda statuses,record:statuses['restore']['facts'].update(backup_label='20261027-001723F'),
            lambda statuses,record:record['entry'].update(native_restore_receipt_sha256='f'*64),
            lambda statuses,record:record['evidence']['native'].update(transaction='f'*32),
            lambda statuses,record:statuses['retire'].update(state='running',receipt_sha256='',facts={})):
            value,selected,lock,statuses,record,payload=self.fixture();mutate(statuses,record)
            with patch.object(value,'_native_retention',return_value=selected), \
                    patch.object(h.provider,'repository_lock',side_effect=lock), \
                    patch.object(value,'_dispatch') as dispatch,patch.object(h,'pins'):
                with self.assertRaises(Exception):value.start_health(TX)
            dispatch.assert_not_called();self.assertFalse(value.store.exists('bundle-accepted.json'))

    def test_native_completion_keeps_health_delivery_and_exact_capture_payload_gates(self):
        for health,payload_change in (({'state':'health_complete','normal_report_accepted':False},False),
                                     ({'state':'health_complete','normal_report_accepted':True},True)):
            value,selected,lock,statuses,record,payload=self.fixture()
            if payload_change:payload['backup_label']='20261027-001723F'
            with patch.object(value,'_native_retention',return_value=selected), \
                    patch.object(h.provider,'repository_lock',side_effect=lock), \
                    patch.object(value,'health_status',return_value=health),patch.object(value,'_global'):
                with self.assertRaises(h.Refused):value.complete(TX,payload)
            self.assertFalse(value.store.exists('complete-result.json'))


if __name__ == '__main__': unittest.main()
