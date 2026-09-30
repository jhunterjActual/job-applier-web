"""Protected evidence handoff and historical reconciliation; no activation.

Invoked only by the immutable admission launcher. Explicit guided entry uses
the operator's visible secure terminal; other actions never prompt. No action
prints identities.
"""
import hashlib
import logging
import os
from pathlib import Path
import re
import stat
from uuid import UUID

import admission_operations as ops
import identity_evidence as evidence
import identity_selection_barrier as barrier

INBOX = Path('/run/careertrellis/identity-intake')
DATABASE = 'careertrellis'
HEAD = '20260909_0015'
HEADS = (HEAD, '20260915_0016', '20260929_0021')
STATES = ('held','verified','committed','superseded')


def secure_connection(engine):
    ops.require(not engine.echo and not engine.pool.echo and engine.hide_parameters
        and not engine.logger.isEnabledFor(logging.DEBUG))
    return engine.begin()


def setup(connection, *, expected_schema=None):
    from sqlalchemy import text
    connection.exec_driver_sql("SET LOCAL statement_timeout='5s'")
    connection.exec_driver_sql("SET LOCAL lock_timeout='2s'")
    connection.exec_driver_sql('SET LOCAL log_parameter_max_length=0')
    connection.exec_driver_sql('SET LOCAL log_parameter_max_length_on_error=0')
    ops.require(connection.scalar(text("SELECT session_user='careertrellis_migration' AND current_database()=:db "
        "AND current_setting('server_version_num')::int BETWEEN 180000 AND 189999 "
        "AND current_setting('transaction_isolation')='read committed'"),{'db':DATABASE}) is True)
    connection.exec_driver_sql('SET LOCAL ROLE careertrellis_schema_owner')
    head=connection.scalar(text('SELECT version_num FROM careertrellis.alembic_version'))
    ops.require(head in HEADS and (expected_schema is None or head==expected_schema))
    connection.exec_driver_sql('SET LOCAL ROLE careertrellis_admission_owner')


def preflight(engine):
    with secure_connection(engine) as connection:
        setup(connection)


def inspect(engine, operation, digest=None, verified=None, expires=None):
    """Historical digest match is not a current grant or permission to activate."""
    from sqlalchemy import text
    with secure_connection(engine) as connection:
        setup(connection)
        value = connection.scalar(text("SELECT jsonb_build_object('state',state,"
            "'recorded',COALESCE(evidence_digest=:digest AND verified_at=CAST(:verified AS timestamptz) AND evidence_expires=CAST(:expires AS timestamptz),false)) "
            "FROM careertrellis_admission.identity_transitions WHERE operation=CAST(:op AS uuid)"),
            {'op':operation,'digest':digest,'verified':verified,'expires':expires})
        ops.require(type(value) is dict and value['state'] in STATES and type(value['recorded']) is bool)
        return value


def locations(operation):
    ops.require(type(operation) is str and str(UUID(operation)) == operation)
    ops.protected_parents(INBOX/'sentinel')
    item=INBOX.lstat()
    ops.require(stat.S_ISDIR(item.st_mode) and item.st_uid==0 and stat.S_IMODE(item.st_mode)==0o700)
    return INBOX/(operation+'.json'), ops.STATE/('identity-intake-'+operation+'.json')


def fingerprint(path):
    item=path.lstat()
    return (item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns,item.st_ctime_ns)


def remove_exact(path, raw, original):
    ops.require(fingerprint(path)==original and ops.read_protected(path,limit=evidence.LIMIT)==raw)
    ops.require(fingerprint(path)==original)
    path.unlink()
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def journal(path, operation, source):
    value=ops.strict_json(ops.read_protected(path))
    ops.require(type(value) is dict and set(value)=={'version','operation','source','digest','phase','verified','expires'}
        and type(value['version']) is int and value['version']==1 and value['operation']==operation and value['source']==source
        and value['phase'] in ('prepared','recorded') and re.fullmatch('[0-9a-f]{64}',value['digest']))
    ops.require(evidence.instant(value['verified']) < evidence.instant(value['expires']))
    return value


def recorded_status(engine, operation, record):
    return inspect(engine,operation,record['digest'],record['verified'],record['expires']) if record else inspect(engine,operation)


def process(action, operation, source, engine, *, submit=None):
    """Caller holds host locks for attest/reconcile. No raw input in receipts."""
    ops.require(action in ('status','attest','reconcile') and re.fullmatch('[0-9a-f]{40}',source))
    if action != 'status':
        barrier.require_unblocked()
    path,record_path=locations(operation)
    preflight(engine)
    record=journal(record_path,operation,source) if record_path.exists() else None
    if action=='status':
        return recorded_status(engine,operation,record)
    if not path.exists():
        ops.require(record is not None and record['phase']=='recorded')
        ops.require(recorded_status(engine,operation,record)['recorded'])
        return {'outcome':'recorded_input_removed'}
    original=fingerprint(path)
    raw=ops.read_protected(path,limit=evidence.LIMIT)
    packet=evidence.validate(raw)
    ops.require(packet['context']['operation']==operation and fingerprint(path)==original)
    digest=hashlib.sha256(ops.encoded(packet)).hexdigest()
    if record is None:
        ops.require(action=='attest')
        record={'version':1,'operation':operation,'source':source,'digest':digest,'phase':'prepared',
            'verified':packet['verified_at'],'expires':packet['expires_at']}
        ops.write_protected(record_path,ops.encoded(record))
    ops.require(record['digest']==digest)
    result=recorded_status(engine,operation,record)
    if not result['recorded']:
        ops.require(action=='attest' and record['phase']=='prepared' and result['state']=='held')
        (submit or evidence.submit)(raw,engine)
        # A lost acknowledgement escapes without cleanup. Reconcile explicitly.
        ops.require(recorded_status(engine,operation,record)['recorded'])
    record['phase']='recorded'
    try:
        ops.state_write(record_path,record)
        remove_exact(path,raw,original)
    except Exception:
        return {'outcome':'recorded_cleanup_pending'}
    return {'outcome':'recorded_input_removed'}


def run(action, operation, source):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    ops.require(str(UUID(operation))==operation)
    url=ops.migration_connection_url(DATABASE)
    engine=create_engine(url,poolclass=NullPool,hide_parameters=True,connect_args={'connect_timeout':5})
    try:
        if action=='preflight':
            locations(operation)
            ops.protected_parents(ops.STATE/'sentinel')
            state=ops.STATE.lstat()
            ops.require(stat.S_ISDIR(state.st_mode) and state.st_uid==0 and stat.S_IMODE(state.st_mode)==0o700)
            preflight(engine)
            result={'outcome':'preflight_ready'}
        elif action=='enter':
            import identity_entry
            result=identity_entry.enter(operation,engine)
        elif action=='status':
            result=process(action,operation,source.parent.parent.name,engine)
        else:
            with ops.host_locks():
                result=process(action,operation,source.parent.parent.name,engine)
        # Fixed vocabularies only. No data, hashes, paths, SQL or exception text.
        print(ops.encoded(result).decode().strip())
        return 1 if result.get('outcome')=='recorded_cleanup_pending' else 0
    finally: engine.dispose()
