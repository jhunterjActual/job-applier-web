#!/bin/bash
set -Eeuo pipefail
set +x
umask 077
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset SYSTEMD_HOST SYSTEMD_MACHINE SYSTEMD_LOG_TARGET SYSTEMD_LOG_LEVEL SYSTEMD_PAGER SYSTEMD_PAGERSECURE
unset http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this pilot acceptance helper as root." >&2
    exit 1
fi

operation=${1:-}
if [[ $# -ne 1 || ! ${operation} =~ ^(prepare|verify-active|verify-disabled|verify-recovered|inspect|cleanup|record-browser)$ ]]; then
    echo "Usage: $0 prepare|verify-active|verify-disabled|verify-recovered|inspect|cleanup|record-browser" >&2
    exit 1
fi
if [[ ! -t 0 || ! -t 1 || ! -t 2 ]]; then
    echo "Pilot acceptance operations require an interactive terminal." >&2
    exit 1
fi

readonly installed_helper=/usr/local/sbin/careertrellis-pilot-acceptance
readonly installed_runner=/usr/local/libexec/careertrellis/pilot_acceptance.py
readonly installed_receipts=/usr/local/libexec/careertrellis/pilot_acceptance_receipts.py
readonly current_link=/opt/careertrellis/staging/current
readonly release_root=/opt/careertrellis/staging/releases
readonly receipt_parent=/var/backups/careertrellis
readonly receipt_root=${receipt_parent}/pilot-auth-acceptance
readonly authority_helper=/usr/local/sbin/careertrellis-authority
readonly maintenance_lock_helper=/usr/local/libexec/careertrellis/maintenance-lock.sh
readonly pending_cutover=/etc/careertrellis/authority/pending-cutover
readonly recovery_evidence=/etc/careertrellis/authority/post-import-recovery
readonly activation_lease=/run/careertrellis-hosted-activation.lease
readonly operation_lock=/run/lock/careertrellis-pilot-auth-acceptance.lock
readonly script_path=$(realpath "${BASH_SOURCE[0]}")

for command_name in basename cut dirname env flock getent grep id readlink realpath sha256sum stat systemctl curl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "A required host command is unavailable." >&2
        exit 1
    fi
done
if [[ ${script_path} != "${installed_helper}" || -L ${installed_helper} || \
      $(stat -c '%U:%G %a' "${installed_helper}") != "root:root 750" || \
      ! -f ${installed_runner} || -L ${installed_runner} || \
      $(stat -c '%U:%G %a' "${installed_runner}") != "root:root 640" || \
      ! -f ${installed_receipts} || -L ${installed_receipts} || \
      $(stat -c '%U:%G %a' "${installed_receipts}") != "root:root 640" ]]; then
    echo "The installed pilot acceptance boundary is invalid." >&2
    exit 1
fi
if [[ ! -f ${maintenance_lock_helper} || -L ${maintenance_lock_helper} || \
      $(stat -c '%U:%G %a' "${maintenance_lock_helper}") != "root:root 755" ]]; then
    echo "The reviewed maintenance-lock helper is unavailable." >&2
    exit 1
fi

# Lock before resolving current so deployment cannot change the exact release
# during binding, live-state validation, or receipt publication.
# shellcheck source=maintenance-lock.sh
source "${maintenance_lock_helper}"
acquire_careertrellis_maintenance_lock
exec 9>"${operation_lock}"
if ! flock -n 9; then
    echo "Another pilot acceptance operation is running." >&2
    exit 1
fi

if [[ ! -L ${current_link} ]]; then
    echo "The active immutable release is unavailable." >&2
    exit 1
fi
current_release=$(readlink -f "${current_link}")
current_revision=$(basename "${current_release}")
release_helper=${current_release}/deploy/scripts/manage-pilot-acceptance.sh
release_runner=${current_release}/backend/pilot_acceptance.py
release_receipts=${current_release}/backend/pilot_acceptance_receipts.py
python_command=${current_release}/backend/venv/bin/python
if [[ $(dirname "${current_release}") != "${release_root}" || \
      ! ${current_revision} =~ ^[0-9a-f]{40}$ || \
      ! -d ${current_release} || \
      ! -f ${release_helper} || -L ${release_helper} || \
      $(stat -c '%U:%G %a' "${release_helper}") != "root:careertrellis 750" || \
      ! -f ${release_runner} || -L ${release_runner} || \
      $(stat -c '%U:%G %a' "${release_runner}") != "root:careertrellis 640" || \
      ! -f ${release_receipts} || -L ${release_receipts} || \
      $(stat -c '%U:%G %a' "${release_receipts}") != "root:careertrellis 640" || \
      ! -x ${python_command} ]]; then
    echo "The active release does not provide the reviewed pilot acceptance boundary." >&2
    exit 1
fi
if [[ $(sha256sum "${installed_helper}" | cut -d' ' -f1) != \
      $(sha256sum "${release_helper}" | cut -d' ' -f1) || \
      $(sha256sum "${installed_runner}" | cut -d' ' -f1) != \
      $(sha256sum "${release_runner}" | cut -d' ' -f1) || \
      $(sha256sum "${installed_receipts}" | cut -d' ' -f1) != \
      $(sha256sum "${release_receipts}" | cut -d' ' -f1) ]]; then
    echo "The installed pilot acceptance boundary does not match the active release." >&2
    exit 1
fi

if [[ ! -x ${authority_helper} || -L ${authority_helper} || \
      $(stat -c '%U:%G %a' "${authority_helper}") != "root:root 755" || \
      $(env -i PATH="${PATH}" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
          "${authority_helper}" status) != "postgres-r2-authoritative" ]] || \
      ! env -i PATH="${PATH}" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
          "${authority_helper}" assert-release-startable "${current_release}" >/dev/null; then
    echo "Hosted authority does not permit pilot acceptance operations." >&2
    exit 1
fi
for evidence_file in "${pending_cutover}" "${recovery_evidence}"; do
    if [[ ! -f ${evidence_file} || -L ${evidence_file} || \
          $(stat -c '%U:%G %a' "${evidence_file}") != "root:root 600" ]]; then
        echo "Hosted activation evidence is unavailable." >&2
        exit 1
    fi
done
if ! grep -Fxq 'phase=activated' "${pending_cutover}" || \
   [[ -e ${activation_lease} || -L ${activation_lease} ]]; then
    echo "Hosted activation is not in a terminal accepted state." >&2
    exit 1
fi
if ! env -i PATH="${PATH}" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        CAREERTRELLIS_DATA_STORE=postgres CAREERTRELLIS_OBJECT_STORE=r2_encrypted \
        "${authority_helper}" check-web-runtime >/dev/null || \
   ! env -i PATH="${PATH}" LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        CAREERTRELLIS_DATA_STORE=postgres CAREERTRELLIS_OBJECT_STORE=r2_encrypted \
        "${authority_helper}" check-worker-runtime >/dev/null; then
    echo "Hosted runtime authority validation failed." >&2
    exit 1
fi

for unit in caddy.service careertrellis-web.service careertrellis-worker.service careertrellis-erasure-worker.service; do
    if ! systemctl is-active --quiet "${unit}" || \
       ! systemctl is-enabled --quiet "${unit}" || \
       [[ $(systemctl show --property=NeedDaemonReload --value "${unit}") != "no" ]]; then
        echo "Every pilot acceptance unit must be active, enabled, and fully loaded." >&2
        exit 1
    fi
done

declare -A unit_sources=(
    [careertrellis-web.service]="${current_release}/deploy/systemd/careertrellis-web.service"
    [careertrellis-worker.service]="${current_release}/deploy/systemd/careertrellis-worker.service"
    [careertrellis-erasure-worker.service]="${current_release}/deploy/systemd/careertrellis-erasure-worker.service"
)
search_worker_main_pid=""
erasure_worker_main_pid=""
for unit in "${!unit_sources[@]}"; do
    installed_unit=/etc/systemd/system/${unit}
    if [[ ! -f ${installed_unit} || -L ${installed_unit} || \
          $(stat -c '%U:%G %a' "${installed_unit}") != "root:root 644" || \
          $(sha256sum "${installed_unit}" | cut -d' ' -f1) != \
          $(sha256sum "${unit_sources[${unit}]}" | cut -d' ' -f1) ]]; then
        echo "An installed pilot acceptance unit does not match the active release." >&2
        exit 1
    fi
    main_pid=$(systemctl show --property=MainPID --value "${unit}")
    if [[ ! ${main_pid} =~ ^[1-9][0-9]*$ || \
          $(readlink -f "/proc/${main_pid}/cwd" 2>/dev/null || true) != "${current_release}/backend" ]]; then
        echo "An application unit is not running from the active immutable release." >&2
        exit 1
    fi
    case "${unit}" in
        careertrellis-worker.service)
            if [[ $(systemctl show --property=User --value "${unit}") != "careertrellis-worker" || \
                  $(systemctl show --property=Group --value "${unit}") != "careertrellis" || \
                  $(systemctl show --property=UMask --value "${unit}") != "0077" ]]; then
                echo "The search worker heartbeat metadata contract is unavailable." >&2
                exit 1
            fi
            search_worker_main_pid=${main_pid}
            ;;
        careertrellis-erasure-worker.service)
            if [[ $(systemctl show --property=User --value "${unit}") != "careertrellis-erasure-worker" || \
                  $(systemctl show --property=Group --value "${unit}") != "careertrellis" || \
                  $(systemctl show --property=UMask --value "${unit}") != "0077" ]]; then
                echo "The erasure worker heartbeat metadata contract is unavailable." >&2
                exit 1
            fi
            erasure_worker_main_pid=${main_pid}
            ;;
    esac
done
if [[ ! ${search_worker_main_pid} =~ ^[1-9][0-9]*$ || \
      ! ${erasure_worker_main_pid} =~ ^[1-9][0-9]*$ ]]; then
    echo "The worker process binding is unavailable." >&2
    exit 1
fi
search_worker_uid=$(id -u careertrellis-worker)
erasure_worker_uid=$(id -u careertrellis-erasure-worker)
worker_group_gid=$(getent group careertrellis | cut -d: -f3)
search_worker_gid=${worker_group_gid}
erasure_worker_gid=${worker_group_gid}
for numeric_identity in \
    "${search_worker_uid}" "${search_worker_gid}" \
    "${erasure_worker_uid}" "${erasure_worker_gid}"; do
    if [[ ! ${numeric_identity} =~ ^[0-9]+$ ]]; then
        echo "The worker heartbeat ownership binding is unavailable." >&2
        exit 1
    fi
done
if [[ ! -f /etc/caddy/Caddyfile || -L /etc/caddy/Caddyfile || \
      $(stat -c '%U:%G %a' /etc/caddy/Caddyfile) != "root:root 644" || \
      $(sha256sum /etc/caddy/Caddyfile | cut -d' ' -f1) != \
      $(sha256sum "${current_release}/deploy/caddy/Caddyfile.staging" | cut -d' ' -f1) ]]; then
    echo "The active Caddy configuration does not match the immutable release." >&2
    exit 1
fi

expected_build=$("${python_command}" -I -B -c \
    'import runpy,sys; value=runpy.run_path(sys.argv[1]).get("APP_BUILD"); print(value) if isinstance(value,str) else sys.exit(1)' \
    "${current_release}/backend/version.py")
readiness=$(curl --fail --silent --show-error --max-time 2 http://127.0.0.1:8001/health/ready)
if ! printf '%s' "${readiness}" | CAREERTRELLIS_EXPECTED_BUILD="${expected_build}" \
        "${python_command}" -I -B -c \
        'import json,os,sys; value=json.load(sys.stdin); raise SystemExit(0 if value == {"status":"ready","build":os.environ["CAREERTRELLIS_EXPECTED_BUILD"]} else 1)' \
        >/dev/null 2>&1; then
    echo "The active release readiness binding failed." >&2
    exit 1
fi
unset readiness expected_build

if [[ ! -d ${receipt_parent} || -L ${receipt_parent} || \
      $(realpath "${receipt_parent}") != "${receipt_parent}" || \
      $(stat -c '%U:%G %a' "${receipt_parent}") != "root:root 700" ]]; then
    echo "The protected CareerTrellis receipt parent is invalid." >&2
    exit 1
fi
if [[ ! -d ${receipt_root} || -L ${receipt_root} || \
      $(realpath "${receipt_root}") != "${receipt_root}" || \
      $(stat -c '%U:%G %a' "${receipt_root}") != "root:root 700" ]]; then
    echo "The pilot acceptance receipt root is invalid." >&2
    exit 1
fi

exec env -i \
    PATH="${PATH}" \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONNOUSERSITE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CAREERTRELLIS_PILOT_ACCEPTANCE_RELEASE="${current_revision}" \
    CAREERTRELLIS_PILOT_ACCEPTANCE_SEARCH_WORKER_PID="${search_worker_main_pid}" \
    CAREERTRELLIS_PILOT_ACCEPTANCE_ERASURE_WORKER_PID="${erasure_worker_main_pid}" \
    CAREERTRELLIS_PILOT_ACCEPTANCE_SEARCH_HEARTBEAT_UID="${search_worker_uid}" \
    CAREERTRELLIS_PILOT_ACCEPTANCE_SEARCH_HEARTBEAT_GID="${search_worker_gid}" \
    CAREERTRELLIS_PILOT_ACCEPTANCE_SEARCH_HEARTBEAT_MODE=600 \
    CAREERTRELLIS_PILOT_ACCEPTANCE_ERASURE_HEARTBEAT_UID="${erasure_worker_uid}" \
    CAREERTRELLIS_PILOT_ACCEPTANCE_ERASURE_HEARTBEAT_GID="${erasure_worker_gid}" \
    CAREERTRELLIS_PILOT_ACCEPTANCE_ERASURE_HEARTBEAT_MODE=600 \
    "${python_command}" -s -B "${release_runner}" "${operation}"
