"""Pure local rollout bindings; no host attestation or executable plan output.

Inputs are private. Supplied bytes/hashes do not establish Git provenance,
archive contents, host state, operation freshness on a host, or authorization.
"""
from copy import deepcopy
import hashlib
import re

import admission_operations as ops
import factor_release_packet as source
import identity_plan_packet as packet
import identity_selection_journal as journal
import session_v2_transition as transition

ERROR = 'Schema0021 rollout contract rejected.'
SCHEMA = '20260929_0021'
PREDECESSOR = '20260907_0014'
DEPLOYED_BUILD = '20260928.78'
PENDING = ('verified_archive_contents_and_immutable_commit_provenance',
    'protected_host_inventory_and_no_prior_operation_effects',
    'durable_two_host_selection_and_independent_consumer_acceptance',
    'cohesive_schema21_operations_configuration_and_owner_recovery',
    'quiescence_migration_and_compatible_fallback_evidence',
    'separately_authorized_deployment_and_attended_acceptance')


def require(value):
    if not value:
        raise ValueError(ERROR)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def operation_ids(plan):
    return {plan[name]['provider']['operation'] for name in ('bind', 'switch')} | set(
        plan['switch']['provider']['transitions'])


def validate(raw, *, predecessor_selection, predecessor_archive, source_files, release_archive):
    """Validate supplied local evidence only; return hashes, never a live packet.

    The historical packet validator is reused for pair/generation constraints
    through a private structural projection. The actual target must be0021;
    no existing operational parser or schema allowlist is modified.
    """
    try:
        require(type(raw) is bytes and len(raw) <= packet.LIMIT)
        value = ops.strict_json(raw)
        require(type(value) is dict and set(value) == {'version','kind','predecessor_schema',
            'target_schema','application','operations','build','archive_sha256',
            'source_manifest_sha256','predecessor_selection_sha256',
            'predecessor_archive_sha256','plan','execution_authorized'})
        require(type(value['version']) is int and value['version'] == 1)
        require(value['kind'] == 'schema21_rollout_contract_v1'
            and value['predecessor_schema'] == PREDECESSOR and value['target_schema'] == SCHEMA
            and value['execution_authorized'] is False)
        for name in ('application','operations'):
            require(type(value[name]) is str and re.fullmatch('[0-9a-f]{40}',value[name]))
        for name in ('archive_sha256','source_manifest_sha256',
                     'predecessor_selection_sha256','predecessor_archive_sha256'):
            require(type(value[name]) is str and re.fullmatch('[0-9a-f]{64}',value[name]))
        require(type(value['build']) is str and re.fullmatch(r'\d{8}\.\d+',value['build'])
            and value['build'] != DEPLOYED_BUILD)
        selected = journal.pointer(predecessor_selection)
        old = packet.validate(predecessor_archive)
        require(old['version'] == 1 and selected['digest'] == sha(predecessor_archive))
        packet.selected(predecessor_archive,selected['operation'])
        require(value['predecessor_selection_sha256'] == sha(predecessor_selection)
            and value['predecessor_archive_sha256'] == sha(predecessor_archive))
        require(type(release_archive) is bytes and 0 < len(release_archive) <= 128*1024*1024)
        require(value['archive_sha256'] == sha(release_archive))
        evidence = source.source_packet(source_files,base=value['application'])
        require(evidence['schema'] == SCHEMA and evidence['build'] == value['build']
            and evidence['source_manifest_sha256'] == value['source_manifest_sha256'])
        plan = value['plan']
        require(type(plan) is dict and set(plan) == {'version','bind','switch'}
            and type(plan['version']) is int and plan['version'] == 1)
        projection = deepcopy(plan)
        for name in ('bind','switch'):
            require(type(plan[name]) is dict and set(plan[name]) == {'provider','activation'})
            activation = plan[name]['activation']
            require(type(activation) is dict and activation.get('schema') == SCHEMA)
            projection[name]['activation']['schema'] = transition.HEAD
        packet.validate(ops.encoded(projection))
        require(not operation_ids(plan).intersection(operation_ids(old)))
        for name in ('bind','switch'):
            provider, activation = plan[name]['provider'],plan[name]['activation']
            require(provider['application'] == value['application']
                and provider['operations'] == value['operations'])
            require(provider['source_application'] == old['bind']['provider']['source_application'])
            require(provider['baseline_binding'] == old['bind']['provider']['baseline_binding'])
            require(provider['application'] != provider['source_application'])
            require(activation['session_seconds'] == 3600)
            # Replace release/schema and operation identifiers, not the
            # predecessor's identity scope. Transition identifiers are fresh,
            # but their count does not establish any actual account mapping.
            prior_provider, prior_activation = old[name]['provider'],old[name]['activation']
            for field in ('mode','baseline_binding','target_binding','expected_generation',
                          'quarantined_accounts'):
                require(provider[field] == prior_provider[field])
            require(len(provider['transitions']) == len(prior_provider['transitions']))
            for field in ('realm','cap','ttl','session_seconds','provider_binding'):
                require(activation[field] == prior_activation[field])
        require(plan['bind']['provider']['expected_generation'] == old['bind']['provider']['expected_generation'])
        # Proof digests are paired by packet validation. No claim is made that
        # the supplied digest proves ownership or any current provider state.
        return dict(kind='schema21_rollout_contract_receipt_v1',
            contract_sha256=sha(ops.encoded(value)),application=value['application'],
            operations=value['operations'],build=value['build'],schema=SCHEMA,
            archive_sha256=value['archive_sha256'],source_manifest_sha256=value['source_manifest_sha256'],
            predecessor_selection_sha256=value['predecessor_selection_sha256'],
            predecessor_archive_sha256=value['predecessor_archive_sha256'],
            target_plan_sha256=sha(ops.encoded(plan)),pending=list(PENDING),
            execution_authorized=False,host_state_verified=False,consumers_enabled=False)
    except Exception:
        raise ValueError(ERROR) from None
