"""Protected immutable hold request and exclusive dispatch/reconciliation.

Run through the attended operator only. Journals contain exact targeting and
digests, never recovery codes. A committed claim permanently prevents replay,
including when the process dies before sending the database request.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import re
from uuid import UUID

import admission_operations as ops
from recovery_factor_proof import RecoveryProofUncertain

KEYS = {'account_id', 'generation', 'recovery_operation', 'target_binding',
        'target_subject', 'until_time', 'owner_authorized'}


def require(value):
    if not value:
        raise RecoveryProofUncertain()


def validate(request):
    try:
        require(type(request) is dict and set(request) == KEYS)
        require(type(request['account_id']) is int and request['account_id'] > 0
                and type(request['owner_authorized']) is bool)
        for key in ('generation', 'recovery_operation'):
            require(type(request[key]) is str and str(UUID(request[key])) == request[key])
        require(type(request['target_binding']) is str
                and re.fullmatch('[0-9a-f]{64}', request['target_binding'])
                and type(request['target_subject']) is str
                and re.fullmatch('user_[A-Za-z0-9_]{6,120}', request['target_subject']))
        require(type(request['until_time']) is str)
        until = datetime.fromisoformat(request['until_time'])
        require(until.tzinfo is not None and until.utcoffset().total_seconds() == 0)
        return dict(request)
    except Exception:
        raise RecoveryProofUncertain() from None


def paths(operation):
    require(type(operation) is str and str(UUID(operation)) == operation)
    return (ops.STATE / ('recovery-hold-request-' + operation + '.json'),
            ops.STATE / ('recovery-hold-dispatch-' + operation + '.json'))


def save_request(request):
    try:
        request = validate(request)
        now = datetime.now(timezone.utc)
        remaining = (datetime.fromisoformat(request['until_time']) - now).total_seconds()
        require(0 < remaining <= 3600)
        path, _ = paths(request['recovery_operation'])
        raw = ops.encoded({'version': 1, 'request': request})
        ops.write_protected(path, raw)
        require(ops.read_protected(path) == raw)
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        raise RecoveryProofUncertain() from None


def load_request(operation):
    try:
        path, _ = paths(operation)
        value = ops.strict_json(ops.read_protected(path))
        require(type(value) is dict and set(value) == {'version', 'request'}
                and type(value['version']) is int and value['version'] == 1)
        request = validate(value['request'])
        require(request['recovery_operation'] == operation)
        return request
    except Exception:
        raise RecoveryProofUncertain() from None


def dispatch_claimed(operation):
    """Used only to decide whether hidden credential entry is needed."""
    try:
        _, path = paths(operation)
        ops.protected_parents(path)
        require(not path.is_symlink())
        return path.exists()
    except Exception:
        raise RecoveryProofUncertain() from None


async def consume_or_reconcile(store, operation, code=None):
    try:
        request = load_request(operation)
        raw = ops.encoded({'version': 1, 'request': request})
        _, claim_path = paths(operation)
        claim = {'version': 1, 'phase': 'claimed_before_hold',
                 'operation': operation, 'request_digest': hashlib.sha256(raw).hexdigest()}
        kwargs = {**request, 'until_time': datetime.fromisoformat(request['until_time'])}
        if dispatch_claimed(operation):
            require(ops.strict_json(ops.read_protected(claim_path)) == claim)
            return await store.reconcile_hold(**kwargs)
        require(type(code) is str and 0 < len(code) <= 256)
        remaining = (kwargs['until_time'] - datetime.now(timezone.utc)).total_seconds()
        require(0 < remaining <= 3600)
        # Exclusive create is the dispatch decision, committed before the call.
        # A concurrent loser must restart via read-only reconciliation.
        ops.write_protected(claim_path, ops.encoded(claim))
        require(ops.strict_json(ops.read_protected(claim_path)) == claim)
        return await store.consume_and_hold(**kwargs, code=code)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise RecoveryProofUncertain() from None
    finally:
        code = None
