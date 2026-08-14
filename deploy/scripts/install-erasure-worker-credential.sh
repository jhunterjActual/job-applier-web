#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this erasure-worker credential installer as root." >&2
    exit 1
fi

encrypted_bundle=${1:-}
identity_file=/etc/careertrellis/credential-transfer.agekey
database_host=${CAREERTRELLIS_DATABASE_WIREGUARD_IP:-10.70.0.2}
database_name=${CAREERTRELLIS_POSTGRES_DATABASE:-careertrellis}
target_environment=/etc/careertrellis/postgres-erasure-worker.env
retained_bundle=/etc/careertrellis/erasure-worker-credential.age

if [[ -z ${encrypted_bundle} || ! -f ${encrypted_bundle} || -L ${encrypted_bundle} ]]; then
    echo "Usage: $0 /path/to/erasure-worker-credential.age" >&2
    exit 1
fi
if [[ ! -f ${identity_file} || -L ${identity_file} ]]; then
    echo "The root-owned credential-transfer identity is missing." >&2
    exit 1
fi
if [[ $(stat -c '%U:%G %a' "${identity_file}") != "root:root 600" ]]; then
    echo "The credential-transfer identity must be root:root mode 0600." >&2
    exit 1
fi
if [[ -L ${target_environment} || -L ${retained_bundle} ]]; then
    echo "An account-erasure credential target is a symbolic link." >&2
    exit 1
fi

install -d -o root -g root -m 0700 /etc/careertrellis
plaintext=$(mktemp /etc/careertrellis/.erasure-worker-credential.XXXXXX)
environment_temporary=""
trap 'shred -u -- "${plaintext:-}" "${environment_temporary:-}" 2>/dev/null || true' EXIT
chmod 0600 "${plaintext}"
age --decrypt --identity "${identity_file}" "${encrypted_bundle}" >"${plaintext}"

mapfile -t credential_lines <"${plaintext}"
if [[ ${#credential_lines[@]} -ne 1 || ! ${credential_lines[0]} =~ ^CAREERTRELLIS_ERASURE_WORKER_PASSWORD=([A-Za-z0-9_-]{60,})$ ]]; then
    echo "The encrypted bundle contains an invalid erasure-worker credential." >&2
    exit 1
fi
password=${BASH_REMATCH[1]}

environment_temporary=$(mktemp /etc/careertrellis/.postgres-erasure-worker.XXXXXX)
chmod 0600 "${environment_temporary}"
printf 'CAREERTRELLIS_DATABASE_URL=postgresql+psycopg://careertrellis_erasure_worker:%s@%s:5432/%s\n' \
    "${password}" "${database_host}" "${database_name}" >"${environment_temporary}"

if command -v psql >/dev/null 2>&1; then
    authenticated_role=$(PGPASSWORD="${password}" psql \
        --host="${database_host}" \
        --port=5432 \
        --username=careertrellis_erasure_worker \
        --dbname="${database_name}" \
        --no-password \
        --no-psqlrc \
        --tuples-only \
        --no-align \
        --command='SELECT current_user')
    if [[ ${authenticated_role} != "careertrellis_erasure_worker" ]]; then
        echo "Authentication validation failed for careertrellis_erasure_worker." >&2
        exit 1
    fi
fi

install -o root -g root -m 0600 "${environment_temporary}" "${target_environment}"
install -o root -g root -m 0600 "${encrypted_bundle}" "${retained_bundle}"

shred -u -- "${plaintext}" "${environment_temporary}"
unset password

echo "The root-owned erasure-worker PostgreSQL environment was installed."
sha256sum "${retained_bundle}"
