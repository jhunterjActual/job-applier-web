"""UNAPPLIED source-only adapter draft. No host IO, runtime wiring or admission."""
import hashlib
import json
import re
from datetime import datetime

TARGET = 'bc68ea0dc253425a44e268452130becdf35d764e'
CORRECTION = 'f70417d4bd70c3eb086ea63c90938dd2f43e0b99'
BASE = '7a4d3f077c4da21f2a315cbbcf4fd926d91151f4'
PREVIOUS = '4095417909a8b13ad252be9420e422af3662bef5'
KINDS = {TARGET: 'corrected-fixture97-new/v1', CORRECTION: 'repair98-retained/v1'}
REPAIR_ROOT = '/var/backups/careertrellis/preparation98-repair-20261027/repair98-fixture-g1/'
CHECKS = {'p0', 'objects', 'ai', 'syntax', 'authority_native', 'systemd_native', 'runtime_isolation'}
QUALIFICATION_KEYS = {'schema', 'test_id', 'calls', 'constructor_returned', 'method_returned',
    'binding_restored', 'analyzer_returncodes', 'serial_suite_verified', 'runtime_code_verified',
    'fixture_ancestors_verified', 'module_sha256', 'method_source_sha256', 'method_ast_sha256', 'qualification'}
RECEIPT_KEYS = {'schema', 'result', 'release', 'build', 'baseline', 'previous', 'repair_attempt_id',
    'repair_ops_commit', 'source_manifest_sha256', 'repair_plan_sha256', 'repair_decision_sha256',
    'candidate_sha256', 'source_archive_sha256', 'source_diff_sha256', 'runner_sha256', 'qualifier_sha256',
    'old_failure_chain_sha256', 'full_live_preflight_sha256', 'retained_runtime_before_sha256',
    'retained_runtime_after_sha256', 'runtime_manifest', 'runtime_manifest_sha256', 'fixture_qualification',
    'checks', 'checks_sha256', 'unit_terminal_sha256', 'owner_terminal_sha256', 'postterminal_proof_sha256',
    'completed_utc', 'activated'}

class Refused(RuntimeError):
    pass

def need(value):
    if not value:
        raise Refused('PREPARED_ADAPTER_DRAFT_REFUSED')

def sha(raw):
    need(type(raw) is bytes)
    return hashlib.sha256(raw).hexdigest()

def pairs(items):
    result = {}
    for key, value in items:
        need(key not in result)
        result[key] = value
    return result

def decode(raw):
    need(type(raw) is bytes and len(raw) <= 16 * 1024 * 1024)
    return json.loads(raw.decode('utf8'), object_pairs_hook=pairs,
        parse_constant=lambda value: need(False))

def digest(value):
    need(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) and value != '0' * 64)

def validate_kind_plan(plan):
    """Additional closed-plan constraint; does NOT replace original validate_plan."""
    need(type(plan) is dict and type(plan.get('prepared_receipt_kinds')) is dict
         and plan['prepared_receipt_kinds'] == KINDS)
    need(type(plan.get('prepared_receipts')) is dict and set(plan['prepared_receipts']) == set(KINDS))
    for value in plan['prepared_receipts'].values():
        digest(value)

def validate_qualification(q):
    need(type(q) is dict and set(q) == QUALIFICATION_KEYS)
    need(q['schema'] == 'careertrellis.exact-fixture-relocation/v1'
         and q['test_id'] == 'test_p0_regressions.DependencyLockTests.test_backup_health_units_resolve_only_staged_executables'
         and type(q['calls']) is int and q['calls'] == 1)
    for key in ('constructor_returned', 'method_returned', 'binding_restored', 'serial_suite_verified',
                'runtime_code_verified', 'fixture_ancestors_verified'):
        need(q[key] is True)
    need(q['module_sha256'] == '341e728a03f4f76bfa04c64aec612fae9fd87dc831266e547d9f42c7f0a42ab4'
         and q['method_source_sha256'] == '584aec03af3f6347fbce3f10f56b449deeea0925f3365e6f28a5d3cfcbd85cac'
         and q['method_ast_sha256'] == 'ad2abbb01e5d212e3045b604105c2dc788281d56d64735887a827caad841af73')
    codes = q['analyzer_returncodes']
    need(type(codes) is list and len(codes) == 2 and all(type(code) is int for code in codes)
         and codes[0] == 0 and 1 <= codes[1] <= 255)
    need(q['qualification'] == 'one TemporaryDirectory dir relocated; unchanged body and real positive/negative analyzer calls')

def inspect_repair98(raw, plan):
    """Typed inspection only. Its return value NEVER authorizes preparation."""
    validate_kind_plan(plan)
    need(sha(raw) == plan['prepared_receipts'][CORRECTION])
    value = decode(raw)
    need(type(value) is dict and set(value) == RECEIPT_KEYS)
    need(value['schema'] == 'careertrellis.preparation98-repair-acceptance/v1'
         and value['result'] == 'qualified_prepared_inactive'
         and value['release'] == CORRECTION and value['build'] == '20261026.98'
         and value['baseline'] == BASE and value['previous'] == PREVIOUS
         and value['repair_attempt_id'] == 'repair98-fixture-g1-20261027'
         and value['activated'] is False)
    need(type(value['repair_ops_commit']) is str and re.fullmatch('[0-9a-f]{40}', value['repair_ops_commit'])
         and value['repair_ops_commit'] not in ('0' * 40, '291d1b4b58bb17448395012408556090bea5ede1'))
    for key in RECEIPT_KEYS:
        if key.endswith('_sha256'):
            digest(value[key])
    need(value['source_diff_sha256'] == plan['source_diffs'][CORRECTION]
         and value['retained_runtime_before_sha256'] == value['retained_runtime_after_sha256']
         and value['runtime_manifest'] == REPAIR_ROOT + 'runtime-manifest.json')
    need(type(value['checks']) is dict and set(value['checks']) == CHECKS
         and all(check is True for check in value['checks'].values()))
    stamp = datetime.fromisoformat(value['completed_utc'].replace('Z', '+00:00'))
    need(stamp.tzinfo is not None)
    validate_qualification(value['fixture_qualification'])
    return value

def verify_prepared_draft(*args, **kwargs):
    # Mandatory hold: final closed nested registry, exact qualified97 receipt,
    # protected chain reader and original complete source/runtime guards are
    # not integrated/reviewed yet. No caller flag can enable this draft.
    raise Refused('FINAL_NESTED_REGISTRY_AND_QUALIFIED97_NOT_REVIEWED')
