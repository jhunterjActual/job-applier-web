"""Pure opened-file mount proof controls; no real /proc/host/file opens."""
from contextlib import ExitStack
import io
from pathlib import Path
import stat
import types
import unittest
from unittest.mock import patch
import run_checks as r

RO=b'101 1 8:1 / /cover ro,nosuid shared:7 - ext4 /dev/public rw,relatime\n'
RW=b'202 1 8:1 / /override rw,nosuid - ext4 /dev/public rw,relatime\n'

class Tests(unittest.TestCase):
    def proof(self,*,fdinfo=(b'mnt_id:\t101\n',b'mnt_id:\t101\n'),mountinfo=RO,flags=(1,1),mode=stat.S_IFREG|0o444):
        reads=list(fdinfo);stats=types.SimpleNamespace(st_mode=mode,st_dev=8,st_ino=123)
        def proc(path,*args,**kwargs):
            name=path.as_posix()
            if name=='/proc/self/fdinfo/57':return io.BytesIO(reads.pop(0))
            if name=='/proc/self/mountinfo':return io.BytesIO(mountinfo)
            raise AssertionError('unexpected synthetic proc path')
        with ExitStack() as stack:
            stack.enter_context(patch.object(r.os,'O_NOFOLLOW',getattr(r.os,'O_NOFOLLOW',0x20000),create=True))
            stack.enter_context(patch.object(r.os,'ST_RDONLY',1,create=True))
            stack.enter_context(patch.object(r.os,'fstatvfs',side_effect=[types.SimpleNamespace(f_flag=f) for f in flags],create=True))
            opened=stack.enter_context(patch.object(r.os,'open',return_value=57))
            stack.enter_context(patch.object(r.os,'fstat',return_value=stats))
            closed=stack.enter_context(patch.object(r.os,'close'))
            stack.enter_context(patch.object(Path,'open',proc))
            try:value=r.verify_opened_readonly(Path('/synthetic/deep/input.bin'))
            except r.ReadonlyProofRefused as error:value=error.code
            self.assertEqual(opened.call_count,1);self.assertEqual(opened.call_args.args[1],r.os.O_RDONLY|r.os.O_NOFOLLOW)
            closed.assert_called_once_with(57)
            return value
    def test_readonly_covering_mount_without_exact_path_row_passes(self):
        value=self.proof()
        self.assertEqual(value,{'mount_id':101,'fstatvfs_readonly':True,'mountinfo_readonly':True})
        self.assertNotIn(b'/synthetic/deep/input.bin',RO)
    def test_readwrite_cover_refused_even_if_dac_could_deny_write(self):
        self.assertEqual(self.proof(fdinfo=(b'mnt_id: 202\n',),mountinfo=RW,flags=(0,)),'RO_VFS_RW')
    def test_opened_override_id_wins_over_readonly_cover_row(self):
        self.assertEqual(self.proof(fdinfo=(b'mnt_id: 202\n',),mountinfo=RO+RW),'RO_MOUNT_RW')
    def test_readonly_bind_with_readwrite_superblock_is_valid(self):
        self.assertIsInstance(self.proof(mountinfo=RO),dict)
    def test_bad_missing_duplicate_or_unknown_mnt_id_refused(self):
        for data in (b'pos: 0\n',b'mnt_id: nope\n',b'mnt_id: 0\n',b'mnt_id: 101\nmnt_id: 101\n'):
            with self.subTest(data=data):self.assertEqual(self.proof(fdinfo=(data,)),'RO_FDINFO_ID')
        self.assertEqual(self.proof(fdinfo=(b'mnt_id: 999\n',)),'RO_MOUNT_ROW')
    def test_duplicate_or_malformed_mount_row_refused(self):
        self.assertEqual(self.proof(mountinfo=RO+RO),'RO_MOUNT_ROW')
        self.assertEqual(self.proof(mountinfo=b'101 1 8:1 / /cover ro\n'),'RO_MOUNT_FORMAT')
        self.assertEqual(self.proof(mountinfo=RO.replace(b'ro,nosuid',b'ro,rw,nosuid')),'RO_MOUNT_RW')
    def test_inconsistent_flags_or_changed_mount_refused(self):
        self.assertEqual(self.proof(flags=(0,)),'RO_VFS_RW')
        self.assertEqual(self.proof(flags=(1,0)),'RO_VFS_CHANGED')
        self.assertEqual(self.proof(fdinfo=(b'mnt_id: 101\n',b'mnt_id: 202\n')),'RO_MOUNT_CHANGED')
    def test_nonregular_and_oversized_proc_proofs_refused(self):
        self.assertEqual(self.proof(mode=stat.S_IFDIR|0o555),'RO_NOT_REGULAR')
        self.assertEqual(self.proof(fdinfo=(b'x'*4097,)),'RO_FDINFO_SIZE')
        self.assertEqual(self.proof(mountinfo=b'x'*(1024*1024+1)),'RO_MOUNTINFO_SIZE')
    def test_bind_requires_fd_proof_before_nonmutating_write_open(self):
        alias=Path('/synthetic/browsers');leaf=alias/'public-file'
        with patch.object(Path,'is_file',return_value=True),patch.object(Path,'is_symlink',return_value=False),patch.object(r,'verify_opened_readonly',side_effect=r.ReadonlyProofRefused('RO_VFS_RW')),patch.object(r.os,'open') as opened:
            with self.assertRaises(r.ReadonlyProofRefused):r.require_readonly_bind(alias,leaf)
            opened.assert_not_called()
        with patch.object(r.os,'O_NOFOLLOW',getattr(r.os,'O_NOFOLLOW',0x20000),create=True),patch.object(Path,'is_file',return_value=True),patch.object(Path,'is_symlink',return_value=False),patch.object(r,'verify_opened_readonly',return_value={'verified':True}) as bound,patch.object(r.os,'open',side_effect=PermissionError(13,'synthetic DAC')) as opened:
            r.require_readonly_bind(alias,leaf);bound.assert_called_once_with(leaf)
            self.assertEqual(opened.call_args.args[1],r.os.O_WRONLY|r.os.O_NOFOLLOW)
            self.assertEqual(opened.call_args.args[1]&r.os.O_TRUNC,0);self.assertEqual(opened.call_args.args[1]&r.os.O_CREAT,0)

if __name__=='__main__':unittest.main()
