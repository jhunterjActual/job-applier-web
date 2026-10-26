"""Explicit five-minute Claude prompt-cache preparation; no storage or dispatch."""
from dataclasses import dataclass, field
from decimal import Decimal
from ai_contracts import RateCard


@dataclass(frozen=True)
class CachedPrompt:
    # Candidate evidence is private request data, never diagnostic representation.
    stable_prefix: str = field(repr=False)
    suffix: str = field(repr=False)
    ttl: str = '5m'

    def __post_init__(self):
        if (type(self.stable_prefix) is not str or not self.stable_prefix.strip()
                or type(self.suffix) is not str or not self.suffix.strip()
                or self.ttl != '5m' or type(self.ttl) is not str):
            raise ValueError('Explicit nonempty five-minute cache blocks required')

    @property
    def input_bytes(self):
        return len(self.stable_prefix.encode('utf-8')) + len(self.suffix.encode('utf-8'))

    def content(self):
        # Both blocks retain the user role. No candidate facts become system policy.
        return [{'type': 'text', 'text': self.stable_prefix,
                 'cache_control': {'type': 'ephemeral', 'ttl': '5m'}},
                {'type': 'text', 'text': self.suffix}]


def cache_miss_reservation_usd(input_token_bound, output_token_bound, rates):
    """Pure per-request envelope, not a ledger or full-stage spending guard.

    Reserve the entire input at the highest regular/read/write price, with no assumed
    cache hit. Bounds and the exact reviewed rate version remain caller authority.
    """
    if (not isinstance(rates, RateCard)
            or any(type(n) is not int or not 0 <= n <= 1_000_000_000
            for n in (input_token_bound, output_token_bound))
            or rates.provider != 'anthropic'
            or rates.cache_creation_5m_per_million_usd is None):
        raise ValueError('Known Claude input/output bounds and write rate required')
    incoming = max(rates.input_per_million_usd, rates.cached_input_per_million_usd,
                   rates.cache_creation_5m_per_million_usd)
    return (input_token_bound * incoming
            + output_token_bound * rates.output_per_million_usd) / Decimal(1_000_000)
