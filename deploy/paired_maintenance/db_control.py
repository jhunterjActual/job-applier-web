"""Deterministic DB control protocol; all host operations are injected.

The entry point supplies an existing transaction Journal for read-only requests
and holds a native exclusive per-transaction control lock around each handle.
Dispatch intents survive lost acknowledgements and never authorize redispatch.
"""
import math
import re
import uuid

try:
    from .common import Refused, canonical
except ImportError:
    from common import Refused, canonical


SCHEMA = 'careertrellis.paired-maintenance-request/v1'
READ_ONLY = {'preflight', 'status', 'mirror_status', 'health_status'}
RETENTION = {'bundle_' + stage for stage in ('hydrate', 'publish', 'restore', 'prepare', 'retire', 'status')}
RETENTION_PREFLIGHT = 'retention_guard_snapshot'
RETENTION_CAPACITY = 'retention_capacity_snapshot'
RETENTION_SCHEDULE = 'retention_schedule'
IDENTITY = {'release', 'web_boot', 'web_invocation'}
FIELDS = {
    'begin': IDENTITY,
    'quiesced': IDENTITY | {'quiesced_at', 'services_stopped'},
    'capture': IDENTITY | {'capture'},
    'restored': IDENTITY | {'services_restored'},
    'abort': IDENTITY,
    'mirror': {'release', 'backup_label', 'publication_manifest_sha256'},
    'complete': {'release', 'backup_label', 'publication_manifest_sha256'},
    **{name: set() for name in READ_ONLY},
    'health': set(),
}
CAPTURE_FIELDS = {'result', 'manifest_verified', 'release', 'backup_label',
    'snapshot_id', 'authority', 'object_count', 'ciphertext_bytes',
    'manifest_sha256', 'capture_started_epoch', 'captured_epoch'}


def need(value):
    if not value:
        raise Refused('db_protocol_refused')


def hex_string(value, length):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None


def full_label(value):
    return isinstance(value, str) and re.fullmatch('[0-9]{8}-[0-9]{6}F', value) is not None


def number(value):
    return type(value) in (int, float) and 0 <= value <= 10**12 and math.isfinite(value)


def validate_capture(value):
    need(type(value) is dict and set(value) == CAPTURE_FIELDS)
    need(value['result'] == 'pass' and value['manifest_verified'] is True)
    need(hex_string(value['release'], 40) and full_label(value['backup_label']))
    need(value['snapshot_id'] == value['release'] + '-' + value['backup_label'])
    need(value['authority'] == 'postgres-r2-authoritative')
    need(hex_string(value['manifest_sha256'], 64))
    need(type(value['object_count']) is int and 0 <= value['object_count'] <= 100000)
    need(type(value['ciphertext_bytes']) is int and 0 <= value['ciphertext_bytes'] <= 100000 * (10 * 1024**2 + 64))
    need(number(value['capture_started_epoch']) and number(value['captured_epoch']))
    need(value['capture_started_epoch'] <= value['captured_epoch'])


def validate_request(request):
    need(type(request) is dict and set(request) == {'schema', 'operation', 'transaction', 'policy_sha256', 'payload'})
    need(request['schema'] == SCHEMA)
    need(hex_string(request['transaction'], 32) and hex_string(request['policy_sha256'], 64))
    operation = request['operation']
    if operation == RETENTION_SCHEDULE:
        payload=request['payload']
        need(type(payload) is dict and set(payload)=={'witness'}
             and type(payload['witness']) is dict and len(canonical(request))<=16384)
        return request
    if operation in (RETENTION_PREFLIGHT,RETENTION_CAPACITY):
        payload = request['payload']
        need(type(payload) is dict and set(payload) == {'stage','retention_policy_sha256'}
             and payload['stage'] in ('hydrate','publish','restore','prepare','retire')
             and hex_string(payload['retention_policy_sha256'],64)
             and len(canonical(request)) <= 16384)
        return request
    if type(operation) is str and operation in RETENTION:
        return validate_retention_envelope(request)
    need(isinstance(operation, str) and operation in FIELDS)
    payload = request['payload']
    need(type(payload) is dict)
    expected = FIELDS[operation]
    if operation == 'restored' and 'capture_sha256' in payload:
        expected = expected | {'capture_sha256'}
        need(hex_string(payload['capture_sha256'], 64))
    need(set(payload) == expected)
    if 'release' in payload:
        need(hex_string(payload['release'], 40))
    if 'web_boot' in payload:
        need(isinstance(payload['web_boot'], str) and len(payload['web_boot']) == 36)
        try:
            need(str(uuid.UUID(payload['web_boot'])) == payload['web_boot'])
        except (ValueError, AttributeError):
            raise Refused('db_protocol_refused') from None
        need(hex_string(payload['web_invocation'], 32))
    if operation == 'quiesced':
        need(payload['services_stopped'] is True and number(payload['quiesced_at']))
    elif operation == 'capture':
        validate_capture(payload['capture'])
        need(payload['capture']['release'] == payload['release'])
    elif operation == 'restored':
        need(payload['services_restored'] is True)
    elif operation in ('mirror', 'complete'):
        need(full_label(payload['backup_label']) and hex_string(payload['publication_manifest_sha256'], 64))
    need(len(canonical(request)) <= 16384)
    return request


def validate_retention_envelope(request):
    """Bounded transport validation; authenticated endpoint validates semantics.

    This gate imports no unverified retention package. Existing paired schema,
    key, forced command, host binding and outer policy binding still apply.
    """
    payload = request['payload']
    need(type(payload) is dict and set(payload) == {
        'retention_policy_sha256', 'capture_transaction', 'payload'})
    need(hex_string(payload['retention_policy_sha256'], 64)
         and payload['capture_transaction'] == request['transaction'])
    stage = request['operation'][7:]
    inner = payload['payload']
    keys = {'stage', 'receipt_facts'}
    if stage != 'status': keys.add('web_history')
    if stage == 'restore': keys.add('web_preflight')
    need(type(inner) is dict and set(inner) == keys
         and inner['stage'] in ('hydrate', 'publish', 'restore', 'prepare', 'retire')
         and (stage == 'status' or stage == inner['stage']))
    facts = inner['receipt_facts']
    need(type(facts) is dict and set(facts) ==
         (set() if stage in ('hydrate', 'status') else {'predecessor_receipt_sha256'}))
    need(all(hex_string(value, 64) for value in facts.values()))
    if stage != 'status': need(type(inner['web_history']) is dict)
    if stage == 'restore': need(type(inner['web_preflight']) is dict)
    need(len(canonical(request)) <= 16384)
    return request


class DBControl:
    def __init__(self, policy, store, backend):
        self.policy, self.store, self.backend = policy, store, backend

    def _same_or_write(self, name, value):
        if self.store.exists(name):
            need(canonical(self.store.read(name)) == canonical(value))
            return False
        self.store.write_once(name, value)
        return True

    def _status(self, method, transaction):
        try:
            value = method(transaction)
            need(type(value) is dict and len(canonical(value)) <= 16384)
            return value
        except Exception:
            return {'state': 'unknown', 'operator_required': True}

    def handle(self, request):
        request = validate_request(request)
        need(request['policy_sha256'] == self.policy.digest)
        operation, transaction, payload = request['operation'], request['transaction'], request['payload']
        if 'release' in payload:
            need(payload['release'] == self.policy.data['application_release'])
        intent = self.store.read('begin-intent.json') if self.store.exists('begin-intent.json') else None
        if intent is not None:
            need(intent['transaction'] == transaction and intent['policy_sha256'] == request['policy_sha256'])
        if operation == 'preflight':
            try:
                value = self.backend.preflight()
                need(type(value) is dict and len(canonical(value)) <= 16384)
                return value
            except Exception:
                return {'state': 'unknown', 'operator_required': True}
        if operation in ('status', 'mirror_status', 'health_status'):
            method = {'status': self.backend.worker_status, 'mirror_status': self.backend.mirror_status,
                      'health_status': self.backend.health_status}[operation]
            return self._status(method, transaction)
        if operation == 'begin':
            if intent is not None:
                need(canonical(intent) == canonical(request))
                return self._status(self.backend.worker_status, transaction)
            self.policy.require_new_start(self.backend.now())
            self.store.write_once('begin-intent.json', request)
            try:
                self.backend.start_db_worker(transaction)
            except Exception:
                return {'state': 'unknown', 'operator_required': True}
            return self._status(self.backend.worker_status, transaction)
        need(intent is not None)
        if 'web_boot' in payload:
            need(all(payload[key] == intent['payload'][key] for key in IDENTITY))
        if operation in ('quiesced', 'capture', 'restored', 'abort'):
            self._same_or_write(operation + '.json', request)
            if operation == 'abort':
                # Abort is a durable request consumed by the existing worker;
                # repeated requests only inspect that worker's original state.
                if not self.store.exists('abort-dispatch-intent.json'):
                    self.store.write_once('abort-dispatch-intent.json', request)
                    try:
                        self.backend.request_abort(transaction)
                    except Exception:
                        return {'state': 'unknown', 'operator_required': True}
            return self._status(self.backend.worker_status, transaction)
        if operation in ('mirror', 'health', 'complete'):
            name = operation + '-intent.json'
            fresh = self._same_or_write(name, request)
            if fresh:
                try:
                    if operation == 'mirror':
                        self.backend.start_mirror(transaction, payload)
                    elif operation == 'complete':
                        self.backend.complete(transaction, payload)
                    else:
                        self.backend.start_health(transaction)
                except Exception:
                    return {'state': 'unknown', 'operator_required': True}
            method = (self.backend.mirror_status if operation == 'mirror' else
                      self.backend.worker_status if operation == 'complete' else self.backend.health_status)
            return self._status(method, transaction)
        raise Refused('db_protocol_refused')
