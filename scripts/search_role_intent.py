"""Conservative offline role-intent experiment; not a natural-language classifier."""
import re

MAX_DESCRIPTION_CHARS = 50_000


def affirmative_role(description, role):
    """Recognize a few explicit current-duty statements, otherwise abstain.

    Recognized negative/conditional words and double quotes cause abstention, even when
    unrelated to the role. This deliberately loses recall; it is not runtime policy.
    """
    text = str(description or '')
    if len(text) > MAX_DESCRIPTION_CHARS:
        return False  # Do not accept a prefix whose later text may contradict it.
    text = text.casefold().replace('\u2019', "'")
    if re.search(r'\b(?:no|not|never|without|if|unless|would|could|might)\b|n\x27t\b|["\u201c\u201d]', text):
        return False
    tokens = re.findall(r'\w+', str(role or '').casefold())
    if not tokens or len(tokens) > 12:
        return False
    phrase = r'\s+'.join(re.escape(token) for token in tokens)
    # Full sentences prevent a colleague/customer phrase from supplying the subject.
    patterns = (
        rf'duties (?:are|include) (?:exclusively )?{phrase} (?:work|responsibilities)',
        rf'(?:this|the) (?:position|role|job) is (?:a|an) {phrase}(?: role| position| job)?',
        rf'you will (?:work|serve) as (?:a|an) {phrase}',
    )
    return any(re.fullmatch(pattern, sentence.strip())
               for sentence in re.split(r'[.!?;\n]+', text)
               for pattern in patterns)
