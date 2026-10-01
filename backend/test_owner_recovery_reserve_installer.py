"""Offline archive tests and root-POSIX temporary namespace installer fixtures.

No remote process, real credential, live database, or service interaction.
"""
from contextlib import ExitStack,nullcontext
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

SCRIPT=Path(__file__).resolve().parents[1]/'deploy/scripts/install-owner-recovery-reserve.py'
spec=importlib.util.spec_from_file_location('reserve_installer_fixture',SCRIPT)
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

def archive_fixture(*,change=None):
    commit='c'*40
    files={'backend/'+name+'.py':b'"""Synthetic public source."""\n' for name in installer.MODULES}
    manifest={'version':1,'operations':installer.OPS,
        'wrapper':{name:installer.digest(raw) for name,raw in files.items()},
        'library':{name:'a'*64 for name in ('backend/owner_recovery.py','backend/identity_plan_store.py','backend/identity_plan_packet.py')}}
    files['source-manifest.json']=installer.encoded(manifest)+b'\n'
    files[installer.WRAPPER.name]=installer.launcher(commit)
    if change:change(files)
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as bundle:
        for name,raw in files.items():
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o600
            bundle.addfile(info,io.BytesIO(raw))
    raw=stream.getvalue()
    pins={'commit':commit,'archive':installer.digest(raw),
        'manifest':installer.digest(files['source-manifest.json']),
        'launcher':installer.digest(files[installer.WRAPPER.name]),'runtime':'d'*64}
    return raw,pins

class ArchiveTests(unittest.TestCase):
    def test_runtime_digest_cannot_replace_source_provenance(self):
        with patch.object(installer,'runtime_manifest',side_effect=[{'a':'old'},{'a':'changed'}]),\
             patch.object(installer,'runtime_digest',return_value='d'*64) as fingerprint:
            with self.assertRaises(ValueError):installer.verify_runtime({'runtime':'d'*64})
            fingerprint.assert_not_called()
        with patch.object(installer,'runtime_manifest',return_value={'a':'same'}) as manifest,\
             patch.object(installer,'runtime_digest',return_value='d'*64):
            installer.verify_runtime({'runtime':'d'*64})
            self.assertEqual(manifest.call_args_list[0].args,(installer.RUNTIME_SOURCE,installer.RUNTIME))
    def test_only_fixed_sticky_lock_parent_allowed(self):
        for mode,accepted in ((0o1777,True),(0o755,True),(0o777,False),(0o775,False)):
            with patch.object(installer,'parents'),patch.object(Path,'lstat',return_value=SimpleNamespace(
                st_mode=stat.S_IFDIR|mode,st_uid=0)):
                if accepted:installer.lock_parents(Path('/run/lock/careertrellis-maintenance.lock'))
                else:
                    with self.assertRaises(ValueError):installer.lock_parents(Path('/run/lock/careertrellis-maintenance.lock'))
        with patch.object(installer,'parents',side_effect=ValueError('untrusted parent')):
            with self.assertRaises(ValueError):installer.lock_parents(Path('/tmp/lock'))
    def test_exact_seven_files_and_launcher_binding(self):
        raw,pins=archive_fixture();files,manifest=installer.unpack(raw,pins)
        self.assertEqual(set(files),installer.NAMES);self.assertEqual(len(files),7)
        self.assertEqual(manifest['operations'],installer.OPS)
    def test_extra_traversal_or_wrong_launcher_refused(self):
        for change in (lambda files:files.update({'../escape':b'no'}),
            lambda files:files.update({'backend/unapproved.py':b'no'}),
            lambda files:files.update({installer.WRAPPER.name:b'#!/bin/sh\nfalse\n'})):
            raw,pins=archive_fixture(change=change)
            with self.assertRaises(ValueError):installer.unpack(raw,pins)
    def test_archive_or_manifest_pin_mismatch_refused(self):
        raw,pins=archive_fixture()
        for key in ('archive','manifest','launcher'):
            altered=dict(pins);altered[key]='f'*64
            with self.assertRaises(ValueError):installer.unpack(raw,altered)
    def test_duplicate_or_link_members_refused(self):
        raw,pins=archive_fixture()
        for kind in ('duplicate','symlink','hardlink'):
            stream=io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as original,tarfile.open(fileobj=stream,mode='w') as bundle:
                for item in original:bundle.addfile(item,original.extractfile(item))
                item=tarfile.TarInfo('source-manifest.json');item.mode=0o600
                if kind!='duplicate':item.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE;item.linkname='/etc/passwd'
                bundle.addfile(item,io.BytesIO())
            altered=stream.getvalue();pins['archive']=installer.digest(altered)
            with self.assertRaises(ValueError):installer.unpack(altered,pins)
    def test_public_pin_shapes(self):
        for values in (['x']*5,['c'*40,*(['a'*64]*3)],['c'*40,*(['A'*64]*4)]):
            with self.assertRaises(ValueError):installer.pins(values)

@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'native root-POSIX fixture')
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.target=self.root/'reserve';self.state=self.root/'state';self.wrapper=self.root/'careertrellis-owner-recovery-reserve'
        for name,value in (('BASE',self.target),('STATE',self.state),('WRAPPER',self.wrapper)):
            self.stack.enter_context(patch.object(installer,name,value))
        self.marker=self.root/'attempt.json';self.incoming=self.root/'source.tar'
        self.stack.enter_context(patch.object(installer,'marker',return_value=self.marker))
        self.stack.enter_context(patch.object(installer,'upload',return_value=self.incoming))
        self.stack.enter_context(patch.object(installer,'uploaded',side_effect=lambda commit:installer.protected(self.incoming,mode=0o600)))
        # Fixture parent /tmp is intentionally writable; all installed inode
        # ownership/mode/no-follow checks still execute against actual files.
        self.stack.enter_context(patch.object(installer,'parents'))
        self.stack.enter_context(patch.object(installer,'locked',side_effect=lambda:nullcontext()))
        self.snapshot=self.stack.enter_context(patch.object(installer,'snapshot',return_value=('private unchanged',)))
        self.stack.enter_context(patch.object(installer,'verify_library'))
        self.stack.enter_context(patch.object(installer,'verify_runtime'))
        self.smoke=self.stack.enter_context(patch.object(installer,'smoke'))
        self.stack.enter_context(patch.object(installer.shutil,'disk_usage',return_value=SimpleNamespace(free=8*1024**3)))
        raw,self.pins=archive_fixture();self.incoming.write_bytes(raw);self.incoming.chmod(0o600)
        self.original=self.root/'original-credential';self.original.write_bytes(b'synthetic existing primary')
    def test_exclusive_install_modes_and_no_credentials_created(self):
        old=os.umask(0o077)
        try:result=installer.install(self.pins)
        finally:os.umask(old)
        self.assertEqual(result['outcome'],'installed_not_enrolled')
        self.assertEqual(stat.S_IMODE(self.wrapper.stat().st_mode),0o755)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode),0o700)
        self.assertEqual(list(self.state.iterdir()),[self.state/'convergence'])
        self.assertEqual(list((self.state/'convergence').iterdir()),[])
        self.assertEqual(self.original.read_bytes(),b'synthetic existing primary')
        self.smoke.assert_called_once_with(self.pins)
        self.assertEqual(installer.inspect(self.pins)['outcome'],'recorded_installation')
        with self.assertRaises(ValueError):installer.install(self.pins)
    def test_failed_probe_retains_marker_and_sources_without_launcher(self):
        self.smoke.side_effect=ValueError('probe refused')
        with self.assertRaises(ValueError):installer.install(self.pins)
        self.assertTrue(self.marker.exists());self.assertTrue((self.target/self.pins['commit']).is_dir())
        self.assertFalse(self.wrapper.exists());before=self.marker.read_bytes()
        self.assertEqual(installer.inspect(self.pins)['outcome'],'incomplete_retained')
        with self.assertRaises(ValueError):installer.install(self.pins)
        self.assertEqual(self.marker.read_bytes(),before)
    def test_preflight_or_existing_path_refused_before_attempt(self):
        self.snapshot.side_effect=ValueError('nonactive primary or changed owner')
        with self.assertRaises(ValueError):installer.install(self.pins)
        self.assertFalse(self.marker.exists())
        self.snapshot.side_effect=None;self.state.mkdir()
        with self.assertRaises(ValueError):installer.inspect(self.pins)
        with self.assertRaises(ValueError):installer.install(self.pins)
        self.assertFalse(self.marker.exists())
    def test_snapshot_drift_prevents_publication(self):
        self.snapshot.side_effect=[('before',),('drift',)]
        with self.assertRaises(ValueError):installer.install(self.pins)
        self.assertTrue(self.marker.exists());self.assertFalse(self.wrapper.exists())
    def test_symlink_or_hardlink_file_refused(self):
        link=self.root/'link';link.symlink_to(self.incoming)
        with self.assertRaises(OSError):installer.protected(link)
        link.unlink();os.link(self.incoming,link)
        with self.assertRaises(ValueError):installer.protected(link)

if __name__=='__main__':unittest.main()
