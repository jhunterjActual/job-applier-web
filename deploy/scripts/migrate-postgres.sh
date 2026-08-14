#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the migration wrapper as root." >&2
    exit 1
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=maintenance-lock.sh
source "${script_dir}/maintenance-lock.sh"
acquire_careertrellis_maintenance_lock

release_dir=${1:-}
environment_file=${2:-/etc/careertrellis/postgres-migration.env}
if [[ -z ${release_dir} || ! -f ${release_dir}/backend/alembic.ini ]]; then
    echo "Usage: $0 /absolute/path/to/release [migration-environment-file]" >&2
    exit 1
fi
release_dir=$(realpath "${release_dir}")
if [[ ! -f ${environment_file} ]]; then
    echo "The root-owned migration environment file is missing." >&2
    exit 1
fi
if [[ $(stat -c '%a' "${environment_file}") != 600 ]]; then
    echo "The migration environment file must have mode 0600." >&2
    exit 1
fi

exec 9>/run/lock/careertrellis-postgres-migration.lock
if ! flock -n 9; then
    echo "Another database migration is already running." >&2
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
./venv/bin/python -m alembic -c alembic.ini heads
./venv/bin/python -m alembic -c alembic.ini upgrade head
./venv/bin/python -m alembic -c alembic.ini current --check-heads
