#!/usr/bin/env bash
set -euo pipefail

fixed_error() {
    printf '%s\n' \
        'operation=caddy-log-privacy-check' \
        'result=blocked' \
        'error_code=wrapper_invalid' \
        'records=0' \
        'query_records=0' \
        'representative_records=0' \
        'source_disclosed=no' \
        'personal_data_disclosed=no' \
        'secret_disclosed=no'
}

if [[ ${EUID} -ne 0 || $# -ne 0 ]]; then
    fixed_error
    exit 1
fi

if ! script_path=$(/usr/bin/realpath "$0" 2>/dev/null); then
    fixed_error
    exit 1
fi
script_dir=${script_path%/*}
if ! ops_dir=$(/usr/bin/realpath "${script_dir}/../.." 2>/dev/null) || \
   ! release_dir=$(/usr/bin/realpath /opt/careertrellis/staging/current 2>/dev/null); then
    fixed_error
    exit 1
fi
if ! /usr/bin/python3 -I -B - "${ops_dir}" "${script_path}" "${release_dir}" <<'PY' 2>/dev/null
import hashlib,json,os,re,stat,sys
from pathlib import Path

def require(ok):
    if not ok: raise ValueError('ops binding refused')

def checked(path, mode=None, limit=4*1024**2):
    for parent in path.parents:
        info=parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode&0o022)
        require(mode is None or stat.S_IMODE(info.st_mode)==mode)
        require(info.st_size<=limit)
        raw=stream.read(limit+1)
        require(len(raw)<=limit)
        return raw

def metadata(manifest,ops_commit,runtime_commit):
    require(set(manifest)=={'schema','source_commit','runtime_commit','files','runtime_files'})
    require(manifest['schema']=='careertrellis.caddy-ops-binding/v1')
    require(manifest['source_commit']==ops_commit and manifest['runtime_commit']==runtime_commit)
    require(set(manifest['files'])=={'deploy/caddy/Caddyfile.staging','deploy/scripts/check-caddy-access-log-privacy-ops.sh','backend/caddy_log_privacy.py'})
    require(set(manifest['runtime_files'])=={'backend/caddy_log_privacy.py','python_executable'})
    require(all(isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) for values in (manifest['files'],manifest['runtime_files']) for v in values.values()))
    require(manifest['files']['backend/caddy_log_privacy.py']==manifest['runtime_files']['backend/caddy_log_privacy.py'])

ops,script,runtime=map(Path,sys.argv[1:])
require(os.geteuid()==0)
require(re.fullmatch('/opt/careertrellis/ops/[a-f0-9]{40}',str(ops)))
require(re.fullmatch('/opt/careertrellis/staging/releases/[a-f0-9]{40}',str(runtime)))
require(script==ops/'deploy/scripts/check-caddy-access-log-privacy-ops.sh')
manifest=json.loads(checked(ops/'ops-manifest.json',0o600,65536))
metadata(manifest,ops.name,runtime.name)
for relative,digest in manifest['files'].items():
    require(hashlib.sha256(checked(ops/relative)).hexdigest()==digest)
require(checked(Path('/etc/caddy/Caddyfile'),0o644)==checked(ops/'deploy/caddy/Caddyfile.staging'))
require(hashlib.sha256(checked(runtime/'backend/caddy_log_privacy.py')).hexdigest()==manifest['runtime_files']['backend/caddy_log_privacy.py'])
entry=runtime/'backend/venv/bin/python'
for parent in entry.parents:
    info=parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022)
require(entry.lstat().st_uid==0)
python=entry.resolve(strict=True)
require(re.fullmatch('/usr/bin/python3(?:[.][0-9]+)?',str(python)))
require(hashlib.sha256(checked(python,limit=64*1024**2)).hexdigest()==manifest['runtime_files']['python_executable'])
PY
then
    fixed_error
    exit 1
fi
config_path=/etc/caddy/Caddyfile
log_prefix=/var/log/caddy/careertrellis-staging-access-v7
log_path=${log_prefix}.log

collect_log_paths() {
    shopt -s nullglob
    local rotations=("${log_prefix}"-*.log "${log_prefix}"-*.log.gz)
    shopt -u nullglob
    collected_paths=("${log_path}" "${rotations[@]}")
}

snapshot_metadata() {
    local candidate
    for candidate in "${collected_paths[@]}"; do
        /usr/bin/stat -c '%d:%i:%s:%y' "${candidate}" 2>/dev/null || return 1
    done
}

collect_log_paths
log_paths=("${collected_paths[@]}")
arguments=(--config "${config_path}")
for candidate in "${log_paths[@]}"; do
    if [[ ! -f ${candidate} || -L ${candidate} || \
          $(/usr/bin/stat -c '%U:%G %a' "${candidate}" 2>/dev/null) != 'caddy:caddy 600' ]]; then
        fixed_error
        exit 1
    fi
    arguments+=(--log "${candidate}")
done

if ! before_metadata=$(snapshot_metadata); then
    fixed_error
    exit 1
fi
set +e
result=$(/usr/bin/env -i \
    PATH=/usr/bin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    "${release_dir}/backend/venv/bin/python" -B \
    "${ops_dir}/backend/caddy_log_privacy.py" \
    "${arguments[@]}" 2>/dev/null)
status=$?
set -e
if [[ ${status} -ne 0 ]]; then
    printf '%s\n' "${result}"
    exit "${status}"
fi
collect_log_paths
if [[ ${#collected_paths[@]} -ne ${#log_paths[@]} ]]; then
    fixed_error
    exit 1
fi
for index in "${!log_paths[@]}"; do
    if [[ ${collected_paths[index]} != "${log_paths[index]}" ]]; then
        fixed_error
        exit 1
    fi
done
if ! after_metadata=$(snapshot_metadata) || [[ ${after_metadata} != "${before_metadata}" ]]; then
    fixed_error
    exit 1
fi
printf '%s\n' "${result}"
