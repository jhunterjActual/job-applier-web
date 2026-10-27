"""Unprivileged forced-command gate; no caller-selected command or environment."""
import subprocess

from .client import COMMAND, RESPONSE_SCHEMA, decode, response, request_timeout
from .common import canonical
from .db_control import validate_request

ROOT_COMMAND = ['/usr/bin/sudo', '-n', '--', '/usr/bin/python3', '-I', '-B',
                '/usr/local/libexec/careertrellis/paired_maintenance/entry.py', 'db-endpoint']


def run(env, raw, runner=None):
    request = {'transaction': '0' * 32, 'policy_sha256': '0' * 64}
    try:
        if env.get('SSH_ORIGINAL_COMMAND') != COMMAND or 'SSH_TTY' in env:
            raise ValueError()
        candidate = decode(raw, 16384)
        validate_request(candidate)
        request = candidate
        completed = (runner or subprocess.run)(list(ROOT_COMMAND), input=canonical(request),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=request_timeout(request['operation'],endpoint=True,request=request), check=False,
                    env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        if completed.returncode != 0:
            raise ValueError()
        return canonical(response(decode(completed.stdout, 65536), request))
    except Exception:
        return canonical({'schema': RESPONSE_SCHEMA, 'transaction': request['transaction'],
                          'policy_sha256': request['policy_sha256'],
                          'payload': {'state': 'unknown', 'operator_required': True}})
