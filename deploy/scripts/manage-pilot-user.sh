#!/bin/bash
set -Eeuo pipefail
set +x
umask 077
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this pilot user helper as root." >&2
    exit 1
fi

operation=${1:-}
if [[ $# -ne 1 || ! ${operation} =~ ^(seed|inspect|disable|reinvite)$ ]]; then
    echo "Usage: $0 seed|inspect|disable|reinvite" >&2
    exit 1
fi
if [[ ! -t 0 || ! -t 1 || ! -t 2 ]]; then
    echo "Pilot user operations require an interactive terminal." >&2
    exit 1
fi

readonly installed_helper=/usr/local/sbin/careertrellis-pilot-user
readonly installed_backend=/usr/local/libexec/careertrellis/pilot_users.py
readonly current_link=/opt/careertrellis/staging/current
readonly release_root=/opt/careertrellis/staging/releases
readonly authority_helper=/usr/local/sbin/careertrellis-authority
readonly maintenance_lock_helper=/usr/local/libexec/careertrellis/maintenance-lock.sh
readonly migration_environment=/etc/careertrellis/postgres-migration.env
readonly staging_environment=/etc/careertrellis/staging.env
readonly operation_lock=/run/lock/careertrellis-pilot-user.lock
readonly script_path=$(realpath "${BASH_SOURCE[0]}")

for command_name in basename cut dirname env flock readlink realpath sha256sum stat; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "A required host command is unavailable." >&2
        exit 1
    fi
done
if [[ ${script_path} != "${installed_helper}" || -L ${installed_helper} || \
      $(stat -c '%U:%G %a' "${installed_helper}") != "root:root 750" || \
      ! -f ${installed_backend} || -L ${installed_backend} || \
      $(stat -c '%U:%G %a' "${installed_backend}") != "root:root 640" ]]; then
    echo "The installed pilot user boundary is invalid." >&2
    exit 1
fi
if [[ ! -f ${maintenance_lock_helper} || -L ${maintenance_lock_helper} || \
      $(stat -c '%U:%G %a' "${maintenance_lock_helper}") != "root:root 755" ]]; then
    echo "The reviewed maintenance-lock helper is unavailable." >&2
    exit 1
fi

# Lock before resolving current so a deployment cannot change the exact release
# between the binding checks and the database transaction.
# shellcheck source=maintenance-lock.sh
source "${maintenance_lock_helper}"
acquire_careertrellis_maintenance_lock
exec 9>"${operation_lock}"
if ! flock -n 9; then
    echo "Another pilot user operation is running." >&2
    exit 1
fi

if [[ ! -L ${current_link} ]]; then
    echo "The active immutable release is unavailable." >&2
    exit 1
fi
current_release=$(readlink -f "${current_link}")
current_revision=$(basename "${current_release}")
if [[ $(dirname "${current_release}") != "${release_root}" || \
      ! ${current_revision} =~ ^[0-9a-f]{40}$ || \
      ! -d ${current_release} || -L ${current_release}/deploy/scripts/manage-pilot-user.sh || \
      ! -f ${current_release}/deploy/scripts/manage-pilot-user.sh || \
      $(stat -c '%U:%G %a' "${current_release}/deploy/scripts/manage-pilot-user.sh") != "root:careertrellis 750" || \
      -L ${current_release}/backend/pilot_users.py || \
      ! -f ${current_release}/backend/pilot_users.py || \
      $(stat -c '%U:%G %a' "${current_release}/backend/pilot_users.py") != "root:careertrellis 640" || \
      ! -x ${current_release}/backend/venv/bin/python ]]; then
    echo "The active release does not provide the reviewed pilot user boundary." >&2
    exit 1
fi
if [[ $(sha256sum "${installed_helper}" | cut -d' ' -f1) != \
      $(sha256sum "${current_release}/deploy/scripts/manage-pilot-user.sh" | cut -d' ' -f1) || \
      $(sha256sum "${installed_backend}" | cut -d' ' -f1) != \
      $(sha256sum "${current_release}/backend/pilot_users.py" | cut -d' ' -f1) ]]; then
    echo "The installed pilot user boundary does not match the active release." >&2
    exit 1
fi
for protected_file in "${migration_environment}" "${staging_environment}"; do
    if [[ ! -f ${protected_file} || -L ${protected_file} || \
          $(stat -c '%U:%G %a' "${protected_file}") != "root:root 600" ]]; then
        echo "A required root-owned environment is invalid." >&2
        exit 1
    fi
done
if [[ ! -x ${authority_helper} || -L ${authority_helper} || \
      $(stat -c '%U:%G %a' "${authority_helper}") != "root:root 755" || \
      $("${authority_helper}" status) != "postgres-r2-authoritative" ]] || \
      ! "${authority_helper}" assert-release-startable "${current_release}" >/dev/null; then
    echo "Hosted authority does not permit pilot user operations." >&2
    exit 1
fi
exec env -i \
    PATH="${PATH}" \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    "${current_release}/backend/venv/bin/python" \
    "${current_release}/backend/pilot_users.py" \
    "${operation}"
