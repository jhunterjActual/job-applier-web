#!/usr/bin/env bash
set -euo pipefail

fixed_error() {
    printf '%s\n' \
        'operation=caddy-log-privacy-check' \
        'result=blocked' \
        'error_code=wrapper_invalid' \
        'records=0' \
        'query_records=0' \
        'representative_records=0' \
        'source_disclosed=no' \
        'personal_data_disclosed=no' \
        'secret_disclosed=no'
}

if [[ ${EUID} -ne 0 || $# -ne 0 ]]; then
    fixed_error
    exit 1
fi

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
   [[ ! -f ${release_dir}/backend/caddy_log_privacy.py ]] || \
   [[ -L ${release_dir}/backend/caddy_log_privacy.py ]]; then
    fixed_error
    exit 1
fi

config_path=/etc/caddy/Caddyfile
release_config=${release_dir}/deploy/caddy/Caddyfile.staging
log_prefix=/var/log/caddy/careertrellis-staging-access-v7
log_path=${log_prefix}.log
if [[ ! -f ${config_path} || -L ${config_path} || \
      $(/usr/bin/stat -c '%U:%G %a' "${config_path}" 2>/dev/null) != 'root:root 644' || \
      ! -f ${release_config} || -L ${release_config} ]] || \
   ! /usr/bin/cmp -s "${config_path}" "${release_config}"; then
    fixed_error
    exit 1
fi

collect_log_paths() {
    shopt -s nullglob
    local rotations=("${log_prefix}"-*.log "${log_prefix}"-*.log.gz)
    shopt -u nullglob
    collected_paths=("${log_path}" "${rotations[@]}")
}

snapshot_metadata() {
    local candidate
    for candidate in "${collected_paths[@]}"; do
        /usr/bin/stat -c '%d:%i:%s:%y' "${candidate}" 2>/dev/null || return 1
    done
}

collect_log_paths
log_paths=("${collected_paths[@]}")
arguments=(--config "${config_path}")
for candidate in "${log_paths[@]}"; do
    if [[ ! -f ${candidate} || -L ${candidate} || \
          $(/usr/bin/stat -c '%U:%G %a' "${candidate}" 2>/dev/null) != 'caddy:caddy 600' ]]; then
        fixed_error
        exit 1
    fi
    arguments+=(--log "${candidate}")
done

if ! before_metadata=$(snapshot_metadata); then
    fixed_error
    exit 1
fi
set +e
result=$(/usr/bin/env -i \
    PATH=/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    "${release_dir}/backend/venv/bin/python" -B \
    "${release_dir}/backend/caddy_log_privacy.py" \
    "${arguments[@]}" 2>/dev/null)
status=$?
set -e
if [[ ${status} -ne 0 ]]; then
    printf '%s\n' "${result}"
    exit "${status}"
fi
collect_log_paths
if [[ ${#collected_paths[@]} -ne ${#log_paths[@]} ]]; then
    fixed_error
    exit 1
fi
for index in "${!log_paths[@]}"; do
    if [[ ${collected_paths[index]} != "${log_paths[index]}" ]]; then
        fixed_error
        exit 1
    fi
done
if ! after_metadata=$(snapshot_metadata) || [[ ${after_metadata} != "${before_metadata}" ]]; then
    fixed_error
    exit 1
fi
printf '%s\n' "${result}"
