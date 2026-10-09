"""Protected immutable packet archive and atomic selection, no activation.

No CLI or input collection. A reviewed caller must supply privately obtained
packet bytes and explicit expected selection bytes. Never overwrite an archive
or switch to a different packet as a substitute for recovery/supersession.
"""
import hashlib
import re

import admission_operations as ops
import identity_plan_packet as packet


def location():
    return ops.ROOT/'identity-selected-plan.json'


def exists():
    return location().exists() or location().is_symlink()


def archive(digest):
    ops.require(type(digest) is str and re.fullmatch('[0-9a-f]{64}',digest))
    return ops.STATE/('identity-plan-'+digest+'.json')


def legacy_absent():
    for name in ('provider-transition.json','session-v2-transition.json'):
        path=ops.ROOT/name
        ops.require(not path.exists() and not path.is_symlink())


def register(raw):
    """Store canonical private bytes once; exact retry never rewrites them."""
    try:
        canonical=packet.canonical(raw)
        ops.require(packet.validate(canonical)['version']==1)
        digest=hashlib.sha256(canonical).hexdigest()
        with ops.host_locks():
            path=archive(digest)
            if path.exists() or path.is_symlink():
                ops.require(ops.read_protected(path,limit=packet.LIMIT)==canonical)
            else: ops.write_protected(path,canonical)
        return digest
    except Exception:
        raise ops.OperationError(ops.ERROR) from None


def decode(raw, *, activation=False):
    ops.require(type(raw) is bytes and len(raw)<=1024)
    value=ops.strict_json(raw)
    ops.require(type(value) is dict and set(value)=={'version','digest','operation','sequence'})
    ops.require(type(value['version']) is int and value['version']==1)
    ops.require(type(value['sequence']) is int and 1<=value['sequence']<9223372036854775807)
    contents=ops.read_protected(archive(value['digest']),limit=packet.LIMIT)
    ops.require(hashlib.sha256(contents).hexdigest()==value['digest'])
    child=packet.validate(contents)
    # Bound lineage traversal and reject malformed/missing/cyclic ancestry.
    seen={value['digest']}
    while child['version']==2:
        parent_digest=child['predecessor']
        ops.require(parent_digest not in seen and len(seen)<32)
        seen.add(parent_digest)
        parent_raw=ops.read_protected(archive(parent_digest),limit=packet.LIMIT)
        ops.require(hashlib.sha256(parent_raw).hexdigest()==parent_digest)
        parent=packet.validate(parent_raw)
        packet.successor(parent,child)
        child=parent
    return value,packet.selected(contents,value['operation'],activation=activation)


def load(*, activation=False):
    """One pointer snapshot, validated against an immutable exact archive."""
    try:
        legacy_absent()
        raw=ops.read_protected(location(),limit=1024)
        _,pair=decode(raw,activation=activation)
        return raw,pair
    except Exception:
        raise ops.OperationError(ops.ERROR) from None


def require_current(snapshot):
    """Call under host locks immediately before a selected-plan side effect."""
    raw,_=load()
    ops.require(raw==snapshot)


def select(digest, operation, *, previous):
    """Atomic compare-and-select under host locks. None means first selection.

    A lost acknowledgement is reconciled by load(), never a blind retry. A
    repeated selection still increments sequence, preventing an A/B/A stale
    snapshot from becoming valid again. Cross-packet changes are rejected.
    """
    try:
        with ops.host_locks():
            legacy_absent()
            sequence=1
            if previous is None:
                ops.require(not exists())
            else:
                current,_=load(); ops.require(current==previous)
                old,_=decode(current)
                ops.require(old['digest']==digest)
                sequence=old['sequence']+1
            raw=ops.encoded({'version':1,'digest':digest,'operation':operation,'sequence':sequence})
            decode(raw)
            ops.write_protected(location(),raw,previous=previous)
            ops.require(load()[0]==raw)
            return raw
    except Exception:
        raise ops.OperationError(ops.ERROR) from None
