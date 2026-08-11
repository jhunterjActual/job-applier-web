#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this rclone installer as root." >&2
    exit 1
fi

version=${CAREERTRELLIS_RCLONE_VERSION:-1.75.0}
signing_fingerprint=FBF737ECE9F8AB18604BD2AC93935E02FF3B54FA
archive="rclone-v${version}-linux-amd64.zip"
release_base="https://downloads.rclone.org/v${version}"

if [[ ! ${version} =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "CAREERTRELLIS_RCLONE_VERSION is invalid." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg unzip

working_directory=$(mktemp -d /tmp/careertrellis-rclone.XXXXXX)
trap 'rm -rf -- "${working_directory}"' EXIT
chmod 0700 "${working_directory}"
cd "${working_directory}"

curl --fail --show-error --silent --remote-name https://rclone.org/KEYS
curl --fail --show-error --silent --remote-name "${release_base}/SHA256SUMS"
curl --fail --show-error --silent --remote-name "${release_base}/${archive}"

install -d -m 0700 "${working_directory}/gnupg"
gpg --batch --homedir "${working_directory}/gnupg" --import KEYS >/dev/null 2>&1
if ! gpg --batch --homedir "${working_directory}/gnupg" --with-colons --fingerprint \
    | awk -F: '$1 == "fpr" { print $10 }' \
    | grep -Fxq "${signing_fingerprint}"; then
    echo "The documented rclone release-signing fingerprint is missing." >&2
    exit 1
fi

verification_status=$(gpg --batch --homedir "${working_directory}/gnupg" \
    --status-fd 1 --verify SHA256SUMS 2>/dev/null)
if ! grep -Fq "[GNUPG:] VALIDSIG ${signing_fingerprint} " <<<"${verification_status}"; then
    echo "The rclone checksum signature is not valid for the documented signing key." >&2
    exit 1
fi

gpg --batch --homedir "${working_directory}/gnupg" --decrypt SHA256SUMS \
    >SHA256SUMS.verified 2>/dev/null
if ! grep -E "^[0-9a-f]{64}  ${archive}$" SHA256SUMS.verified \
    | sha256sum --check --strict -; then
    echo "The rclone archive checksum is invalid." >&2
    exit 1
fi

unzip -q "${archive}"
install -o root -g root -m 0755 \
    "rclone-v${version}-linux-amd64/rclone" \
    /usr/local/bin/rclone

if [[ $(/usr/local/bin/rclone version | sed -n '1p') != "rclone v${version}" ]]; then
    echo "The installed rclone version is not the pinned release." >&2
    exit 1
fi
echo "Installed verified rclone v${version} at /usr/local/bin/rclone."
