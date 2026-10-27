"""Exact-object retirement transaction, currently OFFLINE adapters only.

No native/provider adapter exists here. Default/native execution refuses. Offline
receipts and terminal records are explicitly simulations and MUST NOT become
accepted catalog tombstones or clear operational alerts.

A future reviewed adapter must authenticate snapshots/receipt bindings, own the
canonical repository lock and durable interruption fences, and store once()
records privately and exclusively with fsync before acknowledging their digest.
Unfinished operations block new work; unknown outcomes require reconciliation,
never another delete call. Recoverable single-object deletion is the only API:
there is no sync, account-trash, permanent-delete or legacy/source operation.
"""
import copy
import datetime as dt
import hashlib
import re

from . import bundle, coordinator, policy


NAMESPACE = 'onedrive:job-applier-staging/postgresql-bundles'
LOCK = '/run/lock/careertrellis-pgbackrest-repository.lock'


class RetirementRefused(ValueError):
    """Fixed public category; backend exceptions may contain private details."""


def need(value, code='retirement_requires_reconciliation'):
    if not value:
        raise RetirementRefused(code)


def digest(value):
    return hashlib.sha256(bundle.canonical(value)).hexdigest()


def fresh(value, now):
    observed = policy.timestamp(value)
    need(0 <= (now - observed).total_seconds() <= policy.MAX_INVENTORY_AGE_SECONDS)


def objects(catalog):
    result = {}
    for entry in catalog['entries']:
        for obj in entry['objects']:
            policy._keys(obj, ('path', 'object_id', 'size', 'sha256'))
            policy._path(obj['path']); policy._text(obj['object_id']); policy._digest(obj['sha256'])
            policy.integer(obj['size'], 1)
            need(obj['path'] not in result)
            result[obj['path']] = copy.deepcopy(obj)
        need(any(obj['path'] == entry['id'] + '/repository.tar'
                 and obj['sha256'] == entry['archive_sha256'] for obj in entry['objects']))
    return result


def prepare(catalog, now):
    """Pure planner wrapper with validated zero/one-bundle bootstrap behavior."""
    current = policy.timestamp(now)
    policy._keys(catalog, ('schema', 'observed_at', 'entries', 'holds'))
    need(catalog['schema'] == 'careertrellis.onedrive-bundle-catalog/v1')
    fresh(catalog['observed_at'], current)
    need(type(catalog['entries']) is list and type(catalog['holds']) is list)
    if len(catalog['entries']) >= 2:
        result = policy.plan_retirement(catalog, now)
        objects(catalog)
        return result
    # Bootstrap never fabricates a second accepted generation to make the normal
    # planner run. Validate the single accepted entry and preserve every object.
    ids = set()
    for entry in catalog['entries']:
        policy._keys(entry, ('id', 'accepted_at', 'archive_sha256', 'download_sha256',
            'restore_archive_sha256', 'native_restore_receipt_sha256', 'complete',
            'current', 'dependencies_resolved', 'dependencies', 'objects'))
        for field in ('id', 'archive_sha256', 'download_sha256', 'restore_archive_sha256',
                      'native_restore_receipt_sha256'):
            policy._digest(entry[field])
        need(entry['complete'] is True and entry['dependencies_resolved'] is True
             and type(entry['current']) is bool and entry['dependencies'] == [])
        need(entry['archive_sha256'] == entry['download_sha256'] == entry['restore_archive_sha256'])
        need(policy.timestamp(entry['accepted_at']) <= policy.timestamp(catalog['observed_at']))
        need(type(entry['objects']) is list and 0 < len(entry['objects']) <= 2)
        for obj in entry['objects']:
            need(type(obj) is dict and type(obj.get('path')) is str
                 and obj['path'] in (entry['id'] + '/repository.tar', entry['id'] + '/manifest.json'))
        ids.add(entry['id'])
    names = set()
    for hold in catalog['holds']:
        policy._keys(hold, ('name', 'owner', 'reason', 'review_after', 'bundles'))
        need(type(hold['name']) is str and policy.NAME.fullmatch(hold['name'])
             and hold['name'] not in names)
        names.add(hold['name']); policy._text(hold['owner']); policy._text(hold['reason'])
        policy.timestamp(hold['review_after'])
        need(type(hold['bundles']) is list and 0 < len(hold['bundles']) <= 10000
             and all(type(key) is str and key in ids for key in hold['bundles']))
    rows = objects(catalog)
    need(len({row['object_id'] for row in rows.values()}) == len(rows))
    size = sum(row['size'] for row in rows.values()); need(size <= policy.NAMESPACE_BUDGET)
    return {'schema': 'careertrellis.onedrive-retirement-plan/v1',
            'catalog_sha256': hashlib.sha256(policy.canonical(catalog)).hexdigest(),
            'planned_at': now, 'namespace_bytes': size, 'retained_bundle_ids': sorted(ids),
            'candidates': [], 'requires_locked_revalidation': True, 'deletions_performed': 0,
            'bootstrap': True}


def clock(backend):
    now = backend.now()
    need(type(now) is dt.datetime and now.utcoffset() == dt.timedelta(0))
    return now


def quota(backend):
    value = backend.quota()
    policy._keys(value, ('total', 'free', 'observed_at'))
    need(type(value['total']) is int and type(value['free']) is int
         and 0 <= value['free'] <= value['total'] and value['total'] > 0)
    fresh(value['observed_at'], clock(backend))
    return copy.deepcopy(value)


def snapshot(backend, transaction, completed=()):
    """Validate a fresh authenticated snapshot while the canonical lock is held.

    completed contains only objects with known successful acknowledgements and
    observed absence in this invocation. It is not a reusable deletion allowlist.
    The backend must report an earlier/unknown invocation as unresolved even if
    the caller reuses its transaction ID; current owned progress is not unknown.
    """
    value = backend.trusted_snapshot(transaction)
    policy._keys(value, ('policy', 'catalog', 'inventory', 'repository_idle', 'unresolved_retirement'))
    need(value['repository_idle'] is True and value['unresolved_retirement'] is False)
    active = coordinator.Policy(value['policy']); now = clock(backend)
    active.require_active(now)
    # The hard-coded planner must not silently weaken stricter active policies.
    need(active.data['keep_verified'] == 2 and active.data['grace_seconds'] == policy.GRACE_SECONDS
         and active.data['namespace_budget_bytes'] == policy.NAMESPACE_BUDGET,
         'retirement_policy_profile_unsupported')
    plan = prepare(value['catalog'], now.isoformat())
    inventory = value['inventory']
    policy._keys(inventory, ('schema', 'namespace', 'observed_at', 'objects'))
    need(inventory['schema'] == 'careertrellis.onedrive-bundle-inventory/v1'
         and inventory['namespace'] == NAMESPACE and type(inventory['objects']) is list)
    fresh(inventory['observed_at'], now)
    expected = objects(value['catalog'])
    for removed in completed:
        need(expected.get(removed['path']) == removed)
        del expected[removed['path']]
    actual = {}
    for obj in inventory['objects']:
        policy._keys(obj, ('path', 'object_id', 'size', 'sha256'))
        policy._path(obj['path']); policy._text(obj['object_id']); policy._digest(obj['sha256'])
        policy.integer(obj['size'], 1)
        need(obj['path'] not in actual)
        actual[obj['path']] = obj
    need(actual == expected, 'retirement_inventory_diverged')
    return copy.deepcopy(value), plan, active.digest


def comparable(catalog):
    value = copy.deepcopy(catalog); value.pop('observed_at')
    return bundle.canonical(value)


def run(transaction, backend=None):
    """Run an offline exact-object transaction; native/provider work refuses.

    Backend contract: locked(LOCK), now(), trusted_snapshot(tx), quota(),
    once(tx,name,value)->SHA256(bundle.canonical(value)), and
    delete_exact(namespace,object,request_id,recoverable=True,permanent=False).
    once is durable/exclusive; a duplicate or lost acknowledgement must refuse.
    The canonical-lock context must preserve interruption evidence/fences rather
    than make an unknown operation look disposable. No retry path is provided.
    """
    need(type(transaction) is str and re.fullmatch('[0-9a-f]{32}', transaction))
    need(backend is not None and getattr(backend, 'mode', None) == 'offline-test',
         'native_retirement_adapter_unsupported')
    try:
        with backend.locked(LOCK):
            baseline, plan, policy_sha = snapshot(backend, transaction)
            before = quota(backend)
            operation = {'schema': 'careertrellis.bundle-retirement-simulation-intent/v1',
                'transaction': transaction, 'namespace': NAMESPACE, 'retention_policy_sha256': policy_sha,
                'catalog_sha256': plan['catalog_sha256'], 'plan': plan, 'simulation': True}

            def once(name, value):
                expected = digest(value)
                need(backend.once(transaction, name, copy.deepcopy(value)) == expected)
                return expected

            operation_sha = once('operation-intent.json', operation)
            completed, terminals = [], []
            for candidate in plan['candidates']:
                for obj in candidate['objects']:
                    current, revised, current_policy = snapshot(backend, transaction, completed)
                    need(current_policy == policy_sha and comparable(current['catalog']) == comparable(baseline['catalog']))
                    need(revised['candidates'] == plan['candidates']
                         and revised['retained_bundle_ids'] == plan['retained_bundle_ids'])
                    index = len(completed)
                    intent = {'schema': 'careertrellis.bundle-object-retirement-intent/v1',
                        'transaction': transaction, 'sequence': index, 'namespace': NAMESPACE,
                        'bundle_id': candidate['bundle_id'], 'object': obj,
                        'successor_id': candidate['successor_id'],
                        'successor_accepted_at': candidate['successor_accepted_at'],
                        'operation_intent_sha256': operation_sha, 'simulation': True}
                    intent_sha = once('object-%05d-intent.json' % index, intent)
                    once('object-%05d-dispatch.json' % index, {'intent_sha256': intent_sha, 'simulation': True})
                    # Sole dispatch. Any exception, malformed acknowledgement or
                    # failure to durably publish it ends this invocation.
                    ack = backend.delete_exact(NAMESPACE, copy.deepcopy(obj), intent_sha,
                                               recoverable=True, permanent=False)
                    policy._keys(ack, ('result', 'object_id', 'request_id', 'recoverable', 'permanent'))
                    need(ack['result'] == 'deleted' and ack['object_id'] == obj['object_id']
                         and ack['request_id'] == intent_sha and ack['recoverable'] is True
                         and ack['permanent'] is False)
                    ack_sha = once('object-%05d-ack.json' % index, ack)
                    acknowledged_at = clock(backend)
                    absent, _revised, absent_policy = snapshot(backend, transaction, completed + [obj])
                    need(absent_policy == policy_sha and comparable(absent['catalog']) == comparable(baseline['catalog']))
                    need(policy.timestamp(absent['inventory']['observed_at']) >= acknowledged_at,
                         'retirement_absence_observation_predates_acknowledgement')
                    terminal = {'schema': 'careertrellis.bundle-object-retirement-terminal/v1',
                        'transaction': transaction, 'sequence': index, 'namespace': NAMESPACE,
                        'bundle_id': candidate['bundle_id'], 'object': obj, 'intent_sha256': intent_sha,
                        'dispatch_ack_sha256': ack_sha, 'absent': True,
                        'absence_observed_at': absent['inventory']['observed_at'], 'simulation': True}
                    terminals.append(once('object-%05d-terminal.json' % index, terminal))
                    completed.append(copy.deepcopy(obj))
            after = quota(backend)
            result = {'schema': 'careertrellis.bundle-retirement-simulation/v1',
                'result': 'simulated_complete', 'transaction': transaction,
                'catalog_sha256': plan['catalog_sha256'], 'retention_policy_sha256': policy_sha,
                'native_retirement_accepted': False, 'deletions_performed': 0,
                'simulated_retired_count': len(completed),
                'simulated_logical_bytes': sum(obj['size'] for obj in completed),
                'terminal_receipt_sha256': terminals, 'quota_before': before, 'quota_after': after,
                'provider_reported_free_delta_bytes': after['free'] - before['free']
                    if after['total'] == before['total'] else None,
                'physical_bytes_reclaimed': None, 'bootstrap': plan.get('bootstrap', False)}
            once('operation-complete.json', result)
            return result
    except BaseException:
        # No repeated append/delete, cleanup, accepted tombstone or replay. The
        # unfinished exclusive intent is authoritative reconciliation evidence.
        raise RetirementRefused('retirement_requires_reconciliation') from None
