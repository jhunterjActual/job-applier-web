#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

readonly target_confirmation=I_HAVE_VERIFIED_THE_R2_TARGET_IS_DISPOSABLE_AND_EMPTY
readonly leave_confirmation=I_UNDERSTAND_THE_DISPOSABLE_R2_TARGET_WILL_BE_LEFT_INTACT
readonly release_root=/opt/careertrellis/staging
readonly recovery_user=careertrellis-object-recovery
readonly recovery_group=careertrellis-object-recovery
readonly recovery_state=/var/lib/careertrellis/generated-object-recovery
readonly rclone_config=${recovery_state}/rclone.conf
readonly source_environment_file=/etc/careertrellis/generated-object-recovery.env
readonly authority_helper=/usr/local/sbin/careertrellis-authority
readonly maintenance_lock_helper=/usr/local/libexec/careertrellis/maintenance-lock.sh

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the generated-object restore test as root." >&2
    exit 1
fi

release_dir=${1:-}
backup_label=${2:-}
expected_manifest_sha256=${3:-}
target_environment_file=${4:-}
target_acknowledgement=${5:-}
leave_acknowledgement=${6:-}
if [[ $# -ne 6 || ${target_acknowledgement} != "${target_confirmation}" || ${leave_acknowledgement} != "${leave_confirmation}" ]]; then
    echo "Usage: $0 /opt/careertrellis/staging/releases/EXACT_COMMIT YYYYMMDD-HHMMSSF EXPECTED_MANIFEST_SHA256 /root-owned/disposable-r2.env ${target_confirmation} ${leave_confirmation}" >&2
    exit 1
fi
if [[ ! ${backup_label} =~ ^[0-9]{8}-[0-9]{6}F$ || ! ${expected_manifest_sha256} =~ ^[0-9a-f]{64}$ ]]; then
    echo "The backup label or expected manifest digest is invalid." >&2
    exit 1
fi
if [[ ! -d ${release_dir} || -L ${release_dir} ]]; then
    echo "The restore-test release must be an exact immutable 40-character release." >&2
    exit 1
fi
release_dir=$(realpath "${release_dir}")
release_revision=$(basename "${release_dir}")
if [[ $(dirname "${release_dir}") != "${release_root}/releases" || ! ${release_revision} =~ ^[0-9a-f]{40}$ ]]; then
    echo "The restore-test release must be an exact immutable 40-character release." >&2
    exit 1
fi
python_command=${release_dir}/backend/venv/bin/python
recovery_cli=${release_dir}/backend/generated_object_recovery.py
if [[ ! -x ${python_command} || ! -f ${recovery_cli} || -L ${recovery_cli} ]]; then
    echo "The restore-test release is incomplete." >&2
    exit 1
fi

for command_name in awk flock grep mktemp realpath rm runuser sed stat systemctl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "A required generated-object recovery command is unavailable." >&2
        exit 1
    fi
done
if ! rclone_command=$(command -v rclone); then
    echo "A required generated-object recovery command is unavailable." >&2
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

validate_root_environment_file() {
    local environment_file=$1
    [[ -f ${environment_file} && ! -L ${environment_file} && $(stat -c '%U:%G %a' "${environment_file}") == "root:root 600" ]]
}
if ! validate_root_environment_file "${source_environment_file}" || ! validate_root_environment_file "${target_environment_file}"; then
    echo "Every generated-object recovery environment must be a regular root:root mode 0600 file." >&2
    exit 1
fi
if [[ $(realpath "${source_environment_file}") == $(realpath "${target_environment_file}") ]]; then
    echo "The disposable R2 target must be identity-distinct from the source." >&2
    exit 1
fi

validate_environment_allowlist() {
    local environment_file=$1
    local contract=$2
    local line setting_name
    while IFS= read -r line || [[ -n ${line} ]]; do
        [[ -z ${line} || ${line} =~ ^[[:space:]]*# ]] && continue
        if [[ ! ${line} =~ ^([A-Z0-9_]+)=(.*)$ ]]; then
            return 1
        fi
        setting_name=${BASH_REMATCH[1]}
        if [[ ${contract} == source ]]; then
            case "${setting_name}" in
                CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ENDPOINT|\
                CAREERTRELLIS_GENERATED_OBJECT_SOURCE_BUCKET|\
                CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ACCESS_KEY_ID|\
                CAREERTRELLIS_GENERATED_OBJECT_SOURCE_SECRET_ACCESS_KEY|\
                CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_REMOTE|\
                CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_ROOT) ;;
                *) return 1 ;;
            esac
        else
            case "${setting_name}" in
                CAREERTRELLIS_GENERATED_OBJECT_RESTORE_ENDPOINT|\
                CAREERTRELLIS_GENERATED_OBJECT_RESTORE_BUCKET|\
                CAREERTRELLIS_GENERATED_OBJECT_RESTORE_ACCESS_KEY_ID|\
                CAREERTRELLIS_GENERATED_OBJECT_RESTORE_SECRET_ACCESS_KEY) ;;
                *) return 1 ;;
            esac
        fi
    done < "${environment_file}"
}

read_exact_setting() {
    local environment_file=$1
    local setting_name=$2
    local setting_value
    [[ $(grep -Ec "^${setting_name}=" "${environment_file}") -eq 1 ]] || return 1
    setting_value=$(sed -n "s/^${setting_name}=//p" "${environment_file}")
    [[ -n ${setting_value} && ${setting_value} != *$'\r'* && ${setting_value} != *$'\n'* ]] || return 1
    printf '%s' "${setting_value}"
}

if ! validate_environment_allowlist "${source_environment_file}" source || \
   ! validate_environment_allowlist "${target_environment_file}" target || \
   ! source_endpoint=$(read_exact_setting "${source_environment_file}" CAREERTRELLIS_GENERATED_OBJECT_SOURCE_ENDPOINT) || \
   ! source_bucket=$(read_exact_setting "${source_environment_file}" CAREERTRELLIS_GENERATED_OBJECT_SOURCE_BUCKET) || \
   ! onedrive_remote=$(read_exact_setting "${source_environment_file}" CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_REMOTE) || \
   ! onedrive_root=$(read_exact_setting "${source_environment_file}" CAREERTRELLIS_GENERATED_OBJECT_ONEDRIVE_ROOT) || \
   ! target_endpoint=$(read_exact_setting "${target_environment_file}" CAREERTRELLIS_GENERATED_OBJECT_RESTORE_ENDPOINT) || \
   ! target_bucket=$(read_exact_setting "${target_environment_file}" CAREERTRELLIS_GENERATED_OBJECT_RESTORE_BUCKET) || \
   ! target_access_key_id=$(read_exact_setting "${target_environment_file}" CAREERTRELLIS_GENERATED_OBJECT_RESTORE_ACCESS_KEY_ID) || \
   ! target_secret_access_key=$(read_exact_setting "${target_environment_file}" CAREERTRELLIS_GENERATED_OBJECT_RESTORE_SECRET_ACCESS_KEY); then
    echo "A generated-object recovery environment has an invalid assignment contract." >&2
    exit 1
fi
if [[ ! ${source_endpoint} =~ ^https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com/?$ ]] || \
   [[ ! ${target_endpoint} =~ ^https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com/?$ ]] || \
   [[ ! ${source_bucket} =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ || ! ${target_bucket} =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || \
   [[ ! ${onedrive_remote} =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]] || \
   [[ ${onedrive_remote,,} == "generatedobjectsource" || ${onedrive_remote,,} == "generatedobjectrestore" ]] || \
   [[ ! ${onedrive_root} =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$ ]] || \
   [[ ${onedrive_root} == */ || ${onedrive_root} == *//* || ${onedrive_root} == *../* || ${onedrive_root} == ../* || ${onedrive_root} == */.. ]] || \
   [[ ${source_endpoint}${source_bucket}${target_endpoint}${target_bucket}${target_access_key_id}${target_secret_access_key}${onedrive_remote}${onedrive_root} == *replace-with* ]]; then
    echo "A generated-object recovery environment has an invalid assignment contract." >&2
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

if ! CAREERTRELLIS_RECOVERY_SOURCE_ENDPOINT="${source_endpoint}" \
    CAREERTRELLIS_RECOVERY_SOURCE_BUCKET="${source_bucket}" \
    CAREERTRELLIS_RECOVERY_TARGET_ENDPOINT="${target_endpoint}" \
    CAREERTRELLIS_RECOVERY_TARGET_BUCKET="${target_bucket}" \
    "${python_command}" -c '
import os
from urllib.parse import urlsplit

def identity(endpoint, bucket):
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError
    return parsed.hostname.casefold().rstrip("."), parsed.port or 443, parsed.path.rstrip("/"), bucket

try:
    source = identity(os.environ["CAREERTRELLIS_RECOVERY_SOURCE_ENDPOINT"], os.environ["CAREERTRELLIS_RECOVERY_SOURCE_BUCKET"])
    target = identity(os.environ["CAREERTRELLIS_RECOVERY_TARGET_ENDPOINT"], os.environ["CAREERTRELLIS_RECOVERY_TARGET_BUCKET"])
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
raise SystemExit(1 if source == target else 0)
'; then
    echo "The disposable R2 target must be identity-distinct from the source." >&2
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
for service in careertrellis-web.service careertrellis-worker.service careertrellis-erasure-worker.service; do
    if systemctl is-active --quiet "${service}"; then
        echo "Stop the web, search-worker, and erasure-worker services before a generated-object restore test." >&2
        exit 1
    fi
done

snapshot_id=${release_revision}-${backup_label}
work_root=${recovery_state}/work
if [[ ! -d ${work_root} || -L ${work_root} || $(stat -c '%U:%G %a' "${work_root}") != "${recovery_user}:${recovery_group} 700" ]]; then
    echo "The dedicated generated-object recovery work directory is invalid." >&2
    exit 1
fi
work_dir=$(mktemp -d "${work_root}/restore-${snapshot_id}.XXXXXX")
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
    if [[ -n ${work_dir:-} && ${work_dir} == "${work_root}"/restore-* && -d ${work_dir} && ! -L ${work_dir} ]]; then
        rm -rf -- "${work_dir}"
    fi
    exit "${operation_status}"
}
trap cleanup_and_verify_authority EXIT

clear_exported_environment() {
    local variable_name
    while IFS= read -r variable_name; do
        unset "${variable_name}" 2>/dev/null || true
    done < <(compgen -e)
}

run_rclone() (
    local saved_recovery_state=${recovery_state}
    local saved_rclone_config=${rclone_config}
    local saved_target_access_key_id=${target_access_key_id}
    local saved_target_secret_access_key=${target_secret_access_key}
    local saved_target_endpoint=${target_endpoint}
    local saved_recovery_user=${recovery_user}
    local saved_rclone_command=${rclone_command}
    clear_exported_environment
    export HOME="${saved_recovery_state}"
    export PATH=/usr/local/bin:/usr/bin:/bin
    export RCLONE_CONFIG="${saved_rclone_config}"
    export RCLONE_CONFIG_GENERATEDOBJECTRESTORE_TYPE=s3
    export RCLONE_CONFIG_GENERATEDOBJECTRESTORE_PROVIDER=Cloudflare
    export RCLONE_CONFIG_GENERATEDOBJECTRESTORE_ACCESS_KEY_ID="${saved_target_access_key_id}"
    export RCLONE_CONFIG_GENERATEDOBJECTRESTORE_SECRET_ACCESS_KEY="${saved_target_secret_access_key}"
    export RCLONE_CONFIG_GENERATEDOBJECTRESTORE_ENDPOINT="${saved_target_endpoint}"
    export RCLONE_CONFIG_GENERATEDOBJECTRESTORE_NO_CHECK_BUCKET=true
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

if ! target_size=$(run_rclone size "generatedobjectrestore:${target_bucket}" --json 2> "${rclone_error_log}") || \
   ! printf '%s' "${target_size}" | "${python_command}" -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("count") == 0 else 1)' 2>/dev/null; then
    echo "The disposable R2 target must be empty." >&2
    exit 1
fi

snapshot_source=${onedrive_remote}:${onedrive_root}/${backup_label}
rclone_fixed copy "${snapshot_source}" "${marker_root}" --include snapshot-manifest.json --immutable --stats 0 --log-level ERROR
rclone_fixed copy "${snapshot_source}/objects" "${source_root}" --immutable --stats 0 --log-level ERROR
rclone_fixed check "${snapshot_source}/objects" "${source_root}" --download --stats 0 --log-level ERROR
manifest_receipt=$(run_recovery_cli verify-manifest "${source_root}" "${manifest_path}" "${expected_manifest_sha256}")
if ! (
    export CAREERTRELLIS_EXPECTED_RELEASE="${release_revision}"
    export CAREERTRELLIS_EXPECTED_BACKUP_LABEL="${backup_label}"
    export CAREERTRELLIS_EXPECTED_SNAPSHOT_ID="${snapshot_id}"
    export CAREERTRELLIS_EXPECTED_MANIFEST_SHA256="${expected_manifest_sha256}"
    printf '%s' "${manifest_receipt}" | "${python_command}" -c '
import json
import os
import sys

try:
    receipt = json.load(sys.stdin)
except (TypeError, ValueError):
    raise SystemExit(1)
expected = {
    "release": os.environ["CAREERTRELLIS_EXPECTED_RELEASE"],
    "backup_label": os.environ["CAREERTRELLIS_EXPECTED_BACKUP_LABEL"],
    "snapshot_id": os.environ["CAREERTRELLIS_EXPECTED_SNAPSHOT_ID"],
    "manifest_sha256": os.environ["CAREERTRELLIS_EXPECTED_MANIFEST_SHA256"],
}
if any(receipt.get(name) != value for name, value in expected.items()):
    raise SystemExit(1)
if receipt.get("manifest_verified") is not True:
    raise SystemExit(1)
'
); then
    echo "Generated-object recovery manifest failed." >&2
    exit 1
fi

rclone_fixed copy "${source_root}" "generatedobjectrestore:${target_bucket}" --immutable --stats 0 --log-level ERROR
rclone_fixed check "${source_root}" "generatedobjectrestore:${target_bucket}" --download --stats 0 --log-level ERROR

# Leave only canonical ciphertext objects in the disposable bucket. The
# manifest remains outside R2 because the application reconciler rejects
# every non-v1 object key. The whole target is intentionally left intact.

printf '{"authority":"%s","backup_label":"%s","manifest_sha256":"%s","release":"%s","result":"disposable_r2_restore_verified_and_left_intact","snapshot_id":"%s"}\n' \
    "${authority_before}" "${backup_label}" "${expected_manifest_sha256}" "${release_revision}" "${snapshot_id}"
