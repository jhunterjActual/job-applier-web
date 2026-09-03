#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the rollback script as root." >&2
    exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
maintenance_lock_helper=${script_dir}/maintenance-lock.sh
if [[ ! -f ${maintenance_lock_helper} ]]; then
    maintenance_lock_helper=/usr/local/libexec/careertrellis/maintenance-lock.sh
fi
if [[ ! -f ${maintenance_lock_helper} || -L ${maintenance_lock_helper} ]]; then
    echo "The reviewed maintenance-lock helper is unavailable." >&2
    exit 1
fi
# shellcheck source=maintenance-lock.sh
source "${maintenance_lock_helper}"
acquire_careertrellis_maintenance_lock

release_root=${CAREERTRELLIS_RELEASE_ROOT:-/opt/careertrellis/staging}
browser_path=/var/lib/careertrellis/shared/ms-playwright
current_link=${release_root}/current
previous_link=${release_root}/previous
pilot_acceptance_parent=/var/backups/careertrellis
pilot_acceptance_root=${pilot_acceptance_parent}/pilot-auth-acceptance
pilot_acceptance_state=/var/lib/careertrellis/pilot-auth-acceptance/active.json
backup_health_receipt_root=${pilot_acceptance_parent}/backup-health/operations
object_backup_health_state_root=/var/lib/careertrellis/backup-health/objects
object_backup_health_lock=/run/lock/careertrellis-backup-health-objects.lock
requested_revision=${1:-}

ensure_pilot_acceptance_root() {
    if [[ -e ${pilot_acceptance_parent} || -L ${pilot_acceptance_parent} ]]; then
        if [[ ! -d ${pilot_acceptance_parent} || -L ${pilot_acceptance_parent} || \
              $(realpath "${pilot_acceptance_parent}") != "${pilot_acceptance_parent}" || \
              $(stat -c '%U:%G %a' "${pilot_acceptance_parent}") != "root:root 700" ]]; then
            echo "The protected CareerTrellis receipt parent is invalid." >&2
            return 1
        fi
    else
        install -d -o root -g root -m 0700 "${pilot_acceptance_parent}" || return 1
    fi
    if [[ -e ${pilot_acceptance_root} || -L ${pilot_acceptance_root} ]]; then
        if [[ ! -d ${pilot_acceptance_root} || -L ${pilot_acceptance_root} || \
              $(realpath "${pilot_acceptance_root}") != "${pilot_acceptance_root}" || \
              $(stat -c '%U:%G %a' "${pilot_acceptance_root}") != "root:root 700" ]]; then
            echo "The pilot acceptance receipt root is invalid." >&2
            return 1
        fi
    else
        install -d -o root -g root -m 0700 "${pilot_acceptance_root}" || return 1
    fi
}

ensure_root_only_directory() {
    local path=$1
    if [[ -e ${path} || -L ${path} ]]; then
        if [[ ! -d ${path} || -L ${path} || \
              $(realpath "${path}") != "${path}" || \
              $(stat -c '%U:%G %a' "${path}") != "root:root 700" ]]; then
            echo "A protected backup-health directory is invalid." >&2
            return 1
        fi
    else
        install -d -o root -g root -m 0700 "${path}" || return 1
    fi
}

ensure_object_backup_health_roots() {
    local protected_directory
    for protected_directory in \
        "${pilot_acceptance_parent}" \
        "${pilot_acceptance_parent}/backup-health" \
        "${backup_health_receipt_root}" \
        /var/lib/careertrellis/backup-health \
        "${object_backup_health_state_root}" \
        "${object_backup_health_state_root}/alerts" \
        "${object_backup_health_state_root}/unit-alerts"; do
        ensure_root_only_directory "${protected_directory}" || return 1
    done
    if [[ -e ${object_backup_health_lock} || -L ${object_backup_health_lock} ]]; then
        if [[ ! -f ${object_backup_health_lock} || -L ${object_backup_health_lock} || \
              $(stat -c '%U:%G %a' "${object_backup_health_lock}") != "root:root 600" ]]; then
            echo "The generated-object backup-health lock is invalid." >&2
            return 1
        fi
    else
        install -o root -g root -m 0600 /dev/null "${object_backup_health_lock}" || return 1
    fi
}

ensure_caddy_access_log() {
    local log_directory=/var/log/caddy
    local log_path=${log_directory}/careertrellis-staging-access-v6.log
    if [[ -e ${log_directory} || -L ${log_directory} ]]; then
        if [[ ! -d ${log_directory} || -L ${log_directory} || \
              $(realpath "${log_directory}") != "${log_directory}" || \
              $(stat -c '%U:%G %a' "${log_directory}") != "caddy:caddy 750" ]]; then
            echo "The Caddy access-log directory is invalid." >&2
            return 1
        fi
    else
        install -d -o caddy -g caddy -m 0750 "${log_directory}" || return 1
    fi
    if [[ -e ${log_path} || -L ${log_path} ]]; then
        if [[ -f ${log_path} && ! -L ${log_path} && \
              $(stat -c '%U:%G %a %s' "${log_path}") == "root:root 600 0" ]]; then
            chown --no-dereference --from=root:root caddy:caddy "${log_path}" || return 1
        fi
        if [[ ! -f ${log_path} || -L ${log_path} || \
              $(stat -c '%U:%G %a' "${log_path}") != "caddy:caddy 600" ]]; then
            echo "The Caddy access log is invalid." >&2
            return 1
        fi
    else
        install -o caddy -g caddy -m 0600 /dev/null "${log_path}" || return 1
    fi
}

assert_caddy_runtime_privacy_ready() {
    local target_release=$1
    if [[ ! -x ${target_release}/backend/venv/bin/python || \
          ! -f ${target_release}/backend/caddy_log_privacy.py || \
          -L ${target_release}/backend/caddy_log_privacy.py || \
          ! -f ${target_release}/deploy/caddy/Caddyfile.staging || \
          -L ${target_release}/deploy/caddy/Caddyfile.staging ]]; then
        echo "The Caddy runtime privacy validator is unavailable." >&2
        return 1
    fi
    "${target_release}/backend/venv/bin/python" -B \
        "${target_release}/backend/caddy_log_privacy.py" \
        --config "${target_release}/deploy/caddy/Caddyfile.staging" \
        --validate-only >/dev/null || return 1
    "${current_release}/backend/venv/bin/python" -B \
        "${current_release}/backend/caddy_log_privacy.py" \
        --config "${target_release}/deploy/caddy/Caddyfile.staging" \
        --validate-privacy-floor-only >/dev/null
}

assert_no_active_pilot_acceptance() {
    if [[ -e ${pilot_acceptance_state} || -L ${pilot_acceptance_state} ]]; then
        echo "An active pilot acceptance attempt prevents rollback." >&2
        return 1
    fi
}

if [[ ! -L ${current_link} ]]; then
    echo "No current release is installed." >&2
    exit 1
fi
current_release=$(readlink -f "${current_link}")

if [[ -n ${requested_revision} ]]; then
    if [[ ! ${requested_revision} =~ ^[0-9a-f]{40}$ ]]; then
        echo "Optional revision must be an exact 40-character Git commit." >&2
        exit 1
    fi
    target_release=${release_root}/releases/${requested_revision}
else
    if [[ ! -L ${previous_link} ]]; then
        echo "No previous release is recorded." >&2
        exit 1
    fi
    target_release=$(readlink -f "${previous_link}")
fi

case "${target_release}" in
    "${release_root}"/releases/*) ;;
    *) echo "Rollback target escaped the release root." >&2; exit 1 ;;
esac
if [[ ! -d ${target_release} || ${target_release} == "${current_release}" ]]; then
    echo "Rollback target is missing or already current: ${target_release}" >&2
    exit 1
fi

target_revision=$(basename "${target_release}")

if [[ ! -x /usr/local/sbin/careertrellis-authority ]]; then
    echo "Hosted authority state is unavailable." >&2
    exit 1
fi
/usr/local/sbin/careertrellis-authority assert-code-rollback-allowed
/usr/local/sbin/careertrellis-authority assert-release-startable "${target_release}"

assert_erasure_worker_activation_ready() {
    local configured_enabled=$1

    if [[ ${configured_enabled} != true ]]; then
        return 0
    fi
    if ! systemctl is-active --quiet careertrellis-erasure-worker.service || \
       ! systemctl is-enabled --quiet careertrellis-erasure-worker.service; then
        echo "The account-erasure worker must already be active and enabled before its producer switch is set to true." >&2
        return 1
    fi
}

activate_release() {
    local release=$1
    local revision activation_link environment_file erasure_batch_size erasure_data_store_count erasure_poll_seconds erasure_worker_active=false object_backup_health_available=false object_health_active_state object_health_file object_health_target pilot_acceptance_helper_available=false pilot_user_helper_available=false recovery_helper recovery_helpers_available=false
    local erasure_worker_enabled search_worker_enabled web_erasure_worker_enabled web_search_worker_enabled
    revision=$(basename "${release}")

    assert_caddy_runtime_privacy_ready "${release}" || return 1

    /usr/local/sbin/careertrellis-authority assert-release-startable "${release}" || return 1

    if systemctl is-active --quiet careertrellis-erasure-worker.service; then
        erasure_worker_active=true
        if [[ ! -f ${release}/backend/account_erasure_worker.py || ! -f ${release}/deploy/systemd/careertrellis-erasure-worker.service ]]; then
            echo "An active account-erasure worker prevents rollback to an incompatible release." >&2
            return 1
        fi
    fi
    # Quiesce the scheduler before checking the service so rollback cannot
    # expose a timer-started evaluator to a mixed or disappearing bundle.
    if systemctl is-active --quiet careertrellis-generated-object-backup-health.timer; then
        systemctl stop careertrellis-generated-object-backup-health.timer || return 1
    fi
    object_health_active_state=$(systemctl show \
        careertrellis-generated-object-backup-health.service \
        --property=ActiveState --value 2>/dev/null || true)
    if [[ -n ${object_health_active_state} && \
          ${object_health_active_state} != inactive && \
          ${object_health_active_state} != failed ]]; then
        echo "An active generated-object health evaluation prevents rollback." >&2
        return 1
    fi
    for object_health_target in \
        /usr/local/libexec/careertrellis/backup_health.py \
        /usr/local/sbin/careertrellis-backup-health \
        /etc/systemd/system/careertrellis-generated-object-backup-health.service \
        /etc/systemd/system/careertrellis-generated-object-backup-health.timer \
        /etc/systemd/system/careertrellis-generated-object-backup-health-alert@.service; do
        if [[ -L ${object_health_target} ]]; then
            echo "An installed generated-object backup-health target must not be a symbolic link." >&2
            return 1
        fi
    done

    for environment_file in \
        /etc/careertrellis/staging.env \
        /etc/careertrellis/search-worker.env \
        /etc/careertrellis/account-erasure-worker.env \
        /etc/careertrellis/postgres-runtime.env \
        /etc/careertrellis/postgres-worker.env \
        /etc/careertrellis/postgres-erasure-worker.env \
        /etc/careertrellis/provider-secrets.env \
        /etc/careertrellis/object-storage.env \
        /etc/careertrellis/account-erasure-object-storage.env \
        /etc/caddy/careertrellis-staging.env; do
        if [[ ! -f ${environment_file} || -L ${environment_file} || $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
            echo "Every service runtime environment file must be root:root mode 0600." >&2
            return 1
        fi
    done
    if [[ -e ${release}/deploy/scripts/mirror-generated-objects-onedrive.sh || \
          -L ${release}/deploy/scripts/mirror-generated-objects-onedrive.sh || \
          -e ${release}/deploy/scripts/test-generated-object-restore.sh || \
          -L ${release}/deploy/scripts/test-generated-object-restore.sh ]]; then
        for recovery_helper in \
            "${release}/deploy/scripts/mirror-generated-objects-onedrive.sh" \
            "${release}/deploy/scripts/test-generated-object-restore.sh"; do
            if [[ ! -f ${recovery_helper} || -L ${recovery_helper} || ! -x ${recovery_helper} ]]; then
                echo "Generated-object recovery helpers must be a complete reviewed executable pair." >&2
                return 1
            fi
        done
        recovery_helpers_available=true
    fi
    if [[ -e ${release}/backend/backup_health.py || \
          -L ${release}/backend/backup_health.py || \
          -e ${release}/deploy/scripts/manage-generated-object-backup-health.sh || \
          -L ${release}/deploy/scripts/manage-generated-object-backup-health.sh || \
          -e ${release}/deploy/systemd/careertrellis-generated-object-backup-health.service || \
          -L ${release}/deploy/systemd/careertrellis-generated-object-backup-health.service || \
          -e ${release}/deploy/systemd/careertrellis-generated-object-backup-health.timer || \
          -L ${release}/deploy/systemd/careertrellis-generated-object-backup-health.timer || \
          -e ${release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service || \
          -L ${release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service ]]; then
        for object_health_file in \
            "${release}/backend/backup_health.py" \
            "${release}/deploy/scripts/manage-generated-object-backup-health.sh" \
            "${release}/deploy/systemd/careertrellis-generated-object-backup-health.service" \
            "${release}/deploy/systemd/careertrellis-generated-object-backup-health.timer" \
            "${release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service"; do
            if [[ ! -f ${object_health_file} || -L ${object_health_file} ]]; then
                echo "Generated-object backup health must be a complete reviewed bundle." >&2
                return 1
            fi
        done
        if [[ $(stat -c '%U:%G %a' "${release}/backend/backup_health.py") != "root:careertrellis 640" || \
              $(stat -c '%U:%G %a' "${release}/deploy/scripts/manage-generated-object-backup-health.sh") != "root:careertrellis 750" || \
              $(stat -c '%U:%G %a' "${release}/deploy/systemd/careertrellis-generated-object-backup-health.service") != "root:careertrellis 640" || \
              $(stat -c '%U:%G %a' "${release}/deploy/systemd/careertrellis-generated-object-backup-health.timer") != "root:careertrellis 640" || \
              $(stat -c '%U:%G %a' "${release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service") != "root:careertrellis 640" ]]; then
            echo "The generated-object backup-health bundle has an unsafe owner or mode." >&2
            return 1
        fi
        object_backup_health_available=true
        ensure_object_backup_health_roots || return 1
    fi
    if [[ -e ${release}/deploy/scripts/manage-pilot-user.sh || \
          -L ${release}/deploy/scripts/manage-pilot-user.sh || \
          -e ${release}/backend/pilot_users.py || \
          -L ${release}/backend/pilot_users.py ]]; then
        if [[ ! -f ${release}/deploy/scripts/manage-pilot-user.sh || \
              -L ${release}/deploy/scripts/manage-pilot-user.sh || \
              $(stat -c '%U:%G %a' "${release}/deploy/scripts/manage-pilot-user.sh") != "root:careertrellis 750" || \
              ! -f ${release}/backend/pilot_users.py || \
              -L ${release}/backend/pilot_users.py || \
              $(stat -c '%U:%G %a' "${release}/backend/pilot_users.py") != "root:careertrellis 640" ]]; then
            echo "The pilot user operator boundary must be a complete reviewed pair." >&2
            return 1
        fi
        pilot_user_helper_available=true
    fi
    if [[ -e ${release}/deploy/scripts/manage-pilot-acceptance.sh || \
          -L ${release}/deploy/scripts/manage-pilot-acceptance.sh || \
          -e ${release}/backend/pilot_acceptance.py || \
          -L ${release}/backend/pilot_acceptance.py || \
          -e ${release}/backend/pilot_acceptance_receipts.py || \
          -L ${release}/backend/pilot_acceptance_receipts.py ]]; then
        if [[ ! -f ${release}/deploy/scripts/manage-pilot-acceptance.sh || \
              -L ${release}/deploy/scripts/manage-pilot-acceptance.sh || \
              $(stat -c '%U:%G %a' "${release}/deploy/scripts/manage-pilot-acceptance.sh") != "root:careertrellis 750" || \
              ! -f ${release}/backend/pilot_acceptance.py || \
              -L ${release}/backend/pilot_acceptance.py || \
              $(stat -c '%U:%G %a' "${release}/backend/pilot_acceptance.py") != "root:careertrellis 640" || \
              ! -f ${release}/backend/pilot_acceptance_receipts.py || \
              -L ${release}/backend/pilot_acceptance_receipts.py || \
              $(stat -c '%U:%G %a' "${release}/backend/pilot_acceptance_receipts.py") != "root:careertrellis 640" ]]; then
            echo "The pilot acceptance boundary must be a complete reviewed trio." >&2
            return 1
        fi
        pilot_acceptance_helper_available=true
        ensure_pilot_acceptance_root || return 1
    fi
    if grep -Ev '^(#|[[:space:]]*$|CAREERTRELLIS_(ENVIRONMENT|DATA_DIR|DATA_STORE|ACCOUNT_ERASURE_WORKER_(ENABLED|POLL_SECONDS|BATCH_SIZE))=)' \
        /etc/careertrellis/account-erasure-worker.env >/dev/null || \
       grep -Ev '^(#|[[:space:]]*$|CAREERTRELLIS_DATABASE_URL=)' \
        /etc/careertrellis/postgres-erasure-worker.env >/dev/null || \
       grep -Ev '^(#|[[:space:]]*$|CAREERTRELLIS_OBJECT_STORE(_ENDPOINT|_BUCKET|_ACCESS_KEY_ID|_SECRET_ACCESS_KEY)?=)' \
        /etc/careertrellis/account-erasure-object-storage.env >/dev/null; then
        echo "The account-erasure worker environment contains a forbidden credential." >&2
        return 1
    fi
    erasure_poll_seconds=$(sed -n 's/^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_POLL_SECONDS=//p' /etc/careertrellis/account-erasure-worker.env)
    erasure_batch_size=$(sed -n 's/^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_BATCH_SIZE=//p' /etc/careertrellis/account-erasure-worker.env)
    erasure_worker_enabled=$(sed -n 's/^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=//p' /etc/careertrellis/account-erasure-worker.env)
    search_worker_enabled=$(sed -n 's/^CAREERTRELLIS_SEARCH_WORKER_ENABLED=//p' /etc/careertrellis/search-worker.env)
    web_erasure_worker_enabled=$(sed -n 's/^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=//p' /etc/careertrellis/staging.env)
    web_search_worker_enabled=$(sed -n 's/^CAREERTRELLIS_SEARCH_WORKER_ENABLED=//p' /etc/careertrellis/staging.env)
    erasure_data_store_count=$(grep -Ec '^CAREERTRELLIS_DATA_STORE=' /etc/careertrellis/account-erasure-worker.env)
    if [[ $(grep -Ec '^CAREERTRELLIS_ENVIRONMENT=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Fxc "CAREERTRELLIS_ENVIRONMENT=staging" /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_DATA_DIR=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Fxc "CAREERTRELLIS_DATA_DIR=/var/lib/careertrellis/account-erasure" /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ ${erasure_data_store_count} -gt 1 ]] || \
       [[ ${erasure_data_store_count} -eq 1 && $(grep -Fxc "CAREERTRELLIS_DATA_STORE=postgres" /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=(true|false)$' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_SEARCH_WORKER_ENABLED=' /etc/careertrellis/search-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_SEARCH_WORKER_ENABLED=(true|false)$' /etc/careertrellis/search-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_SEARCH_WORKER_ENABLED=' /etc/careertrellis/staging.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_SEARCH_WORKER_ENABLED=(true|false)$' /etc/careertrellis/staging.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=' /etc/careertrellis/staging.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=(true|false)$' /etc/careertrellis/staging.env) -ne 1 ]] || \
       [[ ${web_search_worker_enabled} != "${search_worker_enabled}" ]] || \
       [[ ${web_erasure_worker_enabled} != "${erasure_worker_enabled}" ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_POLL_SECONDS=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ ! ${erasure_poll_seconds} =~ ^(0|[1-9][0-9]{0,2})$ || ${erasure_poll_seconds} -lt 5 || ${erasure_poll_seconds} -gt 300 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_BATCH_SIZE=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ ! ${erasure_batch_size} =~ ^(0|[1-9][0-9]{0,2})$ || ${erasure_batch_size} -lt 1 || ${erasure_batch_size} -gt 500 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_DATABASE_URL=' /etc/careertrellis/postgres-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_DATABASE_URL=postgresql\+psycopg://careertrellis_erasure_worker:' /etc/careertrellis/postgres-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_OBJECT_STORE=' /etc/careertrellis/account-erasure-object-storage.env) -ne 1 ]] || \
       [[ $(grep -Fxc "CAREERTRELLIS_OBJECT_STORE=r2_encrypted" /etc/careertrellis/account-erasure-object-storage.env) -ne 1 ]]; then
        echo "The account-erasure worker must retain its reviewed exact enablement flag, dedicated database, and object-store selections." >&2
        return 1
    fi
    assert_erasure_worker_activation_ready "${erasure_worker_enabled}" || return 1
    for environment_file in \
        /etc/careertrellis/staging.env \
        /etc/careertrellis/search-worker.env; do
        if ! grep -Fx "PLAYWRIGHT_BROWSERS_PATH=${browser_path}" "${environment_file}" >/dev/null; then
            echo "Web and worker runtime files must use the reviewed shared browser path." >&2
            return 1
        fi
    done

    if grep -Fxq $'\t\tpilot-01 {$STAGING_BASIC_AUTH_PILOT_01_HASH}' "${release}/deploy/caddy/Caddyfile.staging"; then
        if [[ ! -x ${release}/deploy/scripts/manage-pilot-caddy-credential.sh || \
              -L ${release}/deploy/scripts/manage-pilot-caddy-credential.sh ]]; then
            echo "The reviewed pilot Caddy credential helper is unavailable." >&2
            return 1
        fi
        "${release}/deploy/scripts/manage-pilot-caddy-credential.sh" check >/dev/null || return 1
    fi

    assert_no_active_pilot_acceptance || return 1
    ensure_caddy_access_log || return 1
    caddy validate \
        --config "${release}/deploy/caddy/Caddyfile.staging" \
        --envfile /etc/caddy/careertrellis-staging.env || return 1
    activation_link=${release_root}/.rollback-${revision}-$$
    rm -f -- "${activation_link}"
    ln -s "${release}" "${activation_link}" || return 1
    mv -Tf "${activation_link}" "${current_link}" || return 1
    install -o root -g root -m 0644 "${release}/deploy/systemd/careertrellis-web.service" /etc/systemd/system/careertrellis-web.service || return 1
    install -o root -g root -m 0644 "${release}/deploy/systemd/careertrellis-worker.service" /etc/systemd/system/careertrellis-worker.service || return 1
    if [[ -f ${release}/deploy/systemd/careertrellis-erasure-worker.service ]]; then
        install -o root -g root -m 0644 "${release}/deploy/systemd/careertrellis-erasure-worker.service" /etc/systemd/system/careertrellis-erasure-worker.service || return 1
    fi
    install -d -o root -g root -m 0755 /etc/systemd/system/caddy.service.d || return 1
    install -o root -g root -m 0644 "${release}/deploy/systemd/caddy.service.d/careertrellis-staging.conf" /etc/systemd/system/caddy.service.d/careertrellis-staging.conf || return 1
    install -o root -g root -m 0644 "${release}/deploy/caddy/Caddyfile.staging" /etc/caddy/Caddyfile || return 1
    if [[ -f ${release}/deploy/scripts/manage-pilot-caddy-credential.sh && ! -L ${release}/deploy/scripts/manage-pilot-caddy-credential.sh ]]; then
        install -o root -g root -m 0755 "${release}/deploy/scripts/manage-pilot-caddy-credential.sh" /usr/local/sbin/careertrellis-caddy-pilot-credential || return 1
    fi
    if [[ ${pilot_user_helper_available} == true ]]; then
        install -o root -g root -m 0750 "${release}/deploy/scripts/manage-pilot-user.sh" /usr/local/sbin/careertrellis-pilot-user || return 1
        install -o root -g root -m 0640 "${release}/backend/pilot_users.py" /usr/local/libexec/careertrellis/pilot_users.py || return 1
    else
        rm -f -- /usr/local/sbin/careertrellis-pilot-user /usr/local/libexec/careertrellis/pilot_users.py || return 1
    fi
    if [[ ${pilot_acceptance_helper_available} == true ]]; then
        install -o root -g root -m 0750 "${release}/deploy/scripts/manage-pilot-acceptance.sh" /usr/local/sbin/careertrellis-pilot-acceptance || return 1
        install -o root -g root -m 0640 "${release}/backend/pilot_acceptance.py" /usr/local/libexec/careertrellis/pilot_acceptance.py || return 1
        install -o root -g root -m 0640 "${release}/backend/pilot_acceptance_receipts.py" /usr/local/libexec/careertrellis/pilot_acceptance_receipts.py || return 1
    else
        rm -f -- /usr/local/sbin/careertrellis-pilot-acceptance /usr/local/libexec/careertrellis/pilot_acceptance.py /usr/local/libexec/careertrellis/pilot_acceptance_receipts.py || return 1
    fi
    if [[ ${recovery_helpers_available} == true ]]; then
        install -o root -g root -m 0755 "${release}/deploy/scripts/mirror-generated-objects-onedrive.sh" /usr/local/sbin/careertrellis-mirror-generated-objects || return 1
        install -o root -g root -m 0755 "${release}/deploy/scripts/test-generated-object-restore.sh" /usr/local/sbin/careertrellis-test-generated-object-restore || return 1
    fi
    if [[ ${object_backup_health_available} == true ]]; then
        install -o root -g root -m 0644 "${release}/backend/backup_health.py" /usr/local/libexec/careertrellis/backup_health.py || return 1
        install -o root -g root -m 0755 "${release}/deploy/scripts/manage-generated-object-backup-health.sh" /usr/local/sbin/careertrellis-backup-health || return 1
        systemd-analyze verify \
            "${release}/deploy/systemd/careertrellis-generated-object-backup-health.service" \
            "${release}/deploy/systemd/careertrellis-generated-object-backup-health.timer" \
            "${release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service" || return 1
        install -o root -g root -m 0644 "${release}/deploy/systemd/careertrellis-generated-object-backup-health.service" /etc/systemd/system/careertrellis-generated-object-backup-health.service || return 1
        install -o root -g root -m 0644 "${release}/deploy/systemd/careertrellis-generated-object-backup-health.timer" /etc/systemd/system/careertrellis-generated-object-backup-health.timer || return 1
        install -o root -g root -m 0644 "${release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service" /etc/systemd/system/careertrellis-generated-object-backup-health-alert@.service || return 1
    else
        if [[ -f /etc/systemd/system/careertrellis-generated-object-backup-health.timer ]]; then
            systemctl disable --now careertrellis-generated-object-backup-health.timer >/dev/null || return 1
        fi
        rm -f -- \
            /usr/local/libexec/careertrellis/backup_health.py \
            /usr/local/sbin/careertrellis-backup-health \
            /etc/systemd/system/careertrellis-generated-object-backup-health.service \
            /etc/systemd/system/careertrellis-generated-object-backup-health.timer \
            /etc/systemd/system/careertrellis-generated-object-backup-health-alert@.service || return 1
    fi
    systemctl daemon-reload || return 1
    if [[ ${object_backup_health_available} == true ]]; then
        systemctl enable careertrellis-generated-object-backup-health.timer >/dev/null || return 1
        systemctl start careertrellis-generated-object-backup-health.timer || return 1
    fi
    # Stop the old producer before changing either consumer. The running web
    # process retains its startup flags and must not enqueue work after a
    # newly-disabled worker has been stopped.
    systemctl stop careertrellis-web.service || return 1
    if [[ ${search_worker_enabled} == false ]]; then
        systemctl disable careertrellis-worker.service >/dev/null || return 1
        systemctl stop careertrellis-worker.service || return 1
    fi
    if [[ ${erasure_worker_enabled} == false ]]; then
        systemctl disable careertrellis-erasure-worker.service >/dev/null || return 1
        systemctl stop careertrellis-erasure-worker.service || return 1
    fi
    systemctl reload caddy || return 1
    systemctl restart careertrellis-web.service || return 1
    if [[ ${search_worker_enabled} == true ]]; then
        systemctl restart careertrellis-worker.service || return 1
    fi
    if [[ ${erasure_worker_enabled} == true ]]; then
        systemctl restart careertrellis-erasure-worker.service || return 1
    fi
    for _attempt in $(seq 1 30); do
        if curl --fail --silent --show-error --max-time 2 http://127.0.0.1:8001/health/ready >/dev/null; then
            if [[ ${recovery_helpers_available} == false ]]; then
                rm -f -- /usr/local/sbin/careertrellis-mirror-generated-objects /usr/local/sbin/careertrellis-test-generated-object-restore || return 1
            fi
            systemctl enable careertrellis-web.service >/dev/null || return 1
            if [[ ${search_worker_enabled} == true ]]; then
                systemctl enable careertrellis-worker.service >/dev/null || return 1
            fi
            return 0
        fi
        sleep 1
    done
    return 1
}

if activate_release "${target_release}"; then
    return_link=${release_root}/.return-${target_revision}-$$
    ln -s "${current_release}" "${return_link}"
    mv -Tf "${return_link}" "${previous_link}"
    echo "CareerTrellis staging rolled back to ${target_revision}."
    exit 0
fi

echo "Rollback target did not become ready; returning to the original release." >&2
if ! activate_release "${current_release}"; then
    echo "Critical: the original release also failed readiness after restoration." >&2
fi
exit 1
