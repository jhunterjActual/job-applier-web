"""Bounded read-only systemd properties, with typed proof of empty Exec arrays.

systemctl may omit empty Exec* arrays, even with --all. Their absence is never
interpreted as empty: each requested hook needs busctl's exact typed-empty reply.
Capture must already bound subprocess time/output and discard raw stderr. This
module never starts a process itself and never returns raw failure output.
"""
import re

HOOK_PROPERTIES = frozenset(('ExecCondition', 'ExecStartPre', 'ExecStartPost', 'ExecStop', 'ExecStopPost'))
SCALAR_PROPERTIES = frozenset(('LoadState', 'Type', 'Restart', 'RemainAfterExit',
    'ActiveState', 'SubState', 'MainPID', 'ControlPID', 'Job', 'KillMode', 'SendSIGKILL',
    'RuntimeMaxUSec', 'TimeoutStartUSec', 'TimeoutStopUSec', 'InvocationID', 'ControlGroup',
    'PrivateNetwork', 'Result', 'ExecMainStatus', 'ExecMainPID'))
MAX_REPLY = 16384
EMPTY_EXEC_ARRAY = b'a(sasbttttuii) 0\n'
SERVICE = re.compile(r'careertrellis-onedrive-bundle-(?:'
    r'(?:hydrate|publish|restore|collector|retire)-[0-9a-f]{32}(?:-0[2-9])?'
    r'|identity-[0-9a-f]{32}-(?:0[1-9]|[1-9][0-9]))\.service\Z')


class UnitPropertyRefused(ValueError):
    pass


def need(condition):
    if not condition: raise UnitPropertyRefused('typed_unit_property_refused')


def unit_object_path(unit):
    need(type(unit) is str and SERVICE.fullmatch(unit))
    escaped = ''.join(chr(byte) if 48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122
                      else '_%02x' % byte for byte in unit.encode('ascii'))
    return '/org/freedesktop/systemd1/unit/' + escaped


def read_unit_properties(unit, fields, capture):
    """Project exactly requested scalars plus individually proven empty hooks."""
    path = unit_object_path(unit)
    need(type(fields) in (tuple, list) and fields and all(type(item) is str for item in fields)
         and len(set(fields)) == len(fields) and set(fields) <= SCALAR_PROPERTIES | HOOK_PROPERTIES
         and callable(capture))
    try:
        result = {}
        scalars = [field for field in fields if field not in HOOK_PROPERTIES]
        if scalars:
            raw = capture(['/usr/bin/systemctl', 'show', unit, '--all', '--property=' + ','.join(scalars)])
            need(type(raw) is bytes and len(raw) <= MAX_REPLY and raw.endswith(b'\n') and b'\r' not in raw)
            for line in raw.decode('ascii').splitlines():
                key, separator, value = line.partition('=')
                need(separator and key in scalars and key not in result
                     and all(32 <= ord(char) <= 126 for char in value))
                result[key] = value
            need(set(result) == set(scalars))
        for field in fields:
            if field in HOOK_PROPERTIES:
                raw = capture(['/usr/bin/busctl', 'get-property', 'org.freedesktop.systemd1', path,
                    'org.freedesktop.systemd1.Service', field])
                need(type(raw) is bytes and raw == EMPTY_EXEC_ARRAY)
                result[field] = ''  # Derived from typed proof, never from omission.
        need(set(result) == set(fields))
        return {field: result[field] for field in fields}
    except Exception:
        raise UnitPropertyRefused('typed_unit_property_refused') from None
