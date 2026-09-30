"""Synthetic atomic launcher publication; never touches installed credentials."""
from pathlib import Path
import os
import runpy
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'),
                     'Native POSIX no-follow and executable mode semantics required')
class BridgeInstallerTests(unittest.TestCase):
    def setUp(self):
        self.functions = runpy.run_path(str(Path(__file__).resolve().parents[1] /
            'deploy/scripts/install-owner-recovery-bridge.py'))
        self.publish = self.functions['publish_launcher']
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'launcher'
        self.path.write_bytes(b'old')
        self.publish.__globals__['WRAPPER'] = self.path

    def test_exact_upgrade_is_atomic_and_preserves_mode(self):
        self.publish(b'new', b'old')
        self.assertEqual(b'new', self.path.read_bytes())
        self.assertEqual(0o755, self.path.stat().st_mode & 0o777)
        self.assertFalse(self.path.with_name('launcher.upgrade').exists())

    def test_restrictive_service_umask_preserves_exact_publication_modes(self):
        previous_umask = os.umask(0o077)
        try:
            self.publish(b'upgraded', b'old')
            self.assertEqual(b'upgraded', self.path.read_bytes())
            self.assertEqual(0o755, self.path.stat().st_mode & 0o777)
            self.assertFalse(self.path.with_name('launcher.upgrade').exists())
            fresh = Path(self.temp.name) / 'fresh-launcher'
            self.publish.__globals__['WRAPPER'] = fresh
            self.publish(b'installed', None)
            self.assertEqual(b'installed', fresh.read_bytes())
            self.assertEqual(0o755, fresh.stat().st_mode & 0o777)
            receipt = Path(self.temp.name) / 'receipt.json'
            self.functions['write'](receipt, b'{}')
            self.assertEqual(0o600, receipt.stat().st_mode & 0o777)
        finally:
            os.umask(previous_umask)

    def test_changed_predecessor_rejects_without_write(self):
        with self.assertRaises(ValueError): self.publish(b'new', b'wrong')
        self.assertEqual(b'old', self.path.read_bytes())
        self.assertFalse(self.path.with_name('launcher.upgrade').exists())

    def test_failed_replace_retains_working_launcher_and_pending_evidence(self):
        with patch('os.replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): self.publish(b'new', b'old')
        self.assertEqual(b'old', self.path.read_bytes())
        self.assertEqual(b'new', self.path.with_name('launcher.upgrade').read_bytes())

    def test_existing_pending_file_prevents_blind_retry(self):
        pending = self.path.with_name('launcher.upgrade'); pending.write_bytes(b'prior')
        with self.assertRaises(FileExistsError): self.publish(b'new', b'old')
        self.assertEqual(b'old', self.path.read_bytes())
        self.assertEqual(b'prior', pending.read_bytes())


if __name__ == '__main__': unittest.main()
