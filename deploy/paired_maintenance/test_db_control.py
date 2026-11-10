"""Synthetic deterministic DB endpoint tests; no host or provider operations."""
import copy
import unittest
from types import SimpleNamespace

try:
    from .common import Refused, canonical
    from .db_control import DBControl, SCHEMA, validate_request
except ImportError:
    from common import Refused, canonical
    from db_control import DBControl, SCHEMA, validate_request


RELEASE = 'a' * 40
TX = 'b' * 32
HASH = 'c' * 64
IDENTITY = {'release': RELEASE, 'web_boot': '12345678-1234-1234-1234-123456789abc',
            'web_invocation': 'd' * 32}


def request(operation, payload=None):
    return {'schema': SCHEMA, 'operation': operation, 'transaction': TX,
            'policy_sha256': HASH, 'payload': copy.deepcopy(payload or {})}


def capture():
    return {'result': 'pass', 'manifest_verified': True, 'release': RELEASE,
            'backup_label': '20261026-001723F', 'snapshot_id': RELEASE + '-20261026-001723F',
            'authority': 'postgres-r2-authoritative', 'object_count': 28,
            'ciphertext_bytes': 793722, 'manifest_sha256': 'e' * 64,
            'capture_started_epoch': 100, 'captured_epoch': 105}


class Store:
    def __init__(self): self.values = {}
    def exists(self, name): return name in self.values
    def read(self, name): return copy.deepcopy(self.values[name])
    def write_once(self, name, value):
        if name in self.values and canonical(value) != canonical(self.values[name]):
            raise Refused('journal_collision')
        self.values[name] = copy.deepcopy(value)


class Policy:
    digest = HASH
    data = {'application_release': RELEASE}
    allowed = True
    def require_new_start(self, now):
        if not self.allowed: raise Refused('outside_start_window')


class Backend:
    def __init__(self, store): self.store = store; self.calls = []; self.fail = None; self.missing = False
    def now(self): return 100
    def preflight(self): return {'state': 'preflight'}
    def invoke(self, name, tx):
        self.calls.append(name)
        intent = {'begin': 'begin', 'mirror': 'mirror', 'health': 'health', 'complete': 'complete', 'abort': 'abort-dispatch'}[name]
        assert self.store.exists(intent + '-intent.json')
        if self.fail == name: raise RuntimeError('PRIVATE ERROR MUST NOT LEAVE BACKEND')
    def start_db_worker(self, tx): self.invoke('begin', tx)
    def start_mirror(self, tx, payload): self.invoke('mirror', tx)
    def start_health(self, tx): self.invoke('health', tx)
    def complete(self, tx, payload): self.invoke('complete', tx)
    def request_abort(self, tx): self.invoke('abort', tx)
    def worker_status(self, tx):
        if self.missing: raise FileNotFoundError('PRIVATE')
        return {'state': 'running'}
    def mirror_status(self, tx): return self.worker_status(tx)
    def health_status(self, tx): return self.worker_status(tx)


class DBControlTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(); self.policy = Policy(); self.backend = Backend(self.store)
        self.control = DBControl(self.policy, self.store, self.backend)

    def begin(self): return self.control.handle(request('begin', IDENTITY))

    def test_all_readonly_operations_do_not_write(self):
        for op in ('preflight', 'status', 'mirror_status', 'health_status'):
            self.control.handle(request(op))
        self.assertEqual({}, self.store.values)
        self.assertEqual([], self.backend.calls)

    def test_begin_duplicate_never_dispatches_twice_even_outside_window(self):
        self.begin(); self.policy.allowed = False; self.begin()
        self.assertEqual(['begin'], self.backend.calls)

    def test_new_begin_outside_window_writes_nothing(self):
        self.policy.allowed = False
        with self.assertRaises(Refused): self.begin()
        self.assertEqual({}, self.store.values)

    def test_lost_begin_ack_preserves_intent_and_never_restarts(self):
        self.backend.fail = 'begin'
        self.assertTrue(self.begin()['operator_required'])
        self.backend.fail = None; self.backend.missing = True
        result = self.begin()
        self.assertTrue(result['operator_required'])
        self.assertEqual(['begin'], self.backend.calls)
        self.assertNotIn('PRIVATE', str(result))

    def test_mirror_health_dispatch_once_intents_precede_dispatch(self):
        self.begin()
        mirror = {'release': RELEASE, 'backup_label': '20261026-001723F',
                  'publication_manifest_sha256': 'e' * 64}
        for op, payload in (('mirror', mirror), ('health', {})):
            self.control.handle(request(op, payload)); self.control.handle(request(op, payload))
        self.assertEqual(['begin', 'mirror', 'health'], self.backend.calls)

    def test_lost_mirror_and_health_ack_cannot_reissue(self):
        self.begin()
        for op, payload in (('mirror', {'release': RELEASE, 'backup_label': '20261026-001723F',
                                       'publication_manifest_sha256': 'e' * 64}), ('health', {})):
            self.backend.fail = op
            self.assertTrue(self.control.handle(request(op, payload))['operator_required'])
            self.backend.fail = None
            self.control.handle(request(op, payload))
        self.assertEqual(['begin', 'mirror', 'health'], self.backend.calls)

    def test_peer_messages_identical_repeat_no_mutation_changed_bytes_refused(self):
        self.begin()
        variants = [('quiesced', dict(IDENTITY, quiesced_at=90, services_stopped=True)),
                    ('capture', dict(IDENTITY, capture=capture())),
                    ('restored', dict(IDENTITY, services_restored=True, capture_sha256='f' * 64))]
        for op, payload in variants:
            self.control.handle(request(op, payload)); original = copy.deepcopy(self.store.values)
            self.control.handle(request(op, payload)); self.assertEqual(original, self.store.values)
            changed = copy.deepcopy(payload)
            if op == 'quiesced': changed['quiesced_at'] = 91
            elif op == 'capture': changed['capture']['object_count'] = 29
            else: changed['capture_sha256'] = '1' * 64
            with self.assertRaises(Refused): self.control.handle(request(op, changed))

    def test_abort_repeat_signals_original_worker_only_once(self):
        self.begin(); self.policy.allowed = False
        self.control.handle(request('abort', IDENTITY)); self.control.handle(request('abort', IDENTITY))
        self.assertEqual(['begin', 'abort'], self.backend.calls)

    def test_wrong_policy_transaction_release_and_peer_identity_refused(self):
        self.begin()
        for field, value in (('policy_sha256', '0' * 64), ('transaction', '0' * 32)):
            changed = request('status'); changed[field] = value
            with self.assertRaises(Refused): self.control.handle(changed)
        for field, value in (('release', '0' * 40), ('web_invocation', '0' * 32),
                             ('web_boot', '00000000-0000-0000-0000-000000000000')):
            with self.assertRaises(Refused):
                self.control.handle(request('abort', dict(IDENTITY, **{field: value})))

    def test_no_caller_paths_units_env_or_extra_fields(self):
        for field in ('path', 'unit', 'command', 'env'):
            with self.assertRaises(Refused): validate_request(request('status', {field: '/arbitrary'}))
        for value in (None, [], 'status', 7):
            with self.assertRaises(Refused): validate_request(value)
        for op in ('../../run', 'restart', 'expire'):
            with self.assertRaises(Refused): validate_request(request(op))

    def test_malformed_nested_values_and_timestamps_refused(self):
        for value in (True, float('nan'), float('inf'), -1, 'now'):
            with self.assertRaises(Refused):
                validate_request(request('quiesced', dict(IDENTITY, services_stopped=True, quiesced_at=value)))
        for field, value in (('manifest_verified', False), ('object_count', True),
                             ('manifest_sha256', '../../key'), ('authority', 'other'),
                             ('captured_epoch', 99)):
            body = capture(); body[field] = value
            with self.assertRaises(Refused): validate_request(request('capture', dict(IDENTITY, capture=body)))

    def test_differential_mirror_label_and_changed_dispatch_refused(self):
        self.begin()
        body = {'release': RELEASE, 'backup_label': '20261026-001723F',
                'publication_manifest_sha256': 'e' * 64}
        self.control.handle(request('mirror', body))
        with self.assertRaises(Refused): self.control.handle(request('mirror', dict(body, publication_manifest_sha256='f' * 64)))
        with self.assertRaises(Refused): validate_request(request('mirror', dict(body, backup_label='20261026-001723F_20261027-000000D')))

    def test_messages_before_begin_are_refused(self):
        with self.assertRaises(Refused): self.control.handle(request('abort', IDENTITY))
        with self.assertRaises(Refused): self.control.handle(request('health'))

    def test_complete_dispatch_once_and_lost_ack_never_repeats(self):
        self.begin()
        body = {'release': RELEASE, 'backup_label': '20261026-001723F', 'publication_manifest_sha256': 'e' * 64}
        self.backend.fail = 'complete'
        self.assertTrue(self.control.handle(request('complete', body))['operator_required'])
        self.backend.fail = None
        self.control.handle(request('complete', body))
        self.assertEqual(['begin', 'complete'], self.backend.calls)
        with self.assertRaises(Refused):
            self.control.handle(request('complete', dict(body, publication_manifest_sha256='f' * 64)))


if __name__ == '__main__': unittest.main()
