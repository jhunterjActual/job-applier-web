import tempfile
from pathlib import Path
import unittest
from deploy.onedrive_retention import package


class Tests(unittest.TestCase):
    def test_literal_inventory_does_not_execute_source(self):
        self.assertEqual(package.file_set(b"FILES=frozenset(('entry.py','guard.py'))\nraise RuntimeError()"),
                         {'entry.py','guard.py'})
        for source in (b"FILES=__import__('os').listdir('/')",b"FILES=('entry.py','entry.py')",
                       b"FILES=('../entry.py',)",b"FILES=('a.py',)\nFILES=('b.py',)"):
            with self.subTest(source=source),self.assertRaises((package.PacketRefused,ValueError)):
                package.file_set(source)

    def test_exact_manifest_and_exclusive_destination(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as temporary:
            root=Path(temporary); repository=root/'repository'
            for name in ('onedrive_retention','paired_maintenance'):
                folder=repository/'deploy'/name; folder.mkdir(parents=True)
                (folder/'entry.py').write_bytes(b"FILES=frozenset(('entry.py',))\n")
            receipt=package.write(repository,'a'*40,root/'packet')
            self.assertFalse(receipt['installation_performed'])
            self.assertFalse(receipt['native_acceptance'])
            raw=(root/'packet'/'onedrive_retention'/'manifest.json').read_bytes()
            self.assertEqual(package.digest(raw),receipt['manifests']['onedrive_retention'])
            with self.assertRaises(FileExistsError):package.write(repository,'a'*40,root/'packet')


if __name__=='__main__':unittest.main()
