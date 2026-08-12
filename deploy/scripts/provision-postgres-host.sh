#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this PostgreSQL provisioner as root." >&2
    exit 1
fi

postgres_version=${CAREERTRELLIS_POSTGRES_VERSION:-18}
cluster_name=${CAREERTRELLIS_POSTGRES_CLUSTER:-main}
database_name=${CAREERTRELLIS_POSTGRES_DATABASE:-careertrellis}
wireguard_address=${CAREERTRELLIS_DATABASE_WIREGUARD_IP:-10.70.0.2}
application_address=${CAREERTRELLIS_APPLICATION_WIREGUARD_IP:-10.70.0.1}

if [[ ! ${postgres_version} =~ ^[0-9]+$ ]]; then
    echo "CAREERTRELLIS_POSTGRES_VERSION must be numeric." >&2
    exit 1
fi
if [[ ! ${database_name} =~ ^[a-z][a-z0-9_]*$ ]]; then
    echo "CAREERTRELLIS_POSTGRES_DATABASE is invalid." >&2
    exit 1
fi
if ! ip -brief address show wg0 | grep -Fq "${wireguard_address}/"; then
    echo "WireGuard address ${wireguard_address} is not active on wg0." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl postgresql-common

. /etc/os-release
if [[ ${VERSION_CODENAME:-} != noble ]]; then
    echo "This provisioner is pinned to Ubuntu 24.04 (noble)." >&2
    exit 1
fi

install -d -o root -g root -m 0755 /usr/share/postgresql-common/pgdg
curl --fail --show-error --silent \
    --output /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    https://www.postgresql.org/media/keys/ACCC4CF8.asc

cat >/etc/apt/sources.list.d/pgdg.sources <<EOF
Types: deb
URIs: https://apt.postgresql.org/pub/repos/apt
Suites: ${VERSION_CODENAME}-pgdg
Architectures: amd64
Components: main
Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
EOF

apt-get update
apt-get install -y "postgresql-${postgres_version}" "postgresql-client-${postgres_version}"

if ! pg_lsclusters --no-header | awk -v version="${postgres_version}" -v cluster="${cluster_name}" \
    '$1 == version && $2 == cluster { found = 1 } END { exit !found }'; then
    pg_createcluster "${postgres_version}" "${cluster_name}" --start
fi

config_directory="/etc/postgresql/${postgres_version}/${cluster_name}"
if [[ ! -d ${config_directory} ]]; then
    echo "PostgreSQL cluster configuration was not created." >&2
    exit 1
fi

install -d -o postgres -g postgres -m 0750 "${config_directory}/conf.d"
cat >"${config_directory}/conf.d/60-careertrellis.conf" <<EOF
# Managed by deploy/scripts/provision-postgres-host.sh.
listen_addresses = '127.0.0.1,${wireguard_address}'
port = 5432
password_encryption = 'scram-sha-256'
max_connections = 50
shared_buffers = '256MB'
effective_cache_size = '1GB'
maintenance_work_mem = '64MB'
work_mem = '4MB'
idle_in_transaction_session_timeout = '60s'
log_connections = on
log_disconnections = on
EOF
chown root:postgres "${config_directory}/conf.d/60-careertrellis.conf"
chmod 0640 "${config_directory}/conf.d/60-careertrellis.conf"

if ! grep -Eq "^[[:space:]]*include_dir[[:space:]]*=[[:space:]]*['\"]conf\\.d['\"]" \
    "${config_directory}/postgresql.conf"; then
    echo "PostgreSQL configuration does not include conf.d." >&2
    exit 1
fi

if [[ ! -f ${config_directory}/pg_hba.conf.careertrellis-original ]]; then
    install -o root -g postgres -m 0640 \
        "${config_directory}/pg_hba.conf" \
        "${config_directory}/pg_hba.conf.careertrellis-original"
fi
cat >"${config_directory}/pg_hba.conf" <<EOF
# Managed by deploy/scripts/provision-postgres-host.sh.
# Local operating-system administration remains peer-authenticated.
local   all             postgres                                peer
local   all             all                                     peer

# Loopback clients must use SCRAM passwords.
host    all             all             127.0.0.1/32            scram-sha-256
host    all             all             ::1/128                 scram-sha-256

# Application roles may connect only through WireGuard.
host    ${database_name} careertrellis_migration ${application_address}/32 scram-sha-256
host    ${database_name} careertrellis_runtime   ${application_address}/32 scram-sha-256
host    ${database_name} careertrellis_worker    ${application_address}/32 scram-sha-256
EOF
chown root:postgres "${config_directory}/pg_hba.conf"
chmod 0640 "${config_directory}/pg_hba.conf"

systemctl enable postgresql >/dev/null
systemctl restart postgresql

if ! runuser -u postgres -- psql --dbname=postgres --tuples-only --no-align \
    --command="SELECT 1 FROM pg_database WHERE datname = '${database_name}'" | grep -qx 1; then
    runuser -u postgres -- createdb --encoding=UTF8 --template=template0 "${database_name}"
fi
runuser -u postgres -- psql --dbname=postgres --set=ON_ERROR_STOP=1 \
    --command="REVOKE CONNECT ON DATABASE ${database_name} FROM PUBLIC" \
    --command="ALTER DATABASE ${database_name} CONNECTION LIMIT 40"

ufw allow in on wg0 from "${application_address}" to "${wireguard_address}" \
    port 5432 proto tcp comment 'PostgreSQL over WireGuard'

mapfile -t listeners < <(ss -H -ltn | awk '$4 ~ /:5432$/ { print $4 }')
if [[ ${#listeners[@]} -eq 0 ]]; then
    echo "PostgreSQL is not listening on port 5432." >&2
    exit 1
fi
for listener in "${listeners[@]}"; do
    if [[ ${listener} != "127.0.0.1:5432" && ${listener} != "${wireguard_address}:5432" ]]; then
        echo "Unexpected PostgreSQL listener: ${listener}" >&2
        exit 1
    fi
done

runuser -u postgres -- pg_isready --host=127.0.0.1 --port=5432 --dbname="${database_name}"
echo "PostgreSQL ${postgres_version} is ready on loopback and ${wireguard_address} only."
printf 'Listeners:\n'
printf '  %s\n' "${listeners[@]}"
