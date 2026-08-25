#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this pgBackRest restore rehearsal as root." >&2
    exit 1
fi

readonly stanza=careertrellis
readonly postgres_version=18
readonly database_name=careertrellis
readonly test_port=55432
readonly restore_root=/var/lib/postgresql/careertrellis-restore-tests
readonly pg_ctl=/usr/lib/postgresql/18/bin/pg_ctl
readonly pg_checksums=/usr/lib/postgresql/18/bin/pg_checksums
readonly lock_path=/run/lock/careertrellis-pgbackrest-repository.lock
readonly core=/usr/local/libexec/careertrellis/backup_health.py
readonly receipt_root=/var/backups/careertrellis/backup-health/operations

for command_path in \
    /usr/bin/chmod \
    /usr/bin/flock \
    /usr/bin/install \
    /usr/bin/jq \
    /usr/bin/pgbackrest \
    /usr/bin/psql \
    /usr/bin/tee \
    /usr/sbin/runuser \
    /usr/bin/python3 \
    "${pg_ctl}" \
    "${pg_checksums}"; do
    if [[ ! -x ${command_path} ]]; then
        echo "A required restore-test command is unavailable." >&2
        exit 1
    fi
done
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
    echo "Another repository operation is active; this restore was not started." >&2
    exit 75
fi

if [[ ! -e ${restore_root} && ! -L ${restore_root} ]]; then
    install -d -o root -g postgres -m 0710 "${restore_root}"
fi
if [[ $(stat -c '%a:%U:%G' "${restore_root}" 2>/dev/null || true) != 710:root:postgres ]] || \
    [[ -L ${restore_root} || ! -d ${restore_root} || \
    $(realpath -e "${restore_root}" 2>/dev/null || true) != "${restore_root}" ]]; then
    echo "The dedicated restore root has an unsafe owner, mode, or location." >&2
    exit 1
fi

readonly started_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
readonly started_epoch=$(date -u +%s)
private_log=$(mktemp /run/careertrellis-pgbackrest-restore.XXXXXX)
info_json=$(mktemp /run/careertrellis-pgbackrest-info.XXXXXX)
full_receipt_json=$(mktemp /run/careertrellis-pgbackrest-full-receipt.XXXXXX)
differential_receipt_json=$(mktemp /run/careertrellis-pgbackrest-differential-receipt.XXXXXX)
restore_directory=

cleanup_restore() {
    local cleanup_failed=false
    local current_identity
    if [[ -z ${restore_directory:-} ]]; then
        return 0
    fi
    if [[ ${restore_directory} != "${restore_root}/"* || -L ${restore_directory} ]]; then
        echo "The restore cleanup boundary was invalid; no deletion was attempted." >&2
        return 1
    fi
    current_identity=$(stat -c '%d:%i' "${restore_directory}" 2>/dev/null || true)
    if [[ -z ${restore_device_inode:-} || ${current_identity} != "${restore_device_inode}" ]]; then
        echo "The restore cleanup identity changed; no deletion was attempted." >&2
        return 1
    fi
    if runuser -u postgres -- "${pg_ctl}" --pgdata="${restore_directory}" status >/dev/null 2>&1; then
        if ! runuser -u postgres -- "${pg_ctl}" \
            --pgdata="${restore_directory}" \
            --mode=fast \
            --wait \
            --timeout=60 \
            stop >>"${private_log}" 2>&1; then
            cleanup_failed=true
        fi
    fi
    if [[ ${cleanup_failed} == false ]]; then
        current_identity=$(stat -c '%d:%i' "${restore_directory}" 2>/dev/null || true)
        if [[ ${current_identity} != "${restore_device_inode}" ]]; then
            echo "The restore cleanup identity changed; no deletion was attempted." >&2
            return 1
        fi
        rm -rf --one-file-system -- "${restore_directory}"
        if [[ -e ${restore_directory} || -L ${restore_directory} ]]; then
            cleanup_failed=true
        else
            restore_directory=
        fi
    fi
    if [[ ${cleanup_failed} == true ]]; then
        echo "The isolated restore could not be cleaned up; operator containment is required." >&2
        return 1
    fi
}

cleanup_on_exit() {
    local exit_status=$?
    trap - EXIT
    if ! cleanup_restore; then
        exit_status=1
    fi
    rm -f -- \
        "${private_log:-}" \
        "${info_json:-}" \
        "${full_receipt_json:-}" \
        "${differential_receipt_json:-}"
    exit "${exit_status}"
}
trap cleanup_on_exit EXIT

# Bind the rehearsal to the newest manifest-verified backup receipt before
# creating any disposable cluster state. A repository-only label that was not
# accepted by the monitor is never selected implicitly.
set +e
/usr/bin/python3 -B "${core}" latest \
    --operation full_backup \
    --receipt-root "${receipt_root}" >"${full_receipt_json}" 2>>"${private_log}"
full_receipt_result=$?
/usr/bin/python3 -B "${core}" latest \
    --operation differential_backup \
    --receipt-root "${receipt_root}" >"${differential_receipt_json}" 2>>"${private_log}"
differential_receipt_result=$?
set -e
if [[ ${full_receipt_result} -gt 1 || ${differential_receipt_result} -gt 1 ]]; then
    echo "Accepted backup receipt evidence was invalid or unsafe." >&2
    exit 1
fi
if [[ ${full_receipt_result} -ne 0 && ${differential_receipt_result} -ne 0 ]]; then
    echo "No manifest-verified backup receipt is available for restore." >&2
    exit 1
fi
backup_label=$(/usr/bin/jq -ser '
    map(select(
        .schema == "careertrellis.backup-health-operation/v1"
        and (.operation == "full_backup" or .operation == "differential_backup")
        and .result == "ok"
    ))
    | sort_by(.completed_at)
    | last.backup_label
' "${full_receipt_json}" "${differential_receipt_json}") || {
    echo "The newest accepted backup receipt could not be selected." >&2
    exit 1
}
if [[ ! ${backup_label} =~ ^[0-9]{8}-[0-9]{6}F(_[0-9]{8}-[0-9]{6}[DI])?$ ]]; then
    echo "The accepted restore label metadata was invalid." >&2
    exit 1
fi

# Confirm that the accepted label still exists in a healthy repository. Raw
# repository metadata never reaches stdout.
if ! runuser -u postgres -- /usr/bin/pgbackrest \
    --stanza="${stanza}" \
    --output=json \
    info >"${info_json}" 2>"${private_log}"; then
    echo "The repository could not be inspected for an accepted restore label." >&2
    exit 1
fi
backup_completed_epoch=$(/usr/bin/jq -er --arg backup_label "${backup_label}" '
    .[0]
    | select(.name == "careertrellis" and .status.code == 0)
    | .backup
    | map(select(.label == $backup_label))
    | if length == 1 then .[0].timestamp.stop else error("accepted label missing") end
' "${info_json}") || {
    echo "The accepted restore label is not available in the healthy repository." >&2
    exit 1
}
if [[ ! ${backup_completed_epoch} =~ ^[0-9]+$ ]]; then
    echo "The accepted restore label metadata was invalid." >&2
    exit 1
fi
readonly rpo_seconds=$(( started_epoch > backup_completed_epoch ? started_epoch - backup_completed_epoch : 0 ))

restore_directory=$(mktemp -d "${restore_root}/restore.XXXXXX")
chown postgres:postgres "${restore_directory}"
chmod 0700 "${restore_directory}"
restore_directory=$(realpath -e "${restore_directory}")
if [[ ${restore_directory} != "${restore_root}/"* || -L ${restore_directory} ]] || \
    [[ $(stat -c '%a:%U:%G' "${restore_directory}" 2>/dev/null || true) != 700:postgres:postgres ]]; then
    echo "The disposable restore directory escaped its protected root." >&2
    exit 1
fi
readonly restore_device_inode=$(stat -c '%d:%i' "${restore_directory}")
if [[ ! ${restore_device_inode} =~ ^[0-9]+:[0-9]+$ ]]; then
    echo "The disposable restore directory identity was invalid." >&2
    exit 1
fi

if ! runuser -u postgres -- /usr/bin/pgbackrest \
    --stanza="${stanza}" \
    --set="${backup_label}" \
    --pg1-path="${restore_directory}" \
    --type=immediate \
    --target-action=promote \
    restore >>"${private_log}" 2>&1; then
    echo "The isolated restore failed; private output was discarded." >&2
    exit 1
fi

socket_directory="${restore_directory}/socket"
test_config="${restore_directory}/postgresql.restore-test.conf"
test_hba="${restore_directory}/pg_hba.restore-test.conf"
test_ident="${restore_directory}/pg_ident.restore-test.conf"
runuser -u postgres -- /usr/bin/install -d -m 0700 "${socket_directory}"
runuser -u postgres -- /usr/bin/tee -- "${test_hba}" >/dev/null <<'EOF'
local all postgres peer
local all all reject
EOF
runuser -u postgres -- /usr/bin/tee -- "${test_config}" >/dev/null <<EOF
data_directory = '${restore_directory}'
hba_file = '${test_hba}'
ident_file = '${test_ident}'
listen_addresses = ''
port = ${test_port}
unix_socket_directories = '${socket_directory}'
max_connections = 50
shared_buffers = '64MB'
archive_mode = off
logging_collector = off
EOF
runuser -u postgres -- /usr/bin/install -m 0600 /dev/null "${test_ident}"
runuser -u postgres -- /usr/bin/chmod 0600 "${test_config}" "${test_hba}"

readonly postgres_start_options="-c config_file=${test_config} -c data_directory=${restore_directory} -c hba_file=${test_hba} -c ident_file=${test_ident} -c listen_addresses= -c port=${test_port} -c unix_socket_directories=${socket_directory} -c unix_socket_group= -c unix_socket_permissions=0700 -c ssl=off -c shared_preload_libraries= -c session_preload_libraries= -c local_preload_libraries= -c archive_mode=off -c archive_command= -c archive_cleanup_command= -c recovery_end_command= -c logging_collector=off -c log_destination=stderr -c external_pid_file= -c max_connections=50 -c shared_buffers=64MB -c primary_conninfo= -c primary_slot_name= -c jit=off"
if ! runuser -u postgres -- "${pg_ctl}" \
    --pgdata="${restore_directory}" \
    --options="${postgres_start_options}" \
    --log="${restore_directory}/postgresql.restore-test.log" \
    --wait \
    --timeout=90 \
    start >>"${private_log}" 2>&1; then
    echo "The isolated restore cluster could not be started." >&2
    exit 1
fi

recovery_state=$(runuser -u postgres -- /usr/bin/psql \
    --host="${socket_directory}" \
    --port="${test_port}" \
    --username=postgres \
    --dbname="${database_name}" \
    --no-psqlrc \
    --tuples-only \
    --no-align \
    --command="SELECT pg_is_in_recovery()" 2>>"${private_log}")
if [[ ${recovery_state} == t ]]; then
    if ! runuser -u postgres -- "${pg_ctl}" \
        --pgdata="${restore_directory}" \
        --wait \
        --timeout=60 \
        promote >>"${private_log}" 2>&1; then
        echo "The isolated restore could not be promoted." >&2
        exit 1
    fi
fi

# A rollback-only temporary write proves that the promoted cluster is writable
# without retaining any synthetic row in the disposable restore.
write_result=$(runuser -u postgres -- /usr/bin/psql \
    --host="${socket_directory}" \
    --port="${test_port}" \
    --username=postgres \
    --dbname="${database_name}" \
    --no-psqlrc \
    --set=ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    --command="BEGIN; CREATE TEMP TABLE careertrellis_restore_probe(value integer); INSERT INTO careertrellis_restore_probe VALUES (1); SELECT CASE WHEN pg_is_in_recovery() OR current_setting('listen_addresses') <> '' OR count(*) <> 1 THEN 'invalid' ELSE 'ok' END FROM careertrellis_restore_probe; ROLLBACK;" \
    2>>"${private_log}")
if [[ ${write_result} != *$'\nok\n'* && ${write_result} != ok ]]; then
    echo "The restored cluster did not prove isolated writable-primary behavior." >&2
    exit 1
fi
if ! runuser -u postgres -- "${pg_ctl}" \
    --pgdata="${restore_directory}" \
    --mode=fast \
    --wait \
    --timeout=60 \
    stop >>"${private_log}" 2>&1; then
    echo "The restored cluster could not be stopped before checksum validation." >&2
    exit 1
fi
if ! runuser -u postgres -- "${pg_checksums}" \
    --check \
    --pgdata="${restore_directory}" >>"${private_log}" 2>&1; then
    echo "The restored cluster checksum validation failed." >&2
    exit 1
fi
readonly rto_seconds=$(( $(date -u +%s) - started_epoch ))

# A successful operation is receipted only after the temporary primary and all
# restored data have been removed.
if ! cleanup_restore; then
    exit 1
fi
readonly completed_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
readonly elapsed_seconds=$(( $(date -u +%s) - started_epoch ))
printf '{"schema":"careertrellis.backup-health-operation/v1","operation":"pgbackrest_restore","result":"ok","started_at":"%s","completed_at":"%s","backup_label":"%s","elapsed_seconds":%d,"rto_seconds":%d,"rpo_seconds":%d,"promotion_verified":true,"checksum_verified":true,"cleanup_verified":true}\n' \
    "${started_at}" \
    "${completed_at}" \
    "${backup_label}" \
    "${elapsed_seconds}" \
    "${rto_seconds}" \
    "${rpo_seconds}" \
    | /usr/bin/python3 -B "${core}" publish --receipt-root "${receipt_root}"
