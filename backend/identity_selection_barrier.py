"""Persistent, fail-closed replacement fence. No release or publication API."""
import re
from uuid import UUID

import admission_operations as ops


def location():
    return ops.ROOT/'identity-selection-barrier.json'


def validate(raw):
    value=ops.strict_json(raw)
    ops.require(type(value) is dict and set(value)=={
        'kind','transaction','host','prior_selection_sha256','target_archive_sha256',
        'journal_sha256','state'})
    ops.require(value['kind']=='identity_selection_barrier_v1' and value['state']=='blocked'
        and value['host'] in ('web','database'))
    ops.require(type(value['transaction']) is str
        and str(UUID(value['transaction']))==value['transaction'])
    for field in ('prior_selection_sha256','target_archive_sha256','journal_sha256'):
        ops.require(type(value[field]) is str and re.fullmatch('[0-9a-f]{64}',value[field]))
    return value


def inspect():
    """Read only; malformed or unreadable evidence is never treated as absence."""
    try:
        path=location()
        ops.protected_parents(path)
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        return validate(ops.read_protected(path,limit=4096))
    except Exception:
        raise ops.OperationError(ops.ERROR) from None


def require_unblocked():
    ops.require(inspect() is None)


def install(raw):
    """Durably block consumers only. Existing evidence is never replaced.

    This does not attest host readiness or authorize selection. A future reviewed
    protocol must govern recovery; no timer, convergence or caller flag clears it.
    """
    try:
        value=validate(raw)
        canonical=ops.encoded(value)
        with ops.host_locks():
            current=inspect()
            if current is None:
                ops.write_protected(location(),canonical)
            else:
                ops.require(current==value)
            ops.require(ops.read_protected(location(),limit=4096)==canonical)
        return {'outcome':'selection_consumers_blocked'}
    except Exception:
        raise ops.OperationError(ops.ERROR) from None
