"""Offline exact-fact benchmark; no provider dispatch or runtime routing policy."""
import argparse
from decimal import Decimal
import json
from pathlib import Path

from summarize_ai_usage import cost, fields, identifier, unique_fields


VERSION = 'ai-fact-evaluation-v1'


def fact_list(value):
    if (not isinstance(value, list) or len(value) > 100
            or any(not isinstance(item, str) or not 1 <= len(item) <= 200 for item in value)
            or len(set(value)) != len(value)):
        raise ValueError('Expected bounded unique fact strings')
    return set(value)


def evaluate(document):
    fields(document, {'version', 'provenance', 'cases', 'candidates'})
    if document['version'] != VERSION or document['provenance'] != 'synthetic':
        raise ValueError('Only versioned synthetic benchmarks are accepted')
    cases = document['cases']
    candidates = document['candidates']
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise ValueError('Expected 1-100 cases')
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 20:
        raise ValueError('Expected 1-20 candidates')
    reference = {}
    for case in cases:
        fields(case, {'id', 'supported_facts', 'required_facts'})
        identifier(case['id'])
        supported = fact_list(case['supported_facts'])
        required = fact_list(case['required_facts'])
        if case['id'] in reference or not required or not required <= supported:
            raise ValueError('Duplicate case or invalid required facts')
        reference[case['id']] = (supported, required)
    reports = []
    seen_candidates = set()
    for candidate in candidates:
        fields(candidate, {'id', 'runs'})
        identifier(candidate['id'])
        if candidate['id'] in seen_candidates:
            raise ValueError('Duplicate candidate')
        seen_candidates.add(candidate['id'])
        runs = candidate['runs']
        if not isinstance(runs, list) or len(runs) != len(reference):
            raise ValueError('Every candidate must cover the same complete case set')
        seen = set()
        results = []
        total_cost = Decimal(0)
        total_latency = 0
        unknown_cost = unknown_latency = attempts_count = 0
        for run in runs:
            fields(run, {'case_id', 'attempts'})
            case_id = run['case_id']
            if not isinstance(case_id, str) or case_id not in reference or case_id in seen:
                raise ValueError('Unknown or duplicate case')
            seen.add(case_id)
            attempts = run['attempts']
            if not isinstance(attempts, list) or not 1 <= len(attempts) <= 10:
                raise ValueError('Expected 1-10 attempts per case')
            for attempt in attempts:
                fields(attempt, {'outcome', 'output', 'latency_ms', 'estimated_cost_usd'})
                if attempt['outcome'] not in ('success', 'error', 'cancelled'):
                    raise ValueError('Invalid attempt outcome')
                elapsed = attempt['latency_ms']
                if elapsed is not None and (type(elapsed) is not int or not 0 <= elapsed <= 86400000):
                    raise ValueError('Invalid latency')
                amount = cost(attempt['estimated_cost_usd'])
                total_cost += amount or Decimal(0)
                total_latency += elapsed or 0
                unknown_cost += amount is None
                unknown_latency += elapsed is None
                attempts_count += 1
            final = attempts[-1]
            output = final['output']
            schema = False
            facts = set()
            if final['outcome'] == 'success' and isinstance(output, dict) and set(output) == {'facts'}:
                try:
                    facts = fact_list(output['facts'])
                    schema = True
                except ValueError:
                    pass
            supported, required = reference[case_id]
            unsupported = len(facts - supported) if schema else None
            missing = len(required - facts) if schema else None
            results.append({'case_id': case_id, 'schema_valid': schema,
                            'unsupported_fact_count': unsupported,
                            'missing_required_fact_count': missing,
                            'passed': schema and unsupported == 0 and missing == 0})
        completed = sum(row['passed'] for row in results)
        reports.append({'candidate_id': candidate['id'], 'cases': results,
                        'passed_cases': completed, 'case_count': len(reference),
                        'all_cases_passed': completed == len(reference),
                        'attempts': attempts_count, 'unknown_cost_attempts': unknown_cost,
                        'unknown_latency_attempts': unknown_latency,
                        'known_cost_subtotal_usd': str(total_cost),
                        'estimated_cost_total_usd': None if unknown_cost else str(total_cost),
                        'estimated_cost_per_passed_case_usd': (
                            str(total_cost / completed) if completed and not unknown_cost else None),
                        'known_latency_subtotal_ms': total_latency,
                        'total_latency_ms': None if unknown_latency else total_latency})
    return {'version': VERSION, 'provenance': 'synthetic', 'runtime_routing_approved': False,
            'scope': 'Exact fact preservation only; no semantic relevance or live model quality claim',
            'candidates': reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.input.open('rb') as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Input exceeds 1 MiB')
        result = evaluate(json.loads(raw, object_pairs_hook=unique_fields))
        with args.output.open('x', encoding='utf-8', newline='\n') as target:
            json.dump(result, target, indent=2, allow_nan=False)
            target.write('\n')
    except (ValueError, OSError, RecursionError) as error:
        parser.exit(2, f'Benchmark refused ({type(error).__name__}); check input and use a new output path.\n')


if __name__ == '__main__':
    main()
