"""Pure, fail-closed capacity checks and immutable-bundle retirement planning.

No provider access or deletion is performed. A returned plan still needs fresh
locked inventory/receipt comparison and durable execution intent. Catalog hashes
bind evidence, not authorization. All timestamps must be explicit UTC strings.
"""
import datetime as dt
import hashlib
import json
import re


NAMESPACE_BUDGET = 512 * 1024 ** 2
GRACE_SECONDS = 48 * 60 * 60
MAX_INVENTORY_AGE_SECONDS = 15 * 60
HEX = re.compile(r'[0-9a-f]{64}\Z')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


class Refused(ValueError):
    """Fixed public refusal code; never include private evidence in errors."""


def need(condition, code='invalid_policy_evidence'):
    if not condition:
        raise Refused(code)


def integer(value, minimum=0):
    need(type(value) is int and value >= minimum)
    return value


def timestamp(value):
    need(type(value) is str and len(value) <= 40)
    try:
        result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Refused('invalid_policy_evidence') from None
    need(result.utcoffset() == dt.timedelta(0))
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def capacity(total_bytes, free_bytes, measured_increment_bytes,
             namespace_bytes, candidate_bytes):
    """Evaluate peak before any retirement; candidates/holds count in namespace.

    measured_increment_bytes is the conservatively measured next-cycle allocation
    including provider versions and overlapping successor/rollback copies. Peak
    is twice this measurement, and cannot be smaller than the candidate itself.
    Missing measurements are invalid; a zero-byte upload may use a zero peak.
    """
    total = integer(total_bytes, 1)
    free = integer(free_bytes)
    increment = integer(measured_increment_bytes)
    namespace = integer(namespace_bytes)
    candidate = integer(candidate_bytes)
    need(free <= total)
    need(increment >= candidate, 'incomplete_peak_measurement')
    reserve = (total + 4) // 5
    peak = 2 * increment
    required = reserve + peak
    namespace_peak = namespace + candidate
    reasons = []
    if free < required:
        reasons.append('account_reserve_shortfall')
    if namespace_peak > NAMESPACE_BUDGET:
        reasons.append('namespace_budget_exceeded')
    used = total - free
    severity = 'urgent' if used * 100 >= total * 85 else (
        'warning' if used * 100 >= total * 80 else 'ok')
    return {'schema': 'careertrellis.onedrive-capacity/v1',
            'allowed': not reasons, 'reasons': reasons, 'severity': severity,
            'reserve_bytes': reserve, 'projected_peak_bytes': peak,
            'required_free_bytes': required,
            'shortfall_bytes': max(0, required - free),
            'namespace_peak_bytes': namespace_peak,
            'namespace_budget_bytes': NAMESPACE_BUDGET}


def _keys(value, keys):
    need(type(value) is dict and set(value) == set(keys))


def _digest(value):
    need(type(value) is str and HEX.fullmatch(value))


def _text(value):
    need(type(value) is str and 0 < len(value) <= 512
         and all(32 <= ord(char) != 127 for char in value))


def _path(value):
    _text(value)
    need('\\' not in value and ':' not in value
         and all(piece not in ('', '.', '..') for piece in value.split('/')))


def plan_retirement(catalog, now):
    """Plan exact objects from obsolete, completely verified immutable bundles.

    Catalog schema v1 contains entries and explicit named holds. Each entry has
    complete byte/native-restore evidence bound to its archive digest, exact
    provider object identities and dependencies. Unknown dependencies refuse the
    entire plan. Named holds never expire merely because review_after has passed.
    Retain newest two accepted bundles and transitive dependencies of every
    current/held bundle. Each obsolete bundle waits 48h after the second newer
    bundle is accepted (the event that makes it obsolete). An oversized namespace
    refuses retirement for operator review.
    """
    current_time = timestamp(now)
    _keys(catalog, ('schema', 'observed_at', 'entries', 'holds'))
    need(catalog['schema'] == 'careertrellis.onedrive-bundle-catalog/v1')
    observed = timestamp(catalog['observed_at'])
    need(observed <= current_time, 'future_evidence')
    need((current_time - observed).total_seconds() <= MAX_INVENTORY_AGE_SECONDS,
         'stale_inventory')
    entries = catalog['entries']
    holds = catalog['holds']
    need(type(entries) is list and 2 <= len(entries) <= 10000)
    need(type(holds) is list and len(holds) <= 10000)
    by_id, acceptance, paths, identities = {}, {}, set(), set()
    logical_bytes = 0
    for entry in entries:
        _keys(entry, ('id', 'accepted_at', 'archive_sha256', 'download_sha256',
                     'restore_archive_sha256', 'native_restore_receipt_sha256',
                     'complete', 'current', 'dependencies_resolved',
                     'dependencies', 'objects'))
        for field in ('id', 'archive_sha256', 'download_sha256',
                      'restore_archive_sha256', 'native_restore_receipt_sha256'):
            _digest(entry[field])
        need(entry['id'] not in by_id, 'duplicate_bundle')
        need(entry['complete'] is True, 'incomplete_bundle')
        need(type(entry['current']) is bool)
        need(entry['dependencies_resolved'] is True, 'unknown_dependencies')
        need(entry['download_sha256'] == entry['restore_archive_sha256'] ==
             entry['archive_sha256'], 'verification_binding_mismatch')
        accepted = timestamp(entry['accepted_at'])
        need(accepted <= timestamp(catalog['observed_at']), 'future_evidence')
        need(accepted not in acceptance.values(), 'ambiguous_successor_order')
        acceptance[entry['id']] = accepted
        need(type(entry['dependencies']) is list and len(entry['dependencies']) <= 10000)
        for dependency in entry['dependencies']:
            _digest(dependency)
            need(dependency != entry['id'])
        need(len(set(entry['dependencies'])) == len(entry['dependencies']))
        need(type(entry['objects']) is list and 0 < len(entry['objects']) <= 10000)
        for obj in entry['objects']:
            _keys(obj, ('path', 'object_id', 'size', 'sha256'))
            _path(obj['path']); _text(obj['object_id']); _digest(obj['sha256'])
            pieces = obj['path'].split('/')
            need(len(pieces) == 2 and pieces[0] == entry['id']
                 and pieces[1] in ('repository.tar', 'manifest.json'),
                 'object_outside_bundle')
            integer(obj['size'], 1)
            need(obj['path'] not in paths and obj['object_id'] not in identities,
                 'shared_or_duplicate_object')
            paths.add(obj['path']); identities.add(obj['object_id'])
            logical_bytes += obj['size']
            need(len(paths) <= 10000)
        need(any(obj['sha256'] == entry['archive_sha256'] for obj in entry['objects']),
             'archive_object_missing')
        by_id[entry['id']] = entry
    need(logical_bytes <= NAMESPACE_BUDGET, 'namespace_budget_exceeded')
    for entry in entries:
        need(all(key in by_id for key in entry['dependencies']), 'missing_dependency')
    ordered = sorted(by_id, key=lambda key: acceptance[key], reverse=True)
    retained = set(ordered[:2])
    retained.update(entry['id'] for entry in entries if entry['current'])
    hold_names = set()
    for hold in holds:
        _keys(hold, ('name', 'owner', 'reason', 'review_after', 'bundles'))
        need(type(hold['name']) is str and NAME.fullmatch(hold['name']))
        need(hold['name'] not in hold_names)
        hold_names.add(hold['name'])
        _text(hold['owner']); _text(hold['reason']); timestamp(hold['review_after'])
        need(type(hold['bundles']) is list and 0 < len(hold['bundles']) <= 10000)
        for key in hold['bundles']:
            _digest(key); need(key in by_id, 'missing_hold_bundle'); retained.add(key)
    # Dependency closure also covers bundles retained by the grace period below.
    for position, key in enumerate(ordered[2:], 2):
        successor = ordered[position - 2]
        if (current_time - acceptance[successor]).total_seconds() < GRACE_SECONDS:
            retained.add(key)
    pending = list(retained)
    while pending:
        key = pending.pop()
        for dependency in by_id[key]['dependencies']:
            if dependency not in retained:
                retained.add(dependency); pending.append(dependency)
    candidates = []
    for position, key in enumerate(ordered):
        if key not in retained:
            successor = ordered[position - 2]
            candidates.append({'bundle_id': key, 'successor_id': successor,
                               'successor_accepted_at': by_id[successor]['accepted_at'],
                               'objects': [dict(obj) for obj in by_id[key]['objects']]})
    return {'schema': 'careertrellis.onedrive-retirement-plan/v1',
            'catalog_sha256': hashlib.sha256(canonical(catalog)).hexdigest(),
            'planned_at': now, 'namespace_bytes': logical_bytes,
            'retained_bundle_ids': sorted(retained), 'candidates': candidates,
            'requires_locked_revalidation': True, 'deletions_performed': 0}
