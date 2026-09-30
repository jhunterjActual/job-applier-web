"""Protected P/ancestry transfer only; never transfers web credentials or effects.

Transport is separately authorized SSH/operator work. This format authenticates
no sender; the attended importer requires independently verified source transfer.
"""
import hashlib

import admission_operations as ops
import identity_plan_packet as packet
import identity_plan_store as store

LIMIT=2097152


def validate(raw):
    ops.require(type(raw) is bytes and len(raw)<=LIMIT)
    value=ops.strict_json(raw)
    ops.require(type(value) is dict and set(value)=={'version','selection','archives'} and type(value['version']) is int and value['version']==1)
    s=value['selection']; archives=value['archives']
    ops.require(type(s) is dict and set(s)=={'version','digest','operation','sequence'}
        and type(s['version']) is int and s['version']==1 and type(s['sequence']) is int and 1<=s['sequence']<9223372036854775807)
    ops.require(type(archives) is dict and 1<=len(archives)<=32)
    for digest,contents in archives.items():
        store.archive(digest)
        canonical=packet.canonical(ops.encoded(contents))
        ops.require(hashlib.sha256(canonical).hexdigest()==digest)
    digest=s['digest']; seen=set(); child=None
    while True:
        ops.require(digest in archives and digest not in seen)
        seen.add(digest); current=archives[digest]
        if child is not None: packet.successor(current,child)
        if current['version']==1: break
        child=current; digest=current['predecessor']
    ops.require(seen==set(archives))
    selected=packet.selected(ops.encoded(archives[s['digest']]),s['operation'],activation=True)
    return value,selected


def export(source):
    with ops.host_locks():
        snapshot,pair=store.load(activation=True)
        ops.require(pair['provider']['operations']==source.parent.parent.name)
        selection=ops.strict_json(snapshot); archives={}; digest=selection['digest']
        while True:
            ops.require(digest not in archives and len(archives)<32)
            value=packet.validate(ops.read_protected(store.archive(digest),limit=packet.LIMIT))
            archives[digest]=value
            if value['version']==1: break
            digest=value['predecessor']
        raw=ops.encoded({'version':1,'selection':selection,'archives':archives})
        validate(raw)
        path=ops.STATE/'identity-plan-transfer.outgoing.json'
        previous=ops.read_protected(path,limit=LIMIT) if path.exists() or path.is_symlink() else None
        ops.write_protected(path,raw,previous=previous)
    return {'outcome':'private_plan_transfer_prepared_no_remote_action'}


def install(raw,previous,source,guard):
    """Guard is the fixed DB-host check supplied by run; tests use a fixture."""
    value,pair=validate(raw); selection=value['selection']
    ops.require(pair['provider']['operations']==source.parent.parent.name)
    with ops.host_locks():
        store.barrier.require_unblocked()
        guard()
        store.legacy_absent()
        if previous is None: ops.require(not store.exists())
        else:
            current,old_pair=store.load(activation=True)
            ops.require(current==previous)
            old=ops.strict_json(current)
            if selection==old:
                return {'outcome':'exact_plan_transfer_already_present'}
            ops.require(selection['sequence']>old['sequence'] and selection['operation']==old['operation']
                and old['digest'] in value['archives'] and pair['activation']==old_pair['activation'])
        # Archives are immutable. Interruption leaves only validated extra
        # archives; selection is published last, with exact readback.
        for digest,contents in value['archives'].items():
            path=store.archive(digest); encoded=ops.encoded(contents)
            if path.exists() or path.is_symlink(): ops.require(ops.read_protected(path,limit=packet.LIMIT)==encoded)
            else: ops.write_protected(path,encoded)
        new=ops.encoded(selection)
        store.decode(new,activation=True)
        ops.write_protected(store.location(),new,previous=previous)
        ops.require(store.load(activation=True)[0]==new)
    return {'outcome':'plan_imported_no_database_or_service_change'}


def db_guard():
    db=ops.LocalPostgres()
    ops.require(db.query("SELECT current_database()='careertrellis' AND session_user='postgres' "
        "AND current_setting('server_version_num')::int BETWEEN 180000 AND 189999 "
        "AND current_setting('data_directory')='/var/lib/postgresql/18/main';") is True)
    ops.require(db.query("SELECT minimum_session_version=1 AND NOT signup_open FROM careertrellis_admission.policy;") is True)


def run(action,source):
    try:
        if action=='export': result=export(source)
        else:
            ops.require(action=='import')
            from identity_entry import SecureTerminal
            raw=ops.read_protected(ops.STATE/'identity-plan-transfer.incoming.json',limit=LIMIT)
            _,pair=validate(raw); db_guard()
            previous=store.load(activation=True)[0] if store.exists() else None
            with SecureTerminal() as terminal:
                ops.require(terminal.ask('Confirm your visible secure terminal: type VISIBLE')=='VISIBLE')
                ops.require(terminal.ask('Independently approved P operation UUID')==pair['provider']['operation'])
                ops.require(terminal.ask('Exact protected source transfer independently verified: type SOURCE-VERIFIED')=='SOURCE-VERIFIED')
                ops.require(terminal.ask('Import plans only; no database activation: type IMPORT')=='IMPORT')
            ops.require(ops.read_protected(ops.STATE/'identity-plan-transfer.incoming.json',limit=LIMIT)==raw)
            result=install(raw,previous,source,db_guard)
        print(ops.encoded(result).decode().strip()); return 0
    except Exception:
        raise ops.OperationError(ops.ERROR) from None
