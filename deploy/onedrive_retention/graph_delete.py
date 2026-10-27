"""Fixed-root conditional Graph recycle-bin gateway and protected grant factory.

Native credentials remain in memory; namespace mapping, installed bindings,
canonical lock and fresh byte/eTag witnesses are mandatory. Offline grants work
only with explicit fake transports. No refresh, retry or permanent deletion.
"""
from dataclasses import dataclass, field
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import configparser
import datetime as dt
import io
import os
from pathlib import Path
import select
import stat
import subprocess
import tempfile

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
    read_only: bool = field(default=False, repr=False)

    def __repr__(self): return '<GraphGrant redacted>'


def offline_grant(drive_id, root_id, archive_sha256, expected_etag, token='synthetic'):
    """Synthetic tests only; never accepted by the concrete HTTP transport."""
    return Grant(drive_id, root_id, token, archive_sha256, expected_etag, seal=_SEAL, offline=True)


def valid_etag(value):
    # Provider-opaque header value; wildcard never establishes byte identity.
    return (type(value) is str and 0 < len(value) <= 1024 and value.strip() != '*'
            and value == value.strip() and all(32 <= ord(c) <= 126 for c in value))


def bearer(remote,now):
    """Private in-memory token validation; no credential-bearing exception text."""
    try:
        token=json.loads(remote['token'])
        need(type(token) is dict and token.get('token_type','').lower()=='bearer'
             and type(token.get('access_token')) is str and 0<len(token['access_token'])<=16384
             and all(33<=ord(char)<=126 for char in token['access_token']))
        expiry=dt.datetime.fromisoformat(token['expiry'].replace('Z','+00:00'))
        need(expiry.utcoffset()==dt.timedelta(0) and (expiry-now).total_seconds()>TIMEOUT*3)
        return token['access_token']
    except Exception:raise Refused('native_graph_token_unavailable') from None


def refreshed_remote(original,raw,now):
    """A refresh may rotate only the token, never identity/root/provider options."""
    try:
        need(type(raw) is bytes and 0<len(raw)<=1024*1024)
        parser=configparser.ConfigParser(interpolation=None,strict=True)
        parser.read_string(raw.decode('utf-8'))
        need(not parser.defaults() and set(parser.sections())=={'onedrive'})
        remote=dict(parser['onedrive'])
        need({key:value for key,value in remote.items() if key!='token'}
             =={key:value for key,value in original.items() if key!='token'})
        bearer(remote,now);return remote
    except Exception:raise Refused('native_graph_refresh_refused') from None


def refresh_existing_remote(original,transaction,repository_lock_fd,original_config):
    """Let the pinned existing rclone identity refresh an exclusive private copy.

    The original configuration is read-only. A proven-terminal child and exact
    one-file directory census are required before removing our private copy.
    Unknown child state or rclone leftover files retain the root-private folder
    for reconciliation. Tokens and token fingerprints never enter receipts.
    """
    from . import catalog,capture_plan,hydrate
    need(original.get('type')=='onedrive' and original.get('region','global') in ('','global'))
    need(original.get('token_url','') in ('','https://login.microsoftonline.com/common/oauth2/v2.0/token')
         and original.get('auth_url','') in ('','https://login.microsoftonline.com/common/oauth2/v2.0/authorize'))
    files=catalog.NativeFiles(repository_lock_fd);files.require_repository_lock()
    source=catalog.SOURCE_ROOT/transaction
    with files.directory(source,private=True):pass
    binary,binary_identity=hydrate.protected(capture_plan.BINARY,limit=256*1024**2)
    need(hashlib_sha(binary)==capture_plan.BINARY_SHA256)
    need(hydrate.protected(capture_plan.CONFIG,0o600)==original_config)
    root=Path(tempfile.mkdtemp(prefix='careertrellis-onedrive-oauth-'+transaction+'-',dir='/run'))
    config=root/'rclone.conf';terminal=False;owned_identity=None;child=None
    try:
        meta=root.lstat();need(stat.S_ISDIR(meta.st_mode) and meta.st_uid==meta.st_gid==0
             and stat.S_IMODE(meta.st_mode)==0o700)
        owned_identity=(meta.st_dev,meta.st_ino)
        parser=configparser.ConfigParser(interpolation=None);parser['onedrive']=dict(original)
        stream=io.StringIO();parser.write(stream);raw=stream.getvalue().encode('utf-8')
        hydrate.exclusive(config,raw)
        journal=source/('oauth-refresh-'+root.name.rsplit('-',1)[1]+'-intent.json')
        hydrate.exclusive(journal,hydrate.bundle.canonical({'schema':'careertrellis.onedrive-oauth-refresh/v1',
            'private_resource':str(root),'owner_pid':os.getpid(),
            'owner_start_ticks':int(Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19]),
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'original_configuration_writes':0,'provider_identity_changed':False}))
        args=[capture_plan.BINARY,'about','onedrive:','--json','--config',str(config),
            '--log-level','ERROR','--stats','0','--retries','1','--low-level-retries','1',
            '--contimeout','15s','--timeout','45s']
        child=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,env=hydrate.ENV,pass_fds=(repository_lock_fd,))
        try:
            raw=bytearray();deadline=time.monotonic()+120
            try:
                ticks=int(Path('/proc/%d/stat'%child.pid).read_text().rsplit(') ',1)[1].split()[19])
                hydrate.exclusive(journal.with_name(journal.name.replace('-intent.json','-child.json')),
                    hydrate.bundle.canonical({'pid':child.pid,'start_ticks':ticks,
                        'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))
                while True:
                    left=deadline-time.monotonic();need(left>0)
                    if not select.select([child.stdout],[],[],min(left,1))[0]:continue
                    part=os.read(child.stdout.fileno(),min(8192,65537-len(raw)))
                    if not part:break
                    raw.extend(part);need(len(raw)<=65536)
                code=child.wait(timeout=max(.01,deadline-time.monotonic()));terminal=True;need(code==0)
                quota=json.loads(raw)
                need(type(quota) is dict and type(quota.get('total')) is int
                     and type(quota.get('free')) is int and 0<=quota['free']<=quota['total'])
            finally:
                if child.poll() is None:
                    child.kill()
                    try:child.wait(timeout=5);terminal=True
                    except subprocess.TimeoutExpired:terminal=False
                elif child.returncode is not None:terminal=True
                if terminal:
                    hydrate.exclusive(journal.with_name(journal.name.replace('-intent.json','-terminal.json')),
                        hydrate.bundle.canonical({'pid':child.pid,'exit_code':child.returncode,
                            'original_configuration_writes':0}))
        finally:child.stdout.close()
        files.require_repository_lock()
        need(hydrate.protected(capture_plan.CONFIG,0o600)==original_config)
        _binary,current_identity=hydrate.protected(capture_plan.BINARY,limit=256*1024**2)
        need(current_identity==binary_identity)
        value=refreshed_remote(original,hydrate.protected(config,0o600,limit=1024*1024)[0],
                               dt.datetime.now(dt.timezone.utc))
        need(set(os.listdir(root))=={'rclone.conf'})
        return value
    except Exception:raise Refused('native_graph_refresh_requires_reconciliation') from None
    finally:
        # Only our exact known file and empty directory; a failed save may
        # leave random rclone temp/backup names that must remain protected.
        if terminal and owned_identity is not None:
            meta=root.lstat()
            need((meta.st_dev,meta.st_ino)==owned_identity and stat.S_ISDIR(meta.st_mode)
                 and meta.st_uid==meta.st_gid==0 and stat.S_IMODE(meta.st_mode)==0o700)
            if set(os.listdir(root))=={'rclone.conf'}:
                hydrate.protected(config,0o600,limit=1024*1024)
                config.unlink();root.rmdir()
                hydrate.exclusive(journal.with_name(journal.name.replace('-intent.json','-cleanup.json')),
                    hydrate.bundle.canonical({'schema':'careertrellis.onedrive-oauth-cleanup/v1',
                        'result':'private_copy_removed','private_resource':str(root),
                        'child_terminal':True,'original_configuration_writes':0,'provider_identity_changed':False}))


def hashlib_sha(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def native_grant(transaction=None, archive_sha256=None, repository_lock_fd=None, *, verification=None,
                 operation_transaction=None):
    """Fixed protected credentials/root mapping; no refresh or caller credentials.

    Numbered verification grants are read-only. A deletion grant additionally
    requires a protected recent byte/eTag witness. The canonical lock is held.
    """
    from . import catalog, capture_plan, hydrate, entry
    need(catalog.hex_value(transaction, 32) and catalog.hex_value(archive_sha256))
    files = catalog.NativeFiles(repository_lock_fd)
    revision = entry.verify_install(); entry._native_dependencies(revision, 'db')
    if verification is not None:
        need(type(verification) is int and 1 <= verification <= 99
             and entry.identity_reverification_idle(transaction, verification) is True)
        etag = 'read-only'
    else:
        need(catalog.hex_value(operation_transaction, 32)
             and entry.retention_idle(operation_transaction, 'retire') is True)
        witness = catalog.read_identity(files,transaction)
        need(witness.get('schema') == 'careertrellis.onedrive-bundle-identity/v2'
             and witness.get('sha256') == archive_sha256
             and witness.get('identity_verified_before_after_download') is True
             and witness.get('etag_verified_before_after_download') is True
             and valid_etag(witness.get('etag')))
        age = (dt.datetime.now(dt.timezone.utc)-catalog.timestamp(witness['observed_at'])).total_seconds()
        need(0 <= age <= 900); etag = witness['etag']
    config_snapshot = hydrate.protected(capture_plan.CONFIG, 0o600)
    config_raw = config_snapshot[0]
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(config_raw.decode('utf-8'))
        need(not parser.defaults() and set(parser.sections()) == {'r2', 'onedrive'}
             and parser['onedrive'].get('type') == 'onedrive')
        remote = dict(parser['onedrive'])
        try:access_token=bearer(remote,dt.datetime.now(dt.timezone.utc))
        except Refused:
            remote=refresh_existing_remote(remote,transaction,repository_lock_fd,config_snapshot)
            access_token=bearer(remote,dt.datetime.now(dt.timezone.utc))
        drive = remote['drive_id']; configured_root = remote.get('root_folder_id', '')
        component(drive)
        if configured_root: component(configured_root)
        temporary = Grant(drive, configured_root or 'root', access_token,
                          archive_sha256, etag, seal=_SEAL, read_only=True)
        base = ORIGIN+'/drives/'+component(drive)
        root = '/items/'+component(configured_root) if configured_root else '/root'
        status, raw = HttpTransport().request('GET', base+root+':/'+NAMESPACE,
            {'Authorization':'Bearer '+temporary.token, 'Accept':'application/json'}, temporary)
        need(status == 200)
        mapped = json.loads(raw)
        need(type(mapped) is dict and type(mapped.get('folder')) is dict
             and 'remoteItem' not in mapped and mapped.get('name') == 'postgresql-bundles'
             and mapped['parentReference']['driveId'] == drive)
        component(mapped['id']); files.require_repository_lock()
        need(hydrate.protected(capture_plan.CONFIG, 0o600) == config_snapshot)
        return Grant(drive, mapped['id'], temporary.token, archive_sha256, etag,
                     seal=_SEAL, read_only=verification is not None)
    except Exception:
        raise Refused('native_graph_credentials_or_mapping_refused') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class HttpTransport:
    """Concrete HTTPS transport restricted to sealed fixed-origin grants."""
    simulation = False

    def request(self, method, url, headers, grant):
        need(type(grant) is Grant and grant.seal is _SEAL and not grant.offline)
        need(method in ('GET', 'DELETE') and url.startswith(ORIGIN+'/drives/'))
        need(method != 'DELETE' or grant.read_only is False)
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

    def metadata(self, expected_id, expected_size):
        """Bounded metadata for the exact archive path; private identifiers stay private."""
        grant = self._grant; component(expected_id)
        need(type(expected_size) is int and 0 < expected_size <= 2**50)
        path = ORIGIN+'/drives/'+component(grant.drive_id)+'/items/'+component(grant.root_id)
        path += ':/'+grant.archive_sha256+'/repository.tar'
        try:
            status, raw = self._transport.request('GET', path,
                {'Authorization':'Bearer '+grant.token, 'Accept':'application/json'}, grant)
            need(status == 200 and type(raw) is bytes and len(raw) <= MAX_RESPONSE)
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    need(key not in result); result[key] = value
                return result
            value = json.loads(raw, object_pairs_hook=unique)
            need(type(value) is dict and value['id'] == expected_id
                 and type(value['size']) is int and value['size'] == expected_size
                 and value['name'] == 'repository.tar' and type(value['file']) is dict
                 and 'folder' not in value and 'remoteItem' not in value
                 and value['parentReference']['driveId'] == grant.drive_id
                 and valid_etag(value['eTag']))
            return {'object_id': value['id'], 'size': value['size'], 'etag': value['eTag']}
        except Exception: raise Refused('metadata_binding_refused') from None

    def soft_delete(self, archive_sha256, expected_id, expected_size):
        """Revalidate scoped path then recycle SAME ID with its fresh If-Match eTag.

        204 means Graph accepted soft deletion, not physical reclamation or a
        retirement receipt. Any unknown DELETE response requires reconciliation.
        """
        need(self._grant.read_only is False)
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
