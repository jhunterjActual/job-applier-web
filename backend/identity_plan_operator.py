"""Visible-terminal initial packet authoring and CAS selection; no activation.

Inputs are private assertions, not independently authenticated evidence. The
immutable launcher supplies the source path; never invoke with identity argv.
"""
from pathlib import Path
import hashlib
import re
import stat

import admission_operations as ops
from identity_entry import SecureTerminal
import identity_plan_packet as packet
import identity_plan_store as store
import identity_web_handoff as handoff
import session_v2_transition as transition


def preflight(source):
    source=Path(source)
    ops.require(source==Path(ops.__file__).absolute()
        and source.parent.parent.parent==handoff.RELEASES
        and re.fullmatch('[0-9a-f]{40}',source.parent.parent.name))
    ops.read_protected(source,modes=(0o600,0o640,0o644),limit=131072)
    ops.read_protected(Path(__file__).absolute(),modes=(0o600,0o640,0o644))
    for directory in (ops.ROOT,ops.STATE):
        ops.protected_parents(directory/'sentinel')
        item=directory.lstat()
        ops.require(stat.S_ISDIR(item.st_mode) and item.st_uid==0)
        if directory==ops.STATE: ops.require(stat.S_IMODE(item.st_mode)==0o700)
    store.legacy_absent()
    current=handoff.CURRENT.resolve(strict=True)
    ops.require(current.parent==handoff.RELEASES and re.fullmatch('[0-9a-f]{40}',current.name))
    files=handoff.read_files()
    handoff.validate_files(files)
    return {'application':current.name,'operations':source.parent.parent.name,
        'files':files,'units':handoff.inventory(),'boot':transition.boot()}


def number(ask,prompt,maximum):
    value=ask(prompt)
    ops.require(re.fullmatch('0|[1-9][0-9]{0,18}',value))
    result=int(value); ops.require(result<=maximum)
    return result


def build(initial,ask):
    ops.require(ask('Confirm your visible secure terminal: type VISIBLE')=='VISIBLE')
    ops.require(ask('Exact observed source application commit')==initial['application'])
    ops.require(ask('Exact installed operational commit')==initial['operations'])
    application=ask('Exact approved target application commit')
    realm=ask('Approved beta realm')
    binding,staging,_=handoff.validate_files(initial['files'])
    ops.require(ask('Independently verified source provider binding')==binding.binding)
    target=ask('Independently verified Production provider binding')
    owner=ask('Independently verified Production owner WorkOS user ID')
    ops.require(re.fullmatch('user_[A-Za-z0-9_]{1,120}',owner))
    generation=number(ask,'Observed source policy generation',9223372036854775807)
    bind_operation=ask('Approved current-binding operation UUID')
    switch_operation=ask('Approved Production-switch operation UUID')
    count=number(ask,'Eligible account transition count',25)
    operations=[ask('Next approved account transition UUID') for _ in range(count)]
    quarantine_count=number(ask,'Disabled or erasing quarantine count',25-count)
    quarantine=[number(ask,'Next independently verified quarantine local account ID',9223372036854775807)
        for _ in range(quarantine_count)]
    ops.require(ask('Provider domain, mappings, owner and target settings independently verified: type VERIFIED')=='VERIFIED')
    ops.require(ask('Exact two-operation plan authoring approved; this does not approve execution: type AUTHORING-AUTHORIZED')=='AUTHORING-AUTHORIZED')
    schema=ask('Exact approved target migration head (20260909_0015 or 20260915_0016)')
    ops.require(schema in transition.HEADS)
    def pair(operation,mode,target_binding,subject,expected,transitions,quarantined):
        proof=transition.owner_proof(operation,target_binding,subject)
        return {'provider':{'version':1,'operation':operation,'mode':mode,
            'source_application':initial['application'],'application':application,'operations':initial['operations'],
            'baseline_binding':binding.binding,'target_binding':target_binding,'expected_generation':expected,
            'owner_proof':proof,'transitions':sorted(transitions),'quarantined_accounts':sorted(quarantined),'authorized':True},
            'activation':{'operation':operation,'application':application,'operations':initial['operations'],
            'realm':realm,'cap':25,'ttl':600,'session_seconds':3600,'schema':schema,
            'provider_binding':target_binding,'owner_proof':proof}}
    value={'version':1,'bind':pair(bind_operation,'bind_current',binding.binding,
        staging['CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID'],generation,[],[]),
        'switch':pair(switch_operation,'switch_environment',target,owner,generation+1+count,operations,quarantine)}
    value['bind']['activation']['realm']=binding.generation
    raw=packet.canonical(ops.encoded(value))
    ops.require(ask('Archive immutable packet only, with no selection or activation: type SAVE')=='SAVE')
    return raw


def find_packet(operation):
    from uuid import UUID
    ops.require(str(UUID(operation))==operation)
    matches=[]
    for index,path in enumerate(ops.STATE.glob('identity-plan-*.json')):
        ops.require(index<256)
        digest=path.name[len('identity-plan-'):-len('.json')]
        ops.require(path==store.archive(digest))
        raw=ops.read_protected(path,limit=packet.LIMIT)
        ops.require(hashlib.sha256(raw).hexdigest()==digest)
        value=packet.validate(raw)
        if value['version']==1 and any(value[name]['provider']['operation']==operation for name in ('bind','switch')):
            matches.append((digest,raw))
    ops.require(len(matches)==1)
    return matches[0]


def run(action,source):
    try:
        ops.require(action in ('enter','select','status'))
        initial=preflight(source)
        previous=store.load()[0] if store.exists() else None
        if action=='status':
            result={'outcome':'selection_present' if previous is not None else 'selection_absent'}
        elif action=='enter':
            ops.require(previous is None)
            with SecureTerminal() as terminal: raw=build(initial,terminal.ask)
            # register owns host locks. No transaction or host lock spans input.
            ops.require(preflight(source)==initial and not store.exists())
            store.register(raw)
            result={'outcome':'packet_archived_not_selected'}
        else:
            with SecureTerminal() as terminal:
                ops.require(terminal.ask('Confirm your visible secure terminal: type VISIBLE')=='VISIBLE')
                operation=terminal.ask('Exact approved operation UUID to select')
                ops.require(terminal.ask('Authorize plan selection only, with no activation: type SELECT')=='SELECT')
            ops.require(preflight(source)==initial)
            if previous is None: digest,contents=find_packet(operation)
            else:
                pointer,_=store.decode(previous)
                digest=pointer['digest']
                contents=ops.read_protected(store.archive(digest),limit=packet.LIMIT)
            selected=packet.selected(contents,operation)['provider']
            ops.require(selected['operations']==initial['operations'] and initial['application'] in
                (selected['source_application'],selected['application']))
            store.select(digest,operation,previous=previous)
            result={'outcome':'plan_selected_not_activated'}
        print(ops.encoded(result).decode().strip())
        return 0
    except Exception:
        raise ops.OperationError(ops.ERROR) from None
