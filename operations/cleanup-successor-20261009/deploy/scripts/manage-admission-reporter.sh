#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077
if [[ ${EUID} -ne 0 || $# -ne 1 ]]; then exit 2; fi
case "$1" in report|enter) ;; *) exit 2 ;; esac
script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
release=$(dirname -- "$(dirname -- "${script_directory}")")
if [[ ! ${release} =~ ^/opt/careertrellis/staging/releases/[0-9a-f]{40}$ ]]; then exit 2; fi
ancestor=${release}
while [[ ${ancestor} != / ]]; do
    if [[ -L ${ancestor} || $(stat -c '%u' "${ancestor}") != 0 || $(stat -c '%a' "${ancestor}") =~ [2367][0-7]$ || $(stat -c '%a' "${ancestor}") =~ [2367]$ ]]; then exit 2; fi
    ancestor=$(dirname -- "${ancestor}")
done
for path in "${BASH_SOURCE[0]}" "${release}/backend/admission_reporter.py" "${release}/backend/admission_operations.py" "${release}/backend/admission_purge.py" "${release}/backend/admission_alert_contract.py" "${release}/backend/admission_healthchecks.py" "${release}/backend/totp_cleanup_alert_contract.py" "${release}/backend/identity_entry.py" "${release}/backend/identity_evidence.py" "${release}/backend/identity_operator.py" "${release}/backend/session_authorization.py" "${release}" "${release}/backend" "${release}/deploy" "${release}/deploy/scripts"; do
    if [[ -L ${path} || $(stat -c '%u' "${path}") != 0 || $(stat -c '%a' "${path}") =~ [2367][0-7]$ || $(stat -c '%a' "${path}") =~ [2367]$ ]]; then exit 2; fi
done
exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 \
    /usr/bin/python3 -I -B -c 'import sys,runpy; from pathlib import Path; p=Path(sys.argv[1]); sys.path.insert(0,str(p.parent)); sys.argv=sys.argv[1:]; runpy.run_path(str(p),run_name="__main__")' \
    "${release}/backend/admission_reporter.py" "$1"
