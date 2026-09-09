#!/usr/bin/env bash
set -Eeuo pipefail
if [[ ${EUID} -ne 0 ]]; then
    echo "Run the reviewed admission-role bootstrap as root." >&2
    exit 1
fi
script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
bootstrap_sql=${script_directory}/../postgres/bootstrap-admission-roles.sql
database_name=${CAREERTRELLIS_POSTGRES_DATABASE:-careertrellis}
if [[ ! ${database_name} =~ ^[a-z][a-z0-9_]{0,62}$ || ! -f ${bootstrap_sql} || -L ${bootstrap_sql} ]]; then
    echo "Admission bootstrap target or reviewed SQL is invalid." >&2
    exit 1
fi
runuser -u postgres -- psql --dbname="${database_name}" --no-psqlrc \
    --single-transaction --set=ON_ERROR_STOP=1 --file=- < "${bootstrap_sql}"
echo "Admission roles prepared without login credentials; signup remains unchanged."
