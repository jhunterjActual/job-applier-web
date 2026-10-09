"""Protected operator assertions; no provider actions, hold release, or public API.

The caller supplies a non-logging migration engine from the protected coordinator.
This library does not authenticate the truth of an operator's assertions.
"""
from datetime import datetime
import hashlib
import logging
from uuid import UUID

from admission_operations import encoded, read_protected, strict_json
from session_authorization import SessionAuthorization

LIMIT = 8192
CONTEXT_KEYS = {'operation', 'account_id', 'incarnation', 'epoch', 'old_binding',
    'old_subject', 'target_binding', 'target_subject', 'expected_role',
    'owner_authorized', 'started', 'deadline', 'predecessor'}


class EvidenceRejected(Exception):
    def __init__(self):
        super().__init__('Operator evidence not acknowledged; inspect operation state.')


def require(value):
    if not value:
        raise EvidenceRejected()


def instant(value):
    require(isinstance(value, str) and len(value) <= 40)
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None and result.utcoffset().total_seconds() == 0)
    return result


def identifier(value):
    require(isinstance(value, str) and str(UUID(value)) == value)


def validate(raw):
    """No free text, attachments, email matching, external_id, or token fields."""
    try:
        require(type(raw) is bytes and len(raw) <= LIMIT)
        packet = strict_json(raw)
        require(type(packet) is dict and set(packet) == {
            'version', 'context', 'holder', 'target', 'revocation',
            'verified_at', 'expires_at', 'owner_approval'})
        require(type(packet['version']) is int and packet['version'] in (1, 2))
        c = packet['context']
        require(type(c) is dict and set(c) == CONTEXT_KEYS)
        identifier(c['operation']); identifier(c['incarnation'])
        if c['predecessor'] is not None: identifier(c['predecessor'])
        require(type(c['owner_authorized']) is bool and c['expected_role'] in ('user', 'owner'))
        for prefix in ('old', 'target'):
            SessionAuthorization(c['account_id'], c[prefix+'_subject'], c[prefix+'_binding'], c['epoch'], c['incarnation'])
        start, deadline = instant(c['started']), instant(c['deadline'])
        verified, expires = instant(packet['verified_at']), instant(packet['expires_at'])
        require(start <= verified < expires <= deadline and (deadline-start).total_seconds() <= 3600)
        target_method = ('fresh_authentication_and_enrollment' if packet['version'] == 1
                         else 'fresh_authentication_under_optional_mfa')
        for name, method in (('holder', 'independent_assisted_verification'),
                             ('target', target_method),
                             ('revocation', 'all_source_and_target_provider_sessions')):
            proof = packet[name]
            target_keys = ({'factor'} if packet['version'] == 1 else
                           {'authentication_method', 'email_verified', 'applicable_factor_status'})
            keys = {'method', 'completed_at'} | (target_keys if name == 'target' else set())
            require(type(proof) is dict and set(proof) == keys and proof['method'] == method)
            require(start <= instant(proof['completed_at']) <= verified)
        if packet['version'] == 1:
            require(packet['target']['factor'] in ('passkey', 'password_totp'))
        else:
            require(packet['target']['authentication_method'] in ('google', 'password', 'passkey'))
            require(packet['target']['email_verified'] is True)
            require(packet['target']['applicable_factor_status'] in ('none_enrolled', 'enforced'))
            if packet['target']['authentication_method'] == 'passkey':
                require(packet['target']['applicable_factor_status'] == 'enforced')
        # Revoke after verification/enrollment; activation will require another fresh sign-in.
        require(instant(packet['revocation']['completed_at']) >= max(
            instant(packet['holder']['completed_at']), instant(packet['target']['completed_at'])))
        approval = packet['owner_approval']
        if c['expected_role'] == 'owner':
            require(c['owner_authorized'] is True and type(approval) is dict)
            require(set(approval) == {'operation', 'account_id', 'epoch', 'target_binding',
                'target_subject', 'configuration_verified', 'authorized_at'})
            for key in ('operation', 'account_id', 'epoch', 'target_binding', 'target_subject'):
                require(type(approval[key]) is type(c[key]) and approval[key] == c[key])
            require(approval['configuration_verified'] is True)
            require(start <= instant(approval['authorized_at']) <= verified)
        else:
            require(approval is None)
        return packet
    except Exception:
        raise EvidenceRejected() from None


def _same_context(expected, actual):
    require(type(actual) is dict and set(actual) == CONTEXT_KEYS)
    for key in CONTEXT_KEYS:
        if key in ('started', 'deadline'):
            require(instant(expected[key]) == instant(actual[key]))
        else:
            require(type(expected[key]) is type(actual[key]) and expected[key] == actual[key])


def submit(raw, engine):
    """Commit an exact attestation, or fail closed. No identity activation.

    Retry the same bytes after an uncertain commit. Expired/changed state requires
    operator reconciliation; this function never resumes or replaces operations.
    """
    packet = validate(raw)
    c = packet['context']
    digest = hashlib.sha256(encoded(packet)).hexdigest()
    try:
        # Reject caller logging configurations before sensitive parameters are submitted.
        require(not engine.echo and not engine.pool.echo and engine.hide_parameters)
        require(not engine.logger.isEnabledFor(logging.DEBUG))
        from sqlalchemy import text
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
            connection.exec_driver_sql("SET LOCAL log_parameter_max_length=0")
            connection.exec_driver_sql("SET LOCAL log_parameter_max_length_on_error=0")
            actual = connection.scalar(text('SELECT careertrellis_admission.identity_evidence_context(CAST(:op AS uuid))'), {'op': c['operation']})
            _same_context(c, actual)
            connection.execute(text('SELECT careertrellis_admission.identity_attest('
                'CAST(:op AS uuid),:epoch,:digest,:verified,:expires,:owner)'), {
                'op': c['operation'], 'epoch': c['epoch'], 'digest': digest,
                'verified': instant(packet['verified_at']), 'expires': instant(packet['expires_at']),
                'owner': packet['owner_approval'] is not None})
        return {'version': 1, 'outcome': 'attested_account_held'}
    except Exception:
        # Includes lost commit acknowledgements; do not falsely report rollback.
        raise EvidenceRejected() from None


def submit_protected(path, engine):
    """Root-owned, single-link 0600 bounded input beneath protected parents."""
    try:
        raw = read_protected(path, limit=LIMIT)
    except Exception:
        raise EvidenceRejected() from None
    return submit(raw, engine)
