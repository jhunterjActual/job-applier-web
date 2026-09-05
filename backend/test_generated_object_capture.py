"""Synthetic coverage for the durable, root-sealed generated-object handoff."""

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest import mock

import generated_object_recovery as recovery


RELEASE = "a" * 40
BACKUP_LABEL = "20260905-160000F"
SNAPSHOT_ID = f"{RELEASE}-{BACKUP_LABEL}"
AUTHORITY = "postgres-r2-authoritative"
OBJECT_NAME = "1" * 32
ROOT_LINUX = os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0


class CapturePrivilegeTests(unittest.TestCase):
    def test_nonroot_capture_and_verification_are_rejected_before_io(self):
        with mock.patch.object(os, "geteuid", return_value=1234, create=True):
            with mock.patch.object(recovery, "_capture_parent") as parent:
                with self.assertRaises(recovery.GeneratedObjectRecoveryError):
                    recovery.seal_capture(
                        Path("/synthetic"), RELEASE, BACKUP_LABEL, "0" * 64,
                        AUTHORITY, 0, int(time.time()),
                    )
                with self.assertRaises(recovery.GeneratedObjectRecoveryError):
                    recovery.verify_capture(RELEASE, BACKUP_LABEL, AUTHORITY, 0)
                parent.assert_not_called()


@unittest.skipUnless(ROOT_LINUX, "root Linux filesystem semantics are required")
class GeneratedObjectCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.work = self.parent / "work"
        self.objects = self.work / "objects"
        (self.objects / "v1").mkdir(parents=True)
        self.source = self.objects / "v1" / OBJECT_NAME
        self.payload = b"synthetic-encrypted-payload"
        self.source.write_bytes(self.payload)
        (self.work / "marker").mkdir()
        self.manifest_path = self.work / "marker" / "snapshot-manifest.json"
        self.manifest = recovery.create_manifest(
            self.objects, RELEASE, BACKUP_LABEL, SNAPSHOT_ID, self.manifest_path,
        )
        self.captures = self.parent / "captures"
        self.captures.mkdir(mode=0o750)
        self.captures.chmod(0o750)
        self.group = os.getgid()
        self.target = self.captures / SNAPSHOT_ID
        self.started = int(time.time()) - 2
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(recovery, "CAPTURE_ROOT", self.captures).start()
        # /tmp is deliberately world-writable; retain the real capture-root check.
        mock.patch.object(
            recovery, "_capture_parent",
            side_effect=lambda group: recovery._protected_directory(self.captures, group),
        ).start()

    def seal(self, **overrides):
        arguments = dict(
            work=self.work, release=RELEASE, backup_label=BACKUP_LABEL,
            digest=self.manifest.digest(), authority=AUTHORITY, group=self.group,
            started_epoch=self.started,
        )
        arguments.update(overrides)
        return recovery.seal_capture(**arguments)

    def verify(self, **overrides):
        arguments = dict(release=RELEASE, backup_label=BACKUP_LABEL,
                         authority=AUTHORITY, group=self.group)
        arguments.update(overrides)
        return recovery.verify_capture(**arguments)

    def assert_rejected(self, function, *args, **kwargs):
        # CLI normalizes filesystem/JSON failures to its fixed public error.
        with self.assertRaises((recovery.GeneratedObjectRecoveryError, OSError, ValueError)):
            function(*args, **kwargs)

    def mutate_seal(self, change):
        path = self.target / "seal.json"
        document = json.loads(path.read_bytes())
        change(document)
        path.write_text(json.dumps(document, sort_keys=True), encoding="ascii")

    def test_roundtrip_binds_identity_authority_bytes_and_private_modes(self):
        sealed = self.seal()
        self.assertEqual(self.verify(), sealed)
        self.assertEqual(sealed["release"], RELEASE)
        self.assertEqual(sealed["backup_label"], BACKUP_LABEL)
        self.assertEqual(sealed["snapshot_id"], SNAPSHOT_ID)
        self.assertEqual(sealed["authority"], AUTHORITY)
        self.assertEqual(sealed["capture_started_epoch"], self.started)
        self.assertEqual(sealed["manifest_sha256"], self.manifest.digest())
        self.assertEqual(sealed["object_count"], 1)
        self.assertEqual(sealed["ciphertext_bytes"], len(self.payload))
        for path in (self.target, self.target / "objects", self.target / "objects/v1", self.target / "marker"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o750)
            self.assertEqual(path.stat().st_uid, 0)
        for path in (self.target / "objects/v1" / OBJECT_NAME,
                     self.target / "marker/snapshot-manifest.json"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            self.assertEqual(path.stat().st_uid, 0)
        self.assertEqual(stat.S_IMODE((self.target / "seal.json").stat().st_mode), 0o600)

    def test_original_open_descriptor_cannot_mutate_sealed_copy(self):
        os.chown(self.source, 12345, self.group)
        with self.source.open("r+b") as original:
            self.seal()
            original.seek(0)
            original.write(b"x" * len(self.payload))
            original.flush()
            os.fsync(original.fileno())
        copied = self.target / "objects/v1" / OBJECT_NAME
        self.assertNotEqual(copied.stat().st_ino, self.source.stat().st_ino)
        self.assertEqual(copied.read_bytes(), self.payload)
        self.verify()

    def test_sealed_extra_missing_corrupt_and_noncanonical_content_rejected(self):
        self.seal()
        copied = self.target / "objects/v1" / OBJECT_NAME
        copied.write_bytes(b"x" * len(self.payload))
        self.assert_rejected(self.verify)
        copied.write_bytes(self.payload)
        copied.unlink()
        self.assert_rejected(self.verify)
        copied.write_bytes(self.payload)
        copied.chmod(0o640)
        for extra in (self.target / "extra", self.target / "marker/extra",
                      self.target / "objects/v1" / ("2" * 32),
                      self.target / "objects/v1" / "not-a-canonical-object"):
            with self.subTest(extra=extra.name):
                extra.write_bytes(b"extra")
                extra.chmod(0o640)
                self.assert_rejected(self.verify)
                extra.unlink()
        self.verify()

    def test_manifest_must_remain_canonical_even_with_matching_digest(self):
        self.seal()
        path = self.target / "marker/snapshot-manifest.json"
        path.write_text(json.dumps(json.loads(path.read_bytes()), indent=2), encoding="ascii")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.mutate_seal(lambda seal: seal.update(manifest_sha256=digest))
        self.assert_rejected(self.verify)

    def test_capture_rejects_stale_future_and_invalid_authority_before_creation(self):
        for started in (int(time.time()) - recovery.MAXIMUM_CAPTURE_AGE_SECONDS - 10,
                        int(time.time()) + 100):
            with self.subTest(started=started):
                self.assert_rejected(self.seal, started_epoch=started)
                self.assertFalse(self.target.exists())
        self.assert_rejected(self.seal, authority="not-an-authority")
        self.assertFalse(self.target.exists())

    def test_verification_rejects_stale_future_and_boolean_timestamps(self):
        self.seal()
        original = (self.target / "seal.json").read_bytes()
        mutations = (
            {"capture_started_epoch": int(time.time()) - recovery.MAXIMUM_CAPTURE_AGE_SECONDS - 10},
            {"capture_started_epoch": int(time.time()) + 100},
            {"captured_epoch": int(time.time()) + 100},
            {"captured_epoch": self.started - 1},
            {"capture_started_epoch": True},
            {"captured_epoch": True},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.mutate_seal(lambda seal: seal.update(mutation))
                self.assert_rejected(self.verify)
                (self.target / "seal.json").write_bytes(original)

    def test_identity_authority_and_summary_mismatch_are_rejected(self):
        self.seal()
        self.assert_rejected(self.verify, authority="sqlite-authoritative")
        self.assert_rejected(self.verify, release="b" * 40)
        self.assert_rejected(self.verify, backup_label="20260905-170000F")
        original = (self.target / "seal.json").read_bytes()
        for mutation in ({"release": "b" * 40}, {"snapshot_id": "other"},
                         {"backup_label": "20260905-170000F"}, {"object_count": 5},
                         {"manifest_verified": False}, {"result": "failed"},
                         {"unexpected": True}):
            with self.subTest(mutation=mutation):
                self.mutate_seal(lambda seal: seal.update(mutation))
                self.assert_rejected(self.verify)
                (self.target / "seal.json").write_bytes(original)

    def test_wrong_source_manifest_identity_or_digest_is_rejected(self):
        self.assert_rejected(self.seal, digest="0" * 64)
        self.assert_rejected(self.seal, release="b" * 40)
        self.assert_rejected(self.seal, backup_label="20260905-170000F")
        self.assertFalse(self.target.exists())

    def test_owner_group_and_mode_tampering_rejected(self):
        self.seal()
        candidates = (self.captures, self.target, self.target / "objects",
                      self.target / "objects/v1" / OBJECT_NAME,
                      self.target / "marker/snapshot-manifest.json", self.target / "seal.json")
        for path in candidates:
            metadata = path.stat()
            for operation in ("owner", "group", "mode"):
                with self.subTest(path=path.name, operation=operation):
                    if operation == "owner":
                        os.chown(path, 12345, metadata.st_gid)
                    elif operation == "group":
                        os.chown(path, metadata.st_uid, metadata.st_gid + 1)
                    else:
                        path.chmod(stat.S_IMODE(metadata.st_mode) | 0o002)
                    self.assert_rejected(self.verify)
                    os.chown(path, metadata.st_uid, metadata.st_gid)
                    path.chmod(stat.S_IMODE(metadata.st_mode))

    def test_sealed_symlink_and_hardlink_are_rejected(self):
        self.seal()
        copied = self.target / "objects/v1" / OBJECT_NAME
        displaced = self.parent / "displaced"
        copied.rename(displaced)
        copied.symlink_to(displaced)
        self.assert_rejected(self.verify)
        copied.unlink()
        os.link(displaced, copied)
        self.assert_rejected(self.verify)
        copied.unlink()
        displaced.rename(copied)
        marker = self.target / "marker"
        displaced_marker = self.parent / "displaced-marker"
        marker.rename(displaced_marker)
        marker.symlink_to(displaced_marker, target_is_directory=True)
        self.assert_rejected(self.verify)

    def test_source_symlink_and_hardlink_are_rejected(self):
        displaced = self.parent / "displaced-source"
        self.source.rename(displaced)
        self.source.symlink_to(displaced)
        self.assert_rejected(self.seal)
        self.assertFalse((self.target / "seal.json").exists())
        self.source.unlink()
        os.link(displaced, self.source)
        self.assert_rejected(recovery._anchored_read, self.work,
                             ("objects", "v1", OBJECT_NAME), len(self.payload))

    def test_mutable_source_ancestor_symlink_is_rejected(self):
        actual = self.parent / "real-work"
        self.work.rename(actual)
        self.work.symlink_to(actual, target_is_directory=True)
        self.assert_rejected(self.seal)
        self.assertFalse(self.target.exists())

    def test_missing_or_corrupt_source_retains_incomplete_capture_without_seal(self):
        self.source.write_bytes(b"x" * len(self.payload))
        self.assert_rejected(self.seal)
        self.assertTrue(self.target.is_dir())
        self.assertFalse((self.target / "seal.json").exists())
        self.assert_rejected(self.verify)
        self.source.write_bytes(self.payload)
        self.assert_rejected(self.seal)
        self.assertFalse((self.target / "seal.json").exists())

    def test_fsync_failure_retains_incomplete_capture_and_prevents_overwrite(self):
        with mock.patch.object(recovery.os, "fsync", side_effect=OSError("synthetic failure")):
            self.assert_rejected(self.seal)
        self.assertTrue(self.target.is_dir())
        self.assertFalse((self.target / "seal.json").exists())
        self.assert_rejected(self.verify)
        self.assert_rejected(self.seal)

    def test_completed_capture_cannot_be_overwritten(self):
        original = self.seal()
        seal_bytes = (self.target / "seal.json").read_bytes()
        self.assert_rejected(self.seal)
        self.assertEqual((self.target / "seal.json").read_bytes(), seal_bytes)
        self.assertEqual(self.verify(), original)

    def test_files_and_directories_are_synced_before_success(self):
        with mock.patch.object(recovery.os, "fsync", wraps=os.fsync) as fsync:
            self.seal()
        # Payload, manifest, seal; v1, objects, marker, target, capture parent.
        self.assertGreaterEqual(fsync.call_count, 8)


if __name__ == "__main__":
    unittest.main()
