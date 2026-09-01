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
log_path=/var/log/caddy/careertrellis-staging-access.log
if [[ ! -f ${config_path} || -L ${config_path} || \
      $(/usr/bin/stat -c '%U:%G %a' "${config_path}" 2>/dev/null) != 'root:root 644' ]] || \
   [[ ! -f ${log_path} || -L ${log_path} || \
      $(/usr/bin/stat -c '%U:%G %a' "${log_path}" 2>/dev/null) != 'caddy:caddy 600' ]]; then
    fixed_error
    exit 1
fi

exec /usr/bin/env -i \
    PATH=/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    "${release_dir}/backend/venv/bin/python" -B \
    "${release_dir}/backend/caddy_log_privacy.py" \
    --config "${config_path}" \
    --log "${log_path}"
