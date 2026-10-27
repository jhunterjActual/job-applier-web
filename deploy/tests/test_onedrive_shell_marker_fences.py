"""Portable tests of fallback marker census and current source-byte bindings."""
from pathlib import Path
import hashlib
import re
import stat
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


if __name__=='__main__':unittest.main()
