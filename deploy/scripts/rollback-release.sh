#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the rollback script as root." >&2
    exit 1
fi

exec 8>/run/lock/careertrellis-maintenance.lock
if ! flock -n 8; then
    echo "Another deployment, rollback, or import operation is running." >&2
    exit 1
fi

release_root=${CAREERTRELLIS_RELEASE_ROOT:-/opt/careertrellis/staging}
browser_path=/var/lib/careertrellis/shared/ms-playwright
current_link=${release_root}/current
previous_link=${release_root}/previous
requested_revision=${1:-}

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

activate_release() {
    local release=$1
    local revision activation_link environment_file erasure_batch_size erasure_data_store_count erasure_poll_seconds erasure_worker_active=false
    revision=$(basename "${release}")

    /usr/local/sbin/careertrellis-authority assert-release-startable "${release}" || return 1

    if systemctl is-active --quiet careertrellis-erasure-worker.service; then
        erasure_worker_active=true
        if [[ ! -f ${release}/backend/account_erasure_worker.py || ! -f ${release}/deploy/systemd/careertrellis-erasure-worker.service ]]; then
            echo "An active account-erasure worker prevents rollback to an incompatible release." >&2
            return 1
        fi
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
        /etc/careertrellis/account-erasure-object-storage.env; do
        if [[ ! -f ${environment_file} || -L ${environment_file} || $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
            echo "Every service runtime environment file must be root:root mode 0600." >&2
            return 1
        fi
    done
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
    erasure_data_store_count=$(grep -Ec '^CAREERTRELLIS_DATA_STORE=' /etc/careertrellis/account-erasure-worker.env)
    if [[ $(grep -Ec '^CAREERTRELLIS_ENVIRONMENT=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Fxc "CAREERTRELLIS_ENVIRONMENT=staging" /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_DATA_DIR=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Fxc "CAREERTRELLIS_DATA_DIR=/var/lib/careertrellis/account-erasure" /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ ${erasure_data_store_count} -gt 1 ]] || \
       [[ ${erasure_data_store_count} -eq 1 && $(grep -Fxc "CAREERTRELLIS_DATA_STORE=postgres" /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Fxc "CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=false" /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_POLL_SECONDS=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ ! ${erasure_poll_seconds} =~ ^(0|[1-9][0-9]{0,2})$ || ${erasure_poll_seconds} -lt 5 || ${erasure_poll_seconds} -gt 300 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_BATCH_SIZE=' /etc/careertrellis/account-erasure-worker.env) -ne 1 ]] || \
       [[ ! ${erasure_batch_size} =~ ^(0|[1-9][0-9]{0,2})$ || ${erasure_batch_size} -lt 1 || ${erasure_batch_size} -gt 500 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_DATABASE_URL=' /etc/careertrellis/postgres-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_DATABASE_URL=postgresql\+psycopg://careertrellis_erasure_worker:' /etc/careertrellis/postgres-erasure-worker.env) -ne 1 ]] || \
       [[ $(grep -Ec '^CAREERTRELLIS_OBJECT_STORE=' /etc/careertrellis/account-erasure-object-storage.env) -ne 1 ]] || \
       [[ $(grep -Fxc "CAREERTRELLIS_OBJECT_STORE=r2_encrypted" /etc/careertrellis/account-erasure-object-storage.env) -ne 1 ]]; then
        echo "The account-erasure worker must retain its reviewed disabled settings, dedicated database, and object-store selections." >&2
        return 1
    fi
    for environment_file in \
        /etc/careertrellis/staging.env \
        /etc/careertrellis/search-worker.env; do
        if ! grep -Fx "PLAYWRIGHT_BROWSERS_PATH=${browser_path}" "${environment_file}" >/dev/null; then
            echo "Web and worker runtime files must use the reviewed shared browser path." >&2
            return 1
        fi
    done

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
    install -o root -g root -m 0644 "${release}/deploy/caddy/Caddyfile.staging" /etc/caddy/Caddyfile.careertrellis-staging || return 1
    systemctl daemon-reload || return 1
    systemctl reload caddy || return 1
    systemctl restart careertrellis-worker.service careertrellis-web.service || return 1
    if [[ ${erasure_worker_active} == true ]]; then
        systemctl restart careertrellis-erasure-worker.service || return 1
    fi
    for _attempt in $(seq 1 30); do
        if curl --fail --silent --show-error --max-time 2 http://127.0.0.1:8001/health/ready >/dev/null; then
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
