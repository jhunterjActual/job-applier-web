#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this pgBackRest restore rehearsal as root." >&2
    exit 1
fi

stanza=${CAREERTRELLIS_PGBACKREST_STANZA:-careertrellis}
postgres_version=${CAREERTRELLIS_POSTGRES_VERSION:-18}
database_name=${CAREERTRELLIS_POSTGRES_DATABASE:-careertrellis}
test_port=${CAREERTRELLIS_RESTORE_TEST_PORT:-55432}
restore_root=/var/lib/postgresql/careertrellis-restore-tests
pg_ctl="/usr/lib/postgresql/${postgres_version}/bin/pg_ctl"
pg_checksums="/usr/lib/postgresql/${postgres_version}/bin/pg_checksums"

if [[ ! -x ${pg_ctl} || ! -x ${pg_checksums} ]]; then
    echo "PostgreSQL ${postgres_version} restore-test utilities are missing." >&2
    exit 1
fi
if [[ ! ${test_port} =~ ^[0-9]+$ || ${test_port} -lt 1024 || ${test_port} -gt 65535 ]]; then
    echo "CAREERTRELLIS_RESTORE_TEST_PORT is invalid." >&2
    exit 1
fi

install -d -o postgres -g postgres -m 0700 "${restore_root}"
restore_directory=$(runuser -u postgres -- mktemp -d "${restore_root}/restore.XXXXXX")
restore_directory=$(realpath "${restore_directory}")
if [[ ${restore_directory} != "${restore_root}/"* ]]; then
    echo "The restore-test directory escaped its dedicated root." >&2
    exit 1
fi

cleanup() {
    local exit_status=$?
    local cleanup_failed=false
    if [[ ${exit_status} -ne 0 && -f ${restore_directory}/postgresql.restore-test.log ]]; then
        echo "PostgreSQL restore-test log:" >&2
        tail -n 80 "${restore_directory}/postgresql.restore-test.log" >&2
    fi
    if runuser -u postgres -- "${pg_ctl}" --pgdata="${restore_directory}" status >/dev/null 2>&1; then
        if ! runuser -u postgres -- "${pg_ctl}" \
            --pgdata="${restore_directory}" \
            --mode=fast \
            --wait \
            --timeout=60 \
            stop >/dev/null 2>&1; then
            echo "The restore-test server could not be stopped; retaining ${restore_directory}." >&2
            cleanup_failed=true
        fi
    fi
    if [[ ${cleanup_failed} == false && -n ${restore_directory:-} && ${restore_directory} == "${restore_root}/"* ]]; then
        rm -rf --one-file-system -- "${restore_directory}"
    fi
    trap - EXIT
    if [[ ${cleanup_failed} == true ]]; then
        exit 1
    fi
    exit "${exit_status}"
}
trap cleanup EXIT

runuser -u postgres -- pgbackrest \
    --stanza="${stanza}" \
    --pg1-path="${restore_directory}" \
    --type=immediate \
    --target-action=promote \
    restore

socket_directory="${restore_directory}/socket"
test_config="${restore_directory}/postgresql.restore-test.conf"
test_hba="${restore_directory}/pg_hba.restore-test.conf"
test_ident="${restore_directory}/pg_ident.restore-test.conf"
install -d -o postgres -g postgres -m 0700 "${socket_directory}"
cat >"${test_hba}" <<EOF
local all postgres peer
local all all reject
EOF
cat >"${test_config}" <<EOF
data_directory = '${restore_directory}'
hba_file = '${test_hba}'
ident_file = '${test_ident}'
listen_addresses = ''
port = ${test_port}
unix_socket_directories = '${socket_directory}'
max_connections = 50
shared_buffers = '64MB'
archive_mode = off
logging_collector = off
EOF
install -o postgres -g postgres -m 0600 /dev/null "${test_ident}"
chown postgres:postgres "${test_config}" "${test_hba}"
chmod 0600 "${test_config}" "${test_hba}"

runuser -u postgres -- "${pg_ctl}" \
    --pgdata="${restore_directory}" \
    --options="-c config_file=${test_config}" \
    --log="${restore_directory}/postgresql.restore-test.log" \
    --wait \
    --timeout=90 \
    start

restore_result=$(runuser -u postgres -- psql \
    --host="${socket_directory}" \
    --port="${test_port}" \
    --username=postgres \
    --dbname="${database_name}" \
    --no-psqlrc \
    --tuples-only \
    --no-align \
    --command="SELECT current_database(), current_setting('server_version'), pg_is_in_recovery()")
if [[ ${restore_result} == "${database_name}|"*"|t" ]]; then
    runuser -u postgres -- "${pg_ctl}" \
        --pgdata="${restore_directory}" \
        --wait \
        --timeout=60 \
        promote
    restore_result=$(runuser -u postgres -- psql \
        --host="${socket_directory}" \
        --port="${test_port}" \
        --username=postgres \
        --dbname="${database_name}" \
        --no-psqlrc \
        --tuples-only \
        --no-align \
        --command="SELECT current_database(), current_setting('server_version'), pg_is_in_recovery()")
fi
if [[ ${restore_result} != "${database_name}|"*"|f" ]]; then
    echo "The restored PostgreSQL cluster did not become a writable primary." >&2
    exit 1
fi

runuser -u postgres -- "${pg_ctl}" \
    --pgdata="${restore_directory}" \
    --mode=fast \
    --wait \
    --timeout=60 \
    stop

runuser -u postgres -- "${pg_checksums}" --check --pgdata="${restore_directory}"

echo "Restore rehearsal succeeded: ${restore_result}"
echo "The isolated restore used a Unix socket only and its temporary data directory was removed."
