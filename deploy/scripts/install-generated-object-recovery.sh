#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

# Operations-only installation: no app deployment, restart, provider operation,
# backup, or restore. Caller retains the reviewed source bundle and prior files.
readonly acknowledgement=I_HAVE_REVIEWED_THE_GENERATED_OBJECT_OPERATIONS_BUNDLE
[[ ${EUID} -eq 0 && $# -eq 2 && ${2} == "${acknowledgement}" ]] || exit 1
bundle=$1
[[ -d ${bundle} && ! -L ${bundle} && $(realpath "${bundle}") == "${bundle}" ]] || exit 1
for directory in "${bundle}" /usr/local /usr/local/libexec /usr/local/libexec/careertrellis /usr/local/sbin /var/lib/careertrellis; do
    /usr/bin/python3 -B -c 'import pathlib,stat,sys
p=pathlib.Path(sys.argv[1])
for d in (p,*p.parents):
 s=d.lstat()
 if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022: raise SystemExit(1)
' "${directory}"
done
[[ $(stat -c '%U:%G %a' /usr/local/libexec/careertrellis) == 'root:root 755' ]] || exit 1
readonly lock_helper=/usr/local/libexec/careertrellis/maintenance-lock.sh
[[ -f ${lock_helper} && ! -L ${lock_helper} && $(stat -c '%U:%G %a' "${lock_helper}") == 'root:root 755' ]] || exit 1
source "${lock_helper}"
acquire_careertrellis_maintenance_lock
exec 9>/run/lock/careertrellis-generated-object-recovery.lock
flock -n 9 || exit 1
readonly health_lock=/run/lock/careertrellis-backup-health-objects.lock
[[ -f ${health_lock} && ! -L ${health_lock} && $(stat -c '%U:%G %a' "${health_lock}") == 'root:root 600' ]] || exit 1
exec 7>"${health_lock}"
flock -n 7 || exit 1

sources=(backend/generated_object_recovery.py backend/backup_health.py deploy/scripts/mirror-generated-objects-onedrive.sh deploy/scripts/test-generated-object-restore.sh)
targets=(/usr/local/libexec/careertrellis/generated_object_recovery.py /usr/local/libexec/careertrellis/backup_health.py /usr/local/sbin/careertrellis-mirror-generated-objects /usr/local/sbin/careertrellis-test-generated-object-restore)
for i in "${!sources[@]}"; do
    source_path=${bundle}/${sources[i]}
    target_path=${targets[i]}
    [[ -f ${source_path} && ! -L ${source_path} && $(realpath "${source_path}") == "${source_path}" && ! -L ${target_path} ]] || exit 1
    [[ ! -e ${target_path} || -f ${target_path} ]] || exit 1
    /usr/bin/python3 -B -c 'import os,stat,sys
s=os.lstat(sys.argv[1])
if s.st_uid != 0 or s.st_mode & 0o022 or not stat.S_ISREG(s.st_mode): raise SystemExit(1)
' "${source_path}"
    case "${source_path}" in
        *.py) /usr/bin/python3 -B -c 'import pathlib,sys; compile(pathlib.Path(sys.argv[1]).read_text(),sys.argv[1],"exec")' "${source_path}" ;;
        *.sh) /bin/bash -n "${source_path}" ;;
    esac
done
capture_root=/var/lib/careertrellis/generated-object-captures
if [[ -e ${capture_root} || -L ${capture_root} ]]; then
    [[ -d ${capture_root} && ! -L ${capture_root} && $(stat -c '%U:%G %a' "${capture_root}") == 'root:careertrellis-object-recovery 750' ]] || exit 1
else
    install -d -o root -g careertrellis-object-recovery -m 0750 "${capture_root}"
fi
for i in "${!sources[@]}"; do
    mode=0644
    [[ ${targets[i]} != /usr/local/sbin/* ]] || mode=0755
    install -o root -g root -m "${mode}" "${bundle}/${sources[i]}" "${targets[i]}"
    cmp -s "${bundle}/${sources[i]}" "${targets[i]}"
done
runuser -u careertrellis-object-recovery -- /usr/bin/python3 -B /usr/local/libexec/careertrellis/generated_object_recovery.py --help >/dev/null
printf '{"operation":"generated_object_ops_install","result":"ok","installed_files":4,"application_restart":false,"provider_operations":false}\n'
