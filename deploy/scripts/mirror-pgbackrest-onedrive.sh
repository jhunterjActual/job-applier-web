#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this OneDrive mirror as root." >&2
    exit 1
fi

config=/etc/careertrellis/rclone.conf
source_path=r2:job-applier-staging-db-backups/job-applier/staging/postgresql
destination_path=onedrive:job-applier-staging/postgresql
rclone_binary=/usr/local/bin/rclone

if [[ $(stat -c '%a:%U:%G' "${config}" 2>/dev/null || true) != 600:root:root ]]; then
    echo "The rclone configuration must be root:root with mode 0600." >&2
    exit 1
fi
if [[ ! -x ${rclone_binary} ]]; then
    echo "The verified rclone installation is missing." >&2
    exit 1
fi
remote_inventory=$(${rclone_binary} listremotes --config "${config}")
for remote_name in r2: onedrive:; do
    if [[ $'\n'${remote_inventory}$'\n' != *$'\n'${remote_name}$'\n'* ]]; then
        echo "Required rclone remote ${remote_name} is not configured." >&2
        exit 1
    fi
done

# Copy without deleting the destination. If the primary repository expires or
# loses objects, the secondary destination retains its older ciphertext.
${rclone_binary} copy \
    "${source_path}" \
    "${destination_path}" \
    --config "${config}" \
    --checkers 4 \
    --transfers 2 \
    --create-empty-src-dirs \
    --stats-one-line \
    --stats 30s

${rclone_binary} size "${destination_path}" --config "${config}"
