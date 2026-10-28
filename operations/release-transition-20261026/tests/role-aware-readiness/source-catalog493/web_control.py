"""Injected, deterministic WEB paired-maintenance coordinator.

Caller serializes transaction access. Backend proves all host-specific transitions;
an ambiguous prior intent is reconciled, never replayed as a new operation.
"""
import datetime as dt
import re
import uuid
try:
    from .common import Refused, canonical, digest
    from .db_control import hex_string, validate_capture
except ImportError:
    from common import Refused, canonical, digest
    from db_control import hex_string, validate_capture


def need(value):
    if not value:
        raise Refused('web_protocol_refused')


def slot_name(date, policy_sha256):
    need(isinstance(date,str) and re.fullmatch('[0-9]{8}',date)
         and hex_string(policy_sha256,64))
    return date+'-'+policy_sha256


def validate_prestart_proof(value, transaction, policy_sha256, release, boot=None):
    keys={'schema','transaction','policy_sha256','release','boot_id','slot_claim_name',
          'original_status_sha256','no_dispatch','services_unchanged','services_restored',
          'restoration_performed','verified_at','verifier_invocation'}
    need(type(value) is dict and set(value)==keys
         and value['schema']=='careertrellis.paired-maintenance-prestart-abort/v1'
         and value['transaction']==transaction and value['policy_sha256']==policy_sha256
         and value['release']==release and hex_string(transaction,32)
         and hex_string(policy_sha256,64) and hex_string(value['original_status_sha256'],64)
         and hex_string(value['verifier_invocation'],32)
         and value['no_dispatch'] is True and value['services_unchanged'] is True
         and value['services_restored'] is True and value['restoration_performed'] is False)
    need(str(uuid.UUID(value['boot_id']))==value['boot_id'] and (boot is None or value['boot_id']==boot))
    claim=value['slot_claim_name']
    need(isinstance(claim,str) and re.fullmatch(r'[0-9]{8}(?:-'+policy_sha256+r')?\.json',claim))
    when=dt.datetime.fromisoformat(value['verified_at'].replace('Z','+00:00'))
    need(when.utcoffset()==dt.timedelta(0))
    return value


class WebControl:
    def __init__(self, policy, store, backend):
        need(hex_string(policy.data['application_release'], 40))
        need(type(policy.data['backup_wait_seconds']) is int and policy.data['backup_wait_seconds'] > 0)
        self.policy, self.store, self.backend = policy, store, backend
        self.fenced = False

    def _status(self, phase, result='running', restored=False):
        self.backend.status(phase, result=result, services_restored=restored)

    def _fence(self):
        if not self.fenced:
            self.backend.acquire_fence()
            self.fenced = True

    def _release(self):
        if self.fenced:
            self.backend.release_fence()
            self.fenced = False

    def _wait(self, state, timeout):
        value = self.backend.wait_peer({state}, timeout)
        need(type(value) is dict and value.get('state', value.get('phase')) == state)
        return value

    def _original(self, transaction):
        original = self.store.read('start-intent.json')
        need(original['transaction'] == transaction and original['policy_sha256'] == self.policy.digest)
        identity = original['identity']
        current = self.backend.identity()
        need(identity['release'] == self.policy.data['application_release'])
        need(all(current[key] == identity[key] for key in ('release', 'web_boot')))
        return identity

    def _operator(self, restored=False):
        try:
            self._status('operator_required', 'failed', restored)
        except Exception:
            pass
        return {'state': 'operator_required', 'services_restored': restored}

    def _recover(self, transaction):
        if not self.store.exists('start-intent.json'):
            try:
                proof=self.backend.prove_prestart_terminal(transaction)
                validate_prestart_proof(proof,transaction,self.policy.digest,self.policy.data['application_release'])
                return {'state':'prestart_aborted','services_restored':True,'restoration_performed':False}
            except Exception:
                return self._operator()
        try:
            identity = self._original(transaction)
            if self.store.exists('db-released.json'):
                # Online publication/mirror ambiguity never aborts an active DB
                # mirror or replays provider work. Writers were proved restored.
                self._release()
                self.backend.clear_writer_block()
                return self._operator(restored=True)
            self._fence()
            self._status('recovering')
            if not self.store.exists('db-begin-intent.json'):
                need(not self.store.exists('stop-intent.json'))
                self._release()
                self._status('blocked', 'failed')
                return {'state': 'skipped', 'services_restored': False}
            if not self.store.exists('abort-intent.json'):
                self.store.write_once('abort-intent.json', identity)
            # An identical abort request is idempotent at the DB endpoint.
            self.backend.peer('abort', identity)
            terminal = self.backend.wait_peer({'abort_ready', 'released', 'aborted'}, self.policy.data['backup_wait_seconds'])
            need(type(terminal) is dict and terminal.get('state') in {'abort_ready', 'released', 'aborted'}
                 and not terminal.get('operator_required'))
            if terminal['state'] in ('released', 'aborted'):
                self.backend.reconcile_terminal_peer(terminal)
                self._release()
                self.backend.clear_writer_block()
                if terminal['state'] == 'released':
                    return self._operator(restored=True)
                self.store.write_once('aborted-restored.json', {'transaction': transaction,
                    'policy_sha256': self.policy.digest, 'services_restored': True})
                self._status('aborted_restored', 'failed', True)
                return {'state': 'aborted_restored', 'services_restored': True}
            self.backend.allow_writer_restore()
            self.backend.restore_writers(recovery=True)
            payload = dict(identity, services_restored=True)
            if self.store.exists('writers-restored.json'):
                payload['capture_sha256'] = self.store.read('writers-restored.json')['capture_sha256']
            self.backend.peer('restored', payload)
            self._wait('aborted', 300)
            self._release()
            self.backend.clear_writer_block()
            self.store.write_once('aborted-restored.json', {'transaction': transaction,
                'policy_sha256': self.policy.digest, 'services_restored': True})
            self._status('aborted_restored', 'failed', True)
            return {'state': 'aborted_restored', 'services_restored': True}
        except Exception:
            return self._operator()

    def run(self, transaction, recovery_only=False):
        need(hex_string(transaction, 32) and type(recovery_only) is bool)
        if self.store.exists('complete.json'):
            value = self.store.read('complete.json')
            need(value['transaction'] == transaction and value['policy_sha256'] == self.policy.digest)
            return value
        if self.store.exists('aborted-restored.json'):
            value = self.store.read('aborted-restored.json')
            need(value['transaction'] == transaction and value['policy_sha256'] == self.policy.digest)
            return {'state': 'aborted_restored', 'services_restored': True}
        if self.store.exists('prestart-aborted.json'):
            return self._recover(transaction)
        if recovery_only or self.store.exists('start-intent.json'):
            return self._recover(transaction)
        try:
            self.policy.require_new_start(self.backend.now())
            preflight = self.backend.preflight()
            need(preflight.get('ready') is True)
            if not self.policy.due(preflight['last_capture_started_at'], self.backend.now()):
                self._status('fresh', 'skipped')
                return {'state': 'skipped', 'services_restored': False}
            identity = self.backend.identity()
            need(type(identity) is dict and set(identity) == {'release', 'web_boot', 'web_invocation'})
            need(identity['release'] == self.policy.data['application_release'])
            self.store.write_once('start-intent.json', {'transaction': transaction,
                'policy_sha256': self.policy.digest, 'identity': identity})
            self._fence()
            self.store.write_once('db-begin-intent.json', identity)
            self.backend.peer('begin', identity)
            self._wait('fenced', 120)
            # Recheck the approved start window immediately before writer stop.
            self.policy.require_new_start(self.backend.now())
            writers = self.backend.snapshot_writers()
            need(type(writers) is dict and bool(writers))
            need(all(isinstance(unit, str) and type(pid) is int and pid > 0 for unit, pid in writers.items()))
            self.store.write_once('stop-intent.json', {'writers': writers})
            self.backend.block_writers()
            self._status('stopping')
            quiesced = self.backend.stop_writers()
            self.backend.peer('quiesced', dict(identity, quiesced_at=quiesced, services_stopped=True))
            pairing = self._wait('pair_ready', self.policy.data['backup_wait_seconds'])
            capture = self.backend.capture(pairing['pair'])
            validate_capture(capture)
            need(capture['release'] == identity['release'])
            self.store.write_once('capture-result.json', capture)
            self.backend.peer('capture', dict(identity, capture=capture))
            self._wait('sealed', 120)
            self.backend.allow_writer_restore()
            self.backend.restore_writers()
            self.store.write_once('writers-restored.json', {'services_restored': True,
                'capture_sha256': digest(capture)})
            self.backend.peer('restored', dict(identity, services_restored=True, capture_sha256=digest(capture)))
            self._wait('released', 120)
            self.store.write_once('db-released.json', {'services_restored': True})
            self._release()
            self.backend.clear_writer_block()
            self.store.write_once('publish-intent.json', {'capture_sha256': digest(capture)})
            publication = self.backend.publish(capture)
            need(type(publication) is dict and publication.get('result') == 'onedrive_snapshot_created')
            need(all(publication.get(key) == capture[key] for key in
                ('release', 'backup_label', 'snapshot_id', 'manifest_sha256', 'authority')))
            self.store.write_once('publication-result.json', publication)
            self.backend.peer('mirror', {'release': identity['release'], 'backup_label': capture['backup_label'],
                'publication_manifest_sha256': capture['manifest_sha256']})
            self.backend.wait_mirror()
            self.backend.peer('health', {})
            self.backend.wait_health()
            self.backend.normal_reports()
            complete = {'state': 'complete', 'transaction': transaction,
                'policy_sha256': self.policy.digest, 'services_restored': True,
                'backup_label': capture['backup_label'], 'manifest_sha256': capture['manifest_sha256']}
            self.store.write_once('complete.json', complete)
            self._status('complete', 'ok', True)
            return complete
        except Exception:
            return self._recover(transaction)
