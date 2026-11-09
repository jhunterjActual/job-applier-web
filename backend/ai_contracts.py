"""Bounded AI request and usage contracts; no credentials or persistence."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock


@dataclass(frozen=True)
class Operation:
    structured: bool
    max_input_bytes: int
    max_output_tokens: int


OPERATIONS = {
    'structured': Operation(True, 512 * 1024, 32768),
    # Preserve accepted 200k-character resumes at up to four UTF-8 bytes per
    # character, plus job/evidence fields and caller prompt instructions.
    'query_preparation': Operation(True, 1024 * 1024, 4096),
    'scoring': Operation(True, 2 * 1024 * 1024, 16384),
    'tailoring': Operation(True, 2 * 1024 * 1024, 32768),
    'interview_help': Operation(True, 512 * 1024, 16384),
    'headquarters': Operation(True, 128 * 1024, 4096),
    'capability_check': Operation(True, 4096, 1024),
    'resume_ocr': Operation(False, 12 * 1024 * 1024, 8192),
}
SCHEMA_OPERATIONS = {
    'SearchKeywordResponse': 'query_preparation', 'JobMatchResponse': 'scoring',
    'BatchMatchResponse': 'scoring', 'TailoringResponse': 'tailoring',
    'InterviewPrepAIResponse': 'interview_help', 'HeadquartersAIResponse': 'headquarters',
    'CapabilityResponse': 'capability_check',
}
MAX_OCR_PAGES = 12
MAX_RECEIPTS = 1000
# Reviewed defaults only. Availability for a particular account remains remote authority.
MODEL_CAPABILITIES = {
    ('gemini', 'gemini-2.5-flash'): frozenset({'structured', 'image'}),
    ('openai', 'gpt-5-mini'): frozenset({'structured', 'image'}),
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_creation_5m_input_tokens: int | None = None
    cache_creation_1h_input_tokens: int | None = None

    @property
    def known(self):
        return self.input_tokens is not None and self.output_tokens is not None


def _value(source, key):
    return source.get(key) if isinstance(source, dict) else getattr(source, key, None)


def _count(source, key):
    value = _value(source, key)
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def normalize_usage(provider, metadata):
    if provider == 'anthropic':
        # Anthropic's input_tokens excludes cache reads/writes. Require all
        # components; missing cache information is not evidence of zero usage.
        counts = [_count(metadata, name) for name in
                  ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')]
        if any(value is None for value in counts):
            return Usage()
        incoming, cached, created, outgoing = counts
        five_minute = one_hour = None
        if ((isinstance(metadata, dict) and 'cache_creation' in metadata)
                or (not isinstance(metadata, dict) and hasattr(metadata, 'cache_creation'))):
            split = _value(metadata, 'cache_creation')
            expected = {'ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens'}
            if not isinstance(split, dict) or set(split) != expected:
                return Usage()
            five_minute, one_hour = [_count(split, name) for name in
                                    ('ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens')]
            if five_minute is None or one_hour is None or five_minute + one_hour != created:
                return Usage()
        incoming += cached + created
        return Usage(incoming, outgoing, cached, None, incoming + outgoing, created,
                     five_minute, one_hour)
    if provider == 'openai':
        incoming = _count(metadata, 'input_tokens')
        outgoing = _count(metadata, 'output_tokens')
        cached = _count(_value(metadata, 'input_tokens_details'), 'cached_tokens')
        thinking = _count(_value(metadata, 'output_tokens_details'), 'reasoning_tokens')
        total = _count(metadata, 'total_tokens')
    elif provider == 'gemini':
        incoming = _count(metadata, 'prompt_token_count')
        visible = _count(metadata, 'candidates_token_count')
        thinking = _count(metadata, 'thoughts_token_count')
        total = _count(metadata, 'total_token_count')
        # Total includes thought tokens; never silently treat absent thought usage as zero.
        outgoing = total - incoming if total is not None and incoming is not None and total >= incoming else None
        if outgoing is None and visible is not None and thinking is not None:
            outgoing = visible + thinking
        cached = _count(metadata, 'cached_content_token_count')
        if (outgoing is not None and ((visible is not None and outgoing < visible)
                or (visible is not None and thinking is not None and outgoing != visible + thinking))):
            return Usage()
    else:
        raise ValueError('Unsupported usage provider')
    if ((cached is not None and incoming is not None and cached > incoming)
            or (thinking is not None and outgoing is not None and thinking > outgoing)
            or (total is not None and incoming is not None and outgoing is not None
                and total != incoming + outgoing)):
        return Usage()
    return Usage(incoming, outgoing, cached, thinking, total)


@dataclass(frozen=True)
class RateCard:
    provider: str
    model: str
    version: str
    input_per_million_usd: Decimal
    output_per_million_usd: Decimal
    cached_input_per_million_usd: Decimal
    cache_creation_5m_per_million_usd: Decimal | None = None
    cache_creation_1h_per_million_usd: Decimal | None = None

    def __post_init__(self):
        for rate in (self.input_per_million_usd, self.output_per_million_usd,
                     self.cached_input_per_million_usd):
            if not isinstance(rate, Decimal) or not rate.is_finite() or rate < 0:
                raise ValueError('Rates must be finite nonnegative Decimals')
        for rate in (self.cache_creation_5m_per_million_usd, self.cache_creation_1h_per_million_usd):
            if rate is not None and (not isinstance(rate, Decimal) or not rate.is_finite() or rate < 0):
                raise ValueError('Cache write rates must be finite nonnegative Decimals or unknown')


def estimated_cost(usage, rate):
    if not usage.known or rate is None:
        return None
    if rate.provider == 'anthropic' and (usage.cache_creation_input_tokens is None
                                         or usage.cached_input_tokens is None):
        return None
    cached = usage.cached_input_tokens
    if cached is None:
        if rate.input_per_million_usd != rate.cached_input_per_million_usd:
            return None
        cached = 0
    created = 0 if usage.cache_creation_input_tokens is None else usage.cache_creation_input_tokens
    if any(type(n) is not int or not 0 <= n <= 1_000_000_000
           for n in (usage.input_tokens, usage.output_tokens, cached, created)):
        return None
    if cached + created > usage.input_tokens:
        return None
    write_cost = Decimal(0)
    split = (usage.cache_creation_5m_input_tokens, usage.cache_creation_1h_input_tokens)
    if any(n is not None for n in split):
        if (rate.provider != 'anthropic' or any(type(n) is not int or n < 0 for n in split)
                or sum(split) != created):
            return None
    if created:
        # Aggregate-only evidence never identifies the cache's write TTL.
        if any(n is None for n in split):
            return None
        for count, write_rate in zip(split, (rate.cache_creation_5m_per_million_usd,
                                            rate.cache_creation_1h_per_million_usd)):
            if count and write_rate is None:
                return None
            write_cost += count * (write_rate or Decimal(0))
    return ((usage.input_tokens - cached - created) * rate.input_per_million_usd
            + cached * rate.cached_input_per_million_usd
            + write_cost + usage.output_tokens * rate.output_per_million_usd) / Decimal(1_000_000)


@dataclass(frozen=True)
class Receipt:
    provider: str
    model: str
    operation: str
    outcome: str
    dispatched: bool
    usage: Usage
    estimated_cost_usd: Decimal | None
    rate_version: str | None


_RECEIPTS = ContextVar('ai_receipts', default=None)
_RECEIPT_LOCK = ContextVar('ai_receipt_lock', default=None)
_ATTEMPT = ContextVar('ai_attempt', default=None)


@contextmanager
def capture_receipts():
    """Opt-in caller-owned scope. Never global telemetry or a billing ledger."""
    records = []
    token = _RECEIPTS.set(records)
    lock_token = _RECEIPT_LOCK.set(Lock())
    try:
        yield records
    finally:
        _RECEIPTS.reset(token)
        _RECEIPT_LOCK.reset(lock_token)


def observe_usage(provider, metadata):
    attempt = _ATTEMPT.get()
    if attempt is not None:
        attempt['usage'] = normalize_usage(provider, metadata)
