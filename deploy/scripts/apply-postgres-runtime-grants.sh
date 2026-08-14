#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the PostgreSQL runtime-grant wrapper as root." >&2
    exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=maintenance-lock.sh
source "${script_dir}/maintenance-lock.sh"
acquire_careertrellis_maintenance_lock

release_dir=${1:-}
environment_file=${2:-/etc/careertrellis/postgres-migration.env}
if [[ -z ${release_dir} || ! -d ${release_dir} ]]; then
    echo "Usage: $0 /absolute/path/to/reviewed-release [migration-environment-file]" >&2
    exit 1
fi
release_dir=$(realpath "${release_dir}")
release_revision=$(basename "${release_dir}")
if [[ $(dirname "${release_dir}") != "/opt/careertrellis/staging/releases" || ! ${release_revision} =~ ^[0-9a-f]{40}$ || ! -f ${release_dir}/backend/apply_postgres_runtime_grants.py || -L ${release_dir}/backend/apply_postgres_runtime_grants.py || ! -f ${release_dir}/deploy/postgres/grant-runtime.sql || -L ${release_dir}/deploy/postgres/grant-runtime.sql ]]; then
    echo "The reviewed release must be an immutable 40-character release containing the runtime grants." >&2
    exit 1
fi
if [[ ! -f ${environment_file} || -L ${environment_file} || $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
    echo "The migration environment file must be a regular root:root mode 0600 file." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${environment_file}"
set +a
if [[ -z ${CAREERTRELLIS_MIGRATION_DATABASE_URL:-} ]]; then
    echo "CAREERTRELLIS_MIGRATION_DATABASE_URL is required." >&2
    exit 1
fi

cd "${release_dir}/backend"
./venv/bin/python apply_postgres_runtime_grants.py
