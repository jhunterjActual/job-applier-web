"""Offline AI-001 receipt summary for MON-002; no provider calls or billing."""
import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re


VERSION = 'ai-usage-observations-v1'
MAX_RECEIPTS = 1000
MAX_INPUT_BYTES = 1024 * 1024
USAGE_FIELDS = {'input_tokens', 'output_tokens', 'cached_input_tokens',
                'reasoning_tokens', 'total_tokens', 'cache_creation_input_tokens'}
RECEIPT_FIELDS = {'provider', 'model', 'operation', 'outcome', 'dispatched',
                  'usage', 'estimated_cost_usd', 'rate_version'}
OPERATIONS = {'structured', 'query_preparation', 'scoring', 'tailoring',
              'interview_help', 'headquarters', 'capability_check', 'resume_ocr'}
OUTCOMES = {'success', 'cancelled', 'in_progress', 'provider_error', 'authentication',
            'permission', 'rate_limit', 'model_unavailable', 'unsupported_request',
            'timeout', 'network', 'invalid_response', 'empty_response', 'refusal',
            'truncated', 'oversized_response', 'capability', 'invalid_input',
            'unsupported_operation', 'missing_key', 'receipt_limit', 'invalid_rate',
            'redirect_rejected', 'overloaded'}


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('Unexpected or missing fields in receipt document')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,120}', value):
        raise ValueError('Invalid model or rate identifier')


def cost(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Estimated cost must be a bounded decimal string or null')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid estimated cost') from None
    if (not result.is_finite() or result < 0 or result > 100_000_000
            or result.as_tuple().exponent < -12):
        raise ValueError('Estimated cost is outside supported bounds')
    return result


def validate_receipt(record):
    fields(record, RECEIPT_FIELDS)
    if record['provider'] not in ('gemini', 'openai', 'anthropic'):
        raise ValueError('Unsupported provider')
    identifier(record['model'])
    if (not isinstance(record['operation'], str) or record['operation'] not in OPERATIONS
            or not isinstance(record['outcome'], str) or record['outcome'] not in OUTCOMES
            or type(record['dispatched']) is not bool):
        raise ValueError('Invalid operation, outcome or dispatch state')
    usage = record['usage']
    fields(usage, USAGE_FIELDS)
    for value in usage.values():
        if value is not None and (type(value) is not int or not 0 <= value <= 1_000_000_000):
            raise ValueError('Invalid token count')
    incoming, outgoing = usage['input_tokens'], usage['output_tokens']
    cached, created = usage['cached_input_tokens'], usage['cache_creation_input_tokens']
    reasoning, total = usage['reasoning_tokens'], usage['total_tokens']
    if (incoming is not None and sum(v or 0 for v in (cached, created)) > incoming
            or reasoning is not None and outgoing is not None and reasoning > outgoing
            or total is not None and incoming is not None and outgoing is not None
            and total != incoming + outgoing):
        raise ValueError('Inconsistent token counts')
    estimate = cost(record['estimated_cost_usd'])
    if record['rate_version'] is not None:
        identifier(record['rate_version'])
    if estimate is not None and (incoming is None or outgoing is None or record['rate_version'] is None):
        raise ValueError('Estimated cost requires known usage and a rate version')
    if not record['dispatched'] and (record['outcome'] == 'success'
            or any(v not in (None, 0) for v in usage.values()) or estimate not in (None, Decimal(0))):
        raise ValueError('Undispatched receipt contradicts usage or success')
    return estimate


def summarize(document):
    fields(document, {'version', 'provenance', 'capture_complete', 'receipts'})
    if document['version'] != VERSION or document['provenance'] not in ('synthetic', 'supplied_observations'):
        raise ValueError('Unsupported version or provenance')
    records = document['receipts']
    if type(document['capture_complete']) is not bool or not isinstance(records, list) or len(records) > MAX_RECEIPTS:
        raise ValueError('Invalid capture completeness or receipt count')
    groups = {}
    for record in records:
        estimate = validate_receipt(record)
        key = (record['provider'], record['model'], record['operation'])
        group = groups.setdefault(key, {
            'provider': key[0], 'model': key[1], 'operation': key[2],
            'attempts': 0, 'dispatched_attempts': 0, 'successful_attempts': 0,
            'in_progress_attempts': 0, 'unknown_cost_attempts': 0,
            'unknown_usage_attempts': 0, 'known_estimated_cost_subtotal_usd': Decimal(0),
            'known_input_token_subtotal': 0, 'known_output_token_subtotal': 0,
            'outcomes': {}, 'rate_versions': set(),
        })
        group['attempts'] += 1
        outcome = record['outcome']
        group['outcomes'][outcome] = group['outcomes'].get(outcome, 0) + 1
        group['successful_attempts'] += outcome == 'success'
        group['in_progress_attempts'] += outcome == 'in_progress'
        if record['dispatched']:
            group['dispatched_attempts'] += 1
            group['unknown_cost_attempts'] += estimate is None
            group['known_estimated_cost_subtotal_usd'] += estimate or Decimal(0)
            usage = record['usage']
            group['unknown_usage_attempts'] += usage['input_tokens'] is None or usage['output_tokens'] is None
            group['known_input_token_subtotal'] += usage['input_tokens'] or 0
            group['known_output_token_subtotal'] += usage['output_tokens'] or 0
            if record['rate_version'] is not None:
                group['rate_versions'].add(record['rate_version'])
    known = sum((g['known_estimated_cost_subtotal_usd'] for g in groups.values()), Decimal(0))
    unresolved = sum(g['unknown_cost_attempts'] for g in groups.values())
    in_progress = sum(g['in_progress_attempts'] for g in groups.values())
    complete = document['capture_complete'] and not in_progress
    for group in groups.values():
        group['known_estimated_cost_subtotal_usd'] = str(group['known_estimated_cost_subtotal_usd'])
        group['estimated_cost_total_usd'] = (group['known_estimated_cost_subtotal_usd']
            if complete and not group['unknown_cost_attempts'] else None)
        group['rate_versions'] = sorted(group['rate_versions'])
    return {
        'version': VERSION, 'provenance': document['provenance'],
        'capture_complete': complete, 'attempts': len(records),
        'unknown_cost_attempts': unresolved, 'in_progress_attempts': in_progress,
        'known_estimated_cost_subtotal_usd': str(known),
        'estimated_ai_cost_total_usd': str(known) if complete and not unresolved else None,
        'groups': [groups[key] for key in sorted(groups)],
        'completed_workflows': None, 'monthly_cost_usd': None, 'contribution_margin': None,
        'limits': 'Supplied receipt estimates only, not provider invoices or a spend ledger. '
                  'Attempts include retries; they are not job, page or completed-workflow counts. '
                  'Missing requests cannot be detected from receipts. Capture completeness is caller-declared. '
                  'Non-AI costs and monthly workload remain unmeasured.',
    }


def receipt_document(records, *, provenance, capture_complete):
    """Export caller-owned AI-001 receipts after the capture scope has finished."""
    if not isinstance(records, (list, tuple)) or len(records) > MAX_RECEIPTS:
        raise ValueError('Invalid receipt collection')
    document = {'version': VERSION, 'provenance': provenance,
                'capture_complete': capture_complete, 'receipts': []}
    for record in records:
        document['receipts'].append({
            'provider': record.provider, 'model': record.model, 'operation': record.operation,
            'outcome': record.outcome, 'dispatched': record.dispatched,
            'usage': {name: getattr(record.usage, name) for name in sorted(USAGE_FIELDS)},
            'estimated_cost_usd': None if record.estimated_cost_usd is None else str(record.estimated_cost_usd),
            'rate_version': record.rate_version,
        })
    summarize(document)
    return document


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        with args.input.open('rb') as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError('Receipt input exceeds size limit')
        report = summarize(json.loads(raw.decode('utf-8'), object_pairs_hook=unique_fields))
        with args.output.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    except (OSError, ValueError, TypeError, RecursionError):
        parser.exit(1, 'Receipt summary failed: check the input schema, size and new output path.\n')


if __name__ == '__main__':
    main()
