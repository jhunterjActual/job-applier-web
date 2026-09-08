"""Synthetic-only native checks: no live paths, services or providers."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backup_repository_lock as repair


class SourceContractTests(unittest.TestCase):
    def test_rule_and_installer_include_nontruncating_boot_configuration(self):
        source = Path(__file__).resolve().parents[1]
        rule = (source / 'deploy/tmpfiles' / repair.RULE_NAME).read_bytes()
        lines = [line for line in rule.splitlines() if line and not line.startswith(b'#')]
        self.assertEqual([repair.RULE.rstrip(b'\n')], lines)
        installer = (source / 'deploy/scripts/install-backup-health-monitor.sh').read_text()
        self.assertIn('readonly source_tmpfiles=deploy/tmpfiles/careertrellis-pgbackrest.conf', installer)
        self.assertIn('"${repository_source}/${source_tmpfiles}" "${verification_root}${tmpfiles_target}"', installer)
        self.assertIn('"${verification_root}${tmpfiles_target}" "${tmpfiles_target}"', installer)
        self.assertIn('"${unit_target_paths[@]}" "${tmpfiles_target}"', installer)


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Native root fixture required')
class NativeLockTests(unittest.TestCase):
    def setUp(self):
        # Root-owned root-level fixture satisfies the real protected-parent rule.
        self.temporary = tempfile.TemporaryDirectory(prefix='ct-lock-test-', dir='/')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'run/lock').mkdir(parents=True)
        (self.root / 'etc/tmpfiles.d').mkdir(parents=True)
        self.rule = self.root / 'source.conf'
        self.rule.write_bytes(repair.RULE)
        self.rule.chmod(0o644)
        self.lock = self.root / 'run/lock' / repair.LOCK_NAME
        self.target = self.root / 'etc/tmpfiles.d' / repair.RULE_NAME

    def install(self):
        repair.install(self.rule, root=self.root)

    def tmpfiles(self):
        command = shutil.which('systemd-tmpfiles')
        if command is None:
            self.skipTest('Native systemd-tmpfiles required')
        return subprocess.run([command, '--create', '--boot', '--root=' + str(self.root),
                               str(self.target)], capture_output=True)

    def test_install_idempotent_preserves_inode_contents_and_metadata(self):
        self.install()
        self.lock.write_bytes(b'synthetic-sentinel')
        before = self.lock.stat()
        self.install()
        self.assertEqual(before.st_ino, self.lock.stat().st_ino)
        self.assertEqual(b'synthetic-sentinel', self.lock.read_bytes())
        self.assertEqual(0o600, stat.S_IMODE(self.lock.stat().st_mode))
        self.assertEqual(repair.RULE, self.target.read_bytes())

    def test_busy_lock_refuses_without_config_write_or_inode_replacement(self):
        import fcntl
        self.lock.touch(mode=0o600)
        with self.lock.open('r+b') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = self.lock.stat().st_ino
            with self.assertRaises(BlockingIOError):
                self.install()
            self.assertFalse(self.target.exists())
            self.assertEqual(before, self.lock.stat().st_ino)

    def test_native_boot_rule_and_reapplication_preserve_held_lock(self):
        import fcntl
        self.install()
        # Simulate volatile /run loss in this synthetic fixture only.
        self.lock.unlink()
        result = self.tmpfiles()
        self.assertEqual(0, result.returncode, result.stderr.decode())
        repair.metadata(self.lock.stat(), 0o600)
        self.lock.write_bytes(b'held-sentinel')
        with self.lock.open('r+b') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = self.lock.stat().st_ino
            result = self.tmpfiles()
            self.assertEqual(0, result.returncode, result.stderr.decode())
            self.assertEqual(before, self.lock.stat().st_ino)
            self.assertEqual(b'held-sentinel', self.lock.read_bytes())
            with self.assertRaises(BlockingIOError):
                self.install()

    def test_unsafe_existing_lock_forms_refuse(self):
        for kind in ('symlink', 'dangling', 'hardlink', 'fifo', 'directory', 'mode', 'owner'):
            with self.subTest(kind=kind):
                other = self.root / 'other'
                other.write_bytes(b'untouched')
                other.chmod(0o600)
                if kind == 'symlink': self.lock.symlink_to(other)
                elif kind == 'dangling': self.lock.symlink_to(self.root / 'absent')
                elif kind == 'hardlink': os.link(other, self.lock)
                elif kind == 'fifo': os.mkfifo(self.lock, 0o600)
                elif kind == 'directory': self.lock.mkdir()
                else:
                    self.lock.touch(mode=0o644 if kind == 'mode' else 0o600)
                    if kind == 'owner': os.chown(self.lock, 65534, 65534)
                with self.assertRaises((OSError, ValueError)):
                    self.install()
                self.assertFalse(self.target.exists())
                self.assertEqual(b'untouched', other.read_bytes())
                if kind == 'directory': self.lock.rmdir()
                else: self.lock.unlink()
                other.unlink()

    def test_conflict_and_publish_failure_preserve_existing_state(self):
        self.install()
        before = self.lock.stat().st_ino
        self.target.write_bytes(b'# conflicting operator config\n')
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(b'# conflicting operator config\n', self.target.read_bytes())
        self.target.unlink()
        with patch.object(repair.os, 'link', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): self.install()
        self.assertEqual(before, self.lock.stat().st_ino)
        self.assertEqual([], list(self.target.parent.iterdir()))

    def test_unsafe_parent_and_substitution_refuse(self):
        self.target.parent.chmod(0o777)
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(self.lock.exists())
        self.target.parent.chmod(0o755)
        with patch.object(repair, 'same_inode', side_effect=ValueError('synthetic substitution')):
            with self.assertRaises(ValueError): self.install()
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
