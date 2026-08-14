#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this one-time provisioning script as root." >&2
    exit 1
fi

repository_root=${1:-}
if [[ -z ${repository_root} || ! -f ${repository_root}/deploy/systemd/careertrellis-web.service ]]; then
    echo "Usage: $0 /absolute/path/to/job-applier-web" >&2
    exit 1
fi
repository_root=$(realpath "${repository_root}")

for command in age caddy curl flock git python3 realpath stat sync systemctl tar; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "Required command is missing: ${command}" >&2
        exit 1
    fi
done

if ! id careertrellis >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/careertrellis --shell /usr/sbin/nologin careertrellis
fi
if ! id careertrellis-worker >/dev/null 2>&1; then
    useradd --system --gid careertrellis --home-dir /var/lib/careertrellis/worker --shell /usr/sbin/nologin careertrellis-worker
fi
if ! id careertrellis-erasure-worker >/dev/null 2>&1; then
    useradd --system --gid careertrellis --home-dir /var/lib/careertrellis/account-erasure --shell /usr/sbin/nologin careertrellis-erasure-worker
fi

install -d -o root -g root -m 0755 /opt/careertrellis/staging/releases
install -d -o careertrellis -g careertrellis -m 0700 /var/lib/careertrellis/staging
install -d -o careertrellis-worker -g careertrellis -m 0700 /var/lib/careertrellis/worker
install -d -o careertrellis-erasure-worker -g careertrellis -m 0700 /var/lib/careertrellis/account-erasure
install -d -o root -g careertrellis -m 0750 /var/lib/careertrellis/shared
install -d -o root -g root -m 0755 /etc/careertrellis
install -d -o caddy -g caddy -m 0750 /var/log/caddy
caddy_access_log=/var/log/caddy/careertrellis-staging-access.log
if [[ -e ${caddy_access_log} ]]; then
    chown caddy:caddy "${caddy_access_log}"
    chmod 0600 "${caddy_access_log}"
else
    install -o caddy -g caddy -m 0600 /dev/null "${caddy_access_log}"
fi

install -o root -g root -m 0644 "${repository_root}/deploy/systemd/careertrellis-web.service" /etc/systemd/system/careertrellis-web.service
install -o root -g root -m 0644 "${repository_root}/deploy/systemd/careertrellis-worker.service" /etc/systemd/system/careertrellis-worker.service
install -o root -g root -m 0644 "${repository_root}/deploy/systemd/careertrellis-erasure-worker.service" /etc/systemd/system/careertrellis-erasure-worker.service
install -d -o root -g root -m 0755 /etc/systemd/system/careertrellis-web.service.d
install -d -o root -g root -m 0755 /etc/systemd/system/careertrellis-worker.service.d
install -d -o root -g root -m 0755 /etc/systemd/system/careertrellis-erasure-worker.service.d
install -o root -g root -m 0644 "${repository_root}/deploy/systemd/careertrellis-web.service.d/hosted-authority.conf" /etc/systemd/system/careertrellis-web.service.d/hosted-authority.conf
install -o root -g root -m 0644 "${repository_root}/deploy/systemd/careertrellis-worker.service.d/hosted-authority.conf" /etc/systemd/system/careertrellis-worker.service.d/hosted-authority.conf
install -o root -g root -m 0644 "${repository_root}/deploy/systemd/careertrellis-erasure-worker.service.d/hosted-authority.conf" /etc/systemd/system/careertrellis-erasure-worker.service.d/hosted-authority.conf
install -d -o root -g root -m 0755 /etc/systemd/system/caddy.service.d
install -o root -g root -m 0644 "${repository_root}/deploy/systemd/caddy.service.d/careertrellis-staging.conf" /etc/systemd/system/caddy.service.d/careertrellis-staging.conf
install -o root -g root -m 0644 "${repository_root}/deploy/caddy/Caddyfile.staging" /etc/caddy/Caddyfile.careertrellis-staging
install -d -o root -g root -m 0755 /usr/local/libexec/careertrellis
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/maintenance-lock.sh" /usr/local/libexec/careertrellis/maintenance-lock.sh
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/deploy-release.sh" /usr/local/sbin/careertrellis-deploy
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/rollback-release.sh" /usr/local/sbin/careertrellis-rollback
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/hosted-authority.sh" /usr/local/sbin/careertrellis-authority
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/install-erasure-worker-credential.sh" /usr/local/sbin/careertrellis-install-erasure-worker-credential
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/finalize-postgres-r2-cutover.sh" /usr/local/sbin/careertrellis-finalize-cutover
/usr/local/sbin/careertrellis-authority initialize

if [[ ! -e /etc/careertrellis/staging.env ]]; then
    install -o root -g root -m 0600 "${repository_root}/deploy/config/staging-app.environment.example" /etc/careertrellis/staging.env
fi
if [[ ! -e /etc/careertrellis/search-worker.env ]]; then
    install -o root -g root -m 0600 "${repository_root}/deploy/config/search-worker.environment.example" /etc/careertrellis/search-worker.env
fi
if [[ ! -e /etc/careertrellis/account-erasure-worker.env ]]; then
    install -o root -g root -m 0600 "${repository_root}/deploy/config/account-erasure-worker.environment.example" /etc/careertrellis/account-erasure-worker.env
fi
if [[ ! -e /etc/careertrellis/account-erasure-object-storage.env ]]; then
    install -o root -g root -m 0600 "${repository_root}/deploy/config/account-erasure-object-storage.environment.example" /etc/careertrellis/account-erasure-object-storage.env
fi
if [[ ! -e /etc/caddy/careertrellis-staging.env ]]; then
    install -o root -g root -m 0600 "${repository_root}/deploy/config/staging-caddy.environment.example" /etc/caddy/careertrellis-staging.env
fi

systemctl daemon-reload
echo "Provisioning files installed. Replace every placeholder in:"
echo "  /etc/careertrellis/staging.env"
echo "  /etc/careertrellis/search-worker.env"
echo "  /etc/careertrellis/account-erasure-worker.env"
echo "  /etc/careertrellis/account-erasure-object-storage.env"
echo "  /etc/caddy/careertrellis-staging.env"
echo "Install the separate erasure-worker database credential, then validate Caddy and deploy an exact commit."
echo "The account-erasure worker remains disabled and no services were started."
