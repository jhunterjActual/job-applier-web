"""Offline section/subject-aware role experiment; never a runtime eligibility policy."""
import re

MAX_DESCRIPTION_CHARS = 50_000


def role_evidence(description, role):
    """Return bounded reason codes, never description text or private excerpts."""
    text = str(description or '')
    tokens = re.findall(r'\w+', str(role or '').casefold())
    if not tokens or len(tokens) > 12 or not text.strip():
        return {'matched': False, 'reason': 'missing_evidence'}
    if len(text) > MAX_DESCRIPTION_CHARS:
        return {'matched': False, 'reason': 'input_limit'}
    text = text.casefold().replace('\u2019', "'")
    phrase = r'\s+'.join(re.escape(token) for token in tokens)

    # Keep attributed quotations only when they expressly describe this opening.
    # Other quotations (including multiline customer examples) cannot supply duties.
    quote_pattern = r'"[^"\n]*(?:\n[^"\n]*)*"|\u201c[^\u201d]*\u201d|(?<!\w)\x27[^\x27]*\x27'
    def unquote(match):
        prefix = text[max(0, match.start() - 160):match.start()]
        if re.search(r'(?:describes?|describing) (?:this|our|the current) (?:opening|role|position)\s*:\s*$', prefix):
            return match.group(0)[1:-1]
        return '\n'  # Preserve a boundary; never join attribution to later duties.
    text = re.sub(quote_pattern, unquote, text)
    current = True
    matched = False
    denied = False
    explicit = (
        rf'duties (?:are|include) (?:exclusively )?{phrase} (?:work|responsibilities)',
        rf'(?:this|the|our|your(?: own)?) (?:position|role|job|opening) is (?:a|an) {phrase}(?: role| position| job)?(?:, responsible for .+)?',
        rf'you will (?:work|serve|act) as (?:a|an) {phrase}',
        rf'(?:your role|job title|position title)\s*:\s*{phrase}',
        rf'(?:today )?we are (?:hiring|recruiting|seeking) (?:a|an) {phrase}(?: to .+)?',
    )
    for raw in re.split(r'(?<=[.!?;])\s+|[\r\n]+', text):
        sentence = raw.strip(' \t.!?;*-')
        if not sentence:
            continue
        sentence = re.sub(r'^(?:the )?(?:hiring )?manager describes (?:this|our|the current) '
                          r'(?:opening|role|position)\s*:\s*', '', sentence)
        # A correction withdraws preceding evidence; do not guess what remains true.
        if re.match(r'(?:correction|erratum|update)\s*:', sentence):
            return {'matched': False, 'reason': 'corrected_or_ambiguous'}
        if re.search(r'\b(?:draft|label|description|statement)\b.{0,30}\b(?:incorrect|wrong|superseded)\b', sentence):
            return {'matched': False, 'reason': 'corrected_or_ambiguous'}
        if re.fullmatch(r'(?:final duties|final responsibilities)', sentence):
            current, matched, denied = True, False, False
            continue
        heading = r'(?:current (?:opening|vacancy|role|position|responsibilities)|our opening|your (?:responsibilities|work|job)|open position)'
        if re.fullmatch(heading, sentence):
            current = True
            continue
        if re.match(heading + r'\s*:', sentence):
            current = True
            sentence = sentence.split(':', 1)[1].strip()
        elif re.match(r'(?:archived?\b|draft\b|background on\b|historical\b|history\b|previous (?:opening|vacancy|role)\b|'
                      r'past (?:opening|role)\b|example\b|sample\b|training example\b|'
                      r'(?:our )?customer(?:\x27s)? (?:vacancy|opening|role|posting|work)\b|'
                      r'(?:our )?client(?:\x27s)? (?:vacancy|opening|role|posting|work)\b|'
                      r'your (?:supervisor|manager|colleague)\x27s (?:assignment|role|work)\b)', sentence):
            current = False
            continue
        elif re.search(r'\b(?:archived job description|old vacancy|example posting)\b', sentence):
            current = False
            continue
        # Explicit ownership can reset an excluded third-party or historical section.
        if re.match(r'(?:our|this) opening (?:is|requires)\b', sentence):
            current = True
        if not current:
            continue
        if re.search(rf'\b(?:not|no|never)\b[^.!?;]{{0,35}}\b{phrase}\b', sentence):
            denied = True
            continue
        if re.search(r'\byou will not (?:perform|do) (?:that|this|the) work\b', sentence):
            denied = True
            continue
        if re.search(r'\b(?:if|unless|would|could|might|promoted|promotion)\b', sentence):
            continue
        sentence = re.sub(r',\s*not as (?:a|an) [\w -]+$', '', sentence)
        if any(re.fullmatch(pattern, sentence) for pattern in explicit):
            matched = True
        # Narrow duty bundles, requiring assigned technical actions as well as
        # multiple domain signals. A domain mention by itself supplies no role.
        assigned = re.match(r'(?:you will |own |design[ ,]|implement[ ,]|develop[ ,]|build[ ,]|define[ ,])', sentence)
        technical = re.search(r'\b(?:design|implement|build|develop|write|maintain|operation|governance|govern)\b', sentence)
        if assigned and technical and not re.search(r'\b(?:not|no|never|sell|sales|supporting|assist)\b', sentence):
            if tokens == ['data', 'engineer']:
                matched |= bool(re.search(r'\b(?:etl|pipelines?)\b', sentence)
                                and re.search(r'\b(?:warehouse|ingestion)\b', sentence))
            elif tokens == ['software', 'engineer']:
                matched |= bool(re.search(r'\b(?:application code|web services|software)\b', sentence)
                                and len(re.findall(r'\b(?:design|implement|test|maintain|write|deploy|build)\b', sentence)) >= 2)
            elif tokens == ['enterprise', 'architect']:
                matched |= bool(re.search(r'\b(?:enterprise[ -]wide|enterprise technology)\b', sentence)
                                and re.search(r'\barchitecture\b', sentence)
                                and re.search(r'\b(?:governance|target-state|standards)\b', sentence))
    if denied:
        return {'matched': False, 'reason': 'role_denied'}
    return {'matched': matched, 'reason': 'current_role_statement' if matched else 'no_current_role_statement'}


def affirmative_role(description, role):
    return role_evidence(description, role)['matched']
