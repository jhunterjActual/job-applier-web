#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the SQLite import preflight as root." >&2
    exit 1
fi

release_dir=${1:-}
staging_environment_file=${2:-/etc/careertrellis/staging.env}
if [[ -z ${release_dir} || ! -f ${release_dir}/backend/sqlite_postgres_import.py ]]; then
    echo "Usage: $0 /absolute/path/to/reviewed-release [staging-environment-file]" >&2
    exit 1
fi
release_dir=$(realpath "${release_dir}")
case "${release_dir}" in
    /opt/careertrellis/staging/releases/*) ;;
    *) echo "The reviewed release must be inside the staging release directory." >&2; exit 1 ;;
esac
if [[ ! -f ${staging_environment_file} ]]; then
    echo "The root-owned staging environment file is missing." >&2
    exit 1
fi
if [[ $(stat -c '%a' "${staging_environment_file}") != 600 ]]; then
    echo "The staging environment file must have mode 0600." >&2
    exit 1
fi

exec 9>/run/lock/careertrellis-sqlite-import.lock
if ! flock -n 9; then
    echo "Another SQLite import operation is running." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${staging_environment_file}"
set +a
if [[ -z ${CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID:-} ]]; then
    echo "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID is required." >&2
    exit 1
fi
if [[ -z ${CAREERTRELLIS_DATA_DIR:-} ]]; then
    echo "CAREERTRELLIS_DATA_DIR is required." >&2
    exit 1
fi
data_directory=$(realpath "${CAREERTRELLIS_DATA_DIR}")
source_database=${data_directory}/jobapplier.db
if [[ ! -f ${source_database} ]]; then
    echo "The SQLite workspace database is missing." >&2
    exit 1
fi
source_database=$(realpath "${source_database}")
case "${source_database}" in
    "${data_directory}"/jobapplier.db) ;;
    *) echo "The SQLite source escaped the configured data directory." >&2; exit 1 ;;
esac

export CAREERTRELLIS_IMPORT_OWNER_WORKOS_USER_ID="${CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID}"
cd "${release_dir}/backend"
./venv/bin/python sqlite_postgres_import.py "${source_database}" --preflight-only
