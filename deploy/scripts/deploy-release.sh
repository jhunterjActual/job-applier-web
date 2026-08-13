#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the deployment script as root." >&2
    exit 1
fi

exec 8>/run/lock/careertrellis-maintenance.lock
if ! flock -n 8; then
    echo "Another deployment, rollback, or import operation is running." >&2
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
browser_path=${PLAYWRIGHT_BROWSERS_PATH:-/var/lib/careertrellis/staging/ms-playwright}
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
install -d -o careertrellis -g careertrellis -m 0750 "${browser_path}"

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
    chown -R careertrellis:careertrellis "${browser_path}"
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
        ./venv/bin/python -m unittest test_p0_regressions.py test_object_store.py test_object_import.py test_stored_objects.py test_analytics.py test_observability.py
        ./venv/bin/python -m compileall -q .
    )
    chown -R root:careertrellis "${temporary_release}"
    chmod -R u=rwX,g=rX,o= "${temporary_release}"
    mv "${temporary_release}" "${release_dir}"
    temporary_release=""
fi

if [[ ${prepare_only} == true ]]; then
    echo "CareerTrellis staging release prepared at ${revision}; it was not activated."
    exit 0
fi

old_release=""
if [[ -L ${current_link} ]]; then
    old_release=$(readlink -f "${current_link}")
fi

activate_release() {
    local target_release=$1
    local target_revision activation_link environment_file
    target_revision=$(basename "${target_release}")

    for environment_file in \
        /etc/careertrellis/staging.env \
        /etc/careertrellis/search-worker.env \
        /etc/careertrellis/postgres-runtime.env \
        /etc/careertrellis/postgres-worker.env \
        /etc/careertrellis/provider-secrets.env \
        /etc/careertrellis/object-storage.env; do
        if [[ ! -f ${environment_file} || $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
            echo "Every service runtime environment file must be root:root mode 0600." >&2
            return 1
        fi
    done

    caddy validate \
        --config "${target_release}/deploy/caddy/Caddyfile.staging" \
        --envfile /etc/caddy/careertrellis-staging.env || return 1

    activation_link=${release_root}/.activate-${target_revision}-$$
    rm -f -- "${activation_link}"
    ln -s "${target_release}" "${activation_link}" || return 1
    mv -Tf "${activation_link}" "${current_link}" || return 1

    install -o root -g root -m 0644 "${target_release}/deploy/systemd/careertrellis-web.service" /etc/systemd/system/careertrellis-web.service || return 1
    install -o root -g root -m 0644 "${target_release}/deploy/systemd/careertrellis-worker.service" /etc/systemd/system/careertrellis-worker.service || return 1
    install -d -o root -g root -m 0755 /etc/systemd/system/caddy.service.d || return 1
    install -o root -g root -m 0644 "${target_release}/deploy/systemd/caddy.service.d/careertrellis-staging.conf" /etc/systemd/system/caddy.service.d/careertrellis-staging.conf || return 1
    install -o root -g root -m 0644 "${target_release}/deploy/caddy/Caddyfile.staging" /etc/caddy/Caddyfile.careertrellis-staging || return 1
    install -o root -g root -m 0755 "${target_release}/deploy/scripts/deploy-release.sh" /usr/local/sbin/careertrellis-deploy || return 1
    install -o root -g root -m 0755 "${target_release}/deploy/scripts/rollback-release.sh" /usr/local/sbin/careertrellis-rollback || return 1
    systemctl daemon-reload || return 1
    if systemctl is-active --quiet caddy; then
        systemctl reload caddy || return 1
    else
        systemctl enable --now caddy || return 1
    fi
    systemctl restart careertrellis-worker.service careertrellis-web.service || return 1

    for _attempt in $(seq 1 30); do
        if curl --fail --silent --show-error --max-time 2 http://127.0.0.1:8001/health/ready >/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

if ! activate_release "${release_dir}"; then
    echo "New release did not become ready; restoring the prior code release." >&2
    if [[ -n ${old_release} ]]; then
        if ! activate_release "${old_release}"; then
            echo "Critical: the prior release also failed readiness after restoration." >&2
        fi
    else
        rm -f -- "${current_link}"
        systemctl stop careertrellis-worker.service careertrellis-web.service
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
