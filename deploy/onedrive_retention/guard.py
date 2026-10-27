"""Persistent-attempt assessment, fixed native histories and capacity admission.

Parsed evidence is not authentication. NativeProvider performs bounded provider
reads under an existing canonical lock and authenticated installed baseline.
The native admission path consumes protected stage intents plus fresh local and
authenticated peer histories. Unknown schemas or partial attempts block growth.
A free flock, absent unit or elapsed timeout never proves completion. Provider
reads never upload/delete; catalog initialization may create its fixed private
directories and lock during an explicitly admitted operation, never a poll.

The INFO-only exception permits its designated next attempt, not general history
resolution. An accepted replacement cannot silently clear an old failure.
A separate exact historical adapter can recognize
the retained source94 INFO-only failure after a protected preservation receipt
and fresh terminal/data/archive verification. It accepts no backup or retry.
"""
import datetime as dt
import hashlib
import re
import configparser
import io
import json
import os
from pathlib import Path
import socket
import stat
import time
import types
import sys
import math

from . import bundle, catalog, native_restore, policy, retire


COVERAGE = ('hydration', 'publication', 'native_restore', 'catalog_admission',
            'retirement', 'legacy_pg_mirror', 'legacy_object_mirror')
OPERATIONS = ('bundle_capture', 'bundle_publish', 'bundle_restore',
              'legacy_pg_mirror', 'legacy_object_mirror')
MAX_RECORDS = 10000
_PROVIDER_SEAL = object()
_POLICY_ARCHIVE_SEAL = object()
LEGACY_PAIRED_POLICY = '3dcd50ba5bbc6fdd3270a83c7dd255c35f86c18f9da5d0613a55c8a4be94c880'
LEGACY_PAIRED_RELEASE = '7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'


class GuardRefused(ValueError): pass


def need(value):
    if not value: raise GuardRefused('growth_guard_evidence_refused')


def capacity(total_bytes, free_bytes, source_bytes, candidate_bytes, namespace_bytes,
             namespace_new_bytes, *, reserve_percent=20, peak_multiplier=4,
             namespace_budget_bytes=policy.NAMESPACE_BUDGET):
    """Require account reserve plus at least 4x the larger next allocation.

    Namespace bytes include ALL accepted, held and staging objects. Planned new
    bundle bytes are added without credit for future deletions. Legacy mirrors
    still use the account guard but do not grow the new bundle namespace.
    """
    for value in (total_bytes, free_bytes, source_bytes, candidate_bytes,
                  namespace_bytes, namespace_new_bytes): policy.integer(value)
    need(total_bytes > 0 and free_bytes <= total_bytes)
    need(type(reserve_percent) is int and 20 <= reserve_percent <= 99
         and type(peak_multiplier) is int and 4 <= peak_multiplier <= 100
         and type(namespace_budget_bytes) is int and 0 < namespace_budget_bytes <= policy.NAMESPACE_BUDGET)
    reserve = (total_bytes * reserve_percent + 99) // 100
    peak = peak_multiplier * max(source_bytes, candidate_bytes)
    required = reserve + peak
    namespace_peak = namespace_bytes + namespace_new_bytes
    reasons = []
    if free_bytes < required: reasons.append('account_reserve_shortfall')
    if namespace_peak > namespace_budget_bytes: reasons.append('namespace_budget_exceeded')
    return {'allowed': not reasons, 'reasons': reasons, 'reserve_bytes': reserve,
            'projected_peak_bytes': peak, 'required_free_bytes': required,
            'shortfall_bytes': max(0, required - free_bytes),
            'namespace_peak_bytes': namespace_peak, 'namespace_budget_bytes': namespace_budget_bytes}


def fresh(value, now):
    observed = policy.timestamp(value)
    need(0 <= (now - observed).total_seconds() <= policy.MAX_INVENTORY_AGE_SECONDS)
    return observed


def optional_digest(value):
    if value is not None: policy._digest(value)


def attempt_record(value, now):
    policy._keys(value, ('schema', 'transaction', 'attempt', 'stage', 'archive_sha256',
        'observed_at', 'owner', 'journal', 'unit', 'restore', 'reconciliation'))
    need(value['schema'] == 'careertrellis.onedrive-persistent-attempt/v1'
         and type(value['transaction']) is str and re.fullmatch('[0-9a-f]{32}', value['transaction'])
         and type(value['attempt']) is int and 1 <= value['attempt'] <= 9
         and value['stage'] in COVERAGE)
    fresh(value['observed_at'], now); catalog.process_identity(value['owner'])
    optional_digest(value['archive_sha256'])
    journal = value['journal']
    policy._keys(journal, ('state', 'phase', 'intent_sha256', 'dispatch_sha256',
                          'terminal_sha256', 'exit_code', 'receipt_sha256'))
    need(journal['state'] in ('complete', 'failed', 'running', 'unknown')
         and type(journal['phase']) is str and policy.NAME.fullmatch(journal['phase']))
    policy._digest(journal['intent_sha256'])
    for field in ('dispatch_sha256', 'terminal_sha256', 'receipt_sha256'):
        optional_digest(journal[field])
    need(journal['exit_code'] is None or (type(journal['exit_code']) is int and -255 <= journal['exit_code'] <= 255))
    if journal['state'] == 'complete' and value['stage'] in (
            'hydration', 'publication', 'native_restore', 'catalog_admission'):
        need(value['archive_sha256'] is not None)
    unit = value['unit']
    policy._keys(unit, ('state', 'original_process', 'cgroup_empty', 'terminal_proof_sha256'))
    need(unit['state'] in ('inactive', 'failed', 'active', 'activating', 'absent', 'unknown')
         and unit['original_process'] in ('terminal', 'alive', 'reused', 'unknown')
         and type(unit['cgroup_empty']) is bool)
    optional_digest(unit['terminal_proof_sha256'])
    if value['stage'] == 'native_restore':
        policy._keys(value['restore'], ('restore_dispatched', 'start_dispatched', 'data_present', 'postmaster'))
        need(all(type(value['restore'][field]) is bool for field in
                 ('restore_dispatched', 'start_dispatched', 'data_present'))
             and value['restore']['postmaster'] in ('absent', 'terminal', 'alive', 'unknown'))
    else:
        need(value['restore'] is None and value['reconciliation'] is None)
    return value


def terminal_unit(record):
    unit = record['unit']
    return (unit['state'] in ('inactive', 'failed', 'absent')
            and unit['original_process'] == 'terminal' and unit['cgroup_empty'] is True
            and unit['terminal_proof_sha256'] is not None)


def info_retry(record, request, now):
    """Only a reconciled INFO-only failure may precede its designated next attempt."""
    if not (request['operation'] == 'bundle_restore' and record['stage'] == 'native_restore'
            and record['transaction'] == request['transaction'] and record['attempt'] + 1 == request['attempt']
            and record['journal']['state'] == 'failed' and record['journal']['phase'] == 'info'
            and type(record['journal']['exit_code']) is int and record['journal']['exit_code'] != 0
            and record['journal']['dispatch_sha256'] is not None
            and record['journal']['terminal_sha256'] is not None and record['journal']['receipt_sha256'] is None
            and terminal_unit(record) and record['restore'] == {
                'restore_dispatched': False, 'start_dispatched': False, 'data_present': False, 'postmaster': 'absent'}):
        return False
    evidence = record['reconciliation']
    policy._keys(evidence, ('plan', 'proof', 'ownership', 'intent', 'info_dispatch', 'info_terminal'))
    next_plan = evidence['plan']
    need(next_plan['transaction'] == request['transaction'] and next_plan['attempt'] == request['attempt']
         and next_plan['archive_sha256'] == record['archive_sha256'])
    checked = native_restore.validate_reconciliation(bundle.canonical(evidence['proof']),
        bundle.canonical(evidence['ownership']), bundle.canonical(evidence['intent']), next_plan, now)
    need(checked['prior_owner'] == record['owner']
         and checked['proof_sha256'] == record['unit']['terminal_proof_sha256']
         and hashlib.sha256(bundle.canonical(evidence['intent'])).hexdigest() == record['journal']['intent_sha256']
         and hashlib.sha256(bundle.canonical(evidence['info_dispatch'])).hexdigest() == record['journal']['dispatch_sha256']
         and hashlib.sha256(bundle.canonical(evidence['info_terminal'])).hexdigest() == record['journal']['terminal_sha256'])
    need(type(evidence['info_terminal']) is dict
         and evidence['info_terminal'].get('exit_code') == record['journal']['exit_code'])
    return True


def assess(evidence, now):
    """Closed assessment API. Malformed, missing, stale or unknown evidence blocks.

    `scan` must cover every persistent root category, not just currently loaded
    units. Record receipts/proofs must be authenticated by the future collector;
    this pure function deliberately reports authenticated=False even on success.
    Zero/one accepted bundles may capture; no result authorizes deletion.
    """
    result = {'schema': 'careertrellis.onedrive-growth-guard-assessment/v1',
        'allowed': False, 'reasons': [], 'authenticated': False,
        'native_guard_installed': False, 'dispatch_authorized': False,
        'deletion_authorized': False, 'capacity': None}
    try:
        current = policy.timestamp(now)
        policy._keys(evidence, ('schema', 'operation', 'transaction', 'attempt', 'observed_at',
                               'limits', 'quota', 'allocation', 'catalog', 'inventory', 'scan'))
        need(evidence['schema'] == 'careertrellis.onedrive-growth-guard-evidence/v1'
             and evidence['operation'] in OPERATIONS and type(evidence['transaction']) is str
             and re.fullmatch('[0-9a-f]{32}', evidence['transaction'])
             and type(evidence['attempt']) is int and 1 <= evidence['attempt'] <= 9)
        fresh(evidence['observed_at'], current)
        limits = evidence['limits']
        policy._keys(limits, ('enabled', 'valid_until', 'reserve_percent', 'peak_multiplier', 'namespace_budget_bytes'))
        need(limits['enabled'] is True and current < policy.timestamp(limits['valid_until']))
        quota = evidence['quota']; policy._keys(quota, ('total', 'free', 'observed_at'))
        fresh(quota['observed_at'], current)
        allocation = evidence['allocation']; policy._keys(allocation, ('source_bytes', 'candidate_bytes'))
        catalog_plan = retire.prepare(evidence['catalog'], now)
        accepted = retire.objects(evidence['catalog'])
        accepted_ids = {entry['id'] for entry in evidence['catalog']['entries']}
        native_receipts = {entry['id']: entry['native_restore_receipt_sha256'] for entry in evidence['catalog']['entries']}
        inventory = evidence['inventory']
        policy._keys(inventory, ('schema', 'namespace', 'observed_at', 'objects'))
        need(inventory['schema'] == 'careertrellis.onedrive-bundle-inventory/v1'
             and inventory['namespace'] == retire.NAMESPACE and type(inventory['objects']) is list
             and len(inventory['objects']) <= MAX_RECORDS)
        fresh(inventory['observed_at'], current)
        inventory_rows, object_ids = {}, set()
        for obj in inventory['objects']:
            policy._keys(obj, ('path', 'object_id', 'size', 'sha256'))
            policy._text(obj['object_id']); policy._digest(obj['sha256']); policy.integer(obj['size'], 1)
            pieces = obj['path'].split('/')
            need(len(pieces) == 2 and policy.HEX.fullmatch(pieces[0])
                 and pieces[1] in ('repository.tar', 'manifest.json')
                 and obj['path'] not in inventory_rows and obj['object_id'] not in object_ids)
            inventory_rows[obj['path']] = obj; object_ids.add(obj['object_id'])
        need(all(inventory_rows.get(path) == obj for path, obj in accepted.items()))
        scan = evidence['scan']
        policy._keys(scan, ('schema', 'observed_at', 'complete', 'coverage', 'unknown_count', 'records'))
        need(scan['schema'] == 'careertrellis.onedrive-persistent-attempt-scan/v1'
             and scan['complete'] is True and type(scan['coverage']) is list
             and len(scan['coverage']) == len(COVERAGE) and set(scan['coverage']) == set(COVERAGE)
             and type(scan['unknown_count']) is int and scan['unknown_count'] == 0
             and type(scan['records']) is list and len(scan['records']) <= MAX_RECORDS)
        scanned_at = fresh(scan['observed_at'], current)
        records, keys = [], set()
        for value in scan['records']:
            record = attempt_record(value, current)
            need(policy.timestamp(record['observed_at']) <= scanned_at)
            key = (record['transaction'], record['stage'], record['attempt'])
            need(key not in keys); keys.add(key); records.append(record)
        reasons = set()
        complete_records = []
        for record in records:
            journal = record['journal']
            complete = (journal['state'] == 'complete' and journal['phase'] == 'complete'
                        and journal['dispatch_sha256'] is not None and journal['terminal_sha256'] is not None
                        and journal['exit_code'] == 0 and journal['receipt_sha256'] is not None
                        and terminal_unit(record))
            if complete and record['stage'] == 'native_restore':
                complete = (record['restore']['restore_dispatched'] is True
                    and record['restore']['start_dispatched'] is True
                    and record['restore']['postmaster'] in ('terminal', 'absent')
                    and native_receipts.get(record['archive_sha256']) == journal['receipt_sha256'])
            if complete: complete_records.append(record)
            if not complete and not info_retry(record, evidence, current):
                reasons.add('persistent_attempt_requires_reconciliation')
                if (record['stage'] == 'native_restore' and journal['state'] == 'failed'
                        and record['archive_sha256'] in accepted_ids):
                    reasons.add('historical_failure_resolution_evidence_required')
            if record['archive_sha256'] is not None and record['stage'] in (
                    'hydration', 'publication', 'native_restore') and record['archive_sha256'] not in accepted_ids:
                following = ('bundle_publish' if record['stage'] == 'hydration' else 'bundle_restore')
                if not (record['transaction'] == evidence['transaction']
                        and evidence['operation'] in (following, 'bundle_restore')):
                    reasons.add('unaccepted_capture_blocks_other_growth')
        for identifier, receipt_sha in native_receipts.items():
            if not any(record['stage'] == 'native_restore' and record['archive_sha256'] == identifier
                       and record['journal']['receipt_sha256'] == receipt_sha for record in complete_records):
                reasons.add('catalog_native_attempt_proof_missing')
        if evidence['operation'] in ('bundle_publish', 'bundle_restore'):
            required_stage = 'hydration' if evidence['operation'] == 'bundle_publish' else 'publication'
            prior_stages = [record for record in complete_records if record['stage'] == required_stage
                            and record['transaction'] == evidence['transaction']]
            if not prior_stages: reasons.add('capture_stage_evidence_required')
            if evidence['operation'] == 'bundle_restore' and not any(
                    inventory_rows.get(record['archive_sha256'] + '/repository.tar', {}).get('sha256')
                    == record['archive_sha256'] for record in prior_stages):
                reasons.add('downloaded_candidate_identity_required')
        for path in set(inventory_rows) - set(accepted):
            identifier = path.split('/')[0]
            owners = [record for record in records if record['archive_sha256'] == identifier]
            if not (owners and evidence['operation'] == 'bundle_restore'
                    and all(record['transaction'] == evidence['transaction'] for record in owners)):
                reasons.add('unresolved_staging_objects')
        candidate = allocation['candidate_bytes']
        if evidence['operation'] in ('bundle_capture', 'bundle_publish'):
            policy.integer(candidate); policy.integer(allocation['source_bytes'])
            need(candidate >= allocation['source_bytes'])
        new_bytes = candidate if evidence['operation'] in ('bundle_capture', 'bundle_publish') else 0
        assessment = capacity(quota['total'], quota['free'], allocation['source_bytes'], candidate,
            sum(obj['size'] for obj in inventory_rows.values()), new_bytes,
            reserve_percent=limits['reserve_percent'], peak_multiplier=limits['peak_multiplier'],
            namespace_budget_bytes=limits['namespace_budget_bytes'])
        reasons.update(assessment['reasons'])
        result.update(allowed=not reasons, reasons=sorted(reasons), capacity=assessment,
                      accepted_bundle_count=len(accepted_ids),
                      minimum_two_verified_bundles_present=len(accepted_ids) >= 2,
                      retained_catalog_bundle_count=len(catalog_plan['retained_bundle_ids']))
    except Exception:
        result.update(allowed=False, reasons=['missing_invalid_or_stale_guard_evidence'], capacity=None)
    return result


def native_check(operation, transaction, attempt=1, repository_lock_fd=None):
    """Concrete DB admission check; no supplied evidence dictionaries are trusted.

    The WEB witness comes from this exact immutable authenticated stage intent.
    Every fact is recollected from fixed roots or the fixed provider under the
    existing canonical repository lock. Unknown history blocks, including the
    intentionally unsupported legacy DB mirror once this policy is active.
    """
    from . import baseline, builder, coordinator, entry, preservation, publish
    stages = {'bundle_capture':'hydrate','bundle_publish':'publish','bundle_restore':'restore',
              'bundle_prepare':'prepare'}
    need(type(operation) is str and operation in stages
         and type(transaction) is str and re.fullmatch('[0-9a-f]{32}',transaction)
         and type(attempt) is int and attempt == 1 and repository_lock_fd is not None)
    stage = stages[operation]
    if operation in ('bundle_restore','bundle_prepare'):
        return check_local_stage(transaction,stage,repository_lock_fd)
    context = baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role == 'db'
         and Path(__file__).absolute() == entry.ROOT/'guard.py')
    catalog.NativeFiles(repository_lock_fd).require_repository_lock()
    need(entry.retention_idle(transaction,stage) is True)
    peer = native_web_witness(transaction,stage,context)
    local = native_history_snapshot(transaction,stage,repository_lock_fd)
    validate_peer_history(local,transaction,stage,context.bindings['standalone_policy_sha256'],expected_role='db')
    need(local['baseline_bindings'] == peer['baseline_bindings'])
    source_bytes = candidate_bytes = 0
    provider = NativeProvider(repository_lock_fd)
    try:
        if operation == 'bundle_capture':
            source_bytes,candidate_bytes = provider.source_allocation()
        else:
            root = Path('/var/lib/careertrellis/onedrive-bundles')/transaction
            archive_bytes = publish.digest_file(root/'repository.tar')
            need(archive_bytes[1] <= bundle.MAX_BYTES)
            manifest_raw = native_restore.protected(root/'bundle-manifest.json',maximum=bundle.MAX_MANIFEST)
            manifest = native_restore.decode(manifest_raw); bundle.validate_manifest(manifest)
            need(manifest['generation_sha256'] == builder.digest(native_restore.decode(
                native_restore.protected(root/'generation.json',maximum=bundle.MAX_MANIFEST))))
            candidate_bytes = archive_bytes[1]
            source_bytes = sum(item['size'] for item in manifest['files'].values())
        facts = provider.collect().value
    finally: provider.close()
    _namespace_owners(facts,repository_lock_fd,
        transaction if operation in ('bundle_restore','bundle_prepare') else None)
    # Capacity counts every provider object, even held/unaccepted staging bytes.
    # New immutable archives cannot reuse any extant prefix, including unknowns.
    if operation == 'bundle_publish': need(archive_bytes[0]+'/repository.tar' not in facts['rows'])
    raw,document = entry._protected_document(coordinator.PRIVATE_POLICY,0o600)
    live = coordinator.Policy(document); coordinator.native_policy_binding(live)
    live.require_active(dt.datetime.now(dt.timezone.utc))
    assessment = capacity(facts['quota']['total'],facts['quota']['free'],source_bytes,candidate_bytes,
        facts['namespace_bytes'],candidate_bytes if operation in ('bundle_capture','bundle_publish') else 0,
        reserve_percent=document['reserve_percent'],peak_multiplier=document['peak_multiplier'],
        namespace_budget_bytes=document['namespace_budget_bytes'])
    need(assessment['allowed'])
    validate_peer_history(peer,transaction,stage,context.bindings['standalone_policy_sha256'])
    validate_peer_history(local,transaction,stage,context.bindings['standalone_policy_sha256'],expected_role='db')
    need(baseline.native_context().bindings == context.bindings)
    preservation.require_owned_lock('/run/lock/careertrellis-pgbackrest-repository.lock',repository_lock_fd)
    return {'schema':'careertrellis.onedrive-native-growth-guard/v1','allowed':True,
        'authenticated':True,'operation':operation,'transaction':transaction,'attempt':attempt,
        'capacity':assessment,'local_history_sha256':hashlib.sha256(bundle.canonical(local)).hexdigest(),
        'peer_history_sha256':hashlib.sha256(bundle.canonical(peer)).hexdigest(),
        'quota_observed_at':facts['quota']['observed_at'],'native_restore_accepted':False,
        'deletion_authorized':False,'automatic_retry':False}


def check_local_stage(transaction, stage, repository_lock_fd):
    """Provider-free admission for the private-network restore/prepare owner."""
    from . import baseline, builder, entry, local_restore_plan as plans, preservation
    need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}',transaction)
         and stage in ('restore','prepare') and repository_lock_fd is not None)
    context = baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role == 'db'
         and Path(__file__).absolute() == entry.ROOT/'guard.py')
    catalog.NativeFiles(repository_lock_fd).require_repository_lock()
    need(entry.retention_idle(transaction,stage) is True)
    peer = native_web_witness(transaction,stage,context)
    local = native_history_snapshot(transaction,stage,repository_lock_fd)
    validate_peer_history(local,transaction,stage,context.bindings['standalone_policy_sha256'],expected_role='db')
    need(local['baseline_bindings'] == peer['baseline_bindings'])
    root = Path(plans.ROOT)/transaction
    def read(name,maximum):
        raw = native_restore.protected(root/name,maximum=maximum)
        value = native_restore.decode(raw); need(bundle.canonical(value) == raw)
        return value
    download = read('downloaded-receipt.json',16384)
    manifest = read('downloaded-manifest.json',bundle.MAX_MANIFEST)
    facts = read('source-database-size.json',8192)
    prepared = builder.plan_from_facts(download,manifest,facts)
    need(prepared['transaction'] == transaction)
    need(prepared['attempt'] == 1)
    stats = os.statvfs(root)
    need(stats.f_bavail*stats.f_frsize >= prepared['minimum_free_bytes']
         and stats.f_favail >= prepared['minimum_free_inodes'])
    validate_peer_history(peer,transaction,stage,context.bindings['standalone_policy_sha256'])
    validate_peer_history(local,transaction,stage,context.bindings['standalone_policy_sha256'],expected_role='db')
    need(baseline.native_context().bindings == context.bindings)
    preservation.require_owned_lock('/run/lock/careertrellis-pgbackrest-repository.lock',repository_lock_fd)
    return {'schema':'careertrellis.onedrive-native-local-guard/v1','allowed':True,'authenticated':True,
        'transaction':transaction,'stage':stage,'plan_sha256':hashlib.sha256(bundle.canonical(prepared)).hexdigest(),
        'local_history_sha256':hashlib.sha256(bundle.canonical(local)).hexdigest(),
        'peer_history_sha256':hashlib.sha256(bundle.canonical(peer)).hexdigest(),
        'provider_calls':0,'native_restore_accepted':False,'deletion_authorized':False,'automatic_retry':False}


def provider_rows(raw):
    """Strict metadata projection; an object name is never a byte digest proof."""
    need(type(raw) is bytes and 0 < len(raw) <= 4 * 1024**2)
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value); value[key] = item
        return value
    try: rows = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: need(False))
    except Exception: raise GuardRefused('provider_inventory_invalid') from None
    need(type(rows) is list and len(rows) <= MAX_RECORDS)
    result, identifiers = {}, set()
    for row in rows:
        need(type(row) is dict and row.get('IsDir') is False)
        path, identifier, size = row.get('Path'), row.get('ID'), row.get('Size')
        need(type(path) is str and re.fullmatch('[0-9a-f]{64}/repository[.]tar', path)
             and path not in result and type(identifier) is str and 0 < len(identifier) <= 512
             and all(33 <= ord(char) < 127 for char in identifier) and identifier not in identifiers
             and type(size) is int and 0 < size <= bundle.MAX_BYTES)
        result[path] = {'path': path, 'object_id': identifier, 'size': size}
        identifiers.add(identifier)
    return result


def drive_scope(value):
    """Private stable-drive binding; never fingerprint an OAuth token or secret."""
    need(type(value) is str and 0 < len(value) <= 512
         and all(33 <= ord(char) < 127 for char in value))
    return hashlib.sha256(b'careertrellis.onedrive-drive-scope/v1\0'+value.encode('ascii')).hexdigest()


def witness_inventory(rows, witnesses, observed_at):
    """Join actual ID/size metadata to separately authenticated byte witnesses.

    This pure validator does not authenticate either input. Missing witnesses,
    aliases, duplicates or unknown objects refuse rather than assigning a digest
    from the object's directory name.
    """
    policy.timestamp(observed_at)
    need(type(rows) is dict and type(witnesses) is list and len(witnesses) <= MAX_RECORDS)
    result, seen = [], set()
    for witness in witnesses:
        need(type(witness) is dict)
        path = witness.get('path')
        need(type(path) is str and path in rows and path not in seen
             and witness.get('identity_verified_before_after_download') is True
             and witness.get('object_id') == rows[path]['object_id']
             and type(witness.get('size')) is int and witness['size'] == rows[path]['size'])
        policy._digest(witness.get('sha256'))
        need(witness['sha256'] == path.split('/')[0])
        result.append(dict(rows[path], sha256=witness['sha256'])); seen.add(path)
    need(seen == set(rows))
    return {'schema': 'careertrellis.onedrive-bundle-inventory/v1',
            'namespace': retire.NAMESPACE, 'observed_at': observed_at,
            'objects': sorted(result, key=lambda value: value['path'])}


class AuthenticatedProviderFacts:
    """Issued only by the fixed installed native collector, never parsed claims."""
    __slots__ = ('_raw', '_seal')
    def __init__(self, value, seal=None):
        need(seal is _PROVIDER_SEAL)
        self._raw, self._seal = bundle.canonical(value), seal
    @property
    def value(self): return json.loads(self._raw)
    @property
    def authenticated(self): return self._seal is _PROVIDER_SEAL


class NativeProvider:
    """Read-only provider census under an already-owned canonical repository lock.

    Credentials remain in a sealed OneDrive-only memfd. No raw provider output,
    config bytes or provider error text is logged. This collector never acquires
    a lock itself and therefore cannot be used as an operation progress poll.
    """
    def __init__(self, repository_lock_fd):
        from . import baseline, capture_plan, entry
        need(os.name == 'posix' and os.geteuid() == os.getegid() == 0
             and socket.gethostname() == 'job-applier-db-staging-01'
             and Path(__file__).absolute() == entry.ROOT / 'guard.py')
        entry.verify_install()
        self.context = baseline.native_context()
        need(type(self.context) is baseline.AuthenticatedContext and self.context.authenticated
             and self.context.role == 'db')
        self.files = catalog.NativeFiles(repository_lock_fd)
        self.fd = None
        self.binary = capture_plan.BINARY
        from . import hydrate
        raw, self.binary_identity = hydrate.protected(self.binary, limit=256 * 1024**2)
        need(hashlib.sha256(raw).hexdigest() == capture_plan.BINARY_SHA256)
        self.config = hydrate.protected(capture_plan.CONFIG, 0o600)
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.read_string(self.config[0].decode('utf-8'))
        need(not parser.defaults() and set(parser.sections()) == {'r2', 'onedrive'}
             and parser['onedrive'].get('type') == 'onedrive')
        self.account_scope = drive_scope(parser['onedrive'].get('drive_id'))
        sealed = configparser.ConfigParser(interpolation=None, strict=True)
        sealed['onedrive'] = dict(parser['onedrive'])
        output = io.StringIO(); sealed.write(output)
        import fcntl
        fd = os.memfd_create('retention-provider-readonly-config', os.MFD_ALLOW_SEALING)
        try:
            raw = output.getvalue().encode('utf-8')
            need(os.write(fd, raw) == len(raw)); os.fchmod(fd, 0o600); os.lseek(fd, 0, 0)
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
                        | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
            self.fd = fd
        except BaseException:
            os.close(fd); raise

    def close(self):
        if self.fd is not None: os.close(self.fd); self.fd = None

    def unchanged(self):
        from . import baseline, capture_plan, hydrate
        self.files.require_repository_lock()
        need(self.fd is not None and hydrate.protected(capture_plan.CONFIG, 0o600) == self.config
             and hydrate.protected(self.binary, limit=256 * 1024**2)[1] == self.binary_identity
             and baseline.native_context().bindings == self.context.bindings)

    def read(self, operation):
        from . import hydrate
        commands = {'quota': ['about', 'onedrive:', '--json'],
            'parent': ['lsjson', 'onedrive:job-applier-staging', '--dirs-only', '--no-modtime', '--no-mimetype'],
            'inventory': ['lsjson', retire.NAMESPACE, '--recursive', '--files-only',
                          '--no-modtime', '--no-mimetype']}
        need(operation in commands); self.unchanged()
        raw = hydrate.command([self.binary, *commands[operation], '--config', '/proc/self/fd/' + str(self.fd),
            '--log-level', 'ERROR', '--stats', '0', '--retries', '1', '--low-level-retries', '1',
            '--contimeout', '15s', '--timeout', '45s'], descriptor=self.fd,
            lock_descriptor=self.files.repository_lock_fd, output=True, timeout=600)
        need(type(raw) is bytes and len(raw) <= 4 * 1024**2)
        self.unchanged(); return raw

    def source_allocation(self):
        """Bound the next deterministic tar from actual R2 count/byte census."""
        from . import capture_plan, hydrate
        self.unchanged()
        parser = configparser.ConfigParser(interpolation=None,strict=True)
        parser.read_string(self.config[0].decode('utf-8'))
        need(parser['r2'].get('type') == 's3' and parser['r2'].get('provider') == 'Cloudflare'
             and re.fullmatch('https://[0-9a-f]{32}[.]r2[.]cloudflarestorage[.]com/?',
                              parser['r2'].get('endpoint','')))
        sealed = configparser.ConfigParser(interpolation=None,strict=True)
        sealed['r2'] = dict(parser['r2']); output = io.StringIO(); sealed.write(output)
        import fcntl
        fd = os.memfd_create('retention-source-readonly-config',os.MFD_ALLOW_SEALING)
        try:
            raw = output.getvalue().encode('utf-8')
            need(os.write(fd,raw) == len(raw)); os.fchmod(fd,0o600); os.lseek(fd,0,0)
            fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_SEAL|fcntl.F_SEAL_SHRINK
                        |fcntl.F_SEAL_GROW|fcntl.F_SEAL_WRITE)
            raw = hydrate.command([self.binary,'size',capture_plan.SOURCE,'--json','--config',
                '/proc/self/fd/'+str(fd),'--log-level','ERROR','--stats','0','--retries','1',
                '--low-level-retries','1','--contimeout','15s','--timeout','45s'],descriptor=fd,
                lock_descriptor=self.files.repository_lock_fd,output=True,timeout=600)
            need(len(raw) <= 65536)
            value = native_restore.decode(raw)
            need(type(value) is dict and type(value.get('bytes')) is int
                 and 0 < value['bytes'] <= bundle.MAX_BYTES and type(value.get('count')) is int
                 and 0 < value['count'] <= 100000)
            candidate = value['bytes'] + value['count']*1536 + bundle.MAX_MANIFEST + 20480
            need(candidate <= bundle.MAX_BYTES)
            self.unchanged(); return value['bytes'],candidate
        finally: os.close(fd)

    def collect(self):
        """Return quota plus ID/size census; byte identity is a separate witness."""
        need(type(self) is NativeProvider)
        started = time.monotonic()
        raw = self.read('quota')
        quota_observed = dt.datetime.now(dt.timezone.utc).isoformat()
        need(len(raw) <= 65536)
        quota = native_restore.decode(raw)
        need(type(quota) is dict and type(quota.get('total')) is int and quota['total'] > 0
             and type(quota.get('free')) is int and 0 <= quota['free'] <= quota['total'])
        parent_raw = self.read('parent')
        need(len(parent_raw) <= 1024**2)
        parents = native_restore.decode(parent_raw)
        need(type(parents) is list and len(parents) <= MAX_RECORDS)
        matches = []
        for value in parents:
            need(type(value) is dict and type(value.get('Path')) is str and value.get('IsDir') is True)
            if value['Path'].casefold() == 'postgresql-bundles':
                need(value['Path'] == 'postgresql-bundles'); matches.append(value)
        need(len(matches) <= 1)
        rows = provider_rows(self.read('inventory')) if matches else {}
        observed = dt.datetime.now(dt.timezone.utc).isoformat()
        need(0 <= time.monotonic() - started <= policy.MAX_INVENTORY_AGE_SECONDS)
        self.unchanged()
        return AuthenticatedProviderFacts({'schema': 'careertrellis.onedrive-provider-census/v1',
            'observed_at': observed, 'quota': {'total': quota['total'], 'free': quota['free'],
            'observed_at': quota_observed}, 'rows': rows, 'namespace_bytes': sum(row['size'] for row in rows.values()),
            'baseline_bindings': self.context.bindings, 'account_scope':self.account_scope,
            'provider_writes': 0}, _PROVIDER_SEAL)


def collect_provider_facts(repository_lock_fd):
    collector = NativeProvider(repository_lock_fd)
    try: return collector.collect()
    finally: collector.close()


def _namespace_owners(facts, repository_lock_fd, candidate_transaction=None, *, retirement_transaction=None):
    """Every remote row needs accepted, current-candidate or explicit hold provenance."""
    from . import preservation
    store = catalog.NativeStore(repository_lock_fd)
    with store.io.locked():
        records = store.records(); retired = store.retired(records)
        expected = {}
        for identifier,record in records.items():
            if identifier in retired: continue
            for obj in record['entry']['objects']:
                need(obj['path'] not in expected); expected[obj['path']] = obj
        current = None
        if candidate_transaction is not None:
            need(type(candidate_transaction) is str and re.fullmatch('[0-9a-f]{32}',candidate_transaction))
            raw = native_restore.protected(Path('/var/lib/careertrellis/onedrive-bundles')/
                candidate_transaction/'downloaded-receipt.json',maximum=16384)
            download = native_restore.decode(raw)
            need(bundle.canonical(download) == raw and download.get('transaction') == candidate_transaction
                 and download.get('schema') == 'careertrellis.onedrive-bundle-download/v1'
                 and download.get('result') == 'download_verified'
                 and download.get('native_restore_accepted') is False)
            policy._digest(download.get('archive_sha256'))
            current = (download['archive_sha256']+'/repository.tar',download['archive_bytes'])
        held = None
        historical_path = preservation.HISTORICAL_ARCHIVE_SHA256+'/repository.tar'
        if historical_path in facts['rows'] and historical_path not in expected:
            held_state = preservation.read_historical_info_preservation()['fresh_state']
            held = (historical_path,held_state['held_remote_bytes'])
        accepted_rows,held_rows,candidate_rows = {},{},{}
        for path,row in facts['rows'].items():
            if path in expected:
                need(row['object_id'] == expected[path]['object_id'] and row['size'] == expected[path]['size'])
                accepted_rows[path] = row
            elif held is not None and (path,row['size']) == held:
                held_rows[path] = row
            elif current is not None and (path,row['size']) == current:
                candidate_rows[path] = row
            else: raise GuardRefused('unknown_remote_staging_object')
        missing = set(expected)-set(accepted_rows)
        if missing:
            need(retirement_transaction is not None)
            # This is not a caller allowlist. The native retirement module must
            # authenticate the current finite owner and every durable original
            # candidate/dispatch/known-success acknowledgement before returning
            # exactly the missing paths. Unknown outcomes still refuse.
            authenticated = retire.authenticated_retirement_absences(
                repository_lock_fd,retirement_transaction,facts['rows'])
            need(type(authenticated) is frozenset and authenticated == missing)
        need(set(expected) == set(accepted_rows)|missing)
        return accepted_rows,held_rows,candidate_rows


def native_catalog_inventory(repository_lock_fd, *, retirement_transaction=None):
    """Collect protected catalog + fresh provider census for native revalidation.

    Missing accepted objects are represented by the inventory and MUST be
    reconciled by the caller's exact completed-retirement chain. This function
    neither treats absence as retirement nor issues growth/deletion permission.
    Unknown provider objects have no authenticated byte witness and refuse.
    """
    from . import baseline, coordinator, entry
    facts = collect_provider_facts(repository_lock_fd)
    need(type(facts) is AuthenticatedProviderFacts and facts.authenticated)
    collected = facts.value
    accepted_rows,held_rows,_candidate = _namespace_owners(collected,repository_lock_fd,
        retirement_transaction=retirement_transaction)
    store = catalog.NativeStore(repository_lock_fd)
    with store.io.locked():
        records = store.records()
        retired = store.retired(records)
        active = {identifier: record for identifier, record in records.items() if identifier not in retired}
        witnesses = []
        for identifier, record in active.items():
            source_transaction = record['evidence']['download']['transaction']
            witness = catalog.read_identity(store.io, source_transaction)
            need(witness.get('schema') == 'careertrellis.onedrive-bundle-identity/v2'
                 and witness.get('etag_verified_before_after_download') is True
                 and witness.get('download_receipt_sha256') == hashlib.sha256(
                     bundle.canonical(record['evidence']['download'])).hexdigest())
            if witness['path'] in accepted_rows: witnesses.append(witness)
        inventory = witness_inventory(accepted_rows, witnesses, collected['observed_at'])
        entries = [json.loads(bundle.canonical(record['entry'])) for _, record in sorted(active.items())]
        holds = []
        for name in sorted(store.io.names('holds')):
            need(type(name) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}[.]json', name))
            hold = catalog.decode(store.io.read_store('holds/' + name))
            need(hold.get('name') + '.json' == name); holds.append(hold)
        snapshot = {'schema':'careertrellis.onedrive-bundle-catalog/v1',
                    'observed_at':collected['observed_at'], 'entries':entries, 'holds':holds}
        # Validate retained dependency/hold closure even during bootstrap.
        retire.prepare(snapshot, collected['observed_at'])
        raw, document = entry._protected_document(coordinator.PRIVATE_POLICY, 0o600)
        live_policy = coordinator.Policy(document)
        coordinator.native_policy_binding(live_policy)
        live_policy.require_active(dt.datetime.now(dt.timezone.utc))
        context = baseline.native_context()
        need(type(context) is baseline.AuthenticatedContext and context.authenticated
             and context.bindings == collected['baseline_bindings'])
        store.io.require_repository_lock()
        return {'policy':document, 'catalog':snapshot, 'inventory':inventory,
                'quota':collected['quota'], 'namespace_bytes':collected['namespace_bytes'],
                'unaccepted_held_objects':list(held_rows.values()),
                'authenticated':True, 'growth_authorized':False, 'deletion_authorized':False}


HISTORY_ROOTS = {
    'db': (('hydration', '/var/lib/careertrellis/onedrive-bundles'),
           ('publication', '/var/lib/careertrellis/onedrive-bundles'),
           ('native_restore', '/var/lib/careertrellis-onedrive-bundle-acceptance'),
           ('catalog_admission', '/var/lib/careertrellis/onedrive-retention/db/catalog'),
           ('retirement', '/var/lib/careertrellis/onedrive-retention/db/retirements'),
           ('stage_dispatch', '/var/lib/careertrellis/onedrive-retention/db'),
           ('paired_runs', '/var/lib/careertrellis/paired-maintenance/db/runs'),
           ('legacy_pg_mirror', '/var/backups/careertrellis/backup-health/operations')),
    'web': (('legacy_object_mirror', '/var/lib/careertrellis/generated-object-captures'),
            ('legacy_object_mirror', '/var/lib/careertrellis/generated-object-recovery/work'),
            ('paired_runs', '/var/lib/careertrellis/paired-maintenance/web/runs'),
            ('legacy_object_mirror', '/var/backups/careertrellis/backup-health/operations'))}


def _history_names(root):
    """Bounded anchored names and metadata; missing/unsafe roots are unknown."""
    root = Path(root)
    special = root == Path('/var/lib/careertrellis/generated-object-recovery/work')
    recovery_uid = None
    if special:
        import pwd
        recovery_uid = pwd.getpwnam('careertrellis-object-recovery').pw_uid
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        traversed = Path('/')
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
            meta = os.fstat(fd)
            traversed /= part
            allowed = ((0,recovery_uid) if special and traversed.is_relative_to(
                '/var/lib/careertrellis/generated-object-recovery') else (0,))
            need(meta.st_uid in allowed and not meta.st_mode & 0o022)
        before = os.fstat(fd)
        names = os.listdir(fd); need(len(names) <= MAX_RECORDS and len(names) == len(set(names)))
        rows = []
        for name in sorted(names):
            need(type(name) is str and 0 < len(name) <= 255 and '/' not in name
                 and all(32 <= ord(char) < 127 for char in name))
            meta = os.stat(name, dir_fd=fd, follow_symlinks=False)
            need(meta.st_uid in ((0,recovery_uid) if special else (0,)) and not meta.st_mode & 0o022
                 and (stat.S_ISDIR(meta.st_mode) or stat.S_ISREG(meta.st_mode)))
            if stat.S_ISREG(meta.st_mode): need(meta.st_nlink == 1)
            rows.append({'name':name, 'directory':stat.S_ISDIR(meta.st_mode),
                'device':meta.st_dev, 'inode':meta.st_ino, 'bytes':meta.st_size,
                'mtime_ns':meta.st_mtime_ns, 'ctime_ns':meta.st_ctime_ns})
        after = os.fstat(fd)
        need((before.st_dev,before.st_ino,before.st_mtime_ns,before.st_ctime_ns) ==
             (after.st_dev,after.st_ino,after.st_mtime_ns,after.st_ctime_ns))
        return rows
    finally: os.close(fd)


def _legacy_history(category, rows):
    """Verify every legacy receipt pair, including orphan/failed evidence."""
    from . import hydrate
    path = hydrate.CORE / 'backup_health.py'
    raw = hydrate.protected(path)[0]
    need(hashlib.sha256(raw).hexdigest() == hydrate.PINS['backup_health'])
    name = '_careertrellis_retention_history_health'
    module = types.ModuleType(name); module.__file__ = str(path)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        exec(compile(raw,str(path),'exec'),module.__dict__)
        inventory = module.scan_receipts()
        operation = 'pgbackrest_mirror' if category == 'legacy_pg_mirror' else 'generated_object_mirror'
        need(not inventory.invalid_operations and inventory.receipts[operation])
        names = {row['name'] for row in rows}
        referenced = set()
        for row in rows:
            name = row['name']; need(not row['directory'])
            if module._RECEIPT_NAME_PATTERN.fullmatch(name):
                need(name[:-5]+'.sha256' in names)
            elif module._MANIFEST_NAME_PATTERN.fullmatch(name):
                need(name[:-7]+'.json' in names)
                document = module.read_receipt(name)
                # A failure remains reconciliation evidence. A later success
                # cannot silently erase its persistent operation history.
                need(document['result'] == 'ok')
                if 'generation_sha256' in document: referenced.add(document['generation_sha256'])
            else:
                match = re.fullmatch('pgbackrest-generation-([0-9a-f]{64})[.]json',name)
                need(match is not None)
                generation_raw = hydrate.protected(module.RECEIPT_ROOT/name, limit=32*1024**2)[0]
                need(hashlib.sha256(generation_raw).hexdigest() == match[1])
        generation_names = {'pgbackrest-generation-'+digest+'.json' for digest in referenced}
        need(generation_names <= names)
        return {'operation':operation,'receipt_count':len(inventory.receipts[operation]),
            'history_sha256':hashlib.sha256(bundle.canonical(
                [dict(receipt) for receipt in inventory.receipts[operation]])).hexdigest()}
    finally:
        if previous is None: sys.modules.pop('_careertrellis_retention_history_health',None)
        else: sys.modules['_careertrellis_retention_history_health'] = previous


def _new_stage_history(category, rows, transaction, stage, context):
    from . import endpoint, execution, preservation, publish
    mapped = {'hydration':'hydrate','publication':'publish','native_restore':'restore','retirement':'retire'}[category]
    own = endpoint.unit(stage,transaction)
    current_owner = (Path('/proc/self/cgroup').read_text().strip() == '0::/system.slice/'+own
                     and execution.show(own).get('MainPID') == str(os.getpid()))
    for row in rows:
        identifier = row['name']
        need(row['directory'] and re.fullmatch('[0-9a-f]{32}',identifier))
        if identifier == preservation.HISTORICAL_TRANSACTION and category in (
                'hydration','publication','native_restore'):
            preservation.read_historical_info_preservation()
            if category == 'hydration':
                candidate = publish.Native().candidate(identifier)
                need(candidate['archive_sha256'] == preservation.HISTORICAL_ARCHIVE_SHA256
                     and candidate['generation_sha256'] == preservation.HISTORICAL_GENERATION_SHA256)
            elif category == 'publication':
                _historical_publication()
            continue
        # Only the authenticated actual current unit can account for its own
        # transaction before it publishes terminal proof. This does not forgive
        # any older transaction, other stage, orphan collector or previous retry.
        if identifier == transaction and current_owner:
            if mapped == 'publish' and stage in ('hydrate','publish'):
                source = Path('/var/lib/careertrellis/onedrive-bundles')/identifier
                need(not any(name.startswith('publish') for name in os.listdir(source)))
                continue
        result = _completed_stage(identifier,mapped,context)
        need(result['state'] == 'complete')


def _source_auxiliary_history(transaction):
    """Identity and temporary OAuth attempts remain durable growth fences."""
    from . import execution, preservation, publish
    root = Path('/var/lib/careertrellis/onedrive-bundles')/transaction
    rows = _history_names(root); names = {row['name'] for row in rows}
    def document(path,maximum=131072):
        raw = native_restore.protected(path,maximum=maximum)
        value = native_restore.decode(raw); need(bundle.canonical(value) == raw)
        return value
    attempts = {int(name[-2:]) for name in names if re.fullmatch('identity-verification-[0-9]{2}',name)}
    aliases = {int(name[-7:-5]) for name in names if re.fullmatch('bundle-object-identity-[0-9]{2}[.]json',name)}
    need(attempts == aliases and all(1 <= number <= 99 for number in attempts))
    for number in sorted(attempts):
        target = root/('identity-verification-%02d'%number)
        actual = {row['name'] for row in _history_names(target)}
        expected = {'intent.json','receipt.json','archive.tar'} | {
            phase+'-'+suffix+'.json' for phase in ('stat-before','download','stat-after')
            for suffix in ('intent','child','terminal')}
        need(actual == expected)
        intent = document(target/'intent.json'); witness = document(target/'receipt.json')
        need(intent.get('schema') == 'careertrellis.onedrive-identity-intent/v1'
             and intent.get('transaction') == transaction and intent.get('verification') == number
             and witness == document(root/('bundle-object-identity-%02d.json'%number)))
        preservation._terminal_birth(intent['owner'])
        for phase in ('stat-before','download','stat-after'):
            child = document(target/(phase+'-child.json'))
            terminal = document(target/(phase+'-terminal.json'))
            need(type(terminal.get('returncode')) is int and terminal['returncode'] == 0
                 and terminal.get('pid') == child.get('pid'))
            preservation._terminal_birth(child)
        need(publish.digest_file(target/'archive.tar') == (witness.get('sha256'),witness.get('size')))
        execution.terminal('careertrellis-onedrive-bundle-identity-'+transaction+'-%02d.service'%number)
    prefixes = set()
    for name in names:
        if name.startswith('oauth-refresh-'):
            match = re.fullmatch('(oauth-refresh-[a-z0-9_]{6,32})-(intent|child|terminal|cleanup)[.]json',name)
            need(match is not None); prefixes.add(match[1])
    for prefix in prefixes:
        need(all(prefix+'-'+phase+'.json' in names for phase in ('intent','child','terminal','cleanup')))
        intent = document(root/(prefix+'-intent.json')); child = document(root/(prefix+'-child.json'))
        terminal = document(root/(prefix+'-terminal.json')); cleanup = document(root/(prefix+'-cleanup.json'))
        private = '/run/careertrellis-onedrive-oauth-'+transaction+'-'+prefix[len('oauth-refresh-'):]
        need(intent.get('schema') == 'careertrellis.onedrive-oauth-refresh/v1'
             and intent.get('private_resource') == private
             and cleanup == {'schema':'careertrellis.onedrive-oauth-cleanup/v1','result':'private_copy_removed',
                'private_resource':private,'child_terminal':True,'original_configuration_writes':0,
                'provider_identity_changed':False}
             and terminal.get('pid') == child.get('pid') and type(terminal.get('exit_code')) is int
             and terminal['exit_code'] == 0 and terminal.get('original_configuration_writes') == 0)
        try: Path(private).lstat()
        except FileNotFoundError: pass
        else: raise GuardRefused('private_oauth_copy_still_present')
        preservation._terminal_birth({'pid':intent['owner_pid'],'start_ticks':intent['owner_start_ticks'],
                                     'boot_id':intent['boot_id']})
        preservation._terminal_birth(child)


def _completed_stage(transaction,stage,context):
    from . import coordinator, execution
    raw = native_restore.protected(coordinator.STATE/'db'/transaction/stage/'intent.json',maximum=262144)
    intent = native_restore.decode(raw); need(bundle.canonical(intent) == raw)
    old_policy = intent.get('retention_policy_sha256'); policy._digest(old_policy)
    if old_policy == context.bindings['standalone_policy_sha256']:
        return execution.observe(transaction,stage,old_policy)
    archive = authenticated_policy_archive(old_policy)
    return execution.observe_completed_history(transaction,stage,archive)


def _endpoint_history(rows,transaction,stage,context):
    from . import coordinator, execution, preservation
    fixed = {'catalog','retirements','control.lock'}
    for row in rows:
        identifier = row['name']
        if identifier in fixed: continue
        if identifier == 'schedule':
            from . import health
            need(row['directory'])
            schedule_root = coordinator.STATE/'db'/'schedule'
            names = {part['name'] for part in _history_names(schedule_root)}
            need(names <= {'witness.json','control.lock'})
            if 'control.lock' in names:
                need(native_restore.protected(schedule_root/'control.lock',maximum=0) == b'')
            if 'witness.json' in names:
                raw = native_restore.protected(schedule_root/'witness.json',maximum=131072)
                witness = native_restore.decode(raw); need(bundle.canonical(witness) == raw)
                health.validate_schedule(witness,context.bindings,context.revision,
                                         context.document['application_release'],dt.datetime.now(dt.timezone.utc))
            continue
        need(row['directory'] and re.fullmatch('[0-9a-f]{32}',identifier))
        root = coordinator.STATE/'db'/identifier
        children = _history_names(root)
        for child in children:
            part = child['name']; need(child['directory'])
            if part.startswith('collector'):
                need(re.fullmatch('collector(?:-0[2-9])?',part))
                if identifier == transaction and stage == 'restore':
                    execution.require_stage_owner(transaction,stage)
                    continue
                collector_root = root/part
                names = {value['name'] for value in _history_names(collector_root)}
                need(names == {'intent.json','proof-ready.json'})
                intent_raw = native_restore.protected(collector_root/'intent.json',maximum=131072)
                intent = native_restore.decode(intent_raw)
                ready_raw = native_restore.protected(collector_root/'proof-ready.json',maximum=131072)
                ready = native_restore.decode(ready_raw)
                need(intent.get('transaction') == identifier
                     and intent.get('schema') == 'careertrellis.onedrive-native-collector/v1'
                     and ready.get('result') == 'terminal_proof_ready')
                attempt = intent.get('attempt')
                need(type(attempt) is int and 1 <= attempt <= 9)
                suffix = '' if attempt == 1 else '-%02d' % attempt
                proof_raw = native_restore.protected(Path('/var/lib/careertrellis-onedrive-bundle-acceptance')/
                    (identifier+suffix)/'unit-terminal-proof.json',maximum=131072)
                need(ready.get('proof_sha256') == hashlib.sha256(proof_raw).hexdigest())
                preservation._terminal_birth(intent['collector_owner'])
                execution.terminal(intent['collector_unit'])
                result = _completed_stage(identifier,'restore',context)
                need(result['state'] == 'complete')
            else:
                need(part in coordinator.STAGES)
                if identifier == transaction and part == stage:
                    execution.require_stage_owner(transaction,stage)
                    need(not any(value['name'] in ('failed.json','failure.json')
                                 for value in _history_names(root/part)))
                    continue
                result = _completed_stage(identifier,part,context)
                need(result['state'] == 'complete')


def _historical_publication():
    """Bind original failed publication plus its explicitly reconciled successor."""
    from . import execution, preservation
    root = preservation.HISTORICAL_INPUT
    for unit in ('careertrellis-onedrive-bundle-publish-'+preservation.HISTORICAL_TRANSACTION+'.service',
                 'careertrellis-onedrive-bundle-publish-'+preservation.HISTORICAL_TRANSACTION+'-02.service',
                 'careertrellis-onedrive-bundle-publish-'+preservation.HISTORICAL_TRANSACTION+'-03.service'):
        execution.terminal(unit)
    def read(name):
        raw = native_restore.protected(root/name,maximum=131072)
        value = native_restore.decode(raw); need(bundle.canonical(value) == raw)
        return raw,value
    _,first = read('publish-intent.json')
    _,next_intent = read('publish-02-intent.json')
    need(first.get('schema') == next_intent.get('schema') == 'careertrellis.onedrive-publication/v1'
         and first.get('transaction') == next_intent.get('transaction') == preservation.HISTORICAL_TRANSACTION
         and first.get('archive_sha256') == next_intent.get('archive_sha256') == preservation.HISTORICAL_ARCHIVE_SHA256)
    for intent in (first,next_intent):
        preservation._terminal_birth({'pid':intent['pid'],'start_ticks':intent['process_start_ticks'],
                                     'boot_id':intent['boot_id']})
    _,reconciliation = read('publish-reconciliation-01.json')
    need(reconciliation.get('schema') == 'careertrellis.onedrive-publication-reconciliation/v1'
         and reconciliation.get('transaction') == preservation.HISTORICAL_TRANSACTION
         and reconciliation.get('archive_sha256') == preservation.HISTORICAL_ARCHIVE_SHA256
         and reconciliation.get('prior_attempt') == 1 and reconciliation.get('next_attempt') == 2
         and reconciliation.get('prior_terminal') is True and reconciliation.get('prior_cgroup_empty') is True
         and reconciliation.get('candidate_archive_absent') is True
         and reconciliation.get('prior_intent_sha256') == hashlib.sha256(read('publish-intent.json')[0]).hexdigest())
    _,failed_upload = read('publish-upload-terminal.json')
    need(type(failed_upload.get('returncode')) is int and failed_upload['returncode'] != 0)
    for phase in ('upload','download'):
        _,terminal = read('publish-02-'+phase+'-terminal.json')
        need(type(terminal.get('returncode')) is int and terminal['returncode'] == 0
             and type(terminal.get('pid')) is int and terminal['pid'] > 1
             and not os.path.lexists(Path('/proc')/str(terminal['pid'])))
    raw,download = read('downloaded-receipt.json')
    need(hashlib.sha256(raw).hexdigest() == preservation.HISTORICAL_DOWNLOAD_SHA256
         and download.get('native_restore_accepted') is False)


def _generated_history(rows):
    """Authenticate historical sealed captures without changing publish age rules."""
    from . import entry, hydrate
    from paired_maintenance import entry as paired_entry, runtime
    need(Path(runtime.__file__).absolute() == entry.PAIRED_ROOT/'runtime.py')
    paired_entry.verify_install()
    path = Path('/usr/local/libexec/careertrellis/generated_object_recovery.py')
    raw = hydrate.protected(path)[0]
    need(hashlib.sha256(raw).hexdigest() == runtime.PINS['web'][str(path)])
    name = '_careertrellis_retention_generated_history'
    module = types.ModuleType(name); module.__file__ = str(path)
    previous = sys.modules.get(name); sys.modules[name] = module
    try:
        exec(compile(raw,str(path),'exec'),module.__dict__)
        import grp
        group = grp.getgrnam('careertrellis-object-recovery').gr_gid
        now = dt.datetime.now(dt.timezone.utc).timestamp()
        for row in rows:
            need(row['directory'] and re.fullmatch('[0-9a-f]{40}-[0-9]{8}-[0-9]{6}F',row['name']))
            root = module.CAPTURE_ROOT/row['name']
            module._protected_directory(root,group)
            need({path.name for path in root.iterdir()} == {'objects','marker','seal.json'})
            for directory_name in ('objects','objects/v1','marker'):
                module._protected_directory(root/directory_name,group)
            seal_raw = native_restore.protected(root/'seal.json',maximum=4096)
            seal = native_restore.decode(seal_raw)
            need(type(seal) is dict and set(seal) == {'backup_label','ciphertext_bytes',
                'manifest_sha256','manifest_verified','object_count','release','result','snapshot_id',
                'authority','capture_started_epoch','captured_epoch'})
            need(type(seal['capture_started_epoch']) is int and type(seal['captured_epoch']) is int
                 and 0 < seal['capture_started_epoch'] <= seal['captured_epoch'] <= now
                 and seal['authority'] == 'postgres-r2-authoritative')
            manifest = module.verify_manifest(root/'objects',root/'marker'/'snapshot-manifest.json',
                                              seal['manifest_sha256'])
            need(seal == dict(manifest.sanitized_summary(),authority=seal['authority'],
                capture_started_epoch=seal['capture_started_epoch'],captured_epoch=seal['captured_epoch'])
                 and manifest.snapshot_id == row['name'])
            need({path.name for path in (root/'marker').iterdir()} == {'snapshot-manifest.json'})
            # verify_manifest verifies exact paths, inventory and every byte;
            # independently retain the protected owner/mode/link boundary.
            import itertools
            paths = list(itertools.islice(root.rglob('*'),100001)); need(len(paths) <= 100000)
            for candidate in paths:
                meta = candidate.lstat()
                if candidate == root/'seal.json':
                    need(meta.st_uid == meta.st_gid == 0 and stat.S_IMODE(meta.st_mode) == 0o600)
                elif stat.S_ISDIR(meta.st_mode): module._protected_directory(candidate,group)
                else:
                    need(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and meta.st_uid == 0
                         and meta.st_gid == group and stat.S_IMODE(meta.st_mode) == 0o640)
    finally:
        if previous is None: sys.modules.pop(name,None)
        else: sys.modules[name] = previous


def _paired_capture_claim(value):
    """Closed historical capture-v1 format, independent of native imports."""
    policy._keys(value,('result','manifest_verified','release','backup_label','snapshot_id','authority',
                       'object_count','ciphertext_bytes','manifest_sha256','capture_started_epoch','captured_epoch'))
    need(value['result'] == 'pass' and value['manifest_verified'] is True
         and type(value['release']) is str and re.fullmatch('[0-9a-f]{40}',value['release'])
         and type(value['backup_label']) is str and re.fullmatch('[0-9]{8}-[0-9]{6}F',value['backup_label'])
         and value['snapshot_id'] == value['release']+'-'+value['backup_label']
         and value['authority'] == 'postgres-r2-authoritative'
         and type(value['object_count']) is int and 0 <= value['object_count'] <= 100000
         and type(value['ciphertext_bytes']) is int and 0 <= value['ciphertext_bytes'] <= 100000*(10*1024**2+64))
    policy._digest(value['manifest_sha256'])
    for key in ('capture_started_epoch','captured_epoch'):
        need(type(value[key]) in (int,float) and math.isfinite(value[key]) and 0 <= value[key] <= 10**12)
    need(value['capture_started_epoch'] <= value['captured_epoch'])


def paired_run_claim(role,transaction,documents,current_pair_sha,current_release):
    """Pure recognized terminal-chain contract; supplied records authenticate nothing."""
    need(role in ('web','db') and type(documents) is dict)
    known = {
        'web': {'status.json','prestart-original-status.json','prestart-aborted.json','complete.json',
            'start-intent.json','db-begin-intent.json','stop-intent.json','quiesced-result.json',
            'capture-dispatch-intent.json','capture-result.json','restore-dispatch-intent.json',
            'restore-intent.json','recovery-restore-intent.json','writers-restored.json','db-released.json',
            'publish-intent.json','publish-dispatch-intent.json','publication-result.json','peer-identity.json',
            'db-mirror-accepted.json','db-health-accepted.json','health-dispatch-intent.json','health-dispatched.json',
            'normal-reports-accepted.json','abort-intent.json','aborted-restored.json'},
        'db': {'status.json','begin-intent.json','worker-started.json','worker-dispatch.json',
            'quiesced.json','capture.json','restored.json','abort.json','abort-dispatch-intent.json',
            'backup-intent.json','backup-invocation.json','recovery-dispatch-intent.json',
            'mirror-intent.json','mirror-unit-intent.json','mirror-unit-dispatch.json',
            'health-intent.json','health-unit-intent.json','health-unit-dispatch.json',
            'complete-intent.json','complete-result.json'}}
    need(set(documents) <= known[role])
    status = documents['status.json']
    need(type(status) is dict and status.get('transaction') == transaction)
    paired_sha,release,boot = status.get('policy_sha256'),status.get('release'),status.get('boot_id')
    need((paired_sha,release) in ((LEGACY_PAIRED_POLICY,LEGACY_PAIRED_RELEASE),
                                 (current_pair_sha,current_release)))
    policy._digest(paired_sha)
    need(type(boot) is str and re.fullmatch(
        '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',boot))
    invocation = status.get('worker_invocation')
    need(type(invocation) is str and re.fullmatch('[0-9a-f]{32}',invocation))
    if role == 'web':
        if 'prestart-aborted.json' in documents:
            need(set(documents) == {'status.json','prestart-original-status.json','prestart-aborted.json'})
            proof,original = documents['prestart-aborted.json'],documents['prestart-original-status.json']
            need(proof.get('transaction') == transaction and proof.get('policy_sha256') == paired_sha
                 and proof.get('release') == release and proof.get('boot_id') == boot
                 and proof.get('original_status_sha256') == hashlib.sha256(bundle.canonical(original)).hexdigest()
                 and original.get('transaction') == transaction and original.get('policy_sha256') == paired_sha
                 and original.get('release') == release and original.get('boot_id') == boot
                 and proof.get('no_dispatch') is True and proof.get('services_unchanged') is True
                 and proof.get('services_restored') is True and proof.get('restoration_performed') is False
                 and status.get('services_restored') is True)
            state = 'prestart_aborted'
        else:
            terminal = documents.get('complete.json',documents.get('aborted-restored.json'))
            need(type(terminal) is dict and terminal.get('transaction') == transaction
                 and terminal.get('policy_sha256') == paired_sha and terminal.get('services_restored') is True)
            if 'start-intent.json' not in documents:
                need(terminal.get('state') == 'skipped'
                     and status.get('state') == 'idle' and status.get('services_restored') is True
                     and not any('dispatch' in name or name in ('stop-intent.json','publish-intent.json')
                                 for name in documents))
                state = 'skipped'
            else:
                original = documents['start-intent.json']
                policy._keys(original,('transaction','policy_sha256','identity'))
                identity = original['identity']; policy._keys(identity,('release','web_boot','web_invocation'))
                need(original['transaction'] == transaction and original['policy_sha256'] == paired_sha
                     and identity == {'release':release,'web_boot':boot,'web_invocation':invocation})
                need(documents.get('db-begin-intent.json') == identity)
                if 'aborted-restored.json' in documents:
                    need(documents.get('abort-intent.json') == identity
                         and status.get('state') in ('failed','aborted_restored')
                         and status.get('services_restored') is True
                         and 'publish-intent.json' not in documents
                         and 'publish-dispatch-intent.json' not in documents)
                    if 'stop-intent.json' in documents:
                        need(documents.get('restore-dispatch-intent.json') == {'restore_original_writers':True})
                    state = 'aborted_restored'
                else:
                    need(terminal.get('state') == 'complete' and status.get('state') == 'complete'
                         and status.get('services_restored') is True)
                    capture = documents['capture-result.json']
                    _paired_capture_claim(capture)
                    need(capture['release'] == release and documents.get('writers-restored.json') == {
                            'services_restored':True,'capture_sha256':hashlib.sha256(bundle.canonical(capture)).hexdigest()}
                         and documents.get('db-released.json') == {'services_restored':True})
                    publication = documents['publication-result.json']
                    need(publication.get('result') == 'onedrive_snapshot_created'
                         and all(publication.get(key) == capture[key] for key in
                                 ('release','backup_label','snapshot_id','manifest_sha256','authority'))
                         and terminal.get('backup_label') == capture['backup_label']
                         and terminal.get('manifest_sha256') == capture['manifest_sha256'])
                    mirror,health = documents['db-mirror-accepted.json'],documents['db-health-accepted.json']
                    need(mirror.get('state') == 'mirror_complete' and mirror.get('backup_label') == capture['backup_label']
                         and health.get('state') == 'health_complete' and health.get('normal_report_accepted') is True
                         and all(record.get('transaction') == transaction and record.get('policy_sha256') == paired_sha
                                 for record in (mirror,health)))
                    state = 'complete'
    else:
        original = documents['begin-intent.json']
        need(original.get('transaction') == transaction and original.get('policy_sha256') == paired_sha
             and original.get('operation') == 'begin' and original.get('payload',{}).get('release') == release)
        worker = documents['worker-started.json']
        need(worker == {'boot_id':boot,'worker_invocation':invocation})
        restored = documents['restored.json']
        need(restored.get('transaction') == transaction and restored.get('policy_sha256') == paired_sha
             and restored.get('operation') == 'restored' and restored.get('payload',{}).get('services_restored') is True
             and all(restored['payload'].get(key) == original['payload'][key]
                     for key in ('release','web_boot','web_invocation')))
        state = status.get('state'); need(state in ('complete','released','aborted'))
        if state == 'aborted':
            abort = documents['abort.json']
            need(abort.get('transaction') == transaction and abort.get('policy_sha256') == paired_sha
                 and abort.get('operation') == 'abort' and abort.get('payload') == original['payload']
                 and not any(name in documents for name in ('mirror-intent.json','health-intent.json','complete-result.json')))
        else:
            captured = documents['capture.json']
            need(captured.get('transaction') == transaction and captured.get('policy_sha256') == paired_sha
                 and captured.get('operation') == 'capture')
            capture = captured['payload']['capture']
            _paired_capture_claim(capture)
            need(capture['release'] == release and restored['payload'].get('capture_sha256') ==
                 hashlib.sha256(bundle.canonical(capture)).hexdigest())
            if state == 'complete':
                result = documents['complete-result.json']; mirror = documents['mirror-intent.json']
                need(status.get('services_restored') is True
                     and result == {'transaction':transaction,'policy_sha256':paired_sha,
                        'backup_label':capture['backup_label'],'manifest_sha256':capture['manifest_sha256']}
                     and mirror.get('payload',{}).get('backup_label') == capture['backup_label']
                     and mirror['payload'].get('publication_manifest_sha256') == capture['manifest_sha256'])
            else:
                need(not any(name in documents for name in ('mirror-intent.json','health-intent.json','complete-result.json')))
    return {'state':state,'transaction':transaction,'paired_policy_sha256':paired_sha,
            'release':release,'boot_id':boot,'original_invocation':invocation,'authenticated':False}


def validate_paired_handoff(documents,transaction,paired_policy_sha256,retention_policy_sha256,
                            release,parent_owner,child_owner,now):
    """SOURCE PROPOSAL ONLY: validate a same-TX post-restoration handoff.

    No native history or dispatcher consumes this result. It cannot clear an
    active paired unit, authorize a child, or replace approval of the proposed
    paired workflow change. A reviewed parent must still collect actual owners,
    unit/cgroup/source bindings and durably record one dispatch before use.
    """
    need(type(documents) is dict and type(transaction) is str and re.fullmatch('[0-9a-f]{32}',transaction))
    catalog.process_identity(parent_owner); catalog.process_identity(child_owner)
    need(parent_owner['pid'] != child_owner['pid'] and parent_owner['boot_id'] == child_owner['boot_id'])
    policy._digest(paired_policy_sha256); policy._digest(retention_policy_sha256)
    handoff,child = documents['bundle-dispatch-intent.json'],documents['bundle-child-dispatched.json']
    policy._keys(handoff,('schema','transaction','paired_policy_sha256','retention_policy_sha256','release',
        'backup_label','capture_sha256','publication_sha256','parent_owner','parent_unit','parent_invocation','issued_at'))
    policy._keys(child,('schema','transaction','handoff_sha256','owner','command'))
    need(handoff['schema'] == 'careertrellis.paired-retention-handoff/v1'
         and handoff['transaction'] == transaction and handoff['paired_policy_sha256'] == paired_policy_sha256
         and handoff['retention_policy_sha256'] == retention_policy_sha256 and handoff['release'] == release
         and handoff['parent_owner'] == parent_owner
         and handoff['parent_unit'] == 'careertrellis-paired-maintenance.service'
         and type(handoff['parent_invocation']) is str and re.fullmatch('[0-9a-f]{32}',handoff['parent_invocation']))
    current = policy.timestamp(now)
    need(0 <= (current-policy.timestamp(handoff['issued_at'])).total_seconds() <= 120)
    original = documents['start-intent.json']
    need(original == {'transaction':transaction,'policy_sha256':paired_policy_sha256,
        'identity':{'release':release,'web_boot':parent_owner['boot_id'],'web_invocation':handoff['parent_invocation']}})
    capture,publication = documents['capture-result.json'],documents['publication-result.json']
    _paired_capture_claim(capture)
    need(capture['release'] == release and handoff['backup_label'] == capture['backup_label']
         and capture['captured_epoch'] <= policy.timestamp(handoff['issued_at']).timestamp()
         and handoff['capture_sha256'] == hashlib.sha256(bundle.canonical(capture)).hexdigest()
         and handoff['publication_sha256'] == hashlib.sha256(bundle.canonical(publication)).hexdigest()
         and publication.get('result') == 'onedrive_snapshot_created'
         and all(publication.get(key) == capture[key] for key in
                 ('release','backup_label','snapshot_id','manifest_sha256','authority'))
         and documents.get('writers-restored.json') == {'services_restored':True,'capture_sha256':handoff['capture_sha256']}
         and documents.get('db-released.json') == {'services_restored':True})
    need(child == {'schema':'careertrellis.paired-retention-child/v1','transaction':transaction,
        'handoff_sha256':hashlib.sha256(bundle.canonical(handoff)).hexdigest(),'owner':child_owner,
        'command':['/usr/bin/python3','-I','-B','/usr/local/libexec/careertrellis/onedrive_retention/entry.py',
                   'web-run','--transaction',transaction]})
    return {'result':'consistent_post_restoration_handoff','authenticated':False,
            'continuation_authorized':False,'dispatch_authorized':False,'backup_accepted':False,
            'transaction':transaction,'required_backup_label':capture['backup_label']}


def _paired_unit_terminal(unit):
    """Bounded legacy-unit/cgroup terminal proof, with no state transition."""
    from . import hydrate
    need(re.fullmatch(r'careertrellis-paired-(?:maintenance|recovery|db(?:-recovery)?@[0-9a-f]{32})[.]service',unit))
    fields = ('LoadState','ActiveState','SubState','MainPID','ControlPID','Job','ControlGroup','InvocationID')
    raw = hydrate.command(['/usr/bin/systemctl','show',unit,'--all','--property='+','.join(fields)],output=True,timeout=10)
    need(len(raw) <= 16384)
    state = {}
    for line in raw.decode('ascii').splitlines():
        key,separator,value = line.partition('='); need(separator and key in fields and key not in state)
        state[key] = value
    need(set(state) == set(fields) and state['LoadState'] in ('loaded','not-found')
         and state['ActiveState'] in ('inactive','failed') and state['MainPID'] == state['ControlPID'] == '0'
         and state['Job'] == '')
    groups = {Path('/sys/fs/cgroup/system.slice')/unit}
    if '@' in unit:
        template = unit.split('@',1)[0].replace('-','\\x2d')
        groups.add(Path('/sys/fs/cgroup/system.slice')/('system-'+template+'.slice')/unit)
    if state['ControlGroup']:
        group = state['ControlGroup']
        need(group.startswith('/system.slice/') and '..' not in group.split('/')
             and all(re.fullmatch('[A-Za-z0-9_.@\\\\-]+',part) for part in group.split('/')[2:])
             and group.rsplit('/',1)[1] == unit)
        groups.add(Path('/sys/fs/cgroup')/group.lstrip('/'))
    for root in groups:
        try: meta = root.lstat()
        except FileNotFoundError: continue
        need(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022)
        import itertools
        files = list(itertools.islice(root.rglob('cgroup.procs'),65))
        need(0 < len(files) <= 64 and root/'cgroup.procs' in files)
        for path in files:
            meta = path.lstat(); need(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022)
            with path.open('rb') as stream: contents = stream.read(65537)
            need(len(contents) <= 65536 and not contents.strip())
    return state


def _paired_history(role,rows,context):
    from . import entry
    from paired_maintenance import entry as paired_entry,host,web_control
    paired_entry.verify_install()
    need(Path(host.__file__).absolute() == entry.PAIRED_ROOT/'host.py'
         and Path(web_control.__file__).absolute() == entry.PAIRED_ROOT/'web_control.py')
    root = Path('/var/lib/careertrellis/paired-maintenance')/role/'runs'
    for row in rows:
        transaction = row['name']; need(row['directory'] and re.fullmatch('[0-9a-f]{32}',transaction))
        run = root/transaction; files = _history_names(run); need(0 < len(files) <= 512)
        documents = {}
        for file in files:
            need(not file['directory'] and re.fullmatch('[a-z0-9][a-z0-9-]{0,95}[.]json',file['name']))
            raw = native_restore.protected(run/file['name'],maximum=131072)
            value = native_restore.decode(raw); need(bundle.canonical(value) == raw)
            documents[file['name']] = value
        claim = paired_run_claim(role,transaction,documents,context.bindings['paired_policy_sha256'],
                                 context.document['application_release'])
        if role == 'web':
            need(host._finished_claim({'transaction':transaction,'policy_sha256':claim['paired_policy_sha256']},
                                     claim['release'],claim['boot_id']) is True)
            for unit in ('careertrellis-paired-maintenance.service','careertrellis-paired-recovery.service'):
                _paired_unit_terminal(unit)
        else:
            for suffix in ('','-recovery'):
                _paired_unit_terminal('careertrellis-paired-db'+suffix+'@'+transaction+'.service')


def native_history_snapshot(transaction, stage, repository_lock_fd=None, *, inherited_reservation=None):
    """Fresh provider-free history census for an authenticated preflight channel.

    This is not a progress endpoint: the caller must schedule it as a one-shot
    admission/preflight operation. Unknown older/manual schemas are reported as
    unresolved and cannot be converted into completed attempts by an absent unit
    or an acquired lock. No authentication claim survives JSON serialization.
    """
    from . import baseline, entry, preservation
    need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}',transaction)
         and stage in ('hydrate','publish','restore','prepare','retire'))
    context = baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated
         and Path(__file__).absolute() == entry.ROOT / 'guard.py')
    reservation_path = ('/run/lock/careertrellis-maintenance.lock' if context.role == 'web'
                        else '/run/lock/careertrellis-pgbackrest-repository.lock')
    if context.role == 'db': need(repository_lock_fd is not None)
    if inherited_reservation is None:
        reservation_identity = preservation.require_owned_lock(reservation_path,
            repository_lock_fd if context.role == 'db' else None)
    else:
        need(context.role == 'web' and type(inherited_reservation) is preservation.InheritedObjectReservation
             and inherited_reservation.authenticated)
        reservation_identity = preservation._inherited_lock(reservation_path,
            inherited_reservation.maintenance_fd,inherited_reservation.parent)
        preservation._inherited_lock('/run/lock/careertrellis-generated-object-recovery.lock',
            inherited_reservation.recovery_fd,inherited_reservation.parent)
    roots, unresolved = [], []
    for category, root in HISTORY_ROOTS[context.role]:
        try:
            rows = _history_names(root)
        except Exception:
            unresolved.append({'category':category,'reason':'root_missing_or_unsafe'})
            continue
        # Root metadata proves an exhaustive census, not successful operations.
        # The per-schema normalizer must separately bind every extant attempt.
        roots.append({'category':category,'root':root,'entry_count':len(rows),
                      'inventory_sha256':hashlib.sha256(bundle.canonical(rows)).hexdigest()})
        if rows:
            try:
                if root == '/var/backups/careertrellis/backup-health/operations':
                    _legacy_history(category,rows)
                elif category == 'catalog_admission':
                    need(repository_lock_fd is not None)
                    store = catalog.NativeStore(repository_lock_fd)
                    with store.io.locked():
                        records = store.records(); store.retired(records)
                        need({row['name'] for row in rows} == {'accepted','intents','complete','holds',
                            'tombstones','tombstone-intents','tombstone-complete','catalog.lock'})
                elif category in ('hydration','publication','native_restore','retirement'):
                    _new_stage_history(category,rows,transaction,stage,context)
                    if category == 'hydration':
                        for row in rows: _source_auxiliary_history(row['name'])
                elif category == 'stage_dispatch':
                    _endpoint_history(rows,transaction,stage,context)
                elif category == 'paired_runs':
                    _paired_history(context.role,rows,context)
                elif root == '/var/lib/careertrellis/generated-object-captures':
                    _generated_history(rows)
                elif root == '/var/lib/careertrellis/generated-object-recovery/work':
                    need(inherited_reservation is not None and len(rows) == 1)
                    current = rows[0]
                    need(current['directory'] and re.fullmatch('mirror-'+inherited_reservation.release+'-'
                        +inherited_reservation.backup_label+'[.][A-Za-z0-9]{6}',current['name']))
                else:
                    raise GuardRefused('historical_attempt_binding_required')
            except Exception:
                unresolved.append({'category':category,'reason':'historical_attempt_binding_required'})
    observed = dt.datetime.now(dt.timezone.utc).isoformat()
    return {'schema':'careertrellis.onedrive-host-history/v1','role':context.role,
        'transaction':transaction,'stage':stage,'observed_at':observed,
        'baseline_bindings':context.bindings,'roots':roots,'unresolved':unresolved,
        'complete':not unresolved,'growth_authorized':False,'deletion_authorized':False,
        'authentication':'requires_authenticated_transport',
        'owner':native_restore.process_identity(os.getpid()),
        'reservation':{'path':reservation_path,'identity':reservation_identity,'held':True}}


def validate_peer_history(value, transaction, stage, policy_sha256=None, *, expected_role='web', now=None):
    """Validate protocol claims; authentication belongs to protected intent/SSH.

    This validator alone never converts an arbitrary caller dictionary into a
    trusted peer. DB native checking reads only the immutable stage intent saved
    by the authenticated paired endpoint.
    """
    need(expected_role in ('web','db') and type(value) is dict and set(value) == {
        'schema','role','transaction','stage','observed_at','baseline_bindings','roots','unresolved',
        'complete','growth_authorized','deletion_authorized','authentication','owner','reservation'})
    need(value['schema'] == 'careertrellis.onedrive-host-history/v1' and value['role'] == expected_role
         and value['transaction'] == transaction and value['stage'] == stage
         and value['complete'] is True and value['unresolved'] == []
         and value['growth_authorized'] is False and value['deletion_authorized'] is False
         and value['authentication'] == 'requires_authenticated_transport')
    current = dt.datetime.now(dt.timezone.utc) if now is None else policy.timestamp(now)
    need(0 <= (current-policy.timestamp(value['observed_at'])).total_seconds() <= 120)
    catalog.process_identity(value['owner'])
    bindings = value['baseline_bindings']
    policy._keys(bindings,('standalone_manifest_sha256','paired_manifest_sha256',
                          'paired_policy_sha256','standalone_policy_sha256'))
    for digest in bindings.values(): policy._digest(digest)
    if policy_sha256 is not None: need(bindings['standalone_policy_sha256'] == policy_sha256)
    roots = value['roots']
    need(type(roots) is list and len(roots) == len(HISTORY_ROOTS[expected_role]))
    expected = set(HISTORY_ROOTS[expected_role]); seen = set()
    for row in roots:
        policy._keys(row,('category','root','entry_count','inventory_sha256'))
        identity = (row['category'],row['root'])
        need(identity in expected and identity not in seen)
        need(type(row['entry_count']) is int and 0 <= row['entry_count'] <= MAX_RECORDS)
        policy._digest(row['inventory_sha256']); seen.add(identity)
    need(seen == expected)
    reservation = value['reservation']
    policy._keys(reservation,('path','identity','held'))
    need(reservation['path'] == ('/run/lock/careertrellis-maintenance.lock' if expected_role == 'web'
                                 else '/run/lock/careertrellis-pgbackrest-repository.lock')
         and reservation['held'] is True)
    identity = reservation['identity']; policy._keys(identity,('device','inode','owner_pid'))
    need(type(identity['device']) is int and identity['device'] >= 0
         and type(identity['inode']) is int and identity['inode'] > 0
         and type(identity['owner_pid']) is int and identity['owner_pid'] == value['owner']['pid'])
    return value


def native_web_witness(transaction, stage, context):
    """Only the authenticated endpoint's fixed protected intent supplies WEB facts."""
    from . import baseline, coordinator
    need(type(context) is baseline.AuthenticatedContext and context.authenticated and context.role == 'db'
         and type(transaction) is str and re.fullmatch('[0-9a-f]{32}',transaction)
         and stage in ('hydrate','publish','restore','prepare','retire'))
    path = coordinator.STATE/'db'/transaction/stage/'intent.json'
    raw = native_restore.protected(path,maximum=262144)
    intent = native_restore.decode(raw)
    need(bundle.canonical(intent) == raw and intent.get('capture_transaction') == transaction
         and intent.get('operation') == 'bundle_'+stage
         and intent.get('retention_policy_sha256') == context.bindings['standalone_policy_sha256'])
    witness = validate_peer_history(intent['payload']['web_history'],transaction,stage,
                                   context.bindings['standalone_policy_sha256'])
    need(witness['baseline_bindings'] == context.bindings)
    return witness


class AuthenticatedPolicyArchive:
    """Read-only historical acceptance provenance, never an action policy."""
    __slots__ = ('_document','_bindings','_sha','_seal')
    def __init__(self,document,bindings,sha,seal=None):
        need(seal is _POLICY_ARCHIVE_SEAL)
        self._document,self._bindings,self._sha,self._seal = (
            bundle.canonical(document),bundle.canonical(bindings),sha,seal)
    @property
    def authenticated(self): return self._seal is _POLICY_ARCHIVE_SEAL
    @property
    def current_bindings(self): return json.loads(self._bindings)
    @property
    def archive_policy_sha(self): return self._sha
    @property
    def policy(self): return json.loads(self._document)['policy']


def authenticated_policy_archive(policy_sha256):
    """Load only an installer's fixed root-private accepted-policy archive.

    The installer must preserve this record while the old private/public policy
    and accepted baseline are still exactly verified. This reader never creates
    an archive or reinterprets a previous policy as authority for a new action.
    """
    from . import baseline, coordinator, entry
    policy._digest(policy_sha256)
    need(Path(__file__).absolute() == entry.ROOT/'guard.py')
    context = baseline.native_context()
    need(type(context) is baseline.AuthenticatedContext and context.authenticated)
    path = Path('/etc/careertrellis/onedrive-retention/accepted-policies')/(policy_sha256+'.json')
    raw = native_restore.protected(path,maximum=262144)
    document = native_restore.decode(raw)
    policy._keys(document,('schema','policy','accepted_baseline','accepted_baseline_sha256','archived_at'))
    need(bundle.canonical(document) == raw
         and document['schema'] == 'careertrellis.onedrive-accepted-policy-archive/v1'
         and hashlib.sha256(bundle.canonical(document['policy'])).hexdigest() == policy_sha256)
    old_policy = coordinator.Policy(document['policy'])
    old = document['accepted_baseline']
    policy._keys(old,('schema','standalone_revision','standalone_manifest_sha256','paired_revision',
        'paired_manifest_sha256','paired_policy_sha256','application_release','accepted_at','valid_until',
        'acceptance_receipts'))
    need(old['schema'] == 'careertrellis.onedrive-retention-accepted-baseline/v1'
         and hashlib.sha256(bundle.canonical(old)).hexdigest() == document['accepted_baseline_sha256']
         and old['standalone_revision'] == old_policy.data['artifact_revision']
         and old['paired_policy_sha256'] == old_policy.data['paired_policy_sha256']
         and old['application_release'] == old_policy.data['application_release'])
    for key in ('standalone_manifest_sha256','paired_manifest_sha256','paired_policy_sha256'):
        policy._digest(old[key])
    for key in ('standalone_revision','paired_revision','application_release'):
        need(type(old[key]) is str and re.fullmatch('[0-9a-f]{40}',old[key]) and old[key] != '0'*40)
    refs = old['acceptance_receipts']; policy._keys(refs,('ops','native_controls','independent_review'))
    for digest in refs.values(): policy._digest(digest); need(digest != '0'*64)
    accepted,archived = policy.timestamp(old['accepted_at']),policy.timestamp(document['archived_at'])
    expiry = policy.timestamp(old['valid_until'])
    need(accepted <= archived <= dt.datetime.now(dt.timezone.utc)
         and accepted < expiry <= policy.timestamp(old_policy.data['valid_until']))
    need(baseline.native_context().bindings == context.bindings)
    return AuthenticatedPolicyArchive(document,context.bindings,policy_sha256,_POLICY_ARCHIVE_SEAL)


def _object_configuration():
    """Read the fixed service-owned private config without permissive ancestors."""
    import pwd
    account = pwd.getpwnam('careertrellis-object-recovery')
    state = Path('/var/lib/careertrellis/generated-object-recovery')
    root = os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        current = Path('/')
        for part in state.parts[1:]:
            child = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
            os.close(root); root = child; current /= part
            meta = os.fstat(root)
            if current == state:
                need(meta.st_uid == account.pw_uid and meta.st_gid == account.pw_gid
                     and stat.S_IMODE(meta.st_mode) == 0o700)
            else: need(meta.st_uid == 0 and not meta.st_mode & 0o022)
        before = os.stat('rclone.conf',dir_fd=root,follow_symlinks=False)
        fd = os.open('rclone.conf',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root)
        try:
            def identity(value):
                return (value.st_dev,value.st_ino,value.st_uid,value.st_gid,value.st_mode,
                        value.st_nlink,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
            need(identity(before) == identity(os.fstat(fd)) and stat.S_ISREG(before.st_mode)
                 and before.st_uid == account.pw_uid and before.st_gid == account.pw_gid
                 and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                 and before.st_size <= 65536)
            raw = bytearray()
            while len(raw) <= 65536:
                part = os.read(fd,min(8192,65537-len(raw)))
                if not part: break
                raw.extend(part)
            need(len(raw) == before.st_size and identity(os.fstat(fd)) == identity(before)
                 and identity(os.stat('rclone.conf',dir_fd=root,follow_symlinks=False)) == identity(before))
            return bytes(raw),identity(before)
        finally: os.close(fd)
    finally: os.close(root)


def legacy_object_guard(release,backup_label,maintenance_fd=8,recovery_fd=9):
    """Authenticate the existing WEB object's sealed source and shared capacity.

    The actual pinned shell parent retains both inherited same-description locks
    through its subsequent upload. This guard never takes over the helper, buys
    storage, changes source data, rewrites credentials, or uploads a byte.
    """
    from . import baseline, entry, hydrate, preservation
    from paired_maintenance import client, entry as paired_entry,runtime
    reservation = preservation.inherited_object_reservation(
        release,backup_label,maintenance_fd,recovery_fd)
    context = baseline.native_context()
    need(context.authenticated and context.role == 'web' and context.document['application_release'] == release)
    need(Path(client.__file__).absolute() == entry.PAIRED_ROOT/'client.py'
         and Path(runtime.__file__).absolute() == entry.PAIRED_ROOT/'runtime.py')
    paired_entry.verify_install(); paired = paired_entry.load_policy()
    need(paired.digest == context.bindings['paired_policy_sha256'])
    core = '/usr/local/libexec/careertrellis/generated_object_recovery.py'
    authority = '/usr/local/sbin/careertrellis-authority'
    need(hashlib.sha256(hydrate.protected(core,0o644)[0]).hexdigest() == runtime.PINS['web'][core]
         and hashlib.sha256(hydrate.protected(authority,0o755)[0]).hexdigest() == runtime.PINS['web'][authority])
    observed_authority = hydrate.command([authority,'status'],output=True,timeout=10)
    need(observed_authority == b'postgres-r2-authoritative\n')
    import grp
    group = grp.getgrnam('careertrellis-object-recovery').gr_gid
    def capture():
        raw = hydrate.command(['/usr/bin/python3','-I','-B',core,'verify-capture',release,backup_label,
            'postgres-r2-authoritative',str(group)],descriptor=maintenance_fd,
            lock_descriptor=recovery_fd,output=True,timeout=600)
        need(len(raw) <= 16384)
        value = native_restore.decode(raw)
        need(type(value) is dict and value.get('manifest_verified') is True
             and value.get('release') == release and value.get('backup_label') == backup_label
             and value.get('snapshot_id') == release+'-'+backup_label
             and type(value.get('ciphertext_bytes')) is int and value['ciphertext_bytes'] >= 0)
        return value
    sealed = capture()
    config = _object_configuration()
    parser = configparser.ConfigParser(interpolation=None,strict=True)
    parser.read_string(config[0].decode('utf-8'))
    need(not parser.defaults() and set(parser.sections()) == {'generated-object-onedrive'}
         and parser['generated-object-onedrive'].get('type') == 'onedrive')
    scope = drive_scope(parser['generated-object-onedrive'].get('drive_id'))
    transaction = hashlib.sha256((release+'-'+backup_label).encode('ascii')).hexdigest()[:32]
    local = native_history_snapshot(transaction,'prepare',inherited_reservation=reservation)
    validate_peer_history(local,transaction,'prepare',context.bindings['standalone_policy_sha256'])
    # This is the actual authenticated fixed-host client call, not a factory
    # that blesses a caller-supplied response dictionary.
    peer = client.Client(paired,transaction).call('retention_capacity_snapshot',{
        'stage':'prepare','retention_policy_sha256':context.bindings['standalone_policy_sha256']})
    policy._keys(peer,('schema','history','quota','namespace_bytes','observed_at','baseline_bindings','account_scope'))
    need(peer['schema'] == 'careertrellis.retention-capacity-preflight/v1'
         and peer['baseline_bindings'] == context.bindings and peer['account_scope'] == scope)
    validate_peer_history(peer['history'],transaction,'prepare',context.bindings['standalone_policy_sha256'],expected_role='db')
    fresh(peer['observed_at'],dt.datetime.now(dt.timezone.utc))
    quota = peer['quota']; policy._keys(quota,('total','free','observed_at'))
    fresh(quota['observed_at'],dt.datetime.now(dt.timezone.utc))
    policy.integer(peer['namespace_bytes'])
    marker = Path('/var/lib/careertrellis/generated-object-captures')/(release+'-'+backup_label)/'marker'/'snapshot-manifest.json'
    manifest_raw = native_restore.protected(marker,maximum=bundle.MAX_MANIFEST,modes=(0o640,),gids=(group,))
    need(hashlib.sha256(manifest_raw).hexdigest() == sealed['manifest_sha256'])
    source_bytes = sealed['ciphertext_bytes']+len(manifest_raw)
    from . import coordinator
    policy_raw,document = entry._protected_document(coordinator.PRIVATE_POLICY,0o600)
    live = coordinator.Policy(document); coordinator.native_policy_binding(live)
    live.require_active(dt.datetime.now(dt.timezone.utc))
    assessment = capacity(quota['total'],quota['free'],source_bytes,source_bytes,peer['namespace_bytes'],0,
        reserve_percent=document['reserve_percent'],peak_multiplier=document['peak_multiplier'],
        namespace_budget_bytes=document['namespace_budget_bytes'])
    need(assessment['allowed'] and capture() == sealed and _object_configuration() == config
         and baseline.native_context().bindings == context.bindings)
    preservation._inherited_lock('/run/lock/careertrellis-maintenance.lock',maintenance_fd,reservation.parent)
    preservation._inherited_lock('/run/lock/careertrellis-generated-object-recovery.lock',recovery_fd,reservation.parent)
    return {'schema':'careertrellis.onedrive-object-growth-guard/v1','allowed':True,'authenticated':True,
        'capacity':assessment,'source_bytes':source_bytes,'provider_writes':0,
        'deletion_authorized':False,'automatic_retry':False}
