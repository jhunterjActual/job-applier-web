#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the SQLite import as root." >&2
    exit 1
fi

release_dir=${1:-}
target_environment_file=${2:-}
shift $(( $# >= 2 ? 2 : $# ))
object_environment_file=""
if [[ -n ${1:-} && ${1} != --* ]]; then
    object_environment_file=$1
    shift
fi
if [[ -z ${release_dir} || ! -f ${release_dir}/backend/sqlite_postgres_import.py || -z ${target_environment_file} ]]; then
    echo "Usage: $0 /absolute/path/to/reviewed-release /root-owned/target-migration.env [/root-owned/target-object-storage.env] [--omit-provider-secrets] [--omit-generated-files]" >&2
    exit 1
fi
release_dir=$(realpath "${release_dir}")
case "${release_dir}" in
    /opt/careertrellis/staging/releases/*) ;;
    *) echo "The reviewed release must be inside the staging release directory." >&2; exit 1 ;;
esac

staging_environment_file=/etc/careertrellis/staging.env
secret_environment_file=/etc/careertrellis/provider-secrets.env
for environment_file in \
    "${staging_environment_file}" \
    "${target_environment_file}" \
    "${secret_environment_file}"; do
    if [[ ! -f ${environment_file} || $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
        echo "Every import environment file must be root:root mode 0600." >&2
        exit 1
    fi
done

for argument in "$@"; do
    case "${argument}" in
        --omit-provider-secrets|--omit-generated-files) ;;
        *) echo "Unsupported import option: ${argument}" >&2; exit 1 ;;
    esac
done
if [[ " $* " != *" --omit-generated-files "* && -z ${object_environment_file} ]]; then
    echo "A root-owned target object-storage environment file is required unless generated files are omitted." >&2
    exit 1
fi
if [[ -n ${object_environment_file} && (! -f ${object_environment_file} || $(stat -c '%U:%G %a' "${object_environment_file}") != "root:root 600") ]]; then
    echo "Every import environment file must be root:root mode 0600." >&2
    exit 1
fi

exec 8>/run/lock/careertrellis-maintenance.lock
if ! flock -n 8; then
    echo "Another deployment, rollback, or import operation is running." >&2
    exit 1
fi
exec 9>/run/lock/careertrellis-sqlite-import.lock
if ! flock -n 9; then
    echo "Another SQLite import operation is running." >&2
    exit 1
fi
for service in careertrellis-web.service careertrellis-worker.service; do
    if systemctl is-active --quiet "${service}"; then
        echo "Stop the web and worker services before importing a SQLite snapshot." >&2
        exit 1
    fi
done

set -a
# shellcheck disable=SC1090
source "${staging_environment_file}"
# shellcheck disable=SC1090
source "${target_environment_file}"
# shellcheck disable=SC1090
source "${secret_environment_file}"
if [[ -n ${object_environment_file} ]]; then
    # shellcheck disable=SC1090
    source "${object_environment_file}"
fi
set +a
if [[ -z ${CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID:-} ]]; then
    echo "CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID is required." >&2
    exit 1
fi
if [[ -z ${CAREERTRELLIS_DATA_DIR:-} || -z ${CAREERTRELLIS_MIGRATION_DATABASE_URL:-} ]]; then
    echo "The source data directory and target migration URL are required." >&2
    exit 1
fi
data_directory=$(realpath "${CAREERTRELLIS_DATA_DIR}")
source_database=$(realpath "${data_directory}/jobapplier.db")
case "${source_database}" in
    "${data_directory}"/jobapplier.db) ;;
    *) echo "The SQLite source escaped the configured data directory." >&2; exit 1 ;;
esac

export CAREERTRELLIS_IMPORT_OWNER_WORKOS_USER_ID="${CAREERTRELLIS_STAGING_OWNER_WORKOS_USER_ID}"
cd "${release_dir}/backend"
./venv/bin/python sqlite_postgres_import.py "${source_database}" "$@"
