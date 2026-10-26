"""Offline bundle format tests; filesystem containment runs only on native POSIX."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "onedrive_retention" / "bundle.py"
SPEC = importlib.util.spec_from_file_location("onedrive_repository_bundle", SOURCE)
bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle)
LABEL = "20261026-155056F"
PAYLOADS = {path: ("synthetic:" + path).encode() for path in bundle.REQUIRED_METADATA}
PAYLOADS["backup/careertrellis/" + LABEL + "/backup.manifest"] = b"synthetic manifest"
PAYLOADS["backup/careertrellis/" + LABEL + "/pg_data/base/1/2"] = b"ciphertext fixture"


def generation():
    return {"schema": bundle.GENERATION_SCHEMA, "backup_label": LABEL,
        "started_at": "2026-10-26T15:59:28Z",
        "files": {name: len(raw) for name, raw in PAYLOADS.items()},
        "metadata_sha256": {name: hashlib.sha256(raw).hexdigest()
                            for name, raw in PAYLOADS.items() if name in bundle.METADATA}}


def manifest():
    return {"schema": bundle.SCHEMA, "backup_label": LABEL,
        "generation_sha256": hashlib.sha256(bundle.canonical(generation())).hexdigest(),
        "total_bytes": sum(len(raw) for raw in PAYLOADS.values()),
        "files": {name: {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                  for name, raw in PAYLOADS.items()}}


class ManifestTests(unittest.TestCase):
    def test_manifest_directory_and_depth_budgets_before_extraction(self):
        value = manifest()
        with mock.patch.object(bundle, "MAX_DIRECTORIES", 1):
            with self.assertRaises(bundle.BundleError):
                bundle.validate_manifest(value)
        name = "backup/careertrellis/" + "deep/" * bundle.MAX_DEPTH + "payload"
        value["files"][name] = {"size": 0, "sha256": hashlib.sha256(b"").hexdigest()}
        with self.assertRaises(bundle.BundleError):
            bundle.validate_manifest(value)

    def test_manifest_exact_schema_and_limits(self):
        self.assertEqual(bundle.validate_manifest(manifest()), manifest())
        for change in (lambda m: m.update(total_bytes=True),
                       lambda m: m.update(extra="unknown"),
                       lambda m: m.update(backup_label="../escape"),
                       lambda m: m["files"].pop(next(iter(bundle.REQUIRED_METADATA))),
                       lambda m: m["files"].update({"backup/careertrellis/../escape":
                            {"size": 0, "sha256": "0" * 64}})):
            value = manifest()
            change(value)
            with self.assertRaises(bundle.BundleError):
                bundle.validate_manifest(value)

    def test_duplicate_json_and_noncanonical_encoding_refused(self):
        raw = bundle.canonical(manifest())
        self.assertEqual(bundle._decode(raw), manifest())
        with self.assertRaises(bundle.BundleError):
            bundle._decode(raw.replace(b'"schema":', b'"schema":"duplicate","schema":'))
        with self.assertRaises(bundle.BundleError):
            bundle._decode(json.dumps(manifest()).encode())

    def test_differential_requires_full_ancestor(self):
        value = manifest()
        value["backup_label"] = LABEL + "_20261027-031500D"
        with self.assertRaises(bundle.BundleError):
            bundle.validate_manifest(value)


POSIX_SAFE = (hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd
              and os.mkdir in os.supports_dir_fd and os.scandir in os.supports_fd)


@unittest.skipUnless(POSIX_SAFE, "requires native POSIX descriptor-relative no-follow filesystem APIs")
class BundleFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "source"
        self.repository.mkdir()
        for name, raw in PAYLOADS.items():
            leaf = self.repository / name
            leaf.parent.mkdir(parents=True, exist_ok=True)
            leaf.write_bytes(raw)

    def build(self, name="bundle.tar"):
        target = self.root / name
        return target, bundle.build_bundle(self.repository, target, generation())

    def malicious(self, members):
        target = self.root / "malicious.tar"
        with tarfile.open(target, "w", format=tarfile.USTAR_FORMAT) as archive:
            raw = bundle.canonical(manifest())
            archive.addfile(bundle._member("manifest.json", len(raw)), io.BytesIO(raw))
            for item, payload in members:
                archive.addfile(item, io.BytesIO(payload) if payload is not None else None)
        return target, hashlib.sha256(target.read_bytes()).hexdigest()

    def test_deterministic_roundtrip_and_exclusive_outputs(self):
        first, receipt = self.build()
        second, other = self.build("other.tar")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(receipt, other)
        destination = self.root / "restored"
        self.assertEqual(bundle.extract_bundle(first, destination, receipt["sha256"]), manifest())
        for name, raw in PAYLOADS.items():
            self.assertEqual((destination / name).read_bytes(), raw)
        with self.assertRaises(bundle.BundleError):
            bundle.build_bundle(self.repository, first, generation())
        with self.assertRaises(bundle.BundleError):
            bundle.extract_bundle(first, destination, receipt["sha256"])

    def test_wrong_download_digest_refuses_before_destination_creation(self):
        archive, _ = self.build()
        destination = self.root / "restored"
        with self.assertRaises(bundle.BundleError):
            bundle.extract_bundle(archive, destination, "0" * 64)
        self.assertFalse(destination.exists())

    def test_source_file_symlink_hardlink_and_ancestor_symlink_refused(self):
        leaf = self.repository / next(iter(PAYLOADS))
        original = leaf.read_bytes()
        outside = self.root / "outside"
        outside.write_bytes(original)
        leaf.unlink()
        leaf.symlink_to(outside)
        with self.assertRaises(bundle.BundleError):
            self.build()
        leaf.unlink()
        os.link(outside, leaf)
        with self.assertRaises(bundle.BundleError):
            self.build()
        alias = self.root / "alias"
        alias.symlink_to(self.repository, target_is_directory=True)
        with self.assertRaises(bundle.BundleError):
            bundle.build_bundle(alias, self.root / "alias.tar", generation())

    def test_exact_source_and_metadata_binding(self):
        leaf = self.repository / "backup/careertrellis/unexpected"
        leaf.write_bytes(b"extra")
        with self.assertRaises(bundle.BundleError):
            self.build()
        leaf.unlink()
        value = generation()
        value["metadata_sha256"][next(iter(bundle.REQUIRED_METADATA))] = "0" * 64
        with self.assertRaises(bundle.BundleError):
            bundle.build_bundle(self.repository, self.root / "bad.tar", value)

    def test_unknown_member_links_traversal_duplicates_and_oversize_refuse(self):
        first_name = sorted(PAYLOADS)[0]
        variants = []
        link = bundle._member("repository/" + first_name, 0)
        link.type, link.linkname = tarfile.SYMTYPE, "../../outside"
        hardlink = copy.copy(link)
        hardlink.type = tarfile.LNKTYPE
        variants += [[(link, None)], [(hardlink, None)],
            [(bundle._member("../outside", 1), b"x")],
            [(bundle._member("repository/" + first_name, 0), b"")],
            [(bundle._member("manifest.json", 0), b"")]]
        for members in variants:
            archive, digest = self.malicious(members)
            destination = self.root / "restored"
            with self.assertRaises(bundle.BundleError):
                bundle.extract_bundle(archive, destination, digest)
            # A refused extraction deliberately retains partial output. Each
            # test case owns a new destination; no recovery cleanup is implied.
            self.root = self.root / "next"
            self.root.mkdir()

    def test_payload_tamper_with_recomputed_archive_digest_refused(self):
        members = []
        for name, raw in sorted(PAYLOADS.items()):
            changed = b"x" * len(raw)
            members.append((bundle._member("repository/" + name, len(changed)), changed))
        archive, digest = self.malicious(members)
        with self.assertRaises(bundle.BundleError):
            bundle.extract_bundle(archive, self.root / "restored", digest)

    def test_extra_duplicate_after_complete_payload_is_refused(self):
        members = [(bundle._member("repository/" + name, len(raw)), raw)
                   for name, raw in sorted(PAYLOADS.items())]
        members.append(members[0])
        archive, digest = self.malicious(members)
        with self.assertRaises(bundle.BundleError):
            bundle.extract_bundle(archive, self.root / "restored", digest)

    def test_pax_extended_headers_refused(self):
        target = self.root / "extended.tar"
        with tarfile.open(target, "w", format=tarfile.PAX_FORMAT,
                          pax_headers={"comment": "untrusted extension"}) as archive:
            raw = bundle.canonical(manifest())
            archive.addfile(bundle._member("manifest.json", len(raw)), io.BytesIO(raw))
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        with self.assertRaises(bundle.BundleError):
            bundle.extract_bundle(target, self.root / "restored", digest)
        self.assertFalse((self.root / "restored").exists())

    def test_truncated_archive_with_new_digest_refused(self):
        archive, _ = self.build()
        raw = archive.read_bytes()[:700]
        target = self.root / "truncated.tar"
        target.write_bytes(raw)
        with self.assertRaises(bundle.BundleError):
            bundle.extract_bundle(target, self.root / "restored", hashlib.sha256(raw).hexdigest())

    def test_appended_garbage_and_concatenated_archive_refused(self):
        archive, _ = self.build()
        original = archive.read_bytes()
        for index, extra in enumerate((b"nonzero garbage", original, b"\0" * 10240)):
            target = self.root / ("appended-" + str(index) + ".tar")
            raw = original + extra
            target.write_bytes(raw)
            destination = self.root / ("restored-" + str(index))
            with self.assertRaises(bundle.BundleError):
                bundle.extract_bundle(target, destination, hashlib.sha256(raw).hexdigest())
            self.assertFalse(destination.exists())

    def test_nonzero_end_padding_refused(self):
        archive, _ = self.build()
        raw = bytearray(archive.read_bytes())
        raw[-1] = 1
        target = self.root / "nonzero-padding.tar"
        target.write_bytes(raw)
        with self.assertRaises(bundle.BundleError):
            bundle.extract_bundle(target, self.root / "restored", hashlib.sha256(raw).hexdigest())

    def test_empty_source_directories_count_against_budget(self):
        count = sum(path.is_dir() for path in self.repository.rglob("*"))
        (self.repository / "archive/careertrellis/empty/child").mkdir(parents=True)
        with mock.patch.object(bundle, "MAX_DIRECTORIES", count + 1):
            with self.assertRaises(bundle.BundleError):
                self.build()
        self.assertFalse((self.root / "bundle.tar").exists())

    def test_valid_deep_directory_under_budgets_roundtrips(self):
        name = "backup/careertrellis/" + LABEL + "/deep/one/two/three/four/five/payload"
        leaf = self.repository / name
        leaf.parent.mkdir(parents=True)
        leaf.write_bytes(b"synthetic deep fixture")
        value = generation()
        value["files"][name] = leaf.stat().st_size
        target = self.root / "deep.tar"
        receipt = bundle.build_bundle(self.repository, target, value)
        destination = self.root / "restored"
        bundle.extract_bundle(target, destination, receipt["sha256"])
        self.assertEqual((destination / name).read_bytes(), b"synthetic deep fixture")


if __name__ == "__main__":
    unittest.main()
