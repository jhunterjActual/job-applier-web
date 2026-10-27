"""Fixed DB host adapter and durable paired-backup worker.

No actions occur on import. The entry serializes endpoint requests. Native worker
units own maintenance fences independently of their SSH dispatch connection.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import signal
import time

try:
    from . import runtime, provider
    from .common import Journal, Refused, digest
    from .db_control import validate_request, hex_string
    from .web_control import slot_name
except ImportError:
    import runtime, provider
    from common import Journal, Refused, digest
    from db_control import validate_request, hex_string
    from web_control import slot_name


ROLE_ROOT = Path('/var/lib/careertrellis/paired-maintenance/db')
MIRROR = 'careertrellis-pgbackrest-onedrive.service'
HEALTH = 'careertrellis-backup-health.service'
MIRROR_PINS = {
 '/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive': '630de3092529f165ee305601dc52ad6b89b912e90294dc42ab09ec419de552ca',
 '/etc/systemd/system/' + MIRROR: 'bb1796a7b2131e5964cc119a77fbc0a4260d39a69ef6d6aca6d8e6646d1720a4'}
HEALTH_PINS = {
 '/usr/local/sbin/careertrellis-backup-health': '58bc6e0e408e02830691b34fbebc71a3919a69834df5950c8fae8228db66bf41',
 '/etc/systemd/system/' + HEALTH: '7466dc73af94a06a1ee53f92dca2ee2ed64358ef4806f2b3b2ec56f172e7b3e0',
 '/etc/systemd/system/' + HEALTH + '.d/50-backup-reporting.conf': '34272f25158e3b6dea3c5243a7f0d5d1c516c72f6703bc50032270c4e57448f3'}


def need(value, code='db_host_refused'):
    if not value:
        raise Refused(code)


def terminal(value):
    return value.get('ActiveState') in ('inactive', 'failed') and value.get('MainPID') == '0' and not value.get('Job') and value.get('ControlPID', '0') == '0'


def facts_ok(policy, facts, stopped=False):
    need(facts['schema'] == policy.data['schema_revision'] and facts['policy_rows'] == 1)
    need(facts['maintenance'] is False and facts['signup_open'] is True)
    need(all(facts[key] == 0 for key in ('search_queue', 'active_searches', 'erasure_queue', 'prepared')))
    need(type(facts['database_bytes']) is int and 0 < facts['database_bytes'] <= policy.data['max_database_bytes'])
    if stopped:
        need(facts['clients'] == 0)


def latest(r, operation):
    need(hashlib.sha256(runtime.protected(runtime.CORE, 1048576)).hexdigest() == runtime.PINS['db'][runtime.CORE], 'db_core_drift')
    value = json.loads(r.run(['/usr/bin/python3', '-B', runtime.CORE, 'latest', '--operation', operation,
        '--receipt-root', '/var/backups/careertrellis/backup-health/operations']))
    need(value['operation'] == operation and value['result'] == 'ok')
    return value


def pins(r, values, unit, dropins):
    for path, expected in values.items():
        need(hashlib.sha256(runtime.protected(path, 262144)).hexdigest() == expected)
    value = r.state(unit)
    need(value['FragmentPath'] == '/etc/systemd/system/' + unit and value['DropInPaths'] == dropins
         and value['NeedDaemonReload'] == 'no')


class DBBackend:
    def __init__(self, policy, transaction, store):
        need(hex_string(transaction, 32))
        self.policy, self.transaction, self.store = policy, transaction, store
        self.r = runtime.preflight_runtime(policy, 'db')

    def now(self): return dt.datetime.now(dt.timezone.utc)

    def preflight(self):
        self.r.verify_sources()
        for unit in runtime.BACKUP_UNITS: self.r.terminal(unit)
        facts = provider.database_facts(); facts_ok(self.policy, facts)
        storage = provider.db_provider_facts()
        need(storage['onedrive_free_bytes'] >= storage['source_bytes'] + self.policy.data['minimum_onedrive_reserve_bytes'])
        retention = provider.retention_facts()
        need(retention == self.policy.data['retention_policy'])
        return {'state': 'ready', 'ready': True, 'database_bytes': facts['database_bytes'],
                **{key: storage[key] for key in ('onedrive_free_bytes', 'source_count', 'source_bytes')}, 'retention': retention}

    def _unit(self, recovery=False):
        return 'careertrellis-paired-db' + ('-recovery' if recovery else '') + '@' + self.transaction + '.service'

    def _write_status(self, state, **fields):
        if self.store.exists('status.json'):
            previous = self.store.read('status.json')
            if 'worker_invocation' in previous:
                fields.setdefault('worker_invocation', previous['worker_invocation'])
        value = {'state': state, 'transaction': self.transaction, 'policy_sha256': self.policy.digest,
                 'release': self.policy.data['application_release'], 'boot_id': self.r.boot, **fields}
        self.store.replace('status.json', value)
        self._global(state)
        return value

    def _global(self, state):
        Journal(ROLE_ROOT).replace('status.json', {'schema': 'careertrellis.paired-maintenance-status/v1',
            'transaction': self.transaction, 'policy_sha256': self.policy.digest,
            'release': self.policy.data['application_release'], 'boot_id': self.r.boot,
            'phase': state, 'result': 'ok' if state in ('complete', 'aborted') else 'failed' if state == 'operator_required' else 'running',
            'observed_at': self.now().isoformat(), 'services_restored': state in ('released', 'complete', 'aborted')})

    def start_db_worker(self, transaction):
        need(transaction == self.transaction)
        date = self.policy.require_new_start(self.now())
        slot = slot_name(date,self.policy.digest)
        root = Journal(ROLE_ROOT)
        if root.exists('status.json'):
            prior = root.read('status.json')
            allowed = prior.get('transaction') == transaction or prior.get('phase') in ('idle', 'complete', 'aborted')
            need(allowed)
        slots = Journal(ROLE_ROOT / 'slots')
        if slots.exists(date+'.json'):
            legacy=slots.read(date+'.json')
            need(set(legacy)=={'transaction','policy_sha256'} and hex_string(legacy['transaction'],32)
                 and hex_string(legacy['policy_sha256'],64)
                 and legacy['policy_sha256']!=self.policy.digest)
            previous=Journal(ROLE_ROOT/'runs'/legacy['transaction'])
            result=previous.read('complete-result.json')
            state=previous.read('status.json')
            need(result.get('transaction')==legacy['transaction']
                 and result.get('policy_sha256')==legacy['policy_sha256']
                 and state.get('state')=='complete' and state.get('transaction')==legacy['transaction']
                 and state.get('policy_sha256')==legacy['policy_sha256']
                 and state.get('release')==self.policy.data['application_release']
                 and state.get('boot_id')==self.r.boot and state.get('services_restored') is True)
        value = {'transaction': transaction, 'policy_sha256': self.policy.digest}
        if slots.exists(slot + '.json'):
            need(slots.read(slot + '.json') == value)
        else:
            slots.write_once(slot + '.json', value)
        need(not self.store.exists('worker-dispatch.json'))
        self.store.write_once('worker-dispatch.json', {'boot_id': self.r.boot})
        self._write_status('starting')
        self.r.run(['/usr/bin/systemctl', 'start', '--no-block', self._unit()])

    def worker_status(self, transaction):
        need(transaction == self.transaction)
        if not self.store.exists('status.json'):
            return {'state': 'unknown', 'operator_required': True}
        value = self.store.read('status.json')
        need(value['transaction'] == transaction and value['policy_sha256'] == self.policy.digest)
        if value['boot_id'] != self.r.boot:
            return {'state': 'unknown', 'operator_required': True}
        if value['state'] in ('released', 'aborted', 'complete'):
            return value
        unit = self.r.state(self._unit())
        recovery = self.r.state(self._unit(True))
        active = any(v.get('ActiveState') in ('active', 'activating') or v.get('Job') for v in (unit, recovery))
        if not active:
            return dict(value, state='unknown', operator_required=True)
        return value

    def request_abort(self, transaction):
        need(transaction == self.transaction)
        value = self.store.read('status.json') if self.store.exists('status.json') else {}
        if value.get('state') in ('released', 'aborted', 'complete'): return
        for recovery in (False, True):
            state = self.r.state(self._unit(recovery))
            if state.get('ActiveState') in ('active', 'activating') or state.get('Job'):
                if state.get('MainPID') not in ('0', None):
                    if not self.store.exists('worker-started.json'):
                        # Durable abort.json will be consumed by the original
                        # worker after startup; never signal an unbound PID.
                        return
                    started = self.store.read('worker-started.json')
                    need(started['boot_id'] == self.r.boot)
                    if not recovery: need(state['InvocationID'] == started['worker_invocation'])
                    self.r.run(['/usr/bin/systemctl', 'kill', '--kill-whom=main', '--signal=SIGUSR1', self._unit(recovery)])
                return
        need(not self.store.exists('recovery-dispatch-intent.json'))
        self.store.write_once('recovery-dispatch-intent.json', {'boot_id': self.r.boot})
        self.r.run(['/usr/bin/systemctl', 'start', '--no-block', self._unit(True)])

    def _dispatch(self, kind, unit):
        before = self.r.terminal(unit)
        name = kind + '-unit-intent.json'
        need(not self.store.exists(name))
        self.store.write_once(name, {'prior_invocation': before['InvocationID'], 'boot_id': self.r.boot,
            'started_monotonic_us': int(time.monotonic() * 1000000), 'issued_epoch': time.time()})
        self.r.run(['/usr/bin/systemctl', 'start', '--no-block', unit])
        until = time.monotonic() + 15
        while True:
            after = self.r.state(unit)
            if hex_string(after.get('InvocationID'), 32) and after['InvocationID'] != before['InvocationID']:
                need(int(after['ExecMainStartTimestampMonotonic']) >= self.store.read(name)['started_monotonic_us'])
                self.store.write_once(kind + '-unit-dispatch.json', {'invocation': after['InvocationID'], 'boot_id': self.r.boot})
                return
            need(time.monotonic() < until)
            time.sleep(.25)

    def _unit_status(self, kind, unit):
        need(self.store.exists(kind + '-unit-dispatch.json'))
        original = self.store.read(kind + '-unit-dispatch.json')
        need(original['boot_id'] == self.r.boot)
        value = self.r.state(unit)
        need(value['InvocationID'] == original['invocation'])
        return value

    def start_mirror(self, transaction, payload):
        need(transaction == self.transaction)
        need(self.store.read('status.json')['state'] == 'released')
        sealed = self.store.read('capture.json')['payload']['capture']
        need(payload['release'] == sealed['release'] and payload['backup_label'] == sealed['backup_label']
             and payload['publication_manifest_sha256'] == sealed['manifest_sha256'])
        self.r.verify_sources()
        pins(self.r, MIRROR_PINS, MIRROR, '')
        for unit in runtime.BACKUP_UNITS: self.r.terminal(unit)
        need(latest(self.r, 'full_backup')['backup_label'] == payload['backup_label'])
        self._dispatch('mirror', MIRROR)

    def mirror_status(self, transaction):
        need(transaction == self.transaction)
        value = self._unit_status('mirror', MIRROR)
        if not terminal(value): return {'state': 'mirroring'}
        need(value['Result'] == 'success' and value['ExecMainStatus'] == '0')
        receipt = latest(self.r, 'pgbackrest_mirror')
        expected = self.store.read('mirror-intent.json')['payload']['backup_label']
        intent = self.store.read('mirror-unit-intent.json')
        need(receipt['backup_label'] == receipt['mirrored_backup_label'] == expected)
        need(dt.datetime.fromisoformat(receipt['started_at'].replace('Z', '+00:00')).timestamp() >= int(intent['issued_epoch']))
        return {'state': 'mirror_complete', 'backup_label': expected, 'completed_at': receipt['completed_at']}

    def _native_retention(self):
        """An invalid selector never falls back to the legacy mirror proof."""
        from onedrive_retention import entry as retention_entry
        revision=retention_entry.verify_install()
        activation=retention_entry._load('activation')
        selected=activation.routing_state()
        if selected is None:return None
        context=retention_entry._native_dependencies(revision,'db')
        current=activation.require_activated(context)
        need(current.digest==selected.digest and current.role=='db'
             and current.bindings['paired_policy_sha256']==self.policy.digest
             and context.document['application_release']==self.policy.data['application_release'])
        return retention_entry,context,current

    def _native_bundle_acceptance(self, selected):
        """Authenticate the fixed same-TX native recovery, retirement and catalog.

        No evidence is accepted from the health/complete request payload. The
        original sealed capture supplies its label and publication manifest.
        This creates a genuinely named acceptance record, never a legacy mirror.
        """
        retention_entry,context,selector=selected
        state=self.store.read('status.json')
        need(state.get('state')=='released' and state.get('transaction')==self.transaction
             and state.get('policy_sha256')==self.policy.digest
             and state.get('release')==self.policy.data['application_release'])
        captured=validate_request(self.store.read('capture.json'))
        restored=validate_request(self.store.read('restored.json'))
        original=validate_request(self.store.read('begin-intent.json'))
        need(captured['operation']=='capture' and restored['operation']=='restored'
             and original['operation']=='begin'
             and all(record['transaction']==self.transaction and record['policy_sha256']==self.policy.digest
                     for record in (captured,restored,original))
             and all(record['payload'][key]==original['payload'][key] for record in (captured,restored)
                     for key in ('release','web_boot','web_invocation')))
        capture=captured['payload']['capture']
        need(capture['release']==self.policy.data['application_release']
             and restored['payload'].get('services_restored') is True
             and restored['payload'].get('capture_sha256')==digest(capture))
        coordinator=retention_entry._load('coordinator');endpoint=retention_entry._load('endpoint')
        catalog=retention_entry._load('catalog')
        policy=coordinator.Policy(retention_entry._protected_document(coordinator.PRIVATE_POLICY,0o600)[1])
        need(policy.digest==context.bindings['standalone_policy_sha256'])
        with provider.repository_lock() as (fd,_identity):
            adapter=endpoint.NativeDB(policy)
            def observed(stage):
                query=endpoint.request(policy.digest,self.transaction,'status',{'stage':stage,'receipt_facts':{}})
                value=adapter.observe(query)
                endpoint.validate_response(value,query)
                need(value['state']=='complete');return value
            native,retirement=observed('restore'),observed('retire')
            need(native['facts'].get('native_restore_accepted') is True
                 and native['facts'].get('backup_label')==capture['backup_label'])
            identifier=native['facts']['archive_sha256']
            store=catalog.NativeStore(fd)
            with store.io.locked():
                records=store.records();retired=store.retired(records)
                need(identifier in records and identifier not in retired)
                record=records[identifier];entry=record['entry'];evidence=record['evidence']
                need(entry['archive_sha256']==identifier and entry['native_restore_receipt_sha256']==native['receipt_sha256']
                     and evidence['native']['transaction']==self.transaction
                     and evidence['native']['archive_sha256']==identifier
                     and evidence['native']['backup_label']==capture['backup_label']
                     and evidence['native']['native_restore_accepted'] is True
                     and evidence['download']['transaction']==self.transaction
                     and evidence['download']['backup_label']==capture['backup_label'])
                record_sha256=digest(record)
            store.io.require_repository_lock()
        current=retention_entry._load('activation').require_activated(
            retention_entry._native_dependencies(retention_entry.verify_install(),'db'))
        need(current.digest==selector.digest and current.bindings==selector.bindings)
        accepted={'schema':'careertrellis.paired-native-db-bundle-accepted/v1','transaction':self.transaction,
            'policy_sha256':self.policy.digest,'retention_policy_sha256':policy.digest,
            'activation_sha256':selector.digest,'release':capture['release'],'backup_label':capture['backup_label'],
            'publication_manifest_sha256':capture['manifest_sha256'],'archive_sha256':identifier,
            'native_receipt_sha256':native['receipt_sha256'],'restore_status_sha256':digest(native),
            'retirement_status_sha256':digest(retirement),'catalog_acceptance_sha256':record_sha256}
        if self.store.exists('bundle-accepted.json'):need(self.store.read('bundle-accepted.json')==accepted)
        else:self.store.write_once('bundle-accepted.json',accepted)
        return accepted

    def start_health(self, transaction):
        need(transaction==self.transaction)
        selected=self._native_retention()
        if selected is None:need(self.mirror_status(transaction)['state'] == 'mirror_complete')
        else:self._native_bundle_acceptance(selected)
        pins(self.r, HEALTH_PINS, HEALTH, '/etc/systemd/system/' + HEALTH + '.d/50-backup-reporting.conf')
        for unit in runtime.BACKUP_UNITS: self.r.terminal(unit)
        self._dispatch('health', HEALTH)

    def health_status(self, transaction):
        need(transaction == self.transaction)
        pins(self.r, HEALTH_PINS, HEALTH, '/etc/systemd/system/' + HEALTH + '.d/50-backup-reporting.conf')
        need(hashlib.sha256(runtime.protected(runtime.CORE, 1048576)).hexdigest() == runtime.PINS['db'][runtime.CORE], 'db_core_drift')
        value = self._unit_status('health', HEALTH)
        if not terminal(value): return {'state': 'health_running'}
        need(value['Result'] == 'success' and value['ExecMainStatus'] == '0')
        health = json.loads(self.r.run(['/usr/local/sbin/careertrellis-backup-health', 'status']))
        need(health['result'] == 'ok' and health['failure_codes'] == [])
        issued = self.store.read('health-unit-intent.json')['issued_epoch']
        need(dt.datetime.fromisoformat(health['evaluated_at'].replace('Z', '+00:00')).timestamp() >= int(issued))
        try:
            self._reporter_accepted(issued, self.store.read('health-unit-dispatch.json')['invocation'])
        except Refused as exc:
            if str(exc) == 'reporter_pending' and time.time() <= dt.datetime.fromisoformat(health['evaluated_at'].replace('Z', '+00:00')).timestamp() + 120:
                return {'state': 'health_running', 'normal_report_accepted': False}
            raise
        return {'state': 'health_complete', 'evaluated_at': health['evaluated_at'], 'normal_report_accepted': True}

    def _reporter_accepted(self, issued, invocation):
        reporter = 'careertrellis-backup-reporter@fcfeb0bba27453db1d7300fb9abaf32497ff28e1.service'
        configured = self.r.run(['/usr/bin/systemctl', 'show', HEALTH, '--property=OnSuccess', '--value']).decode().strip().split()
        need(configured == [reporter])
        def events(unit, since):
            raw = self.r.run(['/usr/bin/journalctl', '-u', unit, '--since', '@' + str(int(since)), '-n', '60', '--no-pager', '-o', 'json'])
            result = []
            for line in raw.splitlines():
                try:
                    row = json.loads(line); message = json.loads(row.get('MESSAGE', ''))
                    if isinstance(message, dict):
                        result.append((int(row['__REALTIME_TIMESTAMP']) / 1000000, row.get('_SYSTEMD_INVOCATION_ID', ''), message))
                except (ValueError, TypeError, KeyError):
                    pass
            return result
        observations = events(HEALTH, issued)
        accepted = [(t, i, v) for t, i, v in observations if i == invocation and v.get('surface') == 'database'
                    and v.get('schema') == 'careertrellis.backup-health-status/v1' and v.get('result') == 'ok' and v.get('failure_codes') == []]
        if not accepted: raise Refused('reporter_pending')
        need(len(accepted) == 1)
        observed, _, _ = accepted[0]
        need(observed >= issued)
        status = self.r.state(reporter)
        if not terminal(status): raise Refused('reporter_pending')
        need(status['Result'] == 'success' and status['ExecMainStatus'] == '0' and hex_string(status['InvocationID'], 32))
        delivered = [(t, i, v) for t, i, v in events(reporter, observed) if i == status['InvocationID']
                     and v.get('observation') == 'healthy' and v.get('provider_acceptance') == 'accepted'
                     and v.get('heartbeat_acceptance') == 'accepted']
        if not delivered: raise Refused('reporter_pending')
        need(len(delivered) == 1 and observed <= delivered[0][0] <= observed + 120)
        need(not any(t > observed and i != invocation and v.get('schema') == 'careertrellis.backup-health-status/v1'
                     for t, i, v in observations))

    def complete(self, transaction, payload):
        need(transaction == self.transaction)
        state = self.store.read('status.json')
        need(state['state'] == 'released' and state['transaction'] == transaction)
        selected=self._native_retention()
        if selected is None:
            need(payload == self.store.read('mirror-intent.json')['payload'])
            need(self.mirror_status(transaction)['state'] == 'mirror_complete')
        else:
            accepted=self._native_bundle_acceptance(selected)
            need(payload=={'release':accepted['release'],'backup_label':accepted['backup_label'],
                           'publication_manifest_sha256':accepted['publication_manifest_sha256']})
        health=self.health_status(transaction)
        need(health['state'] == 'health_complete'
             and (selected is None or health.get('normal_report_accepted') is True))
        self.store.write_once('complete-result.json', {'transaction': transaction, 'policy_sha256': self.policy.digest,
            'backup_label': payload['backup_label'], 'manifest_sha256': payload['publication_manifest_sha256']})
        self._write_status('complete', services_restored=True)


class DBWorker:
    def __init__(self, policy, transaction, r=None):
        self.policy, self.transaction = policy, transaction
        self.r = r or runtime.HostRuntime(policy, 'db', transaction, worker=True)
        self.store = self.r.journal
        self.abort_requested = False
        self.original = None

    def status(self, state, **fields):
        identity = self.original or {'boot_id': self.r.boot, 'worker_invocation': self.r.invocation}
        value = self.r.status(state, **identity, **fields)
        self.r.global_status(state, result='failed' if state == 'operator_required' else 'running',
            services_restored=state in ('released', 'aborted'))
        return value

    def message(self, name):
        value = validate_request(self.store.read(name + '.json'))
        begin = self.store.read('begin-intent.json')
        need(value['transaction'] == self.transaction and value['policy_sha256'] == self.policy.digest
             and value['operation'] == name)
        need(all(value['payload'][key] == begin['payload'][key] for key in ('release', 'web_boot', 'web_invocation')))
        return value['payload']

    def wait(self, name, seconds, abortable=True):
        deadline = time.monotonic() + seconds
        while not self.store.exists(name + '.json'):
            self.r.check()
            if abortable and (self.abort_requested or self.store.exists('abort.json')):
                raise Refused('abort_requested')
            need(time.monotonic() < deadline, 'peer_wait_expired')
            time.sleep(1)
        return self.message(name)

    def observe_backup(self, allow_failed=False):
        intent = self.store.read('backup-intent.json')
        need(intent['boot_id'] == self.r.boot)
        if self.store.exists('backup-invocation.json'):
            invocation = self.store.read('backup-invocation.json')['invocation']
        else:
            current = self.r.state(runtime.FULL)
            need(hex_string(current.get('InvocationID'), 32) and current['InvocationID'] != intent['prior_invocation']
                 and int(current['ExecMainStartTimestampMonotonic']) >= intent['started_monotonic_us'], 'backup_outcome_unknown')
            invocation = current['InvocationID']
            self.store.write_once('backup-invocation.json', {'invocation': invocation})
        deadline = time.monotonic() + self.policy.data['backup_wait_seconds']
        while True:
            self.r.check()
            value = self.r.state(runtime.FULL)
            need(value['InvocationID'] == invocation, 'backup_invocation_changed')
            if terminal(value):
                self.r.terminal(runtime.FULL)
                if not allow_failed: need(value['Result'] == 'success' and value['ExecMainStatus'] == '0', 'backup_failed')
                return invocation
            need(time.monotonic() < deadline, 'backup_outcome_unknown')
            time.sleep(2)

    def abort(self):
        if self.store.exists('backup-intent.json'):
            self.observe_backup(allow_failed=True)
        for unit in runtime.BACKUP_UNITS: self.r.terminal(unit)
        self.status('abort_ready')
        restored = self.wait('restored', self.policy.data['backup_wait_seconds'], abortable=False)
        need(restored['services_restored'] is True)
        self.r.fence.close(); self.r.fence = None
        return self.status('aborted')

    def run(self, recovery_only=False):
        try:
            self.r.verify_sources()
            self.r.fence = runtime.Fence(runtime.MAINTENANCE, 0o600).acquire()
            if self.store.exists('worker-started.json'):
                self.original = self.store.read('worker-started.json')
                need(self.original['boot_id'] == self.r.boot, 'db_boot_changed')
                return self.abort()
            need(not recovery_only, 'original_worker_unknown')
            self.original = {'boot_id': self.r.boot, 'worker_invocation': self.r.invocation}
            self.store.write_once('worker-started.json', self.original)
            # Recheck under the acquired maintenance boundary before advertising
            # fenced readiness: the earlier endpoint preflight was only a snapshot.
            # Recovery never changes/uses retention or starts another backup.
            need(provider.retention_facts() == self.policy.data['retention_policy'], 'retention_policy_drift')
            self.status('fenced')
            stopped = self.wait('quiesced', 300)
            facts_ok(self.policy, provider.database_facts(), stopped=True)
            for unit in runtime.BACKUP_UNITS: self.r.terminal(unit)
            if self.store.exists('abort.json') or self.abort_requested: return self.abort()
            prior = self.r.state(runtime.FULL)
            self.store.write_once('backup-intent.json', {'boot_id': self.r.boot, 'prior_invocation': prior['InvocationID'],
                'started_monotonic_us': int(time.monotonic() * 1000000)})
            self.r.run(['/usr/bin/systemctl', 'start', '--no-block', runtime.FULL])
            # Await the original dispatch's new invocation, never another start.
            deadline = time.monotonic() + 30
            while True:
                current = self.r.state(runtime.FULL)
                if current['InvocationID'] != prior['InvocationID'] and hex_string(current.get('InvocationID'), 32): break
                need(time.monotonic() < deadline, 'backup_outcome_unknown'); time.sleep(.25)
            need(int(current['ExecMainStartTimestampMonotonic']) >= self.store.read('backup-intent.json')['started_monotonic_us'])
            self.store.write_once('backup-invocation.json', {'invocation': current['InvocationID']})
            invocation = self.observe_backup()
            if self.store.exists('abort.json') or self.abort_requested: return self.abort()
            receipt = latest(self.r, 'full_backup')
            pair = {key: receipt[key] for key in ('backup_label', 'started_at', 'completed_at')}
            pair['receipt_sha256'] = digest(receipt)
            runtime.validate_pair(pair, stopped['quiesced_at'], time.time())
            self.status('pair_ready', pair=pair, backup_invocation=invocation)
            captured = self.wait('capture', self.policy.data['backup_wait_seconds'])['capture']
            runtime.validate_capture(captured, pair['backup_label'], stopped['quiesced_at'], time.time())
            need(captured['object_count'] <= self.policy.data['max_capture_objects'] and captured['ciphertext_bytes'] <= self.policy.data['max_capture_bytes'])
            self.status('sealed', capture_sha256=digest(captured))
            restored = self.wait('restored', 300)
            need(restored['services_restored'] is True and restored.get('capture_sha256') == digest(captured))
            self.r.terminal(runtime.FULL)
            need(self.r.state(runtime.FULL)['InvocationID'] == invocation)
            self.r.fence.close(); self.r.fence = None
            return self.status('released')
        except Exception:
            try:
                if self.original and self.original['boot_id'] == self.r.boot and self.r.fence:
                    return self.abort()
            except Exception:
                pass
            return self.status('operator_required', operator_required=True)


def db_worker(policy, transaction, recovery_only=False):
    worker = DBWorker(policy, transaction)
    old = signal.signal(signal.SIGUSR1, lambda *_: setattr(worker, 'abort_requested', True))
    try:
        result = worker.run(recovery_only)
        if result.get('operator_required'):
            # Do not let a deadline release an unresolved maintenance boundary.
            while True:
                if worker.r.fence: worker.r.fence.check()
                time.sleep(30)
        return result
    finally:
        signal.signal(signal.SIGUSR1, old)
