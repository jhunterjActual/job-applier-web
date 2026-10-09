"""Protected receipt delivery only; transport and source verification are external.

Never generates a phase receipt or changes database/configuration/service state.
Receiving coordinators must still perform all their live acceptance checks.
"""
import admission_operations as ops
import identity_plan_store as store
import session_v2_transition as transition

LIMIT=32768
DIRECTIONS={'quiesced':('db','web'),'migrated':('web','db'),
    'armed':('db','web'),'enabled':('web','db')}


def contract(p,phase):
    ops.require(phase in DIRECTIONS)
    operation=p['operation']
    if phase=='quiesced':
        return ('database-'+operation+'.json','database-quiesced.json',
            {'phase':phase,'operation':operation,'target':p['application']})
    if phase=='migrated':
        name='web-'+operation+'.json'
        return name,name,{'phase':phase,'operation':operation,'target':p['application'],'head':transition.schema(p)}
    name='v2-'+('armed' if phase=='armed' else 'web')+'-'+operation+'.json'
    return name,name,transition.receipt(p,phase)


def selected(source):
    snapshot,pair=store.load(activation=True)
    ops.require(pair['activation']['operations']==source.parent.parent.name)
    return snapshot,pair['activation']


def envelope(snapshot,p,phase,receipt):
    expected=contract(p,phase)[2]
    # Canonical byte comparison also rejects bools masquerading as integers.
    ops.require(ops.encoded(receipt)==ops.encoded(expected))
    return {'version':1,'phase':phase,'selection':ops.strict_json(snapshot),
        'operations':p['operations'],'receipt':receipt}


def validate(raw,snapshot,p,phase):
    ops.require(type(raw) is bytes and len(raw)<=LIMIT)
    value=ops.strict_json(raw)
    ops.require(type(value) is dict and set(value)=={'version','phase','selection','operations','receipt'})
    expected=envelope(snapshot,p,phase,value['receipt'])
    ops.require(ops.encoded(value)==ops.encoded(expected))
    return ops.encoded(value['receipt'])


def transfer_file(phase,direction):
    ops.require(phase in DIRECTIONS and direction in ('incoming','outgoing'))
    return ops.STATE/('identity-receipt-'+phase+'.'+direction+'.json')


def export(source,side,phase):
    ops.require(phase in DIRECTIONS and DIRECTIONS[phase][0]==side)
    with ops.host_locks():
        snapshot,p=selected(source)
        name,_,_=contract(p,phase)
        receipt=ops.strict_json(ops.read_protected(ops.STATE/name,limit=LIMIT))
        raw=ops.encoded(envelope(snapshot,p,phase,receipt))
        path=transfer_file(phase,'outgoing')
        old=ops.read_protected(path,limit=LIMIT) if path.exists() or path.is_symlink() else None
        if old!=raw: ops.write_protected(path,raw,previous=old)
        ops.require(ops.read_protected(path,limit=LIMIT)==raw)
    return {'outcome':'receipt_exported_no_remote_action'}


def install(source,side,phase,raw,snapshot):
    ops.require(phase in DIRECTIONS and DIRECTIONS[phase][1]==side)
    with ops.host_locks():
        current,p=selected(source)
        ops.require(current==snapshot)
        ops.require(ops.read_protected(transfer_file(phase,'incoming'),limit=LIMIT)==raw)
        receipt=validate(raw,snapshot,p,phase)
        path=ops.STATE/contract(p,phase)[1]
        if path.exists() or path.is_symlink():
            ops.require(ops.read_protected(path,limit=LIMIT)==receipt)
        else: ops.write_protected(path,receipt)
        ops.require(ops.read_protected(path,limit=LIMIT)==receipt)
    return {'outcome':'receipt_imported_no_activation'}


def run(action,source,side,phase):
    try:
        ops.require(action in ('export','import') and phase in DIRECTIONS)
        ops.require(DIRECTIONS[phase][0 if action=='export' else 1]==side)
        if action=='export': result=export(source,side,phase)
        else:
            from identity_entry import SecureTerminal
            snapshot,p=selected(source)
            raw=ops.read_protected(transfer_file(phase,'incoming'),limit=LIMIT)
            validate(raw,snapshot,p,phase)
            with SecureTerminal() as terminal:
                ops.require(terminal.ask('Confirm your visible secure terminal: type VISIBLE')=='VISIBLE')
                ops.require(terminal.ask('Independently approved P operation UUID')==p['operation'])
                ops.require(terminal.ask('Independently verified receipt phase')==phase)
                ops.require(terminal.ask('Protected source and transport verified: type SOURCE-VERIFIED')=='SOURCE-VERIFIED')
                ops.require(terminal.ask('Import receipt only; no activation: type IMPORT')=='IMPORT')
            result=install(source,side,phase,raw,snapshot)
        print(ops.encoded(result).decode().strip()); return 0
    except Exception:
        raise ops.OperationError(ops.ERROR) from None
