#!/usr/bin/env bash
set -Eeuo pipefail

confirmation_phrase=I_UNDERSTAND_THIS_WRITES_ONLY_TO_DISPOSABLE_POSTGRES_AND_R2
release_root=/opt/careertrellis/staging
authority_helper=/usr/local/sbin/careertrellis-authority
systemctl_command=systemctl
live_migration_environment_file=/etc/careertrellis/postgres-migration.env
live_runtime_environment_file=/etc/careertrellis/postgres-runtime.env
live_object_environment_file=/etc/careertrellis/object-storage.env
test_mode=false

if [[ ${1:-} == "--test-root" ]]; then
    if [[ ${CAREERTRELLIS_REHEARSAL_TESTING:-} != "true" || -z ${2:-} || ! -d ${2} || -L ${2} ]]; then
        echo "Cutover rehearsal test configuration is invalid." >&2
        exit 1
    fi
    test_mode=true
    test_root=$(realpath "${2}")
    release_root=${test_root}/staging
    authority_helper=${test_root}/bin/careertrellis-authority
    systemctl_command=${test_root}/bin/systemctl
    live_migration_environment_file=${test_root}/etc/postgres-migration.env
    live_runtime_environment_file=${test_root}/etc/postgres-runtime.env
    live_object_environment_file=${test_root}/etc/object-storage.env
    shift 2
fi

if [[ ${test_mode} == false && ${EUID} -ne 0 ]]; then
    echo "Run the PostgreSQL/R2 cutover rehearsal as root." >&2
    exit 1
fi

release_dir=${1:-}
rehearsal_database_environment_file=${2:-}
rehearsal_object_environment_file=${3:-}
confirmation=${4:-}
if [[ $# -ne 4 || ${confirmation} != "${confirmation_phrase}" ]]; then
    echo "Usage: $0 /opt/careertrellis/staging/releases/EXACT_COMMIT /root-owned/postgres-rehearsal.env /root-owned/object-storage-rehearsal.env ${confirmation_phrase}" >&2
    exit 1
fi

if [[ ! -d ${release_dir} || -L ${release_dir} ]]; then
    echo "The rehearsal release must be an exact immutable 40-character release." >&2
    exit 1
fi
release_dir=$(realpath "${release_dir}")
release_revision=$(basename "${release_dir}")
if [[ $(dirname "${release_dir}") != "${release_root}/releases" || ! ${release_revision} =~ ^[0-9a-f]{40}$ ]]; then
    echo "The rehearsal release must be an exact immutable 40-character release." >&2
    exit 1
fi
if [[ ! -x ${release_dir}/backend/venv/bin/python ]]; then
    echo "The rehearsal release is incomplete." >&2
    exit 1
fi
for required_path in \
    "${release_dir}/deploy/scripts/preflight-sqlite-postgres-import.sh" \
    "${release_dir}/deploy/scripts/import-sqlite-postgres.sh"; do
    if [[ ! -f ${required_path} || -L ${required_path} || ! -x ${required_path} ]]; then
        echo "The rehearsal release is incomplete." >&2
        exit 1
    fi
done

validate_environment_file() {
    local environment_file=$1
    if [[ ! -f ${environment_file} || -L ${environment_file} ]]; then
        echo "Every rehearsal identity environment must be a regular root:root mode 0600 file." >&2
        return 1
    fi
    if [[ ${test_mode} == true ]]; then
        [[ $(stat -c '%a' "${environment_file}") == "600" ]] || {
            echo "Every rehearsal identity environment must be a regular root:root mode 0600 file." >&2
            return 1
        }
    elif [[ $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
        echo "Every rehearsal identity environment must be a regular root:root mode 0600 file." >&2
        return 1
    fi
}

for environment_file in \
    "${rehearsal_database_environment_file}" \
    "${rehearsal_object_environment_file}" \
    "${live_migration_environment_file}" \
    "${live_runtime_environment_file}" \
    "${live_object_environment_file}"; do
    validate_environment_file "${environment_file}"
done

if [[ $(realpath "${rehearsal_database_environment_file}") == $(realpath "${live_migration_environment_file}") || \
      $(realpath "${rehearsal_database_environment_file}") == $(realpath "${live_runtime_environment_file}") || \
      $(realpath "${rehearsal_object_environment_file}") == $(realpath "${live_object_environment_file}") ]]; then
    echo "Rehearsal targets must differ from live targets." >&2
    exit 1
fi

read_exact_setting() {
    local environment_file=$1
    local setting_name=$2
    local setting_value
    if [[ $(grep -Ec "^${setting_name}=" "${environment_file}") -ne 1 ]]; then
        return 1
    fi
    setting_value=$(sed -n "s/^${setting_name}=//p" "${environment_file}")
    [[ -n ${setting_value} ]] || return 1
    printf '%s' "${setting_value}"
}

if ! rehearsal_database_url=$(read_exact_setting "${rehearsal_database_environment_file}" CAREERTRELLIS_MIGRATION_DATABASE_URL) || \
   ! live_migration_database_url=$(read_exact_setting "${live_migration_environment_file}" CAREERTRELLIS_MIGRATION_DATABASE_URL) || \
   ! live_runtime_database_url=$(read_exact_setting "${live_runtime_environment_file}" CAREERTRELLIS_DATABASE_URL) || \
   ! rehearsal_object_store=$(read_exact_setting "${rehearsal_object_environment_file}" CAREERTRELLIS_OBJECT_STORE) || \
   ! rehearsal_object_endpoint=$(read_exact_setting "${rehearsal_object_environment_file}" CAREERTRELLIS_OBJECT_STORE_ENDPOINT) || \
   ! rehearsal_object_bucket=$(read_exact_setting "${rehearsal_object_environment_file}" CAREERTRELLIS_OBJECT_STORE_BUCKET) || \
   ! live_object_store=$(read_exact_setting "${live_object_environment_file}" CAREERTRELLIS_OBJECT_STORE) || \
   ! live_object_endpoint=$(read_exact_setting "${live_object_environment_file}" CAREERTRELLIS_OBJECT_STORE_ENDPOINT) || \
   ! live_object_bucket=$(read_exact_setting "${live_object_environment_file}" CAREERTRELLIS_OBJECT_STORE_BUCKET) || \
   [[ ${rehearsal_object_store} != "r2_encrypted" || ${live_object_store} != "r2_encrypted" ]]; then
    echo "Rehearsal target identity validation failed." >&2
    exit 1
fi

if ! CAREERTRELLIS_REHEARSAL_DATABASE_URL="${rehearsal_database_url}" \
    CAREERTRELLIS_LIVE_MIGRATION_DATABASE_URL="${live_migration_database_url}" \
    CAREERTRELLIS_LIVE_RUNTIME_DATABASE_URL="${live_runtime_database_url}" \
    CAREERTRELLIS_REHEARSAL_OBJECT_ENDPOINT="${rehearsal_object_endpoint}" \
    CAREERTRELLIS_REHEARSAL_OBJECT_BUCKET="${rehearsal_object_bucket}" \
    CAREERTRELLIS_LIVE_OBJECT_ENDPOINT="${live_object_endpoint}" \
    CAREERTRELLIS_LIVE_OBJECT_BUCKET="${live_object_bucket}" \
    "${release_dir}/backend/venv/bin/python" -c '
import os
import sys
from urllib.parse import unquote, urlsplit

def database_identity(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError
    database = unquote(parsed.path.lstrip("/"))
    if not parsed.hostname or not database:
        raise ValueError
    return parsed.hostname.casefold(), parsed.port or 5432, database

def object_identity(endpoint, bucket):
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not bucket:
        raise ValueError
    return (
        parsed.scheme.casefold(),
        parsed.hostname.casefold(),
        parsed.port,
        parsed.path.rstrip("/"),
        bucket,
    )

try:
    rehearsal_database = database_identity(os.environ["CAREERTRELLIS_REHEARSAL_DATABASE_URL"])
    live_databases = {
        database_identity(os.environ["CAREERTRELLIS_LIVE_MIGRATION_DATABASE_URL"]),
        database_identity(os.environ["CAREERTRELLIS_LIVE_RUNTIME_DATABASE_URL"]),
    }
    rehearsal_object = object_identity(
        os.environ["CAREERTRELLIS_REHEARSAL_OBJECT_ENDPOINT"],
        os.environ["CAREERTRELLIS_REHEARSAL_OBJECT_BUCKET"],
    )
    live_object = object_identity(
        os.environ["CAREERTRELLIS_LIVE_OBJECT_ENDPOINT"],
        os.environ["CAREERTRELLIS_LIVE_OBJECT_BUCKET"],
    )
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
if rehearsal_database in live_databases or rehearsal_object == live_object:
    raise SystemExit(1)
'; then
    echo "Rehearsal targets must differ from live targets." >&2
    exit 1
fi

if [[ ! -x ${authority_helper} ]]; then
    echo "Hosted authority state is unavailable." >&2
    exit 1
fi
"${authority_helper}" assert-import-allowed

if ! command -v "${systemctl_command}" >/dev/null 2>&1; then
    echo "Service state is unavailable." >&2
    exit 1
fi
for service in careertrellis-web.service careertrellis-worker.service careertrellis-erasure-worker.service; do
    if "${systemctl_command}" is-active --quiet "${service}"; then
        echo "Stop the web, search-worker, and erasure-worker services before rehearsal." >&2
        exit 1
    fi
done

"${release_dir}/deploy/scripts/preflight-sqlite-postgres-import.sh" "${release_dir}"
"${release_dir}/deploy/scripts/import-sqlite-postgres.sh" \
    "${release_dir}" \
    "${rehearsal_database_environment_file}" \
    "${rehearsal_object_environment_file}"
printf '{"authority":"sqlite-pre-cutover","no_omissions":true,"release":"%s","services_inactive":true,"targets":"disposable"}\n' "${release_revision}"
