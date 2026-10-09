"""Local exact two-pair validation; no I/O, credentials, selection or activation.

Callers must keep inputs and return values private. Validation authenticates
neither operator authority nor live state. Installed tools do not consume this
packet yet; do not split it into live fixed-path files with an ad hoc script.
"""
import admission_operations as ops
import re
import identity_configuration as configuration
import session_v2_transition as transition

LIMIT = 65536


def pair(value):
    ops.require(type(value) is dict and set(value)=={'provider','activation'})
    p=configuration.validate(ops.encoded(value['provider']))
    a=transition.validate_plan(ops.encoded(value['activation']))
    for key in ('operation','application','operations','owner_proof'):
        ops.require(p[key]==a[key])
    ops.require(p['target_binding']==a['provider_binding'] and a['cap']==25 and a['ttl']==600)
    return p,a


def validate(raw):
    """Validate a complete initial B/P packet; reject partial or mixed pairs.

    The generation projection assumes one initial prepare for each P transition,
    with no resumes or intervening mutations. Recovery must not repurpose this
    initial packet: a separate reviewed supersession protocol is still required.
    """
    try:
        ops.require(type(raw) is bytes and len(raw)<=LIMIT)
        value=ops.strict_json(raw)
        ops.require(type(value) is dict and type(value.get('version')) is int and value['version'] in (1,2))
        ops.require(set(value)==({'version','bind','switch'} if value['version']==1 else {'version','bind','switch','predecessor'}))
        if value['version']==2:
            ops.require(type(value['predecessor']) is str and re.fullmatch('[0-9a-f]{64}',value['predecessor']))
        b,ba=pair(value['bind']); p,pa=pair(value['switch'])
        ops.require(ba['schema']==pa['schema'])
        ops.require(b['mode']=='bind_current' and p['mode']=='switch_environment')
        ops.require(b['operation']!=p['operation'])
        ops.require(not {b['operation'],p['operation']}.intersection(p['transitions']))
        for key in ('application','operations','baseline_binding'):
            ops.require(b[key]==p[key])
        ops.require(b['target_binding']==p['baseline_binding'])
        if value['version']==1:
            ops.require(b['source_application']==p['source_application'])
            ops.require(p['expected_generation']==b['expected_generation']+1+len(p['transitions']))
        else:
            ops.require(p['source_application']==p['application'])
        # Quarantined accounts still count against the approved beta cap.
        ops.require(len(p['transitions'])+len(p['quarantined_accounts'])<=25)
        # Reserve the provider increment, activation and changed-subject trigger
        # for every mapping, then arm. No wraparound or generation reset.
        ops.require(p['expected_generation']+2+2*len(p['transitions'])<=9223372036854775807)
        return value
    except Exception:
        raise ops.OperationError(ops.ERROR) from None


def canonical(raw):
    """Return deterministic private packet bytes, never an authority receipt."""
    return ops.encoded(validate(raw))


def successor(previous, value):
    """Structural lineage only; the supersession caller proves database state."""
    ops.require(value['version']==2 and previous['bind']==value['bind'])
    ops.require(previous['switch']['activation']==value['switch']['activation'])
    old,new=previous['switch']['provider'],value['switch']['provider']
    for key in set(old)-{'source_application','expected_generation','transitions'}:
        ops.require(old[key]==new[key])
    ops.require(new['expected_generation']>old['expected_generation']
        and len(new['transitions'])==len(old['transitions']) and new['transitions']!=old['transitions'])


def selected(raw, operation, *, activation=False):
    """Pure snapshot selection; never writes installed plans or pointers.

    B is handoff-only. This guard is preparatory and does not replace checks in
    the eventual installed selector and every activation consumer.
    """
    value=validate(raw)
    ops.require(type(operation) is str and type(activation) is bool)
    for name in ('bind','switch'):
        item=value[name]
        if item['provider']['operation']==operation:
            ops.require(not activation or name=='switch')
            return item
    raise ops.OperationError(ops.ERROR)
