"""Local initial-plan replacement proposal. No selection, archive or host writes.

This deliberately cannot produce a packet accepted by the installed selector.
Host inventories, locks and cross-host interruption recovery are unresolved gates.
"""
import hashlib
import re

import admission_operations as ops
import identity_plan_packet as packet
from factor_release_packet import SCHEMA


REQUIRED_EVIDENCE=(
    'exact_selected_pointer_and_complete_archive_lineage_on_both_hosts',
    'current_application_and_operations_and_protected_configuration_hashes',
    'database_exact0014_policy_generation_and_identity_schema_absence',
    'closed_signup_maintenance_quiesced_callers_and_stopped_services',
    'complete_old_operation_attempt_candidate_receipt_and_transfer_inventory',
    'no_old_migration_provider_mapping_transition_or_activation_effect',
    'fresh_operation_ids_and_owner_proofs_in_reviewed0021_packet',
    'exact_release_preparation_restore_and_compatible_fallback_receipts',
    'cross_host_locks_compare_and_select_and_lost_ack_reconciliation',
    'explicit_protected_delivery_and_selection_authorization')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(value):
    if not value: raise ValueError('Initial plan replacement proposal rejected.')


def propose(selection_raw,archive_raw,target_source):
    """Bind local evidence only. Keep input packets private; output uses hashes.

    A source receipt is not release or host attestation. No caller-supplied flags
    may claim a prior operation is unstarted. Only version1 initial plans qualify
    for this proposal; existing recovery/supersession evidence takes another path.
    """
    try:
        require(type(selection_raw) is bytes and len(selection_raw)<=1024)
        require(type(archive_raw) is bytes and len(archive_raw)<=packet.LIMIT)
        selected=ops.strict_json(selection_raw)
        require(type(selected) is dict and set(selected)=={'version','digest','operation','sequence'})
        require(type(selected['version']) is int and selected['version']==1)
        require(type(selected['sequence']) is int and 1<=selected['sequence']<9223372036854775806)
        require(type(selected['digest']) is str and selected['digest']==digest(archive_raw))
        old=packet.validate(archive_raw)
        require(old['version']==1)
        packet.selected(archive_raw,selected['operation'])
        require(type(target_source) is dict and target_source.get('kind')=='factor_source_preflight_v1'
            and target_source.get('phase')=='local_source_checked_not_release_ready'
            and target_source.get('schema')==SCHEMA and target_source.get('execution_authorized') is False)
        manifest=target_source.get('source_manifest')
        require(type(manifest) is dict and bool(manifest))
        for name,value in manifest.items():
            require(type(name) is str and type(value) is str and re.fullmatch('[0-9a-f]{64}',value))
        import json
        actual=digest(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode())
        require(target_source.get('source_manifest_sha256')==actual)
        require(type(target_source.get('base')) is str and re.fullmatch('[0-9a-f]{40}',target_source['base']))
        require(type(target_source.get('build')) is str and re.fullmatch(r'\d{8}\.\d+',target_source['build']))
        body=dict(kind='initial_plan_replacement_proposal_v1',
            predecessor_selection_sha256=digest(selection_raw),predecessor_archive_sha256=digest(archive_raw),
            predecessor_sequence=selected['sequence'],target_source_manifest_sha256=actual,
            target_source_base=target_source['base'],target_source_build=target_source['build'],target_schema=SCHEMA,
            required_evidence=list(REQUIRED_EVIDENCE),execution_authorized=False,
            selection_change_supported=False,unstarted_verified=False)
        body['proposal_sha256']=digest(ops.encoded(body))
        return body
    except Exception:
        raise ValueError('Initial plan replacement proposal rejected.') from None


def validate_proposal(proposal):
    require(type(proposal) is dict and set(proposal)=={
        'kind','predecessor_selection_sha256','predecessor_archive_sha256',
        'predecessor_sequence','target_source_manifest_sha256','target_source_base',
        'target_source_build','target_schema','required_evidence','execution_authorized',
        'selection_change_supported','unstarted_verified','proposal_sha256'})
    for name in ('predecessor_selection_sha256','predecessor_archive_sha256',
                 'target_source_manifest_sha256','proposal_sha256'):
        require(type(proposal[name]) is str and re.fullmatch('[0-9a-f]{64}',proposal[name]))
    require(type(proposal['predecessor_sequence']) is int
        and 1<=proposal['predecessor_sequence']<9223372036854775806)
    require(type(proposal['target_source_base']) is str
        and re.fullmatch('[0-9a-f]{40}',proposal['target_source_base']))
    require(type(proposal['target_source_build']) is str
        and re.fullmatch(r'\d{8}\.\d+',proposal['target_source_build']))
    require(proposal['target_schema']==SCHEMA and proposal['required_evidence']==list(REQUIRED_EVIDENCE))
    body={key:value for key,value in proposal.items() if key!='proposal_sha256'}
    require(proposal['proposal_sha256']==digest(ops.encoded(body))
        and proposal['kind']=='initial_plan_replacement_proposal_v1'
        and proposal['execution_authorized'] is False
        and proposal['selection_change_supported'] is False
        and proposal['unstarted_verified'] is False)


def reconcile(proposal,*,observed_selection_sha256,attempt_state):
    """Read-only decision after interruption. Never infer success or retry safely.

    attempt_state comes from an independently verified journal inventory. This
    model cannot verify that inventory; no state authorizes an external action.
    """
    validate_proposal(proposal)
    require(attempt_state in ('absent','prepared','selection_write_started','unknown'))
    require(observed_selection_sha256 is None or type(observed_selection_sha256) is str
        and re.fullmatch('[0-9a-f]{64}',observed_selection_sha256))
    if attempt_state in ('selection_write_started','unknown') or observed_selection_sha256 is None:
        return 'stop_unknown_outcome_no_replay'
    if observed_selection_sha256!=proposal['predecessor_selection_sha256']:
        return 'stop_selection_changed_review_lineage'
    return 'proposal_only_live_evidence_required'
