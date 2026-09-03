#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${UID} -ne 0 || ${EUID} -ne 0 ]]; then
    echo "Run this backup-health installer as root." >&2
    exit 1
fi

readonly requested_root=${1:-}
if [[ -z ${requested_root} ]]; then
    echo "Usage: $0 /path/to/reviewed/repository" >&2
    exit 2
fi
if [[ -L ${requested_root} || ! -d ${requested_root} ]]; then
    echo "The reviewed repository root must be a non-symlink directory." >&2
    exit 1
fi
readonly repository_root=$(realpath -e "${requested_root}")
readonly lock_path=/run/lock/careertrellis-pgbackrest-repository.lock
repository_permissions=$(stat -c '%A' "${repository_root}")
if [[ $(stat -c '%U:%G' "${repository_root}") != root:root || \
    ${repository_permissions:5:1} == w || ${repository_permissions:8:1} == w ]]; then
    echo "The reviewed repository root must be root-owned and not group- or world-writable." >&2
    exit 1
fi
readonly repository_identity=$(stat -Lc '%d:%i' "${repository_root}")
exec {repository_fd}<"${repository_root}"
readonly repository_fd
readonly repository_source=/proc/self/fd/${repository_fd}
if [[ ! ${repository_identity} =~ ^[0-9]+:[0-9]+$ || \
      $(stat -Lc '%d:%i' "${repository_source}" 2>/dev/null || true) != "${repository_identity}" ]]; then
    echo "The reviewed repository root changed while it was being bound." >&2
    exit 1
fi

for command_path in \
    /usr/bin/bash \
    /usr/bin/env \
    /usr/bin/findmnt \
    /usr/bin/flock \
    /usr/bin/find \
    /usr/bin/grep \
    /usr/bin/jq \
    /usr/bin/pgbackrest \
    /usr/bin/psql \
    /usr/bin/python3 \
    /usr/bin/sha256sum \
    /usr/bin/setpriv \
    /usr/bin/systemctl \
    /usr/bin/systemd-analyze \
    /usr/local/bin/rclone \
    /usr/lib/postgresql/18/bin/pg_ctl \
    /usr/lib/postgresql/18/bin/pg_checksums; do
    if [[ ! -x ${command_path} ]]; then
        echo "A required backup-health runtime command is unavailable." >&2
        exit 1
    fi
done

readonly -a source_scripts=(
    deploy/scripts/run-pgbackrest-backup.sh
    deploy/scripts/mirror-pgbackrest-onedrive.sh
    deploy/scripts/check-pgbackrest-mirror.sh
    deploy/scripts/test-pgbackrest-restore.sh
    deploy/scripts/manage-backup-health.sh
)
readonly -a source_units=(
    deploy/systemd/careertrellis-pgbackrest-full.service
    deploy/systemd/careertrellis-pgbackrest-full.timer
    deploy/systemd/careertrellis-pgbackrest-differential.service
    deploy/systemd/careertrellis-pgbackrest-differential.timer
    deploy/systemd/careertrellis-pgbackrest-onedrive.service
    deploy/systemd/careertrellis-pgbackrest-onedrive.timer
    deploy/systemd/careertrellis-pgbackrest-inventory.service
    deploy/systemd/careertrellis-pgbackrest-inventory.timer
    deploy/systemd/careertrellis-pgbackrest-restore.service
    deploy/systemd/careertrellis-pgbackrest-restore.timer
    deploy/systemd/careertrellis-backup-health.service
    deploy/systemd/careertrellis-backup-health.timer
    deploy/systemd/careertrellis-backup-health-alert@.service
)
readonly source_core=backend/backup_health.py
readonly -a helper_target_paths=(
    /usr/local/libexec/careertrellis/backup_health.py
    /usr/local/sbin/careertrellis-run-pgbackrest-backup
    /usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive
    /usr/local/sbin/careertrellis-check-pgbackrest-mirror
    /usr/local/sbin/careertrellis-test-pgbackrest-restore
    /usr/local/sbin/careertrellis-backup-health
)
readonly -a unit_target_paths=(
    /etc/systemd/system/careertrellis-pgbackrest-full.service
    /etc/systemd/system/careertrellis-pgbackrest-full.timer
    /etc/systemd/system/careertrellis-pgbackrest-differential.service
    /etc/systemd/system/careertrellis-pgbackrest-differential.timer
    /etc/systemd/system/careertrellis-pgbackrest-onedrive.service
    /etc/systemd/system/careertrellis-pgbackrest-onedrive.timer
    /etc/systemd/system/careertrellis-pgbackrest-inventory.service
    /etc/systemd/system/careertrellis-pgbackrest-inventory.timer
    /etc/systemd/system/careertrellis-pgbackrest-restore.service
    /etc/systemd/system/careertrellis-pgbackrest-restore.timer
    /etc/systemd/system/careertrellis-backup-health.service
    /etc/systemd/system/careertrellis-backup-health.timer
    /etc/systemd/system/careertrellis-backup-health-alert@.service
)
readonly -a timer_units=(
    careertrellis-pgbackrest-full.timer
    careertrellis-pgbackrest-differential.timer
    careertrellis-pgbackrest-onedrive.timer
    careertrellis-pgbackrest-inventory.timer
    careertrellis-pgbackrest-restore.timer
    careertrellis-backup-health.timer
)
readonly -a established_timer_units=(
    careertrellis-pgbackrest-full.timer
    careertrellis-pgbackrest-differential.timer
    careertrellis-pgbackrest-onedrive.timer
)
readonly -a new_timer_units=(
    careertrellis-pgbackrest-inventory.timer
    careertrellis-pgbackrest-restore.timer
    careertrellis-backup-health.timer
)

readonly verification_parent=/var/tmp
if [[ -L ${verification_parent} || ! -d ${verification_parent} || \
      $(realpath -e "${verification_parent}" 2>/dev/null || true) != "${verification_parent}" || \
      $(stat -c '%a:%U:%G' "${verification_parent}" 2>/dev/null || true) != 1777:root:root ]]; then
    echo "The temporary unit-verification parent was unsafe." >&2
    exit 1
fi
if ! verification_mount_options=$(
    /usr/bin/findmnt --noheadings --output OPTIONS --target "${verification_parent}"
) || [[ -z ${verification_mount_options} || ${verification_mount_options} == *$'\n'* ]]; then
    echo "The temporary unit-verification filesystem could not be validated." >&2
    exit 1
fi
case ",${verification_mount_options}," in
    *,noexec,*)
        echo "The temporary unit-verification filesystem does not permit executable checks." >&2
        exit 1
        ;;
esac
readonly verification_mount_options

verification_root=$(mktemp -d "${verification_parent}/careertrellis-backup-health-verify.XXXXXX")
verification_root=$(realpath -e "${verification_root}")
if [[ ${verification_root} != "${verification_parent}"/careertrellis-backup-health-verify.* || \
      -L ${verification_root} || \
    $(stat -c '%a:%U:%G' "${verification_root}" 2>/dev/null || true) != 700:root:root ]]; then
    echo "The temporary unit-verification root was unsafe." >&2
    exit 1
fi
readonly verification_root_identity=$(stat -c '%d:%i' "${verification_root}")
if [[ ! ${verification_root_identity} =~ ^[0-9]+:[0-9]+$ ]]; then
    echo "The temporary unit-verification root identity was invalid." >&2
    exit 1
fi
cleanup_verification_root() {
    local exit_status=$?
    trap - EXIT
    if [[ -n ${verification_root:-} && \
          ${verification_root} == "${verification_parent}"/careertrellis-backup-health-verify.* && \
          ! -L ${verification_root} && -d ${verification_root} && \
          $(stat -c '%d:%i' "${verification_root}" 2>/dev/null || true) == "${verification_root_identity}" && \
          $(stat -c '%a:%U:%G' "${verification_root}" 2>/dev/null || true) == 700:root:root ]]; then
        if ! rm -rf --one-file-system -- "${verification_root}"; then
            echo "The temporary backup-health verification bundle could not be removed." >&2
            exit_status=1
        fi
    elif [[ -n ${verification_root:-} ]]; then
        echo "The temporary backup-health verification bundle changed unexpectedly." >&2
        exit_status=1
    fi
    exit "${exit_status}"
}
trap cleanup_verification_root EXIT
install -d -o root -g root -m 0755 \
    "${verification_root}/etc" \
    "${verification_root}/etc/systemd" \
    "${verification_root}/etc/systemd/system" \
    "${verification_root}/usr" \
    "${verification_root}/usr/local" \
    "${verification_root}/usr/local/libexec" \
    "${verification_root}/usr/local/libexec/careertrellis" \
    "${verification_root}/usr/local/sbin"
printf 'root:x:0:0:root:/root:/usr/sbin/nologin\n' >"${verification_root}/etc/passwd"
printf 'root:x:0:\n' >"${verification_root}/etc/group"
chmod 0600 "${verification_root}/etc/passwd" "${verification_root}/etc/group"

validate_source() {
    local relative_path=$1
    local path directory permissions ownership
    local -a components=()
    local component_index
    if [[ ${relative_path} == /* || ${relative_path} == ../* || \
          ${relative_path} == */../* || ${relative_path} == */.. || \
          ${relative_path} == *//* ]]; then
        echo "A reviewed source path was invalid." >&2
        return 1
    fi
    IFS='/' read -r -a components <<<"${relative_path}"
    directory=${repository_source}
    for (( component_index=0; component_index < ${#components[@]} - 1; component_index++ )); do
        directory=${directory}/${components[${component_index}]}
        if [[ -L ${directory} || ! -d ${directory} ]]; then
            echo "A reviewed source directory is missing or is a symbolic link." >&2
            return 1
        fi
        permissions=$(stat -c '%A' "${directory}")
        ownership=$(stat -c '%U:%G' "${directory}")
        if [[ ${ownership} != root:root || ${permissions:5:1} == w || ${permissions:8:1} == w ]]; then
            echo "A reviewed source directory must be root-owned and not group- or world-writable." >&2
            return 1
        fi
    done
    path=${repository_source}/${relative_path}
    if [[ -L ${path} || ! -f ${path} ]]; then
        echo "A required reviewed source is missing or is a symbolic link." >&2
        return 1
    fi
    permissions=$(stat -c '%A' "${path}")
    ownership=$(stat -c '%U:%G' "${path}")
    if [[ ${ownership} != root:root || ${permissions:5:1} == w || ${permissions:8:1} == w ]]; then
        echo "A reviewed source must be root-owned and not group- or world-writable." >&2
        return 1
    fi
}

for relative_path in "${source_scripts[@]}" "${source_units[@]}" "${source_core}"; do
    validate_source "${relative_path}"
done
install -o root -g root -m 0644 \
    "${repository_source}/${source_core}" \
    "${verification_root}${helper_target_paths[0]}"
for source_index in "${!source_scripts[@]}"; do
    install -o root -g root -m 0755 \
        "${repository_source}/${source_scripts[${source_index}]}" \
        "${verification_root}${helper_target_paths[$(( source_index + 1 ))]}"
done
for source_index in "${!source_units[@]}"; do
    install -o root -g root -m 0644 \
        "${repository_source}/${source_units[${source_index}]}" \
        "${verification_root}${unit_target_paths[${source_index}]}"
done
exec {repository_fd}<&-
# Reviewed source ingestion is complete. Every later check and installation
# consumes only these root-owned staged bytes.
for target_path in "${helper_target_paths[@]:1}"; do
    /usr/bin/bash -n "${verification_root}${target_path}"
done
/usr/bin/python3 -c 'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), sys.argv[1], "exec")' \
    "${verification_root}${helper_target_paths[0]}"

# Verify the exact staged units before any installed helper or unit target is
# changed. The alternate root supplies their fixed absolute helper paths.
if ! /usr/bin/env -u SYSTEMD_UNIT_PATH \
    /usr/bin/systemd-analyze --recursive-errors=no --root="${verification_root}" verify \
    careertrellis-pgbackrest-full.service \
    careertrellis-pgbackrest-full.timer \
    careertrellis-pgbackrest-differential.service \
    careertrellis-pgbackrest-differential.timer \
    careertrellis-pgbackrest-onedrive.service \
    careertrellis-pgbackrest-onedrive.timer \
    careertrellis-pgbackrest-inventory.service \
    careertrellis-pgbackrest-inventory.timer \
    careertrellis-pgbackrest-restore.service \
    careertrellis-pgbackrest-restore.timer \
    careertrellis-backup-health.service \
    careertrellis-backup-health.timer \
    careertrellis-backup-health-alert@.service; then
    echo "Reviewed backup-health unit verification failed before installation." >&2
    exit 1
fi

for service_name in \
    careertrellis-pgbackrest-full.service \
    careertrellis-pgbackrest-differential.service \
    careertrellis-pgbackrest-onedrive.service \
    careertrellis-pgbackrest-inventory.service \
    careertrellis-pgbackrest-restore.service; do
    if systemctl is-active --quiet "${service_name}"; then
        echo "A repository operation is active; installation was not started." >&2
        exit 75
    fi
done
for timer_unit in "${established_timer_units[@]}"; do
    if ! systemctl is-enabled --quiet "${timer_unit}" 2>/dev/null || \
        ! systemctl is-active --quiet "${timer_unit}" 2>/dev/null; then
        echo "An established backup timer is not enabled and active; installation was not started." >&2
        exit 1
    fi
done

umask 077
if [[ -e ${lock_path} || -L ${lock_path} ]]; then
    if [[ -L ${lock_path} || ! -f ${lock_path} || \
        $(stat -c '%a:%U:%G' "${lock_path}" 2>/dev/null || true) != 600:root:root ]]; then
        echo "The canonical repository lock has an unsafe owner, mode, or type." >&2
        exit 1
    fi
else
    install -o root -g root -m 0600 /dev/null "${lock_path}"
fi
exec 9<>"${lock_path}"
if ! flock --nonblock 9; then
    echo "Another repository operation is active; installation was not started." >&2
    exit 75
fi

ensure_directory() {
    local path=$1
    local mode=$2
    local owner=$3
    local group=$4
    if [[ -e ${path} || -L ${path} ]]; then
        if [[ -L ${path} || ! -d ${path} || \
            $(stat -c '%a:%U:%G' "${path}" 2>/dev/null || true) != "${mode}:${owner}:${group}" ]]; then
            echo "A protected backup-health directory has an unsafe owner, mode, or type." >&2
            return 1
        fi
    else
        install -d -o "${owner}" -g "${group}" -m "0${mode}" "${path}"
    fi
}

if [[ -e /var/backups/careertrellis || -L /var/backups/careertrellis ]]; then
    ensure_directory /var/backups/careertrellis 700 root root
else
    ensure_directory /var/backups/careertrellis 700 root root
fi
ensure_directory /var/backups/careertrellis/backup-health 700 root root
ensure_directory /var/backups/careertrellis/backup-health/operations 700 root root

if [[ ! -e /var/lib/careertrellis && ! -L /var/lib/careertrellis ]]; then
    install -d -o root -g root -m 0755 /var/lib/careertrellis
fi
if [[ -L /var/lib/careertrellis || ! -d /var/lib/careertrellis ]]; then
    echo "The CareerTrellis state parent must be a non-symlink directory." >&2
    exit 1
fi
ensure_directory /var/lib/careertrellis/backup-health 700 root root
ensure_directory /var/lib/careertrellis/backup-health/database 700 root root
ensure_directory /var/lib/careertrellis/backup-health/database/alerts 700 root root
ensure_directory /var/lib/careertrellis/backup-health/database/unit-alerts 700 root root

readonly restore_root=/var/lib/postgresql/careertrellis-restore-tests
restore_root_was_absent=false
restore_root_needs_migration=false
if [[ ! -e ${restore_root} && ! -L ${restore_root} ]]; then
    restore_root_was_absent=true
else
    restore_root_state=$(stat -c '%a:%U:%G' "${restore_root}" 2>/dev/null || true)
    if [[ -L ${restore_root} || ! -d ${restore_root} || \
          ( ${restore_root_state} != 710:root:postgres && \
            ${restore_root_state} != 700:postgres:postgres ) ]]; then
        echo "The disposable restore root has an unsafe owner, mode, or type." >&2
        exit 1
    fi
    if [[ ${restore_root_state} == 700:postgres:postgres ]]; then
        restore_root_needs_migration=true
    fi
fi

if [[ -e /usr/local/libexec/careertrellis || -L /usr/local/libexec/careertrellis ]]; then
    if [[ -L /usr/local/libexec/careertrellis || \
        ! -d /usr/local/libexec/careertrellis || \
        $(stat -c '%a:%U:%G' /usr/local/libexec/careertrellis 2>/dev/null || true) != 755:root:root ]]; then
        echo "The installed core directory has an unsafe owner, mode, or type." >&2
        exit 1
    fi
else
    install -d -o root -g root -m 0755 /usr/local/libexec/careertrellis
fi

for target_path in "${helper_target_paths[@]}" "${unit_target_paths[@]}"; do
    if [[ -L ${target_path} || ( -e ${target_path} && ! -f ${target_path} ) ]]; then
        echo "An installed backup-health target has an unsafe type." >&2
        exit 1
    fi
done

declare -A timer_was_enabled=()
declare -A timer_was_active=()
for timer_unit in "${timer_units[@]}"; do
    if systemctl is-enabled --quiet "${timer_unit}" 2>/dev/null; then
        timer_was_enabled["${timer_unit}"]=yes
    else
        timer_was_enabled["${timer_unit}"]=no
    fi
    if systemctl is-active --quiet "${timer_unit}" 2>/dev/null; then
        timer_was_active["${timer_unit}"]=yes
    else
        timer_was_active["${timer_unit}"]=no
    fi
done

rollback_root=$(mktemp -d /run/careertrellis-backup-health-rollback.XXXXXX)
rollback_root=$(realpath -e "${rollback_root}")
if [[ ${rollback_root} != /run/careertrellis-backup-health-rollback.* || \
    -L ${rollback_root} || \
    $(stat -c '%a:%U:%G' "${rollback_root}" 2>/dev/null || true) != 700:root:root ]]; then
    echo "The temporary rollback root was unsafe." >&2
    exit 1
fi
cleanup_bundle_on_error() {
    local exit_status=$?
    trap - ERR
    rm -rf --one-file-system -- "${rollback_root}" 2>/dev/null || true
    exit "${exit_status}"
}
trap cleanup_bundle_on_error ERR
install -d -o root -g root -m 0700 "${rollback_root}/files"
rollback_metadata="${rollback_root}/targets.tsv"
install -o root -g root -m 0600 /dev/null "${rollback_metadata}"
target_index=0
for target_path in "${helper_target_paths[@]}" "${unit_target_paths[@]}"; do
    backup_name=$(printf 'target-%03d' "${target_index}")
    if [[ -e ${target_path} ]]; then
        cp --archive --preserve=all --no-dereference \
            "${target_path}" "${rollback_root}/files/${backup_name}"
        printf 'present\t%s\t%s\n' "${backup_name}" "${target_path}" >>"${rollback_metadata}"
    else
        printf 'absent\t%s\t%s\n' "${backup_name}" "${target_path}" >>"${rollback_metadata}"
    fi
    target_index=$(( target_index + 1 ))
done
expected_target_count=$(( ${#helper_target_paths[@]} + ${#unit_target_paths[@]} ))
if [[ ${target_index} -ne ${expected_target_count} ]] || \
    [[ $(wc -l <"${rollback_metadata}") -ne ${expected_target_count} ]]; then
    echo "The rollback target inventory was incomplete." >&2
    false
fi

rollback_needed=true
units_touched=false
restore_root_creation_started=false
restore_root_created_identity=
restore_root_migration_started=false
trap - ERR
rollback_installation() {
    local exit_status=$?
    local rollback_failed=false state backup_name target_path backup_path known_target timer_unit
    local current_restore_root_identity
    trap - ERR
    set +e
    if [[ ${rollback_needed} != true ]]; then
        exit "${exit_status}"
    fi

    for timer_unit in "${new_timer_units[@]}"; do
        if [[ ${timer_was_active[${timer_unit}]} == no ]] && \
            systemctl is-active --quiet "${timer_unit}" 2>/dev/null; then
            systemctl stop "${timer_unit}" >/dev/null 2>&1 || rollback_failed=true
        fi
    done
    for timer_unit in "${timer_units[@]}"; do
        if [[ ${timer_was_enabled[${timer_unit}]} == no ]] && \
            systemctl is-enabled --quiet "${timer_unit}" 2>/dev/null; then
            systemctl disable "${timer_unit}" >/dev/null 2>&1 || rollback_failed=true
        fi
    done

    while IFS=$'\t' read -r state backup_name target_path; do
        known_target=false
        for candidate_path in "${helper_target_paths[@]}" "${unit_target_paths[@]}"; do
            if [[ ${target_path} == "${candidate_path}" ]]; then
                known_target=true
                break
            fi
        done
        if [[ ${known_target} != true || ! ${backup_name} =~ ^target-[0-9]{3}$ ]]; then
            rollback_failed=true
            continue
        fi
        case "${state}" in
            present)
                backup_path="${rollback_root}/files/${backup_name}"
                if [[ -L ${backup_path} || ! -f ${backup_path} ]]; then
                    rollback_failed=true
                    continue
                fi
                rm -f -- "${target_path}" || {
                    rollback_failed=true
                    continue
                }
                cp --archive --preserve=all --no-dereference \
                    "${backup_path}" "${target_path}" || rollback_failed=true
                ;;
            absent)
                rm -f -- "${target_path}" || rollback_failed=true
                ;;
            *)
                rollback_failed=true
                ;;
        esac
    done <"${rollback_metadata}"

    if [[ ${units_touched} == true ]]; then
        systemctl daemon-reload >/dev/null 2>&1 || rollback_failed=true
    fi
    if [[ ${restore_root_creation_started} == true ]]; then
        current_restore_root_identity=$(stat -c '%d:%i' "${restore_root}" 2>/dev/null || true)
        if [[ -L ${restore_root} || ! -d ${restore_root} || \
              ${current_restore_root_identity} != "${restore_root_created_identity}" || \
              $(stat -c '%a:%U:%G' "${restore_root}" 2>/dev/null || true) != 710:root:postgres ]] || \
            ! rmdir -- "${restore_root}"; then
            rollback_failed=true
        fi
    elif [[ ${restore_root_migration_started} == true ]]; then
        if [[ -L ${restore_root} || ! -d ${restore_root} ]] || \
            ! chown postgres:postgres "${restore_root}" || \
            ! chmod 0700 "${restore_root}" || \
            [[ $(stat -c '%a:%U:%G' "${restore_root}" 2>/dev/null || true) != 700:postgres:postgres ]]; then
            rollback_failed=true
        fi
    fi
    if [[ ${rollback_failed} == false ]]; then
        rm -rf --one-file-system -- "${rollback_root}" || rollback_failed=true
    fi
    if [[ ${rollback_failed} == true ]]; then
        echo "Backup-health installation failed; the root-only rollback bundle was retained." >&2
    else
        echo "Backup-health installation failed and prior installed targets were restored." >&2
    fi
    if [[ ${exit_status} -eq 0 ]]; then
        exit_status=1
    fi
    exit "${exit_status}"
}
trap rollback_installation ERR

if [[ ${restore_root_was_absent} == true ]]; then
    restore_root_creation_started=true
    install -d -o root -g postgres -m 0710 "${restore_root}"
    restore_root_created_identity=$(stat -c '%d:%i' "${restore_root}" 2>/dev/null || true)
    if [[ ! ${restore_root_created_identity} =~ ^[0-9]+:[0-9]+$ || \
          -L ${restore_root} || ! -d ${restore_root} || \
          $(stat -c '%a:%U:%G' "${restore_root}" 2>/dev/null || true) != 710:root:postgres ]]; then
        echo "The disposable restore root could not be created safely." >&2
        false
    fi
fi
if [[ ${restore_root_needs_migration} == true ]]; then
    restore_root_migration_started=true
    chown root:postgres "${restore_root}"
    chmod 0710 "${restore_root}"
    if [[ $(stat -c '%a:%U:%G' "${restore_root}" 2>/dev/null || true) != 710:root:postgres ]]; then
        echo "The disposable restore root could not be migrated safely." >&2
        false
    fi
fi

install -o root -g root -m 0644 \
    "${verification_root}${helper_target_paths[0]}" \
    "${helper_target_paths[0]}"
for target_path in "${helper_target_paths[@]:1}"; do
    install -o root -g root -m 0755 \
        "${verification_root}${target_path}" \
        "${target_path}"
done

units_touched=true
for target_path in "${unit_target_paths[@]}"; do
    install -o root -g root -m 0644 \
        "${verification_root}${target_path}" \
        "${target_path}"
done

systemctl daemon-reload
systemctl enable \
    careertrellis-pgbackrest-inventory.timer \
    careertrellis-pgbackrest-restore.timer \
    careertrellis-backup-health.timer >/dev/null

# Starting these timer units does not start their services. The inventory and
# restore timers have explicit future-only monotonic first delays, so installer
# activation cannot launch either remote traversal. Existing backup and mirror
# timers retain their current live activation state.
systemctl start \
    careertrellis-pgbackrest-inventory.timer \
    careertrellis-pgbackrest-restore.timer \
    careertrellis-backup-health.timer

rollback_needed=false
trap - ERR
if ! rm -rf --one-file-system -- "${rollback_root}"; then
    echo "Backup-health installation succeeded; temporary rollback cleanup requires operator review." >&2
fi
printf '{"operation":"install_backup_health_monitor","result":"ok","surface":"database","remote_operations_started":false}\n'
