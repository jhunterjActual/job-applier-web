#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this credential-recipient setup as root." >&2
    exit 1
fi
if ! command -v age-keygen >/dev/null 2>&1; then
    echo "age is not installed." >&2
    exit 1
fi

install -d -o root -g root -m 0700 /etc/careertrellis
identity_file=/etc/careertrellis/credential-transfer.agekey
if [[ ! -s ${identity_file} ]]; then
    temporary_identity=$(mktemp /etc/careertrellis/.credential-transfer.XXXXXX)
    trap 'rm -f -- "${temporary_identity:-}"' EXIT
    rm -f -- "${temporary_identity}"
    age-keygen --output "${temporary_identity}" >/dev/null
    install -o root -g root -m 0600 "${temporary_identity}" "${identity_file}"
fi

echo "Credential-transfer recipient (public):"
age-keygen -y "${identity_file}"
