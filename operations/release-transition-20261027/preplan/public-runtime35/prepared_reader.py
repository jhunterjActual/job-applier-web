"""UNAPPLIED fixed-path historical snapshot reader. No default host IO."""
import prepared_types as a
import prepared98_history as h

def snapshot_protected_history(protected_read, registry_raw):
    # The eventual call site must supply the original reviewed protected reader.
    # This draft neither implements nor weakens its owner/mode/FD protections.
    a.need(a.sha(registry_raw) == h.REGISTRY_SHA)
    registry=a.decode(registry_raw)
    names=set(registry['records']) | set(registry['plain_full_maps'])
    paths={}
    for name in names:
        a.need(type(name) is str and '/' not in name and '\\' not in name
               and name not in ('.','..'))
        expected=a.REPAIR_ROOT+name
        declared=(registry['records'][name]['path'] if name in registry['records']
                  else registry['plain_full_maps'][name])
        a.need(declared == expected)
        paths[name]=expected
    first={name:protected_read(paths[name]) for name in sorted(names)}
    for raw in first.values():
        a.need(type(raw) is bytes and len(raw) <= 16*1024*1024)
    # No cross-record check is permitted to observe unverified second versions.
    # Every semantic parse below uses this first complete cached snapshot.
    for name in sorted(names):a.need(protected_read(paths[name]) == first[name])
    return first

def inspect_protected_history(protected_read, application_plan, registry_raw):
    first=snapshot_protected_history(protected_read,registry_raw)
    result=h.inspect_dag(first,application_plan,registry_raw)
    a.need(result['authorizes_preparation'] is False)
    return result

def verify_prepared(*args, **kwargs):
    # Mandatory final integration hold: original failure/Git/current complete
    # runtime/source/authority guards and qualified97 chain are still unwired.
    raise a.Refused('FULL_ORIGINAL_GUARDS_AND_QUALIFIED97_NOT_REVIEWED')
