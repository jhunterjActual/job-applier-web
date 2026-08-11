#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this OneDrive mirror setup as root." >&2
    exit 1
fi

config=/etc/careertrellis/rclone.conf
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
if [[ $'\n'${remote_inventory}$'\n' != *$'\n'onedrive:$'\n'* ]]; then
    echo "Create a remote named onedrive with: sudo rclone config --config ${config}" >&2
    exit 1
fi

${rclone_binary} lsd onedrive: --max-depth 1 --config "${config}" >/dev/null
/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive
systemctl daemon-reload
systemctl enable --now careertrellis-pgbackrest-onedrive.timer

echo "The encrypted pgBackRest repository was copied to OneDrive and its daily timer is enabled."
