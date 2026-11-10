"""Read-only extraction from the one retained V5 synthetic attempt; no stderr."""
import hashlib
import json
import os
from pathlib import Path
import stat

ROOT=Path('/run/careertrellis-release-preparation-sandbox-probe-v5-20261026')
SOURCE_SHA='82adc6947366a70243836c0164c17470b73cd9b2a0f39d715ef287a0bb01e6f6'
STUB_SHA='f45bfdea2e0cee443de2006b9954e816430756fd3113f83236daf6556b3f36ec'
CONTROLS={'identity','loopback_only','no_new_privileges','no_capabilities','readonly_mount','readonly_bind','owned_write'}
STAGES=['D00_ARGUMENTS','D10_IDENTITY','D20_NETWORK','D30_PRIVILEGES','D40_RELEASE_MOUNT',
        'D50_ALIAS_MOUNT','D60_SOURCE_READ','D70_BROWSER_READ','D80_ALIAS_WRITE_DENIAL','D90_OWNED_WRITE','D99_COMPLETE']

def need(value):
    if not value:raise ValueError('fixed_synthetic_contract_refused')

def pairs(values):
    result={}
    for key,value in values:
        need(key not in result);result[key]=value
    return result

def parse(raw):
    need(len(raw)<=32768)
    controls=None;stages=[]
    for line in raw.decode('utf8',errors='strict').splitlines():
        value=json.loads(line,object_pairs_hook=pairs);need(type(value) is dict)
        if value.get('schema')=='careertrellis.sandbox-stub-diagnostic/v1':
            need(set(value)=={'schema','stage','outcome'} and value['outcome']=='entered')
            stages.append(value['stage'])
        else:
            need(controls is None and set(value)==CONTROLS and all(v is True for v in value.values()))
            controls=value
    need(controls is not None and stages==STAGES)
    return controls,stages

def read_fixed(path,mode,limit):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
             and stat.S_IMODE(before.st_mode)==mode and before.st_size<=limit)
        with os.fdopen(os.dup(fd),'rb') as stream:raw=stream.read(limit+1)
        after=os.fstat(fd)
        need(len(raw)<=limit and (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
             (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns))
        return raw
    finally:os.close(fd)

def main():
    try:
        raw=read_fixed(ROOT/'client-stdout.bin',0o600,32768)
        controls,stages=parse(raw)
        fixtures={'readonly/input.bin':b'public-synthetic','browser-source/input.bin':b'public-browser-synthetic'}
        for name,expected in fixtures.items():need(read_fixed(ROOT/name,0o640,128)==expected)
        need(hashlib.sha256(read_fixed(ROOT/'readonly/probe.py',0o640,32768)).hexdigest()==STUB_SHA)
        print(json.dumps({'schema':'careertrellis.synthetic-controls-extraction/v1','result':'controls_verified',
            'attempt_source_sha256':SOURCE_SHA,'captured_stdout_sha256':hashlib.sha256(raw).hexdigest(),
            'fixture_stub_sha256':STUB_SHA,
            'captured_stdout_bytes':len(raw),'controls':controls,'stages':stages,
            'fixture_sha256':{name:hashlib.sha256(value).hexdigest() for name,value in fixtures.items()},
            'wrapper_result':'failed_P60_EMPTY_COLLECTION','overall_wrapper_pass':False},sort_keys=True))
        return 0
    except BaseException:
        print('{"result":"refused","failure_code":"SYNTHETIC_EXTRACTION_CONTRACT"}')
        return 1

if __name__=='__main__':raise SystemExit(main())
