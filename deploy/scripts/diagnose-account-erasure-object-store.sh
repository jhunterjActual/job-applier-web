#!/usr/bin/env bash
set -euo pipefail

fixed_error() {
    printf '%s\n' \
        'operation=account-erasure-object-storage-diagnostic' \
        'result=blocked' \
        'classification=wrapper_invalid' \
        'readiness_only=yes' \
        'delete_attempted=no' \
        'object_read_attempted=no' \
        'inventory_attempted=no' \
        'identity_disclosed=no' \
        'personal_data_disclosed=no' \
        'secret_disclosed=no'
}

if [[ ${EUID} -ne 0 || $# -ne 0 ]]; then
    fixed_error
    exit 1
fi

for command_path in /usr/bin/env /usr/bin/realpath /usr/bin/stat /usr/sbin/runuser; do
    if [[ ! -x ${command_path} || -L ${command_path} ]]; then
        fixed_error
        exit 1
    fi
done

if ! script_path=$(/usr/bin/realpath "$0" 2>/dev/null); then
    fixed_error
    exit 1
fi
script_dir=${script_path%/*}
if ! release_dir=$(/usr/bin/realpath "${script_dir}/../.." 2>/dev/null) || \
   ! current_release=$(/usr/bin/realpath /opt/careertrellis/staging/current 2>/dev/null); then
    fixed_error
    exit 1
fi
if [[ ! ${release_dir} =~ ^/opt/careertrellis/staging/releases/[0-9a-f]{40}$ ]] || \
   [[ ${release_dir} != "${current_release}" ]] || \
   [[ ! -x ${release_dir}/backend/venv/bin/python ]] || \
   [[ ! -f ${release_dir}/backend/account_erasure_diagnostics.py ]] || \
   [[ -L ${release_dir}/backend/account_erasure_diagnostics.py ]]; then
    fixed_error
    exit 1
fi

environment_file=/etc/careertrellis/account-erasure-object-storage.env
if [[ ! -f ${environment_file} || -L ${environment_file} || \
      $(/usr/bin/stat -c '%U:%G %a' "${environment_file}" 2>/dev/null) != 'root:root 600' ]]; then
    fixed_error
    exit 1
fi

allowed_names=(
    CAREERTRELLIS_OBJECT_STORE
    CAREERTRELLIS_OBJECT_STORE_ENDPOINT
    CAREERTRELLIS_OBJECT_STORE_BUCKET
    CAREERTRELLIS_OBJECT_STORE_ACCESS_KEY_ID
    CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY
)
declare -A allowed=()
declare -A settings=()
for name in "${allowed_names[@]}"; do
    allowed["${name}"]=1
done
while IFS= read -r line || [[ -n ${line} ]]; do
    [[ -z ${line} || ${line} == \#* ]] && continue
    if [[ ! ${line} =~ ^([A-Z0-9_]+)=(.*)$ ]]; then
        fixed_error
        exit 1
    fi
    name=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    if [[ -z ${allowed[${name}]:-} || -n ${settings[${name}]+present} || -z ${value} ]]; then
        fixed_error
        exit 1
    fi
    settings["${name}"]=${value}
done <"${environment_file}"
for name in "${allowed_names[@]}"; do
    if [[ -z ${settings[${name}]+present} ]]; then
        fixed_error
        exit 1
    fi
done
if [[ ${settings[CAREERTRELLIS_OBJECT_STORE]} != r2_encrypted ]]; then
    fixed_error
    exit 1
fi

exec /usr/bin/env -i \
    PATH=/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    CAREERTRELLIS_ENVIRONMENT=staging \
    CAREERTRELLIS_OBJECT_STORE="${settings[CAREERTRELLIS_OBJECT_STORE]}" \
    CAREERTRELLIS_OBJECT_STORE_ENDPOINT="${settings[CAREERTRELLIS_OBJECT_STORE_ENDPOINT]}" \
    CAREERTRELLIS_OBJECT_STORE_BUCKET="${settings[CAREERTRELLIS_OBJECT_STORE_BUCKET]}" \
    CAREERTRELLIS_OBJECT_STORE_ACCESS_KEY_ID="${settings[CAREERTRELLIS_OBJECT_STORE_ACCESS_KEY_ID]}" \
    CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY="${settings[CAREERTRELLIS_OBJECT_STORE_SECRET_ACCESS_KEY]}" \
    /usr/sbin/runuser --preserve-environment -u careertrellis-erasure-worker -- \
    "${release_dir}/backend/venv/bin/python" -B \
    "${release_dir}/backend/account_erasure_diagnostics.py"
