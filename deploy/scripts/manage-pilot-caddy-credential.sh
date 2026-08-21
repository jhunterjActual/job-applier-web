#!/bin/bash
set -Eeuo pipefail
set +x
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this pilot Caddy credential helper as root." >&2
    exit 1
fi

operation=${1:-}
if [[ $# -ne 1 || ! ${operation} =~ ^(inspect|check|install|rotate)$ ]]; then
    echo "Usage: $0 inspect|check|install|rotate" >&2
    exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
script_path=$(realpath "${BASH_SOURCE[0]}")
environment_file=/etc/caddy/careertrellis-staging.env
pilot_key=STAGING_BASIC_AUTH_PILOT_01_HASH
pilot_username=pilot-01
pilot_state=malformed

fail_validation() {
    echo "Pilot Caddy credential validation failed." >&2
    return 1
}

validate_environment() {
    local require_pilot=$1
    local owner_hash owner_username pilot_hash proxy_token

    pilot_state=malformed
    if [[ ! -f ${environment_file} || -L ${environment_file} || \
          $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
        return 1
    fi
    if grep -Ev '^(#.*|[[:space:]]*|CAREERTRELLIS_TRUSTED_PROXY_TOKEN=[^[:space:]]+|STAGING_BASIC_AUTH_USER=[^[:space:]]+|STAGING_BASIC_AUTH_HASH=[^[:space:]]+|STAGING_BASIC_AUTH_PILOT_01_HASH=[^[:space:]]+)$' \
        "${environment_file}" >/dev/null; then
        return 1
    fi
    if [[ $(grep -Ec '^CAREERTRELLIS_TRUSTED_PROXY_TOKEN=' "${environment_file}") -ne 1 || \
          $(grep -Ec '^STAGING_BASIC_AUTH_USER=' "${environment_file}") -ne 1 || \
          $(grep -Ec '^STAGING_BASIC_AUTH_HASH=' "${environment_file}") -ne 1 || \
          $(grep -Ec '^STAGING_BASIC_AUTH_PILOT_01_HASH=' "${environment_file}") -gt 1 ]]; then
        return 1
    fi

    proxy_token=$(sed -n 's/^CAREERTRELLIS_TRUSTED_PROXY_TOKEN=//p' "${environment_file}")
    owner_username=$(sed -n 's/^STAGING_BASIC_AUTH_USER=//p' "${environment_file}")
    owner_hash=$(sed -n 's/^STAGING_BASIC_AUTH_HASH=//p' "${environment_file}")
    if [[ ! ${proxy_token} =~ ^[A-Fa-f0-9]{64}$ || \
          ! ${owner_username} =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ || \
          ${owner_username} == "${pilot_username}" || \
          ${owner_hash} != '$argon2id$'* ]]; then
        return 1
    fi

    if [[ $(grep -Ec '^STAGING_BASIC_AUTH_PILOT_01_HASH=' "${environment_file}") -eq 0 ]]; then
        pilot_state=inactive
        [[ ${require_pilot} == false ]]
        return
    fi
    pilot_hash=$(sed -n 's/^STAGING_BASIC_AUTH_PILOT_01_HASH=//p' "${environment_file}")
    if [[ ${pilot_hash} != '$argon2id$'* || ${pilot_hash} == "${owner_hash}" ]]; then
        return 1
    fi
    pilot_state=active
}

print_receipt() {
    local result=$1
    printf 'operation=%s\n' "${operation}"
    printf 'result=%s\n' "${result}"
    printf 'environment_metadata_exact=true\n'
    printf 'key_counts_exact=true\n'
    printf 'owner_entry_valid=true\n'
    printf 'pilot_entry_unique=true\n'
    printf 'pilot_state=%s\n' "${pilot_state}"
}

if [[ ${operation} == inspect ]]; then
    if ! validate_environment false; then
        fail_validation
        exit 1
    fi
    print_receipt ok
    exit 0
fi

if [[ ${operation} == check ]]; then
    if ! validate_environment true; then
        fail_validation
        exit 1
    fi
    print_receipt ok
    exit 0
fi

if [[ ${operation} == install ]]; then
    release_dir=$(realpath "${script_dir}/../..")
    release_revision=$(basename "${release_dir}")
    if [[ $(dirname "${release_dir}") != "/opt/careertrellis/staging/releases" || \
          ! ${release_revision} =~ ^[0-9a-f]{40}$ || \
          ${script_path} != "${release_dir}/deploy/scripts/manage-pilot-caddy-credential.sh" || \
          -L ${script_path} || \
          $(stat -c '%U:%G %a' "${script_path}") != "root:careertrellis 750" || \
          ! -f ${release_dir}/deploy/caddy/Caddyfile.staging || \
          -L ${release_dir}/deploy/caddy/Caddyfile.staging || \
          $(stat -c '%U:%G %a' "${release_dir}/deploy/caddy/Caddyfile.staging") != "root:careertrellis 640" ]]; then
        echo "Installation requires one exact prepared release." >&2
        exit 1
    fi
    caddy_config=${release_dir}/deploy/caddy/Caddyfile.staging
    maintenance_lock_helper=${script_dir}/maintenance-lock.sh
    maintenance_lock_metadata="root:careertrellis 750"
else
    caddy_config=/etc/caddy/Caddyfile
    if [[ ${script_path} != "/usr/local/sbin/careertrellis-caddy-pilot-credential" || \
          -L ${script_path} || \
          $(stat -c '%U:%G %a' "${script_path}") != "root:root 755" || \
          ! -f ${caddy_config} || -L ${caddy_config} || \
          $(stat -c '%U:%G %a' "${caddy_config}") != "root:root 644" ]]; then
        echo "The installed Caddy configuration is unavailable." >&2
        exit 1
    fi
    maintenance_lock_helper=/usr/local/libexec/careertrellis/maintenance-lock.sh
    maintenance_lock_metadata="root:root 755"
fi
if [[ ! -f ${maintenance_lock_helper} || -L ${maintenance_lock_helper} || \
      $(stat -c '%U:%G %a' "${maintenance_lock_helper}") != "${maintenance_lock_metadata}" ]]; then
    echo "The reviewed maintenance-lock helper is unavailable." >&2
    exit 1
fi
if ! grep -Fxq $'\t\tpilot-01 {$STAGING_BASIC_AUTH_PILOT_01_HASH}' "${caddy_config}"; then
    echo "The installed Caddy configuration does not declare the pilot principal." >&2
    exit 1
fi
# shellcheck source=maintenance-lock.sh
source "${maintenance_lock_helper}"
acquire_careertrellis_maintenance_lock

if ! validate_environment false; then
    fail_validation
    exit 1
fi
if [[ ${operation} == install && ${pilot_state} == active ]]; then
    print_receipt no_change
    exit 0
fi
if [[ ${operation} == rotate && ${pilot_state} != active ]]; then
    fail_validation
    exit 1
fi
if [[ ! -t 0 || ! -t 1 ]]; then
    echo "Pilot Caddy credential changes require an interactive terminal." >&2
    exit 1
fi
if ! command -v caddy >/dev/null 2>&1; then
    echo "A required host command is unavailable." >&2
    exit 1
fi
if [[ ${operation} == rotate ]] && ! command -v systemctl >/dev/null 2>&1; then
    echo "A required host command is unavailable." >&2
    exit 1
fi

echo "Enter the distinct password stored for pilot-01 in the password manager." >&2
if ! pilot_hash=$(caddy hash-password --algorithm argon2id); then
    echo "Caddy password hashing failed." >&2
    exit 1
fi
if [[ ${pilot_hash} != '$argon2id$'* || ${pilot_hash} == *[[:space:]]* ]]; then
    unset pilot_hash
    echo "Caddy did not produce one valid Argon2id verifier." >&2
    exit 1
fi

umask 077
candidate_file=$(mktemp /etc/caddy/.careertrellis-staging.candidate.XXXXXX)
original_file=$(mktemp /etc/caddy/.careertrellis-staging.original.XXXXXX)
validation_log=$(mktemp /etc/caddy/.careertrellis-staging.validation.XXXXXX)
cleanup() {
    rm -f -- "${candidate_file:-}" "${original_file:-}" "${validation_log:-}"
    unset pilot_hash
}
trap cleanup EXIT
chmod 0600 "${candidate_file}" "${original_file}" "${validation_log}"
cp --preserve=mode,ownership -- "${environment_file}" "${original_file}"

pilot_entry_written=false
while IFS= read -r line || [[ -n ${line} ]]; do
    if [[ ${line} == "${pilot_key}="* ]]; then
        printf '%s=%s\n' "${pilot_key}" "${pilot_hash}" >>"${candidate_file}"
        pilot_entry_written=true
    else
        printf '%s\n' "${line}" >>"${candidate_file}"
    fi
done <"${environment_file}"
if [[ ${pilot_entry_written} == false ]]; then
    printf '%s=%s\n' "${pilot_key}" "${pilot_hash}" >>"${candidate_file}"
fi
unset pilot_hash

if ! caddy validate --config "${caddy_config}" --envfile "${candidate_file}" \
    >"${validation_log}" 2>&1; then
    echo "Candidate Caddy credential validation failed." >&2
    exit 1
fi

mv -Tf -- "${candidate_file}" "${environment_file}"
candidate_file=""
if [[ ${operation} == install ]]; then
    if ! validate_environment true; then
        mv -Tf -- "${original_file}" "${environment_file}"
        original_file=""
        echo "Pilot Caddy credential readback failed." >&2
        exit 1
    fi
    rm -f -- "${original_file}"
    original_file=""
    print_receipt staged
    exit 0
fi
if ! systemctl reload caddy >"${validation_log}" 2>&1; then
    rm -f -- "${original_file}"
    original_file=""
    if systemctl stop caddy >>"${validation_log}" 2>&1; then
        echo "Caddy reload result is ambiguous; Caddy was stopped and the candidate environment was retained. Do not retry." >&2
    else
        echo "Caddy reload and fail-closed stop results are ambiguous; the candidate environment was retained. Do not retry." >&2
    fi
    exit 1
fi
rm -f -- "${original_file}"
original_file=""

if ! validate_environment true; then
    echo "Pilot Caddy credential readback failed." >&2
    exit 1
fi
print_receipt ok
