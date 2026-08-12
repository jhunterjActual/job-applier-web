#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run the provider-secret rotation as root." >&2
    exit 1
fi

release_dir=${1:-}
owner_user_id=${2:-}
runtime_environment_file=${3:-/etc/careertrellis/postgres-runtime.env}
secret_environment_file=${4:-/etc/careertrellis/provider-secrets.env}
if [[ -z ${release_dir} || ! -f ${release_dir}/backend/rotate_provider_secrets.py ]]; then
    echo "Usage: $0 /absolute/path/to/reviewed-release OWNER_USER_ID [runtime-env] [secret-env]" >&2
    exit 1
fi
if [[ ! ${owner_user_id} =~ ^[1-9][0-9]*$ ]]; then
    echo "OWNER_USER_ID must be a positive integer." >&2
    exit 1
fi
release_dir=$(realpath "${release_dir}")
case "${release_dir}" in
    /opt/careertrellis/staging/releases/*) ;;
    *) echo "The reviewed release must be inside the staging release directory." >&2; exit 1 ;;
esac
for environment_file in "${runtime_environment_file}" "${secret_environment_file}"; do
    if [[ ! -f ${environment_file} || $(stat -c '%U:%G %a' "${environment_file}") != "root:root 600" ]]; then
        echo "Every provider-secret environment file must be root:root mode 0600." >&2
        exit 1
    fi
done

exec 9>/run/lock/careertrellis-provider-secret-rotation.lock
if ! flock -n 9; then
    echo "Another provider-secret rotation is already running." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${runtime_environment_file}"
# shellcheck disable=SC1090
source "${secret_environment_file}"
set +a
export CAREERTRELLIS_ENVIRONMENT=staging
export CAREERTRELLIS_ROTATE_OWNER_USER_ID="${owner_user_id}"
cd "${release_dir}/backend"
./venv/bin/python rotate_provider_secrets.py
