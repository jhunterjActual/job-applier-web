"""Bounded conditional Graph recycle-bin deletion gateway; no native grant issuer.

The future installed adapter must authenticate the fixed namespace/root mapping,
credentials and operation fences before issuing a grant. This module deliberately
cannot issue native grants. Offline grants work only with explicit fake transports.
No refresh, retry, permanent deletion, acceptance or catalog mutation exists here.
"""
from dataclasses import dataclass, field
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

ORIGIN = 'https://graph.microsoft.com/v1.0'
NAMESPACE = 'job-applier-staging/postgresql-bundles'
MAX_RESPONSE = 65536
TIMEOUT = 10
_SEAL = object()


class Refused(ValueError): pass
class UnknownOutcome(Refused): pass


def need(value):
    if not value: raise Refused('graph_operation_refused')


def component(value):
    need(type(value) is str and 0 < len(value) <= 512
         and not any(ord(c) < 32 or ord(c) == 127 for c in value))
    return urllib.parse.quote(value, safe='')


@dataclass(frozen=True, repr=False)
class Grant:
    drive_id: str = field(repr=False)
    root_id: str = field(repr=False)
    token: str = field(repr=False)
    archive_sha256: str = field(repr=False)
    expected_etag: str = field(repr=False)
    namespace: str = field(default=NAMESPACE, repr=False)
    seal: object = field(default=None, repr=False)
    offline: bool = field(default=False, repr=False)

    def __repr__(self): return '<GraphGrant redacted>'


def offline_grant(drive_id, root_id, archive_sha256, expected_etag, token='synthetic'):
    """Synthetic tests only; never accepted by the concrete HTTP transport."""
    return Grant(drive_id, root_id, token, archive_sha256, expected_etag, seal=_SEAL, offline=True)


def valid_etag(value):
    # Provider-opaque header value; wildcard never establishes byte identity.
    return (type(value) is str and 0 < len(value) <= 1024 and value.strip() != '*'
            and value == value.strip() and all(32 <= ord(c) <= 126 for c in value))


def native_grant(*args, **kwargs):
    raise Refused('native_adapter_not_installed')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class HttpTransport:
    """Concrete HTTPS transport, usable only after a future native grant issuer."""
    simulation = False

    def request(self, method, url, headers, grant):
        need(type(grant) is Grant and grant.seal is _SEAL and not grant.offline)
        need(method in ('GET', 'DELETE') and url.startswith(ORIGIN+'/drives/'))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        request = urllib.request.Request(url, headers=headers, method=method)
        deadline = time.monotonic()+TIMEOUT
        try:
            response = opener.open(request, timeout=TIMEOUT)
        except urllib.error.HTTPError as error:
            # Do not read or expose provider error bodies or URLs.
            status = error.code; error.close(); return status, b''
        except Exception:
            raise UnknownOutcome('graph_response_unknown') from None
        try:
            raw = bytearray()
            while True:
                if time.monotonic() >= deadline: raise UnknownOutcome('graph_response_unknown')
                part = response.read1(min(8192, MAX_RESPONSE+1-len(raw)))
                if not part: break
                raw.extend(part)
                if len(raw) > MAX_RESPONSE: raise UnknownOutcome('graph_response_unknown')
            return response.status, bytes(raw)
        except Exception:
            raise UnknownOutcome('graph_response_unknown') from None
        finally: response.close()


class Client:
    def __init__(self, grant=None, transport=None):
        need(type(grant) is Grant and grant.seal is _SEAL and grant.namespace == NAMESPACE)
        component(grant.drive_id); component(grant.root_id)
        need(type(grant.archive_sha256) is str
             and re.fullmatch('[0-9a-f]{64}', grant.archive_sha256)
             and valid_etag(grant.expected_etag))
        need(type(grant.token) is str and 0 < len(grant.token) <= 16384
             and all(33 <= ord(c) <= 126 for c in grant.token))
        if transport is None:
            need(not grant.offline); transport = HttpTransport()
        if grant.offline: need(getattr(transport, 'simulation', False) is True)
        else: need(type(transport) is HttpTransport)
        self._grant = grant; self._transport = transport

    def __repr__(self): return '<GraphDeleteClient redacted>'

    def soft_delete(self, archive_sha256, expected_id, expected_size):
        """Revalidate scoped path then recycle SAME ID with its fresh If-Match eTag.

        204 means Graph accepted soft deletion, not physical reclamation or a
        retirement receipt. Any unknown DELETE response requires reconciliation.
        """
        need(type(archive_sha256) is str and re.fullmatch('[0-9a-f]{64}', archive_sha256))
        component(expected_id)
        need(type(expected_size) is int and 0 < expected_size <= 2**50)
        grant = self._grant
        need(archive_sha256 == grant.archive_sha256)
        base = ORIGIN+'/drives/'+component(grant.drive_id)+'/items/'
        path = base+component(grant.root_id)+':/'+archive_sha256+'/repository.tar'
        headers = {'Authorization': 'Bearer '+grant.token, 'Accept': 'application/json'}
        try: status, raw = self._transport.request('GET', path, headers, grant)
        except Exception: raise Refused('metadata_unavailable') from None
        need(status == 200 and type(raw) is bytes and len(raw) <= MAX_RESPONSE)
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result: raise ValueError()
                    result[key] = value
                return result
            value = json.loads(raw, object_pairs_hook=unique)
            need(type(value) is dict and value['id'] == expected_id
                 and type(value['size']) is int and value['size'] == expected_size
                 and value['name'] == 'repository.tar' and type(value['file']) is dict
                 and 'folder' not in value and 'remoteItem' not in value
                 and value['parentReference']['driveId'] == grant.drive_id)
            etag = value['eTag']
            need(valid_etag(etag))
        except Exception: raise Refused('metadata_binding_refused') from None
        if etag != grant.expected_etag: return 'changed_not_deleted'
        conditional = dict(headers, **{'If-Match': etag})
        try: status, raw = self._transport.request('DELETE', base+component(expected_id), conditional, grant)
        except Exception: raise UnknownOutcome('delete_outcome_unknown') from None
        if status == 204 and raw == b'': return 'soft_delete_accepted'
        if status == 412: return 'changed_not_deleted'
        if status in (401, 403): raise Refused('delete_access_refused')
        raise UnknownOutcome('delete_outcome_unknown')
