"""Offline path/manifest/fault controls. Never invokes preparation or a host."""
import ast
import base64
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import hashlib
import io
import json
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch
from pathlib import PurePosixPath
import prepare_release as p
import run_checks as checks

def archive(entries):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as tar:
        for name,body,kind in entries:
            info=tarfile.TarInfo(name);info.mode=0o644
            if kind=='file':info.size=len(body);tar.addfile(info,io.BytesIO(body))
            else:info.type=kind;info.linkname='outside';tar.addfile(info)
    return out.getvalue()
BASE_ENTRIES=[('backend/requirements.in',b'synthetic','file'),('backend/requirements.txt',b'synthetic-lock','file')]
def plan():
    return {'schema':'careertrellis.app-preparation-plan/v1','base':p.BASE,'previous':p.PREVIOUS,
        'revision':p.TARGET,'build':p.SPECS[p.TARGET]['build'],'ops_commit':'a'*40,
        'source_sha256':p.SPECS[p.TARGET]['sha256'],'provenance_sha256':p.PROVENANCE_SHA,'source_diff_sha256':'c'*64,
        'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()}

class Tests(unittest.TestCase):
    def test_exact_plan(self):p.validate_plan(plan(),p.TARGET)
    def test_wrong_release_build_source_and_extra_field_refused(self):
        for key,value in [('revision',p.CORRECTION),('build','20261026.99'),('source_sha256','a'*64),
                          ('base','f'*40),('previous',p.BASE),('unexpected',True)]:
            with self.subTest(key=key):
                value_plan=plan();value_plan[key]=value
                with self.assertRaises(p.Refused):p.validate_plan(value_plan,p.TARGET)
    def test_expired_or_unbounded_plan_refused(self):
        for seconds in (-1,7200):
            value=plan();value['expires_at']=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()
            with self.assertRaises(p.Refused):p.validate_plan(value,p.TARGET)
    def test_archive_manifest_exact(self):
        data=p.archive_files(archive(BASE_ENTRIES))
        self.assertEqual(set(data),{'backend/requirements.in','backend/requirements.txt'})
        self.assertEqual(data['backend/requirements.txt']['sha256'],hashlib.sha256(b'synthetic-lock').hexdigest())
    def test_archive_links_special_duplicate_traversal_refused(self):
        for extra in [('escape',b'',tarfile.SYMTYPE),('hard',b'',tarfile.LNKTYPE),
                      ('device',b'',tarfile.CHRTYPE),('../escape',b'x','file'),
                      ('/absolute',b'x','file'),('backend/requirements.txt',b'duplicate','file')]:
            with self.subTest(extra=extra[0]):
                with self.assertRaises(p.Refused):p.archive_files(archive(BASE_ENTRIES+[extra]))
    def test_unexpected_archive_without_dependency_files_refused(self):
        with self.assertRaises(p.Refused):p.archive_files(archive([('hello',b'x','file')]))
    def test_noncanonical_paths_refused(self):
        for name in ('a/./b','a//b','../b','/b','a\\b','.','', 'bad\nname'):
            with self.subTest(name=name):
                with self.assertRaises(p.Refused):p.safe_name(name)
    def test_capacity_reserves_floor_and_peak(self):
        p.validate_capacity(p.FLOOR+123,123)
        with self.assertRaises(p.Refused):p.validate_capacity(p.FLOOR+122,123)
        with self.assertRaises(p.Refused):p.validate_capacity(p.FLOOR,0)
    def test_peak_counts_materialization_failure_margin(self):
        self.assertGreaterEqual(p.needed_bytes({'source':{'bytes':50}}, {'site_packages':{'one':{'bytes':100}}}),350+1024**3)
    def test_reused_package_provenance_is_full_equality(self):
        package={'a.py':{'sha256':'a'*64}}
        accepted={'backend/venv/lib/python3.12/site-packages/a.py':'a'*64}
        p.verify_reused_packages(package,accepted)
        for changed in ({}, {'a.py':{'sha256':'b'*64}}, {**package,'unknown.py':{'sha256':'b'*64}}):
            with self.assertRaises(p.Refused):p.verify_reused_packages(changed,accepted)
    def test_env_is_allowlist_no_inherited_secrets(self):
        value=checks.isolated_env(Path('/synthetic'))
        self.assertEqual(value['CAREERTRELLIS_ENVIRONMENT'],'local')
        self.assertEqual(value['CAREERTRELLIS_DATA_STORE'],'sqlite')
        self.assertNotIn('ANTHROPIC_API_KEY',value);self.assertNotIn('DATABASE_URL',value)
        self.assertEqual(value['PLAYWRIGHT_BROWSERS_PATH'],str(Path('/synthetic')/'browsers'))
    def test_no_package_install_or_production_service_commands(self):
        tree=ast.parse(Path(p.__file__).read_text())
        strings=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
        self.assertFalse(any(s in ('apt','apt-get','pip','install-deps','daemon-reload','restart') for s in strings))
        self.assertIn('--without-pip',strings);self.assertIn('--copies',strings)
    def test_terminal_execution_rejects_pid_job_failure_or_populated_cgroup(self):
        unit='careertrellis-release-prep-'+p.TARGET[:12]+'-20261026'
        state={'LoadState':'loaded','ActiveState':'active','SubState':'exited','MainPID':'0','ControlPID':'0',
               'Job':'','Result':'success','ExecMainStatus':'0','InvocationID':'a'*32}
        group={'path':'/system.slice/'+unit+'.service','exists':True,'processes':0,'populated':0}
        self.assertEqual(p.execution_terminal(unit,state,group)['invocation_id'],'a'*32)
        for key,value in [('MainPID','123'),('ControlPID','123'),('Job','pending'),('Result','timeout'),
                          ('ExecMainStatus','1'),('InvocationID','invalid'),('ActiveState','failed')]:
            with self.subTest(key=key):
                with self.assertRaises(p.Refused):p.execution_terminal(unit,{**state,key:value},group)
        for key in ('processes','populated'):
            with self.assertRaises(p.Refused):p.execution_terminal(unit,state,{**group,key:1})
    def test_collection_never_stops_changed_invocation_or_nonterminal_unit(self):
        unit='careertrellis-release-prep-'+p.TARGET[:12]+'-20261026'
        state={'LoadState':'loaded','ActiveState':'active','SubState':'exited','MainPID':'0','ControlPID':'0',
               'Job':'','Result':'success','ExecMainStatus':'0','InvocationID':'b'*32}
        group={'processes':0,'populated':0}
        with patch.object(p,'unit_state',return_value=state),patch.object(p,'cgroup_state',return_value=group),patch.object(p.subprocess,'run') as run:
            with self.assertRaises(p.Refused):p.collect_empty_unit(unit,{'invocation_id':'a'*32},object())
            run.assert_not_called()
    def test_alias_mount_and_write_open_denial_required_without_content_write(self):
        alias=Path('/synthetic/browsers');leaf=alias/'public-file'
        with patch.object(checks.os,'O_NOFOLLOW',getattr(checks.os,'O_NOFOLLOW',0),create=True),patch.object(Path,'is_file',return_value=True),patch.object(Path,'is_symlink',return_value=False),patch.object(checks,'verify_opened_readonly',return_value={'verified':True}) as bound,patch.object(checks.os,'open',side_effect=PermissionError(13,'synthetic denied')) as opened:
            checks.require_readonly_bind(alias,leaf);bound.assert_called_once_with(leaf)
            flags=opened.call_args.args[1]
            self.assertEqual(flags&checks.os.O_WRONLY,checks.os.O_WRONLY)
            self.assertEqual(flags&checks.os.O_TRUNC,0);self.assertEqual(flags&checks.os.O_CREAT,0)
        with patch.object(Path,'is_file',return_value=True),patch.object(Path,'is_symlink',return_value=False),patch.object(checks,'verify_opened_readonly',side_effect=checks.ReadonlyProofRefused('RO_VFS_RW')),patch.object(checks.os,'open') as opened:
            with self.assertRaises(RuntimeError):checks.require_readonly_bind(alias,leaf)
            opened.assert_not_called()
        with patch.object(checks.os,'O_NOFOLLOW',getattr(checks.os,'O_NOFOLLOW',0),create=True),patch.object(Path,'is_file',return_value=True),patch.object(Path,'is_symlink',return_value=False),patch.object(checks,'verify_opened_readonly',return_value={'verified':True}),patch.object(checks.os,'open',return_value=123),patch.object(checks.os,'close') as closed:
            with self.assertRaises(RuntimeError):checks.require_readonly_bind(alias,leaf)
            closed.assert_called_once_with(123)

if __name__=='__main__':unittest.main()
