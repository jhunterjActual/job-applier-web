"""Explicit, bounded title alternatives shared by admission and search execution."""
import re
import json
from candidate_filters import words

MAX_TITLE_ALTERNATIVES = 3
MAX_TITLE_CHARS = 120
TITLE_LIST_PREFIX = 'ct:title-list:v1:'


def parse_manual_titles(value):
    if not isinstance(value, str):
        raise ValueError('Enter job titles as text.')
    if len(value) > 500:
        raise ValueError('Keep the complete title list within 500 characters.')
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('Separate title alternatives with semicolons, not newlines or control characters.')
    if not value.strip() or value.strip().casefold() == 'undefined':
        return []  # Preserve the existing resume-suggested mode.
    titles = []
    for part in value.split(';'):
        title = ' '.join(part.split())
        if not title or not re.search(r'\w', title):
            raise ValueError('Enter a title on each side of every semicolon.')
        if len(title) > MAX_TITLE_CHARS:
            raise ValueError('Each title alternative must be 120 characters or fewer.')
        if title.casefold() not in {item.casefold() for item in titles}:
            titles.append(title)
    if len(titles) > MAX_TITLE_ALTERNATIVES:
        raise ValueError('Use at most three different title alternatives.')
    return titles


def encode_titles(value, alternatives=False):
    if value.strip().startswith(TITLE_LIST_PREFIX):
        raise ValueError('Enter plain job titles, not an internal search representation.')
    if not alternatives:
        return value
    titles = parse_manual_titles(value)
    encoded = TITLE_LIST_PREFIX + json.dumps(titles, ensure_ascii=False, separators=(',', ':')) if titles else ''
    if len(encoded) > 500:
        raise ValueError('Shorten the title alternatives to fit the saved-search limit.')
    return encoded


def execution_titles(value):
    """Decode only explicitly versioned lists; legacy semicolons remain one query."""
    if not value.startswith(TITLE_LIST_PREFIX):
        return [] if not value.strip() or value.strip().casefold() == 'undefined' else [value.strip()]
    try:
        titles = json.loads(value[len(TITLE_LIST_PREFIX):])
    except (ValueError, TypeError) as exc:
        raise ValueError('The stored title alternatives are invalid.') from exc
    if not isinstance(titles, list) or not titles or any(not isinstance(item, str) or ';' in item for item in titles):
        raise ValueError('The stored title alternatives are invalid.')
    normalized = parse_manual_titles(';'.join(titles))
    if not normalized or normalized != titles:
        raise ValueError('The stored title alternatives are invalid.')
    return titles


def display_saved_search(search):
    result = dict(search)
    if 'keywords' not in result:
        return result
    value = result.get('keywords') or ''
    result['title_alternatives'] = value.startswith(TITLE_LIST_PREFIX)
    if result['title_alternatives']:
        try:
            result['keywords'] = '; '.join(execution_titles(value))
        except ValueError:
            # Keep a damaged row visible and deletable; admission rejects replaying it.
            result['title_alternatives'] = False
    return result


def title_plan(value, alternatives=False):
    from discovery_sources import bounded_terms, MAX_KEYWORDS
    manual = execution_titles(encode_titles(value, alternatives))
    titles, _ = bounded_terms(manual, MAX_KEYWORDS)
    return {'mode': ('manual' if titles else 'invalid') if manual else 'resume_suggested', 'titles': titles,
            'max_titles': MAX_TITLE_ALTERNATIVES,
            'software_alias': any(words(title) in (['software', 'engineer'], ['software', 'developer'])
                                  for title in titles)}
