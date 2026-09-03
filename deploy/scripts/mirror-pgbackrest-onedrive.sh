#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this OneDrive mirror as root." >&2
    exit 1
fi

readonly config=/etc/careertrellis/rclone.conf
readonly source_path=r2:job-applier-staging-db-backups/job-applier/staging/postgresql
readonly destination_path=onedrive:job-applier-staging/postgresql
readonly rclone_binary=/usr/local/bin/rclone
readonly lock_path=/run/lock/careertrellis-pgbackrest-repository.lock
readonly core=/usr/local/libexec/careertrellis/backup_health.py
readonly receipt_root=/var/backups/careertrellis/backup-health/operations
readonly -a immutable_payload_filters=(
    --exclude /archive/careertrellis/archive.info
    --exclude /archive/careertrellis/archive.info.copy
    --exclude /backup/careertrellis/backup.info
    --exclude /backup/careertrellis/backup.info.copy
    --exclude /backup/careertrellis/latest
)
readonly -a mutable_metadata_filters=(
    --include /archive/careertrellis/archive.info
    --include /archive/careertrellis/archive.info.copy
    --include /backup/careertrellis/backup.info
    --include /backup/careertrellis/backup.info.copy
    --include /backup/careertrellis/latest
)

for command_path in /usr/bin/flock /usr/bin/grep /usr/bin/jq /usr/bin/pgbackrest /usr/bin/setpriv /usr/bin/python3; do
    if [[ ! -x ${command_path} ]]; then
        echo "A required mirror command is unavailable." >&2
        exit 1
    fi
done

as_postgres() {
    /usr/bin/setpriv --reuid=postgres --regid=postgres --init-groups \
        --reset-env -- "$@"
}
if [[ ! -x ${rclone_binary} ]]; then
    echo "The verified rclone installation is unavailable." >&2
    exit 1
fi
if [[ -L ${config} || $(stat -c '%a:%U:%G' "${config}" 2>/dev/null || true) != 600:root:root ]]; then
    echo "The rclone configuration must be a root-owned, non-symlink 0600 file." >&2
    exit 1
fi
if [[ -L ${core} || ! -f ${core} || ! -r ${core} ]]; then
    echo "The reviewed backup-health core is unavailable." >&2
    exit 1
fi
if [[ -L ${lock_path} || ! -f ${lock_path} || \
    $(stat -c '%a:%U:%G' "${lock_path}" 2>/dev/null || true) != 600:root:root ]]; then
    echo "The canonical repository lock has an unsafe owner, mode, or type." >&2
    exit 1
fi

remote_inventory=$(${rclone_binary} listremotes --config "${config}" 2>/dev/null) || {
    echo "The configured recovery remotes could not be inspected." >&2
    exit 1
}
for remote_name in r2: onedrive:; do
    if [[ $'\n'${remote_inventory}$'\n' != *$'\n'${remote_name}$'\n'* ]]; then
        echo "A required recovery remote is not configured." >&2
        exit 1
    fi
done
unset remote_inventory

umask 077
exec 9<>"${lock_path}"
if ! flock --nonblock 9; then
    echo "Another repository operation is active; this mirror was not started." >&2
    exit 75
fi

readonly started_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
readonly started_epoch=$(date -u +%s)
private_log=$(mktemp /run/careertrellis-pgbackrest-mirror.XXXXXX)
info_json=$(mktemp /run/careertrellis-pgbackrest-info.XXXXXX)
size_json=$(mktemp /run/careertrellis-pgbackrest-size.XXXXXX)
cleanup() {
    rm -f -- "${private_log:-}" "${info_json:-}" "${size_json:-}"
}
trap cleanup EXIT

if ! as_postgres /usr/bin/pgbackrest \
    --stanza=careertrellis \
    --output=json \
    info >"${info_json}" 2>"${private_log}"; then
    echo "The source repository could not be bound to a backup label." >&2
    exit 1
fi
backup_label=$(/usr/bin/jq -er '
    .[0]
    | select(.name == "careertrellis" and .status.code == 0)
    | .backup
    | sort_by(.timestamp.stop)
    | last.label
' "${info_json}") || {
    echo "The source repository has no accepted backup label." >&2
    exit 1
}
if [[ ! ${backup_label} =~ ^[0-9]{8}-[0-9]{6}F(_[0-9]{8}-[0-9]{6}[DI])?$ ]]; then
    echo "The source repository returned an invalid backup label." >&2
    exit 1
fi

# Backup sets, manifests, and WAL payloads are immutable. The two redundant
# archive/backup info pairs and optional latest pointer are pgBackRest's only
# reviewed mutable repository paths. Keep the payload append-only, then refresh
# and download-check only that fixed metadata allowlist. A changed payload path
# still fails closed instead of being propagated to the independent mirror.
if ! ${rclone_binary} copy \
    "${source_path}" \
    "${destination_path}" \
    --config "${config}" \
    "${immutable_payload_filters[@]}" \
    --immutable \
    --checkers 4 \
    --transfers 2 \
    --create-empty-src-dirs \
    --log-file "${private_log}" \
    --log-level INFO \
    --stats-one-line-date \
    --stats 5m; then
    if /usr/bin/grep -Eqi 'immutable file modified|do not match.*immutable' "${private_log}"; then
        echo "The independent mirror detected a changed repository payload; private output was discarded." >&2
        exit 1
    fi
    echo "The append-only repository mirror failed; private output was discarded." >&2
    exit 1
fi
if ! ${rclone_binary} copy \
    "${source_path}" \
    "${destination_path}" \
    --config "${config}" \
    "${mutable_metadata_filters[@]}" \
    --checkers 4 \
    --transfers 2 \
    --log-file "${private_log}" \
    --log-level INFO \
    --stats-one-line-date \
    --stats 5m; then
    echo "The reviewed repository metadata refresh failed; private output was discarded." >&2
    exit 1
fi
if ! ${rclone_binary} check \
    "${source_path}" \
    "${destination_path}" \
    --config "${config}" \
    "${mutable_metadata_filters[@]}" \
    --download \
    --one-way \
    --checkers 4 \
    --log-file "${private_log}" \
    --log-level INFO \
    --stats-one-line-date \
    --stats 5m; then
    echo "The refreshed repository metadata did not pass downloaded verification; private output was discarded." >&2
    exit 1
fi
if ! ${rclone_binary} size \
    "${source_path}" \
    --config "${config}" \
    --json >"${size_json}" 2>>"${private_log}"; then
    echo "The completed mirror could not be summarized safely." >&2
    exit 1
fi
read -r object_count byte_count < <(/usr/bin/jq -er '
    select((.count | type) == "number" and (.bytes | type) == "number")
    | [.count, .bytes]
    | @tsv
' "${size_json}") || {
    echo "The mirror aggregate was invalid." >&2
    exit 1
}
if [[ ! ${object_count} =~ ^[0-9]+$ || ! ${byte_count} =~ ^[0-9]+$ ]]; then
    echo "The mirror aggregate was outside its accepted range." >&2
    exit 1
fi

readonly completed_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
readonly elapsed_seconds=$(( $(date -u +%s) - started_epoch ))
printf '{"schema":"careertrellis.backup-health-operation/v1","operation":"pgbackrest_mirror","result":"ok","started_at":"%s","completed_at":"%s","backup_label":"%s","mirrored_backup_label":"%s","object_count":%s,"byte_count":%s,"elapsed_seconds":%d}\n' \
    "${started_at}" \
    "${completed_at}" \
    "${backup_label}" \
    "${backup_label}" \
    "${object_count}" \
    "${byte_count}" \
    "${elapsed_seconds}" \
    | /usr/bin/python3 -B "${core}" publish --receipt-root "${receipt_root}"
