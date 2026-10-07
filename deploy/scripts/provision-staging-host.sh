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

for acceptance_file in \
    "${repository_root}/deploy/scripts/manage-pilot-acceptance.sh" \
    "${repository_root}/backend/pilot_acceptance.py" \
    "${repository_root}/backend/pilot_acceptance_receipts.py"; do
    if [[ ! -f ${acceptance_file} || -L ${acceptance_file} ]]; then
        echo "The pilot acceptance boundary must be a complete reviewed trio." >&2
        exit 1
    fi
done

for object_health_file in \
    "${repository_root}/backend/backup_health.py" \
    "${repository_root}/deploy/scripts/manage-generated-object-backup-health.sh" \
    "${repository_root}/deploy/systemd/careertrellis-generated-object-backup-health.service" \
    "${repository_root}/deploy/systemd/careertrellis-generated-object-backup-health.timer" \
    "${repository_root}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service"; do
    if [[ ! -f ${object_health_file} || -L ${object_health_file} ]]; then
        echo "Generated-object backup health must be a complete reviewed bundle." >&2
        exit 1
    fi
done

for command in age caddy curl flock getent git python3 realpath stat sync systemctl systemd-analyze tar; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "Required command is missing: ${command}" >&2
        exit 1
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
        exit 1
    fi
done
bash -n \
    "${repository_root}/deploy/scripts/manage-generated-object-backup-health.sh" \
    "${repository_root}/deploy/scripts/mirror-generated-objects-onedrive.sh" \
    "${repository_root}/deploy/scripts/test-generated-object-restore.sh"
python3 -B -c 'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), sys.argv[1], "exec")' \
    "${repository_root}/backend/backup_health.py"

maintenance_lock_helper=${repository_root}/deploy/scripts/maintenance-lock.sh
if [[ ! -f ${maintenance_lock_helper} || -L ${maintenance_lock_helper} ]]; then
    echo "The reviewed maintenance-lock helper is unavailable." >&2
    exit 1
fi
# shellcheck source=maintenance-lock.sh
source "${maintenance_lock_helper}"
acquire_careertrellis_maintenance_lock
if systemctl is-active --quiet careertrellis-generated-object-backup-health.timer || \
    [[ $(systemctl show careertrellis-generated-object-backup-health.service \
        --property=ActiveState --value 2>/dev/null || true) =~ ^(active|activating|deactivating|reloading)$ ]]; then
    echo "An active generated-object health scheduler prevents reprovisioning; use a reviewed release activation." >&2
    exit 1
fi

pilot_acceptance_parent=/var/backups/careertrellis
pilot_acceptance_root=${pilot_acceptance_parent}/pilot-auth-acceptance
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

if [[ -e /etc/caddy/careertrellis-staging.env || -L /etc/caddy/careertrellis-staging.env ]] && \
   [[ ! -f /etc/caddy/careertrellis-staging.env || -L /etc/caddy/careertrellis-staging.env || \
      $(stat -c '%U:%G %a' /etc/caddy/careertrellis-staging.env) != "root:root 600" ]]; then
    echo "The Caddy environment is invalid." >&2
    exit 1
fi

if ! id careertrellis >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/careertrellis --shell /usr/sbin/nologin careertrellis
fi
if ! id careertrellis-worker >/dev/null 2>&1; then
    useradd --system --gid careertrellis --home-dir /var/lib/careertrellis/worker --shell /usr/sbin/nologin careertrellis-worker
fi
if ! id careertrellis-erasure-worker >/dev/null 2>&1; then
    useradd --system --gid careertrellis --home-dir /var/lib/careertrellis/account-erasure --shell /usr/sbin/nologin careertrellis-erasure-worker
fi
if ! id careertrellis-object-recovery >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/careertrellis/generated-object-recovery --shell /usr/sbin/nologin careertrellis-object-recovery
fi
IFS=: read -r _ _ _ _ _ generated_object_recovery_home generated_object_recovery_shell < <(getent passwd careertrellis-object-recovery)
if [[ $(id -gn careertrellis-object-recovery) != "careertrellis-object-recovery" || \
      ${generated_object_recovery_home} != "/var/lib/careertrellis/generated-object-recovery" || \
      ${generated_object_recovery_shell} != "/usr/sbin/nologin" ]]; then
    echo "The dedicated generated-object recovery account is invalid." >&2
    exit 1
fi

ensure_pilot_acceptance_root
install -d -o root -g root -m 0755 /opt/careertrellis/staging/releases
install -d -o careertrellis -g careertrellis -m 0700 /var/lib/careertrellis/staging
install -d -o careertrellis-worker -g careertrellis -m 0700 /var/lib/careertrellis/worker
install -d -o careertrellis-erasure-worker -g careertrellis -m 0700 /var/lib/careertrellis/account-erasure
install -d -o root -g careertrellis -m 0750 /var/lib/careertrellis/shared
public_feed_cache=/var/lib/careertrellis/public-job-feeds
if [[ -e ${public_feed_cache} || -L ${public_feed_cache} ]]; then
    if [[ ! -d ${public_feed_cache} || -L ${public_feed_cache} || \
          $(realpath "${public_feed_cache}") != "${public_feed_cache}" || \
          $(stat -c '%U:%G %a' "${public_feed_cache}") != 'careertrellis-worker:careertrellis 750' ]]; then
        echo 'The public feed cache directory has unexpected ownership or permissions.' >&2
        exit 1
    fi
else
    install -d -o careertrellis-worker -g careertrellis -m 0750 "${public_feed_cache}"
fi
for object_health_directory in \
    /var/backups/careertrellis/backup-health \
    /var/backups/careertrellis/backup-health/operations \
    /var/lib/careertrellis/backup-health \
    /var/lib/careertrellis/backup-health/objects \
    /var/lib/careertrellis/backup-health/objects/alerts \
    /var/lib/careertrellis/backup-health/objects/unit-alerts; do
    ensure_root_only_directory "${object_health_directory}"
done
object_health_lock=/run/lock/careertrellis-backup-health-objects.lock
if [[ -e ${object_health_lock} || -L ${object_health_lock} ]]; then
    if [[ ! -f ${object_health_lock} || -L ${object_health_lock} || \
          $(stat -c '%U:%G %a' "${object_health_lock}") != "root:root 600" ]]; then
        echo "The generated-object backup-health lock is invalid." >&2
        exit 1
    fi
else
    install -o root -g root -m 0600 /dev/null "${object_health_lock}"
fi
generated_object_recovery_state=/var/lib/careertrellis/generated-object-recovery
generated_object_recovery_work=${generated_object_recovery_state}/work
for generated_object_recovery_directory in "${generated_object_recovery_state}" "${generated_object_recovery_work}"; do
    if [[ (-e ${generated_object_recovery_directory} || -L ${generated_object_recovery_directory}) && \
          (! -d ${generated_object_recovery_directory} || -L ${generated_object_recovery_directory}) ]]; then
        echo "The dedicated generated-object recovery state is invalid." >&2
        exit 1
    fi
done
install -d -o careertrellis-object-recovery -g careertrellis-object-recovery -m 0700 "${generated_object_recovery_state}"
install -d -o careertrellis-object-recovery -g careertrellis-object-recovery -m 0700 "${generated_object_recovery_work}"
capture_root=/var/lib/careertrellis/generated-object-captures
if [[ -e ${capture_root} || -L ${capture_root} ]]; then
    [[ -d ${capture_root} && ! -L ${capture_root} && $(stat -c '%U:%G %a' "${capture_root}") == 'root:careertrellis-object-recovery 750' ]] || exit 1
else
    install -d -o root -g careertrellis-object-recovery -m 0750 "${capture_root}"
fi
generated_object_rclone_config=/var/lib/careertrellis/generated-object-recovery/rclone.conf
if [[ -e ${generated_object_rclone_config} || -L ${generated_object_rclone_config} ]]; then
    if [[ ! -f ${generated_object_rclone_config} || -L ${generated_object_rclone_config} || $(stat -c '%U:%G %a' "${generated_object_rclone_config}") != "careertrellis-object-recovery:careertrellis-object-recovery 600" ]]; then
        echo "The dedicated generated-object recovery rclone configuration is invalid." >&2
        exit 1
    fi
else
    install -o careertrellis-object-recovery -g careertrellis-object-recovery -m 0600 /dev/null "${generated_object_rclone_config}"
fi
install -d -o root -g root -m 0755 /etc/careertrellis
install -d -o caddy -g caddy -m 0750 /var/log/caddy
caddy_access_log=/var/log/caddy/careertrellis-staging-access-v7.log
if [[ -e ${caddy_access_log} || -L ${caddy_access_log} ]]; then
    if [[ -f ${caddy_access_log} && ! -L ${caddy_access_log} && \
          $(stat -c '%U:%G %a %s' "${caddy_access_log}") == "root:root 600 0" ]]; then
        chown --no-dereference --from=root:root caddy:caddy "${caddy_access_log}"
    fi
    if [[ ! -f ${caddy_access_log} || -L ${caddy_access_log} || \
          $(stat -c '%U:%G %a' "${caddy_access_log}") != "caddy:caddy 600" ]]; then
        echo "The Caddy access log is invalid." >&2
        exit 1
    fi
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
install -o root -g root -m 0644 "${repository_root}/deploy/caddy/Caddyfile.staging" /etc/caddy/Caddyfile
install -d -o root -g root -m 0755 /usr/local/libexec/careertrellis
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/maintenance-lock.sh" /usr/local/libexec/careertrellis/maintenance-lock.sh
install -o root -g root -m 0640 "${repository_root}/backend/pilot_users.py" /usr/local/libexec/careertrellis/pilot_users.py
install -o root -g root -m 0750 "${repository_root}/deploy/scripts/manage-pilot-user.sh" /usr/local/sbin/careertrellis-pilot-user
install -o root -g root -m 0640 "${repository_root}/backend/pilot_acceptance.py" /usr/local/libexec/careertrellis/pilot_acceptance.py
install -o root -g root -m 0640 "${repository_root}/backend/pilot_acceptance_receipts.py" /usr/local/libexec/careertrellis/pilot_acceptance_receipts.py
install -o root -g root -m 0750 "${repository_root}/deploy/scripts/manage-pilot-acceptance.sh" /usr/local/sbin/careertrellis-pilot-acceptance
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/deploy-release.sh" /usr/local/sbin/careertrellis-deploy
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/rollback-release.sh" /usr/local/sbin/careertrellis-rollback
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/hosted-authority.sh" /usr/local/sbin/careertrellis-authority
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/install-erasure-worker-credential.sh" /usr/local/sbin/careertrellis-install-erasure-worker-credential
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/finalize-postgres-r2-cutover.sh" /usr/local/sbin/careertrellis-finalize-cutover
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/mirror-generated-objects-onedrive.sh" /usr/local/sbin/careertrellis-mirror-generated-objects
[[ -f ${repository_root}/backend/generated_object_recovery.py && ! -L ${repository_root}/backend/generated_object_recovery.py && ! -L /usr/local/libexec/careertrellis/generated_object_recovery.py ]]
install -o root -g root -m 0644 "${repository_root}/backend/generated_object_recovery.py" /usr/local/libexec/careertrellis/generated_object_recovery.py
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/test-generated-object-restore.sh" /usr/local/sbin/careertrellis-test-generated-object-restore
for object_health_target in \
    /usr/local/libexec/careertrellis/backup_health.py \
    /usr/local/sbin/careertrellis-backup-health \
    /etc/systemd/system/careertrellis-generated-object-backup-health.service \
    /etc/systemd/system/careertrellis-generated-object-backup-health.timer \
    /etc/systemd/system/careertrellis-generated-object-backup-health-alert@.service; do
    if [[ -L ${object_health_target} ]]; then
        echo "An installed generated-object backup-health target must not be a symbolic link." >&2
        exit 1
    fi
done
install -o root -g root -m 0644 "${repository_root}/backend/backup_health.py" /usr/local/libexec/careertrellis/backup_health.py
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/manage-generated-object-backup-health.sh" /usr/local/sbin/careertrellis-backup-health
systemd-analyze verify \
    "${repository_root}/deploy/systemd/careertrellis-generated-object-backup-health.service" \
    "${repository_root}/deploy/systemd/careertrellis-generated-object-backup-health.timer" \
    "${repository_root}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service"
install -o root -g root -m 0644 "${repository_root}/deploy/systemd/careertrellis-generated-object-backup-health.service" /etc/systemd/system/careertrellis-generated-object-backup-health.service
install -o root -g root -m 0644 "${repository_root}/deploy/systemd/careertrellis-generated-object-backup-health.timer" /etc/systemd/system/careertrellis-generated-object-backup-health.timer
install -o root -g root -m 0644 "${repository_root}/deploy/systemd/careertrellis-generated-object-backup-health-alert@.service" /etc/systemd/system/careertrellis-generated-object-backup-health-alert@.service
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
if [[ -e /etc/careertrellis/generated-object-recovery.env || -L /etc/careertrellis/generated-object-recovery.env ]]; then
    if [[ ! -f /etc/careertrellis/generated-object-recovery.env || -L /etc/careertrellis/generated-object-recovery.env || $(stat -c '%U:%G %a' /etc/careertrellis/generated-object-recovery.env) != "root:root 600" ]]; then
        echo "The generated-object recovery environment is invalid." >&2
        exit 1
    fi
else
    install -o root -g root -m 0600 "${repository_root}/deploy/config/generated-object-recovery.environment.example" /etc/careertrellis/generated-object-recovery.env
fi
if [[ -e /etc/caddy/careertrellis-staging.env || -L /etc/caddy/careertrellis-staging.env ]]; then
    if [[ ! -f /etc/caddy/careertrellis-staging.env || -L /etc/caddy/careertrellis-staging.env || $(stat -c '%U:%G %a' /etc/caddy/careertrellis-staging.env) != "root:root 600" ]]; then
        echo "The Caddy environment is invalid." >&2
        exit 1
    fi
else
    install -o root -g root -m 0600 "${repository_root}/deploy/config/staging-caddy.environment.example" /etc/caddy/careertrellis-staging.env
fi
install -o root -g root -m 0755 "${repository_root}/deploy/scripts/manage-pilot-caddy-credential.sh" /usr/local/sbin/careertrellis-caddy-pilot-credential

systemctl daemon-reload
echo "Provisioning files installed. Replace every placeholder in:"
echo "  /etc/careertrellis/staging.env"
echo "  /etc/careertrellis/search-worker.env"
echo "  /etc/careertrellis/account-erasure-worker.env"
echo "  /etc/careertrellis/account-erasure-object-storage.env"
echo "  /etc/careertrellis/generated-object-recovery.env"
echo "  /etc/caddy/careertrellis-staging.env"
echo "Configure the dedicated OneDrive remote in /var/lib/careertrellis/generated-object-recovery/rclone.conf."
echo "Install the separate erasure-worker database credential, then validate Caddy and deploy an exact commit."
echo "Generated-object snapshot and restore commands remain manual; the local health timer was installed but not enabled or started."
echo "The account-erasure worker remains disabled and no services were started."
