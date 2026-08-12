#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this WireGuard configuration script as root." >&2
    exit 1
fi

role=${1:-}
peer_public_key=${2:-}
peer_vpc_endpoint=${3:-}
listen_port=51820

if ! command -v wg >/dev/null 2>&1; then
    echo "WireGuard is not installed." >&2
    exit 1
fi

install -d -o root -g root -m 0700 /etc/wireguard
if [[ ! -s /etc/wireguard/private.key ]]; then
    umask 077
    wg genkey >/etc/wireguard/private.key
fi
wg pubkey </etc/wireguard/private.key >/etc/wireguard/public.key
chmod 0600 /etc/wireguard/private.key
chmod 0644 /etc/wireguard/public.key

if [[ ${role} == public-key ]]; then
    cat /etc/wireguard/public.key
    exit 0
fi
if [[ ${role} != app && ${role} != database ]]; then
    echo "Usage: $0 <public-key|app|database> [peer-public-key] [peer-vpc-ip]" >&2
    exit 1
fi
if [[ ! ${peer_public_key} =~ ^[A-Za-z0-9+/]{43}=$ ]]; then
    echo "The peer WireGuard public key is invalid." >&2
    exit 1
fi
if [[ ${role} == app && ! ${peer_vpc_endpoint} =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    echo "The app role requires the database VPC endpoint." >&2
    exit 1
fi

private_key=$(</etc/wireguard/private.key)
temporary_config=$(mktemp /etc/wireguard/.wg0.conf.XXXXXX)
trap 'rm -f -- "${temporary_config}"' EXIT
chmod 0600 "${temporary_config}"

if [[ ${role} == app ]]; then
    cat >"${temporary_config}" <<EOF
[Interface]
Address = 10.70.0.1/30
ListenPort = ${listen_port}
PrivateKey = ${private_key}

[Peer]
PublicKey = ${peer_public_key}
AllowedIPs = 10.70.0.2/32
Endpoint = ${peer_vpc_endpoint}:${listen_port}
PersistentKeepalive = 25
EOF
else
    cat >"${temporary_config}" <<EOF
[Interface]
Address = 10.70.0.2/30
ListenPort = ${listen_port}
PrivateKey = ${private_key}

[Peer]
PublicKey = ${peer_public_key}
AllowedIPs = 10.70.0.1/32
EOF
fi

install -o root -g root -m 0600 "${temporary_config}" /etc/wireguard/wg0.conf
systemctl enable wg-quick@wg0 >/dev/null
systemctl restart wg-quick@wg0

echo "WireGuard ${role} interface configured. Local public key:"
cat /etc/wireguard/public.key
wg show wg0
