"""Compare trusted local matchers on frozen synthetic document-context judgments."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_matcher(path):
    spec = importlib.util.spec_from_file_location('context_candidate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.affirmative_role


def measure(reference, matcher):
    ids = set()
    results = []
    for case in reference['cases']:
        if not isinstance(case['id'], str) or not case['id'] or case['id'] in ids:
            raise ValueError('Unique nonempty case IDs required')
        ids.add(case['id'])
        if type(case['expected']) is not bool:
            raise ValueError('Expected judgment must be boolean')
        if not isinstance(case['role'], str) or not case['role'].strip():
            raise ValueError('Explicit role required')
        if case['description'] is not None and not isinstance(case['description'], str):
            raise ValueError('Description must be text or null')
        matched = matcher(case['description'], case['role'])
        if type(matched) is not bool:
            raise ValueError('Matcher must return boolean')
        results.append(dict(id=case['id'], expected=case['expected'], matched=matched))
    tp = sum(row['expected'] and row['matched'] for row in results)
    fp = [row['id'] for row in results if not row['expected'] and row['matched']]
    fn = [row['id'] for row in results if row['expected'] and not row['matched']]
    return dict(cases=len(results), true_positives=tp, false_positives=fp,
                false_negatives=fn, precision=tp / (tp + len(fp)) if tp + len(fp) else None,
                recall=tp / (tp + len(fn)) if tp + len(fn) else None, results=results)


def compare_context(before, after):
    # Proposed offline screening floors, set before holdout comparison.
    thresholds = dict(max_false_positives=0, max_recall_drop=0, min_recall=.8)
    measurable = before['recall'] is not None and after['recall'] is not None
    passed = (measurable and not after['false_positives']
              and after['recall'] >= before['recall'] and after['recall'] >= .8)
    return dict(thresholds=thresholds, accepted=bool(passed),
                runtime_approved=False, scope='synthetic description-only screen')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Receipt exists; select a new path')
    reference = json.loads(args.reference.read_text(encoding='utf-8'))
    before = measure(reference, load_matcher(args.before))
    after = measure(reference, load_matcher(args.candidate))
    result = dict(reference_version=reference['version'], reference_sha256=digest(args.reference),
                  before_sha256=digest(args.before), candidate_sha256=digest(args.candidate),
                  evaluator_sha256=digest(__file__), before=before, after=after,
                  comparison=compare_context(before, after))
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({name: {key: value for key, value in report.items() if key != 'results'}
                      for name, report in result.items() if name in ('before', 'after', 'comparison')}))


if __name__ == '__main__':
    main()