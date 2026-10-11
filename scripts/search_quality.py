"""Offline SEARCH-002 metrics. No application imports, network, or user data."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path

from search_role_intent import affirmative_role


DIAGNOSTIC_COUNTERS = frozenset({
    'coverage_pages_read', 'coverage_cached_pages', 'coverage_http_attempts',
    'coverage_rows_read', 'coverage_advertised_rows', 'coverage_role_excluded',
    'coverage_location_excluded', 'coverage_location_unknown', 'coverage_metadata_matches',
    'coverage_duplicate_candidates', 'coverage_detail_cap_excluded',
    'coverage_failed_boards', 'accepted', 'rejected', 'query_mismatch',
    'eligibility_unknown', 'stale', 'provider_error', 'timeouts', 'catalog_page_limits',
})


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def evaluate(reference, observations):
    if reference['version'] != observations['reference_version']:
        raise ValueError('Reference version mismatch')
    expected = {cell['id']: cell for cell in reference['cells']}
    if len(expected) != len(reference['cells']):
        raise ValueError('Duplicate reference cell')
    reports = {}
    for observed in observations['cells']:
        cell_id = observed['id']
        if cell_id not in expected or cell_id in reports:
            raise ValueError('Unknown or duplicate observation cell')
        cell = expected[cell_id]
        rows = {row['id']: row for row in cell['jobs']}
        if len(rows) != len(cell['jobs']):
            raise ValueError('Duplicate reference job')
        ranked = observed['ranked_ids']
        if any(job_id not in rows for job_id in ranked):
            raise ValueError('Unjudged result: extend the reference before comparison')
        if any(row['relevant'] and row['status'] != 'open' for row in rows.values()):
            raise ValueError('Positive references must be open')
        judgments = {}
        for row in rows.values():
            judgment = (row['relevant'], row['status'], row['employer'])
            if row['identity'] in judgments and judgments[row['identity']] != judgment:
                raise ValueError('Conflicting canonical identity judgments')
            judgments[row['identity']] = judgment
        positives = {row['identity'] for row in rows.values() if row['relevant']}
        seen, useful, top_hits = set(), set(), 0
        duplicates = stale = 0
        for index, job_id in enumerate(ranked):
            row = rows[job_id]
            identity = row['identity']
            duplicate = identity in seen
            duplicates += duplicate
            stale += row['status'] == 'closed'
            eligible = row['relevant'] and row['status'] == 'open'
            if eligible and not duplicate:
                useful.add(identity)
                top_hits += index < 10
            seen.add(identity)
        requests = observed.get('http_attempts')
        attempted = observed.get('detail_attempts')
        succeeded = observed.get('detail_successes')
        for value in (requests, attempted, succeeded, observed.get('latency_ms')):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError('Invalid measurement')
        for value in (requests, attempted, succeeded):
            if value is not None and type(value) is not int:
                raise ValueError('Counts must be integers')
        if succeeded is not None and (attempted is None or succeeded > attempted):
            raise ValueError('Invalid detail success count')
        diagnostics = observed.get('diagnostics', {})
        if any(key not in DIAGNOSTIC_COUNTERS or type(value) is not int or value < 0
               for key, value in diagnostics.items()):
            raise ValueError('Only known aggregate diagnostic counters are accepted')
        reports[cell_id] = {
            'precision_at_10': top_hits / 10,
            'precision_at_returned': ratio(top_hits, min(10, len(ranked))),
            'precision_all_returned': ratio(len(useful), len(ranked)),
            'known_set_recall': ratio(len(useful), len(positives)),
            'unique_eligible_jobs': len(useful),
            'unique_eligible_employers': len({row['employer'] for row in rows.values()
                                               if row['identity'] in useful and row['relevant']}),
            'duplicate_rate': ratio(duplicates, len(ranked)),
            'stale_rate': ratio(stale, len(ranked)),
            'detail_success_rate': ratio(succeeded, attempted) if succeeded is not None else None,
            'latency_ms': observed.get('latency_ms'),
            'http_attempts': requests,
            'requests_per_usable_result': ratio(requests, len(useful)) if requests is not None else None,
            'missed_positive_identities': sorted(positives - useful),
            # One snapshot per cell; repeated diagnostic history rows are never summed.
            'diagnostics': diagnostics,
            'unmeasured_stages': observed.get('unmeasured_stages', []),
        }
    return {'reference_version': reference['version'], 'kind': observations['kind'],
            'cells': reports, 'missing_cells': sorted(set(expected) - set(reports))}


def replay(reference, filters_path, role_scope='title'):
    """Exercise the selected source's pure filters; this is not a search run."""
    if role_scope not in ('title', 'title_or_description', 'title_or_affirmative_description'):
        raise ValueError('Unknown role scope')
    spec = importlib.util.spec_from_file_location('quality_candidate_filters', filters_path)
    filters = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(filters)
    cells = []
    for cell in reference['cells']:
        ranked = [job['id'] for job in cell['jobs']
                  if (filters.role_matches(job['title'], [cell['role']])
                      or (role_scope == 'title_or_description'
                          and filters.role_matches(str(job.get('description') or '')[:50_000],
                                                   [cell['role']]))
                      or (role_scope == 'title_or_affirmative_description'
                          and affirmative_role(job.get('description'), cell['role'])))
                  and filters.detail_location_matches(job, [cell['location']])]
        cells.append({'id': cell['id'], 'ranked_ids': ranked,
                      'unmeasured_stages': ['discovery', 'detail_extraction', 'stale_check',
                                            'deduplication', 'scoring', 'network_latency', 'request_cost']})
    return {'reference_version': reference['version'], 'kind': 'synthetic_filter_replay_fixture_order',
            'filter_sha256': digest(filters_path), 'role_scope': role_scope,
            'evaluator_sha256': digest(__file__),
            'role_intent_sha256': digest(Path(__file__).with_name('search_role_intent.py')),
            'cells': cells}


def compare(before, after, thresholds):
    if before['reference_version'] != after['reference_version'] or before['kind'] != after['kind']:
        raise ValueError('Incomparable reports')
    if before['missing_cells'] or after['missing_cells'] or before['cells'].keys() != after['cells'].keys():
        raise ValueError('Incomplete comparison')
    failures, gains = [], []
    metrics = ['precision_at_10', 'known_set_recall']
    if 'max_precision_all_returned_drop' in thresholds:
        metrics.append('precision_all_returned')
    for cell_id, old in before['cells'].items():
        new = after['cells'][cell_id]
        for metric in metrics:
            if old[metric] is None or new[metric] is None:
                raise ValueError('Missing comparison metric')
            if new[metric] < old[metric] - thresholds['max_' + metric + '_drop']:
                failures.append(cell_id + ':' + metric)
        gains.append(new['known_set_recall'] - old['known_set_recall'])
    improvement = max(gains, default=0) >= thresholds['min_one_cell_recall_gain']
    return {'non_regression_pass': not failures, 'regressions': failures,
            'improvement_pass': improvement, 'accepted': not failures and improvement}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--filters', type=Path)
    source.add_argument('--observations', type=Path)
    parser.add_argument('--before', type=Path)
    parser.add_argument('--role-scope', choices=('title', 'title_or_description',
                                               'title_or_affirmative_description'),
                        help='Offline filter experiment only; default: title')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.observations and args.role_scope:
        parser.error('--role-scope requires --filters')
    reference = json.loads(args.reference.read_text(encoding='utf-8'))
    observations = replay(reference, args.filters, args.role_scope or 'title') if args.filters else json.loads(
        args.observations.read_text(encoding='utf-8'))
    report = evaluate(reference, observations)
    report['reference_sha256'] = digest(args.reference)
    report['observations'] = observations
    report['thresholds'] = reference['thresholds']
    if args.before:
        before = json.loads(args.before.read_text(encoding='utf-8'))
        if before['reference_sha256'] != report['reference_sha256']:
            raise ValueError('Reference or thresholds changed; establish a new baseline')
        report['comparison'] = compare(before, report, reference['thresholds'])
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()
