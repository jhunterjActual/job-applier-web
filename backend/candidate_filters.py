"""Deterministic acquisition filters; unknown location is not affirmative eligibility."""
import re


def words(value):
    return re.findall(r'\w+', str(value or '').casefold())


def role_matches(title, keywords):
    tokens = words(title)
    for keyword in keywords:
        wanted = words(keyword)
        if not wanted:
            continue
        # A narrow reviewed alternative, not an equivalence between architect roles.
        variants = [wanted]
        if wanted == ['software', 'engineer']:
            variants.append(['software', 'developer'])
        elif wanted == ['software', 'developer']:
            variants.append(['software', 'engineer'])
        exact_role = wanted in (['software', 'engineer'], ['software', 'developer'],
                                ['data', 'engineer'], ['enterprise', 'architect'])
        if any((any(tokens[index:index + len(variant)] == variant
                    for index in range(len(tokens) - len(variant) + 1)) if exact_role
                else all(word in tokens for word in variant)) for variant in variants):
            return True
    return False


def location_matches(row, locations):
    """Match explicit metadata using tokens, so Dayton is not Daytona."""
    place = words(row.get('location'))
    arrangement = row.get('work_arrangement', '')
    remote = row.get('remote') is True or arrangement == 'remote' or 'remote' in place
    # Normalize common US aliases without treating pronoun 'us' in prose as geography.
    def normalized(tokens):
        text = ' '.join(tokens)
        text = re.sub(r'\b(united states(?: of america)?|u s a|u s|usa)\b', 'us', text)
        text = re.sub(r'\bohio\b', 'oh', text)
        return text.split()
    place = normalized(place)
    for location in locations:
        wanted = normalized(words(location))
        if not wanted:
            return True
        if wanted == ['remote'] and remote:
            return True
        if all(word in place or (word == 'remote' and remote) for word in wanted):
            return True
    return False


def needs_location_details(row, locations):
    """Allow a small detail queue when listing metadata cannot establish a match."""
    if location_matches(row, locations):
        return False
    place = words(row.get('location'))
    # Missing place and generic remote labels may acquire eligibility in details.
    if not place or set(place) <= {'remote', 'hybrid', 'anywhere', 'worldwide'}:
        return True
    # Providers frequently omit work arrangement on list endpoints.
    return (any('remote' in words(value) for value in locations)
            and not row.get('work_arrangement') and row.get('remote') is not True)


def detail_location_matches(details, locations):
    text = str(details.get('description') or '')[:50_000]
    remote_requested = any('remote' in words(value) for value in locations)
    if remote_requested and re.search(r'\b(?:not|no)\s+(?:a\s+)?remote\b|\bremote (?:work|working) (?:is )?not (?:available|permitted|allowed)\b', text, re.I):
        return location_matches(details, [value for value in locations if 'remote' not in words(value)])
    # Text-inferred remote labels have weaker provenance than a provider's
    # structured arrangement. Require an explicit job/work arrangement statement.
    if details.get('_work_arrangement_from_text') and remote_requested:
        explicit = re.search(r'\b(?:fully remote|remote (?:position|role|job)|work(?:ing)? remotely|work remote|remote work|remote throughout|remote anywhere)\b', text, re.I)
        if not explicit:
            details = {**details, 'work_arrangement': '', 'remote': False}
    if location_matches(details, locations):
        return True
    # Description statements must name eligibility explicitly. Do not infer US
    # eligibility from citizenship, an office address, or a casual mention of us.
    remote_us = re.search(
        r'\bremote\b[^.\n]{0,70}\b(?:within|throughout|anywhere in|based in)\s+(?:the\s+)?United States\b',
        text, re.I)
    if remote_us and not re.search(r'\b(?:not|no)\s+(?:a\s+)?remote\b', text, re.I):
        return location_matches({'location': 'United States', 'remote': True}, locations)
    return False
