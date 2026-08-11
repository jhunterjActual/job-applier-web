#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this one-time database-host provisioning script as root." >&2
    exit 1
fi

admin_public_cidr=${CAREERTRELLIS_ADMIN_PUBLIC_CIDR:-}
app_vpc_ip=${CAREERTRELLIS_APP_VPC_IP:-}
vpc_interface=${CAREERTRELLIS_VPC_INTERFACE:-eth1}
admin_user=${CAREERTRELLIS_ADMIN_USER:-hunter}

if [[ ! ${admin_public_cidr} =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/32$ ]]; then
    echo "CAREERTRELLIS_ADMIN_PUBLIC_CIDR must be one IPv4 /32 CIDR." >&2
    exit 1
fi
if [[ ! ${app_vpc_ip} =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    echo "CAREERTRELLIS_APP_VPC_IP must be one IPv4 address." >&2
    exit 1
fi
if ! ip link show "${vpc_interface}" >/dev/null 2>&1; then
    echo "The VPC interface does not exist: ${vpc_interface}" >&2
    exit 1
fi
if [[ ! -s /root/.ssh/authorized_keys ]]; then
    echo "Root's SSH authorized_keys is required to seed the administrator account." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y dist-upgrade
apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    fail2ban \
    gnupg \
    jq \
    rsync \
    unattended-upgrades \
    ufw \
    wireguard

if ! id "${admin_user}" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --groups sudo "${admin_user}"
fi
passwd --lock "${admin_user}" >/dev/null
install -d -o "${admin_user}" -g "${admin_user}" -m 0700 "/home/${admin_user}/.ssh"
install -o "${admin_user}" -g "${admin_user}" -m 0600 \
    /root/.ssh/authorized_keys "/home/${admin_user}/.ssh/authorized_keys"
cat >/etc/sudoers.d/90-careertrellis-admin <<EOF
${admin_user} ALL=(ALL:ALL) NOPASSWD:ALL
EOF
chmod 0440 /etc/sudoers.d/90-careertrellis-admin
visudo --check --file=/etc/sudoers.d/90-careertrellis-admin >/dev/null

cat >/etc/ssh/sshd_config.d/60-careertrellis-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
PermitTunnel no
EOF
install -d -o root -g root -m 0755 /run/sshd
sshd -t

cat >/etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
bantime = 1h
findtime = 10m
maxretry = 5
backend = systemd
EOF

cat >/etc/apt/apt.conf.d/52careertrellis-unattended-upgrades <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
EOF

if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
    if [[ ! -e /swapfile ]]; then
        fallocate -l 2G /swapfile
        chmod 0600 /swapfile
        mkswap /swapfile >/dev/null
    fi
    if ! grep -qE '^/swapfile\s' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' >>/etc/fstab
    fi
    swapon /swapfile
fi
cat >/etc/sysctl.d/60-careertrellis-database.conf <<'EOF'
vm.swappiness = 10
EOF
sysctl --system >/dev/null

ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow from "${admin_public_cidr}" to any port 22 proto tcp comment 'restricted SSH' >/dev/null
ufw allow in on "${vpc_interface}" from "${app_vpc_ip}" to any port 51820 proto udp comment 'WireGuard from app VPC' >/dev/null
ufw --force enable >/dev/null

install -d -o root -g root -m 0700 /etc/wireguard
if [[ ! -s /etc/wireguard/private.key ]]; then
    umask 077
    wg genkey >/etc/wireguard/private.key
fi
wg pubkey </etc/wireguard/private.key >/etc/wireguard/public.key
chmod 0600 /etc/wireguard/private.key
chmod 0644 /etc/wireguard/public.key

systemctl enable fail2ban unattended-upgrades >/dev/null
systemctl restart fail2ban unattended-upgrades
systemctl reload ssh

echo "Database host baseline is installed. WireGuard public key:"
cat /etc/wireguard/public.key
if [[ -f /var/run/reboot-required ]]; then
    echo "REBOOT_REQUIRED"
fi
