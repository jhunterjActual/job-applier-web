"""Attended uncommitted-candidate supersession; no mapping or activation.

Preserves P's maintenance UUID and every old archive/candidate. Database absence
of the atomic configuration receipt is required under lifecycle containment.
"""
from copy import deepcopy
import hashlib

import admission_operations as ops
import identity_operator as operator
import identity_plan_packet as packet
import identity_plan_store as store
import identity_web_handoff as handoff


def replacement(connection, original):
    """Caller holds host and transaction locks; derive only recorded successors."""
    from sqlalchemy import text
    p=original['switch']['provider']
    connection.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-admission-maintenance-v1',0))")
    connection.exec_driver_sql('SELECT careertrellis_admission.lock_lifecycle()')
    connection.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-alembic-migrations-v1',0))")
    connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
    ops.require(connection.scalar(text('SELECT version_num FROM careertrellis.alembic_version'))==original['switch']['activation']['schema'])
    connection.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')
    ops.require(connection.scalar(text("SELECT maintenance AND NOT signup_open AND minimum_session_version=1 "
        "FROM careertrellis_admission.policy")) is True)
    ops.require(connection.scalar(text("SELECT count(*)=4 AND bool_and(NOT rolcanlogin) FROM pg_roles WHERE rolname IN "
        "('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker')")) is True)
    connection.exec_driver_sql(ops.assert_quiescent_sql())
    ops.require(connection.scalar(text('SELECT NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_configuration_receipts WHERE operation=CAST(:op AS uuid))'),{'op':p['operation']}) is True)
    ops.require(connection.scalar(text('SELECT binding=:b FROM careertrellis_admission.identity_provider'),{'b':p['baseline_binding']}) is True)
    b=original['bind']['provider']
    ops.require(connection.scalar(text('SELECT EXISTS(SELECT 1 FROM careertrellis_admission.identity_configuration_receipts WHERE operation=CAST(:op AS uuid) AND plan_digest=:d)'),
        {'op':b['operation'],'d':hashlib.sha256(ops.encoded(b)).hexdigest()}) is True)
    operations=[]; accounts=[]
    for old_op in p['transitions']:
        row=connection.execute(text('SELECT * FROM careertrellis_admission.identity_transitions WHERE operation=CAST(:op AS uuid)'),{'op':old_op}).mappings().one()
        initial=dict(row)
        for _ in range(32):
            if row['state']!='superseded': break
            row=connection.execute(text('SELECT * FROM careertrellis_admission.identity_transitions WHERE predecessor=CAST(:op AS uuid)'),{'op':str(row['operation'])}).mappings().one()
        ops.require(row['state'] in ('held','verified'))
        for key in ('account_id','incarnation','old_binding','old_subject','target_binding','target_subject','expected_role'):
            ops.require(row[key]==initial[key])
        ops.require(row['old_binding']==p['baseline_binding'] and row['target_binding']==p['target_binding'])
        valid=connection.scalar(text("SELECT EXISTS(SELECT 1 FROM careertrellis_admission.identity_transitions t "
            "JOIN careertrellis_admission.identity_accounts a ON a.id=t.account_id "
            "JOIN careertrellis.app_users u ON u.id=a.id WHERE t.operation=CAST(:op AS uuid) "
            "AND t.deadline>clock_timestamp() AND a.held AND NOT a.disabled AND NOT careertrellis_admission.identity_erasing(a.id) "
            "AND a.epoch=t.epoch AND a.incarnation=t.incarnation AND a.binding=t.old_binding AND a.subject=t.old_subject "
            "AND a.role=t.expected_role AND u.role=a.role AND u.workos_user_id=a.subject AND u.disabled_at IS NULL)"),{'op':str(row['operation'])})
        ops.require(valid is True and (row['expected_role']!='owner' or row['owner_authorized'] is True))
        operations.append(str(row['operation'])); accounts.append(row['account_id'])
    eligible=connection.execute(text('SELECT id FROM careertrellis_admission.identity_accounts WHERE NOT disabled AND NOT careertrellis_admission.identity_erasing(id) ORDER BY id')).scalars().all()
    quarantine=connection.execute(text('SELECT id FROM careertrellis_admission.identity_accounts WHERE disabled OR careertrellis_admission.identity_erasing(id) ORDER BY id')).scalars().all()
    ops.require(sorted(accounts)==eligible and quarantine==p['quarantined_accounts'])
    result=deepcopy(original)
    result['version']=2
    result['switch']['provider'].update(source_application=p['application'],transitions=sorted(operations),
        expected_generation=connection.scalar(text('SELECT generation FROM careertrellis_admission.policy')))
    return result


def supersede(snapshot, source, engine):
    store.barrier.require_unblocked()
    from cryptography.fernet import Fernet
    with handoff.locks(),operator.secure_connection(engine) as connection:
        store.require_current(snapshot)
        selection,pair=store.decode(snapshot)
        p=pair['provider']
        ops.require(p['mode']=='switch_environment' and p['operations']==source.parent.parent.name)
        ops.require(handoff.CURRENT.resolve()==handoff.RELEASES/p['application'])
        ops.stopped_services()
        old_raw=ops.read_protected(store.archive(selection['digest']),limit=packet.LIMIT)
        original=packet.validate(old_raw)
        sealed,receipt=handoff.paths(p)
        ops.require(not receipt.exists() and not receipt.is_symlink())
        files=handoff.read_files()
        _,_,auth=handoff.validate_files(files)
        candidate=ops.strict_json(Fernet(auth[handoff.PREFIX+'ENVELOPE_KEY'].encode()).decrypt(ops.read_protected(sealed,limit=524288)))
        ops.require(candidate['plan']==p and candidate['activation']==pair['activation']
            and candidate['before']==files and candidate['units']==handoff.inventory())
        operator.setup(connection)
        value=replacement(connection,original)
        value['predecessor']=selection['digest']
        raw=packet.canonical(ops.encoded(value))
        packet.successor(original,value)
        digest=hashlib.sha256(raw).hexdigest()
        target=store.archive(digest)
        if target.exists() or target.is_symlink(): ops.require(ops.read_protected(target,limit=packet.LIMIT)==raw)
        else: ops.write_protected(target,raw)
        selected=ops.encoded({'version':1,'digest':digest,'operation':p['operation'],'sequence':selection['sequence']+1})
        store.decode(selected)
        # No database mutation occurs here. A lost file acknowledgement is
        # resolved by inspecting selection, not by repeating identity_resume.
        ops.write_protected(store.location(),selected,previous=snapshot)
        ops.require(store.load()[0]==selected)
    return {'outcome':'uncommitted_plan_superseded_candidate_reentry_required'}


def run(source):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from identity_entry import SecureTerminal
    try:
        snapshot,pair=store.load()
        ops.require(pair['provider']['mode']=='switch_environment' and pair['provider']['operations']==source.parent.parent.name)
        with SecureTerminal() as terminal:
            ops.require(terminal.ask('Confirm this is your visible secure terminal: type VISIBLE')=='VISIBLE')
            ops.require(terminal.ask('Approved uncommitted configuration operation UUID')==pair['provider']['operation'])
            ops.require(terminal.ask('Authorize forward supersession preserving original evidence: type SUPERSEDE')=='SUPERSEDE')
        engine=create_engine(ops.migration_connection_url(operator.DATABASE),poolclass=NullPool,hide_parameters=True,connect_args={'connect_timeout':5})
        try: result=supersede(snapshot,source,engine)
        finally: engine.dispose()
        print(ops.encoded(result).decode().strip())
        return 0
    except Exception:
        raise ops.OperationError(ops.ERROR) from None
