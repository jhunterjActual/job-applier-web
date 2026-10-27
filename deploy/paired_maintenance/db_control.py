"""Deterministic DB control protocol; all host operations are injected.

The entry point supplies an existing transaction Journal for read-only requests
and holds a native exclusive per-transaction control lock around each handle.
Dispatch intents survive lost acknowledgements and never authorize redispatch.
"""
import math
import datetime as dt
import hashlib
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
RETENTION_ACTIVATION = 'retention_activation_evidence'
RETENTION_BOOTSTRAP = 'retention_bootstrap_evidence'
RETENTION_STATE = 'retention_activation_state'
RETENTION_PREPARE = 'retention_activation_prepare'
RETENTION_COMMIT = 'retention_activation_commit'
RETENTION_ACTOR_STATE = 'retention_activation_actor_state'
RETENTION_ACTOR_TERMINAL_STATUS = 'retention_activation_actor_terminal_status'
RETENTION_MATERIAL = 'retention_activation_material'
RETENTION_FINALIZE = 'retention_activation_finalize'
RETENTION_BRIDGE = frozenset((RETENTION_PREPARE,RETENTION_COMMIT,RETENTION_ACTOR_STATE,RETENTION_MATERIAL,RETENTION_ACTOR_TERMINAL_STATUS))
RETENTION_PRE_GROWTH = 'retire_existing_before_growth'
RETENTION_PRE_GROWTH_STATUS = RETENTION_PRE_GROWTH+'_status'
RETENTION_PRE_GROWTH_VERIFY = RETENTION_PRE_GROWTH+'_verify'
BINDINGS = frozenset(('standalone_manifest_sha256','paired_manifest_sha256',
    'paired_policy_sha256','standalone_policy_sha256'))
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


def nonzero(value,length=64):return hex_string(value,length) and value!='0'*length


def exact(value,fields):need(type(value) is dict and set(value)==set(fields))


def digests(value,fields):
    exact(value,fields);need(all(nonzero(item) for item in value.values()))


def utc(value):
    need(type(value) is str and 0<len(value)<=40)
    try:result=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise Refused('db_protocol_refused') from None
    need(result.utcoffset()==dt.timedelta(0));return result


def validate_activation_bridge(request):
    """Closed transport shapes only; fixed native actor proves local authority."""
    need(nonzero(request['transaction'],32) and nonzero(request['policy_sha256']))
    payload=request['payload'];operation=request['operation'];tx=request['transaction']
    if operation in (RETENTION_PREPARE,RETENTION_COMMIT):
        fields={'baseline_bindings','installation_receipts','intent_sha256','web_baseline_sha256'}
        if operation==RETENTION_COMMIT:fields.add('commit_authority_sha256')
        exact(payload,fields);digests(payload['baseline_bindings'],BINDINGS)
        digests(payload['installation_receipts'],('web','db'))
        need(payload['baseline_bindings']['paired_policy_sha256']==request['policy_sha256'])
        need(all(nonzero(payload[key]) for key in fields-{'baseline_bindings','installation_receipts'}))
    elif operation in (RETENTION_ACTOR_STATE,RETENTION_ACTOR_TERMINAL_STATUS):
        digests(payload,('retention_policy_sha256','intent_sha256'))
    else:
        need(operation==RETENTION_MATERIAL)
        exact(payload,('transaction','retention_policy_sha256','intent_sha256','documents'))
        need(payload['transaction']==tx and nonzero(payload['retention_policy_sha256']) and nonzero(payload['intent_sha256']))
        docs=payload['documents'];exact(docs,('commit-authority.json','prepared-web.json','prepare-request-web.json'))
        authority=docs['commit-authority.json'];receipt=docs['prepared-web.json'];prepared=docs['prepare-request-web.json']
        exact(authority,('schema','transaction','intent_sha256','prepare_deadline','dispatcher_owner',
            'prepared','selector','selector_sha256'))
        need(authority['schema']=='careertrellis.retention-activation-commit-authority/v1'
            and authority['transaction']==tx and authority['intent_sha256']==payload['intent_sha256'])
        deadline=utc(authority['prepare_deadline']);digests(authority['prepared'],('web','db'))
        owner=authority['dispatcher_owner'];exact(owner,('pid','start_ticks','boot_id'))
        need(type(owner['pid']) is int and 1<owner['pid']<2**31
            and type(owner['start_ticks']) is int and 0<owner['start_ticks']<2**64
            and type(owner['boot_id']) is str)
        try:need(str(uuid.UUID(owner['boot_id']))==owner['boot_id'] and uuid.UUID(owner['boot_id']).int!=0)
        except ValueError:raise Refused('db_protocol_refused') from None
        selector=authority['selector']
        exact(selector,('schema','transaction','activation_intent_sha256','enabled','activated_at','valid_until'))
        need(selector['schema']=='careertrellis.onedrive-retention-activation/v1'
            and selector['transaction']==tx and selector['activation_intent_sha256']==payload['intent_sha256']
            and selector['enabled'] is True and nonzero(authority['selector_sha256'])
            and authority['selector_sha256']==hashlib.sha256(canonical(selector)).hexdigest())
        activated=utc(selector['activated_at']);need(activated<deadline and activated<utc(selector['valid_until']))
        exact(prepared,('transaction','role','phase','intent_sha256','prepare_deadline','baseline_bindings'))
        digests(prepared['baseline_bindings'],BINDINGS)
        need(prepared['transaction']==tx and prepared['role']=='web' and prepared['phase']=='prepare'
            and prepared['intent_sha256']==payload['intent_sha256'] and prepared['prepare_deadline']==authority['prepare_deadline']
            and prepared['baseline_bindings']['paired_policy_sha256']==request['policy_sha256']
            and prepared['baseline_bindings']['standalone_policy_sha256']==payload['retention_policy_sha256'])
        need(receipt=={'transaction':tx,'role':'web','phase':'prepare','intent_sha256':payload['intent_sha256'],
            'request_sha256':hashlib.sha256(canonical(prepared)).hexdigest(),'result':'prepared'}
            and authority['prepared']['web']==hashlib.sha256(canonical(receipt)).hexdigest())
    need(len(canonical(request))<=16384)
    return request


def validate_activation_finalization(request):
    """Closed consistent wire claims only; native receiver proves authority."""
    need(nonzero(request['transaction'],32) and nonzero(request['policy_sha256']) and len(canonical(request))<=16384)
    value=request['payload'];tx=request['transaction']
    exact(value,('schema','transaction','retention_policy_sha256','intent','selector','finalized','documents'))
    need(value['schema']=='careertrellis.retention-activation-finalization-material/v1' and value['transaction']==tx)
    intent=value['intent'];selector=value['selector'];finalized=value['finalized'];docs=value['documents']
    exact(intent,('schema','transaction','artifact_revision','application_release','baseline_bindings',
        'installation_receipts','replacement_bundles','reviews','prepared_at','valid_until'))
    digests(intent['baseline_bindings'],BINDINGS);bindings=intent['baseline_bindings']
    need(intent['schema']=='careertrellis.onedrive-retention-activation-intent/v1' and intent['transaction']==tx
        and nonzero(intent['artifact_revision'],40) and nonzero(intent['application_release'],40)
        and bindings['paired_policy_sha256']==request['policy_sha256']
        and bindings['standalone_policy_sha256']==value['retention_policy_sha256'])
    digests(intent['installation_receipts'],('web','db'))
    digests(intent['reviews'],('source_review','native_controls','paired_transition','rollback','schedule'))
    prepared=utc(intent['prepared_at']);expiry=utc(intent['valid_until']);need(prepared<expiry)
    rows=intent['replacement_bundles'];need(type(rows) is list and 2<=len(rows)<=32)
    seen={key:set() for key in ('archive_sha256','native_receipt_sha256','terminal_proof_sha256')};order=[]
    for row in rows:
        exact(row,('archive_sha256','native_receipt_sha256','terminal_proof_sha256','backup_label','accepted_at'))
        for key in seen:
            need(nonzero(row[key]) and row[key] not in seen[key]);seen[key].add(row[key])
        need(full_label(row['backup_label']) and utc(row['accepted_at'])<=prepared)
        order.append((utc(row['accepted_at']),row['archive_sha256']))
    need(order==sorted(order))
    def sha(document):return hashlib.sha256(canonical(document)).hexdigest()
    intent_sha=sha(intent);selector_sha=sha(selector)
    exact(selector,('schema','transaction','activation_intent_sha256','enabled','activated_at','valid_until'))
    need(selector['schema']=='careertrellis.onedrive-retention-activation/v1' and selector['transaction']==tx
        and selector['activation_intent_sha256']==intent_sha and selector['enabled'] is True
        and selector['valid_until']==intent['valid_until'] and prepared<=utc(selector['activated_at'])<expiry)
    exact(finalized,('schema','transaction','intent_sha256','selector_sha256','baseline_bindings','baseline_sha256',
        'installation_receipts','committed_receipts','controller_terminal_sha256','finalized_at','valid_until'))
    need(finalized['schema']=='careertrellis.retention-activation-finalized/v1' and finalized['transaction']==tx
        and finalized['intent_sha256']==intent_sha and finalized['selector_sha256']==selector_sha
        and finalized['baseline_bindings']==bindings and finalized['installation_receipts']==intent['installation_receipts']
        and finalized['valid_until']==intent['valid_until'])
    for key in ('baseline_sha256','committed_receipts'):digests(finalized[key],('web','db'))
    names={'controller-terminal.json'}|{stem+role+'.json' for role in ('web','db')
        for stem in ('committed-','commit-ack-','reconciliation-')}
    exact(docs,names);terminal=docs['controller-terminal.json']
    exact(terminal,('schema','transaction','intent_sha256','selector_sha256','baseline_bindings','baseline_sha256',
        'installation_receipts','committed_receipts','commit_acknowledgements','reconciliations','owner',
        'source_manifest_sha256','result','unknown_outcome','completed_at','prepare_deadline'))
    need(terminal['schema']=='careertrellis.retention-activation-controller-terminal/v1'
        and finalized['controller_terminal_sha256']==sha(terminal)
        and terminal['result']=='both_commits_verified' and terminal['unknown_outcome'] is False
        and terminal['source_manifest_sha256']==bindings['standalone_manifest_sha256'])
    for key in ('transaction','intent_sha256','selector_sha256','baseline_bindings','baseline_sha256',
                'installation_receipts','committed_receipts'):need(terminal[key]==finalized[key])
    for key in ('commit_acknowledgements','reconciliations'):digests(terminal[key],('web','db'))
    owner=terminal['owner'];exact(owner,('pid','start_ticks','process_group','boot_id'))
    need(type(owner['pid']) is int and 1<owner['pid']<2**31
        and type(owner['start_ticks']) is int and 0<owner['start_ticks']<2**64
        and type(owner['process_group']) is int and 0<owner['process_group']<2**31)
    try:need(str(uuid.UUID(owner['boot_id']))==owner['boot_id'] and uuid.UUID(owner['boot_id']).int!=0)
    except (ValueError,AttributeError,TypeError):raise Refused('db_protocol_refused') from None
    completed=utc(terminal['completed_at']);deadline=utc(terminal['prepare_deadline'])
    need(utc(selector['activated_at'])<=completed<=utc(finalized['finalized_at'])<deadline
        and deadline<=prepared+dt.timedelta(minutes=15) and deadline<=expiry)
    for role in ('web','db'):
        commit=docs['committed-'+role+'.json'];ack=docs['commit-ack-'+role+'.json'];state=docs['reconciliation-'+role+'.json']
        exact(commit,('transaction','role','phase','intent_sha256','selector_sha256','result','committed_at'))
        need(commit['transaction']==tx and commit['role']==role and commit['phase']=='commit'
            and commit['intent_sha256']==intent_sha and commit['selector_sha256']==selector_sha
            and commit['result']=='committed' and utc(selector['activated_at'])<=utc(commit['committed_at'])<=completed)
        exact(ack,('schema','transaction','role','intent_sha256','selector_sha256','request_sha256',
            'receipt_sha256','receipt','received_at','provenance'))
        exact(state,('schema','transaction','role','intent_sha256','selector_sha256','observed_at',
            'committed_receipt_sha256','observation_sha256','provenance'))
        provenance={'kind':'paired-client' if role=='db' else 'native-local','owner':owner,
            'source_manifest_sha256':bindings['standalone_manifest_sha256']}
        need(ack['schema']=='careertrellis.retention-activation-commit-ack/v1'
            and state['schema']=='careertrellis.retention-activation-reconciliation/v1'
            and ack['receipt']==commit and ack['receipt_sha256']==state['committed_receipt_sha256']==sha(commit)
            and nonzero(ack['request_sha256']) and nonzero(state['observation_sha256']))
        for document in (ack,state):
            need(document['transaction']==tx and document['role']==role and document['intent_sha256']==intent_sha
                and document['selector_sha256']==selector_sha and document['provenance']==provenance)
        need(utc(commit['committed_at'])<=utc(ack['received_at'])<=utc(state['observed_at'])<=completed
            and terminal['committed_receipts'][role]==sha(commit)
            and terminal['commit_acknowledgements'][role]==sha(ack) and terminal['reconciliations'][role]==sha(state))
    return request


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
    if operation==RETENTION_FINALIZE:return validate_activation_finalization(request)
    if operation in (RETENTION_PRE_GROWTH,RETENTION_PRE_GROWTH_STATUS,RETENTION_PRE_GROWTH_VERIFY):
        need(nonzero(request['transaction'],32) and nonzero(request['policy_sha256']))
        payload=request['payload']
        if operation in (RETENTION_PRE_GROWTH_STATUS,RETENTION_PRE_GROWTH_VERIFY):
            digests(payload,('retention_policy_sha256',))
        else:
            exact(payload,('request','web_history'));value=payload['request']
            exact(value,('schema','operation','transaction','retention_policy_sha256','activation_sha256','web_history_sha256','allocation'))
            need(value['schema']=='careertrellis.pre-growth-retirement-only-request/v1'
                and value['operation']==RETENTION_PRE_GROWTH and value['transaction']==request['transaction'])
            need(all(nonzero(value[key]) for key in ('retention_policy_sha256','activation_sha256','web_history_sha256')))
            exact(value['allocation'],('source_bytes','candidate_bytes','namespace_new_bytes'))
            need(all(type(size) is int and 0<=size<=512*1024**2 for size in value['allocation'].values())
                and value['allocation']['candidate_bytes']>0
                and value['allocation']['namespace_new_bytes']>=value['allocation']['candidate_bytes'])
            history=payload['web_history']
            exact(history,('schema','role','transaction','stage','observed_at','baseline_bindings','roots','unresolved',
                'complete','growth_authorized','deletion_authorized','authentication','owner','reservation'))
            digests(history['baseline_bindings'],BINDINGS)
            need(history['role']=='web' and history['stage']==RETENTION_PRE_GROWTH
                and history['transaction']==request['transaction'] and history['complete'] is True
                and history['unresolved']==[] and history['growth_authorized'] is False and history['deletion_authorized'] is False
                and history['baseline_bindings']['paired_policy_sha256']==request['policy_sha256']
                and history['baseline_bindings']['standalone_policy_sha256']==value['retention_policy_sha256']
                and value['web_history_sha256']==hashlib.sha256(canonical(history)).hexdigest())
        need(len(canonical(request))<=16384);return request
    if type(operation) is str and operation in RETENTION_BRIDGE:
        return validate_activation_bridge(request)
    if operation == RETENTION_STATE:
        payload=request['payload']
        need(type(payload) is dict and set(payload)=={'retention_policy_sha256','intent_sha256'}
             and all(hex_string(value,64) and value!='0'*64 for value in payload.values())
             and len(canonical(request))<=16384)
        return request
    if operation == RETENTION_BOOTSTRAP:
        payload=request['payload']
        need(type(payload) is dict and set(payload)=={'retention_policy_sha256'}
             and hex_string(payload['retention_policy_sha256'],64)
             and payload['retention_policy_sha256']!='0'*64 and len(canonical(request))<=16384)
        return request
    if operation == RETENTION_ACTIVATION:
        payload=request['payload']
        need(type(payload) is dict and set(payload)=={'retention_policy_sha256','activation_sha256'}
             and all(hex_string(value,64) and value!='0'*64 for value in payload.values())
             and len(canonical(request))<=16384)
        return request
    if operation == RETENTION_SCHEDULE:
        payload=request['payload']
        need(type(payload) is dict and set(payload)=={'witness'}
             and type(payload['witness']) is dict and len(canonical(request))<=16384)
        return request
    if operation in (RETENTION_PREFLIGHT,RETENTION_CAPACITY):
        payload = request['payload']
        need(type(payload) is dict and set(payload) == {'stage','retention_policy_sha256'}
             and payload['stage'] in (('hydrate','publish','restore','prepare','retire',RETENTION_PRE_GROWTH)
                 if operation==RETENTION_CAPACITY else ('hydrate','publish','restore','prepare','retire'))
             and hex_string(payload['retention_policy_sha256'],64)
             and len(canonical(request)) <= 16384)
        if payload['stage']==RETENTION_PRE_GROWTH:
            need(nonzero(request['transaction'],32) and nonzero(request['policy_sha256'])
                and nonzero(payload['retention_policy_sha256']))
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
