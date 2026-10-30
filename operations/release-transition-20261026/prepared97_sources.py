"""Draft fixed public Git-source snapshot; no subprocess or default IO."""
import re
import prepared_types as a

PREFIX = 'operations/release-transition-20261027/corrected97/'
NAMES = frozenset({'qualified_specs.py', 'materialize97.py',
    'accepted98_dependency.py', 'source_boundary.py', 'qualified_route.py',
    'admission97.py', 'native_assertions97.py', 'run_checks_qualified97.py',
    'native97_unit.py', 'checks97.py', 'qualified97_records.py',
    'qualified_entry.py', 'historical98_controls.py'})
PREPARATION_OPS = '291d1b4b58bb17448395012408556090bea5ede1'
PREPARATION_PATH = 'operations/release-transition-20261026/preparation/prepare_release.py'
SHARED_PATH = 'operations/release-transition-20261027/repair98/repair_contract.py'
SHARED_SHA = '6eacc5f98edd31dfecf209f8e0692ab5ec993396fde6990e42e547cde598ce8f'


def snapshot(approved_ops_commit, manifest_raw, accepted98_registry_raw, git_blob):
    """The caller must bind approved_ops_commit to its reviewed packet registry.

    Manifest self-consistency alone never chooses an approved revision. All
    callbacks receive literal commit/path pairs assembled here and must use the
    original reviewed read-only Git route.
    """
    a.need(type(approved_ops_commit) is str and
           re.fullmatch('[0-9a-f]{40}', approved_ops_commit))
    a.need(type(accepted98_registry_raw) is bytes and a.sha(accepted98_registry_raw) ==
           '18c97f07801ca83c05d1c434be829b5d894ffcd8dd7d905715e0bebc805a4901')
    manifest = a.decode(manifest_raw)
    a.need(type(manifest) is dict and set(manifest) == {'schema','kind','ops_commit','files'}
           and manifest['schema'] == 'careertrellis.corrected97-source-manifest/v1'
           and manifest['kind'] == 'corrected-fixture97-new/v1'
           and manifest['ops_commit'] == approved_ops_commit
           and type(manifest['files']) is dict and set(manifest['files']) == NAMES)
    requests = {}
    for name, row in manifest['files'].items():
        a.need(type(row) is dict and set(row) == {'bytes','sha256'}
               and type(row['bytes']) is int and 0 < row['bytes'] <= 1048576)
        a.digest(row['sha256'])
        requests['qualified97/' + name] = (approved_ops_commit, PREFIX + name)
    requests['target97/backend/test_p0_regressions.py'] = (a.TARGET, 'backend/test_p0_regressions.py')
    requests['target97/scripts/value_evaluation/test_campaign_transport.py'] = (a.TARGET, 'scripts/value_evaluation/test_campaign_transport.py')
    requests['published-registry'] = (approved_ops_commit, PREFIX+'SOURCE_REGISTRY.json')
    requests['target97/deploy-release.sh'] = (a.TARGET, 'deploy/scripts/deploy-release.sh')
    requests['preparation291d/prepare_release.py'] = (PREPARATION_OPS, PREPARATION_PATH)
    requests['shared-contract'] = (approved_ops_commit, SHARED_PATH)
    raw = {key: git_blob(*pair) for key, pair in requests.items()}
    for key, value in raw.items():
        a.need(type(value) is bytes and 0 < len(value) <= 1048576)
        if key.startswith('qualified97/'):
            row = manifest['files'][key.split('/', 1)[1]]
            a.need(len(value) == row['bytes'] and a.sha(value) == row['sha256'])
    a.need(a.sha(raw['shared-contract']) == SHARED_SHA)
    for key, pair in requests.items():
        a.need(git_blob(*pair) == raw[key])
    a.need(a.sha(raw['published-registry'])=='7e28cdde03df61fad511d03fab77235fc458b22df222edfb0d78069fc2f8b5cf')
    del raw['published-registry']
    del raw['shared-contract']
    raw['repair98/registry.snapshot.json'] = accepted98_registry_raw
    return raw
