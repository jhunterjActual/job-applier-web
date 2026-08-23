#!/usr/bin/env bash
set -Eeuo pipefail

pre_cutover_state=sqlite-pre-cutover
hosted_state=postgres-r2-authoritative
hosted_capability=postgres-r2-authoritative-v1
authority_directory=/etc/careertrellis/authority
authority_parent=/etc/careertrellis
state_file=${authority_directory}/data-authority
pending_cutover_file=${authority_directory}/pending-cutover
recovery_evidence_file=${authority_directory}/post-import-recovery
activation_lease_file=/run/careertrellis-hosted-activation.lease
maintenance_lock_file=/run/lock/careertrellis-maintenance.lock
release_root=/opt/careertrellis/staging
staging_environment_file=/etc/careertrellis/staging.env
object_environment_file=/etc/careertrellis/object-storage.env
test_mode=false

if [[ ${1:-} == "--test-root" ]]; then
    if [[ ${CAREERTRELLIS_AUTHORITY_TESTING:-} != "true" || -z ${2:-} ]]; then
        echo "Hosted authority test configuration is invalid." >&2
        exit 1
    fi
    test_mode=true
    authority_directory=${2}/authority
    authority_parent=${2}
    state_file=${authority_directory}/data-authority
    pending_cutover_file=${authority_directory}/pending-cutover
    recovery_evidence_file=${authority_directory}/post-import-recovery
    activation_lease_file=${2}/run/careertrellis-hosted-activation.lease
    maintenance_lock_file=${2}/run/lock/careertrellis-maintenance.lock
    release_root=${2}/staging
    staging_environment_file=${2}/etc/staging.env
    object_environment_file=${2}/etc/object-storage.env
    shift 2
fi

command_name=${1:-}
shift $(( $# > 0 ? 1 : 0 ))

fixed_state_error() {
    echo "Hosted authority state is unavailable." >&2
}

require_root() {
    if [[ ${test_mode} == false && ${EUID} -ne 0 ]]; then
        echo "Run hosted authority administration as root." >&2
        exit 1
    fi
}

validate_authority_storage() {
    if [[ ! -d ${authority_directory} || -L ${authority_directory} || ! -f ${state_file} || -L ${state_file} ]]; then
        fixed_state_error
        return 1
    fi
    if [[ ${test_mode} == false ]]; then
        if [[ $(readlink -f "${authority_directory}") != "${authority_directory}" || $(stat -c '%U:%G %a' "${authority_parent}") != "root:root 755" || $(stat -c '%U:%G %a' "${authority_directory}") != "root:root 755" || $(stat -c '%U:%G %a' "${state_file}") != "root:root 644" ]]; then
            fixed_state_error
            return 1
        fi
    fi
}

read_authority_state() {
    local -a state_lines=()
    validate_authority_storage || return 1
    mapfile -t state_lines < "${state_file}"
    if [[ ${#state_lines[@]} -ne 1 ]]; then
        fixed_state_error
        return 1
    fi
    if [[ ${state_lines[0]} != "${pre_cutover_state}" && ${state_lines[0]} != "${hosted_state}" ]]; then
        fixed_state_error
        return 1
    fi
    printf '%s\n' "${state_lines[0]}"
}

write_authority_state() {
    local new_state=$1
    local temporary_state
    if [[ ${test_mode} == true ]]; then
        mkdir -p -- "${authority_directory}"
    else
        if [[ ! -d ${authority_parent} || -L ${authority_parent} || $(stat -c '%U:%G %a' "${authority_parent}") != "root:root 755" ]]; then
            fixed_state_error
            return 1
        fi
        if [[ -e ${authority_directory} || -L ${authority_directory} ]]; then
            if [[ ! -d ${authority_directory} || -L ${authority_directory} || $(stat -c '%U:%G %a' "${authority_directory}") != "root:root 755" ]]; then
                fixed_state_error
                return 1
            fi
        fi
        install -d -o root -g root -m 0755 "${authority_directory}"
    fi
    temporary_state=$(mktemp "${authority_directory}/.data-authority-XXXXXX")
    printf '%s\n' "${new_state}" > "${temporary_state}"
    if [[ ${test_mode} == false ]]; then
        chown root:root "${temporary_state}"
        chmod 0644 "${temporary_state}"
        sync -f "${temporary_state}"
    fi
    mv -f -- "${temporary_state}" "${state_file}"
    if [[ ${test_mode} == false ]]; then
        sync -f "${authority_directory}"
    fi
}

initialize_authority_state() {
    require_root
    if [[ -e ${authority_directory} || -L ${authority_directory} ]]; then
        if [[ -e ${state_file} || -L ${state_file} ]]; then
            read_authority_state >/dev/null
            return
        fi
        fixed_state_error
        return 1
    fi
    write_authority_state "${pre_cutover_state}"
    read_authority_state >/dev/null
}

validate_release_target_path() {
    local target_release=$1
    local target_revision
    if [[ ! -d ${target_release} || -L ${target_release} ]]; then
        return 1
    fi
    target_release=$(readlink -f "${target_release}")
    case "${target_release}" in
        "${release_root}"/releases/*) ;;
        *) return 1 ;;
    esac
    target_revision=$(basename "${target_release}")
    if [[ ! ${target_revision} =~ ^[0-9a-f]{40}$ ]]; then
        return 1
    fi
}

validate_release_capability() {
    local target_release=$1
    local capability_file cutover_file
    local -a capability_lines=()
    validate_release_target_path "${target_release}" || return 1
    target_release=$(readlink -f "${target_release}")
    capability_file=${target_release}/deploy/hosted-authority.compatibility
    cutover_file=${target_release}/backend/data_store.py
    if [[ ! -f ${capability_file} || -L ${capability_file} || -x ${capability_file} ]]; then
        return 1
    fi
    mapfile -t capability_lines < "${capability_file}"
    [[ ${#capability_lines[@]} -eq 1 && ${capability_lines[0]} == "${hosted_capability}" ]] && \
        [[ -f ${cutover_file} && ! -L ${cutover_file} ]] && \
        [[ $(grep -Ec '^HOSTED_REPOSITORY_CUTOVER_COMPLETE = (True|False)$' "${cutover_file}") -eq 1 ]] && \
        [[ $(grep -Fxc 'HOSTED_REPOSITORY_CUTOVER_COMPLETE = True' "${cutover_file}") -eq 1 ]]
}

validate_pre_cutover_release() {
    local target_release=$1
    local capability_file cutover_file
    validate_release_target_path "${target_release}" || return 1
    target_release=$(readlink -f "${target_release}")
    capability_file=${target_release}/deploy/hosted-authority.compatibility
    cutover_file=${target_release}/backend/data_store.py
    [[ ! -e ${capability_file} && ! -L ${capability_file} ]] && \
        [[ -f ${cutover_file} && ! -L ${cutover_file} ]] && \
        [[ $(grep -Ec '^HOSTED_REPOSITORY_CUTOVER_COMPLETE = (True|False)$' "${cutover_file}") -eq 1 ]] && \
        [[ $(grep -Fxc 'HOSTED_REPOSITORY_CUTOVER_COMPLETE = False' "${cutover_file}") -eq 1 ]]
}

validate_rehearsal_release() {
    local target_release=$1
    validate_pre_cutover_release "${target_release}" || \
        validate_release_capability "${target_release}"
}

require_hosted_release_capability() {
    local current_release
    if [[ ! -L ${release_root}/current ]]; then
        return 1
    fi
    current_release=$(readlink -f "${release_root}/current")
    validate_release_capability "${current_release}"
}

require_pre_cutover_release() {
    local current_release
    if [[ ! -L ${release_root}/current ]]; then
        return 1
    fi
    current_release=$(readlink -f "${release_root}/current")
    validate_pre_cutover_release "${current_release}"
}

require_current_release_target() {
    local current_release
    if [[ ! -L ${release_root}/current ]]; then
        return 1
    fi
    current_release=$(readlink -f "${release_root}/current")
    validate_release_target_path "${current_release}"
}

environment_file_has_exact_selection() {
    local environment_file=$1
    local setting_name=$2
    local expected_line=$3
    [[ -f ${environment_file} && ! -L ${environment_file} ]] || return 1
    if [[ ${test_mode} == false && $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
        return 1
    fi
    [[ $(grep -Ec "^${setting_name}=" "${environment_file}") -eq 1 ]] || return 1
    grep -Fx "${expected_line}" "${environment_file}" >/dev/null
}

assert_release_startable() {
    local target_release=$1
    local state
    state=$(read_authority_state) || return 1
    if ! validate_release_target_path "${target_release}"; then
        echo "Hosted authority release validation failed." >&2
        return 1
    fi
    if [[ ${state} == "${pre_cutover_state}" ]]; then
        if [[ ! -e ${pending_cutover_file} && ! -L ${pending_cutover_file} ]] && \
           validate_pre_cutover_release "${target_release}"; then
            return
        fi
        echo "Hosted authority release validation failed." >&2
        return 1
    fi
    if ! validate_hosted_release "${target_release}"; then
        echo "Hosted authority release validation failed." >&2
        return 1
    fi
}

validate_hosted_release() {
    local target_release=$1
    validate_release_capability "${target_release}" && \
        environment_file_has_exact_selection "${staging_environment_file}" CAREERTRELLIS_DATA_STORE CAREERTRELLIS_DATA_STORE=postgres && \
        environment_file_has_exact_selection "${object_environment_file}" CAREERTRELLIS_OBJECT_STORE CAREERTRELLIS_OBJECT_STORE=r2_encrypted
}

check_runtime() {
    local runtime_kind=$1
    local state current_release current_revision pending_attempt
    require_root
    state=$(read_authority_state) || return 1
    if ! require_current_release_target; then
        echo "Hosted authority runtime validation failed." >&2
        return 1
    fi
    if [[ ${state} == "${pre_cutover_state}" ]]; then
        if [[ ! -e ${pending_cutover_file} && ! -L ${pending_cutover_file} ]] && \
           require_pre_cutover_release; then
            return
        fi
        echo "Hosted authority runtime validation failed." >&2
        return 1
    fi
    if ! require_hosted_release_capability; then
        echo "Hosted authority runtime validation failed." >&2
        return 1
    fi
    if [[ -e ${pending_cutover_file} || -L ${pending_cutover_file} ]]; then
        current_release=$(readlink -f "${release_root}/current")
        current_revision=$(basename "${current_release}")
        # An activated record is immutable evidence of the release that
        # completed the cutover, not a permanent pin on later compatible
        # releases. The current release remains gated by the hosted capability
        # above. Only the transient activating phase stays revision- and
        # live-coordinator-bound.
        if activated_cutover_history_allows; then
            :
        elif pending_cutover_allows activating "${current_revision}"; then
            pending_attempt=$(sed -n 's/^attempt_id=//p' "${pending_cutover_file}")
            if ! activation_lease_allows "${pending_attempt}" "${current_revision}"; then
                echo "Hosted authority runtime validation failed." >&2
                return 1
            fi
        else
            echo "Hosted authority runtime validation failed." >&2
            return 1
        fi
    fi
    if [[ ${runtime_kind} == "web" && (${CAREERTRELLIS_DATA_STORE:-} != "postgres" || ${CAREERTRELLIS_OBJECT_STORE:-} != "r2_encrypted") ]]; then
        echo "Hosted authority runtime validation failed." >&2
        return 1
    fi
}

assert_pre_cutover() {
    local failure_message=$1
    local state
    state=$(read_authority_state) || return 1
    if [[ ${state} != "${pre_cutover_state}" ]]; then
        echo "${failure_message}" >&2
        return 1
    fi
}

pending_cutover_allows() {
    local expected_phase=$1
    local expected_release=${2:-}
    local expected_attempt=${3:-}
    local expected_migration_environment_sha256=${4:-}
    local expected_object_environment_sha256=${5:-}
    local expected_provider_environment_sha256=${6:-}
    local -a pending_lines=()
    if [[ ! -f ${pending_cutover_file} || -L ${pending_cutover_file} ]]; then
        return 1
    fi
    if [[ ${test_mode} == false && $(stat -c '%U:%G %a' "${pending_cutover_file}") != "root:root 600" ]]; then
        return 1
    fi
    mapfile -t pending_lines < "${pending_cutover_file}"
    [[ ${#pending_lines[@]} -eq 8 && $(wc -l < "${pending_cutover_file}") -eq 8 ]] || return 1
    [[ ${pending_lines[0]} == "version=1" ]] || return 1
    [[ ${pending_lines[1]} == "phase=${expected_phase}" ]] || return 1
    [[ ${pending_lines[2]} =~ ^attempt_id=[0-9a-f]{32}$ ]] || return 1
    [[ ${pending_lines[3]} =~ ^release=[0-9a-f]{40}$ ]] || return 1
    [[ ${pending_lines[4]} =~ ^migration_environment_sha256=[0-9a-f]{64}$ ]] || return 1
    [[ ${pending_lines[5]} =~ ^object_environment_sha256=[0-9a-f]{64}$ ]] || return 1
    [[ ${pending_lines[6]} =~ ^provider_environment_sha256=[0-9a-f]{64}$ ]] || return 1
    if [[ ${expected_phase} == "preparing" ]]; then
        [[ ${pending_lines[7]} =~ ^source_snapshot_sha256=(pending|[0-9a-f]{64})$ ]] || return 1
    else
        [[ ${pending_lines[7]} =~ ^source_snapshot_sha256=[0-9a-f]{64}$ ]] || return 1
    fi
    if [[ -n ${expected_release} && ${pending_lines[3]} != "release=${expected_release}" ]]; then
        return 1
    fi
    if [[ -n ${expected_attempt} && ${pending_lines[2]} != "attempt_id=${expected_attempt}" ]]; then
        return 1
    fi
    if [[ -n ${expected_migration_environment_sha256} && ${pending_lines[4]} != "migration_environment_sha256=${expected_migration_environment_sha256}" ]] || \
       [[ -n ${expected_object_environment_sha256} && ${pending_lines[5]} != "object_environment_sha256=${expected_object_environment_sha256}" ]] || \
       [[ -n ${expected_provider_environment_sha256} && ${pending_lines[6]} != "provider_environment_sha256=${expected_provider_environment_sha256}" ]]; then
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

activation_lease_allows() {
    local expected_attempt=$1
    local expected_release=$2
    local coordinator_pid current_boot_id current_start_time
    local -a lease_lines=()
    if [[ ! -f ${activation_lease_file} || -L ${activation_lease_file} ]]; then
        return 1
    fi
    if [[ ${test_mode} == false && $(stat -c '%U:%G %a' "${activation_lease_file}") != "root:root 600" ]]; then
        return 1
    fi
    mapfile -t lease_lines < "${activation_lease_file}"
    [[ ${#lease_lines[@]} -eq 6 && $(wc -l < "${activation_lease_file}") -eq 6 ]] || return 1
    [[ ${lease_lines[0]} == "version=1" ]] || return 1
    [[ ${lease_lines[1]} == "attempt_id=${expected_attempt}" ]] || return 1
    [[ ${lease_lines[2]} == "release=${expected_release}" ]] || return 1
    [[ ${lease_lines[3]} =~ ^coordinator_pid=[1-9][0-9]*$ ]] || return 1
    [[ ${lease_lines[4]} =~ ^coordinator_boot_id=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || return 1
    [[ ${lease_lines[5]} =~ ^coordinator_start_time=[1-9][0-9]*$ ]] || return 1
    coordinator_pid=${lease_lines[3]#coordinator_pid=}
    kill -0 "${coordinator_pid}" 2>/dev/null || return 1
    [[ -e /proc/${coordinator_pid}/fd/10 ]] || return 1
    [[ $(readlink -f "/proc/${coordinator_pid}/fd/10" 2>/dev/null || true) == "${maintenance_lock_file}" ]] || return 1
    current_boot_id=$(read_boot_id) || return 1
    current_start_time=$(read_process_start_time "${coordinator_pid}") || return 1
    [[ ${lease_lines[4]} == "coordinator_boot_id=${current_boot_id}" ]] || return 1
    [[ ${lease_lines[5]} == "coordinator_start_time=${current_start_time}" ]]
}

require_inherited_maintenance_lock() {
    [[ ${CAREERTRELLIS_MAINTENANCE_LOCK_FD:-} == 10 ]] || return 1
    [[ -e /proc/self/fd/10 ]] || return 1
    [[ $(readlink -f /proc/self/fd/10 2>/dev/null || true) == "${maintenance_lock_file}" ]] || return 1
    flock -n 10
}

recovery_evidence_allows() {
    local expected_release=$1
    local expected_attempt=$2
    local -a evidence_lines=()
    if [[ ! -f ${recovery_evidence_file} || -L ${recovery_evidence_file} ]]; then
        return 1
    fi
    if [[ ${test_mode} == false && $(stat -c '%U:%G %a' "${recovery_evidence_file}") != "root:root 600" ]]; then
        return 1
    fi
    mapfile -t evidence_lines < "${recovery_evidence_file}"
    [[ ${#evidence_lines[@]} -eq 15 ]] || return 1
    [[ ${evidence_lines[0]} == 'version=1' ]] || return 1
    [[ ${evidence_lines[1]} == "attempt_id=${expected_attempt}" ]] || return 1
    [[ ${evidence_lines[2]} == "release=${expected_release}" ]] || return 1
    [[ ${evidence_lines[3]} =~ ^postgres_backup_label=[0-9]{8}-[0-9]{6}F$ ]] || return 1
    [[ ${evidence_lines[4]} =~ ^generated_object_manifest_sha256=[0-9a-f]{64}$ ]] || return 1
    [[ ${evidence_lines[5]} == 'postgres_mirror_verified=true' ]] || return 1
    [[ ${evidence_lines[6]} == 'postgres_restore_verified=true' ]] || return 1
    [[ ${evidence_lines[7]} == 'postgres_bad_checksums_zero=true' ]] || return 1
    [[ ${evidence_lines[8]} == 'generated_object_mirror_verified=true' ]] || return 1
    [[ ${evidence_lines[9]} == 'generated_object_restore_verified=true' ]] || return 1
    [[ ${evidence_lines[10]} == 'restored_database_object_reconciliation_verified=true' ]] || return 1
    [[ ${evidence_lines[11]} == 'provider_keyring_recovery_verified=true' ]] || return 1
    [[ ${evidence_lines[12]} == 'object_keyring_recovery_verified=true' ]] || return 1
    [[ ${evidence_lines[13]} == 'decrypt_every_object_verified=true' ]] || return 1
    [[ ${evidence_lines[14]} == 'reconciliation_verified=true' ]]
}

activated_cutover_history_allows() {
    local historical_release historical_attempt
    pending_cutover_allows activated || return 1
    historical_release=$(sed -n 's/^release=//p' "${pending_cutover_file}")
    historical_attempt=$(sed -n 's/^attempt_id=//p' "${pending_cutover_file}")
    recovery_evidence_allows "${historical_release}" "${historical_attempt}"
}

assert_import_allowed() {
    local attempt_id=${1:-}
    local expected_release=${2:-}
    local migration_environment_sha256=${3:-}
    local object_environment_sha256=${4:-}
    local provider_environment_sha256=${5:-}
    assert_pre_cutover "Hosted authority prevents SQLite import." || return 1
    if [[ ! -e ${pending_cutover_file} && ! -L ${pending_cutover_file} ]]; then
        echo "Hosted authority prevents SQLite import." >&2
        return 1
    fi
    if [[ ! ${attempt_id} =~ ^[0-9a-f]{32}$ || ! ${expected_release} =~ ^[0-9a-f]{40}$ || \
          ! ${migration_environment_sha256} =~ ^[0-9a-f]{64}$ || ! ${object_environment_sha256} =~ ^[0-9a-f]{64}$ || \
          ! ${provider_environment_sha256} =~ ^[0-9a-f]{64}$ ]] || \
       ! pending_cutover_allows importing "${expected_release}" "${attempt_id}" \
            "${migration_environment_sha256}" "${object_environment_sha256}" "${provider_environment_sha256}"; then
        echo "Hosted authority prevents SQLite import." >&2
        return 1
    fi
    if ! require_inherited_maintenance_lock; then
        echo "Hosted authority prevents SQLite import." >&2
        return 1
    fi
}

assert_rehearsal_import_allowed() {
    local expected_release=${1:-}
    local migration_environment_sha256=${2:-}
    local object_environment_sha256=${3:-}
    local coordinator_pid=${4:-}
    assert_pre_cutover "Hosted authority prevents SQLite import." || return 1
    if [[ -e ${pending_cutover_file} || -L ${pending_cutover_file} ]] || \
       [[ ! ${expected_release} =~ ^[0-9a-f]{40}$ ]] || \
       [[ ! ${migration_environment_sha256} =~ ^[0-9a-f]{64}$ ]] || \
       [[ ! ${object_environment_sha256} =~ ^[0-9a-f]{64}$ ]] || \
       [[ ! ${coordinator_pid} =~ ^[1-9][0-9]*$ ]] || \
       ! validate_rehearsal_release "${release_root}/releases/${expected_release}" || \
       ! require_inherited_maintenance_lock || \
       ! kill -0 "${coordinator_pid}" 2>/dev/null || \
       [[ ! -e /proc/${coordinator_pid}/fd/10 ]] || \
       [[ $(readlink -f "/proc/${coordinator_pid}/fd/10" 2>/dev/null || true) != "${maintenance_lock_file}" ]]; then
        echo "Hosted authority prevents SQLite import." >&2
        return 1
    fi
}

mark_hosted_authoritative() {
    local target_release=${1:-}
    local attempt_id=${2:-}
    local confirmation=${3:-}
    local maintenance_lock_helper script_dir
    local state
    require_root
    if [[ ${confirmation} != "I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED" ]]; then
        echo "Hosted authority confirmation is required." >&2
        return 1
    fi
    if [[ ! ${attempt_id} =~ ^[0-9a-f]{32}$ || ${CAREERTRELLIS_MAINTENANCE_LOCK_FD:-} != 10 ]]; then
        echo "Hosted authority confirmation is required." >&2
        return 1
    fi
    if [[ ${test_mode} == false ]]; then
        script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
        maintenance_lock_helper=${script_dir}/maintenance-lock.sh
        if [[ ! -f ${maintenance_lock_helper} ]]; then
            maintenance_lock_helper=/usr/local/libexec/careertrellis/maintenance-lock.sh
        fi
        if [[ ! -f ${maintenance_lock_helper} || -L ${maintenance_lock_helper} ]]; then
            echo "The reviewed maintenance-lock helper is unavailable." >&2
            return 1
        fi
        # shellcheck source=maintenance-lock.sh
        source "${maintenance_lock_helper}"
        acquire_careertrellis_maintenance_lock || return 1
    fi
    state=$(read_authority_state) || return 1
    if [[ ${state} == "${hosted_state}" ]]; then
        return
    fi
    if ! validate_hosted_release "${target_release}"; then
        echo "Hosted authority release validation failed." >&2
        return 1
    fi
    target_release=$(readlink -f "${target_release}")
    if ! pending_cutover_allows recovery-verified "$(basename "${target_release}")" "${attempt_id}" || \
       ! recovery_evidence_allows "$(basename "${target_release}")" "${attempt_id}"; then
        echo "Hosted authority release validation failed." >&2
        return 1
    fi
    if [[ ${test_mode} == false ]]; then
        for service in careertrellis-web.service careertrellis-worker.service careertrellis-erasure-worker.service; do
            if systemctl is-active --quiet "${service}"; then
                echo "Stop the web, search-worker, and erasure-worker services before changing hosted authority." >&2
                return 1
            fi
        done
    fi
    write_authority_state "${hosted_state}"
    [[ $(read_authority_state) == "${hosted_state}" ]]
}

case "${command_name}" in
    initialize)
        [[ $# -eq 0 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        initialize_authority_state
        ;;
    status)
        [[ $# -eq 0 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        read_authority_state
        ;;
    mark-postgres-r2-authoritative)
        [[ $# -eq 3 ]] || { echo "Hosted authority confirmation is required." >&2; exit 1; }
        mark_hosted_authoritative "$1" "$2" "$3"
        ;;
    assert-code-rollback-allowed)
        [[ $# -eq 0 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        assert_pre_cutover "Hosted authority prevents code rollback."
        ;;
    assert-import-allowed)
        [[ $# -eq 0 || $# -eq 5 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        assert_import_allowed "${1:-}" "${2:-}" "${3:-}" "${4:-}" "${5:-}"
        ;;
    assert-rehearsal-import-allowed)
        [[ $# -eq 4 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        assert_rehearsal_import_allowed "$1" "$2" "$3" "$4"
        ;;
    assert-reconciliation-allowed)
        [[ $# -eq 0 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        assert_pre_cutover "Hosted authority prevents SQLite reconciliation."
        ;;
    assert-release-startable)
        [[ $# -eq 1 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        assert_release_startable "$1"
        ;;
    check-web-runtime)
        [[ $# -eq 0 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        check_runtime web
        ;;
    check-worker-runtime)
        [[ $# -eq 0 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        check_runtime worker
        ;;
    *)
        echo "Usage: $0 {initialize|status|mark-postgres-r2-authoritative RELEASE ATTEMPT_ID CONFIRMATION|assert-code-rollback-allowed|assert-import-allowed ATTEMPT_ID RELEASE MIGRATION_SHA256 OBJECT_SHA256 PROVIDER_SHA256|assert-rehearsal-import-allowed RELEASE MIGRATION_SHA256 OBJECT_SHA256 COORDINATOR_PID|assert-reconciliation-allowed|assert-release-startable RELEASE|check-web-runtime|check-worker-runtime}" >&2
        exit 1
        ;;
esac
