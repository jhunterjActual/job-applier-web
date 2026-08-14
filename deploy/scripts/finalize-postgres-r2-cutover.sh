#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the final cutover coordinator as root." >&2
    exit 1
fi

coordinator_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
maintenance_lock_helper=${coordinator_dir}/maintenance-lock.sh
if [[ ! -f ${maintenance_lock_helper} ]]; then
    maintenance_lock_helper=/usr/local/libexec/careertrellis/maintenance-lock.sh
fi
if [[ ! -f ${maintenance_lock_helper} || -L ${maintenance_lock_helper} ]]; then
    echo "The reviewed maintenance-lock helper is unavailable." >&2
    exit 1
fi
# shellcheck source=maintenance-lock.sh
source "${maintenance_lock_helper}"

release_dir=${1:-}
migration_environment_file=${2:-}
object_environment_file=${3:-}
backup_confirmation=${4:-}
authority_confirmation=${5:-}
if [[ -z ${release_dir} || -z ${migration_environment_file} || -z ${object_environment_file} || ${backup_confirmation} != "I_HAVE_VERIFIED_QUIESCENT_SNAPSHOTS_AND_BACKUPS" || ${authority_confirmation} != "I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED" ]]; then
    echo "Usage: $0 RELEASE MIGRATION_ENV OBJECT_ENV I_HAVE_VERIFIED_QUIESCENT_SNAPSHOTS_AND_BACKUPS I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED" >&2
    exit 1
fi
if [[ ! -d ${release_dir} || -L ${release_dir} ]]; then
    echo "The final cutover requires an immutable reviewed release." >&2
    exit 1
fi
release_dir=$(realpath "${release_dir}")
release_revision=$(basename "${release_dir}")
if [[ $(dirname "${release_dir}") != "/opt/careertrellis/staging/releases" || ! ${release_revision} =~ ^[0-9a-f]{40}$ ]]; then
    echo "The final cutover requires an immutable reviewed release." >&2
    exit 1
fi
release_script_dir=${release_dir}/deploy/scripts
capability_file=${release_dir}/deploy/hosted-authority.compatibility
if [[ ! -f ${capability_file} || -L ${capability_file} || -x ${capability_file} ]] || \
   [[ $(grep -Fxc "postgres-r2-authoritative-v1" "${capability_file}" 2>/dev/null || true) -ne 1 ]] || \
   [[ $(wc -l < "${capability_file}") -ne 1 ]] || \
   ! grep -Fx "HOSTED_REPOSITORY_CUTOVER_COMPLETE = True" "${release_dir}/backend/data_store.py" >/dev/null || \
   [[ ! -x ${release_dir}/backend/venv/bin/python ]]; then
    echo "The exact release is not approved for PostgreSQL/R2 authority." >&2
    exit 1
fi
required_scripts=(
    maintenance-lock.sh
    migrate-postgres.sh
    apply-postgres-runtime-grants.sh
    preflight-sqlite-postgres-import.sh
    import-sqlite-postgres.sh
    hosted-authority.sh
    deploy-release.sh
)
for required_script in "${required_scripts[@]}"; do
    if [[ ! -f ${release_script_dir}/${required_script} || -L ${release_script_dir}/${required_script} ]]; then
        echo "The exact release is missing a reviewed final-cutover component." >&2
        exit 1
    fi
done
for environment_file in "${migration_environment_file}" "${object_environment_file}"; do
    if [[ ! -f ${environment_file} || -L ${environment_file} || $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
        echo "Every final-cutover environment file must be regular root:root mode 0600." >&2
        exit 1
    fi
done
migration_environment_file=$(realpath "${migration_environment_file}")
object_environment_file=$(realpath "${object_environment_file}")
if [[ ${migration_environment_file} != "/etc/careertrellis/postgres-migration.env" || ${object_environment_file} != "/etc/careertrellis/object-storage.env" ]]; then
    echo "The final cutover requires the reviewed permanent target environments." >&2
    exit 1
fi
runtime_database_environment_file=/etc/careertrellis/postgres-runtime.env
if [[ ! -f ${runtime_database_environment_file} || -L ${runtime_database_environment_file} || $(stat -c '%U:%G %a' "${runtime_database_environment_file}") != "root:root 600" ]]; then
    echo "Every final-cutover environment file must be regular root:root mode 0600." >&2
    exit 1
fi
read_exact_setting() {
    local environment_file=$1
    local setting_name=$2
    local setting_value
    if [[ $(grep -Ec "^${setting_name}=" "${environment_file}") -ne 1 ]]; then
        return 1
    fi
    setting_value=$(sed -n "s/^${setting_name}=//p" "${environment_file}")
    [[ -n ${setting_value} ]] || return 1
    printf '%s' "${setting_value}"
}
if ! migration_database_url=$(read_exact_setting "${migration_environment_file}" CAREERTRELLIS_MIGRATION_DATABASE_URL) || \
   ! runtime_database_url=$(read_exact_setting "${runtime_database_environment_file}" CAREERTRELLIS_DATABASE_URL) || \
   ! CAREERTRELLIS_CUTOVER_MIGRATION_DATABASE_URL="${migration_database_url}" \
        CAREERTRELLIS_CUTOVER_RUNTIME_DATABASE_URL="${runtime_database_url}" \
        "${release_dir}/backend/venv/bin/python" -c '
import os
from urllib.parse import unquote, urlsplit

def identity(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError
    database = unquote(parsed.path.lstrip("/"))
    if not parsed.hostname or not database:
        raise ValueError
    return parsed.hostname.casefold(), parsed.port or 5432, database

try:
    migration = identity(os.environ["CAREERTRELLIS_CUTOVER_MIGRATION_DATABASE_URL"])
    runtime = identity(os.environ["CAREERTRELLIS_CUTOVER_RUNTIME_DATABASE_URL"])
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
if migration != runtime:
    raise SystemExit(1)
'; then
    echo "The migration and runtime database targets do not match." >&2
    exit 1
fi
require_exact_environment_selection() {
    local environment_file=$1
    local setting_name=$2
    local expected_line=$3
    if [[ ! -f ${environment_file} || -L ${environment_file} || $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]] || \
       [[ $(grep -Ec "^${setting_name}=" "${environment_file}") -ne 1 ]] || \
       ! grep -Fx "${expected_line}" "${environment_file}" >/dev/null; then
        echo "The final-cutover runtime selections are incomplete." >&2
        return 1
    fi
}
require_exact_environment_selection \
    /etc/careertrellis/staging.env \
    CAREERTRELLIS_DATA_STORE \
    CAREERTRELLIS_DATA_STORE=postgres
require_exact_environment_selection \
    /etc/careertrellis/search-worker.env \
    CAREERTRELLIS_DATA_STORE \
    CAREERTRELLIS_DATA_STORE=postgres
require_exact_environment_selection \
    /etc/careertrellis/account-erasure-worker.env \
    CAREERTRELLIS_DATA_STORE \
    CAREERTRELLIS_DATA_STORE=postgres
require_exact_environment_selection \
    /etc/careertrellis/object-storage.env \
    CAREERTRELLIS_OBJECT_STORE \
    CAREERTRELLIS_OBJECT_STORE=r2_encrypted

exec 10>/run/lock/careertrellis-maintenance.lock
export CAREERTRELLIS_MAINTENANCE_LOCK_FD=10
acquire_careertrellis_maintenance_lock

phase=validation
marked_authoritative=false
completed=false
erasure_worker_was_active=false
maintenance_started=false

cutover_failure() {
    local exit_code=${1:-1}
    trap - ERR INT TERM
    if [[ ${completed} != true ]]; then
        if [[ ${maintenance_started} == true ]]; then
            systemctl stop careertrellis-erasure-worker.service careertrellis-worker.service careertrellis-web.service >/dev/null 2>&1 || true
        fi
        if [[ ${marked_authoritative} == true ]]; then
            echo "Final cutover stopped after the authority boundary; keep application services stopped and apply a reviewed forward correction." >&2
        else
            echo "Final cutover stopped before the authority boundary during ${phase}; keep application services stopped and reconcile before any retry." >&2
        fi
    fi
    exit "${exit_code}"
}
trap 'cutover_failure $?' ERR
trap 'cutover_failure 130' INT
trap 'cutover_failure 143' TERM

assert_services_inactive() {
    local service
    for service in careertrellis-web.service careertrellis-worker.service careertrellis-erasure-worker.service; do
        if systemctl is-active --quiet "${service}"; then
            echo "The web, search-worker, and erasure-worker services must remain stopped during final cutover." >&2
            return 1
        fi
    done
}

if [[ $("${release_script_dir}/hosted-authority.sh" status) != "sqlite-pre-cutover" ]]; then
    echo "Final cutover can begin only from SQLite pre-cutover authority." >&2
    exit 1
fi
if systemctl is-active --quiet careertrellis-erasure-worker.service; then
    erasure_worker_was_active=true
fi

phase=service-stop
maintenance_started=true
systemctl stop careertrellis-erasure-worker.service careertrellis-worker.service careertrellis-web.service
assert_services_inactive

phase=migration
"${release_script_dir}/migrate-postgres.sh" "${release_dir}" "${migration_environment_file}"

phase=runtime-grants
"${release_script_dir}/apply-postgres-runtime-grants.sh" "${release_dir}" "${migration_environment_file}"

phase=preflight
"${release_script_dir}/preflight-sqlite-postgres-import.sh" "${release_dir}"

phase=import-and-reconciliation
assert_services_inactive
"${release_script_dir}/import-sqlite-postgres.sh" \
    "${release_dir}" \
    "${migration_environment_file}" \
    "${object_environment_file}"

phase=authority-mark
assert_services_inactive
"${release_script_dir}/hosted-authority.sh" \
    mark-postgres-r2-authoritative \
    "${release_dir}" \
    "${authority_confirmation}"
marked_authoritative=true

phase=activation
"${release_script_dir}/deploy-release.sh" "${release_revision}"
if [[ ${erasure_worker_was_active} == true ]]; then
    systemctl restart careertrellis-erasure-worker.service
fi
completed=true
trap - ERR INT TERM
echo "CareerTrellis final PostgreSQL/R2 cutover completed for ${release_revision}."
