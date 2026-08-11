#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this PostgreSQL credential installer as root." >&2
    exit 1
fi

encrypted_bundle=${1:-}
identity_file=/etc/careertrellis/credential-transfer.agekey
database_host=${CAREERTRELLIS_DATABASE_WIREGUARD_IP:-10.70.0.2}
database_name=${CAREERTRELLIS_POSTGRES_DATABASE:-careertrellis}

if [[ -z ${encrypted_bundle} || ! -r ${encrypted_bundle} ]]; then
    echo "Usage: $0 /path/to/bootstrap-credentials.age" >&2
    exit 1
fi
if [[ ! -r ${identity_file} ]]; then
    echo "The root-owned credential-transfer identity is missing." >&2
    exit 1
fi

install -d -o root -g root -m 0700 /etc/careertrellis
plaintext=$(mktemp /etc/careertrellis/.postgres-credentials.XXXXXX)
trap 'shred -u -- "${plaintext:-}" 2>/dev/null || true' EXIT
chmod 0600 "${plaintext}"
age --decrypt --identity "${identity_file}" "${encrypted_bundle}" >"${plaintext}"

set -a
# shellcheck disable=SC1090
source "${plaintext}"
set +a
for variable_name in \
    CAREERTRELLIS_MIGRATION_PASSWORD \
    CAREERTRELLIS_RUNTIME_PASSWORD \
    CAREERTRELLIS_WORKER_PASSWORD; do
    variable_value=$(printenv "${variable_name}" || true)
    if [[ ! ${variable_value} =~ ^[A-Za-z0-9_-]{60,}$ ]]; then
        echo "The encrypted bundle contains an invalid credential." >&2
        exit 1
    fi
done

write_environment() {
    local path=$1
    local variable_name=$2
    local role=$3
    local password=$4
    local temporary
    temporary=$(mktemp /etc/careertrellis/.postgres-environment.XXXXXX)
    chmod 0600 "${temporary}"
    printf '%s=postgresql+psycopg://%s:%s@%s:5432/%s\n' \
        "${variable_name}" "${role}" "${password}" "${database_host}" "${database_name}" \
        >"${temporary}"
    install -o root -g root -m 0600 "${temporary}" "${path}"
    rm -f -- "${temporary}"
}

write_environment \
    /etc/careertrellis/postgres-migration.env \
    CAREERTRELLIS_MIGRATION_DATABASE_URL \
    careertrellis_migration \
    "${CAREERTRELLIS_MIGRATION_PASSWORD}"
write_environment \
    /etc/careertrellis/postgres-runtime.env \
    CAREERTRELLIS_DATABASE_URL \
    careertrellis_runtime \
    "${CAREERTRELLIS_RUNTIME_PASSWORD}"
write_environment \
    /etc/careertrellis/postgres-worker.env \
    CAREERTRELLIS_DATABASE_URL \
    careertrellis_worker \
    "${CAREERTRELLIS_WORKER_PASSWORD}"

install -o root -g root -m 0600 "${encrypted_bundle}" \
    /etc/careertrellis/bootstrap-credentials.age

if command -v psql >/dev/null 2>&1; then
    for role_and_password in \
        "careertrellis_migration:${CAREERTRELLIS_MIGRATION_PASSWORD}" \
        "careertrellis_runtime:${CAREERTRELLIS_RUNTIME_PASSWORD}" \
        "careertrellis_worker:${CAREERTRELLIS_WORKER_PASSWORD}"; do
        role=${role_and_password%%:*}
        password=${role_and_password#*:}
        authenticated_role=$(PGPASSWORD="${password}" psql \
            --host="${database_host}" \
            --port=5432 \
            --username="${role}" \
            --dbname="${database_name}" \
            --no-password \
            --no-psqlrc \
            --tuples-only \
            --no-align \
            --command='SELECT current_user')
        if [[ ${authenticated_role} != "${role}" ]]; then
            echo "Authentication validation failed for ${role}." >&2
            exit 1
        fi
    done
fi

shred -u -- "${plaintext}"
unset CAREERTRELLIS_MIGRATION_PASSWORD CAREERTRELLIS_RUNTIME_PASSWORD CAREERTRELLIS_WORKER_PASSWORD

echo "Root-owned PostgreSQL migration, runtime, and worker environments were installed."
sha256sum /etc/careertrellis/bootstrap-credentials.age
