#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

readonly source_confirmation=I_HAVE_VERIFIED_SOURCE_R2_CREDENTIAL_IS_READ_ONLY
readonly append_confirmation=I_UNDERSTAND_THIS_APPENDS_AN_IMMUTABLE_ONEDRIVE_SNAPSHOT
readonly release_root=/opt/careertrellis/staging
readonly recovery_user=careertrellis-object-recovery
readonly recovery_group=careertrellis-object-recovery
readonly recovery_state=/var/lib/careertrellis/generated-object-recovery
readonly rclone_config=${recovery_state}/rclone.conf
readonly recovery_environment_file=/etc/careertrellis/generated-object-recovery.env
readonly authority_helper=/usr/local/sbin/careertrellis-authority
readonly maintenance_lock_helper=/usr/local/libexec/careertrellis/maintenance-lock.sh
readonly backup_health_core=/usr/local/libexec/careertrellis/backup_health.py
readonly backup_health_receipt_root=/var/backups/careertrellis/backup-health/operations
readonly capture_root=/var/lib/careertrellis/generated-object-captures

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the generated-object snapshot as root." >&2
    exit 1
fi

mode=combined
if [[ ${1:-} == capture || ${1:-} == publish-captured ]]; then
    mode=$1
    shift
fi
release_dir=${1:-}
backup_label=${2:-}
source_acknowledgement=${3:-}
append_acknowledgement=${4:-}
if [[ $# -ne 4 || ${source_acknowledgement} != "${source_confirmation}" || ${append_acknowledgement} != "${append_confirmation}" ]]; then
    echo "Usage: $0 [capture|publish-captured] /opt/careertrellis/staging/releases/EXACT_COMMIT YYYYMMDD-HHMMSSF ${source_confirmation} ${append_confirmation}" >&2
    exit 1
fi
if [[ ! ${backup_label} =~ ^[0-9]{8}-[0-9]{6}F$ ]]; then
    echo "The backup label must be the exact pgBackRest full-backup label." >&2
    exit 1
fi
if [[ ! -d ${release_dir} || -L ${release_dir} ]]; then
    echo "The snapshot release must be an exact immutable 40-character release." >&2
    exit 1
fi
release_dir=$(realpath "${release_dir}")
release_revision=$(basename "${release_dir}")
if [[ $(dirname "${release_dir}") != "${release_root}/releases" || ! ${release_revision} =~ ^[0-9a-f]{40}$ ]]; then
    echo "The snapshot release must be an exact immutable 40-character release." >&2
    exit 1
fi
python_command=/usr/bin/python3
recovery_cli=/usr/local/libexec/careertrellis/generated_object_recovery.py
if [[ ! -x ${python_command} || ! -f ${recovery_cli} || -L ${recovery_cli} || \
      $(realpath "${recovery_cli}") != "${recovery_cli}" || \
      $(stat -c '%U:%G %a' "${recovery_cli}") != 'root:root 644' ]]; then
    echo "The reviewed generated-object recovery helper is unavailable." >&2
    exit 1
fi

for command_name in awk date flock grep mktemp realpath rm runuser sed stat systemctl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "A required generated-object recovery command is unavailable." >&2
        exit 1
    fi
done
if ! rclone_command=$(command -v rclone); then
    echo "A required generated-object recovery command is unavailable." >&2
    exit 1
fi
if [[ ! -x /usr/bin/python3 || ! -x /usr/bin/env || ! -x /usr/bin/date || \
      ! -f ${backup_health_core} || -L ${backup_health_core} || \
      $(realpath "${backup_health_core}" 2>/dev/null || true) != "${backup_health_core}" || \
      $(stat -c '%U:%G %a' "${backup_health_core}" 2>/dev/null || true) != "root:root 644" ]]; then
    echo "The reviewed backup-health publisher is unavailable." >&2
    exit 1
fi
if [[ ! -d ${backup_health_receipt_root} || -L ${backup_health_receipt_root} || \
      $(realpath "${backup_health_receipt_root}") != "${backup_health_receipt_root}" || \
      $(stat -c '%U:%G %a' "${backup_health_receipt_root}" 2>/dev/null || true) != "root:root 700" ]]; then
    echo "The protected backup-health receipt root is invalid." >&2
    exit 1
fi
if ! id "${recovery_user}" >/dev/null 2>&1 || [[ $(id -gn "${recovery_user}") != "${recovery_group}" ]]; then
    echo "The dedicated generated-object recovery account is unavailable." >&2
    exit 1
fi
if [[ ! -d ${recovery_state} || -L ${recovery_state} || $(stat -c '%U:%G %a' "${recovery_state}") != "${recovery_user}:${recovery_group} 700" ]]; then
    echo "The dedicated generated-object recovery state is invalid." >&2
    exit 1
fi
if [[ ! -f ${rclone_config} || -L ${rclone_config} || $(stat -c '%U:%G %a' "${rclone_config}") != "${recovery_user}:${recovery_group} 600" ]]; then
    echo "The dedicated generated-object recovery rclone configuration is invalid." >&2
    exit 1
fi
if [[ ! -f ${recovery_environment_file} || -L ${recovery_environment_file} || $(stat -c '%U:%G %a' "${recovery_environment_file}") != "root:root 600" ]]; then
    echo "The generated-object source environment must be a regular root:root mode 0600 file." >&2
    exit 1
fi

validate_source_environment_allowlist() {
    local line setting_name
    while IFS= read -r line || [[ -n ${line} ]]; do
        [[ -z ${line} || ${line} =~ ^[[:space:]]*# ]] && continue
        if [[ ! ${line} =~ ^([A-Z0-9_]+)=(.*)$ ]]; then
            return 1
        fi
        setting_name=${BASH_REMATCH[1]}
        case "${setting_name}" in
            CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ENDPOINT|\
            CAREERTRELLIS_GENERATED_OBJECT_SOURCE_BUCKET|\
            CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ACCESS_KEY_ID|\
            CAREERTRELLIS_GENERATED_OBJECT_SOURCE_SECRET_ACCESS_KEY|\
            CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_REMOTE|\
            CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_ROOT) ;;
            *) return 1 ;;
        esac
    done < "${recovery_environment_file}"
}

read_exact_setting() {
    local setting_name=$1
    local setting_value
    [[ $(grep -Ec "^${setting_name}=" "${recovery_environment_file}") -eq 1 ]] || return 1
    setting_value=$(sed -n "s/^${setting_name}=//p" "${recovery_environment_file}")
    [[ -n ${setting_value} && ${setting_value} != *$'\r'* && ${setting_value} != *$'\n'* ]] || return 1
    printf '%s' "${setting_value}"
}

if ! validate_source_environment_allowlist || \
   ! source_endpoint=$(read_exact_setting CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ENDPOINT) || \
   ! source_bucket=$(read_exact_setting CAREERTRELLIS_GENERATED_OBJECT_SOURCE_BUCKET) || \
   ! source_access_key_id=$(read_exact_setting CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ACCESS_KEY_ID) || \
   ! source_secret_access_key=$(read_exact_setting CAREERTRELLIS_GENERATED_OBJECT_SOURCE_SECRET_ACCESS_KEY) || \
   ! onedrive_remote=$(read_exact_setting CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_REMOTE) || \
   ! onedrive_root=$(read_exact_setting CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_ROOT); then
    echo "The generated-object source environment has an invalid assignment contract." >&2
    exit 1
fi
if [[ ! ${source_endpoint} =~ ^https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com/?$ ]] || \
   [[ ! ${source_bucket} =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || \
   [[ ! ${onedrive_remote} =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]] || \
   [[ ${onedrive_remote,,} == "generatedobjectsource" || ${onedrive_remote,,} == "generatedobjectrestore" ]] || \
   [[ ! ${onedrive_root} =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$ ]] || \
   [[ ${onedrive_root} == */ || ${onedrive_root} == *//* || ${onedrive_root} == *../* || ${onedrive_root} == ../* || ${onedrive_root} == */.. ]] || \
   [[ ${source_endpoint}${source_bucket}${source_access_key_id}${source_secret_access_key}${onedrive_remote}${onedrive_root} == *replace-with* ]]; then
    echo "The generated-object source environment has an invalid assignment contract." >&2
    exit 1
fi
if [[ $(grep -Ec '^\[[^]]+\]$' "${rclone_config}") -ne 1 || $(grep -Fxc "[${onedrive_remote}]" "${rclone_config}") -ne 1 ]] || ! awk -v wanted="[${onedrive_remote}]" '
    $0 == wanted { inside = 1; next }
    /^\[/ { inside = 0 }
    inside && $0 ~ /^[[:space:]]*type[[:space:]]*=/ {
        type_count += 1
        if ($0 ~ /^[[:space:]]*type[[:space:]]*=[[:space:]]*onedrive[[:space:]]*$/) found = 1
    }
    END { exit(type_count == 1 && found ? 0 : 1) }
' "${rclone_config}"; then
    echo "The dedicated rclone configuration must contain exactly one reviewed OneDrive remote." >&2
    exit 1
fi

if [[ ! -f ${maintenance_lock_helper} || -L ${maintenance_lock_helper} || $(stat -c '%U:%G %a' "${maintenance_lock_helper}") != "root:root 755" ]]; then
    echo "The reviewed maintenance-lock helper is unavailable." >&2
    exit 1
fi
# shellcheck source=maintenance-lock.sh
source "${maintenance_lock_helper}"
acquire_careertrellis_maintenance_lock
exec 9>/run/lock/careertrellis-generated-object-recovery.lock
if ! flock -n 9; then
    echo "Another generated-object recovery operation is running." >&2
    exit 1
fi

if [[ ! -x ${authority_helper} || -L ${authority_helper} ]]; then
    echo "Hosted authority state is unavailable." >&2
    exit 1
fi
authority_before=$("${authority_helper}" status)
require_stopped_writers() {
    local service state
    for service in careertrellis-web.service careertrellis-worker.service careertrellis-erasure-worker.service; do
        state=$(systemctl show "${service}" --property=ActiveState --value)
        if [[ ${state} != inactive ]]; then
            echo "Stop the web, search-worker, and erasure-worker services before a generated-object capture." >&2
            return 1
        fi
    done
}
if [[ ${mode} != publish-captured ]]; then
    require_stopped_writers
fi

operation_started_epoch=$(/usr/bin/date -u +%s)
operation_started_at=$(/usr/bin/date -u +'%Y-%m-%dT%H:%M:%SZ')
if [[ ! ${operation_started_epoch} =~ ^[0-9]+$ ]]; then
    echo "The generated-object snapshot clock is invalid." >&2
    exit 1
fi

snapshot_id=${release_revision}-${backup_label}
work_root=${recovery_state}/work
if [[ ! -d ${work_root} || -L ${work_root} || $(stat -c '%U:%G %a' "${work_root}") != "${recovery_user}:${recovery_group} 700" ]]; then
    echo "The dedicated generated-object recovery work directory is invalid." >&2
    exit 1
fi
work_dir=$(mktemp -d "${work_root}/mirror-${snapshot_id}.XXXXXX")
chown "${recovery_user}:${recovery_group}" "${work_dir}"
chmod 0700 "${work_dir}"
source_root=${work_dir}/objects
marker_root=${work_dir}/marker
install -d -o "${recovery_user}" -g "${recovery_group}" -m 0700 "${source_root}" "${marker_root}"
manifest_path=${marker_root}/snapshot-manifest.json
rclone_error_log=${work_dir}/rclone-error.log

cleanup_and_verify_authority() {
    local operation_status=$?
    local authority_after=
    trap - EXIT
    if [[ -n ${authority_before:-} ]]; then
        authority_after=$("${authority_helper}" status 2>/dev/null || true)
        if [[ ${authority_after} != "${authority_before}" ]]; then
            echo "Hosted authority changed during generated-object recovery." >&2
            operation_status=1
        fi
    fi
    if [[ -n ${work_dir:-} && ${work_dir} == "${work_root}"/mirror-* && -d ${work_dir} && ! -L ${work_dir} ]]; then
        if ! rm -rf -- "${work_dir}" || [[ -e ${work_dir} || -L ${work_dir} ]]; then
            operation_status=1
        fi
    fi
    if [[ ${operation_status} -ne 0 ]] && \
        ! publish_health_failure_receipt >/dev/null 2>&1; then
        echo "The generated-object mirror failure receipt could not be retained." >&2
    fi
    exit "${operation_status}"
}
trap cleanup_and_verify_authority EXIT

publish_health_receipt() {
    local object_count=$1
    local byte_count=$2
    local completed_epoch completed_at elapsed_seconds
    # Actual terminal ordering; the evaluator ages mirror evidence from the
    # retained capture start so an upload/replay cannot refresh source age.
    completed_epoch=$(/usr/bin/date -u +%s)
    completed_at=$(/usr/bin/date -u +'%Y-%m-%dT%H:%M:%SZ')
    if [[ ! ${completed_epoch} =~ ^[0-9]+$ || ${completed_epoch} -lt ${operation_started_epoch} ]]; then
        return 1
    fi
    elapsed_seconds=$(( completed_epoch - operation_started_epoch ))
    printf '{"schema":"careertrellis.backup-health-operation/v1","operation":"generated_object_mirror","result":"ok","started_at":"%s","completed_at":"%s","object_count":%s,"byte_count":%s,"divergence_count":0,"elapsed_seconds":%s}\n' \
        "${operation_started_at}" "${completed_at}" "${object_count}" "${byte_count}" "${elapsed_seconds}" | \
        /usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/python3 -B "${backup_health_core}" publish \
            --receipt-root "${backup_health_receipt_root}" >/dev/null
}

publish_health_failure_receipt() {
    local completed_epoch completed_at elapsed_seconds
    completed_epoch=$(/usr/bin/date -u +%s)
    completed_at=$(/usr/bin/date -u +'%Y-%m-%dT%H:%M:%SZ')
    if [[ ! ${completed_epoch} =~ ^[0-9]+$ || ${completed_epoch} -lt ${operation_started_epoch} ]]; then
        return 1
    fi
    elapsed_seconds=$(( completed_epoch - operation_started_epoch ))
    printf '{"schema":"careertrellis.backup-health-operation/v1","operation":"generated_object_mirror","result":"failed","started_at":"%s","completed_at":"%s","object_count":0,"byte_count":0,"divergence_count":1,"elapsed_seconds":%s}\n' \
        "${operation_started_at}" "${completed_at}" "${elapsed_seconds}" | \
        /usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/python3 -B "${backup_health_core}" publish \
            --receipt-root "${backup_health_receipt_root}" >/dev/null
}

finish_local_cleanup_and_authority() {
    local authority_after
    if [[ -z ${work_dir:-} || ${work_dir} != "${work_root}"/mirror-* || ! -d ${work_dir} || -L ${work_dir} ]]; then
        return 1
    fi
    rm -rf -- "${work_dir}"
    if [[ -e ${work_dir} || -L ${work_dir} ]]; then
        return 1
    fi
    work_dir=
    authority_after=$("${authority_helper}" status 2>/dev/null) || return 1
    if [[ ${authority_after} != "${authority_before}" ]]; then
        return 1
    fi
}

clear_exported_environment() {
    local variable_name
    while IFS= read -r variable_name; do
        unset "${variable_name}" 2>/dev/null || true
    done < <(compgen -e)
}

run_rclone() (
    local saved_recovery_state=${recovery_state}
    local saved_rclone_config=${rclone_config}
    local saved_source_access_key_id=${source_access_key_id}
    local saved_source_secret_access_key=${source_secret_access_key}
    local saved_source_endpoint=${source_endpoint}
    local saved_recovery_user=${recovery_user}
    local saved_rclone_command=${rclone_command}
    clear_exported_environment
    export HOME="${saved_recovery_state}"
    export PATH=/usr/local/bin:/usr/bin:/bin
    export RCLONE_CONFIG="${saved_rclone_config}"
    export RCLONE_CONFIG_GENERATEDOBJECTSOURCE_TYPE=s3
    export RCLONE_CONFIG_GENERATEDOBJECTSOURCE_PROVIDER=Cloudflare
    export RCLONE_CONFIG_GENERATEDOBJECTSOURCE_ACCESS_KEY_ID="${saved_source_access_key_id}"
    export RCLONE_CONFIG_GENERATEDOBJECTSOURCE_SECRET_ACCESS_KEY="${saved_source_secret_access_key}"
    export RCLONE_CONFIG_GENERATEDOBJECTSOURCE_ENDPOINT="${saved_source_endpoint}"
    export RCLONE_CONFIG_GENERATEDOBJECTSOURCE_NO_CHECK_BUCKET=true
    exec runuser --preserve-environment -u "${saved_recovery_user}" -- "${saved_rclone_command}" "$@"
)

run_recovery_cli() (
    local saved_recovery_state=${recovery_state}
    local saved_recovery_user=${recovery_user}
    local saved_python_command=${python_command}
    local saved_recovery_cli=${recovery_cli}
    clear_exported_environment
    export HOME="${saved_recovery_state}"
    export PATH=/usr/local/bin:/usr/bin:/bin
    exec runuser --preserve-environment -u "${saved_recovery_user}" -- \
        "${saved_python_command}" "${saved_recovery_cli}" "$@"
)

rclone_fixed() {
    if ! run_rclone "$@" > /dev/null 2> "${rclone_error_log}"; then
        echo "Generated-object recovery transfer verification failed." >&2
        return 1
    fi
}

if [[ ${mode} != publish-captured ]]; then
# This phase does not contact OneDrive. The caller must retain the paired full
# database receipt from this same uninterrupted stopped-writer interval.
rclone_fixed copy "generatedobjectsource:${source_bucket}" "${source_root}" --immutable --stats 0 --log-level ERROR
rclone_fixed check "generatedobjectsource:${source_bucket}" "${source_root}" --download --stats 0 --log-level ERROR
manifest_receipt=$(run_recovery_cli create-manifest \
    "${source_root}" "${release_revision}" "${backup_label}" "${snapshot_id}" "${manifest_path}")
if ! manifest_values=$(
    export CAREERTRELLIS_EXPECTED_RELEASE="${release_revision}"
    export CAREERTRELLIS_EXPECTED_BACKUP_LABEL="${backup_label}"
    export CAREERTRELLIS_EXPECTED_SNAPSHOT_ID="${snapshot_id}"
    printf '%s' "${manifest_receipt}" | "${python_command}" -c '
import json
import os
import re
import sys

try:
    receipt = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(1)
expected = {
    "release": os.environ["CAREERTRELLIS_EXPECTED_RELEASE"],
    "backup_label": os.environ["CAREERTRELLIS_EXPECTED_BACKUP_LABEL"],
    "snapshot_id": os.environ["CAREERTRELLIS_EXPECTED_SNAPSHOT_ID"],
}
if any(receipt.get(name) != value for name, value in expected.items()):
    raise SystemExit(1)
value = receipt.get("manifest_sha256", "")
if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
    raise SystemExit(1)
object_count = receipt.get("object_count")
ciphertext_bytes = receipt.get("ciphertext_bytes")
if (
    not isinstance(object_count, int)
    or isinstance(object_count, bool)
    or object_count < 0
    or not isinstance(ciphertext_bytes, int)
    or isinstance(ciphertext_bytes, bool)
    or ciphertext_bytes < 0
):
    raise SystemExit(1)
print(value, object_count, ciphertext_bytes, sep="\t")
'
); then
    echo "Generated-object recovery manifest failed." >&2
    exit 1
fi
IFS=$'\t' read -r manifest_sha256 health_object_count health_byte_count <<<"${manifest_values}"
if [[ ! ${manifest_sha256} =~ ^[0-9a-f]{64}$ || ! ${health_object_count} =~ ^[0-9]+$ || ! ${health_byte_count} =~ ^[0-9]+$ ]]; then
    echo "Generated-object recovery manifest failed." >&2
    exit 1
fi
require_stopped_writers
if [[ $("${authority_helper}" status) != "${authority_before}" ]]; then
    echo "Hosted authority changed during generated-object capture." >&2
    exit 1
fi
capture_receipt=$(/usr/bin/env -i PATH=/usr/bin:/bin "${python_command}" -B "${recovery_cli}" seal-capture \
    "${work_dir}" "${release_revision}" "${backup_label}" "${manifest_sha256}" \
    "${authority_before}" "$(id -g "${recovery_user}")" "${operation_started_epoch}")
require_stopped_writers
if [[ ${mode} == capture ]]; then
    finish_local_cleanup_and_authority
    trap - EXIT
    printf '%s\n' "${capture_receipt}"
    exit 0
fi
fi

# The online phase reads only a durable, root-sealed local snapshot. No live R2
# requests occur here; failed uploads retain that capture for an explicit retry.
capture_receipt=$(/usr/bin/env -i PATH=/usr/bin:/bin "${python_command}" -B "${recovery_cli}" verify-capture \
    "${release_revision}" "${backup_label}" "${authority_before}" "$(id -g "${recovery_user}")")
capture_values=$(printf '%s' "${capture_receipt}" | "${python_command}" -c 'import json,sys; s=json.load(sys.stdin); print(s["manifest_sha256"],s["object_count"],s["ciphertext_bytes"],s["capture_started_epoch"],s["captured_epoch"],sep="\t")')
IFS=$'\t' read -r manifest_sha256 health_object_count health_byte_count operation_started_epoch capture_completed_epoch <<<"${capture_values}"
operation_started_at=$(/usr/bin/date -u -d "@${operation_started_epoch}" +'%Y-%m-%dT%H:%M:%SZ')
source_root=${capture_root}/${snapshot_id}/objects
marker_root=${capture_root}/${snapshot_id}/marker
destination=${onedrive_remote}:${onedrive_root}/${backup_label}

# A successful mkdir followed by a successful empty inventory supports a fresh
# OneDrive folder. Provider/auth failures are never interpreted as emptiness.
rclone_fixed mkdir "${destination}" --stats 0 --log-level ERROR
if ! destination_size=$(run_rclone size "${destination}" --json 2> "${rclone_error_log}") || \
   ! printf '%s' "${destination_size}" | "${python_command}" -c 'import json,sys; s=json.load(sys.stdin); raise SystemExit(0 if type(s.get("count")) is int and s["count"] == 0 and type(s.get("bytes")) is int and s["bytes"] == 0 else 1)' 2>/dev/null; then
    echo "The OneDrive snapshot destination must be empty." >&2
    exit 1
fi

rclone_fixed copy "${source_root}" "${destination}/objects" --immutable --stats 0 --log-level ERROR
rclone_fixed check "${source_root}" "${destination}/objects" --download --stats 0 --log-level ERROR

# The manifest is the completion marker. It is copied only after every
# ciphertext object has passed a downloaded byte comparison.
rclone_fixed copy "${marker_root}" "${destination}" --immutable --stats 0 --log-level ERROR
rclone_fixed check "${marker_root}" "${destination}" --one-way --download --stats 0 --log-level ERROR

# Recheck seal age, identity and bytes before any successful health publication.
/usr/bin/env -i PATH=/usr/bin:/bin "${python_command}" -B "${recovery_cli}" verify-capture \
    "${release_revision}" "${backup_label}" "${authority_before}" "$(id -g "${recovery_user}")" >/dev/null

if ! finish_local_cleanup_and_authority; then
    echo "Generated-object recovery cleanup or authority verification failed." >&2
    exit 1
fi
if ! publish_health_receipt "${health_object_count}" "${health_byte_count}"; then
    echo "Generated-object backup-health evidence could not be retained safely." >&2
    exit 1
fi

trap - EXIT
printf '{"authority":"%s","backup_label":"%s","manifest_sha256":"%s","release":"%s","result":"onedrive_snapshot_created","snapshot_id":"%s"}\n' \
    "${authority_before}" "${backup_label}" "${manifest_sha256}" "${release_revision}" "${snapshot_id}"
