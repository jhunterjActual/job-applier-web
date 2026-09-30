"""Pure protected-file validation; never creates, rotates or discloses keys."""
import base64

PREFIX = 'CAREERTRELLIS_AUTH_V2_'
FIELDS = frozenset((PREFIX+'TOTP_ENABLED', PREFIX+'TOTP_KEY', PREFIX+'REMOVAL_ENABLED'))


def validate(values, existing_keys, *, activation_schema=None):
    # Initial schema21 rollout keeps removal unavailable. Enabling it requires
    # a separately reviewed protected configuration/attended acceptance path.
    if values.get(PREFIX+'REMOVAL_ENABLED', '0') != '0':
        raise ValueError('Removal activation unavailable.')
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
    if flag == '1' and (key is None or activation_schema not in (None, '20260915_0016')):
        raise ValueError('Enrollment activation unavailable.')
    return flag == '1'
