"""Pure persistent-attempt and capacity guard; no native enforcement exists here.

Parsed evidence is not authentication. The future reviewed entry must collect and
authenticate protected journals, all persistent roots, accepted catalog records,
provider identities and unit/process proofs before using this assessment. A free
flock, absent systemd unit or elapsed timeout alone never proves completion.
No files, state, services, provider calls or attempt records are changed here.

The INFO-only exception permits its designated next attempt, not general history
resolution. Even an accepted replacement cannot silently clear the old failure:
an authenticated replacement/reconciliation history-resolution contract remains
unimplemented. Until supplied, unrelated later growth remains blocked.
"""
import datetime as dt
import hashlib
import re

from . import bundle, catalog, native_restore, policy, retire


COVERAGE = ('hydration', 'publication', 'native_restore', 'catalog_admission',
            'retirement', 'legacy_pg_mirror', 'legacy_object_mirror')
OPERATIONS = ('bundle_capture', 'bundle_publish', 'bundle_restore',
              'legacy_pg_mirror', 'legacy_object_mirror')
MAX_RECORDS = 10000


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


def native_check(*_args, **_kwargs):
    """No native entry/collector/installation is supplied by this pure module."""
    raise GuardRefused('native_growth_guard_unavailable')
