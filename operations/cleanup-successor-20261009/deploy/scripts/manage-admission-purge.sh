#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
if [[ ${EUID} -ne 0 || $# -ne 1 ]]; then
    echo 'Run the reviewed admission purge operation as root.' >&2
    exit 2
fi
case "$1" in initialize|run|monitor|status|acknowledge) ;; *) exit 2 ;; esac
script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
release=$(dirname -- "$(dirname -- "${script_directory}")")
if [[ ! ${release} =~ ^/opt/careertrellis/staging/releases/[0-9a-f]{40}$ ]]; then
    echo 'An exact immutable operational release is required.' >&2
    exit 2
fi
ancestor=${release}
while [[ ${ancestor} != / ]]; do
    if [[ -L ${ancestor} || $(stat -c '%u' "${ancestor}") != 0 || $(stat -c '%a' "${ancestor}") =~ [2367][0-7]$ || $(stat -c '%a' "${ancestor}") =~ [2367]$ ]]; then
        echo 'Unsafe admission purge ancestor.' >&2
        exit 2
    fi
    ancestor=$(dirname -- "${ancestor}")
done
for path in "${BASH_SOURCE[0]}" "${release}/backend/admission_purge.py" "${release}/backend/admission_operations.py" "${release}" "${release}/backend" "${release}/deploy" "${release}/deploy/scripts"; do
    if [[ -L ${path} || $(stat -c '%u' "${path}") != 0 || $(stat -c '%a' "${path}") =~ [2367][0-7]$ || $(stat -c '%a' "${path}") =~ [2367]$ ]]; then
        echo 'Unsafe admission purge source.' >&2
        exit 2
    fi
done
# Isolated Python otherwise omits sibling modules. A fixed verified directory
# is inserted by this fixed bootstrap; no caller expression or secret enters it.
exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
    /usr/bin/python3 -I -B -c 'import sys,runpy; from pathlib import Path; p=Path(sys.argv[1]); sys.path.insert(0,str(p.parent)); sys.argv=sys.argv[1:]; runpy.run_path(str(p),run_name="__main__")' \
    "${release}/backend/admission_purge.py" "$1"
