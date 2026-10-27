"""Fixed-host, single-attempt transport for the paired DB protocol."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from .common import Refused, canonical
from .db_control import SCHEMA, validate_request

RESPONSE_SCHEMA = 'careertrellis.paired-maintenance-response/v1'
KEY = '/etc/careertrellis/paired-maintenance/id_ed25519'
KNOWN_HOSTS = '/etc/careertrellis/paired-maintenance/known_hosts'
COMMAND = 'careertrellis-paired-v1'
ACTIVATION_WAIT_OPERATIONS=frozenset(('retention_activation_prepare',
    'retention_activation_commit','retention_activation_material'))


def pre_growth_capacity_transport_ready(request):
    """Implementation capability for this exact closed request, not activation."""
    try:
        validate_request(request)
        return (request['operation']=='retention_capacity_snapshot'
            and request['payload']['stage']=='retire_existing_before_growth') is True
    except Exception:return False


def request_timeout(operation,*,endpoint=False,request=None):
    """Fixed attended activation wait; payloads cannot extend any deadline.

    The actor's bounded110s receipt wait follows its bounded30s start. Allow
    overhead at the root gate and a further15s at the SSH client. Only the
    validated pre-growth capacity request gets210/225s around its shared180s
    provider deadline. Other requests retain35s. No timeout permits redispatch.
    """
    if request is not None and operation==request.get('operation') and pre_growth_capacity_transport_ready(request):
        return 210 if endpoint else 225
    return (180 if endpoint else 195) if operation in ACTIVATION_WAIT_OPERATIONS else 35


def decode(raw, maximum):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise Refused('protocol_refused')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict:
            raise ValueError()
        canonical(value)
        return value
    except (ValueError, UnicodeError, RecursionError, Refused):
        raise Refused('protocol_refused') from None


def response(value, request):
    if (type(value) is not dict or set(value) != {'schema', 'transaction', 'policy_sha256', 'payload'}
            or value['schema'] != RESPONSE_SCHEMA
            or value['transaction'] != request['transaction']
            or value['policy_sha256'] != request['policy_sha256']
            or type(value['payload']) is not dict or len(canonical(value)) > 65536):
        raise Refused('protocol_refused')
    return value


def _trusted_file(path, modes):
    target = Path(path)
    for parent in target.parents:
        info = os.lstat(parent)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Refused('peer_unavailable')
    info = os.lstat(target)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) not in modes):
        raise Refused('peer_unavailable')


class Client:
    def __init__(self, policy, transaction):
        if not isinstance(transaction, str) or re.fullmatch('[0-9a-f]{32}', transaction) is None:
            raise Refused('protocol_refused')
        self.policy, self.transaction = policy, transaction

    def call(self, operation, payload):
        request = {'schema': SCHEMA, 'transaction': self.transaction,
                   'policy_sha256': self.policy.digest, 'operation': operation, 'payload': payload}
        try:
            validate_request(request)
        except Exception:
            raise Refused('protocol_refused') from None
        try:
            _trusted_file(KEY, {0o600})
            _trusted_file(KNOWN_HOSTS, {0o600, 0o644})
            args = ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-b', '10.70.0.1',
                    '-i', KEY, '-o', 'UserKnownHostsFile=' + KNOWN_HOSTS]
            for option in ('BatchMode=yes', 'ConnectTimeout=15', 'ForwardAgent=no',
                           'ClearAllForwardings=yes', 'StrictHostKeyChecking=yes',
                           'IdentitiesOnly=yes', 'PasswordAuthentication=no',
                           'KbdInteractiveAuthentication=no', 'GlobalKnownHostsFile=/dev/null',
                           'UpdateHostKeys=no', 'HostKeyAlias=careertrellis-paired-db'):
                args.extend(['-o', option])
            args.extend(['careertrellis-backup-control@10.70.0.2', COMMAND])
            completed = subprocess.run(args, input=canonical(request), stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, timeout=request_timeout(operation,request=request), check=False,
                                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
            if completed.returncode != 0:
                raise Refused('peer_unavailable')
        except (OSError, subprocess.SubprocessError):
            # A lost acknowledgement never permits a second remote dispatch.
            raise Refused('peer_unavailable') from None
        return response(decode(completed.stdout, 65536), request)['payload']
