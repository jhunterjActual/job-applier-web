#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the deployment script as root." >&2
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

prepare_only=false
if [[ ${1:-} == "--prepare-only" ]]; then
    prepare_only=true
    shift
fi
revision=${1:-}
if [[ ! ${revision} =~ ^[0-9a-f]{40}$ ]]; then
    echo "Usage: $0 [--prepare-only] <exact-40-character-git-commit>" >&2
    exit 1
fi

source_dir=${CAREERTRELLIS_SOURCE_DIR:-/srv/careertrellis/job-applier-web}
release_root=${CAREERTRELLIS_RELEASE_ROOT:-/opt/careertrellis/staging}
releases_dir=${release_root}/releases
release_dir=${releases_dir}/${revision}
current_link=${release_root}/current
previous_link=${release_root}/previous
browser_path=/var/lib/careertrellis/shared/ms-playwright
pilot_acceptance_parent=/var/backups/careertrellis
pilot_acceptance_root=${pilot_acceptance_parent}/pilot-auth-acceptance
pilot_acceptance_state=/var/lib/careertrellis/pilot-auth-acceptance/active.json
backup_health_receipt_root=${pilot_acceptance_parent}/backup-health/operations
object_backup_health_state_root=/var/lib/careertrellis/backup-health/objects
object_backup_health_lock=/run/lock/careertrellis-backup-health-objects.lock
temporary_release=""
test_data=""

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
    local log_path=${log_directory}/careertrellis-staging-access-v7.log
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
    "${release_dir}/backend/venv/bin/python" -B \
        "${release_dir}/backend/caddy_log_privacy.py" \
        --config "${target_release}/deploy/caddy/Caddyfile.staging" \
        --validate-privacy-floor-only >/dev/null
}

assert_no_active_pilot_acceptance() {
    if [[ -e ${pilot_acceptance_state} || -L ${pilot_acceptance_state} ]]; then
        echo "An active pilot acceptance attempt prevents release activation." >&2
        return 1
    fi
}

cleanup() {
    [[ -z ${temporary_release} || ! -d ${temporary_release} ]] || rm -rf -- "${temporary_release}"
    [[ -z ${test_data} || ! -d ${test_data} ]] || rm -rf -- "${test_data}"
}
trap cleanup EXIT

if [[ ! -d ${source_dir}/.git ]]; then
    echo "Deployment source is not a Git checkout: ${source_dir}" >&2
    exit 1
fi
if ! git -C "${source_dir}" cat-file -e "${revision}^{commit}"; then
    echo "Commit is not present in the deployment source: ${revision}" >&2
    exit 1
fi

install -d -o root -g root -m 0755 "${releases_dir}"
install -d -o root -g careertrellis -m 0750 "${browser_path}"

if [[ ! -d ${release_dir} ]]; then
    temporary_release=$(mktemp -d "${releases_dir}/.deploy-${revision}-XXXXXX")
    git -C "${source_dir}" archive "${revision}" | tar -x -C "${temporary_release}"
    python3 -m venv "${temporary_release}/backend/venv"
    "${temporary_release}/backend/venv/bin/python" -m pip install \
        --disable-pip-version-check \
        --require-hashes \
        --requirement "${temporary_release}/backend/requirements.txt"
    "${temporary_release}/backend/venv/bin/python" -m playwright install-deps chromium
    env PLAYWRIGHT_BROWSERS_PATH="${browser_path}" \
        "${temporary_release}/backend/venv/bin/python" -m playwright install chromium
    chown -R root:careertrellis "${browser_path}"
    chmod -R u=rwX,g=rX,o= "${browser_path}"

    test_data=$(mktemp -d /tmp/careertrellis-deploy-test-XXXXXX)
    (
        cd "${temporary_release}/backend"
        PLAYWRIGHT_BROWSERS_PATH="${browser_path}" \
        CAREERTRELLIS_ENVIRONMENT=local \
        CAREERTRELLIS_DATA_STORE=sqlite \
        CAREERTRELLIS_ALLOWED_HOSTS= \
        CAREERTRELLIS_TRUSTED_PROXY_TOKEN= \
        CAREERTRELLIS_DATA_DIR="${test_data}" \
        PYTHONDONTWRITEBYTECODE=1 \
        ./venv/bin/python -m unittest test_p0_regressions.py test_account_choice.py test_backup_health.py test_pilot_acceptance.py test_pilot_acceptance_receipts.py test_search_runs.py test_scheduled_search_dispatch.py test_account_erasure.py test_object_store.py test_object_import.py test_import_reconciliation.py test_stored_objects.py test_cutover_rehearsal.py test_final_cutover_coordinator.py test_generated_object_recovery.py test_generated_object_capture.py test_generated_object_phases.py test_analytics.py test_observability.py
        CAREERTRELLIS_ENVIRONMENT=staging \
        CAREERTRELLIS_DATA_DIR="${test_data}/account-erasure" \
        CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=false \
        ./venv/bin/python account_erasure_worker.py --once
        ./venv/bin/python -m compileall -q .
    )
    chmod 0755 \
        "${temporary_release}/deploy/scripts/maintenance-lock.sh" \
        "${temporary_release}/deploy/scripts/apply-postgres-runtime-grants.sh" \
        "${temporary_release}/deploy/scripts/check-caddy-access-log-privacy.sh" \
        "${temporary_release}/deploy/scripts/deploy-release.sh" \
        "${temporary_release}/deploy/scripts/diagnose-account-erasure-object-store.sh" \
        "${temporary_release}/deploy/scripts/finalize-postgres-r2-cutover.sh" \
        "${temporary_release}/deploy/scripts/hosted-authority.sh" \
        "${temporary_release}/deploy/scripts/bootstrap-erasure-worker-role.sh" \
        "${temporary_release}/deploy/scripts/bootstrap-admission-roles.sh" \
        "${temporary_release}/deploy/scripts/manage-admission-operations.sh" \
        "${temporary_release}/deploy/scripts/check-worker-admission-environment.sh" \
        "${temporary_release}/deploy/scripts/import-sqlite-postgres.sh" \
        "${temporary_release}/deploy/scripts/install-erasure-worker-credential.sh" \
        "${temporary_release}/deploy/scripts/manage-generated-object-backup-health.sh" \
        "${temporary_release}/deploy/scripts/mirror-generated-objects-onedrive.sh" \
        "${temporary_release}/deploy/scripts/migrate-postgres.sh" \
        "${temporary_release}/deploy/scripts/preflight-sqlite-postgres-import.sh" \
        "${temporary_release}/deploy/scripts/rehearse-postgres-r2-cutover.sh" \
        "${temporary_release}/deploy/scripts/reconcile-sqlite-postgres-import.sh" \
        "${temporary_release}/deploy/scripts/test-generated-object-restore.sh"
    bash -n \
        "${temporary_release}/deploy/scripts/check-caddy-access-log-privacy.sh" \
        "${temporary_release}/deploy/scripts/diagnose-account-erasure-object-store.sh" \
        "${temporary_release}/deploy/scripts/manage-generated-object-backup-health.sh" \
        "${temporary_release}/deploy/scripts/mirror-generated-objects-onedrive.sh" \
        "${temporary_release}/deploy/scripts/test-generated-object-restore.sh"
    # Keep staged executable validation on the same already-proven executable
    # filesystem as the release. The test-data root may be mounted noexec.
    object_health_verification_root=${temporary_release}/.object-health-unit-root
    install -d -o root -g root -m 0755 \
        "${object_health_verification_root}/etc" \
        "${object_health_verification_root}/etc/systemd" \
        "${object_health_verification_root}/etc/systemd/system" \
        "${object_health_verification_root}/usr" \
        "${object_health_verification_root}/usr/local" \
        "${object_health_verification_root}/usr/local/libexec" \
        "${object_health_verification_root}/usr/local/libexec/careertrellis" \
        "${object_health_verification_root}/usr/local/sbin"
    printf 'root:x:0:0:root:/root:/usr/sbin/nologin\n' >"${object_health_verification_root}/etc/passwd"
    printf 'root:x:0:\n' >"${object_health_verification_root}/etc/group"
    chmod 0600 "${object_health_verification_root}/etc/passwd" "${object_health_verification_root}/etc/group"
    install -o root -g root -m 0644 "${temporary_release}/backend/backup_health.py" "${object_health_verification_root}/usr/local/libexec/careertrellis/backup_health.py"
    install -o root -g root -m 0755 "${temporary_release}/deploy/scripts/manage-generated-object-backup-health.sh" "${object_health_verification_root}/usr/local/sbin/careertrellis-backup-health"
    for object_health_unit in \
        careertrellis-generated-object-backup-health.service \
        careertrellis-generated-object-backup-health.timer \
        careertrellis-generated-object-backup-health-alert@.service; do
        install -o root -g root -m 0644 \
            "${temporary_release}/deploy/systemd/${object_health_unit}" \
            "${object_health_verification_root}/etc/systemd/system/${object_health_unit}"
    done
    env -u SYSTEMD_UNIT_PATH \
        systemd-analyze --recursive-errors=no --root="${object_health_verification_root}" verify \
        careertrellis-generated-object-backup-health.service \
        careertrellis-generated-object-backup-health.timer \
        careertrellis-generated-object-backup-health-alert@.service
    rm -rf --one-file-system -- "${object_health_verification_root}"
    if [[ -f ${temporary_release}/deploy/scripts/manage-pilot-caddy-credential.sh && \
          ! -L ${temporary_release}/deploy/scripts/manage-pilot-caddy-credential.sh ]]; then
        chmod 0755 "${temporary_release}/deploy/scripts/manage-pilot-caddy-credential.sh"
    fi
    if [[ -f ${temporary_release}/deploy/scripts/manage-pilot-user.sh && \
          ! -L ${temporary_release}/deploy/scripts/manage-pilot-user.sh && \
          -f ${temporary_release}/backend/pilot_users.py && \
          ! -L ${temporary_release}/backend/pilot_users.py ]]; then
        chmod 0755 "${temporary_release}/deploy/scripts/manage-pilot-user.sh"
    fi
    if [[ -e ${temporary_release}/deploy/scripts/manage-pilot-acceptance.sh || \
          -L ${temporary_release}/deploy/scripts/manage-pilot-acceptance.sh || \
          -e ${temporary_release}/backend/pilot_acceptance.py || \
          -L ${temporary_release}/backend/pilot_acceptance.py || \
          -e ${temporary_release}/backend/pilot_acceptance_receipts.py || \
          -L ${temporary_release}/backend/pilot_acceptance_receipts.py ]]; then
        if [[ ! -f ${temporary_release}/deploy/scripts/manage-pilot-acceptance.sh || \
              -L ${temporary_release}/deploy/scripts/manage-pilot-acceptance.sh || \
              ! -f ${temporary_release}/backend/pilot_acceptance.py || \
              -L ${temporary_release}/backend/pilot_acceptance.py || \
              ! -f ${temporary_release}/backend/pilot_acceptance_receipts.py || \
              -L ${temporary_release}/backend/pilot_acceptance_receipts.py ]]; then
            echo "The pilot acceptance boundary must be a complete reviewed trio." >&2
            exit 1
        fi
        chmod 0755 "${temporary_release}/deploy/scripts/manage-pilot-acceptance.sh"
    fi
    chown -R root:careertrellis "${temporary_release}"
    chmod -R u=rwX,g=rX,o= "${temporary_release}"
    mv "${temporary_release}" "${release_dir}"
    temporary_release=""
fi

if [[ ${prepare_only} == true ]]; then
    echo "CareerTrellis staging release prepared at ${revision}; it was not activated."
    exit 0
fi

if [[ ! -x /usr/local/sbin/careertrellis-authority ]]; then
    echo "Hosted authority state is unavailable; rerun reviewed host provisioning before deployment." >&2
    exit 1
fi

assert_consumer_activation_ready() {
    local configured_enabled=$1
    local unit=$2

    if [[ ${configured_enabled} != true ]]; then
        return 0
    fi
    if ! systemctl is-enabled --quiet "${unit}"; then
        echo "Every enabled application consumer must remain enabled before its producer is started." >&2
        return 1
    fi
}

validate_release() {
    local target_release=$1
    local environment_file erasure_batch_size erasure_data_store_count erasure_poll_seconds object_health_command object_health_file recovery_helper target_authority_helper
    local erasure_worker_enabled search_worker_enabled web_erasure_worker_enabled web_search_worker_enabled

    /usr/local/sbin/careertrellis-authority assert-release-startable "${target_release}" || return 1
    target_authority_helper=${target_release}/deploy/scripts/hosted-authority.sh
    if [[ ! -f ${target_authority_helper} || -L ${target_authority_helper} || ! -x ${target_authority_helper} || \
          $(stat -c '%U:%G %a' "${target_authority_helper}") != "root:careertrellis 750" ]]; then
        echo "The target release hosted-authority helper is unavailable." >&2
        return 1
    fi
    "${target_authority_helper}" assert-release-startable "${target_release}" || return 1
    if [[ $(/usr/local/sbin/careertrellis-authority status) == postgres-r2-authoritative ]]; then
        CAREERTRELLIS_DATA_STORE=postgres CAREERTRELLIS_OBJECT_STORE=r2_encrypted \
            "${target_authority_helper}" check-web-runtime || return 1
        "${target_authority_helper}" check-worker-runtime || return 1
    fi
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
    if [[ -e ${target_release}/deploy/scripts/mirror-generated-objects-onedrive.sh || \
          -L ${target_release}/deploy/scripts/mirror-generated-objects-onedrive.sh || \
          -e ${target_release}/deploy/scripts/test-generated-object-restore.sh || \
          -L ${target_release}/deploy/scripts/test-generated-object-restore.sh ]]; then
        for recovery_helper in \
            "${target_release}/deploy/scripts/mirror-generated-objects-onedrive.sh" \
            "${target_release}/deploy/scripts/test-generated-object-restore.sh"; do
            if [[ ! -f ${recovery_helper} || -L ${recovery_helper} || ! -x ${recovery_helper} ]]; then
                echo "Generated-object recovery helpers must be a complete reviewed executable pair." >&2
                return 1
            fi
        done
    fi
    if [[ -e ${target_release}/backend/backup_health.py || \
          -L ${target_release}/backend/backup_health.py || \
          -e ${target_release}/deploy/scripts/manage-generated-object-backup-health.sh || \
          -L ${target_release}/deploy/scripts/manage-generated-object-backup-health.sh || \
          -e ${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.service || \
          -L ${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.service || \
          -e ${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.timer || \
          -L ${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.timer || \
          -e ${target_release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service || \
          -L ${target_release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service ]]; then
        for object_health_file in \
            "${target_release}/backend/backup_health.py" \
            "${target_release}/deploy/scripts/manage-generated-object-backup-health.sh" \
            "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.service" \
            "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.timer" \
            "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service"; do
            if [[ ! -f ${object_health_file} || -L ${object_health_file} ]]; then
                echo "Generated-object backup health must be a complete reviewed bundle." >&2
                return 1
            fi
        done
        for object_health_command in \
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
            /usr/bin/stat \
            /usr/bin/systemctl; do
            if [[ ! -x ${object_health_command} ]]; then
                echo "A required generated-object backup-health command is unavailable." >&2
                return 1
            fi
        done
        if [[ $(stat -c '%U:%G %a' "${target_release}/backend/backup_health.py") != "root:careertrellis 640" || \
              $(stat -c '%U:%G %a' "${target_release}/deploy/scripts/manage-generated-object-backup-health.sh") != "root:careertrellis 750" || \
              $(stat -c '%U:%G %a' "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.service") != "root:careertrellis 640" || \
              $(stat -c '%U:%G %a' "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.timer") != "root:careertrellis 640" || \
              $(stat -c '%U:%G %a' "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service") != "root:careertrellis 640" ]]; then
            echo "The generated-object backup-health bundle has an unsafe owner or mode." >&2
            return 1
        fi
    fi
    local admission_guard=${script_dir}/check-worker-admission-environment.sh
    if [[ ! -f ${admission_guard} ]]; then
        admission_guard=/usr/local/libexec/careertrellis/check-worker-admission-environment.sh
    fi
    if [[ ! -f ${admission_guard} || -L ${admission_guard} || $(stat -c '%u' "${admission_guard}") != 0 || ! $(stat -c '%a' "${admission_guard}") =~ ^75[05]$ ]]; then
        echo "The reviewed worker environment guard is required." >&2
        return 1
    fi
    /bin/bash "${admission_guard}" || return 1
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
    assert_consumer_activation_ready "${search_worker_enabled}" careertrellis-worker.service || return 1
    assert_consumer_activation_ready "${erasure_worker_enabled}" careertrellis-erasure-worker.service || return 1
    for environment_file in \
        /etc/careertrellis/staging.env \
        /etc/careertrellis/search-worker.env; do
        if ! grep -Fx "PLAYWRIGHT_BROWSERS_PATH=${browser_path}" "${environment_file}" >/dev/null; then
            echo "Web and worker runtime files must use the reviewed shared browser path." >&2
            return 1
        fi
    done
    if grep -Fxq $'\t\tpilot-01 {$STAGING_BASIC_AUTH_PILOT_01_HASH}' "${target_release}/deploy/caddy/Caddyfile.staging"; then
        if [[ ! -x ${target_release}/deploy/scripts/manage-pilot-caddy-credential.sh || \
              -L ${target_release}/deploy/scripts/manage-pilot-caddy-credential.sh ]]; then
            echo "The reviewed pilot Caddy credential helper is unavailable." >&2
            return 1
        fi
        "${target_release}/deploy/scripts/manage-pilot-caddy-credential.sh" check >/dev/null || return 1
    fi
    if [[ -e ${target_release}/deploy/scripts/manage-pilot-user.sh || \
          -L ${target_release}/deploy/scripts/manage-pilot-user.sh || \
          -e ${target_release}/backend/pilot_users.py || \
          -L ${target_release}/backend/pilot_users.py ]]; then
        if [[ ! -f ${target_release}/deploy/scripts/manage-pilot-user.sh || \
              -L ${target_release}/deploy/scripts/manage-pilot-user.sh || \
              $(stat -c '%U:%G %a' "${target_release}/deploy/scripts/manage-pilot-user.sh") != "root:careertrellis 750" || \
              ! -f ${target_release}/backend/pilot_users.py || \
              -L ${target_release}/backend/pilot_users.py || \
              $(stat -c '%U:%G %a' "${target_release}/backend/pilot_users.py") != "root:careertrellis 640" ]]; then
            echo "The pilot user operator boundary must be a complete reviewed pair." >&2
            return 1
        fi
    fi
    if [[ -e ${target_release}/deploy/scripts/manage-pilot-acceptance.sh || \
          -L ${target_release}/deploy/scripts/manage-pilot-acceptance.sh || \
          -e ${target_release}/backend/pilot_acceptance.py || \
          -L ${target_release}/backend/pilot_acceptance.py || \
          -e ${target_release}/backend/pilot_acceptance_receipts.py || \
          -L ${target_release}/backend/pilot_acceptance_receipts.py ]]; then
        if [[ ! -f ${target_release}/deploy/scripts/manage-pilot-acceptance.sh || \
              -L ${target_release}/deploy/scripts/manage-pilot-acceptance.sh || \
              $(stat -c '%U:%G %a' "${target_release}/deploy/scripts/manage-pilot-acceptance.sh") != "root:careertrellis 750" || \
              ! -f ${target_release}/backend/pilot_acceptance.py || \
              -L ${target_release}/backend/pilot_acceptance.py || \
              $(stat -c '%U:%G %a' "${target_release}/backend/pilot_acceptance.py") != "root:careertrellis 640" || \
              ! -f ${target_release}/backend/pilot_acceptance_receipts.py || \
              -L ${target_release}/backend/pilot_acceptance_receipts.py || \
              $(stat -c '%U:%G %a' "${target_release}/backend/pilot_acceptance_receipts.py") != "root:careertrellis 640" ]]; then
            echo "The pilot acceptance boundary must be a complete reviewed trio." >&2
            return 1
        fi
    fi
    caddy validate \
        --config "${target_release}/deploy/caddy/Caddyfile.staging" \
        --envfile /etc/caddy/careertrellis-staging.env
}

validate_release "${release_dir}"
install -d -o root -g root -m 0755 /usr/local/libexec/careertrellis
install -o root -g root -m 0755 "${release_dir}/deploy/scripts/maintenance-lock.sh" /usr/local/libexec/careertrellis/maintenance-lock.sh
install -o root -g root -m 0755 "${release_dir}/deploy/scripts/hosted-authority.sh" /usr/local/sbin/careertrellis-authority
install -d -o root -g root -m 0755 /etc/systemd/system/careertrellis-web.service.d
install -d -o root -g root -m 0755 /etc/systemd/system/careertrellis-worker.service.d
install -d -o root -g root -m 0755 /etc/systemd/system/careertrellis-erasure-worker.service.d
install -o root -g root -m 0644 "${release_dir}/deploy/systemd/careertrellis-web.service.d/hosted-authority.conf" /etc/systemd/system/careertrellis-web.service.d/hosted-authority.conf
install -o root -g root -m 0644 "${release_dir}/deploy/systemd/careertrellis-worker.service.d/hosted-authority.conf" /etc/systemd/system/careertrellis-worker.service.d/hosted-authority.conf
install -o root -g root -m 0644 "${release_dir}/deploy/systemd/careertrellis-erasure-worker.service.d/hosted-authority.conf" /etc/systemd/system/careertrellis-erasure-worker.service.d/hosted-authority.conf

old_release=""
if [[ -L ${current_link} ]]; then
    old_release=$(readlink -f "${current_link}")
fi

activate_release() {
    local target_release=$1
    local target_revision activation_link erasure_worker_active=false object_backup_health_available=false object_health_target pilot_acceptance_helper_available=false pilot_user_helper_available=false recovery_helpers_available=false
    local erasure_worker_enabled object_health_active_state search_worker_enabled
    target_revision=$(basename "${target_release}")
    assert_caddy_runtime_privacy_ready "${target_release}" || return 1

    if systemctl is-active --quiet careertrellis-erasure-worker.service; then
        erasure_worker_active=true
        if [[ ! -f ${target_release}/backend/account_erasure_worker.py || ! -f ${target_release}/deploy/systemd/careertrellis-erasure-worker.service ]]; then
            echo "An active account-erasure worker prevents rollback to an incompatible release." >&2
            return 1
        fi
    fi
    # Quiesce the scheduler before the point-in-time service check. This keeps
    # systemd from starting a mixed old/new privileged helper bundle while the
    # release-bound core, wrapper, and units are replaced or removed.
    if systemctl is-active --quiet careertrellis-generated-object-backup-health.timer; then
        systemctl stop careertrellis-generated-object-backup-health.timer || return 1
    fi
    object_health_active_state=$(systemctl show \
        careertrellis-generated-object-backup-health.service \
        --property=ActiveState --value 2>/dev/null || true)
    if [[ -n ${object_health_active_state} && \
          ${object_health_active_state} != inactive && \
          ${object_health_active_state} != failed ]]; then
        echo "An active generated-object health evaluation prevents release activation." >&2
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

    validate_release "${target_release}" || return 1
    search_worker_enabled=$(sed -n 's/^CAREERTRELLIS_SEARCH_WORKER_ENABLED=//p' /etc/careertrellis/search-worker.env)
    erasure_worker_enabled=$(sed -n 's/^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=//p' /etc/careertrellis/account-erasure-worker.env)
    if [[ -x ${target_release}/deploy/scripts/mirror-generated-objects-onedrive.sh && \
          -x ${target_release}/deploy/scripts/test-generated-object-restore.sh ]]; then
        recovery_helpers_available=true
    fi
    if [[ -f ${target_release}/backend/backup_health.py && \
          -x ${target_release}/deploy/scripts/manage-generated-object-backup-health.sh && \
          -f ${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.service && \
          -f ${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.timer && \
          -f ${target_release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service ]]; then
        object_backup_health_available=true
        ensure_object_backup_health_roots || return 1
    fi
    if [[ -f ${target_release}/deploy/scripts/manage-pilot-user.sh && \
          ! -L ${target_release}/deploy/scripts/manage-pilot-user.sh && \
          -f ${target_release}/backend/pilot_users.py && \
          ! -L ${target_release}/backend/pilot_users.py ]]; then
        pilot_user_helper_available=true
    fi
    if [[ -f ${target_release}/deploy/scripts/manage-pilot-acceptance.sh && \
          ! -L ${target_release}/deploy/scripts/manage-pilot-acceptance.sh && \
          -f ${target_release}/backend/pilot_acceptance.py && \
          ! -L ${target_release}/backend/pilot_acceptance.py && \
          -f ${target_release}/backend/pilot_acceptance_receipts.py && \
          ! -L ${target_release}/backend/pilot_acceptance_receipts.py ]]; then
        pilot_acceptance_helper_available=true
        ensure_pilot_acceptance_root || return 1
    fi

    assert_no_active_pilot_acceptance || return 1
    ensure_caddy_access_log || return 1
    activation_link=${release_root}/.activate-${target_revision}-$$
    rm -f -- "${activation_link}"
    ln -s "${target_release}" "${activation_link}" || return 1
    mv -Tf "${activation_link}" "${current_link}" || return 1

    install -o root -g root -m 0644 "${target_release}/deploy/systemd/careertrellis-web.service" /etc/systemd/system/careertrellis-web.service || return 1
    install -o root -g root -m 0644 "${target_release}/deploy/systemd/careertrellis-worker.service" /etc/systemd/system/careertrellis-worker.service || return 1
    if [[ -f ${target_release}/deploy/systemd/careertrellis-erasure-worker.service ]]; then
        install -o root -g root -m 0644 "${target_release}/deploy/systemd/careertrellis-erasure-worker.service" /etc/systemd/system/careertrellis-erasure-worker.service || return 1
    fi
    install -d -o root -g root -m 0755 /etc/systemd/system/caddy.service.d || return 1
    install -o root -g root -m 0644 "${target_release}/deploy/systemd/caddy.service.d/careertrellis-staging.conf" /etc/systemd/system/caddy.service.d/careertrellis-staging.conf || return 1
    install -o root -g root -m 0644 "${target_release}/deploy/caddy/Caddyfile.staging" /etc/caddy/Caddyfile || return 1
    install -o root -g root -m 0755 "${target_release}/deploy/scripts/deploy-release.sh" /usr/local/sbin/careertrellis-deploy || return 1
    install -o root -g root -m 0755 "${target_release}/deploy/scripts/rollback-release.sh" /usr/local/sbin/careertrellis-rollback || return 1
    install -o root -g root -m 0755 "${target_release}/deploy/scripts/install-erasure-worker-credential.sh" /usr/local/sbin/careertrellis-install-erasure-worker-credential || return 1
    if grep -Fxq $'\t\tpilot-01 {$STAGING_BASIC_AUTH_PILOT_01_HASH}' "${target_release}/deploy/caddy/Caddyfile.staging"; then
        install -o root -g root -m 0755 "${target_release}/deploy/scripts/manage-pilot-caddy-credential.sh" /usr/local/sbin/careertrellis-caddy-pilot-credential || return 1
    fi
    if [[ ${pilot_user_helper_available} == true ]]; then
        install -o root -g root -m 0750 "${target_release}/deploy/scripts/manage-pilot-user.sh" /usr/local/sbin/careertrellis-pilot-user || return 1
        install -o root -g root -m 0640 "${target_release}/backend/pilot_users.py" /usr/local/libexec/careertrellis/pilot_users.py || return 1
    else
        rm -f -- /usr/local/sbin/careertrellis-pilot-user /usr/local/libexec/careertrellis/pilot_users.py || return 1
    fi
    if [[ ${pilot_acceptance_helper_available} == true ]]; then
        install -o root -g root -m 0750 "${target_release}/deploy/scripts/manage-pilot-acceptance.sh" /usr/local/sbin/careertrellis-pilot-acceptance || return 1
        install -o root -g root -m 0640 "${target_release}/backend/pilot_acceptance.py" /usr/local/libexec/careertrellis/pilot_acceptance.py || return 1
        install -o root -g root -m 0640 "${target_release}/backend/pilot_acceptance_receipts.py" /usr/local/libexec/careertrellis/pilot_acceptance_receipts.py || return 1
    else
        rm -f -- /usr/local/sbin/careertrellis-pilot-acceptance /usr/local/libexec/careertrellis/pilot_acceptance.py /usr/local/libexec/careertrellis/pilot_acceptance_receipts.py || return 1
    fi
    install -o root -g root -m 0755 "${target_release}/deploy/scripts/finalize-postgres-r2-cutover.sh" /usr/local/sbin/careertrellis-finalize-cutover || return 1
    if [[ ${recovery_helpers_available} == true ]]; then
        [[ -f ${target_release}/backend/generated_object_recovery.py && ! -L ${target_release}/backend/generated_object_recovery.py && ! -L /usr/local/libexec/careertrellis/generated_object_recovery.py ]] || return 1
        install -o root -g root -m 0644 "${target_release}/backend/generated_object_recovery.py" /usr/local/libexec/careertrellis/generated_object_recovery.py || return 1
        local capture_root=/var/lib/careertrellis/generated-object-captures
        if [[ -e ${capture_root} || -L ${capture_root} ]]; then
            [[ -d ${capture_root} && ! -L ${capture_root} && $(stat -c '%U:%G %a' "${capture_root}") == 'root:careertrellis-object-recovery 750' ]] || return 1
        else
            install -d -o root -g careertrellis-object-recovery -m 0750 "${capture_root}" || return 1
        fi
        install -o root -g root -m 0755 "${target_release}/deploy/scripts/mirror-generated-objects-onedrive.sh" /usr/local/sbin/careertrellis-mirror-generated-objects || return 1
        install -o root -g root -m 0755 "${target_release}/deploy/scripts/test-generated-object-restore.sh" /usr/local/sbin/careertrellis-test-generated-object-restore || return 1
    fi
    if [[ ${object_backup_health_available} == true ]]; then
        install -o root -g root -m 0644 "${target_release}/backend/backup_health.py" /usr/local/libexec/careertrellis/backup_health.py || return 1
        install -o root -g root -m 0755 "${target_release}/deploy/scripts/manage-generated-object-backup-health.sh" /usr/local/sbin/careertrellis-backup-health || return 1
        systemd-analyze verify \
            "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.service" \
            "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.timer" \
            "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service" || return 1
        install -o root -g root -m 0644 "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.service" /etc/systemd/system/careertrellis-generated-object-backup-health.service || return 1
        install -o root -g root -m 0644 "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health.timer" /etc/systemd/system/careertrellis-generated-object-backup-health.timer || return 1
        install -o root -g root -m 0644 "${target_release}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service" /etc/systemd/system/careertrellis-generated-object-backup-health-alert@.service || return 1
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
    if systemctl is-active --quiet caddy; then
        systemctl reload caddy || return 1
    else
        systemctl enable --now caddy || return 1
    fi
    if [[ ${search_worker_enabled} == true ]]; then
        systemctl restart careertrellis-worker.service || return 1
        systemctl is-active --quiet careertrellis-worker.service || return 1
    fi
    if [[ ${erasure_worker_enabled} == true ]]; then
        systemctl restart careertrellis-erasure-worker.service || return 1
        systemctl is-active --quiet careertrellis-erasure-worker.service || return 1
    fi
    systemctl restart careertrellis-web.service || return 1

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

contain_failed_activation() {
    local caddy_enabled_state containment_failed=false unit unit_active_state
    # Stop the public edge first so a pre-reload failure cannot leave the
    # previous Caddy process writing records below the current privacy floor.
    # Disable it as well so a reboot cannot revive that configuration before a
    # reviewed forward correction reaches the normal enable-and-start path.
    systemctl disable --now caddy.service || containment_failed=true
    systemctl stop \
        careertrellis-erasure-worker.service \
        careertrellis-worker.service \
        careertrellis-web.service || containment_failed=true
    for unit in \
        caddy.service \
        careertrellis-erasure-worker.service \
        careertrellis-worker.service \
        careertrellis-web.service; do
        unit_active_state=$(systemctl show "${unit}" --property=ActiveState --value 2>/dev/null || true)
        if [[ ${unit_active_state} != inactive && ${unit_active_state} != failed ]]; then
            containment_failed=true
        fi
    done
    caddy_enabled_state=$(systemctl is-enabled caddy.service 2>/dev/null || true)
    if [[ ${caddy_enabled_state} != disabled ]]; then
        containment_failed=true
    fi
    if [[ ${containment_failed} == true ]]; then
        echo "Critical: failed release containment could not confirm every public/application service stopped." >&2
        return 1
    fi
}

if ! activate_release "${release_dir}"; then
    echo "New release did not become ready." >&2
    if [[ -n ${old_release} ]] && /usr/local/sbin/careertrellis-authority assert-code-rollback-allowed; then
        echo "Restoring the prior code release." >&2
        if ! activate_release "${old_release}"; then
            echo "Critical: the prior release also failed readiness after restoration." >&2
            contain_failed_activation || true
        fi
    else
        contain_failed_activation || true
        if [[ -z ${old_release} ]]; then
            rm -f -- "${current_link}"
        else
            echo "Hosted authority requires a reviewed forward correction; Caddy and application services remain stopped." >&2
        fi
    fi
    exit 1
fi

if [[ -n ${old_release} && ${old_release} != "${release_dir}" ]]; then
    previous_new=${release_root}/.previous-${revision}-$$
    ln -s "${old_release}" "${previous_new}"
    mv -Tf "${previous_new}" "${previous_link}"
fi
echo "CareerTrellis staging deployed at ${revision}."
