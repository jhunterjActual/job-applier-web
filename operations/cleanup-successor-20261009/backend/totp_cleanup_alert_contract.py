"""Strict enrollment scrub observation; fixed classifications only, no delivery."""
import json
import re

import admission_alert_contract as admission
import totp_cleanup_operations as cleanup

UNAVAILABLE = ('observation_unavailable',)


def classify(returncode, output, source, now):
    try:
        if (type(returncode) is not int or returncode not in (0, 1)
            or not isinstance(output, bytes) or not 0 < len(output) <= 4096
            or not isinstance(source, str) or not re.fullmatch('[0-9a-f]{40}', source)
            or type(now) is not int or now < 0):
            return UNAVAILABLE
        value = json.loads(output.decode('utf-8'), object_pairs_hook=admission.unique_object)
        cleanup.validate_health(value, source, now, max_age=cleanup.MAX_AGE)
        if returncode != bool(value['issues']):
            return UNAVAILABLE
        return tuple(sorted(value['issues']))
    except Exception:
        return UNAVAILABLE


def combined(admission_result, cleanup_result):
    """Version2 reporter requires independently fresh observations of both lanes."""
    for result, allowed in ((admission_result, admission.ISSUES_V2), (cleanup_result, cleanup.ISSUES)):
        if (not isinstance(result, tuple) or len(set(result)) != len(result)
            or any(not isinstance(x, str) or x not in allowed | set(UNAVAILABLE) for x in result)):
            raise ValueError('Invalid cleanup classification')
    return tuple('admission:' + x for x in admission_result) + tuple('totp:' + x for x in cleanup_result)


def message(classification):
    allowed = {'admission:' + x for x in admission.ISSUES_V2 | set(UNAVAILABLE)} | {
        'totp:' + x for x in cleanup.ISSUES | set(UNAVAILABLE)}
    if (not isinstance(classification, tuple) or any(not isinstance(x, str) or x not in allowed for x in classification)
        or len(set(classification)) != len(classification)):
        raise ValueError('Invalid cleanup classification')
    return ('CareerTrellis staging: admission and authenticator cleanup need attention. Reasons: '
        + ', '.join(classification) + '. Inspect protected status before taking action.') if classification else None
