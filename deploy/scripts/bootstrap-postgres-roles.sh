#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this PostgreSQL role bootstrap as root." >&2
    exit 1
fi

recipient=${CAREERTRELLIS_CREDENTIAL_RECIPIENT:-}
bootstrap_sql=${1:-/usr/local/share/careertrellis/bootstrap-roles.sql}
database_name=${CAREERTRELLIS_POSTGRES_DATABASE:-careertrellis}
output_directory=/var/lib/careertrellis
output_file=${output_directory}/bootstrap-credentials.age

if [[ ! ${recipient} =~ ^age1[023456789acdefghjklmnpqrstuvwxyz]{58}$ ]]; then
    echo "CAREERTRELLIS_CREDENTIAL_RECIPIENT must be an age X25519 public recipient." >&2
    exit 1
fi
if [[ ! -r ${bootstrap_sql} ]]; then
    echo "The reviewed role-bootstrap SQL file is missing." >&2
    exit 1
fi
if ! command -v age >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1; then
    echo "age and openssl are required." >&2
    exit 1
fi
if [[ -e ${output_file} ]]; then
    echo "Encrypted bootstrap credentials already exist; refusing to rotate roles implicitly." >&2
    exit 1
fi

existing_roles=$(runuser -u postgres -- psql --dbname=postgres --tuples-only --no-align \
    --command="SELECT count(*) FROM pg_roles WHERE rolname IN ('careertrellis_migration', 'careertrellis_runtime', 'careertrellis_worker')")
if [[ ${existing_roles} != 0 ]]; then
    echo "One or more application roles already exist; refusing to rotate them implicitly." >&2
    exit 1
fi

generate_secret() {
    openssl rand -base64 48 | tr '/+' '_-' | tr -d '=\r\n'
}

export CAREERTRELLIS_MIGRATION_PASSWORD=$(generate_secret)
export CAREERTRELLIS_RUNTIME_PASSWORD=$(generate_secret)
export CAREERTRELLIS_WORKER_PASSWORD=$(generate_secret)
for variable_name in \
    CAREERTRELLIS_MIGRATION_PASSWORD \
    CAREERTRELLIS_RUNTIME_PASSWORD \
    CAREERTRELLIS_WORKER_PASSWORD; do
    variable_value=$(printenv "${variable_name}" || true)
    if [[ ${#variable_value} -lt 60 ]]; then
        echo "Credential generation failed." >&2
        exit 1
    fi
done

runuser -u postgres --preserve-environment -- \
    psql --dbname="${database_name}" --no-psqlrc --set=ON_ERROR_STOP=1 <<EOF
\getenv migration_password CAREERTRELLIS_MIGRATION_PASSWORD
\getenv runtime_password CAREERTRELLIS_RUNTIME_PASSWORD
\getenv worker_password CAREERTRELLIS_WORKER_PASSWORD
\i ${bootstrap_sql}
EOF

install -d -o root -g root -m 0700 "${output_directory}"
plaintext=$(mktemp "${output_directory}/.bootstrap-credentials.XXXXXX")
encrypted="${plaintext}.age"
trap 'shred -u -- "${plaintext:-}" 2>/dev/null || true; rm -f -- "${encrypted:-}"' EXIT
chmod 0600 "${plaintext}"
cat >"${plaintext}" <<EOF
CAREERTRELLIS_MIGRATION_PASSWORD=${CAREERTRELLIS_MIGRATION_PASSWORD}
CAREERTRELLIS_RUNTIME_PASSWORD=${CAREERTRELLIS_RUNTIME_PASSWORD}
CAREERTRELLIS_WORKER_PASSWORD=${CAREERTRELLIS_WORKER_PASSWORD}
EOF

age --encrypt --recipient "${recipient}" --output "${encrypted}" "${plaintext}"
chmod 0600 "${encrypted}"
install -o root -g root -m 0600 "${encrypted}" "${output_file}"
shred -u -- "${plaintext}"
unset CAREERTRELLIS_MIGRATION_PASSWORD CAREERTRELLIS_RUNTIME_PASSWORD CAREERTRELLIS_WORKER_PASSWORD

echo "PostgreSQL roles were created and the credential bundle was encrypted."
sha256sum "${output_file}"
