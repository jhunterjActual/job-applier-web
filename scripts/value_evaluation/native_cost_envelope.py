"""Pure arithmetic for the fixed native application-study request envelope.

Declarations are not evidence of token counts, current prices, available credit,
funding approval or complete-batch feasibility on future adaptive requests.
No files, credentials, networks, clocks or campaign ledgers are accessed.
"""
from decimal import Context, Decimal, InvalidOperation, ROUND_CEILING, localcontext
from functools import wraps
import re


MODEL = "claude-sonnet-5-5"
MICRO = Decimal("0.000001")
# Operation, product, stage, cases, maximum requests per case, native output limit.
OPERATIONS = (
    ("ct_fit", "CT", "fit", 12, 1, 16384),
    ("ct_tailor", "CT", "materials", 6, 1, 32768),
    ("ct_headquarters", "CT", "materials", 6, 1, 4096),
    ("up_fit", "UP", "fit", 12, 4, 32768),
    ("up_materials", "UP", "materials", 6, 20, 32768),
)


class EstimateRefused(ValueError):
    pass


def _fixed_decimal_context(function):
    @wraps(function)
    def calculate(*args, **kwargs):
        with localcontext(Context(prec=64)):
            return function(*args, **kwargs)
    return calculate


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?", value):
        raise EstimateRefused("decimal_string_required")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise EstimateRefused("invalid_amount") from None
    if not result.is_finite() or result < 0 or result.as_tuple().exponent < -6:
        raise EstimateRefused("nonnegative_finite_microdollars_required")
    return result


def usd(value):
    return format(value, ".6f")


@_fixed_decimal_context
def request_reservation(counted_input, max_output, input_price, output_price):
    """Retain the reviewed doubled-input, 4096-token and 25% margins."""
    if (type(counted_input) is not int or not 0 <= counted_input <= 100000
            or type(max_output) is not int or not 1 <= max_output <= 32768):
        raise EstimateRefused("request_outside_reviewed_envelope")
    input_rate, output_rate = amount(input_price), amount(output_price)
    if input_rate <= 0 or output_rate <= 0:
        raise EstimateRefused("positive_prices_required")
    raw = ((Decimal(counted_input * 2 + 4096) * input_rate
        + Decimal(max_output) * output_rate) * Decimal("1.25") / Decimal(1000000))
    return raw.quantize(MICRO, rounding=ROUND_CEILING)


@_fixed_decimal_context
def estimate(input_limits, pricing, available_cap_usd, contingency_usd):
    """Compare declared full-batch reservations with a supplied private cap.

    input_limits must include the ENTIRE provider input, including system/tools,
    native reviewer context, thinking/context behavior and nested image inputs.
    The caller must establish those bounds independently for future requests.
    """
    if not isinstance(input_limits, dict) or set(input_limits) != {row[0] for row in OPERATIONS}:
        raise EstimateRefused("all_native_operation_input_limits_required")
    if (not isinstance(pricing, dict) or set(pricing) != {
            "model", "input_usd_per_million", "output_usd_per_million", "source", "verified_utc"}
            or pricing["model"] != MODEL
            or pricing["source"] != "https://platform.claude.com/docs/en/about-claude/pricing"
            or not isinstance(pricing["verified_utc"], str) or not pricing["verified_utc"]):
        raise EstimateRefused("explicit_model_pricing_declaration_required")
    cap, contingency = amount(available_cap_usd), amount(contingency_usd)
    rows, stages = [], {}
    total = Decimal(0)
    for name, product, stage, cases, requests, output in OPERATIONS:
        reservation = request_reservation(input_limits[name], output,
            pricing["input_usd_per_million"], pricing["output_usd_per_million"])
        subtotal = reservation * cases * requests
        total += subtotal
        rows.append({"operation": name, "product_id": product, "stage": stage,
            "cases": cases, "max_requests_each": requests, "max_requests": cases * requests,
            "declared_input_limit_each_including_images": input_limits[name],
            "native_max_output_each": output, "reservation_each_request_usd": usd(reservation),
            "reservation_total_usd": usd(subtotal)})
        key = (product, stage)
        entry = stages.setdefault(key, {"product_id": product, "stage": stage, "count": cases,
            "max_requests_each": 0, "reservation_each_usd": Decimal(0)})
        entry["max_requests_each"] += requests
        entry["reservation_each_usd"] += reservation * requests
    stage_rows, packets = [], {}
    for product in ("CT", "UP"):
        packet = Decimal(0)
        for stage in ("fit", "materials"):
            entry = stages[(product, stage)]
            packet += entry["reservation_each_usd"]
            stage_rows.append({**entry, "reservation_each_usd": usd(entry["reservation_each_usd"])})
        packets[product] = {"full_packet_reservation_usd": usd(packet),
            "fit_only_reservation_usd": usd(stages[(product, "fit")]["reservation_each_usd"])}
    required = total + contingency
    return {"schema": "native-cost-envelope-v1", "classification": "declared_arithmetic_not_execution_authority",
        "pricing": dict(pricing), "operations": rows, "stage_rows": stage_rows, "packets": packets,
        "max_generation_requests": sum(row[3] * row[4] for row in OPERATIONS),
        "max_requested_output_tokens": sum(row[3] * row[4] * row[5] for row in OPERATIONS),
        "batch_reservation_usd": usd(total), "contingency_usd": usd(contingency),
        "required_total_usd": usd(required), "supplied_cap_usd": usd(cap),
        "reservation_gap_usd": usd(max(required - cap, Decimal(0))),
        "arithmetic_within_supplied_cap": required <= cap,
        "future_input_bounds_verified": False, "funding_or_dispatch_authorized": False,
        "limitations": "Token counts must cover native image/tool/reviewer inputs. Prices, cap and input bounds are caller declarations. Estimates are not guaranteed provider billing bounds. No favorable settlements, retries or missing-case replacement assumed."}
