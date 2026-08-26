#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this repository inventory check as root." >&2
    exit 1
fi

readonly config=/etc/careertrellis/rclone.conf
readonly source_path=r2:job-applier-staging-db-backups/job-applier/staging/postgresql
readonly destination_path=onedrive:job-applier-staging/postgresql
readonly rclone_binary=/usr/local/bin/rclone
readonly lock_path=/run/lock/careertrellis-pgbackrest-repository.lock
readonly core=/usr/local/libexec/careertrellis/backup_health.py
readonly receipt_root=/var/backups/careertrellis/backup-health/operations

for command_path in /usr/bin/flock /usr/bin/jq /usr/bin/pgbackrest /usr/bin/setpriv /usr/bin/python3; do
    if [[ ! -x ${command_path} ]]; then
        echo "A required inventory command is unavailable." >&2
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

umask 077
exec 9<>"${lock_path}"
if ! flock --nonblock 9; then
    echo "Another repository operation is active; this inventory was not started." >&2
    exit 75
fi

readonly started_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
readonly started_epoch=$(date -u +%s)
private_log=$(mktemp /run/careertrellis-pgbackrest-inventory.XXXXXX)
info_json=$(mktemp /run/careertrellis-pgbackrest-info.XXXXXX)
source_size_json=$(mktemp /run/careertrellis-pgbackrest-source-size.XXXXXX)
destination_size_json=$(mktemp /run/careertrellis-pgbackrest-destination-size.XXXXXX)
cleanup() {
    rm -f -- \
        "${private_log:-}" \
        "${info_json:-}" \
        "${source_size_json:-}" \
        "${destination_size_json:-}"
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

# Raw rclone output can include object names, so it is confined to a root-only
# temporary file and is never copied to a receipt or the systemd journal.
if ! ${rclone_binary} check \
    "${source_path}" \
    "${destination_path}" \
    --config "${config}" \
    --download \
    --one-way \
    --checkers 4 \
    --log-file "${private_log}" \
    --log-level INFO \
    --stats-one-line-date \
    --stats 5m; then
    echo "Repository inventory divergence was detected; private output was discarded." >&2
    exit 1
fi
if ! ${rclone_binary} size "${source_path}" --config "${config}" --json \
    >"${source_size_json}" 2>>"${private_log}" || \
    ! ${rclone_binary} size "${destination_path}" --config "${config}" --json \
    >"${destination_size_json}" 2>>"${private_log}"; then
    echo "The verified inventory could not be summarized safely." >&2
    exit 1
fi
read -r source_object_count source_byte_count < <(/usr/bin/jq -er '
    select((.count | type) == "number" and (.bytes | type) == "number")
    | [.count, .bytes]
    | @tsv
' "${source_size_json}") || {
    echo "The source inventory aggregate was invalid." >&2
    exit 1
}
read -r destination_object_count destination_byte_count < <(/usr/bin/jq -er '
    select((.count | type) == "number" and (.bytes | type) == "number")
    | [.count, .bytes]
    | @tsv
' "${destination_size_json}") || {
    echo "The destination inventory aggregate was invalid." >&2
    exit 1
}
for aggregate in \
    "${source_object_count}" \
    "${source_byte_count}" \
    "${destination_object_count}" \
    "${destination_byte_count}"; do
    if [[ ! ${aggregate} =~ ^[0-9]+$ ]]; then
        echo "An inventory aggregate was outside its accepted range." >&2
        exit 1
    fi
done

readonly completed_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
readonly elapsed_seconds=$(( $(date -u +%s) - started_epoch ))
printf '{"schema":"careertrellis.backup-health-operation/v1","operation":"pgbackrest_inventory","result":"ok","started_at":"%s","completed_at":"%s","backup_label":"%s","source_object_count":%s,"source_byte_count":%s,"destination_object_count":%s,"destination_byte_count":%s,"divergence_count":0,"elapsed_seconds":%d}\n' \
    "${started_at}" \
    "${completed_at}" \
    "${backup_label}" \
    "${source_object_count}" \
    "${source_byte_count}" \
    "${destination_object_count}" \
    "${destination_byte_count}" \
    "${elapsed_seconds}" \
    | /usr/bin/python3 -B "${core}" publish --receipt-root "${receipt_root}"
