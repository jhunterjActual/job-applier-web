#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this pgBackRest backup wrapper as root." >&2
    exit 1
fi

readonly operation=${1:-}
case "${operation}" in
    full_backup)
        readonly backup_type=full
        readonly expected_info_type=full
        ;;
    differential_backup)
        readonly backup_type=diff
        readonly expected_info_type=diff
        ;;
    *)
        echo "Usage: $0 full_backup|differential_backup" >&2
        exit 2
        ;;
esac

readonly stanza=careertrellis
readonly lock_path=/run/lock/careertrellis-pgbackrest-repository.lock
readonly core=/usr/local/libexec/careertrellis/backup_health.py
readonly receipt_root=/var/backups/careertrellis/backup-health/operations

for command_path in /usr/bin/flock /usr/bin/jq /usr/bin/pgbackrest /usr/bin/setpriv /usr/bin/python3; do
    if [[ ! -x ${command_path} ]]; then
        echo "A required backup command is unavailable." >&2
        exit 1
    fi
done

as_postgres() {
    /usr/bin/setpriv --reuid=postgres --regid=postgres --init-groups \
        --reset-env -- "$@"
}
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
    echo "Another repository operation is active; this backup was not started." >&2
    exit 75
fi

readonly started_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
readonly started_epoch=$(date -u +%s)
private_log=$(mktemp /run/careertrellis-pgbackrest-backup.XXXXXX)
info_json=$(mktemp /run/careertrellis-pgbackrest-info.XXXXXX)
cleanup() {
    rm -f -- "${private_log:-}" "${info_json:-}"
}
trap cleanup EXIT

if ! as_postgres /usr/bin/pgbackrest --stanza="${stanza}" check \
    >"${private_log}" 2>&1; then
    echo "The pgBackRest pre-backup check failed; private output was discarded." >&2
    exit 1
fi
if ! as_postgres /usr/bin/pgbackrest \
    --stanza="${stanza}" \
    --type="${backup_type}" \
    backup >>"${private_log}" 2>&1; then
    echo "The pgBackRest backup failed; private output was discarded." >&2
    exit 1
fi
if ! as_postgres /usr/bin/pgbackrest \
    --stanza="${stanza}" \
    --output=json \
    info >"${info_json}" 2>>"${private_log}"; then
    echo "The completed backup could not be bound to repository metadata." >&2
    exit 1
fi

latest_record=$(/usr/bin/jq -er '
    .[0]
    | select(.name == "careertrellis" and .status.code == 0)
    | .backup
    | sort_by(.timestamp.stop)
    | last
    | [.label, .type]
    | @tsv
' "${info_json}") || {
    echo "The repository did not expose a valid completed backup record." >&2
    exit 1
}
IFS=$'\t' read -r backup_label observed_info_type <<<"${latest_record}"
if [[ ! ${backup_label} =~ ^[0-9]{8}-[0-9]{6}F(_[0-9]{8}-[0-9]{6}[DI])?$ ]] || \
    [[ ${observed_info_type} != "${expected_info_type}" ]]; then
    echo "The latest backup record did not match the requested backup type." >&2
    exit 1
fi

readonly completed_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
readonly elapsed_seconds=$(( $(date -u +%s) - started_epoch ))
printf '{"schema":"careertrellis.backup-health-operation/v1","operation":"%s","result":"ok","started_at":"%s","completed_at":"%s","backup_label":"%s","elapsed_seconds":%d}\n' \
    "${operation}" \
    "${started_at}" \
    "${completed_at}" \
    "${backup_label}" \
    "${elapsed_seconds}" \
    | /usr/bin/python3 -B "${core}" publish --receipt-root "${receipt_root}"
