"""Bounded, owner-private search explanations; exported only on user request."""
import re

MAX_REJECTIONS_PER_STAGE = 50
REASONS = frozenset({'query_mismatch', 'eligibility_unknown', 'location_mismatch', 'stale',
                     'format_drift', 'access_challenge', 'embedded_content', 'provider_error'})
STAGES = ('discovery', 'inspection')
REASON_LABELS = {
    'query_mismatch': 'Posting title did not match the effective role filter.',
    'eligibility_unknown': 'Posting details did not establish a match to the requested location or work arrangement.',
    'location_mismatch': 'Discovery metadata did not match the requested location or work arrangement.',
    'stale': 'Posting was no longer available.',
    'format_drift': 'Posting could not be read in a supported format.',
    'access_challenge': 'Source blocked automated reading.',
    'embedded_content': 'Posting content could not be read automatically.',
    'provider_error': 'Source could not be reached or returned unusable details.',
}


def bounded_text(value, limit=180):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'https?://\S+|www\.\S+|\S+@\S+', '[redacted]', value[:2000])
    return ' '.join(''.join(c for c in value if c.isprintable() or c.isspace()).split())[:limit]


def role_filter(titles, mode):
    from discovery_sources import MAX_KEYWORDS
    from role_queries import MAX_TITLE_ALTERNATIVES
    maximum = MAX_KEYWORDS if mode == 'resume_suggested' else MAX_TITLE_ALTERNATIVES
    return {'mode': mode if mode in ('manual', 'resume_suggested') else 'manual',
            'titles': [bounded_text(title, 120) for title in titles[:maximum]
                       if isinstance(title, str) and title.strip()] if isinstance(titles, list) else []}


def record_rejection(health, title, reason, stage='inspection'):
    if not isinstance(health, dict) or '_role_filter' not in health:
        return
    if reason not in REASONS or stage not in STAGES:
        return
    review = health.setdefault('_search_review', {'rejections': [], 'omitted': {}})
    rows = review['rejections']
    if sum(row['stage'] == stage for row in rows) >= MAX_REJECTIONS_PER_STAGE:
        review['omitted'][stage] = review['omitted'].get(stage, 0) + 1
        return
    rows.append({'title': bounded_text(title) or None, 'reason': reason, 'stage': stage})


def sanitize_review(value):
    if not isinstance(value, dict) or not isinstance(value.get('role_filter'), dict):
        return None
    plan = value['role_filter']
    result = {'role_filter': role_filter(plan.get('titles'), plan.get('mode')),
              'rejections': [], 'omitted': {}}
    rows = value.get('rejections')
    rows = rows if isinstance(rows, list) else []
    for stage in STAGES:
        valid = [row for row in rows[:200] if isinstance(row, dict)
                 and row.get('stage') == stage and isinstance(row.get('reason'), str)
                 and row['reason'] in REASONS]
        for row in valid[:MAX_REJECTIONS_PER_STAGE]:
            result['rejections'].append({'title': bounded_text(row.get('title')) or None,
                                         'reason': row['reason'], 'stage': stage})
        omitted = value.get('omitted', {})
        count = omitted.get(stage, 0) if isinstance(omitted, dict) else 0
        result['omitted'][stage] = min(1_000_000, max(0, count)) if type(count) is int else 0
    return result


def review_from_health(health):
    if not isinstance(health, dict) or '_role_filter' not in health:
        return None
    return sanitize_review({**health.get('_search_review', {}), 'role_filter': health['_role_filter']})
