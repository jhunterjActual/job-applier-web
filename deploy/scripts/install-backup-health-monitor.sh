#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
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

for command_path in \
    /usr/bin/bash \
    /usr/bin/flock \
    /usr/bin/find \
    /usr/bin/jq \
    /usr/bin/pgbackrest \
    /usr/bin/psql \
    /usr/bin/python3 \
    /usr/bin/sha256sum \
    /usr/sbin/runuser \
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

validate_source() {
    local relative_path=$1
    local path resolved permissions ownership
    path="${repository_root}/${relative_path}"
    if [[ -L ${path} || ! -f ${path} ]]; then
        echo "A required reviewed source is missing or is a symbolic link." >&2
        return 1
    fi
    resolved=$(realpath -e "${path}")
    if [[ ${resolved} != "${repository_root}/"* ]]; then
        echo "A reviewed source escaped the repository root." >&2
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
for relative_path in "${source_scripts[@]}"; do
    /usr/bin/bash -n "${repository_root}/${relative_path}"
done
/usr/bin/python3 -c 'import pathlib, sys; compile(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"), sys.argv[1], "exec")' \
    "${repository_root}/${source_core}"

# Verify exact staged copies of the reviewed units before any installed helper
# or unit target is changed. The alternate root supplies the absolute helper
# paths referenced by ExecStart without touching the live filesystem.
verification_root=$(mktemp -d /run/careertrellis-backup-health-verify.XXXXXX)
verification_root=$(realpath -e "${verification_root}")
if [[ ${verification_root} != /run/careertrellis-backup-health-verify.* || \
    -L ${verification_root} || \
    $(stat -c '%a:%U:%G' "${verification_root}" 2>/dev/null || true) != 700:root:root ]]; then
    echo "The temporary unit-verification root was unsafe." >&2
    exit 1
fi
cleanup_verification_on_error() {
    local exit_status=$?
    trap - ERR
    rm -rf --one-file-system -- "${verification_root}" 2>/dev/null || true
    exit "${exit_status}"
}
trap cleanup_verification_on_error ERR
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
install -o root -g root -m 0644 \
    "${repository_root}/${source_core}" \
    "${verification_root}/usr/local/libexec/careertrellis/backup_health.py"
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/run-pgbackrest-backup.sh" \
    "${verification_root}/usr/local/sbin/careertrellis-run-pgbackrest-backup"
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/mirror-pgbackrest-onedrive.sh" \
    "${verification_root}/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive"
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/check-pgbackrest-mirror.sh" \
    "${verification_root}/usr/local/sbin/careertrellis-check-pgbackrest-mirror"
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/test-pgbackrest-restore.sh" \
    "${verification_root}/usr/local/sbin/careertrellis-test-pgbackrest-restore"
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/manage-backup-health.sh" \
    "${verification_root}/usr/local/sbin/careertrellis-backup-health"
for relative_path in "${source_units[@]}"; do
    install -o root -g root -m 0644 \
        "${repository_root}/${relative_path}" \
        "${verification_root}/etc/systemd/system/${relative_path##*/}"
done
if ! systemd-analyze --root="${verification_root}" verify \
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
    trap - ERR
    rm -rf --one-file-system -- "${verification_root}"
    echo "Reviewed backup-health unit verification failed before installation." >&2
    exit 1
fi
rm -rf --one-file-system -- "${verification_root}"
trap - ERR
verification_root=

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

if [[ ! -e /var/lib/postgresql/careertrellis-restore-tests && \
    ! -L /var/lib/postgresql/careertrellis-restore-tests ]]; then
    install -d -o postgres -g postgres -m 0700 /var/lib/postgresql/careertrellis-restore-tests
fi
if [[ -L /var/lib/postgresql/careertrellis-restore-tests || \
    ! -d /var/lib/postgresql/careertrellis-restore-tests || \
    $(stat -c '%a:%U:%G' /var/lib/postgresql/careertrellis-restore-tests 2>/dev/null || true) != 700:postgres:postgres ]]; then
    echo "The disposable restore root has an unsafe owner, mode, or type." >&2
    exit 1
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
trap - ERR
rollback_installation() {
    local exit_status=$?
    local rollback_failed=false state backup_name target_path backup_path known_target timer_unit
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

install -o root -g root -m 0644 \
    "${repository_root}/${source_core}" \
    /usr/local/libexec/careertrellis/backup_health.py
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/run-pgbackrest-backup.sh" \
    /usr/local/sbin/careertrellis-run-pgbackrest-backup
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/mirror-pgbackrest-onedrive.sh" \
    /usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/check-pgbackrest-mirror.sh" \
    /usr/local/sbin/careertrellis-check-pgbackrest-mirror
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/test-pgbackrest-restore.sh" \
    /usr/local/sbin/careertrellis-test-pgbackrest-restore
install -o root -g root -m 0755 \
    "${repository_root}/deploy/scripts/manage-backup-health.sh" \
    /usr/local/sbin/careertrellis-backup-health

units_touched=true
for relative_path in "${source_units[@]}"; do
    install -o root -g root -m 0644 \
        "${repository_root}/${relative_path}" \
        "/etc/systemd/system/${relative_path##*/}"
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
