#!/usr/bin/env bash
set -Eeuo pipefail

pre_cutover_state=sqlite-pre-cutover
hosted_state=postgres-r2-authoritative
hosted_capability=postgres-r2-authoritative-v1
authority_directory=/etc/careertrellis/authority
authority_parent=/etc/careertrellis
state_file=${authority_directory}/data-authority
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
    local capability_file
    local -a capability_lines=()
    validate_release_target_path "${target_release}" || return 1
    target_release=$(readlink -f "${target_release}")
    capability_file=${target_release}/deploy/hosted-authority.compatibility
    if [[ ! -f ${capability_file} || -L ${capability_file} || -x ${capability_file} ]]; then
        return 1
    fi
    mapfile -t capability_lines < "${capability_file}"
    [[ ${#capability_lines[@]} -eq 1 && ${capability_lines[0]} == "${hosted_capability}" ]]
}

require_hosted_release_capability() {
    local current_release
    if [[ ! -L ${release_root}/current ]]; then
        return 1
    fi
    current_release=$(readlink -f "${release_root}/current")
    validate_release_capability "${current_release}"
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
        return
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
    local state
    state=$(read_authority_state) || return 1
    if ! require_current_release_target; then
        echo "Hosted authority runtime validation failed." >&2
        return 1
    fi
    if [[ ${state} == "${pre_cutover_state}" ]]; then
        return
    fi
    if ! require_hosted_release_capability; then
        echo "Hosted authority runtime validation failed." >&2
        return 1
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

mark_hosted_authoritative() {
    local target_release=${1:-}
    local confirmation=${2:-}
    local state
    require_root
    if [[ ${confirmation} != "I_UNDERSTAND_SQLITE_WRITES_WILL_BE_DISABLED" ]]; then
        echo "Hosted authority confirmation is required." >&2
        return 1
    fi
    if [[ ${test_mode} == false ]]; then
        exec 8>/run/lock/careertrellis-maintenance.lock
        if ! flock -n 8; then
            echo "Another deployment, rollback, or import operation is running." >&2
            return 1
        fi
    fi
    state=$(read_authority_state) || return 1
    if [[ ${state} == "${hosted_state}" ]]; then
        return
    fi
    if ! validate_hosted_release "${target_release}"; then
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
        [[ $# -eq 2 ]] || { echo "Hosted authority confirmation is required." >&2; exit 1; }
        mark_hosted_authoritative "$1" "$2"
        ;;
    assert-code-rollback-allowed)
        [[ $# -eq 0 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        assert_pre_cutover "Hosted authority prevents code rollback."
        ;;
    assert-import-allowed)
        [[ $# -eq 0 ]] || { echo "Hosted authority command is invalid." >&2; exit 1; }
        assert_pre_cutover "Hosted authority prevents SQLite import."
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
        echo "Usage: $0 {initialize|status|mark-postgres-r2-authoritative RELEASE CONFIRMATION|assert-code-rollback-allowed|assert-import-allowed|assert-release-startable RELEASE|check-web-runtime|check-worker-runtime}" >&2
        exit 1
        ;;
esac
