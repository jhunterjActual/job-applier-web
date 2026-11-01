"""Standing-policy encoding domain, exactly installed common.py493 semantics.

Plans/journals/completion/global certificates use release_transaction.canonical,
which is a different domain. Never substitute its digest for a policy digest.
"""
import hashlib
import json
import math

COMMON_493_SHA256='b1b14dc5ac8c824836d1342291de83ba747f902f7398dd9af244544face2cb44'

class PolicyEncodingRefused(RuntimeError):pass

def require(value):
    if not value:raise PolicyEncodingRefused('policy_encoding_refused')

def json_types(value):
    if isinstance(value,dict):
        require(all(isinstance(key,str) for key in value))
        for item in value.values():json_types(item)
    elif isinstance(value,list):
        for item in value:json_types(item)
    elif value is None or type(value) in (bool,int,str):return
    elif type(value) is float:require(math.isfinite(value))
    else:require(False)

def policy_canonical(value):
    require(isinstance(value,dict))
    try:
        json_types(value)
        return (json.dumps(value,sort_keys=True,separators=(',',':'),
                           ensure_ascii=True,allow_nan=False)+'\n').encode('utf-8')
    except (TypeError,ValueError,RecursionError,UnicodeError):
        raise PolicyEncodingRefused('policy_encoding_refused') from None

def policy_digest(value):
    return hashlib.sha256(policy_canonical(value)).hexdigest()
