"""Provider-neutral external observation contract; no network or delivery side effects.

Transport must authenticate the host and execute the exact approved status helper.
Only validated classifications may reach an alert provider, never command output.
"""
import json
import re


SCHEMA = 'careertrellis.admission-purge/v1'
SCHEMA_V2 = 'careertrellis.admission-purge/v2'
ISSUES = frozenset(('two_hourly_runs_missed', 'expired_backlog_remaining',
    'last_run_failed', 'run_incomplete', 'state_unavailable',
    'purge_timer_unavailable', 'purge-monitor_timer_unavailable',
    'monitor_evidence_unavailable'))
UNAVAILABLE = ('observation_unavailable',)
ISSUES_V2 = ISSUES | {'source_unverified'}


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate status key')
        value[key] = item
    return value


def classify(returncode, output, source, now, *, version=1):
    """Empty tuple means fresh healthy status; every uncertainty is actionable.

    A transport timeout/failure is represented by returncode=None and output=b''.
    This checks supplied status claims, not host authenticity or actual delivery.
    """
    try:
        if type(version) is not int or version not in (1, 2):
            return UNAVAILABLE
        if (type(returncode) is not int or returncode not in (0, 1)
            or not isinstance(output, bytes) or not 0 < len(output) <= 4096
            or not isinstance(source, str) or not re.fullmatch('[0-9a-f]{40}', source)
            or type(now) is not int or now < 0):
            return UNAVAILABLE
        value = json.loads(output.decode('utf-8'), object_pairs_hook=unique_object)
        if not isinstance(value, dict) or set(value) != {'schema','source','checked_at','result','issues'}:
            return UNAVAILABLE
        checked, issues = value['checked_at'], value['issues']
        if (value['schema'] != (SCHEMA_V2 if version == 2 else SCHEMA) or value['source'] != source
            or type(checked) is not int or not 0 <= now-checked <= 420
            or checked < 0 or not isinstance(issues, list)
            or not all(isinstance(item, str) and item in (ISSUES_V2 if version == 2 else ISSUES) for item in issues)
            or len(set(issues)) != len(issues)):
            return UNAVAILABLE
        if value['result'] != ('alert' if issues else 'ok') or returncode != bool(issues):
            return UNAVAILABLE
        return tuple(sorted(issues))
    except (ValueError, UnicodeError, TypeError, RecursionError):
        return UNAVAILABLE


def message(classification, *, recovery=False):
    """Fixed operator-facing content; adapters supply recipient and incident ID.

    Call recovery only after a previously delivered alert and a fresh healthy
    observation. Provider acknowledgement is not recipient acknowledgement.
    """
    if (not isinstance(classification, tuple)
        or any(not isinstance(item, str) or item not in ISSUES | set(UNAVAILABLE) for item in classification)
        or len(set(classification)) != len(classification)
        or type(recovery) is not bool or recovery and classification):
        raise ValueError('Invalid alert classification')
    if not classification:
        return ('CareerTrellis staging: admission cleanup recovered. '
            'Fresh status reports healthy; verify the protected receipt.') if recovery else None
    if classification == UNAVAILABLE:
        return ('CareerTrellis staging: admission cleanup observation unavailable. '
            'Check observer connectivity and the protected status helper; cleanup health is unknown.')
    return ('CareerTrellis staging: admission cleanup needs attention. '
        'Reasons: '+', '.join(classification)+'. Inspect protected status before taking action.')
