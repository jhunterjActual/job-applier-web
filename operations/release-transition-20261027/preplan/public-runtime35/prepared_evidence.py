"""UNAPPLIED dispatcher. Source/runtime validation stays in original host gate."""
import prepared_types as a
import prepared98_history as history
import prepared_reader as fixed_reader
import prepared_sources as source_and_failure
import prepared97_context
import prepared97_history

def inspect_prepared(plan, revision, protected_read, git_blob, registry_raw, *, registry97_raw=None):
    a.validate_kind_plan(plan)
    a.need(revision in a.KINDS)
    if revision==a.TARGET:
        a.need(type(registry97_raw) is bytes)
        context=prepared97_context.collect(plan,protected_read,git_blob,registry97_raw,registry_raw)
        result=prepared97_history.inspect_history(**context)
        a.need(type(result) is dict and set(result)=={'runtime_map','historical'})
        historical=result['historical']
        a.need(historical['authorizes_preparation'] is False
               and historical['requires_current_source_runtime_equality'] is True
               and historical['revision']==revision and historical['source_kind']==a.KINDS[revision]
               and historical['receipt_sha256']==plan['prepared_receipts'][revision]
               and historical['accepted98_receipt_sha256']==plan['prepared_receipts'][a.CORRECTION]
               and historical['ops_commit']==prepared97_context.OPS97)
        a.digest(historical['runtime_manifest_sha256'])
        return {'historical_metadata_verified':True,'runtime_map':result['runtime_map'],
            'runtime_manifest_sha256':historical['runtime_manifest_sha256'],
            'requires_original_current_source_authority_runtime_guards':True,
            'source_kind':a.KINDS[revision]}
    bundle=fixed_reader.snapshot_protected_history(protected_read,registry_raw)
    inspected=history.inspect_dag(bundle,plan,registry_raw)
    a.need(inspected['historical_dag_inspected'] is True)
    source_and_failure.inspect_git_sources(bundle['source-manifest.json'],registry_raw,git_blob)
    source_and_failure.inspect_original_failure(bundle['old-failure-chain.json'],protected_read)
    proof=a.inspect_repair98(bundle['accepted.json'],plan)
    runtime=a.decode(bundle['runtime-manifest.json'])
    return {'historical_metadata_verified':True,'runtime_map':runtime,
        'runtime_manifest_sha256':proof['runtime_manifest_sha256'],
        'requires_original_current_source_authority_runtime_guards':True,
        'source_kind':a.KINDS[revision]}
