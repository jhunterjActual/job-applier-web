"""Portable tests of fallback marker census and current source-byte bindings."""
import datetime as dt
from pathlib import Path
import hashlib
import re
import stat
import sys
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from deploy.paired_maintenance import runtime,db_host

ROOT=Path(__file__).resolve().parents[1]
NAMES=('activation.json','activation-intent.json','activation-pending.json','activation-finalized.json')
SHELLS=('mirror-pgbackrest-onedrive.sh','mirror-generated-objects-onedrive.sh','manage-backup-health.sh')


class Tests(unittest.TestCase):
    def test_all_four_trigger_and_verified_absence_censuses(self):
        for script in SHELLS:
            source=(ROOT/'scripts'/script).read_text()
            # Presence checks include symlinks, including broken marker links.
            for name in NAMES:self.assertIn(name,source)
            for variable in ('retention_pending','retention_finalized'):
                if script!='manage-backup-health.sh':
                    self.assertIn('-e ${'+variable+'}',source);self.assertIn('-L ${'+variable+'}',source)
            if script=='manage-backup-health.sh':
                for name in NAMES:
                    self.assertIn('-e /etc/careertrellis/onedrive-retention/'+name,source)
                    self.assertIn('-L /etc/careertrellis/onedrive-retention/'+name,source)
            snippet=re.search(r"<<'PY'[^\n]*\n(.*?)\nPY",source,re.S).group(1)
            # Execute ONLY the extracted pure absence census, fake filesystem.
            for present in (None,*NAMES):
                queried=[]
                class FakePath:
                    def __init__(self,path):self.path=str(path)
                    def __truediv__(self,part):return FakePath(self.path+'/'+part)
                    @property
                    def parents(self):return [FakePath('/etc'),FakePath('/')]
                    def lstat(self):
                        queried.append(self.path)
                        if self.path in ('/','/etc'):return SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0)
                        if present and self.path.endswith('/'+present):return SimpleNamespace(st_mode=stat.S_IFLNK|0o777,st_uid=0)
                        raise FileNotFoundError()
                with patch('pathlib.Path',FakePath):
                    if present:
                        with self.assertRaises(SystemExit):exec(compile(snippet,script,'exec'),{})
                    else:
                        exec(compile(snippet,script,'exec'),{})
                        self.assertTrue(all(any(path.endswith('/'+name) for path in queried) for name in NAMES))

    def test_current_native_pins_match_exact_shell_bytes(self):
        pairs=(('mirror-pgbackrest-onedrive.sh','/usr/local/sbin/careertrellis-mirror-pgbackrest-onedrive','db'),
            ('manage-backup-health.sh','/usr/local/sbin/careertrellis-backup-health','db'),
            ('mirror-generated-objects-onedrive.sh',runtime.HELPER,'web'))
        for name,path,role in pairs:
            digest=hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
            self.assertEqual(runtime.PINS[role][path],digest)
            if role=='db':self.assertEqual((db_host.MIRROR_PINS if 'mirror' in name else db_host.HEALTH_PINS)[path],digest)

    def test_paired_full_schedule_ready_heredoc_and_policy_gates(self):
        source=(ROOT/'scripts'/'manage-backup-health.sh').read_text(encoding='utf-8')
        match=re.search(r"<<'PAIRED_PY'[^\n]*\n(.*?)\nPAIRED_PY",source,re.S)
        self.assertIsNotNone(match)
        snippet=match.group(1)
        now=dt.datetime.now(dt.timezone.utc)
        base_data={'enabled':True,'source_readonly_attested':True,'no_external_writers_attested':True,
            'source_custody_reviewed_at':(now-dt.timedelta(minutes=5)).isoformat()}

        def run_snippet(policy_data=None,valid_from=None,valid_until=None,entry_mode=stat.S_IFREG|0o644,
                        init_mode=None,entry_uid=0,dir_mode=stat.S_IFDIR|0o755,verify_error=None):
            data=dict(base_data,**(policy_data or {}))
            policy=SimpleNamespace(data=data,
                valid_from=valid_from or (now-dt.timedelta(days=1)),
                valid_until=valid_until or (now+dt.timedelta(days=30)))
            class FakeEntry:
                @staticmethod
                def verify_install():
                    if verify_error:raise verify_error
                    return 'a'*40
                @staticmethod
                def load_policy():return policy
            pkg=types.ModuleType('paired_maintenance');pkg.entry=FakeEntry
            root='/usr/local/libexec/careertrellis/paired_maintenance'
            class FakePath:
                def __init__(self,path):self._p=str(path)
                def __truediv__(self,part):return FakePath(self._p.rstrip('/')+'/'+part)
                def __str__(self):return self._p
                @property
                def parent(self):
                    head,_=self._p.rsplit('/',1)
                    return FakePath(head or '/')
                @property
                def parents(self):
                    cur=self._p;out=[]
                    while cur!='/':
                        cur=cur.rsplit('/',1)[0] or '/'
                        out.append(FakePath(cur))
                    return out
                def lstat(self):
                    if self._p==root+'/__init__.py':
                        return SimpleNamespace(st_mode=init_mode if init_mode is not None else entry_mode,
                                               st_uid=entry_uid,st_gid=0,st_nlink=1)
                    if self._p in (root+'/entry.py',root+'/manifest.json'):
                        return SimpleNamespace(st_mode=entry_mode,st_uid=entry_uid,st_gid=0,st_nlink=1)
                    if self._p==root:
                        return SimpleNamespace(st_mode=dir_mode,st_uid=0,st_gid=0,st_nlink=2)
                    return SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0,st_gid=0,st_nlink=2)
            with patch('pathlib.Path',FakePath),patch.dict(sys.modules,{'paired_maintenance':pkg,'paired_maintenance.entry':FakeEntry}),\
                 patch.object(sys,'path',list(sys.path)):
                exec(compile(snippet,'manage-backup-health.sh','exec'),{})

        run_snippet()
        run_snippet(policy_data={'source_custody_reviewed_at':None})
        for failure in (
            {'policy_data':{'enabled':False}},
            {'policy_data':{'source_readonly_attested':False}},
            {'policy_data':{'no_external_writers_attested':False}},
            {'valid_from':now+dt.timedelta(minutes=5)},
            {'valid_until':now-dt.timedelta(seconds=1)},
            {'policy_data':{'source_custody_reviewed_at':(now+dt.timedelta(minutes=5)).isoformat()}},
            {'init_mode':stat.S_IFLNK|0o777},
            {'init_mode':stat.S_IFREG|0o664},
            {'entry_mode':stat.S_IFLNK|0o777},
            {'entry_mode':stat.S_IFREG|0o664},
            {'entry_uid':1000},
            {'dir_mode':stat.S_IFDIR|0o775},
            {'verify_error':RuntimeError('tampered')},
        ):
            with self.subTest(failure=failure),self.assertRaises((AssertionError,RuntimeError)):
                run_snippet(**failure)

        paired_fn=source[source.index('paired_full_schedule_ready() {'):source.index('if [[ -L ${core}')]
        self.assertIn('/usr/bin/systemctl show careertrellis-pgbackrest-full.service --property=LoadState --value',paired_fn)
        self.assertNotIn('systemctl show "${unit_name}"',paired_fn)
        self.assertIn('/usr/bin/systemctl list-unit-files --no-legend --no-pager --full "${unit_name}"',paired_fn)
        self.assertIn('careertrellis-paired-db@.service',paired_fn)
        self.assertIn('careertrellis-paired-db-recovery@.service',paired_fn)
        self.assertIn('static | enabled | enabled-runtime',paired_fn)


if __name__=='__main__':unittest.main()
