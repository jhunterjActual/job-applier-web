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

cutover_action=${1:-}
release_dir=${2:-}
migration_environment_file=${3:-}
object_environment_file=${4:-}
evidence_confirmation=${5:-}
authority_confirmation=${6:-}
recovery_confirmation=${7:-}
case "${cutover_action}" in
    import-and-pause)
        if [[ $# -ne 5 || ${evidence_confirmation} != "I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP" ]]; then
            echo "Usage: $0 import-and-pause RELEASE MIGRATION_ENV OBJECT_ENV I_HAVE_VERIFIED_QUIESCENT_SOURCE_AND_PRE_IMPORT_BACKUP" >&2
            exit 1
        fi
        ;;
    resume-preparation)
        if [[ $# -ne 5 || ${evidence_confirmation} != "I_HAVE_REVIEWED_PRE_IMPORT_PREPARATION_FAILURE" ]]; then
            echo "Usage: $0 resume-preparation RELEASE MIGRATION_ENV OBJECT_ENV I_HAVE_REVIEWED_PRE_IMPORT_PREPARATION_FAILURE" >&2
            exit 1
        fi
        ;;
    reconcile-pending)
        if [[ $# -ne 5 || ${evidence_confirmation} != "I_HAVE_REVIEWED_AMBIGUOUS_IMPORT" ]]; then
            echo "Usage: $0 reconcile-pending RELEASE MIGRATION_ENV OBJECT_ENV I_HAVE_REVIEWED_AMBIGUOUS_IMPORT" >&2
            exit 1
        fi
        ;;
    record-recovery-evidence)
        if [[ $# -ne 7 || ${recovery_confirmation} != "I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES" ]]; then
            echo "Usage: $0 record-recovery-evidence RELEASE MIGRATION_ENV OBJECT_ENV POSTGRES_BACKUP_LABEL GENERATED_OBJECT_MANIFEST_SHA256 I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES" >&2
            exit 1
        fi
        ;;
    mark-and-activate)
        if [[ $# -ne 6 || ${evidence_confirmation} != "I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES" || ${authority_confirmation} != "I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED" ]]; then
            echo "Usage: $0 mark-and-activate RELEASE MIGRATION_ENV OBJECT_ENV I_HAVE_VERIFIED_POST_IMPORT_BACKUPS_AND_RESTORES I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED" >&2
            exit 1
        fi
        ;;
    resume-hosted-activation)
        if [[ $# -ne 5 || ${evidence_confirmation} != "I_HAVE_REVIEWED_HOSTED_FORWARD_RECOVERY" ]]; then
            echo "Usage: $0 resume-hosted-activation RELEASE MIGRATION_ENV OBJECT_ENV I_HAVE_REVIEWED_HOSTED_FORWARD_RECOVERY" >&2
            exit 1
        fi
        ;;
    *)
        echo "Usage: $0 {import-and-pause|resume-preparation|reconcile-pending|record-recovery-evidence|mark-and-activate|resume-hosted-activation} RELEASE MIGRATION_ENV OBJECT_ENV ARGUMENTS..." >&2
        exit 1
        ;;
esac
if [[ -z ${release_dir} || -z ${migration_environment_file} || -z ${object_environment_file} ]]; then
    echo "The final cutover requires exact release and environment paths." >&2
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
cutover_file=${release_dir}/backend/data_store.py
if [[ ! -f ${capability_file} || -L ${capability_file} || -x ${capability_file} ]] || \
   [[ $(grep -Fxc "postgres-r2-authoritative-v1" "${capability_file}" 2>/dev/null || true) -ne 1 ]] || \
   [[ $(wc -l < "${capability_file}") -ne 1 ]] || \
   [[ ! -f ${cutover_file} || -L ${cutover_file} ]] || \
   [[ $(grep -Ec '^HOSTED_REPOSITORY_CUTOVER_COMPLETE = (True|False)$' "${cutover_file}") -ne 1 ]] || \
   [[ $(grep -Fxc 'HOSTED_REPOSITORY_CUTOVER_COMPLETE = True' "${cutover_file}") -ne 1 ]] || \
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
    reconcile-sqlite-postgres-import.sh
    hosted-authority.sh
    deploy-release.sh
)
for required_script in "${required_scripts[@]}"; do
    if [[ ! -f ${release_script_dir}/${required_script} || \
          -L ${release_script_dir}/${required_script} ]]; then
        echo "The exact release is missing a reviewed final-cutover component." >&2
        exit 1
    fi
done
required_executable_scripts=(
    migrate-postgres.sh
    apply-postgres-runtime-grants.sh
    preflight-sqlite-postgres-import.sh
    import-sqlite-postgres.sh
    reconcile-sqlite-postgres-import.sh
    hosted-authority.sh
    deploy-release.sh
)
for required_executable_script in "${required_executable_scripts[@]}"; do
    if [[ ! -x ${release_script_dir}/${required_executable_script} ]]; then
        echo "The exact release is missing a reviewed final-cutover component." >&2
        exit 1
    fi
done

verify_installed_start_guards() {
    local require_loaded=${1:-false}
    local installed_authority=/usr/local/sbin/careertrellis-authority
    local service_name source_guard installed_guard loaded_guard found_loaded
    if [[ ! -f ${installed_authority} || -L ${installed_authority} || $(stat -c '%U:%G %a' "${installed_authority}") != "root:root 755" ]] || \
       ! cmp -s -- "${release_script_dir}/hosted-authority.sh" "${installed_authority}"; then
        echo "Install the exact release's authority start guards before final cutover." >&2
        return 1
    fi
    for service_name in careertrellis-web careertrellis-worker careertrellis-erasure-worker; do
        source_guard=${release_dir}/deploy/systemd/${service_name}.service.d/hosted-authority.conf
        installed_guard=/etc/systemd/system/${service_name}.service.d/hosted-authority.conf
        if [[ ! -f ${source_guard} || -L ${source_guard} || ! -f ${installed_guard} || -L ${installed_guard} ]] || \
           [[ $(stat -c '%U:%G %a' "${installed_guard}") != "root:root 644" ]] || \
           ! cmp -s -- "${source_guard}" "${installed_guard}"; then
            echo "Install the exact release's authority start guards before final cutover." >&2
            return 1
        fi
        if [[ ${require_loaded} == true ]]; then
            found_loaded=false
            while IFS= read -r loaded_guard; do
                if [[ ${loaded_guard} == "${installed_guard}" ]]; then
                    found_loaded=true
                fi
            done < <(systemctl show --property=DropInPaths --value "${service_name}.service" | tr ' ' '\n')
            if [[ ${found_loaded} != true ]]; then
                echo "Install the exact release's authority start guards before final cutover." >&2
                return 1
            fi
        fi
    done
}
verify_installed_start_guards false

exec 10>/run/lock/careertrellis-maintenance.lock
export CAREERTRELLIS_MAINTENANCE_LOCK_FD=10
acquire_careertrellis_maintenance_lock

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
provider_environment_file=/etc/careertrellis/provider-secrets.env
pending_cutover_file=/etc/careertrellis/authority/pending-cutover
recovery_evidence_file=/etc/careertrellis/authority/post-import-recovery
activation_lease_file=/run/careertrellis-hosted-activation.lease
if [[ ! -f ${runtime_database_environment_file} || -L ${runtime_database_environment_file} || $(stat -c '%U:%G %a' "${runtime_database_environment_file}") != "root:root 600" ]]; then
    echo "Every final-cutover environment file must be regular root:root mode 0600." >&2
    exit 1
fi
if [[ ! -f ${provider_environment_file} || -L ${provider_environment_file} || $(stat -c '%U:%G %a' "${provider_environment_file}") != "root:root 600" ]]; then
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
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import make_url


def normalized_hosts(value):
    items = value.split(",")
    if not items or any(not item for item in items):
        raise ValueError
    return tuple(
        item if item.startswith("/") else item.casefold().rstrip(".")
        for item in items
    )

def normalized_list(value):
    items = value.split(",")
    if not items or any(not item for item in items):
        raise ValueError
    return tuple(items)

def identity(value):
    url = make_url(value)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError
    native_url = url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    parameters = conninfo_to_dict(native_url)
    if parameters.get("service"):
        raise ValueError
    host = parameters.get("host", "")
    database = parameters.get("dbname", "")
    if not host or not database:
        raise ValueError
    ports = tuple(
        int(item or "5432")
        for item in parameters.get("port", "5432").split(",")
    )
    return (
        normalized_hosts(host),
        normalized_list(parameters["hostaddr"])
        if parameters.get("hostaddr")
        else (),
        ports,
        database,
        parameters.get("target_session_attrs", "any"),
        parameters.get("load_balance_hosts", "disable"),
    )

try:
    migration = identity(os.environ["CAREERTRELLIS_CUTOVER_MIGRATION_DATABASE_URL"])
    runtime = identity(os.environ["CAREERTRELLIS_CUTOVER_RUNTIME_DATABASE_URL"])
except Exception:
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

read_exact_boolean() {
    local environment_file=$1
    local setting_name=$2
    local setting_value
    if [[ $(grep -Ec "^${setting_name}=" "${environment_file}") -ne 1 ]] || \
       [[ $(grep -Ec "^${setting_name}=(true|false)$" "${environment_file}") -ne 1 ]]; then
        echo "The final-cutover producer and consumer switches are incomplete." >&2
        return 1
    fi
    setting_value=$(sed -n "s/^${setting_name}=//p" "${environment_file}")
    printf '%s' "${setting_value}"
}
web_search_worker_enabled=$(read_exact_boolean /etc/careertrellis/staging.env CAREERTRELLIS_SEARCH_WORKER_ENABLED)
search_worker_enabled=$(read_exact_boolean /etc/careertrellis/search-worker.env CAREERTRELLIS_SEARCH_WORKER_ENABLED)
web_erasure_worker_enabled=$(read_exact_boolean /etc/careertrellis/staging.env CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED)
erasure_worker_enabled=$(read_exact_boolean /etc/careertrellis/account-erasure-worker.env CAREERTRELLIS_ACCOUNT_ERASURE_WORKER_ENABLED)
if [[ ${web_search_worker_enabled} != "${search_worker_enabled}" || ${web_erasure_worker_enabled} != "${erasure_worker_enabled}" ]]; then
    echo "The final-cutover producer and consumer switches do not match." >&2
    exit 1
fi
if [[ ${search_worker_enabled} != false || ${erasure_worker_enabled} != false ]]; then
    echo "Final cutover requires both asynchronous workers to remain disabled until their separate post-cutover activation." >&2
    exit 1
fi

migration_environment_sha256=$(sha256sum "${migration_environment_file}" | awk '{print $1}')
object_environment_sha256=$(sha256sum "${object_environment_file}" | awk '{print $1}')
provider_environment_sha256=$(sha256sum "${provider_environment_file}" | awk '{print $1}')
if [[ ! ${migration_environment_sha256} =~ ^[0-9a-f]{64}$ || ! ${object_environment_sha256} =~ ^[0-9a-f]{64}$ || ! ${provider_environment_sha256} =~ ^[0-9a-f]{64}$ ]]; then
    echo "The final-cutover target identity could not be recorded." >&2
    exit 1
fi

if ! staging_data_directory=$(read_exact_setting /etc/careertrellis/staging.env CAREERTRELLIS_DATA_DIR) || \
   ! staging_owner_id=$(read_exact_setting /etc/careertrellis/staging.env CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID); then
    echo "The frozen SQLite source identity is unavailable." >&2
    exit 1
fi

compute_source_snapshot_sha256() {
    CAREERTRELLIS_CUTOVER_DATA_DIR="${staging_data_directory}" \
    CAREERTRELLIS_CUTOVER_OWNER_ID="${staging_owner_id}" \
        "${release_dir}/backend/venv/bin/python" -c '
import hashlib
import os
from pathlib import Path
import sqlite3
import stat

root = Path(os.environ["CAREERTRELLIS_CUTOVER_DATA_DIR"]).resolve(strict=True)
database = root / "jobapplier.db"
output = root / "output"
if not database.is_file() or database.is_symlink():
    raise SystemExit(1)

digest = hashlib.sha256()
digest.update(b"careertrellis-frozen-source-v1\0")
digest.update(os.environ["CAREERTRELLIS_CUTOVER_OWNER_ID"].encode("utf-8"))
digest.update(b"\0")
connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
try:
    connection.execute("PRAGMA query_only = ON")
    serialized = connection.serialize()
finally:
    connection.close()
if len(serialized) > 1024 * 1024 * 1024:
    raise SystemExit(1)
digest.update(len(serialized).to_bytes(8, "big"))
digest.update(serialized)

def inventory():
    if not output.exists():
        return []
    if output.is_symlink() or not output.is_dir():
        raise SystemExit(1)
    entries = []
    for path in sorted(output.rglob("*"), key=lambda item: item.relative_to(output).as_posix()):
        metadata = path.stat(follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise SystemExit(1)
        entries.append((path.relative_to(output).as_posix(), path, metadata))
    if len(entries) > 100000 or sum(item[2].st_size for item in entries) > 10 * 1024 * 1024 * 1024:
        raise SystemExit(1)
    return entries

before = inventory()
for relative, path, expected in before:
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
            expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns
        ):
            raise SystemExit(1)
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        closed = os.fstat(descriptor)
        if (closed.st_size, closed.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
            raise SystemExit(1)
    finally:
        os.close(descriptor)
after = inventory()
if [(item[0], item[2].st_dev, item[2].st_ino, item[2].st_size, item[2].st_mtime_ns) for item in before] != [
    (item[0], item[2].st_dev, item[2].st_ino, item[2].st_size, item[2].st_mtime_ns) for item in after
]:
    raise SystemExit(1)
print(digest.hexdigest())
' 2>/dev/null
}

write_pending_cutover() {
    local pending_phase=$1
    local temporary_pending
    case "${pending_phase}" in
        preparing)
            [[ ${source_snapshot_sha256} == pending || ${source_snapshot_sha256} =~ ^[0-9a-f]{64}$ ]] || return 1
            ;;
        importing|imported|recovery-verified|authority-marked|activating|activation-failed|activated)
            [[ ${source_snapshot_sha256} =~ ^[0-9a-f]{64}$ ]] || return 1
            ;;
        *)
            echo "The final-cutover pending phase is invalid." >&2
            return 1
            ;;
    esac
    if [[ ! ${attempt_id} =~ ^[0-9a-f]{32}$ ]]; then
        echo "The final-cutover attempt identity is invalid." >&2
        return 1
    fi
    if [[ ! -d /etc/careertrellis/authority || -L /etc/careertrellis/authority || $(stat -c '%U:%G %a' /etc/careertrellis/authority) != "root:root 755" ]]; then
        echo "The final-cutover pending state is unavailable." >&2
        return 1
    fi
    temporary_pending=$(mktemp /etc/careertrellis/authority/.pending-cutover-XXXXXX)
    printf '%s\n' \
        'version=1' \
        "phase=${pending_phase}" \
        "attempt_id=${attempt_id}" \
        "release=${release_revision}" \
        "migration_environment_sha256=${migration_environment_sha256}" \
        "object_environment_sha256=${object_environment_sha256}" \
        "provider_environment_sha256=${provider_environment_sha256}" \
        "source_snapshot_sha256=${source_snapshot_sha256}" \
        > "${temporary_pending}"
    chown root:root "${temporary_pending}"
    chmod 0600 "${temporary_pending}"
    sync -f "${temporary_pending}"
    mv -f -- "${temporary_pending}" "${pending_cutover_file}"
    sync -f /etc/careertrellis/authority
}

validate_pending_cutover() {
    local expected_phase=$1
    local -a pending_lines=()
    if [[ ! -f ${pending_cutover_file} || -L ${pending_cutover_file} || $(stat -c '%U:%G %a' "${pending_cutover_file}") != "root:root 600" ]]; then
        echo "The final-cutover pending state is unavailable." >&2
        return 1
    fi
    mapfile -t pending_lines < "${pending_cutover_file}"
    if [[ ${#pending_lines[@]} -ne 8 ]] || \
       [[ ${pending_lines[0]} != "version=1" ]] || \
       [[ ${pending_lines[1]} != "phase=${expected_phase}" ]] || \
       [[ ! ${pending_lines[2]} =~ ^attempt_id=[0-9a-f]{32}$ ]] || \
       [[ ${pending_lines[3]} != "release=${release_revision}" ]] || \
       [[ ${pending_lines[4]} != "migration_environment_sha256=${migration_environment_sha256}" ]] || \
       [[ ${pending_lines[5]} != "object_environment_sha256=${object_environment_sha256}" ]] || \
       [[ ${pending_lines[6]} != "provider_environment_sha256=${provider_environment_sha256}" ]] || \
       [[ ! ${pending_lines[7]} =~ ^source_snapshot_sha256=(pending|[0-9a-f]{64})$ ]] || \
       { [[ ${expected_phase} != preparing ]] && [[ ${pending_lines[7]} == 'source_snapshot_sha256=pending' ]]; }; then
        echo "The final-cutover pending state does not match this exact attempt." >&2
        return 1
    fi
    attempt_id=${pending_lines[2]#attempt_id=}
    source_snapshot_sha256=${pending_lines[7]#source_snapshot_sha256=}
}

verify_coordinator_lock_descriptor() {
    local descriptor_target
    if [[ ${CAREERTRELLIS_MAINTENANCE_LOCK_FD:-} != 10 || ! -e /proc/$$/fd/10 ]]; then
        echo "The hosted activation lease requires the coordinator's inherited maintenance lock." >&2
        return 1
    fi
    descriptor_target=$(readlink -f "/proc/$$/fd/10" 2>/dev/null || true)
    if [[ ${descriptor_target} != "${careertrellis_maintenance_lock_path}" ]] || ! flock -n 10; then
        echo "The hosted activation lease requires the coordinator's inherited maintenance lock." >&2
        return 1
    fi
}

read_boot_id() {
    local boot_id
    IFS= read -r boot_id < /proc/sys/kernel/random/boot_id || return 1
    boot_id=${boot_id,,}
    [[ ${boot_id} =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || return 1
    printf '%s' "${boot_id}"
}

read_process_start_time() {
    local process_id=$1 process_stat process_fields
    [[ ${process_id} =~ ^[1-9][0-9]*$ ]] || return 1
    IFS= read -r process_stat < "/proc/${process_id}/stat" || return 1
    [[ ${process_stat} == *") "* ]] || return 1
    process_fields=${process_stat##*) }
    set -- ${process_fields}
    [[ $# -ge 20 && ${20} =~ ^[1-9][0-9]*$ ]] || return 1
    printf '%s' "${20}"
}

write_activation_lease() {
    local temporary_lease activation_lease_directory coordinator_boot_id coordinator_start_time
    activation_lease_directory=$(dirname "${activation_lease_file}")
    verify_coordinator_lock_descriptor
    coordinator_boot_id=$(read_boot_id) || {
        echo "The hosted activation lease cannot bind the coordinator process." >&2
        return 1
    }
    coordinator_start_time=$(read_process_start_time "$$") || {
        echo "The hosted activation lease cannot bind the coordinator process." >&2
        return 1
    }
    if [[ -e ${activation_lease_file} || -L ${activation_lease_file} ]]; then
        echo "A hosted activation lease already exists." >&2
        return 1
    fi
    temporary_lease=$(mktemp "${activation_lease_directory}/.careertrellis-hosted-activation.XXXXXX")
    printf '%s\n' \
        'version=1' \
        "attempt_id=${attempt_id}" \
        "release=${release_revision}" \
        "coordinator_pid=$$" \
        "coordinator_boot_id=${coordinator_boot_id}" \
        "coordinator_start_time=${coordinator_start_time}" \
        > "${temporary_lease}"
    chown root:root "${temporary_lease}"
    chmod 0600 "${temporary_lease}"
    sync -f "${temporary_lease}"
    mv -f -- "${temporary_lease}" "${activation_lease_file}"
    sync -f "${activation_lease_directory}"
}

validate_activation_lease() {
    local expected_pid=${1:-}
    local lease_pid current_boot_id current_start_time
    local -a lease_lines=()
    if [[ ! -f ${activation_lease_file} || -L ${activation_lease_file} || $(stat -c '%U:%G %a' "${activation_lease_file}") != "root:root 600" ]]; then
        echo "The hosted activation lease is invalid." >&2
        return 1
    fi
    mapfile -t lease_lines < "${activation_lease_file}"
    if [[ ${#lease_lines[@]} -ne 6 || $(wc -l < "${activation_lease_file}") -ne 6 ]] || \
       [[ ${lease_lines[0]} != 'version=1' ]] || \
       [[ ${lease_lines[1]} != "attempt_id=${attempt_id}" ]] || \
       [[ ${lease_lines[2]} != "release=${release_revision}" ]] || \
       [[ ! ${lease_lines[3]} =~ ^coordinator_pid=[1-9][0-9]*$ ]] || \
       [[ ! ${lease_lines[4]} =~ ^coordinator_boot_id=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || \
       [[ ! ${lease_lines[5]} =~ ^coordinator_start_time=[1-9][0-9]*$ ]] || \
       { [[ -n ${expected_pid} ]] && [[ ${lease_lines[3]} != "coordinator_pid=${expected_pid}" ]]; }; then
        echo "The hosted activation lease is invalid." >&2
        return 1
    fi
    lease_pid=${lease_lines[3]#coordinator_pid=}
    current_boot_id=$(read_boot_id) || return 1
    current_start_time=$(read_process_start_time "${lease_pid}") || return 1
    if [[ ${lease_lines[4]} != "coordinator_boot_id=${current_boot_id}" || \
          ${lease_lines[5]} != "coordinator_start_time=${current_start_time}" ]]; then
        echo "The hosted activation lease is invalid." >&2
        return 1
    fi
}

remove_activation_lease() {
    local expected_pid=${1:-}
    if [[ ! -e ${activation_lease_file} && ! -L ${activation_lease_file} ]]; then
        return 0
    fi
    validate_activation_lease "${expected_pid}"
    rm -f -- "${activation_lease_file}"
    sync -f "$(dirname "${activation_lease_file}")"
}

write_recovery_evidence() {
    local backup_label=$1
    local manifest_sha256=$2
    local temporary_evidence
    if [[ ! ${backup_label} =~ ^[0-9]{8}-[0-9]{6}F$ || ! ${manifest_sha256} =~ ^[0-9a-f]{64}$ ]]; then
        echo "The post-import recovery evidence identity is invalid." >&2
        return 1
    fi
    if [[ -e ${recovery_evidence_file} || -L ${recovery_evidence_file} ]]; then
        validate_recovery_evidence "${backup_label}" "${manifest_sha256}"
        return
    fi
    temporary_evidence=$(mktemp /etc/careertrellis/authority/.post-import-recovery-XXXXXX)
    printf '%s\n' \
        'version=1' \
        "attempt_id=${attempt_id}" \
        "release=${release_revision}" \
        "postgres_backup_label=${backup_label}" \
        "generated_object_manifest_sha256=${manifest_sha256}" \
        'postgres_mirror_verified=true' \
        'postgres_restore_verified=true' \
        'postgres_bad_checksums_zero=true' \
        'generated_object_mirror_verified=true' \
        'generated_object_restore_verified=true' \
        'restored_database_object_reconciliation_verified=true' \
        'provider_keyring_recovery_verified=true' \
        'object_keyring_recovery_verified=true' \
        'decrypt_every_object_verified=true' \
        'reconciliation_verified=true' \
        > "${temporary_evidence}"
    chown root:root "${temporary_evidence}"
    chmod 0600 "${temporary_evidence}"
    sync -f "${temporary_evidence}"
    mv -f -- "${temporary_evidence}" "${recovery_evidence_file}"
    sync -f /etc/careertrellis/authority
}

validate_recovery_evidence() {
    local expected_backup_label=${1:-}
    local expected_manifest_sha256=${2:-}
    local -a evidence_lines=()
    if [[ ! -f ${recovery_evidence_file} || -L ${recovery_evidence_file} || $(stat -c '%U:%G %a' "${recovery_evidence_file}") != "root:root 600" ]]; then
        echo "The post-import recovery evidence is unavailable." >&2
        return 1
    fi
    mapfile -t evidence_lines < "${recovery_evidence_file}"
    if [[ ${#evidence_lines[@]} -ne 15 ]] || \
       [[ ${evidence_lines[0]} != 'version=1' ]] || \
       [[ ${evidence_lines[1]} != "attempt_id=${attempt_id}" ]] || \
       [[ ${evidence_lines[2]} != "release=${release_revision}" ]] || \
       [[ ! ${evidence_lines[3]} =~ ^postgres_backup_label=[0-9]{8}-[0-9]{6}F$ ]] || \
       [[ ! ${evidence_lines[4]} =~ ^generated_object_manifest_sha256=[0-9a-f]{64}$ ]] || \
       [[ ${evidence_lines[5]} != 'postgres_mirror_verified=true' ]] || \
       [[ ${evidence_lines[6]} != 'postgres_restore_verified=true' ]] || \
       [[ ${evidence_lines[7]} != 'postgres_bad_checksums_zero=true' ]] || \
       [[ ${evidence_lines[8]} != 'generated_object_mirror_verified=true' ]] || \
       [[ ${evidence_lines[9]} != 'generated_object_restore_verified=true' ]] || \
       [[ ${evidence_lines[10]} != 'restored_database_object_reconciliation_verified=true' ]] || \
       [[ ${evidence_lines[11]} != 'provider_keyring_recovery_verified=true' ]] || \
       [[ ${evidence_lines[12]} != 'object_keyring_recovery_verified=true' ]] || \
       [[ ${evidence_lines[13]} != 'decrypt_every_object_verified=true' ]] || \
       [[ ${evidence_lines[14]} != 'reconciliation_verified=true' ]]; then
        echo "The post-import recovery evidence does not match this exact attempt." >&2
        return 1
    fi
    if [[ -n ${expected_backup_label} && ${evidence_lines[3]} != "postgres_backup_label=${expected_backup_label}" ]] || \
       [[ -n ${expected_manifest_sha256} && ${evidence_lines[4]} != "generated_object_manifest_sha256=${expected_manifest_sha256}" ]]; then
        echo "The post-import recovery evidence does not match this exact attempt." >&2
        return 1
    fi
}

if [[ ${cutover_action} == "import-and-pause" ]]; then
    if [[ -e ${pending_cutover_file} || -L ${pending_cutover_file} || -e ${recovery_evidence_file} || -L ${recovery_evidence_file} ]]; then
        echo "A final-cutover attempt is already pending; reconcile it before any new import." >&2
        exit 1
    fi
fi

phase=validation
completed=false
maintenance_started=false

cutover_failure() {
    local exit_code=${1:-1}
    trap - ERR INT TERM
    if [[ ${completed} != true ]]; then
        if [[ ${maintenance_started} == true ]]; then
            systemctl stop careertrellis-erasure-worker.service careertrellis-worker.service careertrellis-web.service >/dev/null 2>&1 || true
            systemctl disable careertrellis-erasure-worker.service careertrellis-worker.service careertrellis-web.service >/dev/null 2>&1 || true
        fi
        authority_state=$("${release_script_dir}/hosted-authority.sh" status 2>/dev/null || true)
        if [[ ${authority_state} == "postgres-r2-authoritative" ]]; then
            if [[ ${attempt_id:-} =~ ^[0-9a-f]{32}$ && ${source_snapshot_sha256:-} =~ ^[0-9a-f]{64}$ ]]; then
                write_pending_cutover activation-failed >/dev/null 2>&1 || true
            fi
            remove_activation_lease "$$" >/dev/null 2>&1 || true
            echo "Final cutover stopped after the authority boundary; keep application services stopped and apply a reviewed forward correction." >&2
        elif [[ ${authority_state} == "sqlite-pre-cutover" ]]; then
            if [[ -f ${pending_cutover_file} && ! -L ${pending_cutover_file} ]] && grep -Fx 'phase=preparing' "${pending_cutover_file}" >/dev/null 2>&1; then
                echo "Final cutover stopped during pre-import preparation; keep application services stopped, review the failure, then use the reviewed resume-preparation action." >&2
            elif [[ -f ${pending_cutover_file} && ! -L ${pending_cutover_file} ]] && grep -Fx 'phase=importing' "${pending_cutover_file}" >/dev/null 2>&1; then
                echo "Final cutover stopped during an ambiguous import; keep application services stopped and perform the reviewed read-only reconciliation before using reconcile-pending." >&2
            else
                echo "Final cutover stopped before the authority boundary; keep application services stopped and do not retry, reconcile, or activate until the durable cutover state has been reviewed." >&2
            fi
        else
            echo "Final cutover authority state is unavailable; keep application services stopped and do not import, retry, roll back, or activate until authority is manually established." >&2
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

assert_services_inactive_and_disabled() {
    local service
    assert_services_inactive
    for service in careertrellis-web.service careertrellis-worker.service careertrellis-erasure-worker.service; do
        if systemctl is-enabled --quiet "${service}"; then
            echo "The web, search-worker, and erasure-worker services must remain disabled during final cutover." >&2
            return 1
        fi
    done
}

stop_and_disable_application_services() {
    systemctl stop careertrellis-erasure-worker.service careertrellis-worker.service careertrellis-web.service
    systemctl disable careertrellis-erasure-worker.service careertrellis-worker.service careertrellis-web.service
    assert_services_inactive_and_disabled
}

compute_and_validate_bound_source() {
    local expected_source=${source_snapshot_sha256}
    local current_source
    if [[ ! ${expected_source} =~ ^[0-9a-f]{64}$ ]] || \
       ! current_source=$(compute_source_snapshot_sha256) || \
       [[ ! ${current_source} =~ ^[0-9a-f]{64}$ ]] || \
       [[ ${current_source} != "${expected_source}" ]]; then
        echo "The frozen SQLite source does not match this exact attempt." >&2
        return 1
    fi
}

authority_state=$("${release_script_dir}/hosted-authority.sh" status)
if [[ ${cutover_action} == "resume-hosted-activation" ]]; then
    if [[ ${authority_state} != "postgres-r2-authoritative" ]]; then
        echo "Hosted activation recovery requires PostgreSQL/R2 authority." >&2
        exit 1
    fi
elif [[ ${authority_state} != "sqlite-pre-cutover" ]]; then
    echo "Final cutover can begin only from SQLite pre-cutover authority." >&2
    exit 1
fi

phase=start-guard-load
systemctl daemon-reload
verify_installed_start_guards true

if [[ ${cutover_action} == "import-and-pause" ]]; then
    attempt_id=$("${release_dir}/backend/venv/bin/python" -c 'import secrets; print(secrets.token_hex(16))')
    if [[ ! ${attempt_id} =~ ^[0-9a-f]{32}$ ]]; then
        echo "The final-cutover attempt identity could not be recorded." >&2
        exit 1
    fi
    source_snapshot_sha256=pending
    phase=preparing-state
    maintenance_started=true
    write_pending_cutover preparing
elif [[ ${cutover_action} == "resume-preparation" ]]; then
    if [[ -e ${recovery_evidence_file} || -L ${recovery_evidence_file} ]]; then
        echo "Pre-import preparation cannot resume after recovery evidence exists." >&2
        exit 1
    fi
    validate_pending_cutover preparing
fi

phase=service-stop
maintenance_started=true
stop_and_disable_application_services

if [[ ${cutover_action} == "import-and-pause" || ${cutover_action} == "resume-preparation" ]]; then
    if [[ ${source_snapshot_sha256} == pending ]]; then
        if ! source_snapshot_sha256=$(compute_source_snapshot_sha256) || [[ ! ${source_snapshot_sha256} =~ ^[0-9a-f]{64}$ ]]; then
            echo "The frozen SQLite source identity could not be recorded." >&2
            exit 1
        fi
        phase=preparing-source-bound
        write_pending_cutover preparing
    else
        compute_and_validate_bound_source
    fi

    phase=migration
    "${release_script_dir}/migrate-postgres.sh" "${release_dir}" "${migration_environment_file}"

    phase=runtime-grants
    "${release_script_dir}/apply-postgres-runtime-grants.sh" "${release_dir}" "${migration_environment_file}"

    phase=preflight
    "${release_script_dir}/preflight-sqlite-postgres-import.sh" "${release_dir}"

    phase=import-and-reconciliation
    assert_services_inactive_and_disabled
    write_pending_cutover importing
    export CAREERTRELLIS_FINAL_CUTOVER_ATTEMPT_ID="${attempt_id}"
    "${release_script_dir}/import-sqlite-postgres.sh" \
        "${release_dir}" \
        "${migration_environment_file}" \
        "${object_environment_file}"

    if ! source_snapshot_after=$(compute_source_snapshot_sha256) || [[ ${source_snapshot_after} != "${source_snapshot_sha256}" ]]; then
        echo "The frozen SQLite source changed during final-cutover import." >&2
        exit 1
    fi
    write_pending_cutover imported
    completed=true
    trap - ERR INT TERM
    echo "CareerTrellis permanent import and reconciliation completed for ${release_revision}; services remain stopped and SQLite authority is unchanged."
    exit 0
fi

case "${cutover_action}" in
    reconcile-pending)
        validate_pending_cutover importing
        compute_and_validate_bound_source
        phase=pending-import-preflight
        "${release_script_dir}/preflight-sqlite-postgres-import.sh" "${release_dir}"
        phase=pending-import-reconciliation
        assert_services_inactive_and_disabled
        "${release_script_dir}/reconcile-sqlite-postgres-import.sh" \
            "${release_dir}" \
            "${migration_environment_file}" \
            "${object_environment_file}"
        write_pending_cutover imported
        completed=true
        trap - ERR INT TERM
        echo "CareerTrellis pending import reconciled for ${release_revision}; services remain stopped and SQLite authority is unchanged."
        exit 0
        ;;
    record-recovery-evidence)
        validate_pending_cutover imported
        compute_and_validate_bound_source
        phase=post-import-preflight
        "${release_script_dir}/preflight-sqlite-postgres-import.sh" "${release_dir}"
        phase=post-import-reconciliation
        assert_services_inactive_and_disabled
        "${release_script_dir}/reconcile-sqlite-postgres-import.sh" \
            "${release_dir}" \
            "${migration_environment_file}" \
            "${object_environment_file}"
        write_recovery_evidence "${evidence_confirmation}" "${authority_confirmation}"
        write_pending_cutover recovery-verified
        completed=true
        trap - ERR INT TERM
        echo "CareerTrellis post-import recovery evidence recorded for ${release_revision}; services remain stopped and SQLite authority is unchanged."
        exit 0
        ;;
    mark-and-activate)
        validate_pending_cutover recovery-verified
        compute_and_validate_bound_source
        validate_recovery_evidence
        ;;
    resume-hosted-activation)
        if [[ -f ${pending_cutover_file} && ! -L ${pending_cutover_file} ]] && \
           grep -Fx 'phase=authority-marked' "${pending_cutover_file}" >/dev/null; then
            validate_pending_cutover authority-marked
            compute_and_validate_bound_source
            validate_recovery_evidence
        elif [[ -f ${pending_cutover_file} && ! -L ${pending_cutover_file} ]] &&
             grep -Fx 'phase=activation-failed' "${pending_cutover_file}" >/dev/null; then
            validate_pending_cutover activation-failed
            compute_and_validate_bound_source
            validate_recovery_evidence
            remove_activation_lease
        elif [[ -f ${pending_cutover_file} && ! -L ${pending_cutover_file} ]] &&
             grep -Fx 'phase=activating' "${pending_cutover_file}" >/dev/null; then
            validate_pending_cutover activating
            compute_and_validate_bound_source
            validate_recovery_evidence
            remove_activation_lease
            write_pending_cutover activation-failed
        elif [[ -f ${pending_cutover_file} && ! -L ${pending_cutover_file} ]] &&
             grep -Fx 'phase=recovery-verified' "${pending_cutover_file}" >/dev/null; then
            validate_pending_cutover recovery-verified
            compute_and_validate_bound_source
            validate_recovery_evidence
            write_pending_cutover activation-failed
        else
            echo "The final-cutover pending state does not match this exact attempt." >&2
            exit 1
        fi
        ;;
esac

if [[ ${cutover_action} == "mark-and-activate" ]]; then
    phase=final-preflight
    "${release_script_dir}/preflight-sqlite-postgres-import.sh" "${release_dir}"

    phase=final-reconciliation
    assert_services_inactive_and_disabled
    "${release_script_dir}/reconcile-sqlite-postgres-import.sh" \
        "${release_dir}" \
        "${migration_environment_file}" \
        "${object_environment_file}"

    phase=authority-mark
    assert_services_inactive_and_disabled
    "${release_script_dir}/hosted-authority.sh" \
        mark-postgres-r2-authoritative \
        "${release_dir}" \
        "${attempt_id}" \
        "${authority_confirmation}"
    write_pending_cutover authority-marked
fi

phase=activation-state
assert_services_inactive_and_disabled
write_pending_cutover activating
phase=activation-lease
write_activation_lease
phase=activation
"${release_script_dir}/deploy-release.sh" "${release_revision}"
if ! systemctl is-active --quiet careertrellis-web.service || \
   ! systemctl is-enabled --quiet careertrellis-web.service || \
   ! systemctl is-active --quiet caddy.service || \
   ! systemctl is-enabled --quiet caddy.service || \
   systemctl is-active --quiet careertrellis-worker.service || \
   systemctl is-enabled --quiet careertrellis-worker.service || \
   systemctl is-active --quiet careertrellis-erasure-worker.service || \
   systemctl is-enabled --quiet careertrellis-erasure-worker.service; then
    echo "The final-cutover service boundary is incomplete." >&2
    exit 1
fi
write_pending_cutover activated
remove_activation_lease "$$"
completed=true
trap - ERR INT TERM
echo "CareerTrellis final PostgreSQL/R2 cutover completed for ${release_revision}."
