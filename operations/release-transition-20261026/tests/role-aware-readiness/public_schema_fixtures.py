from datetime import datetime,timezone,timedelta
UTC=timezone.utc
UTC = timezone.utc
OBSERVED = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
FULL_LABEL = '20260824-030000F'
LATEST_LABEL = '20260824-030000F_20260825-030000D'

def timestamp(value: datetime) -> str:
    return value.strftime('%Y-%m-%dT%H:%M:%SZ')

def operation_receipt(operation: str, *, completed: datetime | None=None, result: str='ok') -> dict[str, object]:
    completed = completed or OBSERVED - timedelta(hours=1)
    common: dict[str, object] = {'schema': backup_health.RECEIPT_SCHEMA, 'operation': operation, 'result': result, 'started_at': timestamp(completed - timedelta(minutes=10)), 'completed_at': timestamp(completed)}
    fields: dict[str, dict[str, object]] = {'full_backup': {'backup_label': FULL_LABEL, 'elapsed_seconds': 600}, 'differential_backup': {'backup_label': LATEST_LABEL, 'elapsed_seconds': 600}, 'pgbackrest_mirror': {'backup_label': LATEST_LABEL, 'mirrored_backup_label': LATEST_LABEL, 'object_count': 100, 'byte_count': 200, 'elapsed_seconds': 600}, 'pgbackrest_inventory': {'backup_label': LATEST_LABEL, 'source_object_count': 100, 'source_byte_count': 200, 'destination_object_count': 100, 'destination_byte_count': 200, 'divergence_count': 0, 'elapsed_seconds': 600}, 'pgbackrest_restore': {'backup_label': LATEST_LABEL, 'elapsed_seconds': 600, 'rpo_seconds': 3600, 'rto_seconds': 500, 'promotion_verified': True, 'checksum_verified': True, 'cleanup_verified': True}, 'generated_object_mirror': {'object_count': 30, 'byte_count': 60, 'divergence_count': 0, 'elapsed_seconds': 300}, 'generated_object_restore': {'object_count': 30, 'byte_count': 60, 'elapsed_seconds': 300, 'manifest_verified': True, 'ciphertext_verified': True, 'target_left_intact': True}}
    common.update(fields[operation])
    return common

def snapshot(surface: str) -> dict[str, object]:
    operations = backup_health.SURFACE_OPERATIONS[surface]
    document: dict[str, object] = {'schema': backup_health.SNAPSHOT_SCHEMA, 'surface': surface, 'observed_at': timestamp(OBSERVED), 'units': {operation: {'last_result': 'success', 'missed': False} for operation in operations}}
    if surface == 'database':
        document.update({'latest_backup_label': LATEST_LABEL, 'wal': {'failed_count': 0, 'previous_failed_count': 0, 'segment_lag': 1, 'repository_archive_max_behind_database': False}})
    return document
