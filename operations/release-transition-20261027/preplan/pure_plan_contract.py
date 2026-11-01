"""Exact pure source excerpts; no runtime/host methods imported."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib,json,re
from policy_encoding import policy_canonical,policy_digest

BASE = '7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'

PREVIOUS = '4095417909a8b13ad252be9420e422af3662bef5'

TARGET = 'bc68ea0dc253425a44e268452130becdf35d764e'

CORRECTION = 'f70417d4bd70c3eb086ea63c90938dd2f43e0b99'

ROLES = ('web', 'db')

BUILDS = {BASE: '20261017.96', TARGET: '20261026.97'}

POLICY_KEYS = frozenset(('application_release', 'application_build'))

class Refused(RuntimeError):
    pass

def need(value):
    if not value:
        raise Refused('release_transaction_refused_inspect_no_replay')

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False, ensure_ascii=False).encode('utf-8')

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(stamp.tzinfo is not None)
    return stamp.astimezone(timezone.utc)

def validate_plan(plan, now):
    need(type(plan) is dict and set(plan) == {
        'schema','base','previous','target','correction','builds','boots',
        'old_policy_digests','new_policy_digests','packet_sha256',
        'source_diffs','prepared_receipts','prepared_receipt_kinds','expires_at','readiness_manifest_sha256'})
    need(plan['schema'] == 'careertrellis.app-policy-release/v2')
    need(plan['base'] == BASE and plan['previous'] == PREVIOUS and plan['target'] == TARGET)
    correction = plan['correction']
    need(correction == CORRECTION)
    need(plan['builds'] == {**BUILDS, correction: '20261026.98'})
    need(set(plan['boots']) == set(ROLES)
         and all(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', x)
                 for x in plan['boots'].values()))
    need(set(plan['old_policy_digests']) == set(ROLES))
    need(set(plan['new_policy_digests']) == {TARGET, correction})
    need(set(plan['source_diffs']) == {TARGET, correction})
    need(set(plan['prepared_receipts']) == {TARGET, correction})
    need(type(plan['prepared_receipt_kinds']) is dict and plan['prepared_receipt_kinds'] == {TARGET:'corrected-fixture97-new/v1',correction:'repair98-retained/v1'})
    hashes = [plan['packet_sha256'],plan['readiness_manifest_sha256'], *plan['old_policy_digests'].values(),
              *plan['source_diffs'].values(), *plan['prepared_receipts'].values()]
    for revision in (TARGET, correction):
        need(set(plan['new_policy_digests'][revision]) == set(ROLES))
        hashes.extend(plan['new_policy_digests'][revision].values())
    need(all(type(x) is str and re.fullmatch('[0-9a-f]{64}', x) and x != '0'*64 for x in hashes))
    need(now.tzinfo is not None)
    remaining=(timestamp(plan['expires_at'])-now).total_seconds()
    need(0<remaining<=3600)

def new_policy(old, revision, build, now):
    need(type(old) is dict and old.get('schema') == 'careertrellis.paired-maintenance-policy/v1')
    need(old.get('application_release') == BASE and old.get('application_build') == BUILDS[BASE])
    need(timestamp(old['valid_from']) <= now < timestamp(old['valid_until']))
    result = deepcopy(old)
    result.update(application_release=revision, application_build=build)
    need({k:v for k,v in old.items() if k not in POLICY_KEYS}
         == {k:v for k,v in result.items() if k not in POLICY_KEYS})
    return result
