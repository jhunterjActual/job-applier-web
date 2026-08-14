#!/usr/bin/env bash

careertrellis_maintenance_lock_path=/run/lock/careertrellis-maintenance.lock

acquire_careertrellis_maintenance_lock() {
    local inherited_fd=${CAREERTRELLIS_MAINTENANCE_LOCK_FD:-}
    local inherited_target

    if [[ -n ${inherited_fd} ]]; then
        if [[ ${inherited_fd} != "10" || ! -e /proc/self/fd/10 ]]; then
            echo "The inherited maintenance lock is invalid." >&2
            return 1
        fi
        inherited_target=$(readlink -f /proc/self/fd/10 2>/dev/null || true)
        if [[ ${inherited_target} != "${careertrellis_maintenance_lock_path}" ]] || ! flock -n 10; then
            echo "The inherited maintenance lock is invalid." >&2
            return 1
        fi
        return
    fi

    exec 8>"${careertrellis_maintenance_lock_path}"
    if ! flock -n 8; then
        echo "Another CareerTrellis maintenance operation is running." >&2
        return 1
    fi
}
