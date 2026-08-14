#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this erasure-worker role bootstrap as root." >&2
    exit 1
fi

recipient=${CAREERTRELLIS_CREDENTIAL_RECIPIENT:-}
script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
bootstrap_sql=${1:-${script_directory}/../postgres/bootstrap-erasure-worker-role.sql}
database_name=${CAREERTRELLIS_POSTGRES_DATABASE:-careertrellis}
output_directory=/var/lib/careertrellis
output_file=${output_directory}/erasure-worker-credential.age

if [[ ! ${recipient} =~ ^age1[023456789acdefghjklmnpqrstuvwxyz]{58}$ ]]; then
    echo "CAREERTRELLIS_CREDENTIAL_RECIPIENT must be an age X25519 public recipient." >&2
    exit 1
fi
if [[ ! -r ${bootstrap_sql} ]]; then
    echo "The reviewed erasure-worker role-bootstrap SQL file is missing." >&2
    exit 1
fi
if ! command -v age >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1; then
    echo "age and openssl are required." >&2
    exit 1
fi

role_is_safe=$(runuser -u postgres -- psql --dbname=postgres --tuples-only --no-align \
    --command="SELECT count(*) FROM pg_roles WHERE rolname = 'careertrellis_erasure_worker' AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls")
role_exists=$(runuser -u postgres -- psql --dbname=postgres --tuples-only --no-align \
    --command="SELECT count(*) FROM pg_roles WHERE rolname = 'careertrellis_erasure_worker'")
if [[ ${role_exists} == 1 && ${role_is_safe} != 1 ]]; then
    echo "The existing erasure-worker role does not satisfy the least-privilege contract." >&2
    exit 1
fi
if [[ ${role_exists} == 1 && -f ${output_file} && ! -L ${output_file} && \
      $(stat -c '%U:%G %a' "${output_file}") == "root:root 600" ]]; then
    echo "The least-privilege erasure-worker role and encrypted credential already exist."
    sha256sum "${output_file}"
    exit 0
fi
if [[ ${role_exists} != 0 || -e ${output_file} || -L ${output_file} ]]; then
    echo "Erasure-worker role bootstrap is incomplete; refusing implicit credential rotation." >&2
    exit 1
fi

generate_secret() {
    openssl rand -base64 48 | tr '/+' '_-' | tr -d '=\r\n'
}

export CAREERTRELLIS_ERASURE_WORKER_PASSWORD=$(generate_secret)
if [[ ! ${CAREERTRELLIS_ERASURE_WORKER_PASSWORD} =~ ^[A-Za-z0-9_-]{60,}$ ]]; then
    echo "Credential generation failed." >&2
    exit 1
fi

runuser -u postgres --preserve-environment -- \
    psql --dbname="${database_name}" --no-psqlrc --set=ON_ERROR_STOP=1 <<EOF
\getenv erasure_worker_password CAREERTRELLIS_ERASURE_WORKER_PASSWORD
\i ${bootstrap_sql}
EOF

install -d -o root -g root -m 0700 "${output_directory}"
plaintext=$(mktemp "${output_directory}/.erasure-worker-credential.XXXXXX")
encrypted="${plaintext}.age"
trap 'shred -u -- "${plaintext:-}" 2>/dev/null || true; rm -f -- "${encrypted:-}"' EXIT
chmod 0600 "${plaintext}"
printf 'CAREERTRELLIS_ERASURE_WORKER_PASSWORD=%s\n' \
    "${CAREERTRELLIS_ERASURE_WORKER_PASSWORD}" >"${plaintext}"

age --encrypt --recipient "${recipient}" --output "${encrypted}" "${plaintext}"
chmod 0600 "${encrypted}"
install -o root -g root -m 0600 "${encrypted}" "${output_file}"
shred -u -- "${plaintext}"
unset CAREERTRELLIS_ERASURE_WORKER_PASSWORD

echo "The erasure-worker role was created and its credential bundle was encrypted."
sha256sum "${output_file}"
