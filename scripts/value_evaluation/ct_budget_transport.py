"""One-call reservation wrapper; never a source of price estimates.

The caller supplies the pinned adapter's original no-redirect opener and an
reviewed resolver using the frozen run's explicit accounting basis. This module
neither establishes that basis nor authorizes provider execution. It never settles:
even a returned HTTP response is not proof of final complete billing.
"""
from dataclasses import dataclass
import json
from urllib.request import Request

from budget_guard import BudgetRefused, identifier


ENDPOINT = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5-5"


@dataclass(frozen=True)
class RequestForBound:
    """Exact request bytes without credential-bearing headers."""
    url: str
    method: str
    body: bytes


@dataclass(frozen=True)
class ChargeReservation:
    amount_usd: str
    evidence_reference: str
    accounting_basis: str


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BudgetRefused("Ambiguous request JSON")
        result[key] = value
    return result


def _snapshot(request):
    if (not isinstance(request, Request) or request.full_url != ENDPOINT or
            request.get_method() != "POST" or type(request.data) is not bytes or
            not 0 < len(request.data) <= 3 * 1024 * 1024):
        raise BudgetRefused("Request outside evaluation transport contract")
    # Reject header modifiers before asking a resolver that intentionally never
    # sees credentials. Inspect both dictionaries so shadowed duplicates cannot
    # conceal a different value from header_items().
    headers = {}
    for name, value in list(request.headers.items()) + list(request.unredirected_hdrs.items()):
        normalized = name.lower()
        if normalized in headers:
            raise BudgetRefused("Ambiguous request headers")
        headers[normalized] = value
    if (set(headers) != {"content-type", "anthropic-version", "x-api-key"} or
            headers["content-type"] != "application/json" or
            headers["anthropic-version"] != "2023-06-01" or
            not isinstance(headers["x-api-key"], str) or not headers["x-api-key"] or
            any(ord(c) < 33 or ord(c) > 126 for c in headers["x-api-key"])):
        raise BudgetRefused("Request headers outside pinned adapter contract")
    try:
        payload = json.loads(request.data, object_pairs_hook=_unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise BudgetRefused("Invalid evaluation request") from None
    if (not isinstance(payload, dict) or payload.get("model") != MODEL or
            type(payload.get("max_tokens")) is not int or
            not 1 <= payload["max_tokens"] <= 32768):
        raise BudgetRefused("Unapproved model or output limit")
    return RequestForBound(request.full_url, request.get_method(), request.data)


class BudgetedOpener:
    """One call ID per wrapper; a duplicate dispatch fails at the durable ledger.

    Resolver must account for the ENTIRE request, all billable categories,
    and any transport retry behavior. Pass the pinned no-retry/no-redirect opener.
    Evidence references must be sanitized; headers and keys must never enter it.
    """
    def __init__(self, delegate, ledger, call_id, packet_attempt, bound_resolver):
        identifier(call_id)
        identifier(packet_attempt)
        if not callable(bound_resolver) or not callable(getattr(delegate, "open", None)):
            raise BudgetRefused("Reviewed reservation resolver and original opener required")
        self.delegate = delegate
        self.ledger = ledger
        self.call_id = call_id
        self.packet_attempt = packet_attempt
        self.bound_resolver = bound_resolver

    def open(self, request, *, timeout):
        if type(timeout) is not int or not 1 <= timeout <= 90:
            raise BudgetRefused("Invalid request timeout")
        _snapshot(request)
        # Isolate the actual dispatch from later changes to caller-owned Request.
        # Immutable bytes are shared; header mappings are copied, never logged.
        prepared = Request(request.full_url, data=request.data,
                           headers=dict(request.header_items()), method=request.get_method())
        snapshot = _snapshot(prepared)
        bound = self.bound_resolver(snapshot)
        if (not isinstance(bound, ChargeReservation) or
                bound.accounting_basis != self.ledger.config.accounting_basis):
            raise BudgetRefused("No matching charge reservation policy")
        self.ledger.reserve(self.call_id, self.packet_attempt,
                            bound.amount_usd, bound.evidence_reference)
        # No retry, response rewriting, settlement or exception logging here.
        return self.delegate.open(prepared, timeout=timeout)
