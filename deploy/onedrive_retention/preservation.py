"""Offline consistency contract for retaining a failed PRE-RESTORE capture.

No native collector/writer exists here. Supplied observations are unauthenticated;
even a consistent proof cannot clear an installed guard, authorize another attempt,
accept a backup, or authorize cleanup. Historical manual units require an explicit
reviewed binding, never a name inferred from a transaction suffix.
"""
import datetime as dt
import hashlib
import json
import re
from pathlib import PurePosixPath


class PreservationRefused(ValueError):
    pass


def need(value):
    if not value:
        raise PreservationRefused('failed_capture_preservation_refused')


def _read(raw):
    need(type(raw) is bytes and 0 < len(raw) <= 262144)
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result)
            result[key] = value
        return result
    return json.loads(raw.decode('ascii'), object_pairs_hook=pairs,
                      parse_constant=lambda _: need(False))


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _shape(value, keys):
    need(type(value) is dict and set(value) == set(keys.split()))


def _hex(value, count):
    need(type(value) is str and re.fullmatch('[0-9a-f]{%d}' % count, value))


def _time(value):
    need(type(value) is str and value.endswith('Z'))
    result = dt.datetime.fromisoformat(value[:-1] + '+00:00')
    need(result.utcoffset() == dt.timedelta(0))
    return result


def validate_preservation(raw_proof, raw_ownership, raw_intent, raw_info_dispatch,
                          raw_terminal, raw_archive_receipt, now):
    """Validate explicit fixture evidence; never authenticate caller assertions.

    Raw byte hashes bind the complete original records. Terminal observations must
    be freshly collected independently of the failed owner; this function cannot
    establish that provenance. A future installed collector must hold canonical
    fences and authenticate originals and complete unit bindings before use.
    """
    try:
        raws = (raw_ownership, raw_intent, raw_info_dispatch, raw_terminal, raw_archive_receipt)
        ownership, intent, dispatch, terminal, archive = [_read(raw) for raw in raws]
        proof = _read(raw_proof)
        _shape(proof, 'schema transaction attempt hashes held_resources')
        need(proof['schema'] == 'careertrellis.failed-capture-preservation/v1')
        _hex(proof['transaction'], 32)
        _hex(proof['attempt'], 32)
        need(proof['hashes'] == dict(zip(('ownership', 'intent', 'info_dispatch', 'terminal', 'archive_receipt'),
                                       map(_hash, raws))))
        _shape(ownership, 'transaction attempt owner units data_root')
        _shape(ownership['owner'], 'pid start_ticks boot_id')
        owner = ownership['owner']
        need(all(type(owner[key]) is int and owner[key] > 0 for key in ('pid', 'start_ticks')))
        need(type(owner['boot_id']) is str and re.fullmatch(
            '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', owner['boot_id']))
        units = ownership['units']
        need(type(units) is list and 0 < len(units) <= 32 and len(set(units)) == len(units))
        need(all(type(unit) is str and re.fullmatch('[A-Za-z0-9_.@-]+\\.service', unit) for unit in units))
        root = ownership['data_root']
        need(type(root) is str and PurePosixPath(root).parent == PurePosixPath(
            '/var/lib/careertrellis-onedrive-bundle-acceptance') and PurePosixPath(root).name == proof['attempt'])
        _shape(intent, 'transaction attempt ownership_sha256 operation')
        need(intent['ownership_sha256'] == _hash(raw_ownership) and intent['operation'] == 'info-only')
        _shape(dispatch, 'transaction attempt intent_sha256 owner phases exit_code')
        need(dispatch['intent_sha256'] == _hash(raw_intent) and dispatch['owner'] == owner
             and dispatch['phases'] == ['info'] and type(dispatch['exit_code']) is int and dispatch['exit_code'] != 0)
        _shape(terminal, 'transaction attempt ownership_sha256 dispatch_sha256 observed_at observer owner_absence units data')
        need(terminal['ownership_sha256'] == _hash(raw_ownership) and terminal['dispatch_sha256'] == _hash(raw_info_dispatch))
        _shape(terminal['observer'], 'pid start_ticks boot_id')
        observer = terminal['observer']
        need(observer['boot_id'] == owner['boot_id'] and type(observer['pid']) is int and observer['pid'] > 0
             and type(observer['start_ticks']) is int and observer['start_ticks'] > 0 and observer['pid'] != owner['pid'])
        _shape(terminal['owner_absence'], 'identity proc_state')
        need(terminal['owner_absence'] == {'identity': owner, 'proc_state': 'absent'})
        need(type(now) is dt.datetime and now.utcoffset() == dt.timedelta(0)
             and 0 <= (now - _time(terminal['observed_at'])).total_seconds() <= 120)
        need(type(terminal['units']) is dict and set(terminal['units']) == set(units))
        for state in terminal['units'].values():
            need(type(state) is dict and type(state.get('main_pid')) is int
                 and type(state.get('control_pid')) is int)
            need(state == {'active_state': 'inactive', 'main_pid': 0, 'control_pid': 0,
                           'job': '', 'cgroup_members': [], 'cgroup_state': 'absent'})
        need(terminal['data'] == {'root': root, 'entries': [], 'mounts': [], 'socket_entries': []})
        _shape(archive, 'transaction attempt resources')
        resources = archive['resources']
        need(type(resources) is list and 0 < len(resources) <= 32 and proof['held_resources'] == resources)
        paths = set()
        for resource in resources:
            _shape(resource, 'path sha256 bytes role')
            path = resource['path']
            need(type(path) is str and path and len(path) <= 1024 and '\\' not in path
                 and all(ord(char) >= 32 for char in path) and '..' not in path.split('/') and path not in paths)
            need(path.startswith('/var/lib/careertrellis/') or path.startswith('onedrive:job-applier-staging/postgresql-bundles/'))
            _hex(resource['sha256'], 64)
            need(type(resource['bytes']) is int and resource['bytes'] > 0
                 and resource['role'] in ('encrypted-archive', 'downloaded-encrypted-archive'))
            paths.add(path)
        need(set(item['role'] for item in resources) == {'encrypted-archive', 'downloaded-encrypted-archive'})
        need({(item['sha256'], item['bytes']) for item in resources if item['role'] == 'encrypted-archive'}
             == {(item['sha256'], item['bytes']) for item in resources if item['role'] == 'downloaded-encrypted-archive'})
        for record in (ownership, intent, dispatch, terminal, archive):
            need(record['transaction'] == proof['transaction'] and record['attempt'] == proof['attempt'])
        return {'result': 'consistent_pre_restore_failure_held', 'authenticated': False,
                'backup_accepted': False, 'guard_clearance_permitted': False,
                'retry_permitted': False, 'cleanup_permitted': False,
                'transaction': proof['transaction'], 'attempt': proof['attempt'],
                'proof_sha256': _hash(raw_proof), 'held_resource_count': len(resources),
                'held_budget_bytes': sum(item['bytes'] for item in resources),
                'native_collection_available': False}
    except Exception:
        raise PreservationRefused('failed_capture_preservation_refused') from None
