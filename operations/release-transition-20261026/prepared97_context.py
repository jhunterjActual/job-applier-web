"""UNAPPLIED fixed97 consumer input assembly; no readiness or receipt output.

The original transaction validator and authenticated packet loader must run
first. Protected snapshots remain in memory and are never serialized/logged.
"""
import prepared_types as a
import prepared_reader as fixed_reader
import prepared97_reader
import prepared97_sources
import prepared_sources as source_and_failure

OPS97 = 'a62e3f1269df9390d075a263385dc024b2623d6a'


def collect(application_plan, protected_read, git_blob, registry97_raw, registry98_raw):
    a.validate_kind_plan(application_plan)
    a.need(type(registry97_raw) is bytes and 0 < len(registry97_raw) <= 1048576)
    # No receipt is permitted to choose a path, source revision or fallback.
    bundle = prepared97_reader.snapshot(protected_read)
    accepted98 = fixed_reader.snapshot_protected_history(protected_read, registry98_raw)
    source97 = prepared97_sources.snapshot(OPS97, bundle['source-manifest.json'],
                                          registry98_raw, git_blob)
    source98 = source_and_failure.inspect_git_sources(
        accepted98['source-manifest.json'], registry98_raw, git_blob,
        return_bytes=True)
    # Historical98 is a real dependency of97. Keep the original failed native
    # attempt checks even though the consumer itself deliberately has no IO.
    source_and_failure.inspect_original_failure(accepted98['old-failure-chain.json'],
                                               protected_read)
    binding = {'source_kind':'corrected-fixture97-new/v1', 'revision':a.TARGET,
        'build':'20261026.97',
        'receipt_sha256':application_plan['prepared_receipts'][a.TARGET],
        'ops_commit':OPS97, 'registry_sha256':a.sha(registry97_raw),
        'accepted98_receipt_sha256':application_plan['prepared_receipts'][a.CORRECTION]}
    return {'bundle':bundle, 'binding':binding, 'source_bytes':source97,
        'accepted98_bundle':accepted98, 'accepted98_source_bytes':source98,
        'registry_raw':registry97_raw}
