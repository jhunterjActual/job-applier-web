"""Pure protected-file validation; never creates, rotates or discloses keys."""
import base64

PREFIX = 'CAREERTRELLIS_AUTH_V2_'
FIELDS = frozenset((PREFIX+'TOTP_ENABLED', PREFIX+'TOTP_KEY', PREFIX+'REMOVAL_ENABLED', PREFIX+'RECOVERY_ENABLED'))


def validate(values, existing_keys, *, activation_schema=None):
    removal = values.get(PREFIX+'REMOVAL_ENABLED', '0')
    recovery = values.get(PREFIX+'RECOVERY_ENABLED', '0')
    if removal not in ('0', '1') or recovery not in ('0', '1'):
        raise ValueError('Invalid factor configuration.')
    flag = values.get(PREFIX+'TOTP_ENABLED', '0')
    if flag not in ('0', '1'):
        raise ValueError('Invalid enrollment configuration.')
    key = values.get(PREFIX+'TOTP_KEY')
    if key is not None:
        from cryptography.fernet import Fernet
        Fernet(key)
        raw = base64.b64decode(key, altchars=b'-_', validate=True)
        if len(raw) != 32 or raw in {base64.urlsafe_b64decode(k) for k in existing_keys}:
            raise ValueError('Invalid enrollment configuration.')
    if flag == '1' and (key is None or activation_schema not in (None, '20260915_0016', '20261005_0023')):
        raise ValueError('Enrollment activation unavailable.')
    if (removal == '1' or recovery == '1') and (flag != '1' or activation_schema != '20261005_0023'):
        raise ValueError('Recovery/removal activation unavailable.')
    return flag == '1'
