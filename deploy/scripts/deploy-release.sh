#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the deployment script as root." >&2
    exit 1
fi

exec 8>/run/lock/careertrellis-maintenance.lock
if ! flock -n 8; then
    echo "Another deployment, rollback, import, or reconciliation operation is running." >&2
    exit 1
fi

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
temporary_release=""
test_data=""

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
        export PLAYWRIGHT_BROWSERS_PATH="${browser_path}"
        CAREERTRELLIS_ENVIRONMENT=local \
        CAREERTRELLIS_DATA_STORE=sqlite \
        CAREERTRELLIS_ALLOWED_HOSTS= \
        CAREERTRELLIS_TRUSTED_PROXY_TOKEN= \
        CAREERTRELLIS_DATA_DIR="${test_data}" \
        PYTHONDONTWRITEBYTECODE=1 \
        ./venv/bin/python -m unittest test_p0_regressions.py test_search_runs.py test_scheduled_search_dispatch.py test_account_erasure.py test_object_store.py test_object_import.py test_import_reconciliation.py test_stored_objects.py test_analytics.py test_observability.py
        CAREERTRELLIS_ENVIRONMENT=staging \
        CAREERTRELLIS_DATA_DIR="${test_data}/account-erasure" \
        CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED=false \
        ./venv/bin/python account_erasure_worker.py --once
        ./venv/bin/python -m compileall -q .
    )
    chmod 0755 \
        "${temporary_release}/deploy/scripts/bootstrap-erasure-worker-role.sh" \
        "${temporary_release}/deploy/scripts/install-erasure-worker-credential.sh" \
        "${temporary_release}/deploy/scripts/reconcile-sqlite-postgres-import.sh"
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

validate_release() {
    local target_release=$1
    local environment_file erasure_batch_size erasure_data_store_count erasure_poll_seconds

    /usr/local/sbin/careertrellis-authority assert-release-startable "${target_release}" || return 1
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
        --config "${target_release}/deploy/caddy/Caddyfile.staging" \
        --envfile /etc/caddy/careertrellis-staging.env
}

validate_release "${release_dir}"
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
    local target_revision activation_link erasure_worker_active=false
    target_revision=$(basename "${target_release}")

    if systemctl is-active --quiet careertrellis-erasure-worker.service; then
        erasure_worker_active=true
        if [[ ! -f ${target_release}/backend/account_erasure_worker.py || ! -f ${target_release}/deploy/systemd/careertrellis-erasure-worker.service ]]; then
            echo "An active account-erasure worker prevents rollback to an incompatible release." >&2
            return 1
        fi
    fi

    validate_release "${target_release}" || return 1

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
    install -o root -g root -m 0644 "${target_release}/deploy/caddy/Caddyfile.staging" /etc/caddy/Caddyfile.careertrellis-staging || return 1
    install -o root -g root -m 0755 "${target_release}/deploy/scripts/deploy-release.sh" /usr/local/sbin/careertrellis-deploy || return 1
    install -o root -g root -m 0755 "${target_release}/deploy/scripts/rollback-release.sh" /usr/local/sbin/careertrellis-rollback || return 1
    install -o root -g root -m 0755 "${target_release}/deploy/scripts/install-erasure-worker-credential.sh" /usr/local/sbin/careertrellis-install-erasure-worker-credential || return 1
    systemctl daemon-reload || return 1
    if systemctl is-active --quiet caddy; then
        systemctl reload caddy || return 1
    else
        systemctl enable --now caddy || return 1
    fi
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

if ! activate_release "${release_dir}"; then
    echo "New release did not become ready." >&2
    if [[ -n ${old_release} ]] && /usr/local/sbin/careertrellis-authority assert-code-rollback-allowed; then
        echo "Restoring the prior code release." >&2
        if ! activate_release "${old_release}"; then
            echo "Critical: the prior release also failed readiness after restoration." >&2
        fi
    else
        systemctl stop careertrellis-erasure-worker.service careertrellis-worker.service careertrellis-web.service
        if [[ -z ${old_release} ]]; then
            rm -f -- "${current_link}"
        else
            echo "Hosted authority requires a reviewed forward correction; services remain stopped." >&2
        fi
    fi
    exit 1
fi

if [[ -n ${old_release} && ${old_release} != "${release_dir}" ]]; then
    previous_new=${release_root}/.previous-${revision}-$$
    ln -s "${old_release}" "${previous_new}"
    mv -Tf "${previous_new}" "${previous_link}"
fi
systemctl enable careertrellis-worker.service careertrellis-web.service >/dev/null
echo "CareerTrellis staging deployed at ${revision}."
