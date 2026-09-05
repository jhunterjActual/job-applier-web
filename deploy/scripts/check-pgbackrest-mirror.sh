#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this repository inventory check as root." >&2
    exit 1
fi

readonly config=/etc/careertrellis/rclone.conf
readonly rclone_binary=/usr/local/bin/rclone
readonly lock_path=/run/lock/careertrellis-pgbackrest-repository.lock
readonly core=/usr/local/libexec/careertrellis/backup_health.py
readonly generation_core=/usr/local/libexec/careertrellis/pgbackrest_inventory.py

for command_path in /usr/bin/flock /usr/bin/python3 "${rclone_binary}"; do
    if [[ ! -x ${command_path} ]]; then
        echo "A required inventory command is unavailable." >&2
        exit 1
    fi
done
if [[ -L ${config} || $(stat -c '%a:%U:%G' "${config}" 2>/dev/null || true) != 600:root:root ]]; then
    echo "The rclone configuration must be a root-owned, non-symlink 0600 file." >&2
    exit 1
fi
for helper in "${core}" "${generation_core}"; do
    if [[ -L ${helper} || ! -f ${helper} || $(stat -c '%a:%U:%G' "${helper}" 2>/dev/null || true) != 644:root:root ]]; then
        echo "A reviewed inventory helper is unavailable." >&2
        exit 1
    fi
done
if [[ -L ${lock_path} || ! -f ${lock_path} || \
    $(stat -c '%a:%U:%G' "${lock_path}" 2>/dev/null || true) != 600:root:root ]]; then
    echo "The canonical repository lock has an unsafe owner, mode, or type." >&2
    exit 1
fi

umask 077
exec 9<>"${lock_path}"
if ! flock --nonblock 9; then
    echo "Another repository operation is active; this inventory was not started." >&2
    exit 75
fi

# The completed mirror receipt commits the private source file set. Immutable
# files receive full --download verification with exact match coverage; mutable
# metadata is downloaded against its captured SHA-256 values. Missing generation
# evidence fails closed until an ordinary mirror records it.
/usr/bin/python3 -B "${generation_core}" inventory
