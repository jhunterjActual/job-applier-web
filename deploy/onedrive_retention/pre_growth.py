"""OFFLINE preparation of retirement-only work before a growth capacity check.

This module is intentionally outside the installed FILES list. No native entry,
inventory, dispatch, deletion or provider implementation exists here. Parsed
catalog/witness claims are not authentication; all results remain simulations.

Future integration contract:
* Add a separately reviewed `retire_existing_before_growth` operation and finite
  parent/child units with exclusive birth mappings, baseline/policy binding and
  persistent unfinished-attempt guards. A status poll never dispatches work.
* Refresh identities of EXISTING accepted archives only, under native custody.
  Do not use execution.prepare_retirement: it requires a new recovered candidate
  and admits it, which recreates the capacity liveness trap.
* Snapshot/revalidate under the normal ordered maintenance/repository fences.
  Release those fences before child dispatch; the retirement child acquires its
  own fences and runs the existing exact retirement transaction. Parent journal
  ownership must not be one of the child's locks. No retry after lost dispatch.
* Extend native retire owner/idle/birth guards explicitly for this route rather
  than spoofing a completed new-growth pipeline. Authenticate at least two actual
  native acceptances, holds/dependencies, fresh downloaded identity/eTag witnesses
  and exact provider inventory. Keep existing 48h/two-newer rules unchanged.
* Commit known-ack retirement/catalog tombstones, then obtain NEW actual quota
  and whole-namespace facts. Only the ordinary fresh growth guard may authorize
  an upload. Planned deletion, logical bytes and this simulation grant no credit.
"""
import copy
import datetime as dt
import hashlib
import re

from . import bundle, coordinator, graph_delete, guard, policy, retire

SCHEMA = 'careertrellis.onedrive-pre-growth-request/v1'
OPERATION = 'retire_existing_before_growth'
MAX_BYTES = 32 * 1024**2


class PreGrowthRefused(ValueError):
    pass


def need(value):
    if not value: raise PreGrowthRefused('pre_growth_requires_reconciliation')


def sha(value): return hashlib.sha256(bundle.canonical(value)).hexdigest()


def validate_request(value):
    need(type(value) is dict and set(value) == {'schema', 'operation', 'transaction',
        'retention_policy_sha256', 'allocation'} and value['schema'] == SCHEMA and value['operation'] == OPERATION)
    need(type(value['transaction']) is str and re.fullmatch('[0-9a-f]{32}', value['transaction'])
         and type(value['retention_policy_sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['retention_policy_sha256']))
    allocation = value['allocation']
    need(type(allocation) is dict and set(allocation) == {'source_bytes', 'candidate_bytes', 'namespace_new_bytes'})
    need(all(type(size) is int and 0 <= size <= policy.NAMESPACE_BUDGET for size in allocation.values()))
    need(allocation['candidate_bytes'] > 0 and allocation['namespace_new_bytes'] >= allocation['candidate_bytes'])
    return copy.deepcopy(value)


def fresh(value, now):
    observed = policy.timestamp(value)
    need(0 <= (now-observed).total_seconds() <= policy.MAX_INVENTORY_AGE_SECONDS)


def _inventory(value, expected, now):
    policy._keys(value, ('schema', 'namespace', 'observed_at', 'objects'))
    need(value['schema'] == 'careertrellis.onedrive-bundle-inventory/v1' and value['namespace'] == retire.NAMESPACE
         and type(value['objects']) is list and len(value['objects']) <= 10000)
    fresh(value['observed_at'], now)
    actual = {}
    for item in value['objects']:
        policy._keys(item, ('path', 'object_id', 'size', 'sha256'))
        policy._path(item['path']); policy._text(item['object_id']); policy._digest(item['sha256'])
        policy.integer(item['size'], 1)
        need(item['path'] not in actual)
        actual[item['path']] = item
    need(actual == expected)
    return actual


def _identities(values, objects, now):
    need(type(values) is list and len(values) == len(objects))
    result = {}
    for item in values:
        policy._keys(item, ('path', 'object_id', 'size', 'sha256', 'etag', 'observed_at',
                           'identity_verified_before_after_download', 'etag_verified_before_after_download'))
        path = item['path']; need(path in objects and path not in result)
        need(all(item[key] == objects[path][key] for key in ('path', 'object_id', 'size', 'sha256'))
             and type(item['size']) is int and item['identity_verified_before_after_download'] is True
             and item['etag_verified_before_after_download'] is True and graph_delete.valid_etag(item['etag']))
        fresh(item['observed_at'], now); result[path] = item
    return result


def prepare(request, snapshot, now):
    """Deterministic supplied-evidence planner; never native authentication."""
    try:
        request = validate_request(request)
        need(type(now) is dt.datetime and now.utcoffset() == dt.timedelta(0)
             and len(bundle.canonical(snapshot)) <= MAX_BYTES)
        policy._keys(snapshot, ('policy', 'catalog', 'inventory', 'identity_witnesses', 'quota',
                               'repository_idle', 'unresolved_retirement', 'unresolved_growth'))
        need(snapshot['repository_idle'] is True and snapshot['unresolved_retirement'] is False
             and snapshot['unresolved_growth'] is False)
        active = coordinator.Policy(snapshot['policy']); active.require_active(now)
        need(active.digest == request['retention_policy_sha256'] and active.data['keep_verified'] == 2
             and active.data['grace_seconds'] == policy.GRACE_SECONDS
             and active.data['namespace_budget_bytes'] == policy.NAMESPACE_BUDGET)
        need(type(snapshot['catalog'].get('entries')) is list and len(snapshot['catalog']['entries']) >= 2)
        retirement = retire.prepare(snapshot['catalog'], now.isoformat())
        objects = _inventory(snapshot['inventory'], retire.objects(snapshot['catalog']), now)
        _identities(snapshot['identity_witnesses'], objects, now)
        quota = snapshot['quota']; policy._keys(quota, ('total', 'free', 'observed_at'))
        fresh(quota['observed_at'], now)
        allocation = request['allocation']
        capacity = guard.capacity(quota['total'], quota['free'], allocation['source_bytes'], allocation['candidate_bytes'],
            sum(item['size'] for item in objects.values()), allocation['namespace_new_bytes'],
            reserve_percent=active.data['reserve_percent'], peak_multiplier=active.data['peak_multiplier'],
            namespace_budget_bytes=active.data['namespace_budget_bytes'])
        return {'schema': 'careertrellis.onedrive-pre-growth-plan/v1', 'simulation': True,
            'transaction': request['transaction'], 'retention_policy_sha256': active.digest,
            'snapshot_sha256': sha(snapshot), 'request_sha256': sha(request), 'planned_at': now.isoformat(),
            'retirement': retirement, 'capacity_before': capacity,
            'action': 'retire_then_reassess' if retirement['candidates'] else 'reassess_without_retirement',
            'native_route_available': False, 'native_growth_authorized': False,
            'deletions_performed': 0}
    except Exception:
        raise PreGrowthRefused('pre_growth_requires_reconciliation') from None


def _stable(snapshot):
    witnesses = {}
    for item in snapshot['identity_witnesses']:
        item = copy.deepcopy(item); item.pop('observed_at'); witnesses[item['path']] = item
    catalog = copy.deepcopy(snapshot['catalog']); catalog.pop('observed_at')
    return sha({'policy': snapshot['policy'], 'catalog': catalog, 'witnesses': witnesses})


def run(request, backend=None):
    """OFFLINE-only state machine. A Native adapter must never be substituted.

    Backend: mode='offline-test'; now(); once(tx,name,document)->document SHA;
    fenced_snapshot(tx)->snapshot (canonical fences held only during that call);
    dispatch_retirement(tx,plan)->closed simulation acknowledgement. No upload or
    native provider method is called. Repeated/ambiguous intents refuse replay.
    """
    request = validate_request(request)
    need(backend is not None and getattr(backend, 'mode', None) == 'offline-test')
    tx = request['transaction']
    def once(name, value):
        need(backend.once(tx, name, copy.deepcopy(value)) == sha(value))
    try:
        once('workflow-intent.json', {'schema': 'careertrellis.pre-growth-simulation-intent/v1',
            'simulation': True, 'request': request, 'observed_at': backend.now().isoformat()})
    except Exception:
        raise PreGrowthRefused('pre_growth_requires_reconciliation') from None
    try:
        initial = backend.fenced_snapshot(tx)
        plan = prepare(request, initial, backend.now()); once('plan.json', plan)
        candidates = plan['retirement']['candidates']
        retired = sorted(item['bundle_id'] for item in candidates)
        if candidates:
            # This revalidation takes its own short fence scope. Parent retains
            # no child operation lock across dispatch, so there is no deadlock.
            current = backend.fenced_snapshot(tx)
            revised = prepare(request, current, backend.now())
            need(_stable(current) == _stable(initial)
                 and revised['retirement']['candidates'] == candidates
                 and revised['retirement']['retained_bundle_ids'] == plan['retirement']['retained_bundle_ids'])
            once('retirement-dispatch-intent.json', {'simulation': True, 'plan_sha256': sha(plan), 'transaction': tx})
            ack = backend.dispatch_retirement(tx, copy.deepcopy(plan))  # Sole dispatch; never retry.
            policy._keys(ack, ('schema', 'result', 'transaction', 'plan_sha256', 'simulated_retired_bundle_ids',
                              'simulated_retired_object_count', 'native_retirement_accepted'))
            need(ack['schema'] == 'careertrellis.pre-growth-retirement-simulation/v1'
                 and ack['result'] == 'simulated_complete' and ack['transaction'] == tx and ack['plan_sha256'] == sha(plan)
                 and ack['simulated_retired_bundle_ids'] == retired
                 and type(ack['simulated_retired_object_count']) is int
                 and ack['simulated_retired_object_count'] == sum(len(item['objects']) for item in candidates)
                 and ack['native_retirement_accepted'] is False)
            once('retirement-terminal.json', ack)
        # Obtain actual NEW facts from the adapter even if no retirement was due.
        # Never subtract planned logical bytes from the old quota or namespace.
        reassessment_started = backend.now()
        after = backend.fenced_snapshot(tx)
        reassessment = prepare(request, after, backend.now())
        expected = {item['id'] for item in initial['catalog']['entries']} - set(retired)
        expected_after = copy.deepcopy(initial)
        expected_after['catalog']['entries'] = [item for item in expected_after['catalog']['entries'] if item['id'] in expected]
        expected_paths = set(retire.objects(expected_after['catalog']))
        expected_after['identity_witnesses'] = [item for item in expected_after['identity_witnesses'] if item['path'] in expected_paths]
        need({item['id'] for item in after['catalog']['entries']} == expected
             and _stable(after) == _stable(expected_after))
        need(all(policy.timestamp(value) >= reassessment_started for value in
             (after['catalog']['observed_at'], after['inventory']['observed_at'], after['quota']['observed_at'],
              *(item['observed_at'] for item in after['identity_witnesses']))))
        result = {'schema': 'careertrellis.onedrive-pre-growth-simulation/v1', 'simulation': True,
            'transaction': tx, 'result': 'simulated_reassessed', 'plan_sha256': sha(plan),
            'post_snapshot_sha256': sha(after), 'capacity_after': reassessment['capacity_before'],
            'simulated_growth_allowed': reassessment['capacity_before']['allowed'],
            'simulated_retired_bundle_ids': retired, 'native_route_available': False,
            'native_growth_authorized': False, 'deletions_performed': 0,
            'physical_bytes_reclaimed': None}
        once('workflow-complete.json', result)
        return result
    except BaseException:
        try: once('workflow-failed.json', {'simulation': True, 'category': 'pre_growth_requires_reconciliation'})
        except Exception: pass
        raise PreGrowthRefused('pre_growth_requires_reconciliation') from None


def run_native(request):
    validate_request(request)
    raise PreGrowthRefused('pre_growth_native_route_unavailable')
