"""Offline illustrative unit costs. Unknown operating costs never become zero."""
import argparse
from decimal import Decimal
import json
from pathlib import Path


def number(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError('Cost inputs must be finite and nonnegative')
    return result


def calculate(document, multiplier=Decimal(1)):
    multiplier = number(multiplier)
    price = number(document['target_price_usd'])
    margin = number(document['target_contribution_margin'])
    if price <= 0 or margin >= 1:
        raise ValueError('Positive price and margin below one required')
    unknown = [name for name, cost in document['missing_monthly_costs_usd'].items() if cost is None]
    other = sum((number(cost) for cost in document['missing_monthly_costs_usd'].values() if cost is not None), Decimal(0))
    results = {}
    for model, rates in document['rates'].items():
        results[model] = {}
        for name, workload in document['scenarios'].items():
            incoming = outgoing = Decimal(0)
            for operation, tokens in document['unit_token_assumptions'].items():
                incoming += number(workload[operation]) * number(tokens['input'])
                outgoing += number(workload[operation]) * number(tokens['output'])
            retries = 1 + number(workload['retry_fraction'])
            ai = (incoming * number(rates['input_per_million_usd'])
                  + outgoing * number(rates['output_per_million_usd'])) / 1_000_000 * retries * multiplier
            discovery = number(workload['discovery_requests']) * number(document['discovery_request_replacement_usd']) * multiplier
            subtotal = ai + discovery + other
            results[model][name] = {
                'ai_usd': str(ai), 'discovery_replacement_usd': str(discovery),
                'known_cost_subtotal_usd': str(subtotal), 'missing_cost_components': unknown,
                'total_cost_usd': None if unknown else str(subtotal),
                'contribution_margin': None if unknown else str((price - subtotal) / price),
                'remaining_cost_budget_at_target_margin_usd': str(price * (1 - margin) - subtotal),
                'ai_exceeds_proposed_three_dollar_cap': ai > 3,
            }
    return {'inputs_version': document['version'], 'workload_provenance': document['workload_provenance'],
            'variable_cost_multiplier': str(multiplier), 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--multiplier', default='1')
    args = parser.parse_args()
    result = calculate(json.loads(args.inputs.read_text(encoding='utf-8')), number(args.multiplier))
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
