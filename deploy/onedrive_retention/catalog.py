"""Immutable acceptance records; no provider calls, deletion or simulated acceptance.

Pure validation authenticates nothing. Native admission additionally requires
protected native journals, a reviewed terminal-proof validator/collector, and a
protected provider object-identity receipt. Missing integration fails closed.
Historical acceptance records remain immutable. Only an authenticated complete
native retirement chain creates committed tombstones; other missing/unknown
provider objects require reconciliation.
"""
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import stat

from . import bundle, local_restore_plan as plans, policy


ROOT = Path('/var/lib/careertrellis/onedrive-retention/db/catalog')
SOURCE_ROOT = Path('/var/lib/careertrellis/onedrive-bundles')
RESTORE_ROOT = Path('/var/lib/careertrellis-onedrive-bundle-acceptance')
REPOSITORY_LOCK = Path('/run/lock/careertrellis-pgbackrest-repository.lock')
NAMESPACE = 'onedrive:job-applier-staging/postgresql-bundles'
MAX_RECORD = 128 * 1024
MAX_ENTRIES = 10000
PHASES = ('info', 'restore', 'start', 'probe', 'stop', 'checksums')
ENTRY_KEYS = {'id', 'accepted_at', 'archive_sha256', 'download_sha256', 'restore_archive_sha256',
    'native_restore_receipt_sha256', 'complete', 'current', 'dependencies_resolved', 'dependencies', 'objects'}


class CatalogRefused(ValueError): pass


def need(value, code='bundle_catalog_refused'):
    if not value: raise CatalogRefused(code)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def decode(raw, limit=MAX_RECORD):
    need(type(raw) is bytes and 0 < len(raw) <= limit)
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result); result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: need(False))
        need(raw == bundle.canonical(value)); return value
    except CatalogRefused: raise
    except Exception: raise CatalogRefused('bundle_catalog_refused') from None


def timestamp(value):
    try: return policy.timestamp(value)
    except Exception: raise CatalogRefused('bundle_catalog_timestamp_refused') from None


def hex_value(value, length=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{%d}' % length, value) is not None


def keys(value, expected): need(type(value) is dict and set(value) == set(expected))


def retirement_expiry_mode(operation,complete):
    """Bind receipt mode; old mode-less journals mean recoverable only."""
    mode=operation.get('expiry_mode','recoverable_manual')
    need(type(mode) is str and mode in ('recoverable_manual','conditional_permanent'),
         'retirement_expiry_mode_mismatch')
    tagged='expiry_mode' in operation
    need(('expiry_mode' in complete)==tagged and complete.get('expiry_mode','recoverable_manual')==mode,
         'retirement_expiry_mode_mismatch')
    return mode


def validate_retirement_object_mode(operation,intent,ack,terminal,object_id,request_sha256):
    """Consistency only; caller must separately authenticate the native chain."""
    mode=operation.get('expiry_mode','recoverable_manual');tagged='expiry_mode' in operation
    need(mode in ('recoverable_manual','conditional_permanent'))
    for value in (intent,terminal):
        need(('expiry_mode' in value)==tagged and value.get('expiry_mode','recoverable_manual')==mode,
             'retirement_expiry_mode_mismatch')
    expected={'result':'deleted','object_id':object_id,'request_id':request_sha256,
              'recoverable':mode=='recoverable_manual','permanent':mode=='conditional_permanent'}
    if tagged:expected['expiry_mode']=mode
    need(ack==expected and ack['recoverable'] is expected['recoverable']
         and ack['permanent'] is expected['permanent'],'retirement_expiry_mode_mismatch')


def read_identity(io, transaction):
    """Select latest immutable numbered witness, refusing later partial attempts."""
    need(hex_value(transaction,32)); source = SOURCE_ROOT/transaction
    names = io.names_at(source)
    numbered = sorted(name for name in names if re.fullmatch(r'bundle-object-identity-[0-9]{2}\.json',name))
    if not numbered: return decode(io.read(source/'bundle-object-identity.json'))
    latest = int(numbered[-1][-7:-5]); need(1 <= latest <= 99)
    attempts = [int(name[-2:]) for name in names if re.fullmatch('identity-verification-[0-9]{2}',name)]
    need(all(number <= latest for number in attempts), 'later_identity_attempt_requires_reconciliation')
    raw = io.read(source/numbered[-1])
    receipt = io.read(source/('identity-verification-%02d'%latest)/'receipt.json')
    need(raw == receipt,'identity_alias_receipt_mismatch')
    return decode(raw)


def process_identity(value):
    keys(value, ('pid', 'start_ticks', 'process_group', 'boot_id'))
    need(type(value['pid']) is int and value['pid'] > 1
         and all(type(value[key]) is int and value[key] > 0 for key in ('start_ticks', 'process_group'))
         and type(value['boot_id']) is str and re.fullmatch(
             '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value['boot_id']))


def acceptance_entry(plan, manifest, download_raw, native_raw, identity, accepted_at, *, dependencies=()):
    """Validate supplied claims only; never use this as native authentication."""
    now = timestamp(accepted_at)
    try:
        plans.validate_plan(plan); bundle.validate_manifest(manifest)
        download, native = decode(download_raw), decode(native_raw)
        keys(download, ('schema', 'result', 'transaction', 'backup_label', 'generation_sha256',
            'archive_sha256', 'archive_bytes', 'manifest_sha256', 'native_restore_accepted',
            'completed_at', 'destination', 'quota_after'))
        need(download['schema'] == 'careertrellis.onedrive-bundle-download/v1'
             and download['result'] == 'download_verified' and download['native_restore_accepted'] is False)
        need(type(download['archive_bytes']) is int and 0 < download['archive_bytes'] <= bundle.MAX_BYTES
             and download['manifest_sha256'] == sha(bundle.canonical(manifest))
             and download['destination'] == NAMESPACE + '/' + plan['archive_sha256'] + '/repository.tar')
        keys(download['quota_after'], ('total', 'free')); quota = download['quota_after']
        need(type(quota['total']) is int and type(quota['free']) is int
             and quota['total'] > 0 and 0 <= quota['free'] <= quota['total'])
        native_v2 = native.get('schema') == 'careertrellis.onedrive-native-local-restore/v2'
        keys(native, ('schema', 'result', 'transaction', 'attempt', 'archive_sha256', 'generation_sha256',
            'backup_label', 'plan_sha256', 'download_receipt_sha256', 'prior_reconciliation_sha256',
            'native_restore_accepted', 'postmaster_terminal', 'checksums_verified', 'cleanup_performed',
            'elapsed_seconds', 'completed_at') + (('info_metadata_sha256',) if native_v2 else ()))
        if native_v2: need(hex_value(native['info_metadata_sha256']))
        need(native['schema'] in ('careertrellis.onedrive-native-local-restore/v1',
                                  'careertrellis.onedrive-native-local-restore/v2')
             and native['result'] == 'verified' and native['native_restore_accepted'] is True
             and native['postmaster_terminal'] is True and native['checksums_verified'] is True
             and native['cleanup_performed'] is False, 'actual_native_restore_receipt_required')
        need(type(native['attempt']) is int and native['attempt'] == plan['attempt']
             and native['plan_sha256'] == plans.digest(plan)
             and native['download_receipt_sha256'] == sha(download_raw))
        prior = native['prior_reconciliation_sha256']
        need(prior is None if native['attempt'] == 1 else hex_value(prior))
        need(type(native['elapsed_seconds']) in (int, float) and math.isfinite(native['elapsed_seconds'])
             and native['elapsed_seconds'] >= 0)
        for field in ('transaction', 'backup_label', 'generation_sha256', 'archive_sha256'):
            need(download[field] == native[field] == plan[field])
        need(manifest['generation_sha256'] == plan['generation_sha256']
             and manifest['backup_label'] == plan['backup_label'])
        need(timestamp(download['completed_at']) <= timestamp(native['completed_at']) <= now)
        identity_keys = {'schema', 'observed_at', 'download_receipt_sha256', 'path', 'object_id',
                        'size', 'sha256', 'identity_verified_before_after_download'}
        if identity.get('schema') == 'careertrellis.onedrive-bundle-identity/v2':
            from . import graph_delete
            identity_keys |= {'etag', 'etag_verified_before_after_download'}
            need(identity.get('etag_verified_before_after_download') is True
                 and graph_delete.valid_etag(identity.get('etag')))
        keys(identity, identity_keys)
        need(identity['schema'] in ('careertrellis.onedrive-bundle-identity/v1',
                                   'careertrellis.onedrive-bundle-identity/v2')
             and identity['identity_verified_before_after_download'] is True,
             'provider_identity_receipt_required')
        need(identity['download_receipt_sha256'] == sha(download_raw)
             and identity['path'] == download['archive_sha256'] + '/repository.tar'
             and type(identity['size']) is int and identity['size'] == download['archive_bytes']
             and identity['sha256'] == download['archive_sha256'])
        need(type(identity['object_id']) is str and 0 < len(identity['object_id']) <= 512
             and all(32 <= ord(char) < 127 for char in identity['object_id']))
        observed = timestamp(identity['observed_at'])
        need(timestamp(download['completed_at']) <= observed <= now
             and (now - observed).total_seconds() <= policy.MAX_INVENTORY_AGE_SECONDS,
             'provider_identity_receipt_stale')
    except CatalogRefused: raise
    except Exception: raise CatalogRefused('bundle_catalog_refused') from None
    need(type(dependencies) in (list, tuple) and len(dependencies) <= MAX_ENTRIES
         and all(hex_value(value) and value != plan['archive_sha256'] for value in dependencies)
         and len(set(dependencies)) == len(dependencies))
    archive = plan['archive_sha256']
    entry = {'id': archive, 'accepted_at': accepted_at, 'archive_sha256': archive,
        'download_sha256': archive, 'restore_archive_sha256': archive,
        'native_restore_receipt_sha256': sha(native_raw), 'complete': True, 'current': False,
        'dependencies_resolved': True, 'dependencies': sorted(dependencies),
        'objects': [{key: identity[key] for key in ('path', 'object_id', 'size', 'sha256')}]}
    return {'schema': 'careertrellis.onedrive-validated-acceptance-claims/v1',
            'authenticated': False, 'entry': entry}


def verify_journals(io, root, ownership, native, native_raw, terminal_raw, now):
    if native.get('schema') == 'careertrellis.onedrive-native-local-restore/v2':
        need(hex_value(native.get('info_metadata_sha256'))
             and sha(io.read(root / 'info-metadata.json')) == native['info_metadata_sha256'])
    keys(ownership, ('schema', 'owner', 'plan', 'plan_sha256', 'root_identity',
                     'download_receipt_sha256', 'web_preflight_sha256'))
    need(ownership['schema'] == 'careertrellis.native-local-restore-ownership/v1')
    process_identity(ownership['owner'])
    plan = ownership['plan']; plans.validate_plan(plan)
    need(ownership['plan_sha256'] == native['plan_sha256'] == plans.digest(plan)
         and ownership['download_receipt_sha256'] == native['download_receipt_sha256']
         and hex_value(ownership['web_preflight_sha256']))
    need(type(ownership['root_identity']) is list and len(ownership['root_identity']) >= 2
         and ownership['root_identity'][:2] == list(io.directory_identity(root)))
    for phase in PHASES:
        intent = decode(io.read(root / (phase + '-intent.json')))
        keys(intent, ('owner', 'plan_sha256', 'phase', 'observed_at'))
        need(intent['owner'] == ownership['owner'] and intent['phase'] == phase
             and intent['plan_sha256'] == native['plan_sha256']
             and timestamp(intent['observed_at']) <= timestamp(native['completed_at']))
        dispatch = decode(io.read(root / (phase + '-dispatch.json')))
        terminal = decode(io.read(root / (phase + '-terminal.json')))
        if type(dispatch) is dict and set(dispatch) == {'pid', 'boot_id', 'identity_unavailable_child_terminal'}:
            need(type(dispatch['pid']) is int and dispatch['pid'] > 1
                 and dispatch['identity_unavailable_child_terminal'] is True)
        else: process_identity(dispatch)
        need(dispatch['boot_id'] == ownership['owner']['boot_id']
             and type(terminal) is dict and type(terminal.get('exit_code')) is int
             and terminal == dict(dispatch, exit_code=0))
    need(not any(name.startswith(('contained-', 'failure', 'operator-required')) for name in io.names_at(root)),
         'native_failure_marker_present')
    # Schema/validator deliberately supplied by reviewed native code, never by
    # a caller-selected adapter or its assertion of a successful simulation.
    from . import native_restore
    validator = getattr(native_restore, 'validate_terminal_proof', None)
    need(callable(validator), 'native_terminal_validator_unavailable')
    proof = decode(terminal_raw)
    try: stop_intent = decode(io.read(root / 'unit-stop-intent.json'))
    except FileNotFoundError: raise CatalogRefused('native_terminal_stop_intent_required') from None
    verdict = validator(proof, native, ownership['owner'], now=now, stop_intent=stop_intent)
    need(type(verdict) is dict and verdict == proof, 'native_terminal_proof_refused')
    observed = timestamp(proof['observed_at'])
    need(observed <= now and (now - observed).total_seconds() <= policy.MAX_INVENTORY_AGE_SECONDS,
         'native_terminal_proof_stale')


def inventory_objects(value, now):
    keys(value, ('schema', 'namespace', 'observed_at', 'objects'))
    need(value['schema'] == 'careertrellis.onedrive-bundle-inventory/v1'
         and value['namespace'] == NAMESPACE and type(value['objects']) is list
         and len(value['objects']) <= MAX_ENTRIES)
    observed = timestamp(value['observed_at'])
    need(observed <= now and (now - observed).total_seconds() <= policy.MAX_INVENTORY_AGE_SECONDS,
         'fresh_provider_inventory_required')
    result, identities = {}, set()
    for obj in value['objects']:
        keys(obj, ('path', 'object_id', 'size', 'sha256'))
        need(type(obj['path']) is str and re.fullmatch(r'[0-9a-f]{64}/repository\.tar', obj['path'])
             and type(obj['object_id']) is str and 0 < len(obj['object_id']) <= 512
             and all(32 <= ord(char) < 127 for char in obj['object_id'])
             and type(obj['size']) is int and 0 < obj['size'] <= bundle.MAX_BYTES and hex_value(obj['sha256'])
             and obj['path'] not in result and obj['object_id'] not in identities)
        result[obj['path']] = dict(obj); identities.add(obj['object_id'])
    need(sum(obj['size'] for obj in result.values()) <= policy.NAMESPACE_BUDGET)
    return result


class Catalog:
    """Injected storage is for offline tests; native callers use NativeStore."""
    def __init__(self, storage, clock=None):
        self.io = storage; self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))

    def records(self):
        names = self.io.names('accepted'); need(len(names) <= MAX_ENTRIES)
        need(names == self.io.names('intents') == self.io.names('complete'),
             'catalog_interruption_requires_reconciliation')
        result = {}
        for name in sorted(names):
            need(re.fullmatch(r'[0-9a-f]{64}\.json', name))
            raw = self.io.read_store('accepted/' + name); record = decode(raw)
            keys(record, ('schema', 'authentication', 'entry', 'evidence'))
            need(record['schema'] == 'careertrellis.onedrive-bundle-acceptance/v1'
                 and record['authentication'] == self.io.authentication)
            entry = record['entry']; keys(entry, ENTRY_KEYS); evidence = record['evidence']
            keys(evidence, ('download', 'native', 'identity', 'terminal_proof_sha256'))
            need(entry['id'] == entry['archive_sha256'] == name[:-5]
                 and entry['complete'] is True and entry['current'] is False
                 and entry['dependencies_resolved'] is True)
            need(entry['download_sha256'] == entry['restore_archive_sha256'] == entry['archive_sha256']
                 and entry['native_restore_receipt_sha256'] == sha(bundle.canonical(evidence['native'])))
            need(evidence['native']['schema'] in ('careertrellis.onedrive-native-local-restore/v1',
                                                 'careertrellis.onedrive-native-local-restore/v2')
                 and evidence['native']['native_restore_accepted'] is True
                 and evidence['native']['result'] == 'verified'
                 and hex_value(evidence['terminal_proof_sha256']))
            need(evidence['identity']['download_receipt_sha256'] == sha(bundle.canonical(evidence['download'])))
            intent = decode(self.io.read_store('intents/' + name))
            complete = decode(self.io.read_store('complete/' + name))
            need(intent == {'record_sha256': sha(raw), 'archive_sha256': entry['id']}
                 and complete == {'intent_sha256': sha(bundle.canonical(intent)), 'record_sha256': sha(raw)})
            result[entry['id']] = record
        return result

    def accept(self, transaction, attempt=1, *, dependencies=()):
        need(hex_value(transaction, 32) and type(attempt) is int and 1 <= attempt <= 9)
        need(self.io.authentication != 'root-protected-native-journals' or type(self.io) is NativeFiles,
             'caller_selected_native_catalog_refused')
        source = SOURCE_ROOT / transaction
        root = RESTORE_ROOT / (transaction + ('' if attempt == 1 else '-%02d' % attempt))
        try:
            download_raw = self.io.read(source / 'downloaded-receipt.json')
            native_raw = self.io.read(root / 'complete.json')
            manifest = decode(self.io.read(source / 'downloaded-manifest.json', bundle.MAX_MANIFEST), bundle.MAX_MANIFEST)
            try: identity = read_identity(self.io, transaction)
            except FileNotFoundError: raise CatalogRefused('provider_identity_receipt_required') from None
            if self.io.authentication == 'root-protected-native-journals':
                need(identity.get('schema') == 'careertrellis.onedrive-bundle-identity/v2',
                     'native_etag_byte_witness_required')
            try: terminal_raw = self.io.read(root / 'unit-terminal-proof.json')
            except FileNotFoundError: raise CatalogRefused('native_terminal_proof_required') from None
            ownership = decode(self.io.read(root / 'ownership.json'))
            native = decode(native_raw); plan = ownership.get('plan')
            need(type(plan) is dict and plan.get('transaction') == transaction and plan.get('attempt') == attempt
                 and plan.get('attempt_root') == root.as_posix())
            now = self.clock(); verify_journals(self.io, root, ownership, native, native_raw, terminal_raw, now)
            with self.io.locked():
                existing = self.records(); now = self.clock()
                claimed = acceptance_entry(plan, manifest, download_raw, native_raw, identity,
                    now.isoformat(), dependencies=dependencies)
                entry = claimed['entry']; identifier = entry['id']
                need(identifier not in existing, 'duplicate_archive_is_not_a_successor')
                need(all(key in existing and key not in self.retired(existing) for key in entry['dependencies']),
                     'unresolved_bundle_dependency')
                need(all(timestamp(record['entry']['accepted_at']) < now for record in existing.values()),
                     'ambiguous_acceptance_order')
                record = {'schema': 'careertrellis.onedrive-bundle-acceptance/v1',
                    'authentication': self.io.authentication, 'entry': entry,
                    'evidence': {'download': decode(download_raw), 'native': native, 'identity': identity,
                                 'terminal_proof_sha256': sha(terminal_raw)}}
                raw = bundle.canonical(record); need(len(raw) <= MAX_RECORD)
                intent = {'record_sha256': sha(raw), 'archive_sha256': identifier}; name = identifier + '.json'
                self.io.write_store('intents/' + name, bundle.canonical(intent))
                self.io.write_store('accepted/' + name, raw)
                self.io.write_store('complete/' + name, bundle.canonical({
                    'intent_sha256': sha(bundle.canonical(intent)), 'record_sha256': sha(raw)}))
                return {'schema': 'careertrellis.onedrive-catalog-admission/v1',
                    'archive_sha256': identifier, 'record_sha256': sha(raw),
                    'native_authenticated': type(self.io) is NativeFiles,
                    'retirement_performed': False}
        except CatalogRefused: raise
        except Exception: raise CatalogRefused('catalog_admission_requires_reconciliation') from None

    def add_hold(self, hold):
        keys(hold, ('name', 'owner', 'reason', 'review_after', 'bundles'))
        need(type(hold['name']) is str and policy.NAME.fullmatch(hold['name']))
        for field in ('owner', 'reason'):
            need(type(hold[field]) is str and 0 < len(hold[field]) <= 512
                 and all(32 <= ord(char) < 127 for char in hold[field]))
        timestamp(hold['review_after'])
        need(type(hold['bundles']) is list and 0 < len(hold['bundles']) <= MAX_ENTRIES
             and all(hex_value(value) for value in hold['bundles'])
             and len(set(hold['bundles'])) == len(hold['bundles']))
        with self.io.locked():
            records = self.records(); retired = self.retired(records)
            need(all(value in records and value not in retired for value in hold['bundles']))
            self.io.write_store('holds/' + hold['name'] + '.json', bundle.canonical(hold))

    def snapshot(self, inventory, *, current_ids=()):
        fresh = inventory_objects(inventory, self.clock())
        with self.io.locked():
            records = self.records(); entries = []; expected = {}
            need(type(current_ids) in (tuple, list, set, frozenset) and all(value in records for value in current_ids))
            retired = self.retired(records)
            need(not set(current_ids) & retired, 'current_bundle_has_retirement_record')
            for identifier, record in records.items():
                if identifier in retired: continue
                entry = json.loads(bundle.canonical(record['entry'])); entry['current'] = identifier in current_ids
                need(timestamp(entry['accepted_at']) <= timestamp(inventory['observed_at']))
                entries.append(entry)
                for obj in entry['objects']:
                    need(obj['path'] not in expected); expected[obj['path']] = obj
            need(fresh == expected, 'missing_or_unregistered_objects_require_reconciliation')
            holds = []
            for name in sorted(self.io.names('holds')):
                need(type(name) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.json', name))
                value = decode(self.io.read_store('holds/' + name))
                need(value['name'] + '.json' == name
                     and not set(value['bundles']) & retired, 'held_bundle_has_retirement_record')
                holds.append(value)
            return {'schema': 'careertrellis.onedrive-bundle-catalog/v1',
                'observed_at': inventory['observed_at'], 'entries': entries, 'holds': holds}

    def retired(self, records):
        """Read immutable committed tombstones; never infer retirement from absence."""
        if not hasattr(self.io, 'supports_tombstones'): return set()
        names = self.io.names('tombstones')
        need(names == self.io.names('tombstone-intents') == self.io.names('tombstone-complete'),
             'retirement_catalog_interruption_requires_reconciliation')
        result = set()
        for name in names:
            need(re.fullmatch(r'[0-9a-f]{64}\.json', name) and name[:-5] in records)
            raw = self.io.read_store('tombstones/'+name); value = decode(raw)
            keys(value, ('schema', 'archive_sha256', 'acceptance_record_sha256',
                         'retirement_receipt_sha256', 'retired_at', 'authentication'))
            need(value['schema'] == 'careertrellis.onedrive-retired-bundle/v1'
                 and value['archive_sha256'] == name[:-5]
                 and value['acceptance_record_sha256'] == sha(bundle.canonical(records[name[:-5]]))
                 and value['authentication'] == self.io.authentication
                 and hex_value(value['retirement_receipt_sha256'])
                 and timestamp(value['retired_at']) <= self.clock())
            intent = decode(self.io.read_store('tombstone-intents/'+name))
            complete = decode(self.io.read_store('tombstone-complete/'+name))
            need(intent == {'record_sha256':sha(raw), 'archive_sha256':name[:-5]}
                 and complete == {'intent_sha256':sha(bundle.canonical(intent)), 'record_sha256':sha(raw)})
            result.add(name[:-5])
        return result

    def commit_retirement(self, transaction):
        """Authenticate an exact completed native chain before catalog retirement.

        The native adapter publishes every per-object intent/ack/absence record
        before operation-complete. No caller-supplied terminal claim is accepted.
        """
        need(hex_value(transaction, 32) and type(self.io) is NativeFiles
             and self.io.authentication == 'root-protected-native-journals')
        with self.io.locked():
            records = self.records(); retired = self.retired(records)
            operation = decode(self.io.read_retirement(transaction, 'operation-intent.json'))
            complete_raw = self.io.read_retirement(transaction, 'operation-complete.json')
            complete = decode(complete_raw)
            retirement_expiry_mode(operation,complete)
            need(operation.get('schema') == 'careertrellis.bundle-retirement-native-intent/v1'
                 and operation.get('transaction') == transaction and operation.get('simulation') is False
                 and operation.get('namespace') == NAMESPACE
                 and complete.get('schema') == 'careertrellis.bundle-retirement-native/v1'
                 and complete.get('transaction') == transaction
                 and complete.get('native_retirement_accepted') is True
                 and complete.get('result') == 'complete'
                 and complete.get('catalog_sha256') == operation.get('catalog_sha256')
                 and complete.get('retention_policy_sha256') == operation.get('retention_policy_sha256')
                 and complete.get('operation_intent_sha256') == sha(bundle.canonical(operation)))
            planned = operation['catalog']; planned_at = operation['plan']['planned_at']
            need(operation['catalog_sha256'] == sha(policy.canonical(planned)))
            from . import retire
            recalculated = retire.prepare(planned, planned_at)
            need(recalculated == operation['plan'])
            expected_ids = {row['bundle_id'] for row in recalculated['candidates']}
            need(not expected_ids & retired and expected_ids <= set(records))
            need({row['id'] for row in planned['entries']} == set(records)-retired)
            current_holds = [decode(self.io.read_store('holds/'+name)) for name in sorted(self.io.names('holds'))]
            need(current_holds == planned['holds'], 'holds_changed_since_retirement_plan')
            for entry in planned['entries']:
                original = dict(records[entry['id']]['entry']); original['current'] = entry['current']
                need(original == entry)
            terminals = []; count = 0
            for candidate in recalculated['candidates']:
                for obj in candidate['objects']:
                    prefix = 'object-%05d-' % count
                    intent_raw = self.io.read_retirement(transaction, prefix+'intent.json')
                    intent = decode(intent_raw); ack_raw = self.io.read_retirement(transaction, prefix+'ack.json')
                    ack = decode(ack_raw); terminal_raw = self.io.read_retirement(transaction, prefix+'terminal.json')
                    terminal = decode(terminal_raw)
                    dispatch = decode(self.io.read_retirement(transaction, prefix+'dispatch.json'))
                    validate_retirement_object_mode(operation,intent,ack,terminal,obj['object_id'],sha(intent_raw))
                    need(intent.get('schema') == 'careertrellis.bundle-object-retirement-intent/v1'
                         and intent.get('namespace') == NAMESPACE and intent.get('sequence') == count
                         and intent.get('operation_intent_sha256') == sha(bundle.canonical(operation))
                         and intent.get('transaction') == transaction and intent.get('object') == obj
                         and intent.get('bundle_id') == candidate['bundle_id'] and intent.get('simulation') is False
                         and dispatch == {'intent_sha256':sha(intent_raw), 'simulation':False}
                         and terminal.get('schema') == 'careertrellis.bundle-object-retirement-terminal/v1'
                         and terminal.get('transaction') == transaction and terminal.get('sequence') == count
                         and terminal.get('namespace') == NAMESPACE and terminal.get('bundle_id') == candidate['bundle_id']
                         and terminal.get('intent_sha256') == sha(intent_raw)
                         and terminal.get('dispatch_ack_sha256') == sha(ack_raw)
                         and terminal.get('object') == obj and terminal.get('absent') is True
                         and terminal.get('simulation') is False)
                    need(timestamp(terminal['absence_observed_at']) <= timestamp(complete['completed_at']) <= self.clock())
                    terminals.append(sha(terminal_raw)); count += 1
            need(complete.get('terminal_receipt_sha256') == terminals
                 and type(complete.get('deletions_performed')) is int and complete['deletions_performed'] == count)
            for identifier in sorted(expected_ids):
                record = {'schema':'careertrellis.onedrive-retired-bundle/v1', 'archive_sha256':identifier,
                    'acceptance_record_sha256':sha(bundle.canonical(records[identifier])),
                    'retirement_receipt_sha256':sha(complete_raw), 'retired_at':complete['completed_at'],
                    'authentication':self.io.authentication}
                raw = bundle.canonical(record); name = identifier+'.json'
                intent = {'record_sha256':sha(raw), 'archive_sha256':identifier}
                self.io.write_store('tombstone-intents/'+name, bundle.canonical(intent))
                self.io.write_store('tombstones/'+name, raw)
                self.io.write_store('tombstone-complete/'+name, bundle.canonical({
                    'intent_sha256':sha(bundle.canonical(intent)), 'record_sha256':sha(raw)}))
            return sorted(expected_ids)

    def retirement_plan(self, inventory, *, current_ids=()):
        catalog = self.snapshot(inventory, current_ids=current_ids)
        if len(catalog['entries']) < 2:
            return {'schema': 'careertrellis.onedrive-retirement-bootstrap/v1',
                'result': 'awaiting_two_unique_verified_bundles', 'catalog_sha256': sha(policy.canonical(catalog)),
                'retained_bundle_ids': sorted(entry['id'] for entry in catalog['entries']),
                'candidates': [], 'deletions_performed': 0, 'requires_locked_revalidation': True}
        return policy.plan_retirement(catalog, self.clock().isoformat())


class NativeFiles:
    """Fixed-root native storage. Existing canonical repository lock is required."""
    authentication = 'root-protected-native-journals'
    supports_tombstones = True

    def __init__(self, repository_lock_fd):
        need(hasattr(os, 'geteuid') and hasattr(os, 'getegid') and os.geteuid() == os.getegid() == 0
             and socket.gethostname() == 'job-applier-db-staging-01'
             and hasattr(os, 'O_NOFOLLOW') and os.open in os.supports_dir_fd,
             'native_catalog_host_refused')
        need(type(repository_lock_fd) is int and repository_lock_fd >= 0)
        self.repository_lock_fd = repository_lock_fd
        self.require_repository_lock()

    @contextmanager
    def directory(self, path, private=False):
        path = Path(path); need(path.is_absolute() and '..' not in path.parts)
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            current = Path('/')
            for part in path.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = child
                meta = os.fstat(fd)
                current /= part
                need(meta.st_uid == 0)
                if current == Path('/run/lock'):
                    need(meta.st_gid == 0 and stat.S_IMODE(meta.st_mode) == 0o1777)
                else: need(not meta.st_mode & 0o022)
            if private:
                meta = os.fstat(fd)
                need(meta.st_gid == 0 and stat.S_IMODE(meta.st_mode) == 0o700)
            yield fd
        finally: os.close(fd)

    def directory_identity(self, path):
        with self.directory(path) as fd:
            meta = os.fstat(fd); return meta.st_dev, meta.st_ino

    def require_repository_lock(self):
        with self.directory(REPOSITORY_LOCK.parent) as parent:
            path = os.stat(REPOSITORY_LOCK.name, dir_fd=parent, follow_symlinks=False)
            opened = os.fstat(self.repository_lock_fd)
            need(stat.S_ISREG(path.st_mode) and path.st_uid == path.st_gid == 0
                 and stat.S_IMODE(path.st_mode) == 0o600 and path.st_nlink == 1 and path.st_size == 0
                 and (path.st_dev, path.st_ino) == (opened.st_dev, opened.st_ino),
                 'canonical_repository_lock_required')
            key = (os.major(path.st_dev), os.minor(path.st_dev), path.st_ino)
            with open('/proc/locks', 'rb') as stream: raw = stream.read(2 * 1024 ** 2 + 1)
            need(len(raw) <= 2 * 1024 ** 2)
            found = False
            for line in raw.decode('ascii').splitlines():
                fields = line.split()
                for index, field in enumerate(fields):
                    match = re.fullmatch('([0-9a-f]+):([0-9a-f]+):([0-9]+)', field)
                    if not match: continue
                    identity = tuple(int(value, 16 if part < 2 else 10)
                                     for part, value in enumerate(match.groups()))
                    if identity == key:
                        need(index > 0 and fields[index - 1] == str(os.getpid())
                             and 'FLOCK' in fields and 'WRITE' in fields and '->' not in fields,
                             'canonical_repository_lock_required')
                        found = True
            need(found, 'canonical_repository_lock_required')

    def _protected(self, path, limit):
        with self.directory(Path(path).parent) as parent:
            before = os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False)
            fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                def identity(meta):
                    return (meta.st_dev, meta.st_ino, meta.st_uid, meta.st_gid, meta.st_mode,
                            meta.st_nlink, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)
                meta = os.fstat(fd)
                need(identity(meta) == identity(before) and stat.S_ISREG(meta.st_mode)
                     and meta.st_uid == meta.st_gid == 0 and meta.st_nlink == 1
                     and stat.S_IMODE(meta.st_mode) == 0o600 and 0 < meta.st_size <= limit)
                parts, remaining = [], meta.st_size
                while remaining:
                    raw = os.read(fd, min(65536, remaining)); need(bool(raw))
                    parts.append(raw); remaining -= len(raw)
                need(not os.read(fd, 1) and identity(os.fstat(fd)) == identity(meta)
                     and identity(os.stat(Path(path).name, dir_fd=parent, follow_symlinks=False)) == identity(meta))
                return b''.join(parts)
            finally: os.close(fd)

    def read(self, path, limit=MAX_RECORD):
        path = Path(path)
        source = re.fullmatch(re.escape(str(SOURCE_ROOT)) + r'/[0-9a-f]{32}/'
            r'(?:downloaded-receipt\.json|downloaded-manifest\.json|bundle-object-identity(?:-[0-9]{2})?\.json|identity-verification-[0-9]{2}/receipt\.json)', str(path))
        native = re.fullmatch(re.escape(str(RESTORE_ROOT)) + r'/[0-9a-f]{32}(?:-0[2-9])?/'
            r'(?:complete|ownership|info-metadata|unit-terminal-proof|unit-stop-intent|(?:info|restore|start|probe|stop|checksums)-(?:intent|dispatch|terminal))\.json', str(path))
        need(source or native, 'catalog_evidence_path_refused')
        return self._protected(path, limit)

    def names_at(self, root):
        with self.directory(root) as fd:
            import itertools
            with os.scandir(fd) as rows: names = [row.name for row in itertools.islice(rows, 257)]
            need(len(names) <= 256); return set(names)

    def _store_path(self, name):
        need(type(name) is str and re.fullmatch(
            r'(?:accepted|intents|complete|tombstones|tombstone-intents|tombstone-complete)/[0-9a-f]{64}\.json|holds/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.json', name))
        return ROOT / name

    def read_store(self, name): return self._protected(self._store_path(name), MAX_RECORD)

    def names(self, branch):
        need(branch in ('accepted', 'intents', 'complete', 'holds', 'tombstones', 'tombstone-intents', 'tombstone-complete'))
        with self.directory(ROOT / branch, private=True) as fd:
            import itertools
            with os.scandir(fd) as rows: values = [row.name for row in itertools.islice(rows, MAX_ENTRIES + 1)]
            need(len(values) <= MAX_ENTRIES); return set(values)

    def write_store(self, name, raw):
        self.require_repository_lock(); need(type(raw) is bytes and 0 < len(raw) <= MAX_RECORD)
        path = self._store_path(name)
        with self.directory(path.parent, private=True) as parent:
            fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            try:
                os.fchown(fd, 0, 0); os.fchmod(fd, 0o600); view = memoryview(raw)
                while view:
                    count = os.write(fd, view); need(count > 0); view = view[count:]
                os.fsync(fd)
            finally: os.close(fd)
            os.fsync(parent)

    @contextmanager
    def locked(self):
        import fcntl
        self.require_repository_lock()
        with self.directory(ROOT.parent, private=True) as parent:
            try: os.mkdir(ROOT.name, mode=0o700, dir_fd=parent)
            except FileExistsError: pass
            os.fsync(parent)
        with self.directory(ROOT, private=True) as root:
            for name in ('accepted', 'intents', 'complete', 'holds', 'tombstones', 'tombstone-intents', 'tombstone-complete'):
                try: os.mkdir(name, mode=0o700, dir_fd=root)
                except FileExistsError: pass
                with self.directory(ROOT / name, private=True): pass
            try:
                create = os.open('catalog.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=root)
            except FileExistsError: pass
            else:
                os.fchown(create, 0, 0); os.fchmod(create, 0o600); os.fsync(create); os.close(create)
            os.fsync(root)
            fd = os.open('catalog.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
            try:
                meta = os.fstat(fd)
                need(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0
                     and stat.S_IMODE(meta.st_mode) == 0o600 and meta.st_nlink == 1 and meta.st_size == 0)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
                after = os.stat('catalog.lock', dir_fd=root, follow_symlinks=False)
                need((after.st_dev, after.st_ino) == (meta.st_dev, meta.st_ino))
                self.require_repository_lock()
            finally: os.close(fd)

    def read_retirement(self, transaction, name):
        need(hex_value(transaction, 32) and type(name) is str and re.fullmatch(
            r'operation-(?:intent|complete)\.json|object-[0-9]{5}-(?:intent|dispatch|ack|terminal)\.json', name))
        self.require_repository_lock()
        return self._protected(ROOT.parent/'retirements'/transaction/name, MAX_RECORD)


class ExistingNativeFiles(NativeFiles):
    """Read-only evidence never initializes or repairs an absent catalog."""
    @contextmanager
    def locked(self):
        import fcntl
        self.require_repository_lock()
        expected={'accepted','intents','complete','holds','tombstones',
                  'tombstone-intents','tombstone-complete','catalog.lock'}
        with self.directory(ROOT,private=True) as root:
            need(set(os.listdir(root))==expected,'catalog_interruption_requires_reconciliation')
            for name in sorted(expected-{'catalog.lock'}):
                with self.directory(ROOT/name,private=True):pass
            fd=os.open('catalog.lock',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root)
            try:
                meta=os.fstat(fd)
                need(stat.S_ISREG(meta.st_mode) and meta.st_uid==meta.st_gid==0
                     and stat.S_IMODE(meta.st_mode)==0o600 and meta.st_nlink==1 and meta.st_size==0)
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                yield
                after=os.stat('catalog.lock',dir_fd=root,follow_symlinks=False)
                need((after.st_dev,after.st_ino,after.st_mode,after.st_uid,after.st_gid,after.st_nlink,after.st_size)==
                     (meta.st_dev,meta.st_ino,meta.st_mode,meta.st_uid,meta.st_gid,meta.st_nlink,meta.st_size)
                     and set(os.listdir(root))==expected)
                self.require_repository_lock()
            finally:os.close(fd)

    def write_store(self,*args,**kwargs):
        raise CatalogRefused('read_only_catalog_refuses_writes')


class NativeReadStore(Catalog):
    def __init__(self,repository_lock_fd):
        super().__init__(ExistingNativeFiles(repository_lock_fd))


class NativeStore(Catalog):
    """Native admission stays unavailable without the reviewed collectors/proof."""
    def __init__(self, repository_lock_fd):
        super().__init__(NativeFiles(repository_lock_fd))
