"""Read and bind an explicitly accepted baseline; never create or infer one.

Pure validation and synthetic byte fixtures are unauthenticated. Only the fixed,
protected installed factory may construct an authenticated context. That context
does not implement an operational adapter or authorize dispatch by itself.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket

from . import coordinator

ROOT = Path('/usr/local/libexec/careertrellis/onedrive_retention')
BASELINE = Path('/etc/careertrellis/onedrive-retention/accepted-baseline.json')
SCHEMA = 'careertrellis.onedrive-retention-accepted-baseline/v1'
MAX_BYTES = 65536
HEX = re.compile('[0-9a-f]{64}\\Z')
REVISION = re.compile('[0-9a-f]{40}\\Z')
_NATIVE_SEAL = object()


class BaselineRefused(ValueError):
    pass


def need(value):
    if not value: raise BaselineRefused('accepted_retention_baseline_unavailable')


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def decode(raw):
    need(type(raw) is bytes and len(raw) <= MAX_BYTES)
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result); result[key] = value
        return result
    try:
        value = json.loads(raw.decode('ascii'), object_pairs_hook=pairs,
                           parse_constant=lambda _: need(False))
        need(canonical(value) == raw)
        return value
    except (ValueError, TypeError, UnicodeError):
        raise BaselineRefused('accepted_retention_baseline_unavailable') from None


def timestamp(value):
    need(type(value) is str and len(value) <= 64)
    try: result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError: raise BaselineRefused('accepted_retention_baseline_unavailable') from None
    need(result.utcoffset() == dt.timedelta(0))
    return result


def digest(raw): return hashlib.sha256(raw).hexdigest()


def manifest(raw, schema):
    value = decode(raw)
    need(type(value) is dict and set(value) == {'schema', 'revision', 'files'}
         and value['schema'] == schema and type(value['revision']) is str
         and REVISION.fullmatch(value['revision']) and value['revision'] != '0'*40
         and type(value['files']) is dict and 0 < len(value['files']) <= 64)
    for name, sha in value['files'].items():
        need(type(name) is str and re.fullmatch('[A-Za-z_][A-Za-z0-9_]*\\.py', name)
             and type(sha) is str and HEX.fullmatch(sha))
    return value


class ValidatedFixture:
    __slots__ = ('_document', '_bindings')
    def __init__(self, document, bindings):
        self._document, self._bindings = canonical(document), canonical(bindings)
    @property
    def document(self): return json.loads(self._document)
    @property
    def bindings(self): return json.loads(self._bindings)
    @property
    def authenticated(self): return False
    @property
    def native_actions(self): return False


class AuthenticatedContext:
    __slots__ = ('_fixture', '_role', '_seal')
    def __init__(self, fixture, role, seal=None):
        need(seal is _NATIVE_SEAL and type(fixture) is ValidatedFixture and role in ('web', 'db'))
        self._fixture, self._role, self._seal = fixture, role, seal
    @property
    def authenticated(self): return self._seal is _NATIVE_SEAL
    @property
    def document(self): return self._fixture.document
    @property
    def bindings(self): return self._fixture.bindings
    @property
    def role(self): return self._role
    @property
    def revision(self): return self.document['standalone_revision']


def validate(document, *, standalone_manifest, paired_manifest, standalone_policy,
             standalone_public_policy, paired_policy, paired_public_policy, now):
    """Validate supplied canonical bytes; result is always unauthenticated."""
    try:
        need(type(document) is dict and set(document) == {'schema', 'standalone_revision',
            'standalone_manifest_sha256', 'paired_revision', 'paired_manifest_sha256',
            'paired_policy_sha256', 'application_release', 'accepted_at', 'valid_until', 'acceptance_receipts'})
        need(document['schema'] == SCHEMA)
        for key in ('standalone_revision', 'paired_revision', 'application_release'):
            need(type(document[key]) is str and REVISION.fullmatch(document[key]) and document[key] != '0'*40)
        for key in ('standalone_manifest_sha256', 'paired_manifest_sha256', 'paired_policy_sha256'):
            need(type(document[key]) is str and HEX.fullmatch(document[key]))
        refs = document['acceptance_receipts']
        need(type(refs) is dict and set(refs) == {'ops', 'native_controls', 'independent_review'})
        need(all(type(value) is str and HEX.fullmatch(value) and value != '0'*64 for value in refs.values()))
        standalone = manifest(standalone_manifest, 'careertrellis.onedrive-retention-install/v1')
        paired = manifest(paired_manifest, 'careertrellis.paired-maintenance-install/v1')
        need(standalone_policy == standalone_public_policy and paired_policy == paired_public_policy)
        policy = coordinator.Policy(decode(standalone_policy)); policy.require_active(now)
        paired_document = decode(paired_policy)
        need(type(paired_document) is dict
             and paired_document.get('schema') == 'careertrellis.paired-maintenance-policy/v1'
             and paired_document.get('enabled') is True)
        need(document['standalone_revision'] == standalone['revision'] == policy.data['artifact_revision']
             and document['paired_revision'] == paired['revision'] == paired_document.get('manifest_revision')
             and document['application_release'] == policy.data['application_release'] == paired_document.get('application_release')
             and document['standalone_manifest_sha256'] == digest(standalone_manifest)
             and document['paired_manifest_sha256'] == digest(paired_manifest)
             and document['paired_policy_sha256'] == digest(paired_policy) == policy.data['paired_policy_sha256'])
        accepted, expiry = timestamp(document['accepted_at']), timestamp(document['valid_until'])
        need(type(now) is dt.datetime and now.utcoffset() == dt.timedelta(0)
             and accepted <= now < expiry <= timestamp(policy.data['valid_until']) <= coordinator.EXPIRY)
        bindings = {'standalone_manifest_sha256': digest(standalone_manifest),
            'paired_manifest_sha256': digest(paired_manifest), 'paired_policy_sha256': digest(paired_policy),
            'standalone_policy_sha256': digest(standalone_policy)}
        return ValidatedFixture(document, bindings)
    except Exception:
        raise BaselineRefused('accepted_retention_baseline_unavailable') from None


def native_context():
    """Only fixed protected installed sources/policies may authenticate context."""
    try:
        need(Path(__file__).absolute() == ROOT / 'baseline.py' and os.name == 'posix'
             and os.geteuid() == os.getegid() == 0)
        role = {'careertrellis-staging-web-1':'web', 'job-applier-db-staging-01':'db'}.get(socket.gethostname())
        need(role in ('web', 'db'))
        from . import entry
        need(Path(entry.__file__).absolute() == ROOT / 'entry.py')
        revision = entry.verify_install()
        standalone_raw, standalone = entry._protected_document(ROOT / 'manifest.json', 0o644)
        need(standalone['revision'] == revision)
        with entry._PairedPackage() as reader:
            paired = entry.verify_package(reader, entry.PAIRED_FILES, 'careertrellis.paired-maintenance-install/v1')
            paired_raw = entry.read_source(reader, 'manifest.json', entry.MAX_MANIFEST)
            need(entry.decode(paired_raw, entry.MAX_MANIFEST) == paired)
        private, _ = entry._protected_document(Path('/etc/careertrellis/onedrive-retention/policy.json'), 0o600)
        public, _ = entry._protected_document(Path('/var/lib/careertrellis/onedrive-retention-public/policy.json'), 0o644)
        paired_private, _ = entry._protected_document(Path('/etc/careertrellis/paired-maintenance/policy.json'), 0o600)
        paired_public, _ = entry._protected_document(Path('/var/lib/careertrellis/paired-maintenance/public-policy.json'), 0o644)
        _, accepted = entry._protected_document(BASELINE, 0o600)
        fixture = validate(accepted, standalone_manifest=standalone_raw, paired_manifest=paired_raw,
            standalone_policy=private, standalone_public_policy=public,
            paired_policy=paired_private, paired_public_policy=paired_public, now=dt.datetime.now(dt.timezone.utc))
        return AuthenticatedContext(fixture, role, _NATIVE_SEAL)
    except Exception:
        raise BaselineRefused('accepted_retention_baseline_unavailable') from None
