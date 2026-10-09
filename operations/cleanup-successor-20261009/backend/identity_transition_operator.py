"""Attended protected transition preparation. No provider calls or activation.

Inputs and immutable requests are private. An exact historical result is not
fresh evidence. Resume retains the hold and requires subsequent plan supersession.
"""
from datetime import datetime, timedelta, timezone
import re
from uuid import UUID

import admission_operations as ops
import identity_entry as entry
import identity_evidence as evidence
import identity_operator as operator
import identity_plan_store as store
import identity_web_handoff as handoff
import session_v2_transition as transition

LIMIT = 16384


def uuid(value):
    ops.require(type(value) is str and str(UUID(value)) == value)
    return value


def location(operation):
    return ops.STATE / ('identity-request-' + uuid(operation) + '.json')


def validate(raw):
    ops.require(type(raw) is bytes and len(raw) <= LIMIT)
    r = ops.strict_json(raw)
    ops.require(type(r) is dict and set(r) == {'version', 'action', 'selection', 'operation',
        'previous', 'account_id', 'incarnation', 'epoch', 'old_binding', 'old_subject',
        'target_binding', 'target_subject', 'deadline', 'owner_authorized'})
    ops.require(type(r['version']) is int and r['version'] == 1 and r['action'] in ('prepare', 'resume'))
    uuid(r['operation']); uuid(r['incarnation'])
    if r['action'] == 'resume': ops.require(uuid(r['previous']) != r['operation'])
    else: ops.require(r['previous'] is None)
    for key in ('account_id', 'epoch'):
        ops.require(type(r[key]) is int and 0 < r[key] < 9223372036854775807)
    for key in ('old_binding', 'target_binding'):
        ops.require(type(r[key]) is str and re.fullmatch('[0-9a-f]{64}', r[key]))
    for key in ('old_subject', 'target_subject'):
        ops.require(type(r[key]) is str and re.fullmatch('user_[A-Za-z0-9_]{6,120}', r[key]))
    ops.require(type(r['owner_authorized']) is bool and type(r['selection']) is str
        and len(r['selection']) <= 1024)
    evidence.instant(r['deadline'])
    return r


def scope(r, snapshot, pair):
    p = pair['provider']
    ops.require(p['mode'] == 'switch_environment' and r['selection'].encode() == snapshot
        and r['old_binding'] == p['baseline_binding'] and r['target_binding'] == p['target_binding']
        and r['account_id'] not in p['quarantined_accounts'])
    if r['action'] == 'prepare': ops.require(r['operation'] in p['transitions'])
    else:
        ops.require(r['previous'] in p['transitions'] and r['operation'] not in p['transitions']
            and r['operation'] != p['operation'])
    if r['owner_authorized']:
        ops.require(transition.owner_proof(p['operation'], p['target_binding'], r['target_subject']) == p['owner_proof'])


def guard(c, p, activation):
    from sqlalchemy import text
    operator.setup(c)
    c.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-admission-maintenance-v1',0))")
    c.exec_driver_sql('SELECT careertrellis_admission.lock_lifecycle()')
    c.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-alembic-migrations-v1',0))")
    c.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
    ops.require(c.scalar(text('SELECT version_num FROM careertrellis.alembic_version'))==transition.schema(activation))
    c.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
    ops.require(c.scalar(text('SELECT maintenance AND NOT signup_open AND minimum_session_version=1 FROM careertrellis_admission.policy')) is True)
    ops.require(c.scalar(text('SELECT NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_configuration_receipts WHERE operation=CAST(:op AS uuid))'),{'op':p['operation']}) is True)
    ops.require(c.scalar(text("SELECT count(*)=4 AND bool_and(NOT rolcanlogin) FROM pg_roles WHERE rolname IN "
        "('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker')")) is True)
    ops.require(c.scalar(text("SELECT NOT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
        "AND pid<>pg_backend_pid() AND usename IN ('careertrellis_runtime','careertrellis_worker',"
        "'careertrellis_erasure_worker','careertrellis_admission_broker','careertrellis_migration'))")) is True)
    ops.require(c.scalar(text('SELECT binding FROM careertrellis_admission.identity_provider')) == p['baseline_binding'])


def row(c, operation):
    from sqlalchemy import text
    return c.execute(text('SELECT *,deadline<=clock_timestamp() AS expired FROM '
        'careertrellis_admission.identity_transitions WHERE operation=CAST(:op AS uuid)'),
        {'op': operation}).mappings().first()


def same(r, value):
    ops.require(value is not None)
    for key in ('account_id', 'old_binding', 'old_subject', 'target_binding', 'target_subject', 'owner_authorized'):
        ops.require(value[key] == r[key])
    ops.require(str(value['incarnation']) == r['incarnation'] and value['epoch'] == r['epoch'] + 1
        and value['deadline'] == evidence.instant(r['deadline'])
        and (str(value['predecessor']) if value['predecessor'] else None) == r['previous']
        and value['state'] in operator.STATES)


def mutate(c, r, p):
    from sqlalchemy import text
    existing = row(c, r['operation'])
    if existing:
        same(r, existing)
        return {'outcome': 'historical_request_recorded'}
    account = c.execute(text('SELECT a.*,careertrellis_admission.identity_erasing(a.id) AS erasing, '
        '(a.role=u.role AND a.subject=u.workos_user_id AND u.disabled_at IS NULL) AS consistent '
        'FROM careertrellis_admission.identity_accounts a JOIN careertrellis.app_users u ON u.id=a.id '
        'WHERE a.id=:id'), {'id': r['account_id']}).mappings().first()
    ops.require(account is not None and account['consistent'] is True and not account['disabled'] and not account['erasing']
        and account['epoch'] == r['epoch'] and str(account['incarnation']) == r['incarnation']
        and account['binding'] == r['old_binding'] and account['subject'] == r['old_subject']
        and (account['role'] == 'owner') == r['owner_authorized'])
    if r['action'] == 'resume':
        previous = row(c, r['previous'])
        ops.require(previous is not None and previous['expired'] is True and previous['state'] in ('held', 'verified')
            and account['held'] and previous['account_id'] == r['account_id'] and previous['epoch'] == r['epoch']
            and str(previous['incarnation']) == r['incarnation'])
        for key in ('old_binding', 'old_subject', 'target_binding', 'target_subject'):
            ops.require(previous[key] == r[key])
        result = c.scalar(text('SELECT careertrellis_admission.identity_resume(CAST(:previous AS uuid),'
            'CAST(:operation AS uuid),:epoch,CAST(:deadline AS timestamptz),:owner_authorized)'), r)
    else:
        ops.require(not account['held'])
        result = c.scalar(text('SELECT careertrellis_admission.identity_prepare(CAST(:operation AS uuid),'
            ':account_id,:old_binding,:old_subject,:epoch,CAST(:incarnation AS uuid),'
            ':target_binding,:target_subject,CAST(:deadline AS timestamptz),:owner_authorized)'), r)
    ops.require(result == 'held')
    same(r, row(c, r['operation']))
    return {'outcome': 'held_supersession_required' if r['action'] == 'resume' else 'held_evidence_required'}


def capture(action, snapshot, pair, ask):
    ops.require(ask('Confirm you started this in your visible secure terminal: type VISIBLE') == 'VISIBLE')
    operation = uuid(ask('Planned transition UUID' if action == 'prepare' else 'New successor transition UUID'))
    path = location(operation)
    if path.exists() or path.is_symlink():
        r = validate(ops.read_protected(path, limit=LIMIT))
        ops.require(r['action'] == action)
        scope(r, snapshot, pair)
        ops.require(ask('Retry the exact immutable saved request: type RETRY') == 'RETRY')
        return r
    r = {'version': 1, 'action': action, 'selection': snapshot.decode(), 'operation': operation,
        'previous': uuid(ask('Expired predecessor transition UUID')) if action == 'resume' else None}
    for key, label in (('account_id', 'Verified local account ID'), ('incarnation', 'Verified account incarnation UUID'),
            ('epoch', 'Verified current account epoch'), ('old_binding', 'Verified source provider binding'),
            ('old_subject', 'Verified source WorkOS user ID'), ('target_binding', 'Verified target provider binding'),
            ('target_subject', 'Verified target WorkOS user ID')):
        value = ask(label)
        if key in ('account_id', 'epoch'):
            ops.require(re.fullmatch('[1-9][0-9]{0,18}', value))
            value = int(value)
        r[key] = value
    approval = ask('Owner operation separately authorized: OWNER-AUTHORIZED or NOT-OWNER')
    ops.require(approval in ('OWNER-AUTHORIZED', 'NOT-OWNER'))
    r['owner_authorized'] = approval == 'OWNER-AUTHORIZED'
    ops.require(ask('Apply the account hold with fresh evidence still required: type HOLD') == 'HOLD')
    r['deadline'] = (datetime.now(timezone.utc) + timedelta(minutes=55)).isoformat()
    validate(ops.encoded(r)); scope(r, snapshot, pair)
    return r


def current_release(p):
    ops.require(handoff.CURRENT.resolve()==handoff.RELEASES/p['application'])


def execute(r, snapshot, pair, engine):
    """Persist before database submission; never rewrite or delete this journal."""
    validate(ops.encoded(r)); scope(r, snapshot, pair)
    with ops.host_locks():
        store.require_current(snapshot)
        current_release(pair['provider'])
        ops.stopped_services()
        transition.migration_receipt(pair['activation'])
        with operator.secure_connection(engine) as c:
            guard(c, pair['provider'], pair['activation'])
            path = location(r['operation']); raw = ops.encoded(r)
            if path.exists() or path.is_symlink(): ops.require(ops.read_protected(path, limit=LIMIT) == raw)
            else: ops.write_protected(path, raw)
            return mutate(c, r, pair['provider'])


def run(action, source):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    engine = None
    try:
        ops.require(action in ('prepare', 'resume'))
        snapshot, pair = store.load(activation=True)
        ops.require(pair['provider']['operations'] == source.parent.parent.name)
        current_release(pair['provider'])
        engine = create_engine(ops.migration_connection_url(operator.DATABASE), poolclass=NullPool,
            hide_parameters=True, connect_args={'connect_timeout': 5})
        # No host lock or open transaction survives into the human input phase.
        with operator.secure_connection(engine) as c: guard(c, pair['provider'], pair['activation'])
        with entry.SecureTerminal() as terminal: r = capture(action, snapshot, pair, terminal.ask)
        result = execute(r, snapshot, pair, engine)
        print(ops.encoded(result).decode().strip())
        return 0
    except Exception:
        raise ops.OperationError(ops.ERROR) from None
    finally:
        if engine is not None: engine.dispose()
