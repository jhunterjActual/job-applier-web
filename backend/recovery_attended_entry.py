"""Attended prepared hold and recovery continuation; no background entry.

Run only from the authenticated immutable operational source. This module does
not issue credentials or invent targeting. One protected exchange claim survives
all uncertain outcomes; it must never be deleted to retry authentication.
"""
import asyncio
import hashlib
import os
import re
import sys
from uuid import UUID

import admission_operations as ops
from identity_entry import SecureTerminal
from recovery_attended_auth import AttendedRecoveryAuthentication, SCOPE
import recovery_attended_hold as held
from recovery_factor_proof import RecoveryProofUncertain
from recovery_runtime import open_recovery_runtime


def require(value):
    if not value:
        raise RecoveryProofUncertain()


def claim_path(operation):
    require(type(operation) is str and str(UUID(operation)) == operation)
    return ops.STATE / ('recovery-exchange-' + operation + '.json')


def require_unclaimed(operation):
    path = claim_path(operation)
    ops.protected_parents(path)
    require(not path.exists() and not path.is_symlink())


async def claim_exchange(scope, state_digest):
    require(type(scope) is dict and set(scope) == set(SCOPE)
            and type(state_digest) is str and re.fullmatch('[0-9a-f]{64}', state_digest))
    path = claim_path(scope['operation'])
    record = {'version': 1, 'phase': 'claimed_before_exchange',
              'scope': scope, 'state_digest': state_digest}
    # write_protected's no-clobber link is the exclusive dispatch decision.
    # An existing claim is never treated as permission, even for identical input.
    ops.write_protected(path, ops.encoded(record))
    require(ops.strict_json(ops.read_protected(path)) == record)
    return True


def display_authorization(terminal, url):
    # This URL contains state and a PKCE challenge, not the secret verifier/code.
    # Send only to the operator's controlling terminal, never captured stdout.
    os.write(terminal.fd, ('Open this sign-in address in your browser:\n' + url + '\n').encode('ascii'))


async def continue_held(terminal, recovery):
    require(terminal.ask('Confirm this is your visible secure terminal: type VISIBLE') == 'VISIBLE')
    operation = terminal.ask('Prepared or existing held recovery operation UUID', limit=36)
    require_unclaimed(operation)
    mode = terminal.ask('Use the saved hold request or an existing hold: type PREPARED or HELD')
    require(mode in ('PREPARED', 'HELD'))
    if mode == 'PREPARED':
        request = held.load_request(operation)
        request_digest = hashlib.sha256(ops.encoded({'version': 1, 'request': request})).hexdigest()
        os.write(terminal.fd, ('Prepared recovery request SHA-256: ' + request_digest + '\n').encode('ascii'))
        require(terminal.ask('Confirm the prepared account recovery request: type HOLD') == 'HOLD')
        code = None
        try:
            if not held.dispatch_claimed(operation):
                code = terminal.ask('Existing recovery code for the prepared account', limit=256)
            result = await held.consume_or_reconcile(recovery.hold, operation, code)
        finally:
            code = None
        require(type(result) is dict and result.get('outcome') in ('account_held', 'hold_recorded')
                and result.get('operation') == operation)
    factor = terminal.ask('Exact lost authenticator factor ID', limit=172)
    require(re.fullmatch('auth_factor_[A-Za-z0-9_]{6,160}', factor))
    context = await recovery.store.context(operation)
    require(context.get('state') == 'held')
    # A digest lets the attended operator compare scope without printing identity.
    scope_digest = hashlib.sha256(ops.encoded({k: context[k] for k in SCOPE})).hexdigest()
    os.write(terminal.fd, ('Held recovery scope SHA-256: ' + scope_digest + '\n').encode('ascii'))
    require(terminal.ask('Confirm this exact held recovery and lost factor: type CONTINUE') == 'CONTINUE')
    result = await recovery.workflow.contain_provider(operation, factor)
    require(result == {'outcome': 'provider_contained_account_held'})
    context = await recovery.store.context(operation)
    staging = ops.assignments(ops.read_protected(ops.ROOT / 'staging.env'))
    authentication = recovery.workflow.authentication
    # The provider must still be the same installed configuration after effects.
    require(staging.get('WORKOS_CLIENT_ID') == authentication.settings.client_id
            and staging.get('WORKOS_API_KEY') == authentication.settings.client_secret)
    flow = AttendedRecoveryAuthentication(recovery.store, authentication,
                                          recovery.workflow, claim_exchange)
    try:
        url = flow.begin(context, staging['WORKOS_REDIRECT_URI'], authentication.settings)
        display_authorization(terminal, url)
        response = terminal.ask('Paste the recovery response copied from the sign-in page', limit=4096)
        try:
            result = await flow.finish(response)
        finally:
            response = None
        require(result == {'state': 'committed', 'epoch': context['epoch']})
        return {'outcome': 'recovery_released_fresh_sign_in_required'}
    finally:
        flow.close()


async def attended():
    require(os.name == 'posix' and os.geteuid() == 0
            and os.uname().nodename == 'careertrellis-staging-web-1')
    async with open_recovery_runtime() as recovery:
        with SecureTerminal() as terminal:
            return await continue_held(terminal, recovery)


def main():
    try:
        import resource
        require(len(sys.argv) == 1)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        result = asyncio.run(attended())
        print(ops.encoded(result).decode().strip())
        return 0
    except BaseException:
        print('Recovery refused or outcome uncertain; retain the hold and inspect its existing receipt.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
