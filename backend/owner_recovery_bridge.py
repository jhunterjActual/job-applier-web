"""Explicit, operation-bound bridge for an already enrolled owner credential.

No migration, provider mutation, receipt transfer or hold release.
This module belongs in a reviewed immutable operations artifact. It is not loaded
by the currently installed preactivation utility and must not consume a live code
during development/preparation.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import time
from uuid import UUID

from sqlalchemy import text
import admission_operations as ops
import identity_entry
import identity_operator as operator
import identity_plan_store as plans
import owner_recovery as recovery
import session_v2_transition as transition
from workos_transport_candidate import ProviderSettings


def source_digest(database, row, client, subject):
    """Reconstruct the exact enrolled historical preimage, never change its schema."""
    return hashlib.sha256(recovery.encoded({'host': recovery.HOST, 'database': database,
        'client': client, 'subject': subject, 'account': row['id'],
        'created': row['created_at'].isoformat(), 'role': 'owner',
        'schema': '20260907_0014'})).hexdigest()


def snapshot(connection, operation, staging, auth, selected, pair, database):
    """Caller owns a short read-only transaction, with no human interaction."""
    recovery.require(type(operation) is str and str(UUID(operation)) == operation)
    activation, plan = pair['activation'], pair['provider']
    operator.setup(connection, expected_schema=activation['schema'])
    context = identity_entry.context(connection, operation)  # Lifecycle lock + exact held row.
    recovery.context_times(context)
    recovery.require(operation in plan['transitions'] and plan['mode'] == 'switch_environment'
        and plan['authorized'] is True and plan['baseline_binding'] == context['old_binding']
        and plan['target_binding'] == context['target_binding']
        and plan['baseline_binding'] != plan['target_binding']
        and transition.owner_proof(plan['operation'], context['target_binding'], context['target_subject']) == plan['owner_proof'])
    recovery.require(auth['CAREERTRELLIS_AUTH_V2_ENABLED'] == '0')
    source = ProviderSettings(staging['WORKOS_CLIENT_ID'], 'unused-secret',
        auth['CAREERTRELLIS_AUTH_V2_ISSUER'], auth['CAREERTRELLIS_AUTH_V2_GENERATION'],
        auth.get('CAREERTRELLIS_AUTH_V2_AUDIENCE') or None)
    recovery.require(source.binding == context['old_binding'])
    recovery.require(connection.scalar(text('SELECT maintenance AND NOT signup_open AND generation=:generation '
        'FROM careertrellis_admission.policy WHERE singleton'), {'generation': plan['expected_generation']}) is True)
    recovery.require(connection.scalar(text('SELECT binding FROM careertrellis_admission.identity_provider')) == source.binding)
    rows = connection.execute(text("SELECT id,workos_user_id,created_at,disabled_at FROM careertrellis.app_users WHERE role='owner'")).mappings().all()
    recovery.require(len(rows) == 1)
    row = rows[0]
    subject = staging['CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID']
    recovery.require(row['id'] == context['account_id'] and row['disabled_at'] is None
        and row['workos_user_id'] == subject == context['old_subject'])
    # identity_entry.context already rejects erasure, changed incarnation/epoch,
    # source mismatch, absent hold, disabled account and expired operation.
    return {'binding': source_digest(database, row, source.client_id, subject),
            'context': context, 'schema': activation['schema'],
            'selection': hashlib.sha256(selected).hexdigest()}


@contextmanager
def live_view(engine, operation, *, source=None):
    """Exact selection + protected config + live DB state under bounded locks."""
    with ops.host_locks():
        selected, pair = plans.load(activation=True)
        if source is not None:
            recovery.require(pair['provider']['operations'] == source
                and pair['activation']['operations'] == source)
        staging_raw = ops.read_protected(ops.ROOT / 'staging.env')
        auth_raw = ops.read_protected(ops.ROOT / 'auth-v2.env')
        staging, auth = ops.assignments(staging_raw), ops.assignments(auth_raw)
        with operator.secure_connection(engine) as connection:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            database = [engine.url.host, engine.url.port or 5432, engine.url.database,
                str(connection.scalar(text('SELECT oid FROM pg_database WHERE datname=current_database()')))]
            view = snapshot(connection, operation, staging, auth, selected, pair, database)
            plans.require_current(selected)
            recovery.require(ops.read_protected(ops.ROOT / 'staging.env') == staging_raw
                and ops.read_protected(ops.ROOT / 'auth-v2.env') == auth_raw)
            yield view


def consume(store, code, view, now):
    """Called with credential lock AND a current live_view held, never on a stale view."""
    value = store.read()
    recovery.require(value is not None and value['version'] == 1 and value['state'] == 'active'
        and value['binding'] == view['binding'])
    started, deadline = recovery.context_times(view['context'])
    receipt = {'operation': view['context']['operation'], 'scope': recovery.TRANSITION_SCOPE,
        'binding': view['binding'], 'issued': now, 'expires': min(now + 300, int(deadline)),
        'context': view['context'], 'selection': view['selection'], 'schema': view['schema'],
        'credential': value['id']}
    recovery.transition_receipt(receipt, value)
    recovery.require(value['attempts'] < 5 and now >= value['last_attempt'] + (30 if value['attempts'] else 0))
    value['attempts'] += 1
    value['last_attempt'] = now
    store.write(value)  # Spend an attempt even if hashing/process fails afterward.
    try:
        matched = hmac.compare_digest(value['hash'], recovery.verifier(code, value['salt'], value['binding']))
    except ValueError:
        matched = False
    recovery.require(matched)
    value.update(version=2, state='consumed', receipt=receipt)
    store.write(value)  # Receipt and irreversible consumption are ONE atomic commit.
    return receipt


def verify(store, view, now):
    value = store.read()
    recovery.require(value is not None and value['version'] == 2 and value['state'] == 'consumed')
    receipt = recovery.transition_receipt(value['receipt'], value)
    recovery.require(receipt['binding'] == view['binding'] and receipt['context'] == view['context']
        and receipt['selection'] == view['selection'] and receipt['schema'] == view['schema']
        and receipt['issued'] <= now < receipt['expires'])
    return dict(receipt)


def verified_for_entry(engine, operation, *, store=None, now=lambda: int(time.time())):
    """Same-host provenance check, usable by the explicitly named evidence builder.

    Do not replace this with parsing a transported/root-owned JSON receipt.
    The installed DB-host entry point does not yet have this web-host state.
    """
    store = store or recovery.Store()
    with store.locked(), live_view(engine, operation) as current:
        return verify(store, current, now())


def enter(operation, engine, terminal, *, store=None, now=lambda: int(time.time()), source=None):
    """Save an operator-evidence manifest on the credential's WEB host only.

    The separate bridge attest action uses this same WEB intake and the existing
    protected database connection. This function neither transports nor attests.
    Target/revocation/approval evidence remains operator assertions.
    """
    store = store or recovery.Store()
    path, journal = operator.locations(operation)
    with store.locked():
        with live_view(engine, operation, source=source) as initial:
            recovery.require(not path.exists() and not path.is_symlink()
                and not journal.exists() and not journal.is_symlink())
            verify(store, initial, now())
        def verify_holder():
            with live_view(engine, operation, source=source) as current:
                recovery.require(current == initial)
                return verify(store, current, now())
        raw = identity_entry.build_with_verified_holder_receipt(initial['context'], terminal.ask,
            verify_holder=verify_holder, now=lambda: datetime.fromtimestamp(now(), timezone.utc))
        with live_view(engine, operation, source=source) as current:
            recovery.require(current == initial)
            receipt = verify(store, current, now())
            packet = identity_entry.evidence.validate(raw)
            recovery.require(now() < identity_entry.evidence.instant(packet['expires_at']).timestamp()
                <= receipt['expires'] and not path.exists() and not path.is_symlink()
                and not journal.exists() and not journal.is_symlink())
            ops.write_protected(path, raw)
    return {'outcome': 'web_manifest_saved_not_transferred_or_attested'}


def attest(action, operation, source, engine, *, store=None, now=lambda: int(time.time())):
    """Same-WEB-host intake over the existing protected database connection.

    Reconciliation only checks historical acknowledgement and cleans exact input;
    it never submits or requires an expired proof to become current again.
    """
    recovery.require(action in ('attest', 'reconcile', 'status'))
    store = store or recovery.Store()
    with store.locked(), ops.host_locks():
        def submit(raw, target):
            def guard(connection, packet):
                selected, pair = plans.load(activation=True)
                recovery.require(pair['provider']['operations'] == source
                    and pair['activation']['operations'] == source)
                staging_raw = ops.read_protected(ops.ROOT / 'staging.env')
                auth_raw = ops.read_protected(ops.ROOT / 'auth-v2.env')
                database = [target.url.host, target.url.port or 5432, target.url.database,
                    str(connection.scalar(text('SELECT oid FROM pg_database WHERE datname=current_database()')))]
                current = snapshot(connection, operation, ops.assignments(staging_raw),
                    ops.assignments(auth_raw), selected, pair, database)
                receipt = verify(store, current, now())
                identity_entry.evidence._same_context(current['context'], packet['context'])
                recovery.require(packet['version'] == 2
                    and identity_entry.evidence.instant(packet['holder']['completed_at']).timestamp() == receipt['issued']
                    and now() < identity_entry.evidence.instant(packet['expires_at']).timestamp() <= receipt['expires'])
                plans.require_current(selected)
                recovery.require(ops.read_protected(ops.ROOT / 'staging.env') == staging_raw
                    and ops.read_protected(ops.ROOT / 'auth-v2.env') == auth_raw)
            return identity_entry.evidence.submit_with_holder_guard(raw, target, guard)
        return operator.process(action, operation, source, engine, submit=submit)


def run(action, operation, engine, terminal, *, store=None, now=lambda: int(time.time()), source=None):
    """Attended caller supplies personal tested terminal; no secret arguments/stdio."""
    recovery.require(action in ('consume', 'receipt') and str(UUID(operation)) == operation)
    store = store or recovery.Store()
    # Lock order is credential -> host -> DB throughout, avoiding reverse waits.
    with store.locked():
        with live_view(engine, operation, source=source) as initial:
            value = store.read()
            recovery.require(value is not None and value['binding'] == initial['binding'])
        if action == 'consume':
            recovery.require(terminal.ask('Owner held-transition credential proof only; type OWNER-TRANSITION') == 'OWNER-TRANSITION')
            code = terminal.ask('Single-use owner recovery code; this consumes it permanently')
            with live_view(engine, operation, source=source) as current:
                recovery.require(current == initial)
                consume(store, code, current, now())
        with live_view(engine, operation, source=source) as current:
            recovery.require(current == initial)
            receipt = verify(store, current, now())
        # This is an operator-visible summary, not remotely authenticated proof.
        terminal.show('Owner holder proof verified for the exact held operation.\n'
            'Holder verified at: ' + datetime.fromtimestamp(receipt['issued'], timezone.utc).isoformat() + '\n'
            'Proof expires at: ' + datetime.fromtimestamp(receipt['expires'], timezone.utc).isoformat() + '\n'
            'Target authentication, provider revocation and owner approval remain separate.\n')
    return receipt
