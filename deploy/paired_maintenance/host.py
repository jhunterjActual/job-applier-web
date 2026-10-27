"""WEB native adapter. Durable intents precede every non-idempotent dispatch.

Recovery reconciles only the original transaction and restores writers after the
DB guardian proves its backup terminal; publication is never replayed here.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
import urllib.error
import uuid

from . import runtime, provider
from .client import Client, decode
from .common import Journal, Refused, canonical, digest
from .db_control import hex_string, validate_capture
from .web_control import (WebControl, slot_name, validate_prestart_proof,
                          validate_backup_strategy, validate_bundle_acceptance)

ROOT = Path('/var/lib/careertrellis/paired-maintenance')
ROLE_ROOT = ROOT / 'web'
HEALTH = 'careertrellis-generated-object-backup-health.service'
REPORTER = 'careertrellis-backup-reporter@25b88cde302be38ed3d4c86742f406451f058d97.service'
HEALTH_PINS = {
 '/usr/local/sbin/careertrellis-backup-health': 'e7b51cf999481554e3fc68c9cc4159a81dd19cb38464de13b1c9304db09361af',
 '/etc/systemd/system/' + HEALTH: 'beaf0a520da2c4cfcb763ce3ea510236e2534822379aab77387a7a8aac9cb29e',
 '/etc/systemd/system/' + HEALTH + '.d/50-backup-reporting.conf': '1d0603031725579ce8ef1046dbf39c6a90fc054993b9e9de3b184c7d224bc146',
 '/etc/systemd/system/' + HEALTH + '.d/60-completed-alert-report.conf': 'f489e90a40deccd071db322ba0ea9a264ef7c763025c42e252989515660ce8f0'}
DROPINS = ' '.join('/etc/systemd/system/' + HEALTH + '.d/' + name for name in
                  ('50-backup-reporting.conf', '60-completed-alert-report.conf'))
RECOVERY_RECHECK_SECONDS = 60
BUNDLE_OUTPUT_LIMIT = 262144
BUNDLE_ONLINE_SECONDS = 28*3600


def need(value, code='web_host_refused'):
    if not value:
        raise Refused(code)


def native_bundle_acceptance(capture, strategy, transaction, paired_policy_sha256, documents):
    """Normalize protected child receipts; supplied dictionaries alone are claims.

    Only run_native_bundle supplies these records after the exact installed
    child exits successfully. No legacy mirror receipt is manufactured here.
    """
    validate_capture(capture);validate_backup_strategy(strategy)
    need(strategy['mode']=='native-bundle-v2' and type(documents) is dict and set(documents)=={
        'coordinator-intent.json','hydrate-complete.json','restore-complete.json',
        'retire-complete.json','coordinator-terminal.json'})
    original=documents['coordinator-intent.json']
    need(original.get('schema')=='careertrellis.retention-coordinator/v1'
         and original.get('transaction')==transaction
         and original.get('retention_policy_sha256')==strategy['retention_policy_sha256']
         and original.get('expected_backup_label')==capture['backup_label'])
    hydrate_result=documents['hydrate-complete.json'];restore=documents['restore-complete.json']
    final=documents['retire-complete.json'];terminal=documents['coordinator-terminal.json']
    for stage,result in (('hydrate',hydrate_result),('restore',restore),('retire',final)):
        need(type(result) is dict and set(result)=={'schema','capture_transaction','retention_policy_sha256',
            'stage','state','receipt_sha256','facts'}
             and result['schema']=='careertrellis.onedrive-stage-status/v1'
             and result['capture_transaction']==transaction and result['stage']==stage
             and result['retention_policy_sha256']==strategy['retention_policy_sha256']
             and result['state']=='complete' and hex_string(result['receipt_sha256'],64)
             and type(result['facts']) is dict)
    base={'archive_sha256','archive_bytes','generation_sha256','backup_label'}
    need(set(hydrate_result['facts'])==base and set(restore['facts'])==base|{'native_restore_accepted'}
         and type(restore['facts']['archive_bytes']) is int and 0<restore['facts']['archive_bytes']<=512*1024**2
         and hex_string(restore['facts']['generation_sha256'],64)
         and set(final['facts'])=={'catalog_sha256','retired_count'}
         and hex_string(final['facts']['catalog_sha256'],64)
         and type(final['facts']['retired_count']) is int and 0<=final['facts']['retired_count']<=10000)
    need(terminal.get('transaction')==transaction and terminal.get('state')=='complete'
         and terminal.get('result')==final and restore['facts'].get('native_restore_accepted') is True
         and all(hydrate_result['facts'].get(key)==restore['facts'].get(key) for key in
                 ('archive_sha256','archive_bytes','generation_sha256','backup_label'))
         and restore['facts'].get('backup_label')==capture['backup_label'])
    accepted={'schema':'careertrellis.paired-native-bundle-accepted/v1','state':'native_bundle_complete',
        'transaction':transaction,'policy_sha256':paired_policy_sha256,
        'retention_policy_sha256':strategy['retention_policy_sha256'],'activation_sha256':strategy['activation_sha256'],
        'backup_label':capture['backup_label'],'native_restore_accepted':True,
        'archive_sha256':restore['facts']['archive_sha256'],'native_receipt_sha256':restore['receipt_sha256'],
        'restore_status_sha256':digest(restore),'bundle_completion_sha256':digest(terminal)}
    return validate_bundle_acceptance(accepted,transaction,paired_policy_sha256,capture,strategy)


def _journal_names(store, maximum=512):
    import itertools
    names={path.name for path in itertools.islice(store.root.iterdir(),maximum+1)}
    need(len(names)<=maximum)
    for name in names:need(store.exists(name))
    return names


def _slot_for_transaction(policy, transaction):
    slots=Journal(ROLE_ROOT/'slots');matches=[]
    for name in _journal_names(slots):
        need(re.fullmatch(r'[0-9]{8}(?:-[0-9a-f]{64})?\.json',name))
        claim=slots.read(name)
        need(set(claim)=={'transaction','policy_sha256'} and hex_string(claim['transaction'],32)
             and hex_string(claim['policy_sha256'],64))
        if '-' in name:need(name[9:-5]==claim['policy_sha256'])
        if claim['transaction']==transaction:
            need(claim['policy_sha256']==policy.digest)
            matches.append(name)
    need(len(matches)==1)
    return matches[0]


def _finished_claim(claim, release, boot, claim_name=None):
    need(set(claim)=={'transaction','policy_sha256'} and hex_string(claim['transaction'],32)
         and hex_string(claim['policy_sha256'],64))
    store=Journal(ROLE_ROOT/'runs'/claim['transaction'])
    if store.exists('prestart-aborted.json'):
        need(_journal_names(store,3)=={'status.json','prestart-original-status.json','prestart-aborted.json'})
        proof=validate_prestart_proof(store.read('prestart-aborted.json'),claim['transaction'],
            claim['policy_sha256'],release,boot)
        need(proof['original_status_sha256']==digest(store.read('prestart-original-status.json'))
             and (claim_name is None or proof['slot_claim_name']==claim_name))
        need(Journal(ROLE_ROOT/'slots').read(proof['slot_claim_name'])==claim)
        return True
    for name in ('complete.json','aborted-restored.json'):
        if store.exists(name):
            record=store.read(name)
            need(record.get('transaction')==claim['transaction']
                 and record.get('policy_sha256')==claim['policy_sha256']
                 and record.get('services_restored') is True)
            if store.exists('start-intent.json'):
                original=store.read('start-intent.json')
                need(original.get('transaction')==claim['transaction']
                     and original.get('policy_sha256')==claim['policy_sha256']
                     and original.get('identity',{}).get('release')==release)
            else:
                need(name=='complete.json' and record.get('state')=='skipped')
            return True
    return False


def public_marker(policy, boot, phase, transaction=None):
    """Replace only the fixed public marker; never relax its private siblings."""
    need(phase in ('idle', 'blocked', 'restore_allowed'))
    value = {'schema': 'careertrellis.paired-maintenance-interlock/v1',
             'policy_sha256': policy.digest, 'boot_id': boot,
             'release': policy.data['application_release'], 'phase': phase}
    if transaction is not None:
        need(hex_string(transaction, 32))
        value['transaction'] = transaction
    need(phase != 'restore_allowed' or transaction is not None)
    for parent in (ROOT, *ROOT.parents):
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
             and not info.st_mode & 0o022)
    target = ROOT / 'writer-interlock.json'
    try:
        old = target.lstat()
        need(stat.S_ISREG(old.st_mode) and old.st_uid == old.st_gid == 0
             and old.st_nlink == 1 and stat.S_IMODE(old.st_mode) == 0o644)
    except FileNotFoundError:
        pass
    temporary = ROOT / ('.writer-interlock-' + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        os.fchmod(descriptor, 0o644)
        raw = canonical(value)
        offset = 0
        while offset < len(raw):
            amount = os.write(descriptor, raw[offset:])
            need(amount > 0)
            offset += amount
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, target)
        directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        # Only this exclusive temporary leaf is disposable; no old marker is.
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class WebBackend:
    def __init__(self, policy, transaction, store):
        self.policy, self.transaction, self.store = policy, transaction, store
        self.r = runtime.HostRuntime(policy, 'web', transaction)
        self.client = Client(policy, transaction)
        self.last_peer = None
        self.restored = not store.exists('stop-intent.json')
        self.current_status = ('recovery' if store.exists('start-intent.json') else 'preflight', 'running', self.restored)
        self.status_lock = threading.RLock()
        self.stop_heartbeat = threading.Event()
        self.heartbeat_failure = None
        self.bundle_unconfirmed = False

    def now(self):
        return dt.datetime.now(dt.timezone.utc)

    def identity(self):
        return {'release': self.policy.data['application_release'],
                'web_boot': self.r.boot, 'web_invocation': self.r.invocation}

    def prove_prestart_terminal(self, transaction):
        """Prove non-dispatch and current health; perform no restoration action."""
        need(transaction==self.transaction and hex_string(transaction,32)
             and hex_string(self.r.invocation,32) and self.r.fence is None)
        def children_terminal():
            need(self.r.child is None or self.r.child.poll() is not None)
            need(not self.r.live_group())
        def current_policy():
            for path,mode in (('/etc/careertrellis/paired-maintenance/policy.json',0o600),
                              (str(ROOT/'public-policy.json'),0o644)):
                need(canonical(decode(runtime.protected(path,131072,(mode,)),131072))==canonical(self.policy.data))
        def writers():
            result={}
            for unit in runtime.SERVICES:
                state=self.r.state(unit)
                need(state.get('ActiveState')=='active' and state.get('SubState')=='running'
                     and not state.get('Job') and state.get('ControlPID')=='0'
                     and state.get('MainPID','').isdigit() and int(state['MainPID'])>0
                     and hex_string(state.get('InvocationID'),32)
                     and state.get('ExecMainStartTimestampMonotonic','').isdigit()
                     and int(state['ExecMainStartTimestampMonotonic'])>0)
                result[unit]=tuple(state[key] for key in ('MainPID','InvocationID','ExecMainStartTimestampMonotonic'))
            return result
        names=_journal_names(self.store,3)
        need(names in ({'status.json'},{'status.json','prestart-original-status.json'},
                       {'status.json','prestart-original-status.json','prestart-aborted.json'}))
        original=self.store.read('prestart-original-status.json' if 'prestart-original-status.json' in names else 'status.json')
        need(set(original)=={'state','transaction','policy_sha256','release','boot_id',
                             'worker_invocation','observed_at','result','services_restored'}
             and original['transaction']==transaction and original['policy_sha256']==self.policy.digest
             and original['release']==self.policy.data['application_release'] and original['boot_id']==self.r.boot
             and hex_string(original['worker_invocation'],32) and original['services_restored'] is True
             and original['state'] in ('preflight','blocked','operator_required','failed')
             and original['result'] in ('running','failed','blocked'))
        current=self.store.read('status.json')
        need(current.get('transaction')==transaction and current.get('policy_sha256')==self.policy.digest
             and current.get('release')==self.policy.data['application_release'] and current.get('boot_id')==self.r.boot
             and current.get('services_restored') is True)
        slot=_slot_for_transaction(self.policy,transaction)
        children_terminal();current_policy()
        before=writers()
        self.r.verify_sources();self.r.health()
        need(writers()==before and self.r.fence is None)
        children_terminal();current_policy()
        if 'prestart-aborted.json' in names:
            proof=validate_prestart_proof(self.store.read('prestart-aborted.json'),transaction,
                self.policy.digest,self.policy.data['application_release'],self.r.boot)
            need(proof['original_status_sha256']==digest(original) and proof['slot_claim_name']==slot)
        else:
            self.store.write_once('prestart-original-status.json',original)
            proof={'schema':'careertrellis.paired-maintenance-prestart-abort/v1',
                'transaction':transaction,'policy_sha256':self.policy.digest,
                'release':self.policy.data['application_release'],'boot_id':self.r.boot,
                'slot_claim_name':slot,'original_status_sha256':digest(original),
                'no_dispatch':True,'services_unchanged':True,'services_restored':True,
                'restoration_performed':False,'verified_at':self.now().isoformat(),
                'verifier_invocation':self.r.invocation}
            validate_prestart_proof(proof,transaction,self.policy.digest,self.policy.data['application_release'],self.r.boot)
            self.store.write_once('prestart-aborted.json',proof)
        with self.status_lock:
            self.current_status=('failed','failed',True)
            self._global_status('failed','failed',True)
        return proof

    def heartbeat(self):
        while not self.stop_heartbeat.wait(30):
            try:
                with self.status_lock:
                    self._global_status(*self.current_status)
            except Exception:
                self.heartbeat_failure = True
                return

    def status(self, phase, result='running', services_restored=False):
        if phase == 'fresh':
            need(self.restored and not self.store.exists('start-intent.json'))
            self.store.write_once('complete.json', {'state': 'skipped', 'transaction': self.transaction,
                'policy_sha256': self.policy.digest, 'services_restored': True})
            phase, result = 'idle', 'ok'
        aliases = {'recovering': 'recovery', 'stopping': 'quiescing',
                   'aborted_restored': 'failed'}
        phase = aliases.get(phase, phase)
        if result == 'skipped':
            result = 'blocked'
        services_restored = self.restored
        with self.status_lock:
            self.current_status = (phase, result, services_restored)
            self._global_status(phase, result, services_restored)
            self.r.status(phase, result=result, services_restored=services_restored)

    def _record_verified_generation(self):
        """Prove backup delivery before its WEB monitor can report recovery.

        This is generation acceptance, not final WEB-monitor delivery acceptance;
        WebControl's complete.json remains after normal reporting succeeds.
        """
        capture = self.store.read('capture-result.json')
        validate_capture(capture)
        need(capture['release'] == self.policy.data['application_release'])
        publication = self.store.read('publication-result.json')
        need(set(publication) == {'authority', 'backup_label', 'manifest_sha256', 'release', 'result', 'snapshot_id'}
             and publication['result'] == 'onedrive_snapshot_created')
        need(all(publication[key] == capture[key] for key in ('authority', 'backup_label', 'manifest_sha256', 'release', 'snapshot_id')))
        restored = self.store.read('writers-restored.json')
        need(restored.get('services_restored') is True and restored.get('capture_sha256') == digest(capture)
             and self.store.read('db-released.json').get('services_restored') is True and self.restored)
        health = self.store.read('db-health-accepted.json')
        strategy=validate_backup_strategy(self.store.read('start-intent.json').get('backup_strategy',
            {'mode':'legacy-mirror'}) if self.store.exists('start-intent.json') else {'mode':'legacy-mirror'})
        if strategy['mode']=='native-bundle-v2':
            accepted=self.store.read('db-bundle-accepted.json')
            validate_bundle_acceptance(accepted,self.transaction,self.policy.digest,capture,strategy)
            need(not self.store.exists('db-mirror-accepted.json'))
        else:
            accepted=self.store.read('db-mirror-accepted.json')
            need(accepted.get('state')=='mirror_complete' and accepted.get('backup_label')==capture['backup_label'])
        need(health.get('state') == 'health_complete' and health.get('normal_report_accepted') is True)
        need(all(record.get('transaction') == self.transaction and record.get('policy_sha256') == self.policy.digest
                 for record in (accepted, health)))
        self.r.verify_sources()
        self.r.health()
        root = Journal(ROLE_ROOT)
        name = 'verified-generation-' + self.transaction + '.json'
        fields = {'schema': 'careertrellis.paired-maintenance-generation/v1',
            'policy_sha256': self.policy.digest, 'boot_id': self.r.boot,
            'release': self.policy.data['application_release'], 'transaction': self.transaction,
            'backup_label': capture['backup_label'], 'manifest_sha256': capture['manifest_sha256']}
        if root.exists(name):
            record = root.read(name)
            need(set(record) == set(fields) | {'observed_at'} and all(record[key] == value for key, value in fields.items()))
        else:
            record = dict(fields, observed_at=self.now().isoformat())
            root.write_once(name, record)
        self._replace_generation_projection(record, root)

    def _replace_generation_projection(self, record, root):
        path = ROLE_ROOT / 'verified-generation.json'
        try:
            old = decode(runtime.protected(path, 65536, (0o600,)), 65536)
            need(hex_string(old.get('transaction'), 32))
            need(root.read('verified-generation-' + old['transaction'] + '.json') == old)
        except FileNotFoundError:
            pass
        temporary = ROLE_ROOT / ('.verified-generation-' + uuid.uuid4().hex)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            raw = canonical(record); offset = 0
            while offset < len(raw):
                amount = os.write(descriptor, raw[offset:]); need(amount > 0); offset += amount
            os.fsync(descriptor)
            os.close(descriptor); descriptor = None
            os.replace(temporary, path)
            directory = os.open(ROLE_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if descriptor is not None: os.close(descriptor)
            # A failed unique staging leaf is retained for reconciliation.

    def _global_status(self, phase, result, services_restored):
        value = {'schema': 'careertrellis.paired-maintenance-status/v1',
                 'policy_sha256': self.policy.digest, 'boot_id': self.r.boot,
                 'release': self.policy.data['application_release'], 'phase': phase,
                 'result': result, 'observed_at': self.now().isoformat(),
                 'services_restored': services_restored}
        if phase != 'idle':
            value['transaction'] = self.transaction
        Journal(ROLE_ROOT).replace('status.json', value)

    def preflight(self):
        self.status('preflight', services_restored=True)
        self.r.verify_sources()
        self.r.health()
        temporary = runtime.Fence(runtime.MAINTENANCE, 0o644).acquire()
        recovery = None
        try:
            recovery = runtime.Fence(runtime.RECOVERY, 0o600).acquire()
            facts = provider.web_facts()
        finally:
            if recovery:
                recovery.close()
            temporary.close()
        data = self.policy.data
        need(facts['source_count'] <= data['max_capture_objects'] and facts['source_bytes'] <= data['max_capture_bytes'])
        need(facts['onedrive_free_bytes'] >= facts['source_bytes'] + data['minimum_onedrive_reserve_bytes'])
        need(facts['egress_ipv4_cidr'] == data['approved_egress_ipv4_cidr'])
        need(self.client.call('preflight', {}).get('ready') is True)
        latest = decode(self.r.run(['/usr/bin/python3', '-B', runtime.CORE, 'latest',
                        '--operation', 'generated_object_mirror', '--receipt-root',
                        '/var/backups/careertrellis/backup-health/operations']), 65536)
        need(latest.get('operation') == 'generated_object_mirror' and latest.get('result') == 'ok')
        return {'ready': True, 'last_capture_started_at': latest['started_at'],
                'backup_strategy':self.retention_strategy()}

    def retention_strategy(self):
        """Use only the explicit activated selector, never policy-file presence.

        New paired source is installed together with the standalone namespace.
        A missing/corrupt installation is a preflight failure. The selector
        alone may return the legacy contract before the explicit activation.
        """
        from onedrive_retention import entry as retention_entry
        retention_entry.verify_install()
        activation=retention_entry._load('activation')
        strategy=validate_backup_strategy(activation.selection_for_paired(self.policy))
        if strategy['mode']=='native-bundle-v2':
            context=retention_entry._native_dependencies(retention_entry.verify_install(),'web')
            activation.require_activated(context)
            need(context.bindings['paired_policy_sha256']==self.policy.digest
                 and context.bindings['standalone_policy_sha256']==strategy['retention_policy_sha256'])
        return strategy

    def acquire_fence(self):
        need(not self.heartbeat_failure)
        self.r.verify_sources()
        self.r.fence = runtime.Fence(runtime.MAINTENANCE, 0o644).acquire()
        self.r.fence.inherit10()

    def release_fence(self):
        self.r.check()
        need(not self.r.live_group())
        if self.r.fence:
            self.r.fence.close()
            self.r.fence = None

    def peer(self, operation, payload):
        need(not self.heartbeat_failure)
        return self.client.call(operation, payload)

    def _bind_peer(self, value):
        need(not value.get('operator_required'))
        need(value.get('transaction') == self.transaction and value.get('policy_sha256') == self.policy.digest)
        need(value.get('release') == self.policy.data['application_release'])
        need(isinstance(value.get('boot_id'), str) and str(uuid.UUID(value['boot_id'])) == value['boot_id'])
        need(hex_string(value.get('worker_invocation'), 32))
        identity = {key: value[key] for key in ('transaction', 'policy_sha256', 'release', 'boot_id', 'worker_invocation')}
        self.store.write_once('peer-identity.json', identity)
        self.last_peer = value

    def wait_peer(self, wanted, timeout):
        value = self.r.wait_peer(self.client, wanted, timeout,
                                 on_wait=lambda value: self._observe_peer(value))
        self._bind_peer(value)
        if value['state'] == 'fenced':
            self.status('db_fenced', services_restored=True)
        return value

    def _observe_peer(self, value):
        if self.store.exists('peer-identity.json'):
            self._bind_peer(value)
        need(not self.heartbeat_failure)

    def snapshot_writers(self):
        self.r.health()
        result = {}
        for unit in runtime.SERVICES:
            self.r.unit_binding(unit)
            state = self.r.state(unit)
            need(state['ActiveState'] == 'active' and state['SubState'] == 'running' and not state['Job'])
            result[unit] = int(state['MainPID'])
            need(result[unit] > 0)
        return result

    def block_writers(self):
        public_marker(self.policy, self.r.boot, 'blocked', self.transaction)

    def stop_writers(self):
        need(self.store.exists('stop-intent.json'))
        stop_started = time.monotonic()
        self.restored = False
        self.status('quiescing')
        self.r.run(['/usr/bin/systemctl', 'stop', *runtime.SERVICES], timeout=120)
        self.r.stopped()
        value = time.time()
        self.store.write_once('quiesced-result.json', {'quiesced_at': value,
            'stop_started_monotonic': stop_started, 'boot_id': self.r.boot})
        self.status('backup')
        return value

    def capture(self, pair):
        self.r.stopped()
        stopped = self.store.read('quiesced-result.json')
        quiesced = stopped['quiesced_at']
        started = stopped['stop_started_monotonic']
        now = time.monotonic()
        need(stopped['boot_id'] == self.r.boot and type(started) in (int, float)
             and 0 <= started <= now, 'stop_clock_unverified')
        remaining = self.policy.data['planned_stop_budget_seconds'] - (now - started)
        need(remaining > 0, 'stop_budget_exceeded')
        runtime.validate_pair(pair, quiesced, time.time())
        need(not self.store.exists('capture-dispatch-intent.json'))
        self.store.write_once('capture-dispatch-intent.json', {'pair_sha256': digest(pair), 'issued_epoch': time.time()})
        self.status('capture')
        value = decode(self.r.run([runtime.HELPER, 'capture',
            '/opt/careertrellis/staging/releases/' + self.policy.data['application_release'],
            pair['backup_label'], *runtime.ACKS], timeout=max(1, remaining), inherit=True), 65536)
        validate_capture(value)
        runtime.validate_capture(value, pair['backup_label'], quiesced, time.time())
        need(time.monotonic() - started <= self.policy.data['planned_stop_budget_seconds'], 'stop_budget_exceeded')
        return value

    def allow_writer_restore(self):
        need(self.last_peer and self.last_peer.get('state') in ('sealed', 'abort_ready'))
        self.r.verify_sources()
        public_marker(self.policy, self.r.boot, 'restore_allowed', self.transaction)
        self.status('restoring')

    def restore_writers(self, recovery=False):
        need(self.last_peer and self.last_peer.get('state') in ('sealed', 'abort_ready'))
        need(not self.r.live_group())
        self.r.authority()
        if not self.store.exists('stop-intent.json'):
            need(recovery)
            self.r.health()
            self.restored = True
            return
        probe = runtime.Fence(runtime.RECOVERY, 0o600).acquire()
        probe.close()
        original = self.store.read('stop-intent.json')['writers']
        need(set(original) == set(runtime.SERVICES) and all(type(pid) is int and pid > 0 for pid in original.values()))
        for unit in runtime.SERVICES:
            state = self.r.state(unit)
            if recovery and state['ActiveState'] == 'active':
                self.r.process_release(unit, state)
            else:
                self.r.terminal(unit)
        self.store.write_once('restore-dispatch-intent.json', {'restore_original_writers': True})
        self.r.run(['/usr/bin/systemctl', 'start', *runtime.SERVICES], timeout=120)
        self.r.startup_prior_pids = original
        deadline = time.monotonic() + 120
        try:
            while True:
                need(time.monotonic() < deadline)
                try:
                    self.r.health()
                    time.sleep(5)
                    self.r.health()
                    self.restored = True
                    return
                except runtime.StartupPending:
                    pass
                except (urllib.error.URLError, TimeoutError):
                    pass
                except runtime.Refused as exc:
                    if str(exc) not in ('service_not_ready', 'heartbeat_stale'):
                        raise
                time.sleep(2)
        finally:
            del self.r.startup_prior_pids

    def _restoration_identity(self):
        original = self.store.read('start-intent.json')
        need(set(original) == {'transaction', 'policy_sha256', 'identity'}
             and original['transaction'] == self.transaction and original['policy_sha256'] == self.policy.digest)
        identity = original['identity']
        need(type(identity) is dict and set(identity) == {'release', 'web_boot', 'web_invocation'}
             and identity['release'] == self.policy.data['application_release']
             and identity['web_boot'] == self.r.boot and hex_string(identity['web_invocation'], 32))
        stopped = self.store.read('stop-intent.json')
        need(set(stopped) == {'writers'} and type(stopped['writers']) is dict
             and set(stopped['writers']) == set(runtime.SERVICES)
             and all(type(pid) is int and pid > 0 for pid in stopped['writers'].values()))
        peer = self.store.read('peer-identity.json')
        need(set(peer) == {'transaction', 'policy_sha256', 'release', 'boot_id', 'worker_invocation'}
             and peer['transaction'] == self.transaction and peer['policy_sha256'] == self.policy.digest
             and peer['release'] == identity['release'] and hex_string(peer['worker_invocation'], 32)
             and isinstance(peer['boot_id'], str) and str(uuid.UUID(peer['boot_id'])) == peer['boot_id'])
        return identity

    def _capture_restoration(self, identity):
        captured = self.store.read('capture-result.json')
        validate_capture(captured)
        need(captured['release'] == identity['release']
             and captured['object_count'] <= self.policy.data['max_capture_objects']
             and captured['ciphertext_bytes'] <= self.policy.data['max_capture_bytes'])
        need(self.store.read('writers-restored.json') == {
            'services_restored': True, 'capture_sha256': digest(captured)})

    def reconcile_terminal_peer(self, value):
        self.restored = False
        need(type(value) is dict and value.get('state') in ('released', 'aborted'))
        identity = self._restoration_identity()
        self._bind_peer(value)
        if value['state'] == 'released':
            self._capture_restoration(identity)
        else:
            need(self.store.read('restore-dispatch-intent.json') == {'restore_original_writers': True})
        self.r.health()
        if value['state'] == 'released':
            self.store.write_once('db-released.json', {'services_restored': True})
        self.restored = True

    def clear_writer_block(self):
        if self.store.exists('db-released.json'):
            # Online recovery proves the original completed restoration from
            # durable bindings and current worker health without replay.
            self.restored = False
            identity = self._restoration_identity()
            need(self.store.read('db-released.json') == {'services_restored': True})
            self._capture_restoration(identity)
            self.r.health()
            self.restored = True
        else:
            need(self.restored and self.last_peer and self.last_peer.get('state') == 'aborted')
            self.r.health()
        public_marker(self.policy, self.r.boot, 'idle')

    def publish(self, capture):
        need(self.restored and self.r.fence is None and self.store.exists('db-released.json'))
        need(not self.store.exists('publish-dispatch-intent.json'))
        self.store.write_once('publish-dispatch-intent.json', {'capture_sha256': digest(capture)})
        self.status('publishing', services_restored=True)
        value = decode(self.r.run([runtime.HELPER, 'publish-captured',
            '/opt/careertrellis/staging/releases/' + self.policy.data['application_release'],
            capture['backup_label'], *runtime.ACKS], timeout=7200), 65536)
        need(set(value) == {'authority', 'backup_label', 'manifest_sha256', 'release', 'result', 'snapshot_id'})
        need(value['result'] == 'onedrive_snapshot_created')
        need(all(value[key] == capture[key] for key in ('authority', 'backup_label', 'manifest_sha256', 'release', 'snapshot_id')))
        return value

    def run_native_bundle(self,capture,strategy):
        """One same-TX fixed child after writers and object publication recover.

        The parent retains the canonical WEB fence and passes its SAME open
        description to the child. Unknown completion never releases that fence,
        retries the coordinator, starts a legacy mirror, or signals a DB job.
        """
        validate_capture(capture);validate_backup_strategy(strategy)
        need(strategy['mode']=='native-bundle-v2' and strategy==self.retention_strategy())
        need(self.restored and self.r.fence is None and not self.r.live_group()
             and self.store.read('db-released.json')=={'services_restored':True}
             and self.store.read('writers-restored.json')=={'services_restored':True,'capture_sha256':digest(capture)}
             and self.store.read('start-intent.json').get('backup_strategy')==strategy)
        publication=self.store.read('publication-result.json')
        need(publication.get('result')=='onedrive_snapshot_created'
             and all(publication.get(key)==capture[key] for key in
                 ('release','backup_label','snapshot_id','manifest_sha256','authority')))
        from onedrive_retention import entry as retention_entry
        retention_entry.verify_install()
        native=retention_entry._load('native_restore')
        preservation=retention_entry._load('preservation')
        parent_unit='careertrellis-paired-maintenance.service'
        parent_state=self.r.state(parent_unit)
        need(parent_state.get('ActiveState')=='active' and parent_state.get('SubState')=='running'
             and parent_state.get('MainPID')==str(os.getpid()) and parent_state.get('ControlPID')=='0'
             and parent_state.get('Job')=='' and parent_state.get('InvocationID')==self.r.invocation
             and Path('/proc/self/cgroup').read_text().strip()=='0::/system.slice/'+parent_unit)
        self.r.verify_sources();self.r.health()
        self.r.fence=runtime.Fence(runtime.MAINTENANCE,0o644).acquire()
        # From this point, only a fully verified successful child may close it.
        self.bundle_unconfirmed=True
        maintenance_fd=self.r.fence.fd
        maintenance_identity=preservation.require_owned_lock(str(runtime.MAINTENANCE),maintenance_fd)
        need(type(maintenance_fd) is int and 3<=maintenance_fd<=1024)
        handoff={'schema':'careertrellis.paired-retention-handoff/v1','transaction':self.transaction,
            'paired_policy_sha256':self.policy.digest,'retention_policy_sha256':strategy['retention_policy_sha256'],
            'release':capture['release'],'backup_label':capture['backup_label'],'capture_sha256':digest(capture),
            'publication_sha256':digest(publication),'parent_owner':native.process_identity(os.getpid()),
            'parent_unit':parent_unit,'parent_invocation':self.r.invocation,
            'maintenance_fd':maintenance_fd,'maintenance_identity':maintenance_identity,'issued_at':self.now().isoformat()}
        self.store.write_once('bundle-dispatch-intent.json',handoff)
        command=['/usr/bin/python3','-I','-B',
            '/usr/local/libexec/careertrellis/onedrive_retention/entry.py','web-run',
            '--transaction',self.transaction,'--expected-backup-label',capture['backup_label']]
        log=self.store.root/'bundle-private-output.log'
        output=os.open(log,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        proc=None;child=None
        try:
            os.fchmod(output,0o600);os.fsync(output)
            parent_fd=os.open(log.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(parent_fd)
            finally:os.close(parent_fd)
            proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=output,stderr=output,
                env={'PATH':runtime.CHILD_PATH,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1',
                     'INVOCATION_ID':self.r.invocation},pass_fds=(maintenance_fd,),close_fds=True,start_new_session=True)
            self.r.child,self.r.group=proc,proc.pid
            child=native.process_identity(proc.pid)
            self.store.write_once('bundle-child-dispatched.json',{
                'schema':'careertrellis.paired-retention-child/v1','transaction':self.transaction,
                'handoff_sha256':digest(handoff),'owner':child,'command':command})
            self.status('mirroring',services_restored=True)
            deadline=time.monotonic()+BUNDLE_ONLINE_SECONDS
            while proc.poll() is None:
                self.r.fence.check()
                need(not self.heartbeat_failure and time.monotonic()<deadline,'bundle_outcome_unknown')
                need(os.fstat(output).st_size<=BUNDLE_OUTPUT_LIMIT,'bundle_output_bound')
                time.sleep(1)
            code=proc.wait()
            need(not self.r.live_group(),'bundle_descendants_unconfirmed')
            self.store.write_once('bundle-child-terminal.json',{
                'schema':'careertrellis.paired-retention-child-terminal/v1','transaction':self.transaction,
                'handoff_sha256':digest(handoff),'owner':child,'returncode':code,'completed_at':self.now().isoformat()})
            need(code==0 and os.fstat(output).st_size<=BUNDLE_OUTPUT_LIMIT,'bundle_child_failed')
            raw=runtime.protected(log,BUNDLE_OUTPUT_LIMIT,(0o600,))
            returned=decode(raw,BUNDLE_OUTPUT_LIMIT)
            coordinator=retention_entry._load('coordinator')
            root=coordinator.STATE/'web'/self.transaction
            documents={name:coordinator.read_private(root/name) for name in
                ('coordinator-intent.json','hydrate-complete.json','restore-complete.json',
                 'retire-complete.json','coordinator-terminal.json')}
            need(returned==documents['retire-complete.json'] and strategy==self.retention_strategy())
            accepted=native_bundle_acceptance(capture,strategy,self.transaction,self.policy.digest,documents)
            self.r.fence.check()
            self.r.child,self.r.group=None,None
            self.r.fence.close();self.r.fence=None
            self.bundle_unconfirmed=False
            return accepted
        except BaseException:
            # Never kill/replay an unknown coordinator or release its inherited
            # fence. The recovery observer keeps the original guardian alive.
            if not self.store.exists('bundle-child-unconfirmed.json'):
                self.store.write_once('bundle-child-unconfirmed.json',{
                    'schema':'careertrellis.paired-retention-unconfirmed/v1','transaction':self.transaction,
                    'handoff_sha256':digest(handoff),'child_owner':child,
                    'returncode':proc.poll() if proc is not None else None,'automatic_retry':False})
            raise Refused('bundle_outcome_requires_reconciliation') from None
        finally:os.close(output)

    def _wait_online(self, operation, complete, timeout):
        deadline = time.monotonic() + timeout
        while True:
            need(not self.heartbeat_failure)
            value = self.peer(operation, {})
            if value.get('state') == complete:
                return value
            need(value.get('state') in ('mirroring', 'health_running') and not value.get('operator_required'))
            need(time.monotonic() < deadline)
            time.sleep(5)

    def wait_mirror(self):
        self.status('mirroring', services_restored=True)
        value = self._wait_online('mirror_status', 'mirror_complete', 14400)
        need(value['backup_label'] == self.store.read('capture-result.json')['backup_label'])
        self.store.write_once('db-mirror-accepted.json', {'state': 'mirror_complete',
            'transaction': self.transaction, 'policy_sha256': self.policy.digest, 'backup_label': value['backup_label']})

    def wait_health(self):
        self.status('verifying', services_restored=True)
        value = self._wait_online('health_status', 'health_complete', 900)
        need(value.get('normal_report_accepted') is True)
        self.store.write_once('db-health-accepted.json', {'state': 'health_complete',
            'transaction': self.transaction, 'policy_sha256': self.policy.digest, 'normal_report_accepted': True})

    def _events(self, unit, since):
        raw = self.r.run(['/usr/bin/journalctl', '-u', unit, '--since', '@' + str(int(since)),
                          '-n', '60', '--no-pager', '-o', 'json'])
        result = []
        for line in raw.splitlines():
            try:
                row = json.loads(line)
                value = json.loads(row.get('MESSAGE', ''))
                if type(value) is dict:
                    result.append((int(row['__REALTIME_TIMESTAMP']) / 1000000,
                                   row.get('_SYSTEMD_INVOCATION_ID', ''), value))
            except (ValueError, TypeError, KeyError):
                pass
        return result

    def _report_accepted(self, issued, invocation):
        need(self.r.run(['/usr/bin/systemctl', 'show', HEALTH, '--property=OnSuccess', '--value']).decode().strip().split() == [REPORTER])
        events = self._events(HEALTH, issued)
        accepted = [(stamp, worker, value) for stamp, worker, value in events
                    if worker == invocation and value.get('schema') == 'careertrellis.backup-health-status/v1'
                    and value.get('surface') == 'objects' and value.get('result') == 'ok' and value.get('failure_codes') == []]
        need(len(accepted) == 1, 'reporter_pending')
        stamp, _, _ = accepted[0]
        need(stamp >= issued)
        need(not any(t > stamp and i != invocation and v.get('schema') == 'careertrellis.backup-health-status/v1' for t, i, v in events))
        reporter = self.r.terminal(REPORTER)
        need(reporter['Result'] == 'success' and reporter['ExecMainStatus'] == '0' and hex_string(reporter['InvocationID'], 32), 'reporter_pending')
        delivered = [(t, i, v) for t, i, v in self._events(REPORTER, stamp)
                     if i == reporter['InvocationID'] and v.get('observation') == 'healthy' and v.get('provider_acceptance') == 'accepted']
        need(len(delivered) == 1 and stamp <= delivered[0][0] <= stamp + 120, 'reporter_pending')

    def normal_reports(self):
        self._record_verified_generation()
        for path, expected in HEALTH_PINS.items():
            need(hashlib.sha256(runtime.protected(path, 262144)).hexdigest() == expected)
        before = self.r.terminal(HEALTH)
        need(before['FragmentPath'] == '/etc/systemd/system/' + HEALTH and before['DropInPaths'] == DROPINS
             and before['NeedDaemonReload'] == 'no')
        need(not self.store.exists('health-dispatch-intent.json'))
        issued = time.time()
        self.store.write_once('health-dispatch-intent.json', {'issued_epoch': issued, 'prior_invocation': before['InvocationID']})
        self.r.run(['/usr/bin/systemctl', 'start', '--no-block', HEALTH])
        deadline = time.monotonic() + 300
        invocation = None
        while True:
            value = self.r.state(HEALTH)
            if hex_string(value.get('InvocationID'), 32) and value['InvocationID'] != before['InvocationID']:
                if invocation is None:
                    invocation = value['InvocationID']
                    self.store.write_once('health-dispatched.json', {'invocation': invocation})
                need(value['InvocationID'] == invocation)
                if value['ActiveState'] in ('inactive', 'failed') and value['MainPID'] == '0' and not value['Job']:
                    self.r.terminal(HEALTH)
                    need(value['Result'] == 'success' and value['ExecMainStatus'] == '0')
                    health = decode(self.r.run(['/usr/local/sbin/careertrellis-backup-health', 'status']), 65536)
                    need(health['result'] == 'ok' and health['failure_codes'] == [])
                    need(dt.datetime.fromisoformat(health['evaluated_at'].replace('Z', '+00:00')).timestamp() >= int(issued))
                    try:
                        self._report_accepted(issued, invocation)
                        self.store.write_once('normal-reports-accepted.json', {'invocation': invocation})
                        capture = self.store.read('capture-result.json')
                        completion = self.peer('complete', {'release': capture['release'], 'backup_label': capture['backup_label'],
                            'publication_manifest_sha256': capture['manifest_sha256']})
                        need(completion.get('state') == 'complete')
                        self._bind_peer(completion)
                        return
                    except Refused as exc:
                        if exc.code != 'reporter_pending':
                            raise
            need(time.monotonic() < deadline, 'normal_report_unconfirmed')
            time.sleep(2)


def _control_lock():
    import fcntl
    Journal(ROLE_ROOT)
    path = ROLE_ROOT / 'control.lock'
    before = path.lstat()
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
             and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
             and (info.st_dev, info.st_ino) == (before.st_dev, before.st_ino))
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _record_control_timeout(policy, reason):
    """Immutable diagnostic only; never overwrite the current owner's state."""
    need(reason in ('control_lock_timeout', 'policy_start_refused'))
    invocation = os.environ.get('INVOCATION_ID', '')
    need(hex_string(invocation, 32))
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    need(str(uuid.UUID(boot)) == boot)
    root = Journal(ROLE_ROOT)
    name = 'lock-timeout-' + invocation + '.json'
    fields = {'schema': 'careertrellis.paired-maintenance-lock-timeout/v1',
        'policy_sha256': policy.digest, 'boot_id': boot,
        'release': policy.data['application_release'], 'invocation': invocation,
        'result': 'failed', 'reason': reason}
    if root.exists(name):
        prior = root.read(name)
        need(set(prior) == set(fields) | {'observed_at'}
             and all(prior[key] == value for key, value in fields.items()))
        return
    root.write_once(name, dict(fields, observed_at=dt.datetime.now(dt.timezone.utc).isoformat()))


def _initial_control_lock(policy, recovery_only=False):
    """Wait only before a scheduled claim; recovery contention is a benign no-op.

    Only actual flock contention is retryable. No transaction operation, provider
    call, canonical fence or global state mutation occurs in this loop.
    """
    deadline = time.monotonic() + 30
    while True:
        try:
            return _control_lock()
        except BlockingIOError:
            if recovery_only:
                return None
            now = time.monotonic()
            if now >= deadline:
                _record_control_timeout(policy, 'control_lock_timeout')
                raise Refused('initial_control_lock_timeout') from None
            try:
                policy.require_new_start(dt.datetime.now(dt.timezone.utc))
            except Exception:
                _record_control_timeout(policy, 'policy_start_refused')
                raise Refused('initial_control_window_closed') from None
            time.sleep(min(.25, deadline - now))


def _terminal_prior(policy, prior):
    need(prior.get('policy_sha256') == policy.digest
         and prior.get('release') == policy.data['application_release'])
    if prior.get('phase') == 'idle':
        return prior.get('services_restored') is True
    if (prior.get('phase') == 'preflight' and prior.get('result') == 'failed'
            and prior.get('services_restored') is True and 'transaction' not in prior):
        need(set(prior) == {'schema', 'policy_sha256', 'boot_id', 'release', 'phase',
                            'result', 'observed_at', 'services_restored'}
             and prior['schema'] == 'careertrellis.paired-maintenance-status/v1'
             and isinstance(prior['boot_id'], str) and str(uuid.UUID(prior['boot_id'])) == prior['boot_id'])
        timestamp = dt.datetime.fromisoformat(prior['observed_at'].replace('Z', '+00:00'))
        need(timestamp.utcoffset() == dt.timedelta(0))
        return True
    transaction = prior.get('transaction')
    need(hex_string(transaction, 32))
    store = Journal(ROLE_ROOT / 'runs' / transaction)
    if store.exists('prestart-aborted.json'):
        need(prior.get('services_restored') is True and prior.get('result')=='failed'
             and prior.get('phase') in ('failed','blocked','preflight'))
        return _finished_claim({'transaction':transaction,'policy_sha256':policy.digest},
                               policy.data['application_release'],prior.get('boot_id'))
    name = 'complete.json' if prior.get('phase') == 'complete' else 'aborted-restored.json'
    if not store.exists(name):
        return False
    terminal = store.read(name)
    need(terminal.get('transaction') == transaction and terminal.get('policy_sha256') == policy.digest
         and terminal.get('services_restored') is True)
    return prior.get('phase') == 'complete' or name == 'aborted-restored.json'


def _bootstrap_locked(policy, preflight, root):
    prior = root.read('status.json') if root.exists('status.json') else {}
    try:
        need(bool(prior) and _terminal_prior(policy, prior))
        preflight.verify_sources()
        preflight.authority()
        public_marker(policy, preflight.boot, 'idle')
        failed = prior.get('phase') not in ('idle', 'complete')
        prestart_failed = failed and prior.get('phase') == 'preflight' and 'transaction' not in prior
        prestart_closed=bool(failed and hex_string(prior.get('transaction'),32)
            and Journal(ROLE_ROOT/'runs'/prior['transaction']).exists('prestart-aborted.json'))
        result = {'schema': 'careertrellis.paired-maintenance-status/v1',
                  'policy_sha256': policy.digest, 'boot_id': preflight.boot,
                  'release': policy.data['application_release'],
                  'phase': 'preflight' if prestart_failed else 'failed' if failed else 'idle',
                  'result': 'failed' if failed else 'ok',
                  'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'services_restored': True}
        if failed and not prestart_failed:
            result['transaction'] = prior['transaction']
        root.replace('status.json', result)
        value={'state':'prestart_aborted' if prestart_closed else 'prestart_failed' if prestart_failed else 'aborted_restored' if failed else 'idle',
               'services_restored':True}
        if prestart_closed:value['restoration_performed']=False
        return value
    except Exception:
        transaction = prior.get('transaction') if hex_string(prior.get('transaction'), 32) else '0' * 32
        public_marker(policy, preflight.boot, 'blocked', transaction)
        preflight.transaction = transaction
        preflight.global_status('operator_required', 'failed', False)
        raise Refused('bootstrap_unresolved') from None


def bootstrap_web(policy):
    """Boot prerequisite only: no writer starts, provider reads or recovery."""
    descriptor = _control_lock()
    try:
        preflight = runtime.preflight_runtime(policy, 'web')
        return _bootstrap_locked(policy, preflight, Journal(ROLE_ROOT))
    finally:
        os.close(descriptor)


def reconcile_prestart(policy, transaction):
    """Fixed one-off native-unit closure of an existing no-dispatch attempt.

    No calendar claim, provider call, DB message, service start or writer action.
    The staged reviewed runtime must be selected by a separately hash-bound
    support bootstrap; this function never imports an unverified alternate tree.
    """
    need(hex_string(transaction,32) and hex_string(os.environ.get('INVOCATION_ID',''),32))
    descriptor=_control_lock()
    try:
        backend=object.__new__(WebBackend)
        backend.policy=policy;backend.transaction=transaction
        backend.store=Journal(ROLE_ROOT/'runs'/transaction)
        backend.r=runtime.HostRuntime(policy,'web',transaction,worker=False)
        backend.restored=True;backend.status_lock=threading.RLock()
        backend.current_status=('preflight','running',True)
        return backend.prove_prestart_terminal(transaction)
    finally:os.close(descriptor)


def _run_controller(controller, backend, transaction, recovery_only):
    result = controller.run(transaction, recovery_only=recovery_only)
    while result.get('state') == 'operator_required' and (not backend.restored or getattr(backend,'bundle_unconfirmed',False)):
        if getattr(backend,'bundle_unconfirmed',False):
            # Writers were already restored. Preserve that fact and only the
            # original backup fence; do not rerun publication, child dispatch,
            # DB health, or writer restoration as an observation side effect.
            fence=backend.r.fence
            if fence is None or fence.fd is None:break
            fence.check()
            try:backend.status('operator_required','failed',True)
            except Exception:pass
            time.sleep(RECOVERY_RECHECK_SECONDS)
            continue
        try:
            public_marker(backend.policy, backend.r.boot, 'blocked', transaction)
        except Exception:
            # Alert/marker persistence failure cannot surrender an owned fence.
            pass
        fence = backend.r.fence
        if fence is None or fence.fd is None:
            # Failed acquisition grants no ownership of a different guardian's
            # fence. The durable gate and recovery timer remain fail-closed.
            break
        backend.r.check()
        try:
            backend.status('operator_required', 'failed', False)
        except Exception:
            pass
        # Stay inside the control-lock/heartbeat lifetime. Only the original
        # transaction's idempotent abort/restore reconciliation may run again;
        # an over-budget or unknown DB backup is neither killed nor replayed.
        time.sleep(RECOVERY_RECHECK_SECONDS)
        backend.r.check()
        result = controller.run(transaction, recovery_only=True)
    return result


def run_web(policy, recovery_only=False):
    descriptor = _initial_control_lock(policy, recovery_only=recovery_only)
    if descriptor is None:
        return {'state': 'control_busy', 'services_restored': False}
    backend = None
    preflight = root = prior = None
    prior_terminal = False
    slot_claimed = False
    transaction = None
    try:
        preflight = runtime.preflight_runtime(policy, 'web')
        root = Journal(ROLE_ROOT)
        prior = root.read('status.json') if root.exists('status.json') else None
        if prior:
            try:
                prior_terminal = _terminal_prior(policy, prior)
            except Exception:
                prior_terminal = False
        active = prior and prior.get('phase') not in ('idle', 'complete')
        if active and (prior.get('phase') == 'failed' or
                       (prior.get('phase') == 'preflight' and 'transaction' not in prior)):
            active = not prior_terminal
        if active:
            need(prior.get('policy_sha256') == policy.digest and hex_string(prior.get('transaction'), 32))
            if prior.get('boot_id') != preflight.boot:
                public_marker(policy, preflight.boot, 'blocked', prior['transaction'])
                preflight.transaction = prior['transaction']
                preflight.global_status('operator_required', 'failed', False)
                return {'state': 'operator_required', 'services_restored': False}
            transaction = prior['transaction']
        else:
            if recovery_only:
                return _bootstrap_locked(policy, preflight, root)
            need(prior_terminal, 'prior_state_unverified')
            date = policy.require_new_start(dt.datetime.now(dt.timezone.utc))
            slot = slot_name(date,policy.digest)
            slots = Journal(ROLE_ROOT / 'slots')
            if slots.exists(date+'.json'):
                legacy=slots.read(date+'.json')
                need(set(legacy)=={'transaction','policy_sha256'}
                     and hex_string(legacy['transaction'],32) and hex_string(legacy['policy_sha256'],64))
                finished=_finished_claim(legacy,policy.data['application_release'],preflight.boot,date+'.json')
                if legacy['policy_sha256']==policy.digest:
                    return {'state':'slot_consumed','services_restored':finished}
                need(finished,'legacy_claim_unresolved')
            if slots.exists(slot + '.json'):
                claim = slots.read(slot + '.json')
                need(claim.get('policy_sha256') == policy.digest and hex_string(claim.get('transaction'), 32))
                previous_path = ROLE_ROOT / 'runs' / claim['transaction']
                try:
                    previous_path.lstat()
                    previous = Journal(previous_path)
                    finished = _finished_claim(claim,policy.data['application_release'],preflight.boot,slot+'.json')
                except FileNotFoundError:
                    finished = False
                if not finished:
                    preflight.transaction = claim['transaction']
                    preflight.global_status('blocked', 'blocked', True)
                return {'state': 'slot_consumed', 'services_restored': True}
            transaction = uuid.uuid4().hex
            # Even a partial exclusive slot write consumes this attempt; it is
            # no longer eligible for the safe, no-claim prestart discriminator.
            slot_claimed = True
            slots.write_once(slot + '.json', {'transaction': transaction, 'policy_sha256': policy.digest})
            Journal(ROLE_ROOT / 'runs')
            os.mkdir(ROLE_ROOT / 'runs' / transaction, 0o700)
        store = Journal(ROLE_ROOT / 'runs' / transaction)
        backend = WebBackend(policy, transaction, store)
        if not store.exists('prestart-aborted.json'):
            backend.status(*backend.current_status[:2], services_restored=backend.current_status[2])
        heartbeat = threading.Thread(target=backend.heartbeat, daemon=True)
        heartbeat.start()
        try:
            return _run_controller(WebControl(policy, store, backend), backend,
                                   transaction, recovery_only=bool(active or recovery_only))
        finally:
            backend.stop_heartbeat.set()
            heartbeat.join(timeout=2)
    except Exception:
        if backend is not None:
            try:
                if not backend.restored:
                    public_marker(policy, backend.r.boot, 'blocked', backend.transaction)
                backend.status('operator_required', 'failed', backend.restored)
            except Exception:
                pass
        elif preflight is not None and root is not None:
            try:
                if slot_claimed and hex_string(transaction, 32):
                    preflight.transaction = transaction
                    preflight.global_status('blocked', 'failed', prior_terminal)
                elif prior_terminal:
                    # This exact no-TX shape means the scheduled attempt failed
                    # before any claim or writer stop. It preserves the alert
                    # without inventing an unresolved maintenance transaction.
                    root.replace('status.json', {
                        'schema': 'careertrellis.paired-maintenance-status/v1',
                        'policy_sha256': policy.digest, 'boot_id': preflight.boot,
                        'release': policy.data['application_release'], 'phase': 'preflight',
                        'result': 'failed', 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                        'services_restored': True})
                else:
                    # An unknown prior state never becomes a fake healthy or
                    # safely-restored prestart failure. Keep startup fail-closed.
                    known = prior.get('transaction') if isinstance(prior, dict) else None
                    public_marker(policy, preflight.boot, 'blocked', known if hex_string(known, 32) else None)
                    status = {'schema': 'careertrellis.paired-maintenance-status/v1',
                        'policy_sha256': policy.digest, 'boot_id': preflight.boot,
                        'release': policy.data['application_release'], 'phase': 'operator_required',
                        'result': 'failed', 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                        'services_restored': False}
                    if hex_string(known, 32):
                        status['transaction'] = known
                    root.replace('status.json', status)
            except Exception:
                pass
        raise
    finally:
        os.close(descriptor)
