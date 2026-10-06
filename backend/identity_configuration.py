"""Atomic database-side provider coordination, for a protected attended caller.

No provider calls or web configuration writes. The caller independently verifies
the CURRENT protected configuration binding before calling apply. A manifest is
an operator assertion, not authentication of that assertion.
"""
import hashlib
import re
from uuid import UUID

import admission_operations as ops
import identity_operator as operator
import session_v2_transition as transition


def validate(raw):
    ops.require(type(raw) is bytes and len(raw)<=16384)
    p=ops.strict_json(raw)
    ops.require(type(p) is dict and set(p)=={'version','operation','mode','source_application',
        'application','operations','baseline_binding','target_binding','expected_generation',
        'owner_proof','transitions','quarantined_accounts','authorized'})
    ops.require(type(p['version']) is int and p['version']==1 and p['authorized'] is True)
    ops.require(str(UUID(p['operation']))==p['operation'])
    for key in ('source_application','application','operations'):
        ops.require(type(p[key]) is str and re.fullmatch('[0-9a-f]{40}',p[key]))
    for key in ('baseline_binding','target_binding','owner_proof'):
        ops.require(type(p[key]) is str and re.fullmatch('[0-9a-f]{64}',p[key]))
    ops.require(type(p['expected_generation']) is int and 0<=p['expected_generation']<9223372036854775770)
    ops.require(p['mode'] in ('bind_current','switch_environment'))
    ops.require(type(p['transitions']) is list and len(p['transitions'])<=25
        and all(type(x) is str and str(UUID(x))==x for x in p['transitions'])
        and p['transitions']==sorted(set(p['transitions'])))
    ops.require(type(p['quarantined_accounts']) is list and len(p['quarantined_accounts'])<=25
        and all(type(x) is int and 0<x<=9223372036854775807 for x in p['quarantined_accounts'])
        and p['quarantined_accounts']==sorted(set(p['quarantined_accounts'])))
    if p['mode']=='bind_current':
        ops.require(p['target_binding']==p['baseline_binding'] and not p['transitions'] and not p['quarantined_accounts'])
    else:
        ops.require(p['target_binding']!=p['baseline_binding'] and bool(p['transitions']))
    return p


def apply(raw,engine,*,verified_current_binding,expected_schema=None):
    """An exact historical retry does not attest current readiness.

    verified_current_binding must come from independently reviewed CURRENT
    protected web configuration, never the proposed target credentials/manifest.
    The installed configuration handoff remains a separate release gate.
    """
    from sqlalchemy import text
    try:
        p=validate(raw); digest=hashlib.sha256(ops.encoded(p)).hexdigest()
        with operator.secure_connection(engine) as c:
            operator.setup(c)
            c.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-admission-maintenance-v1',0))")
            c.exec_driver_sql('SELECT careertrellis_admission.lock_lifecycle()')
            if expected_schema is not None:
                c.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtextextended('careertrellis-alembic-migrations-v1',0))")
                # setup left the effective role as admission_owner. Its logging
                # safeguards require the verified migration session role again.
                c.exec_driver_sql('SET LOCAL ROLE NONE')
                operator.setup(c,expected_schema=expected_schema)
            record=c.execute(text('SELECT plan_digest,generation FROM careertrellis_admission.identity_configuration_receipts WHERE operation=CAST(:op AS uuid)'),{'op':p['operation']}).first()
            if record:
                ops.require(record[0]==digest)
                return {'outcome':'historical_configuration_committed'}
            ops.require(verified_current_binding==p['baseline_binding'])
            ops.require(c.scalar(text('SELECT maintenance AND NOT signup_open AND generation=:g FROM careertrellis_admission.policy'),{'g':p['expected_generation']}) is True)
            # NOLOGIN is not termination: prove surviving callers are absent.
            ops.require(c.scalar(text("SELECT count(*)=4 AND bool_and(NOT rolcanlogin) FROM pg_roles WHERE rolname IN "
                "('careertrellis_runtime','careertrellis_worker','careertrellis_erasure_worker','careertrellis_admission_broker')")) is True)
            ops.require(c.scalar(text("SELECT NOT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
                "AND pid<>pg_backend_pid() AND usename IN ('careertrellis_runtime','careertrellis_worker',"
                "'careertrellis_erasure_worker','careertrellis_admission_broker','careertrellis_migration'))")) is True)
            binding=c.scalar(text('SELECT binding FROM careertrellis_admission.identity_provider'))
            accounts=c.execute(text("SELECT a.*, (u.role=a.role AND u.workos_user_id=a.subject AND (u.disabled_at IS NOT NULL)=a.disabled) AS consistent, "
                "careertrellis_admission.identity_erasing(a.id) AS erasing FROM careertrellis_admission.identity_accounts a "
                "JOIN careertrellis.app_users u ON u.id=a.id ORDER BY a.id")).mappings().all()
            ops.require(0<len(accounts)<=25 and all(a['consistent'] for a in accounts)
                and len(accounts)==c.scalar(text('SELECT count(*) FROM careertrellis.app_users')))
            owner_seen=False
            if p['mode']=='bind_current':
                ops.require(binding is None and all(a['binding'] is None for a in accounts))
                ops.require(c.scalar(text('SELECT NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_history) AND NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_transitions) AND NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_provider_history)')) is True)
                for a in accounts:
                    if a['role']=='owner':
                        ops.require(not owner_seen and not a['disabled'] and not a['held'] and not a['erasing'])
                        ops.require(transition.owner_proof(p['operation'],p['target_binding'],a['subject'])==p['owner_proof'])
                        owner_seen=True
            else:
                ops.require(binding==p['baseline_binding'])
                ops.require(c.scalar(text('SELECT NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_provider_history WHERE binding=:target)'),{'target':p['target_binding']}) is True)
                ops.require(c.scalar(text("SELECT NOT EXISTS(SELECT 1 FROM careertrellis_admission.identity_history "
                    "WHERE binding=:target AND state<>'pending')"),{'target':p['target_binding']}) is True)
                excluded=[a['id'] for a in accounts if a['disabled'] or a['erasing']]
                ops.require(excluded==p['quarantined_accounts'])
                eligible={a['id']:a for a in accounts if a['id'] not in excluded}
                operations=[]; seen=set()
                for op in p['transitions']:
                    item=c.execute(text("SELECT p.*,p.deadline>clock_timestamp() AND p.evidence_expires>clock_timestamp() AS fresh "
                        "FROM careertrellis_admission.identity_transitions p WHERE operation=CAST(:op AS uuid)"),{'op':op}).mappings().first()
                    ops.require(item is not None and item['state']=='verified' and item['fresh'] is True
                        and item['evidence_digest'] is not None and item['verified_at'] is not None
                        and item['account_id'] in eligible and item['account_id'] not in seen)
                    a=eligible[item['account_id']]
                    ops.require(a['held'] and (a['incarnation'],a['epoch'],a['role'],a['binding'],a['subject'])==
                        (item['incarnation'],item['epoch'],item['expected_role'],item['old_binding'],item['old_subject'])
                        and item['old_binding']==p['baseline_binding'] and item['target_binding']==p['target_binding'])
                    if a['role']=='owner':
                        ops.require(not owner_seen and item['owner_authorized'] and
                            transition.owner_proof(p['operation'],p['target_binding'],item['target_subject'])==p['owner_proof'])
                        owner_seen=True
                    operations.append((op,item['epoch'])); seen.add(a['id'])
                ops.require(seen==set(eligible))
            ops.require(owner_seen)
            c.execute(text('SELECT careertrellis_admission.configure_identity_provider(:old,:target)'),
                {'old':binding,'target':p['target_binding']})
            if p['mode']=='switch_environment':
                for op,epoch in operations:
                    c.execute(text('SELECT careertrellis_admission.identity_activate(CAST(:op AS uuid),:epoch)'),{'op':op,'epoch':epoch})
            generation=c.scalar(text('SELECT generation FROM careertrellis_admission.policy'))
            c.execute(text('INSERT INTO careertrellis_admission.identity_configuration_receipts(operation,plan_digest,generation) VALUES(CAST(:op AS uuid),:digest,:g)'),
                {'op':p['operation'],'digest':digest,'g':generation})
        return {'outcome':'configuration_committed_maintenance_retained'}
    except Exception:
        raise ops.OperationError(ops.ERROR) from None
