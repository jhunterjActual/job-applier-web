"""Pure two-host publication model; no persistence, selector or execution port.

Hashes bind observations, not their provenance. Even convergence cannot release
consumers until separately reviewed host locks, evidence and adapters exist.
"""
from copy import deepcopy
import hashlib
import re
from uuid import UUID

import admission_operations as ops
from identity_plan_replacement import validate_proposal

HOSTS=('web','database')
ERROR='Selection journal rejected.'


def require(value):
    if not value: raise ValueError(ERROR)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def hash_value(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def seal(body):
    return {**body,'journal_sha256':sha(ops.encoded(body))}


def pointer(raw):
    require(type(raw) is bytes and len(raw)<=1024)
    value=ops.strict_json(raw)
    require(type(value) is dict and set(value)=={'version','digest','operation','sequence'})
    require(type(value['version']) is int and value['version']==1 and hash_value(value['digest']))
    require(type(value['operation']) is str and str(UUID(value['operation']))==value['operation'])
    require(type(value['sequence']) is int and 1<=value['sequence']<9223372036854775806)
    return value


def prepare(proposal,*,transaction,target_digest,target_operation,prior_selections):
    try:
        validate_proposal(proposal)
        require(type(transaction) is str and str(UUID(transaction))==transaction)
        require(type(target_operation) is str and str(UUID(target_operation))==target_operation)
        require(hash_value(target_digest) and target_digest!=proposal['predecessor_archive_sha256'])
        require(type(prior_selections) is dict and set(prior_selections)==set(HOSTS))
        prior_operation=pointer(prior_selections['web'])['operation']
        hosts={}
        for host in HOSTS:
            raw=prior_selections[host]; old=pointer(raw)
            require(old['digest']==proposal['predecessor_archive_sha256']
                and old['operation']==prior_operation and old['operation']!=target_operation)
            if host=='web':
                require(sha(raw)==proposal['predecessor_selection_sha256']
                    and old['sequence']==proposal['predecessor_sequence'])
            new={**old,'digest':target_digest,'operation':target_operation,'sequence':old['sequence']+1}
            hosts[host]=dict(prior_sha256=sha(raw),target_sha256=sha(ops.encoded(new)),
                prior_sequence=old['sequence'],target_sequence=new['sequence'],state='prepared')
        return seal(dict(kind='two_host_selection_journal_v1',transaction=transaction,
            proposal_sha256=proposal['proposal_sha256'],target_digest=target_digest,
            target_operation=target_operation,revision=0,previous_sha256=None,hosts=hosts,
            execution_authorized=False,consumers_enabled=False))
    except Exception:
        raise ValueError(ERROR) from None


def validate(record):
    require(type(record) is dict and set(record)=={'kind','transaction','proposal_sha256',
        'target_digest','target_operation','revision','previous_sha256','hosts',
        'execution_authorized','consumers_enabled','journal_sha256'})
    require(record['kind']=='two_host_selection_journal_v1'
        and record['execution_authorized'] is False and record['consumers_enabled'] is False)
    for name in ('transaction','target_operation'):
        require(type(record[name]) is str and str(UUID(record[name]))==record[name])
    for name in ('proposal_sha256','target_digest','journal_sha256'): require(hash_value(record[name]))
    require(type(record['revision']) is int and 0<=record['revision']<=2)
    require(record['previous_sha256'] is None if record['revision']==0 else hash_value(record['previous_sha256']))
    require(type(record['hosts']) is dict and set(record['hosts'])==set(HOSTS))
    claims=0
    for host in HOSTS:
        item=record['hosts'][host]
        require(type(item) is dict and set(item)=={'prior_sha256','target_sha256','prior_sequence','target_sequence','state'})
        require(hash_value(item['prior_sha256']) and hash_value(item['target_sha256'])
            and item['prior_sha256']!=item['target_sha256'])
        require(type(item['prior_sequence']) is int and 1<=item['prior_sequence']<9223372036854775806)
        require(type(item['target_sequence']) is int and item['target_sequence']==item['prior_sequence']+1)
        require(item['target_sha256']==sha(ops.encoded(dict(version=1,digest=record['target_digest'],
            operation=record['target_operation'],sequence=item['target_sequence']))))
        require(item['state'] in ('prepared','publication_claimed'))
        claims+=item['state']=='publication_claimed'
    require(claims==record['revision'])
    require(sha(ops.encoded({k:v for k,v in record.items() if k!='journal_sha256'}))==record['journal_sha256'])


def claim(record,host,*,expected_sha256):
    """Return a new immutable journal version, never permission to publish.

    A future adapter must persist and verify this claim before attempting one CAS.
    Repeated or stale claims fail; retain previous records for lineage readback.
    """
    try:
        validate(record)
        require(expected_sha256==record['journal_sha256'] and host in HOSTS)
        require(record['hosts'][host]['state']=='prepared')
        body=deepcopy(record); del body['journal_sha256']
        body['previous_sha256']=record['journal_sha256']; body['revision']+=1
        body['hosts'][host]['state']='publication_claimed'
        return seal(body)
    except Exception:
        raise ValueError(ERROR) from None


def verify_chain(history):
    require(type(history) in (list,tuple) and 1<=len(history)<=3)
    for index,record in enumerate(history):
        validate(record)
        require(record['revision']==index)
        if index:
            previous=history[index-1]
            changed=[host for host in HOSTS if record['hosts'][host]['state']!=previous['hosts'][host]['state']]
            require(len(changed)==1)
            require(claim(previous,changed[0],expected_sha256=previous['journal_sha256'])==record)


def assess(record,observations,*,history):
    """Each observation must bind the current journal and a pointer readback.

    None means unavailable. No branch returns a retry or consumer-enable command.
    """
    try:
        validate(record)
        verify_chain(history)
        require(history[-1]==record)
        require(type(observations) is dict and set(observations)==set(HOSTS))
        states=[]
        for host in HOSTS:
            observed=observations[host]
            if observed is None: return 'unknown_blocked_no_replay'
            require(type(observed) is dict and set(observed)=={'journal_sha256','selection_sha256'})
            require(hash_value(observed['journal_sha256']) and hash_value(observed['selection_sha256']))
            if observed['journal_sha256']!=record['journal_sha256']: return 'journal_disagreement_blocked'
            item=record['hosts'][host]; selected=observed['selection_sha256']
            if selected==item['prior_sha256']: states.append('prior')
            elif selected==item['target_sha256'] and item['state']=='publication_claimed': states.append('target')
            else: return 'selection_conflict_blocked'
        if states==['target','target']: return 'converged_requires_independent_acceptance'
        if states==['prior','prior']:
            return 'prepared_blocked' if record['revision']==0 else 'not_selected_blocked_no_replay'
        return 'partial_selection_blocked'
    except Exception:
        raise ValueError(ERROR) from None
