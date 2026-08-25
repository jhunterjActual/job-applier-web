#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this backup-health helper as root." >&2
    exit 1
fi

readonly command_name=${1:-status}
readonly command_argument=${2:-}
readonly core=/usr/local/libexec/careertrellis/backup_health.py
readonly receipt_root=/var/backups/careertrellis/backup-health/operations
readonly status_root=/var/lib/careertrellis/backup-health/database
readonly alert_root=/var/lib/careertrellis/backup-health/database/alerts
readonly unit_alert_root=/var/lib/careertrellis/backup-health/database/unit-alerts
readonly lock_path=/run/lock/careertrellis-pgbackrest-repository.lock
readonly wal_baseline_path=/var/lib/careertrellis/backup-health/database/wal-failed-count

if [[ -L ${core} || ! -f ${core} || ! -r ${core} ]]; then
    echo "The reviewed backup-health core is unavailable." >&2
    exit 1
fi
if [[ -L ${lock_path} || ! -f ${lock_path} || \
    $(stat -c '%a:%U:%G' "${lock_path}" 2>/dev/null || true) != 600:root:root ]]; then
    echo "The canonical repository lock has an unsafe owner, mode, or type." >&2
    exit 1
fi
for command_path in /usr/bin/date /usr/bin/find /usr/bin/jq /usr/bin/sha256sum /usr/bin/systemctl; do
    if [[ ! -x ${command_path} ]]; then
        echo "A required backup-health command is unavailable." >&2
        exit 1
    fi
done
if [[ -L ${unit_alert_root} || ! -d ${unit_alert_root} || \
    $(stat -c '%a:%U:%G' "${unit_alert_root}" 2>/dev/null || true) != 700:root:root ]]; then
    echo "The persistent unit-alert directory has an unsafe owner, mode, or type." >&2
    exit 1
fi

validate_unit_name() {
    case "$1" in
        careertrellis-pgbackrest-full.service | \
        careertrellis-pgbackrest-differential.service | \
        careertrellis-pgbackrest-onedrive.service | \
        careertrellis-pgbackrest-inventory.service | \
        careertrellis-pgbackrest-restore.service | \
        careertrellis-backup-health.service)
            return 0
            ;;
        *)
            echo "The backup-health unit name is not allowlisted." >&2
            return 1
            ;;
    esac
}

unit_operation() {
    case "$1" in
        careertrellis-pgbackrest-full.service)
            printf 'full_backup\n'
            ;;
        careertrellis-pgbackrest-differential.service)
            printf 'differential_backup\n'
            ;;
        careertrellis-pgbackrest-onedrive.service)
            printf 'pgbackrest_mirror\n'
            ;;
        careertrellis-pgbackrest-inventory.service)
            printf 'pgbackrest_inventory\n'
            ;;
        careertrellis-pgbackrest-restore.service)
            printf 'pgbackrest_restore\n'
            ;;
        careertrellis-backup-health.service)
            printf 'health\n'
            ;;
        *)
            validate_unit_name "$1"
            return 1
            ;;
    esac
}

unit_alert_path() {
    validate_unit_name "$1"
    printf '%s/%s.alert.json\n' "${unit_alert_root}" "$1"
}

validate_unit_alert_file() {
    local path=$1
    local expected_unit=$2
    local observation observed_at prior_receipt_sha256
    if [[ -L ${path} || ! -f ${path} || \
        $(stat -c '%a:%U:%G' "${path}" 2>/dev/null || true) != 600:root:root ]]; then
        echo "A persistent unit alert has an unsafe owner, mode, or type." >&2
        return 1
    fi
    observation=$(/usr/bin/jq -er --arg expected_unit "${expected_unit}" '
        select(
            (keys | sort) == ["observed_at", "prior_receipt_sha256", "result", "schema", "surface", "unit"]
            and .schema == "careertrellis.backup-health-unit-alert/v1"
            and .surface == "database"
            and .unit == $expected_unit
            and .result == "alert"
            and (.observed_at | type) == "string"
            and (.prior_receipt_sha256 | type) == "string"
        )
        | [.observed_at, .prior_receipt_sha256]
        | @tsv
    ' "${path}" 2>/dev/null) || {
        echo "A persistent unit alert is malformed." >&2
        return 1
    }
    IFS=$'\t' read -r observed_at prior_receipt_sha256 <<<"${observation}"
    if [[ ! ${observed_at} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || \
        [[ ! ${prior_receipt_sha256} =~ ^(none|invalid|[0-9a-f]{64})$ ]]; then
        echo "A persistent unit alert contains invalid bounded evidence." >&2
        return 1
    fi
    printf '%s\t%s\n' "${observed_at}" "${prior_receipt_sha256}"
}

audit_unit_alerts() {
    local found=false path name unit_name
    if [[ -L ${unit_alert_root} || ! -d ${unit_alert_root} || \
        $(stat -c '%a:%U:%G' "${unit_alert_root}" 2>/dev/null || true) != 700:root:root ]]; then
        echo "The persistent unit-alert directory has an unsafe owner, mode, or type." >&2
        return 2
    fi
    while IFS= read -r -d '' path; do
        name=${path##*/}
        case "${name}" in
            careertrellis-pgbackrest-full.service.alert.json)
                unit_name=careertrellis-pgbackrest-full.service
                ;;
            careertrellis-pgbackrest-differential.service.alert.json)
                unit_name=careertrellis-pgbackrest-differential.service
                ;;
            careertrellis-pgbackrest-onedrive.service.alert.json)
                unit_name=careertrellis-pgbackrest-onedrive.service
                ;;
            careertrellis-pgbackrest-inventory.service.alert.json)
                unit_name=careertrellis-pgbackrest-inventory.service
                ;;
            careertrellis-pgbackrest-restore.service.alert.json)
                unit_name=careertrellis-pgbackrest-restore.service
                ;;
            careertrellis-backup-health.service.alert.json)
                unit_name=careertrellis-backup-health.service
                ;;
            *)
                echo "The persistent unit-alert directory contains an unknown entry." >&2
                return 2
                ;;
        esac
        validate_unit_alert_file "${path}" "${unit_name}" >/dev/null || return 2
        found=true
    done < <(/usr/bin/find "${unit_alert_root}" -mindepth 1 -maxdepth 1 -print0)
    if [[ ${found} == true ]]; then
        return 10
    fi
    return 0
}

record_unit_alert() {
    local unit_name=$1
    local path temporary observed_at operation receipt_json latest_result prior_receipt_sha256
    path=$(unit_alert_path "${unit_name}")
    if [[ -L ${path} ]]; then
        echo "The unit alert path must not be a symbolic link." >&2
        return 1
    fi
    operation=$(unit_operation "${unit_name}")
    prior_receipt_sha256=none
    if [[ ${operation} != health ]]; then
        receipt_json=$(mktemp /run/careertrellis-backup-health-alert-receipt.XXXXXX)
        set +e
        /usr/bin/python3 -B "${core}" latest \
            --operation "${operation}" \
            --receipt-root "${receipt_root}" >"${receipt_json}" 2>/dev/null
        latest_result=$?
        set -e
        if [[ ${latest_result} -eq 0 ]]; then
            prior_receipt_sha256=$(/usr/bin/sha256sum "${receipt_json}")
            prior_receipt_sha256=${prior_receipt_sha256%% *}
        elif [[ ${latest_result} -gt 1 ]]; then
            prior_receipt_sha256=invalid
        fi
        rm -f -- "${receipt_json}"
    fi
    observed_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
    temporary=$(mktemp "${status_root}/.unit-alert.XXXXXX")
    chmod 0600 "${temporary}"
    printf '{"schema":"careertrellis.backup-health-unit-alert/v1","surface":"database","unit":"%s","result":"alert","observed_at":"%s","prior_receipt_sha256":"%s"}\n' \
        "${unit_name}" "${observed_at}" "${prior_receipt_sha256}" >"${temporary}"
    mv -f -- "${temporary}" "${path}"
    # The core persists the aggregate alert state when an evaluated status is
    # available. A direct operation failure is still retained by the unit alert.
    /usr/bin/python3 -B "${core}" alert \
        --surface database \
        --status-root "${status_root}" \
        --alert-root "${alert_root}" >/dev/null 2>&1 || true
    printf '{"operation":"backup_health_alert","result":"recorded","surface":"database","unit":"%s"}\n' \
        "${unit_name}"
}

clear_unit_alert() {
    local unit_name=$1
    local path observation observed_at prior_receipt_sha256 operation service_result
    local service_exit_timestamp service_exit_epoch observed_epoch
    local receipt_json latest_result current_receipt_sha256 current_completed_at status_json
    path=$(unit_alert_path "${unit_name}")
    if [[ -L ${path} ]]; then
        echo "The unit alert path must not be a symbolic link." >&2
        return 1
    fi
    if [[ ! -e ${path} ]]; then
        printf '{"operation":"backup_health_alert","result":"already_clear","surface":"database","unit":"%s"}\n' "${unit_name}"
        return 0
    fi
    observation=$(validate_unit_alert_file "${path}" "${unit_name}") || return 1
    IFS=$'\t' read -r observed_at prior_receipt_sha256 <<<"${observation}"
    service_result=$(systemctl show "${unit_name}" --property=Result --value 2>/dev/null || true)
    if [[ ${service_result} != success ]]; then
        echo "The exact failed unit has not returned to success; its alert was retained." >&2
        return 1
    fi
    service_exit_timestamp=$(systemctl show "${unit_name}" --property=ExecMainExitTimestamp --value 2>/dev/null || true)
    if [[ -z ${service_exit_timestamp} ]]; then
        echo "The exact failed unit has no newer successful completion; its alert was retained." >&2
        return 1
    fi
    service_exit_epoch=$(date --date="${service_exit_timestamp}" +%s 2>/dev/null || true)
    observed_epoch=$(date --date="${observed_at}" +%s 2>/dev/null || true)
    if [[ ! ${service_exit_epoch} =~ ^[0-9]+$ || ! ${observed_epoch} =~ ^[0-9]+$ || \
        ${service_exit_epoch} -lt ${observed_epoch} ]]; then
        echo "The exact failed unit has no newer successful completion; its alert was retained." >&2
        return 1
    fi
    operation=$(unit_operation "${unit_name}")
    if [[ ${operation} == health ]]; then
        status_json=$(mktemp /run/careertrellis-backup-health-clear-status.XXXXXX)
        set +e
        /usr/bin/python3 -B "${core}" status \
            --surface database \
            --status-root "${status_root}" >"${status_json}" 2>/dev/null
        latest_result=$?
        set -e
        if [[ ${latest_result} -gt 1 ]] || \
            ! /usr/bin/jq -e --arg observed_at "${observed_at}" '
                .evaluated_at >= $observed_at
            ' "${status_json}" >/dev/null; then
            rm -f -- "${status_json}"
            echo "A newer complete evaluation is required before clearing this alert." >&2
            return 1
        fi
        rm -f -- "${status_json}"
    else
        receipt_json=$(mktemp /run/careertrellis-backup-health-clear-receipt.XXXXXX)
        set +e
        /usr/bin/python3 -B "${core}" latest \
            --operation "${operation}" \
            --receipt-root "${receipt_root}" >"${receipt_json}" 2>/dev/null
        latest_result=$?
        set -e
        if [[ ${latest_result} -ne 0 ]]; then
            rm -f -- "${receipt_json}"
            echo "A new manifest-verified success receipt is required before clearing this alert." >&2
            return 1
        fi
        current_receipt_sha256=$(/usr/bin/sha256sum "${receipt_json}")
        current_receipt_sha256=${current_receipt_sha256%% *}
        current_completed_at=$(/usr/bin/jq -er '.completed_at' "${receipt_json}" 2>/dev/null || true)
        rm -f -- "${receipt_json}"
        if [[ ${current_receipt_sha256} == "${prior_receipt_sha256}" || \
            ! ${current_completed_at} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ || \
            ${current_completed_at} < "${observed_at}" ]]; then
            echo "The exact operation has no newer accepted success receipt; its alert was retained." >&2
            return 1
        fi
    fi
    rm -f -- "${path}"
    printf '{"operation":"backup_health_alert","result":"cleared","surface":"database","unit":"%s"}\n' "${unit_name}"
}

operation_unit_state() {
    local operation=$1
    local service_name=$2
    local timer_name=$3
    local alert_path service_result timer_active timer_enabled timer_loaded latest_result
    alert_path=$(unit_alert_path "${service_name}")
    service_result=$(systemctl show "${service_name}" --property=Result --value 2>/dev/null || true)
    timer_active=$(systemctl is-active "${timer_name}" 2>/dev/null || true)
    timer_enabled=$(systemctl is-enabled "${timer_name}" 2>/dev/null || true)
    timer_loaded=$(systemctl show "${timer_name}" --property=LoadState --value 2>/dev/null || true)

    local missed=false
    if [[ ${timer_active} != active || ${timer_enabled} != enabled || ${timer_loaded} != loaded ]]; then
        missed=true
    fi

    local last_result=never
    if [[ -f ${alert_path} && ! -L ${alert_path} ]]; then
        last_result=failed
    elif [[ -n ${service_result} && ${service_result} != success ]]; then
        last_result=failed
    else
        set +e
        /usr/bin/python3 -B "${core}" latest \
            --operation "${operation}" \
            --receipt-root "${receipt_root}" >/dev/null 2>&1
        latest_result=$?
        set -e
        if [[ ${latest_result} -eq 0 ]]; then
            last_result=success
        elif [[ ${latest_result} -ne 1 ]]; then
            last_result=failed
        fi
    fi
    printf '%s\t%s\n' "${last_result}" "${missed}"
}

evaluate_database_health() {
    local caller_mode=${1:-timer}
    local alert_audit_result
    for command_path in \
        /usr/bin/flock \
        /usr/bin/cat \
        /usr/bin/jq \
        /usr/bin/pgbackrest \
        /usr/bin/psql \
        /usr/sbin/runuser \
        /usr/bin/python3; do
        if [[ ! -x ${command_path} ]]; then
            echo "A required health-check command is unavailable." >&2
            return 2
        fi
    done

    set +e
    audit_unit_alerts >/dev/null
    alert_audit_result=$?
    set -e
    if [[ ${alert_audit_result} -eq 2 ]]; then
        return 2
    fi

    umask 077
    if [[ ${caller_mode} != acknowledge ]]; then
        exec 9<>"${lock_path}"
    fi
    if [[ ${caller_mode} != acknowledge ]] && ! flock --nonblock 9; then
        # A backup, mirror, inventory, or restore owns the repository. Deferral
        # is not success evidence and does not clear an existing alert.
        if [[ ${caller_mode} == status ]]; then
            echo "Backup-health status could not refresh while a repository operation was active." >&2
            return 75
        fi
        printf '{"operation":"backup_health_evaluate","result":"deferred","reason":"repository_busy","surface":"database"}\n'
        return 0
    fi

    local info_json private_log evaluation_output alert_output latest_backup_label repository_archive_max
    info_json=$(mktemp /run/careertrellis-backup-health-info.XXXXXX)
    private_log=$(mktemp /run/careertrellis-backup-health-private.XXXXXX)
    evaluation_output=$(mktemp /run/careertrellis-backup-health-evaluation.XXXXXX)
    alert_output=$(mktemp /run/careertrellis-backup-health-alert-state.XXXXXX)
    trap 'rm -f -- "${info_json:-}" "${private_log:-}" "${evaluation_output:-}" "${alert_output:-}"' RETURN

    if ! runuser -u postgres -- /usr/bin/pgbackrest \
        --stanza=careertrellis \
        --output=json \
        info >"${info_json}" 2>"${private_log}"; then
        echo "Backup-health repository inspection failed; private output was discarded." >&2
        return 2
    fi
    latest_backup_label=$(/usr/bin/jq -er '
        .[0]
        | select(.name == "careertrellis" and .status.code == 0)
        | .backup
        | sort_by(.timestamp.stop)
        | last.label
    ' "${info_json}") || {
        echo "Backup-health could not bind the actual newest repository backup." >&2
        return 2
    }
    if [[ ! ${latest_backup_label} =~ ^[0-9]{8}-[0-9]{6}F(_[0-9]{8}-[0-9]{6}[DI])?$ ]]; then
        echo "Backup-health rejected malformed repository metadata." >&2
        return 2
    fi
    repository_archive_max=$(/usr/bin/jq -er '
        .[0]
        | [.archive[]?.max | select(type == "string")]
        | sort
        | last // ""
    ' "${info_json}") || repository_archive_max=

    local wal_observation failed_count last_archived_wal wal_segment_bytes previous_failed_count
    wal_observation=$(runuser -u postgres -- /usr/bin/psql \
        --dbname=careertrellis \
        --no-psqlrc \
        --tuples-only \
        --no-align \
        --command="SELECT failed_count, COALESCE(last_archived_wal, ''), pg_size_bytes(current_setting('wal_segment_size')) FROM pg_stat_archiver" \
        2>>"${private_log}") || {
        echo "Backup-health could not inspect local WAL state." >&2
        return 2
    }
    IFS='|' read -r failed_count last_archived_wal wal_segment_bytes <<<"${wal_observation}"
    if [[ ! ${failed_count} =~ ^[0-9]+$ || \
        ( -n ${last_archived_wal} && ! ${last_archived_wal} =~ ^[0-9A-F]{24}$ ) || \
        ! ${wal_segment_bytes} =~ ^[0-9]+$ || ${wal_segment_bytes} -le 0 ]]; then
        echo "Backup-health rejected malformed local WAL state." >&2
        return 2
    fi
    if (( 4294967296 % wal_segment_bytes != 0 )); then
        echo "Backup-health rejected malformed local WAL state." >&2
        return 2
    fi

    previous_failed_count=${failed_count}
    if [[ -e ${wal_baseline_path} || -L ${wal_baseline_path} ]]; then
        if [[ -L ${wal_baseline_path} || $(stat -c '%a:%U:%G' "${wal_baseline_path}" 2>/dev/null || true) != 600:root:root ]]; then
            echo "The WAL alert baseline has an unsafe owner, mode, or type." >&2
            return 2
        fi
        previous_failed_count=$(<"${wal_baseline_path}")
        if [[ ! ${previous_failed_count} =~ ^[0-9]+$ ]]; then
            echo "The WAL alert baseline is invalid." >&2
            return 2
        fi
    fi

    local segment_lag=2
    local repository_archive_max_behind_database=true
    if [[ ${repository_archive_max} =~ ^[0-9A-F]{24}$ && ${last_archived_wal} =~ ^[0-9A-F]{24}$ ]]; then
        local archived_timeline archived_log archived_segment repository_timeline repository_log repository_segment
        local archived_index repository_index segments_per_log
        archived_timeline=${last_archived_wal:0:8}
        archived_log=${last_archived_wal:8:8}
        archived_segment=${last_archived_wal:16:8}
        repository_timeline=${repository_archive_max:0:8}
        repository_log=${repository_archive_max:8:8}
        repository_segment=${repository_archive_max:16:8}
        if [[ ${archived_timeline} == "${repository_timeline}" ]]; then
            segments_per_log=$(( 4294967296 / wal_segment_bytes ))
            archived_index=$(( 16#${archived_log} * segments_per_log + 16#${archived_segment} ))
            repository_index=$(( 16#${repository_log} * segments_per_log + 16#${repository_segment} ))
            if (( archived_index > repository_index )); then
                segment_lag=$(( archived_index - repository_index ))
            else
                segment_lag=0
                repository_archive_max_behind_database=false
            fi
        fi
    fi

    local full_state differential_state mirror_state inventory_state restore_state
    local full_result full_missed differential_result differential_missed
    local mirror_result mirror_missed inventory_result inventory_missed restore_result restore_missed
    full_state=$(operation_unit_state full_backup \
        careertrellis-pgbackrest-full.service careertrellis-pgbackrest-full.timer)
    differential_state=$(operation_unit_state differential_backup \
        careertrellis-pgbackrest-differential.service careertrellis-pgbackrest-differential.timer)
    mirror_state=$(operation_unit_state pgbackrest_mirror \
        careertrellis-pgbackrest-onedrive.service careertrellis-pgbackrest-onedrive.timer)
    inventory_state=$(operation_unit_state pgbackrest_inventory \
        careertrellis-pgbackrest-inventory.service careertrellis-pgbackrest-inventory.timer)
    restore_state=$(operation_unit_state pgbackrest_restore \
        careertrellis-pgbackrest-restore.service careertrellis-pgbackrest-restore.timer)
    IFS=$'\t' read -r full_result full_missed <<<"${full_state}"
    IFS=$'\t' read -r differential_result differential_missed <<<"${differential_state}"
    IFS=$'\t' read -r mirror_result mirror_missed <<<"${mirror_state}"
    IFS=$'\t' read -r inventory_result inventory_missed <<<"${inventory_state}"
    IFS=$'\t' read -r restore_result restore_missed <<<"${restore_state}"

    local observed_at snapshot evaluation_result alert_result baseline_temporary
    observed_at=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
    snapshot=$(/usr/bin/jq -cn \
        --arg observed_at "${observed_at}" \
        --arg latest_backup_label "${latest_backup_label}" \
        --argjson failed_count "${failed_count}" \
        --argjson previous_failed_count "${previous_failed_count}" \
        --argjson segment_lag "${segment_lag}" \
        --argjson repository_behind "${repository_archive_max_behind_database}" \
        --arg full_result "${full_result}" \
        --argjson full_missed "${full_missed}" \
        --arg differential_result "${differential_result}" \
        --argjson differential_missed "${differential_missed}" \
        --arg mirror_result "${mirror_result}" \
        --argjson mirror_missed "${mirror_missed}" \
        --arg inventory_result "${inventory_result}" \
        --argjson inventory_missed "${inventory_missed}" \
        --arg restore_result "${restore_result}" \
        --argjson restore_missed "${restore_missed}" \
        '{
            schema: "careertrellis.backup-health-snapshot/v1",
            surface: "database",
            observed_at: $observed_at,
            latest_backup_label: $latest_backup_label,
            wal: {
                failed_count: $failed_count,
                previous_failed_count: $previous_failed_count,
                segment_lag: $segment_lag,
                repository_archive_max_behind_database: $repository_behind
            },
            units: {
                full_backup: {last_result: $full_result, missed: $full_missed},
                differential_backup: {last_result: $differential_result, missed: $differential_missed},
                pgbackrest_mirror: {last_result: $mirror_result, missed: $mirror_missed},
                pgbackrest_inventory: {last_result: $inventory_result, missed: $inventory_missed},
                pgbackrest_restore: {last_result: $restore_result, missed: $restore_missed}
            }
        }')

    set +e
    printf '%s\n' "${snapshot}" | /usr/bin/python3 -B "${core}" evaluate \
        --receipt-root "${receipt_root}" \
        --status-root "${status_root}" >"${evaluation_output}"
    evaluation_result=$?
    set -e
    if [[ ${evaluation_result} -gt 1 ]]; then
        echo "Backup-health rejected the collected database evidence." >&2
        return "${evaluation_result}"
    fi

    set +e
    /usr/bin/python3 -B "${core}" alert \
        --surface database \
        --status-root "${status_root}" \
        --alert-root "${alert_root}" >"${alert_output}"
    alert_result=$?
    set -e
    if [[ ${alert_result} -gt 1 || ${alert_result} -ne ${evaluation_result} ]]; then
        echo "Backup-health could not persist a matching aggregate alert state." >&2
        return 2
    fi

    /usr/bin/cat "${evaluation_output}"
    if [[ ${evaluation_result} -eq 0 ]]; then
        baseline_temporary=$(mktemp "${status_root}/.wal-failed-count.XXXXXX")
        chmod 0600 "${baseline_temporary}"
        printf '%s\n' "${failed_count}" >"${baseline_temporary}"
        mv -f -- "${baseline_temporary}" "${wal_baseline_path}"
    fi
    clear_unit_alert careertrellis-backup-health.service >/dev/null || true
    return "${evaluation_result}"
}

acknowledge_wal_failures() {
    local confirmation alert_audit_result private_log info_json repository_archive_max
    local wal_observation failed_count last_archived_wal wal_segment_bytes
    local archived_timeline archived_log archived_segment repository_timeline repository_log repository_segment
    local archived_index repository_index segments_per_log segment_lag=2 repository_behind=true
    local baseline_temporary evaluation_output evaluation_result

    if [[ ! -t 0 || ! -t 1 ]]; then
        echo "WAL failure acknowledgment requires an interactive terminal." >&2
        return 1
    fi
    printf 'Type ACKNOWLEDGE RESOLVED DATABASE WAL ALERT to continue: ' >&2
    IFS= read -r confirmation
    if [[ ${confirmation} != "ACKNOWLEDGE RESOLVED DATABASE WAL ALERT" ]]; then
        echo "WAL failure acknowledgment was not confirmed." >&2
        return 1
    fi

    set +e
    audit_unit_alerts >/dev/null
    alert_audit_result=$?
    set -e
    if [[ ${alert_audit_result} -ne 0 ]]; then
        printf '{"operation":"acknowledge_wal","result":"refused","surface":"database"}\n'
        return 1
    fi

    umask 077
    exec 8<>"${lock_path}"
    if ! flock --nonblock 8; then
        printf '{"operation":"acknowledge_wal","result":"refused","surface":"database"}\n'
        return 1
    fi
    private_log=$(mktemp /run/careertrellis-backup-health-ack-private.XXXXXX)
    info_json=$(mktemp /run/careertrellis-backup-health-ack-info.XXXXXX)
    evaluation_output=$(mktemp /run/careertrellis-backup-health-ack-evaluation.XXXXXX)
    trap 'rm -f -- "${private_log:-}" "${info_json:-}" "${evaluation_output:-}"' RETURN

    if ! runuser -u postgres -- /usr/bin/pgbackrest \
        --stanza=careertrellis \
        check >"${private_log}" 2>&1 || \
        ! runuser -u postgres -- /usr/bin/pgbackrest \
        --stanza=careertrellis \
        --output=json \
        info >"${info_json}" 2>>"${private_log}"; then
        printf '{"operation":"acknowledge_wal","result":"refused","surface":"database"}\n'
        return 1
    fi
    if ! /usr/bin/jq -e '
        .[0]
        | select(.name == "careertrellis" and .status.code == 0)
        | (.backup | length) > 0
    ' "${info_json}" >/dev/null; then
        printf '{"operation":"acknowledge_wal","result":"refused","surface":"database"}\n'
        return 1
    fi
    repository_archive_max=$(/usr/bin/jq -er '
        .[0]
        | [.archive[]?.max | select(type == "string")]
        | sort
        | last // ""
    ' "${info_json}") || repository_archive_max=

    wal_observation=$(runuser -u postgres -- /usr/bin/psql \
        --dbname=careertrellis \
        --no-psqlrc \
        --tuples-only \
        --no-align \
        --command="SELECT failed_count, COALESCE(last_archived_wal, ''), pg_size_bytes(current_setting('wal_segment_size')) FROM pg_stat_archiver" \
        2>>"${private_log}") || {
        printf '{"operation":"acknowledge_wal","result":"refused","surface":"database"}\n'
        return 1
    }
    IFS='|' read -r failed_count last_archived_wal wal_segment_bytes <<<"${wal_observation}"
    if [[ ! ${failed_count} =~ ^[0-9]+$ || ! ${last_archived_wal} =~ ^[0-9A-F]{24}$ || \
        ! ${repository_archive_max} =~ ^[0-9A-F]{24}$ || \
        ! ${wal_segment_bytes} =~ ^[0-9]+$ || ${wal_segment_bytes} -le 0 ]] || \
        (( 4294967296 % wal_segment_bytes != 0 )); then
        printf '{"operation":"acknowledge_wal","result":"refused","surface":"database"}\n'
        return 1
    fi

    archived_timeline=${last_archived_wal:0:8}
    archived_log=${last_archived_wal:8:8}
    archived_segment=${last_archived_wal:16:8}
    repository_timeline=${repository_archive_max:0:8}
    repository_log=${repository_archive_max:8:8}
    repository_segment=${repository_archive_max:16:8}
    if [[ ${archived_timeline} == "${repository_timeline}" ]]; then
        segments_per_log=$(( 4294967296 / wal_segment_bytes ))
        archived_index=$(( 16#${archived_log} * segments_per_log + 16#${archived_segment} ))
        repository_index=$(( 16#${repository_log} * segments_per_log + 16#${repository_segment} ))
        if (( archived_index > repository_index )); then
            segment_lag=$(( archived_index - repository_index ))
        else
            segment_lag=0
            repository_behind=false
        fi
    fi
    if [[ ${repository_behind} == true || ${segment_lag} -gt 1 ]]; then
        printf '{"operation":"acknowledge_wal","result":"refused","surface":"database"}\n'
        return 1
    fi

    if [[ -e ${wal_baseline_path} || -L ${wal_baseline_path} ]]; then
        if [[ -L ${wal_baseline_path} || ! -f ${wal_baseline_path} || \
            $(stat -c '%a:%U:%G' "${wal_baseline_path}" 2>/dev/null || true) != 600:root:root ]]; then
            printf '{"operation":"acknowledge_wal","result":"refused","surface":"database"}\n'
            return 1
        fi
    fi

    baseline_temporary=$(mktemp "${status_root}/.wal-failed-count.XXXXXX")
    chmod 0600 "${baseline_temporary}"
    printf '%s\n' "${failed_count}" >"${baseline_temporary}"
    mv -f -- "${baseline_temporary}" "${wal_baseline_path}"

    set +e
    evaluate_database_health acknowledge >"${evaluation_output}" 2>>"${private_log}"
    evaluation_result=$?
    set -e
    if [[ ${evaluation_result} -ne 0 ]]; then
        printf '{"operation":"acknowledge_wal","result":"failed","surface":"database"}\n'
        return 1
    fi
    printf '{"operation":"acknowledge_wal","result":"ok","surface":"database"}\n'
    return 0
}

case "${command_name}" in
    evaluate)
        if [[ -n ${command_argument} ]]; then
            echo "Usage: $0 evaluate" >&2
            exit 2
        fi
        evaluate_database_health
        ;;
    status)
        if [[ -n ${command_argument} ]]; then
            echo "Usage: $0 status" >&2
            exit 2
        fi
        set +e
        audit_unit_alerts >/dev/null
        alert_audit_result=$?
        set -e
        if [[ ${alert_audit_result} -eq 2 ]]; then
            printf '{"operation":"backup_health_status","result":"alert","surface":"database","reason":"unit_alert_evidence_invalid"}\n'
            exit 1
        fi
        if [[ ${alert_audit_result} -eq 10 ]]; then
            set +e
            evaluate_database_health status
            evaluation_result=$?
            set -e
            if [[ ${evaluation_result} -eq 75 ]]; then
                printf '{"operation":"backup_health_status","result":"alert","surface":"database","reason":"unit_alert_refresh_deferred"}\n'
                exit 1
            fi
            exit "${evaluation_result}"
        fi
        /usr/bin/python3 -B "${core}" status \
            --surface database \
            --status-root "${status_root}"
        ;;
    acknowledge-wal)
        if [[ -n ${command_argument} ]]; then
            echo "Usage: $0 acknowledge-wal" >&2
            exit 2
        fi
        acknowledge_wal_failures
        ;;
    alert-unit)
        if [[ -z ${command_argument} ]]; then
            echo "Usage: $0 alert-unit UNIT" >&2
            exit 2
        fi
        record_unit_alert "${command_argument}"
        ;;
    clear-unit-alert)
        if [[ -z ${command_argument} ]]; then
            echo "Usage: $0 clear-unit-alert UNIT" >&2
            exit 2
        fi
        clear_unit_alert "${command_argument}"
        ;;
    *)
        echo "Usage: $0 evaluate|status|acknowledge-wal|alert-unit UNIT|clear-unit-alert UNIT" >&2
        exit 2
        ;;
esac
