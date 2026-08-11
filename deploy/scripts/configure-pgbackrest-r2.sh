#!/usr/bin/env bash
set -Eeuo pipefail
set +x

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this pgBackRest configuration script as root." >&2
    exit 1
fi
if [[ ! -t 0 ]]; then
    echo "Run this script from an interactive SSH terminal; never pass secrets as arguments." >&2
    exit 1
fi
for command_name in pgbackrest rclone openssl; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "${command_name} is not installed." >&2
        exit 1
    fi
done

stanza=careertrellis
postgres_version=${CAREERTRELLIS_POSTGRES_VERSION:-18}
cluster_name=${CAREERTRELLIS_POSTGRES_CLUSTER:-main}
postgres_config_directory="/etc/postgresql/${postgres_version}/${cluster_name}"
postgres_data_directory="/var/lib/postgresql/${postgres_version}/${cluster_name}"
r2_bucket=job-applier-staging-db-backups
r2_endpoint=a82e1a46001dfe133141fb04ccd772d0.r2.cloudflarestorage.com
r2_repository_path=/job-applier/staging/postgresql
rclone_config=/etc/careertrellis/rclone.conf

if [[ ! -d ${postgres_data_directory} || ! -d ${postgres_config_directory} ]]; then
    echo "The expected PostgreSQL ${postgres_version}/${cluster_name} cluster is missing." >&2
    exit 1
fi

read -r -p 'R2 Access Key ID: ' r2_access_key
read -r -s -p 'R2 Secret Access Key: ' r2_secret_key
printf '\n'
read -r -s -p 'New pgBackRest encryption passphrase (32+ URL-safe characters): ' cipher_passphrase
printf '\n'
read -r -s -p 'Confirm pgBackRest encryption passphrase: ' cipher_confirmation
printf '\n'

if [[ ! ${r2_access_key} =~ ^[A-Za-z0-9_-]{16,}$ ]]; then
    echo "The R2 Access Key ID is invalid." >&2
    exit 1
fi
if [[ ! ${r2_secret_key} =~ ^[A-Za-z0-9_+/=-]{24,}$ ]]; then
    echo "The R2 Secret Access Key is invalid." >&2
    exit 1
fi
if [[ ! ${cipher_passphrase} =~ ^[A-Za-z0-9_-]{32,}$ ]]; then
    echo "Use at least 32 URL-safe characters for the repository passphrase." >&2
    exit 1
fi
if [[ ${cipher_passphrase} != "${cipher_confirmation}" ]]; then
    echo "The repository passphrases do not match." >&2
    exit 1
fi

install -d -o root -g postgres -m 0750 /etc/pgbackrest
install -d -o postgres -g postgres -m 0750 /var/log/pgbackrest /var/spool/pgbackrest
install -d -o root -g root -m 0700 /etc/careertrellis
if [[ -e ${rclone_config} ]]; then
    if [[ -L ${rclone_config} || $(stat -c '%a:%U:%G' "${rclone_config}") != 600:root:root ]]; then
        echo "The existing rclone configuration is not a safe root-owned file." >&2
        exit 1
    fi
fi

pgbackrest_temporary=$(mktemp /etc/pgbackrest/.pgbackrest.XXXXXX)
rclone_temporary=$(mktemp /etc/careertrellis/.rclone.XXXXXX)
trap 'shred -u -- "${pgbackrest_temporary:-}" "${rclone_temporary:-}" 2>/dev/null || true' EXIT
chmod 0600 "${pgbackrest_temporary}" "${rclone_temporary}"

cat >"${pgbackrest_temporary}" <<EOF
[global]
repo1-type=s3
repo1-path=${r2_repository_path}
repo1-s3-bucket=${r2_bucket}
repo1-s3-endpoint=${r2_endpoint}
repo1-s3-region=auto
repo1-s3-key=${r2_access_key}
repo1-s3-key-secret=${r2_secret_key}
repo1-s3-uri-style=path
repo1-cipher-type=aes-256-cbc
repo1-cipher-pass=${cipher_passphrase}
repo1-retention-full=4
repo1-retention-diff=14
repo1-retention-archive=4
compress-type=zst
compress-level=3
process-max=2
start-fast=y
spool-path=/var/spool/pgbackrest
log-path=/var/log/pgbackrest

[${stanza}]
pg1-path=${postgres_data_directory}
pg1-port=5432
EOF

if [[ -f ${rclone_config} ]]; then
    # Preserve OAuth-backed remotes while replacing only the bucket-scoped R2
    # section. The temporary file remains root-only throughout the rewrite.
    awk '
        /^\[r2\][[:space:]]*$/ { skip = 1; next }
        /^\[[^]]+\][[:space:]]*$/ { skip = 0 }
        !skip { print }
    ' "${rclone_config}" >"${rclone_temporary}"
fi
cat >>"${rclone_temporary}" <<EOF

[r2]
type = s3
provider = Cloudflare
access_key_id = ${r2_access_key}
secret_access_key = ${r2_secret_key}
endpoint = https://${r2_endpoint}
region = auto
acl = private
no_check_bucket = true
EOF

install -o root -g postgres -m 0640 "${pgbackrest_temporary}" /etc/pgbackrest/pgbackrest.conf
install -o root -g root -m 0600 "${rclone_temporary}" "${rclone_config}"
shred -u -- "${pgbackrest_temporary}" "${rclone_temporary}"
unset r2_access_key r2_secret_key cipher_passphrase cipher_confirmation

cat >"${postgres_config_directory}/conf.d/70-careertrellis-archive.conf" <<EOF
# Managed by deploy/scripts/configure-pgbackrest-r2.sh.
archive_mode = on
archive_command = 'pgbackrest --stanza=${stanza} archive-push %p'
archive_timeout = '60s'
EOF
chown root:postgres "${postgres_config_directory}/conf.d/70-careertrellis-archive.conf"
chmod 0640 "${postgres_config_directory}/conf.d/70-careertrellis-archive.conf"

systemctl restart postgresql
runuser -u postgres -- pgbackrest --stanza="${stanza}" stanza-create
runuser -u postgres -- pgbackrest --stanza="${stanza}" check
runuser -u postgres -- pgbackrest --stanza="${stanza}" --type=full backup
runuser -u postgres -- pgbackrest --stanza="${stanza}" info

systemctl daemon-reload
systemctl enable --now \
    careertrellis-pgbackrest-full.timer \
    careertrellis-pgbackrest-differential.timer

echo "Encrypted R2 backups are configured and the first full backup succeeded."
echo "Keep the repository passphrase in your password manager; it is required for disaster recovery."
