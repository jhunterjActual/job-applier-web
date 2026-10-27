"""Pure planner contract checks; no providers or filesystem mutations."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "onedrive_retention" / "capture_plan.py"
SPEC = importlib.util.spec_from_file_location("onedrive_capture_plan", SOURCE)
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
LABEL = "20261026-155056F"


def generation():
    files = {path: 1 for path in capture.METADATA}
    files["backup/careertrellis/" + LABEL + "/backup.manifest"] = 10
    return {"schema": "careertrellis.pgbackrest-file-set/v1", "backup_label": LABEL,
        "started_at": "2026-10-26T15:59:28Z", "files": files,
        "metadata_sha256": {path: "a" * 64 for path in capture.METADATA}}


def plan(value=None, **overrides):
    value = generation() if value is None else value
    args = dict(generation=value,
        generation_sha256=hashlib.sha256(capture.canonical(value)).hexdigest(),
        transaction="b" * 32, archive_sha256="c" * 64, archive_size=10240)
    args.update(overrides)
    return capture.plan_capture(**args)


class CapturePlanTests(unittest.TestCase):
    def test_exact_allowlist_and_new_namespace(self):
        result = plan()
        self.assertEqual(result["files_from_text"].splitlines(), sorted(generation()["files"]))
        self.assertEqual(result["destination"], capture.DESTINATION + "/" + "c"*64 + "/repository.tar")
        self.assertFalse(result["execution_authorized"])
        self.assertIn("--onedrive-hard-delete=false", result["commands"]["upload"])
        self.assertNotIn("delete", str(result["commands"]["download"]))
        for command in result["commands"].values():
            self.assertEqual(command[0], capture.BINARY)
            self.assertEqual(command[command.index("--config")+1], capture.CONFIG)

    def test_paths_cannot_escape_or_inject_file_list(self):
        for path in ("../backup", "backup/careertrellis/../x", "backup/careertrellis//x",
                     "backup/careertrellis/x\ny", "/backup/careertrellis/x",
                     "backup/other/x", "backup/careertrellis/x\\y"):
            value = generation(); value["files"][path] = 1
            with self.subTest(path=path), self.assertRaises(capture.CapturePlanError):
                plan(value)

    def test_boolean_sizes_and_malformed_identity_refused(self):
        for field, value in (("archive_size", True), ("archive_size", 0),
                             ("transaction", "../" + "a"*32),
                             ("archive_sha256", "C"*64), ("generation_sha256", "f"*64)):
            with self.subTest(field=field), self.assertRaises(capture.CapturePlanError):
                plan(**{field: value})
        value = generation(); value["files"][next(iter(value["files"]))] = True
        with self.assertRaises(capture.CapturePlanError): plan(value)

    def test_label_ancestor_and_metadata_required(self):
        cases = []
        value = generation(); value["backup_label"] = "../20261026-155056F"; cases.append(value)
        value = generation(); value["backup_label"] += "_20261027-031500D"; cases.append(value)
        value = generation(); value["metadata_sha256"].pop(next(iter(capture.METADATA))); cases.append(value)
        value = generation(); value["started_at"] = "2026-02-30T00:00:00Z"; cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(capture.CapturePlanError): plan(value)

    def test_required_executor_boundaries_remain_explicit(self):
        required = plan()["requirements"]
        self.assertEqual(required["canonical_lock_held"], capture.LOCK)
        self.assertTrue(required["metadata_snapshot_before_after"])
        self.assertTrue(required["native_local_repository_restore_before_retirement"])
        self.assertTrue(required["private_subprocess_outputs"])


if __name__ == "__main__":
    unittest.main()
