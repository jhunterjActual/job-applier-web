#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

readonly core=/usr/local/libexec/careertrellis/backup_health.py
readonly receipt_root=/var/backups/careertrellis/backup-health/operations
readonly status_root=/var/lib/careertrellis/backup-health/objects
readonly alert_root=/var/lib/careertrellis/backup-health/objects/alerts
readonly unit_alert_root=/var/lib/careertrellis/backup-health/objects/unit-alerts
readonly lock_path=/run/lock/careertrellis-backup-health-objects.lock
readonly health_unit=careertrellis-generated-object-backup-health.service
readonly unit_alert_path=${unit_alert_root}/${health_unit}.alert.json

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this generated-object backup-health helper as root." >&2
    exit 1
fi

command_name=${1:-}
command_argument=${2:-}

for command_path in \
    /usr/bin/cat \
    /usr/bin/chmod \
    /usr/bin/date \
    /usr/bin/find \
    /usr/bin/flock \
    /usr/bin/jq \
    /usr/bin/mktemp \
    /usr/bin/mv \
    /usr/bin/python3 \
    /usr/bin/realpath \
    /usr/bin/rm \
    /usr/bin/stat; do
    if [[ ! -x ${command_path} ]]; then
        echo "A required generated-object backup-health command is unavailable." >&2
        exit 1
    fi
done

validate_root_directory() {
    local path=$1
    [[ -d ${path} && ! -L ${path} ]] || return 1
    [[ $(/usr/bin/realpath "${path}" 2>/dev/null || true) == "${path}" ]] || return 1
    [[ $(/usr/bin/stat -c '%a:%U:%G' "${path}" 2>/dev/null || true) == 700:root:root ]]
}

fsync_file() {
    /usr/bin/python3 -B -c '
import os
import sys

flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
descriptor = os.open(sys.argv[1], flags)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
' "$1"
}

fsync_directory() {
    /usr/bin/python3 -B -c '
import os
import sys

flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
descriptor = os.open(sys.argv[1], flags)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
' "$1"
}

if [[ ! -f ${core} || -L ${core} || \
      $(/usr/bin/realpath "${core}" 2>/dev/null || true) != "${core}" || \
      $(/usr/bin/stat -c '%a:%U:%G' "${core}" 2>/dev/null || true) != 644:root:root ]]; then
    echo "The reviewed backup-health core is unavailable." >&2
    exit 1
fi
for protected_root in "${receipt_root}" "${status_root}" "${alert_root}" "${unit_alert_root}"; do
    if ! validate_root_directory "${protected_root}"; then
        echo "A generated-object backup-health root has an unsafe owner, mode, or type." >&2
        exit 1
    fi
done
if [[ ! -f ${lock_path} || -L ${lock_path} || \
      $(/usr/bin/stat -c '%a:%U:%G' "${lock_path}" 2>/dev/null || true) != 600:root:root ]]; then
    echo "The generated-object backup-health lock has an unsafe owner, mode, or type." >&2
    exit 1
fi

validate_unit_name() {
    if [[ $1 != "${health_unit}" ]]; then
        echo "The generated-object backup-health unit name is not allowlisted." >&2
        return 1
    fi
}

validate_unit_alert_file() {
    local path=$1
    local observed_at
    if [[ ${path} != "${unit_alert_path}" || ! -f ${path} || -L ${path} || \
          $(/usr/bin/stat -c '%a:%U:%G' "${path}" 2>/dev/null || true) != 600:root:root ]]; then
        echo "The generated-object backup-health unit alert is unsafe." >&2
        return 1
    fi
    observed_at=$(/usr/bin/jq -er --arg expected_unit "${health_unit}" '
        select(
            (keys | sort) == ["observed_at", "result", "schema", "surface", "unit"]
            and .schema == "careertrellis.backup-health-unit-alert/v1"
            and .surface == "objects"
            and .unit == $expected_unit
            and .result == "alert"
            and (.observed_at | type) == "string"
        )
        | .observed_at
    ' "${path}" 2>/dev/null) || {
        echo "The generated-object backup-health unit alert is malformed." >&2
        return 1
    }
    if [[ ! ${observed_at} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
        echo "The generated-object backup-health unit alert is malformed." >&2
        return 1
    fi
    printf '%s\n' "${observed_at}"
}

audit_unit_alerts() {
    local path name found=false
    while IFS= read -r -d '' path; do
        name=${path##*/}
        if [[ ${name} != "${health_unit}.alert.json" || ${found} == true ]]; then
            echo "The generated-object unit-alert directory contains an unknown entry." >&2
            return 2
        fi
        validate_unit_alert_file "${path}" >/dev/null || return 2
        found=true
    done < <(/usr/bin/find "${unit_alert_root}" -mindepth 1 -maxdepth 1 -print0)
    if [[ ${found} == true ]]; then
        return 10
    fi
    return 0
}

record_unit_alert() {
    local unit_name=$1
    local observed_at temporary
    validate_unit_name "${unit_name}"
    if [[ -e ${unit_alert_path} || -L ${unit_alert_path} ]]; then
        validate_unit_alert_file "${unit_alert_path}" >/dev/null
        printf '{"operation":"generated_object_backup_health_alert","result":"already_recorded","surface":"objects","unit":"%s"}\n' "${health_unit}"
        return 0
    fi
    observed_at=$(/usr/bin/date -u +'%Y-%m-%dT%H:%M:%SZ')
    if [[ ! ${observed_at} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
        echo "The generated-object backup-health clock is invalid." >&2
        return 1
    fi
    temporary=$(/usr/bin/mktemp "${unit_alert_root}/.unit-alert.XXXXXX")
    /usr/bin/chmod 0600 "${temporary}"
    printf '{"schema":"careertrellis.backup-health-unit-alert/v1","surface":"objects","unit":"%s","result":"alert","observed_at":"%s"}\n' \
        "${health_unit}" "${observed_at}" >"${temporary}"
    if ! fsync_file "${temporary}" || ! /usr/bin/mv -f -- "${temporary}" "${unit_alert_path}"; then
        /usr/bin/rm -f -- "${temporary}"
        return 1
    fi
    fsync_directory "${unit_alert_root}"
    /usr/bin/python3 -B "${core}" alert \
        --surface objects \
        --status-root "${status_root}" \
        --alert-root "${alert_root}" >/dev/null 2>&1 || true
    printf '{"operation":"generated_object_backup_health_alert","result":"recorded","surface":"objects","unit":"%s"}\n' "${health_unit}"
}

clear_unit_alert() {
    local unit_name=$1
    local observed_at status_json
    validate_unit_name "${unit_name}"
    if [[ ! -e ${unit_alert_path} && ! -L ${unit_alert_path} ]]; then
        printf '{"operation":"generated_object_backup_health_alert","result":"already_clear","surface":"objects","unit":"%s"}\n' "${health_unit}"
        return 0
    fi
    observed_at=$(validate_unit_alert_file "${unit_alert_path}")
    status_json=$(/usr/bin/mktemp /run/careertrellis-generated-object-health-status.XXXXXX)
    if ! /usr/bin/python3 -B "${core}" status \
        --surface objects \
        --status-root "${status_root}" >"${status_json}" 2>/dev/null || \
       ! /usr/bin/jq -e --arg observed_at "${observed_at}" '
            .result == "ok" and .surface == "objects" and .evaluated_at >= $observed_at
        ' "${status_json}" >/dev/null; then
        /usr/bin/rm -f -- "${status_json}"
        echo "A newer healthy objects evaluation is required before clearing this alert." >&2
        return 1
    fi
    /usr/bin/rm -f -- "${status_json}"
    /usr/bin/rm -f -- "${unit_alert_path}"
    fsync_directory "${unit_alert_root}"
    printf '{"operation":"generated_object_backup_health_alert","result":"cleared","surface":"objects","unit":"%s"}\n' "${health_unit}"
}

operation_state() {
    local operation=$1
    local latest_result
    set +e
    /usr/bin/python3 -B "${core}" latest \
        --operation "${operation}" \
        --receipt-root "${receipt_root}" >/dev/null 2>&1
    latest_result=$?
    set -e
    case ${latest_result} in
        0) printf 'success\n' ;;
        1) printf 'never\n' ;;
        *) printf 'failed\n' ;;
    esac
}

evaluate_objects_health() {
    local mirror_result restore_result observed_at snapshot
    local evaluation_output alert_output evaluation_result alert_result

    exec 9<>"${lock_path}"
    if ! /usr/bin/flock --nonblock 9; then
        echo "Another generated-object backup-health evaluation is active." >&2
        return 75
    fi

    mirror_result=$(operation_state generated_object_mirror)
    restore_result=$(operation_state generated_object_restore)
    observed_at=$(/usr/bin/date -u +'%Y-%m-%dT%H:%M:%SZ')
    snapshot=$(/usr/bin/jq -cn \
        --arg observed_at "${observed_at}" \
        --arg mirror_result "${mirror_result}" \
        --arg restore_result "${restore_result}" '
        {
            schema: "careertrellis.backup-health-snapshot/v1",
            surface: "objects",
            observed_at: $observed_at,
            units: {
                generated_object_mirror: {last_result: $mirror_result, missed: false},
                generated_object_restore: {last_result: $restore_result, missed: false}
            }
        }
    ')

    evaluation_output=$(/usr/bin/mktemp /run/careertrellis-generated-object-health-evaluation.XXXXXX)
    alert_output=$(/usr/bin/mktemp /run/careertrellis-generated-object-health-alert.XXXXXX)
    set +e
    printf '%s\n' "${snapshot}" | /usr/bin/python3 -B "${core}" evaluate \
        --receipt-root "${receipt_root}" \
        --status-root "${status_root}" >"${evaluation_output}"
    evaluation_result=$?
    set -e
    if [[ ${evaluation_result} -gt 1 ]]; then
        /usr/bin/rm -f -- "${evaluation_output}" "${alert_output}"
        echo "Backup-health rejected the collected generated-object evidence." >&2
        return "${evaluation_result}"
    fi

    set +e
    /usr/bin/python3 -B "${core}" alert \
        --surface objects \
        --status-root "${status_root}" \
        --alert-root "${alert_root}" >"${alert_output}"
    alert_result=$?
    set -e
    if [[ ${alert_result} -gt 1 || ${alert_result} -ne ${evaluation_result} ]]; then
        /usr/bin/rm -f -- "${evaluation_output}" "${alert_output}"
        echo "Backup-health could not persist a matching objects alert state." >&2
        return 2
    fi
    /usr/bin/cat "${evaluation_output}"
    /usr/bin/rm -f -- "${evaluation_output}" "${alert_output}"
    return "${evaluation_result}"
}

case "${command_name}" in
    evaluate)
        if [[ $# -ne 1 ]]; then
            echo "Usage: $0 evaluate" >&2
            exit 2
        fi
        evaluate_objects_health
        ;;
    status)
        if [[ $# -ne 1 ]]; then
            echo "Usage: $0 status" >&2
            exit 2
        fi
        set +e
        audit_unit_alerts >/dev/null
        alert_audit_result=$?
        set -e
        if [[ ${alert_audit_result} -eq 2 ]]; then
            printf '{"operation":"generated_object_backup_health_status","reason":"unit_alert_evidence_invalid","result":"alert","surface":"objects"}\n'
            exit 1
        fi
        if [[ ${alert_audit_result} -eq 10 ]]; then
            refresh_output=$(/usr/bin/mktemp /run/careertrellis-generated-object-health-refresh.XXXXXX)
            set +e
            evaluate_objects_health >"${refresh_output}"
            refresh_result=$?
            set -e
            if [[ ${refresh_result} -eq 0 ]]; then
                if ! clear_unit_alert "${health_unit}" >/dev/null; then
                    /usr/bin/rm -f -- "${refresh_output}"
                    printf '{"operation":"generated_object_backup_health_status","reason":"unit_alert_not_cleared","result":"alert","surface":"objects"}\n'
                    exit 1
                fi
                /usr/bin/cat "${refresh_output}"
                /usr/bin/rm -f -- "${refresh_output}"
                exit 0
            fi
            if [[ ${refresh_result} -eq 1 ]]; then
                /usr/bin/cat "${refresh_output}"
            else
                printf '{"operation":"generated_object_backup_health_status","reason":"unit_alert_refresh_failed","result":"alert","surface":"objects"}\n'
            fi
            /usr/bin/rm -f -- "${refresh_output}"
            exit 1
        fi
        /usr/bin/python3 -B "${core}" status \
            --surface objects \
            --status-root "${status_root}"
        ;;
    alert-unit)
        if [[ $# -ne 2 || -z ${command_argument} ]]; then
            echo "Usage: $0 alert-unit UNIT" >&2
            exit 2
        fi
        record_unit_alert "${command_argument}"
        ;;
    clear-unit-alert)
        if [[ $# -ne 2 || -z ${command_argument} ]]; then
            echo "Usage: $0 clear-unit-alert UNIT" >&2
            exit 2
        fi
        clear_unit_alert "${command_argument}"
        ;;
    *)
        echo "Usage: $0 evaluate|status|alert-unit UNIT|clear-unit-alert UNIT" >&2
        exit 2
        ;;
esac
