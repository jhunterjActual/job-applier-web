"""Exact previously accepted pair, not a substitute for current health checks.

Pure validation only: no filesystem, configuration, provider, or database access.
Hashes below identify reviewed non-secret acceptance receipts, never policies.
"""
from datetime import datetime, timezone
import hashlib
import json

BASE = '7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
TARGET = 'bc68ea0dc253425a44e268452130becdf35d764e'
CORRECTION = 'f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
LABEL = '20261026-155056F'
WEB_DIGEST = '19743124faf4864a8e5efdde5b9f416cc77e0070967dc6f2c487807a07262f09'
DB_DIGEST = '42fec6f6ff56c4c2c4021d717458dc620542cd68e88b06ac32cd4b7762d84f98'
FULL = '0bf45e020bbac0bbdc05f473549a3feea4091f466ee7da906e8ef7cf94fdb0c3'
MANIFEST = 'd346b4727d7443098ce0d4f1efe8e8fa96165b0191b8c1681ff20bdb9794aa20'
METADATA = '9f01ad709f033e9eec5cd765a2276076a982e594a155824173d3acc746cbe78b'
TRANSACTION = '45a42a288e304da8a25e99dbbbd62258'
PLAN = '89ba6b17005ef701a896dd492bf61347b2fad40f5e6c67044da622492ce6a5a5'


class PairRefused(RuntimeError):
    pass


def need(value):
    if not value:
        raise PairRefused('accepted_pair_refused')


def timestamp(value):
    need(type(value) is str)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        raise PairRefused('accepted_pair_refused') from None
    need(parsed.tzinfo is not None)
    return parsed


def unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        need(key not in out)
        out[key] = value
    return out


def decode(raw, expected):
    need(type(raw) is bytes and 0 < len(raw) < 65536)
    need(hashlib.sha256(raw).hexdigest() == expected)
    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(PairRefused('accepted_pair_refused')))
    except (ValueError, UnicodeError):
        raise PairRefused('accepted_pair_refused') from None
    need(type(value) is dict)
    return value


def validate_pair(webraw, dbraw, context, *, now=None):
    """Return safe acceptance binding; all current observations require own proof.

    Caller must authenticate original remote protected bytes and fresh host identities
    before supplying context. This function cannot
turn an untrusted wrapper or a self-reported OK result into live evidence.
"""
    need(type(context) is dict and set(context) == {
        'baseline', 'target', 'correction', 'accepted_backup_label', 'checked_at'})
    need(context['baseline'] == BASE and context['target'] == TARGET
         and context['correction'] == CORRECTION and context['accepted_backup_label'] == LABEL)
    now = now or datetime.now(timezone.utc)
    checked = timestamp(context['checked_at'])
    need(0 <= (now - checked).total_seconds() <= 600)
    web = decode(webraw, WEB_DIGEST)
    db = decode(dbraw, DB_DIGEST)
    need(set(web) == {'schema', 'result', 'backup_label', 'release', 'started_at', 'completed_at',
         'db_checksums_verified', 'resource_cleanup_verified', 'divergence_count',
         'every_object_authenticated', 'full_receipt_sha256', 'independent_escrow_operator_confirmed',
         'manifest_sha256', 'metadata_sha256', 'object_count', 'plaintext_bytes',
         'plaintext_size_sha256_verified'})
    need(web['schema'] == 'careertrellis.paired-plaintext-acceptance/v1'
         and web['result'] == 'verified' and web['release'] == BASE and web['backup_label'] == LABEL)
    need(all(web[k] is True for k in ('every_object_authenticated',
         'independent_escrow_operator_confirmed', 'plaintext_size_sha256_verified')))
    need(web['db_checksums_verified'] is False and web['resource_cleanup_verified'] is False)
    need(type(web['divergence_count']) is int and web['divergence_count'] == 0)
    need(type(web['object_count']) is int and web['object_count'] == 28)
    need(type(web['plaintext_bytes']) is int and web['plaintext_bytes'] == 792798)
    need(web['full_receipt_sha256'] == FULL and web['manifest_sha256'] == MANIFEST
         and web['metadata_sha256'] == METADATA)
    need(set(db) == {'schema', 'result', 'backup_label', 'checksums_verified', 'cleanup_verified',
                     'completed_at', 'plan_sha256', 'transaction'})
    need(db['schema'] == 'careertrellis.retained-restore-cleanup/v1'
         and db['result'] == 'verified' and db['backup_label'] == LABEL)
    need(db['checksums_verified'] is True and db['cleanup_verified'] is True
         and db['plan_sha256'] == PLAN and db['transaction'] == TRANSACTION)
    need(timestamp(web['started_at']) <= timestamp(web['completed_at'])
         <= timestamp(db['completed_at']) <= checked <= now)
    return {'schema': 'careertrellis.reviewed-pair-binding/v1', 'backup_label': LABEL,
            'baseline': BASE, 'target': TARGET, 'correction': CORRECTION,
            'historical_pair_verified': True, 'current_health_verified': False,
            'web_receipt_sha256': WEB_DIGEST, 'db_receipt_sha256': DB_DIGEST}
