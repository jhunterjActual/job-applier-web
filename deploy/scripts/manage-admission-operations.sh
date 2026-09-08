#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
if [[ ${EUID} -ne 0 || $# -lt 1 || $# -gt 3 ]]; then
    echo "Run the reviewed admission operation as root." >&2
    exit 1
fi
# No credentials or configurable commands/paths in argv. Invoke the verified
# source release directly; this wrapper installs no helper or service itself.
script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
release=$(dirname -- "$(dirname -- "${script_directory}")")
runner=${release}/backend/admission_operations.py
if [[ -L ${BASH_SOURCE[0]} || -L ${runner} || ! -f ${runner} ]]; then
    echo "The reviewed admission operation is unavailable." >&2
    exit 1
fi
for path in "${BASH_SOURCE[0]}" "${runner}" "${release}" "${release}/backend"; do
    if [[ $(stat -c '%u' "${path}") != 0 || $(stat -c '%a' "${path}") =~ [2367][0-7]$ || $(stat -c '%a' "${path}") =~ [2367]$ ]]; then
        echo "The admission operation source has unsafe ownership or write access." >&2
        exit 1
    fi
done
case "$1" in
    db-install|db-enable-login|db-begin|db-quiesce|db-finish|db-initial-begin|db-initial-quiesce|db-initial-finish|check-worker-files) python=/usr/bin/python3 ;;
    web-install|web-migrate|web-initial-migrate) python=${release}/backend/venv/bin/python ;;
    *) echo "Unknown admission operation." >&2; exit 1 ;;
esac
# Isolated interpreter and fresh environment prevent secret/config inheritance.
# The runner admits sibling imports only after its immutable location is checked.
exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
    CAREERTRELLIS_CONFIG_READ_ONLY=1 PYTHONDONTWRITEBYTECODE=1 \
    "${python}" -I -B "${runner}" "$@"
