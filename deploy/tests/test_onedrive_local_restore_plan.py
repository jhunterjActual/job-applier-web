import copy
import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "onedrive_retention" / "local_restore_plan.py"
SPEC = importlib.util.spec_from_file_location("onedrive_local_restore_plan", SOURCE)
restore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(restore)
TX = "a" * 32
ARCHIVE = "b" * 64
GENERATION = "c" * 64
LABEL = "20261026-155056F"


def plan(label=LABEL):
    return restore.plan(label, ARCHIVE, GENERATION, TX, 32 * 1024 ** 2)


class LocalRestorePlanTests(unittest.TestCase):
    def test_full_and_differential_labels_bind_ancestor(self):
        for label in (LABEL, LABEL + "_20261027-031500D", LABEL + "_20261027-031500I"):
            value = plan(label)
            self.assertEqual(value["backup_label"], label)
            self.assertEqual(value["full_ancestor_label"], LABEL)
            self.assertIn("--set=" + label, value["restore"])
            self.assertFalse(restore.validate_plan(value)["execution_authorized"])

    def test_bad_labels_digests_transactions_and_sizes_refuse(self):
        for label in ("../escape", "20261326-155056F", "20261026-256056F",
                      LABEL + "_20261025-031500D", LABEL + "_20261026-155056D",
                      LABEL + "_20261027-031500F", LABEL + "\n"):
            with self.assertRaises(restore.RestoreRefused):
                plan(label)
        for values in ((LABEL, "x" * 64, GENERATION, TX, 1),
                       (LABEL, ARCHIVE, "c" * 63, TX, 1),
                       (LABEL, ARCHIVE, GENERATION, "../" + TX, 1),
                       (LABEL, ARCHIVE, GENERATION, TX, True),
                       (LABEL, ARCHIVE, GENERATION, TX, 0),
                       (LABEL, ARCHIVE, GENERATION, TX, 1024 ** 4 + 1)):
            with self.assertRaises(restore.RestoreRefused):
                restore.plan(*values)

    def test_info_restore_and_recovery_wal_explicitly_use_local_repository(self):
        value = plan()
        repository = restore.ROOT + "/" + TX + "/downloaded-repository"
        for command in (value["info"], value["restore"], value["archive_get_template"]):
            self.assertIn("--repo=1", command)
            self.assertIn("--repo1-type=posix", command)
            self.assertIn("--repo1-path=" + repository, command)
            self.assertIn("--config=" + restore.CONFIG, command)
            self.assertEqual(command[:7], ["/usr/bin/setpriv", "--reuid=postgres",
                "--regid=postgres", "--init-groups", "--reset-env", "--", "/usr/bin/pgbackrest"])
        options = next(item for item in value["start"] if item.startswith("--options="))
        self.assertIn("restore_command='/usr/bin/pgbackrest ", options)
        self.assertIn("--repo1-type=posix --repo1-path=" + repository, options)
        for forbidden in ("r2:", "onedrive:", "s3://", "https://", "--repo1-host=", "password"):
            self.assertNotIn(forbidden, restore.canonical(value).decode())

    def test_fixed_paths_private_socket_and_isolation(self):
        value = plan()
        attempt = restore.ROOT + "/" + TX + "/restore"
        self.assertEqual(value["attempt_root"], attempt)
        self.assertEqual(value["socket_root"], attempt + "/socket")
        self.assertEqual(value["locks_in_order"], list(restore.LOCKS))
        self.assertEqual(value["minimum_free_bytes"], 64 * 1024 ** 2 + 5 * 1024 ** 3)
        options = next(item for item in value["start"] if item.startswith("--options="))
        for expected in ("-c listen_addresses= ", "-c port=55432 ",
                         "-c unix_socket_permissions=0700", "-c ssl=off",
                         "-c shared_preload_libraries= ", "-c session_preload_libraries= ",
                         "-c local_preload_libraries= ", "-c archive_mode=off",
                         "-c primary_conninfo= ", "-c primary_slot_name= ",
                         "-c config_file=" + attempt + "/data/postgresql.bundle.conf"):
            self.assertIn(expected, options)
        for expected in ("--type=immediate", "--target-action=promote", "--no-link-all",
                         "--reset-link-map", "--reset-tablespace-map", "--no-delta", "--force=n"):
            self.assertIn(expected, value["restore"])
        self.assertIn("--tablespace-map-all=" + attempt + "/tablespaces", value["restore"])
        self.assertEqual(value["hba"], "local all postgres peer\nlocal all all reject\n")
        self.assertIn("--host=" + attempt + "/socket", value["probe"])
        self.assertIn("--mode=fast", value["stop"])
        self.assertIn("--check", value["checksums"])
        self.assertIn("no_automatic_cleanup", value["limitations"])

    def test_tampering_cannot_switch_to_r2_or_live_cluster(self):
        base = plan()
        variants = []
        value = copy.deepcopy(base)
        value["restore"].remove("--repo1-type=posix")
        variants.append(value)
        value = copy.deepcopy(base)
        value["info"].remove("--repo1-path=" + value["repository_root"])
        variants.append(value)
        value = copy.deepcopy(base)
        value["data_root"] = "/var/lib/postgresql/18/main"
        variants.append(value)
        value = copy.deepcopy(base)
        value["execution_authorized"] = True
        variants.append(value)
        value = copy.deepcopy(base)
        value["restore"].append("--delta")
        variants.append(value)
        for value in variants:
            with self.assertRaises(restore.RestoreRefused):
                restore.validate_plan(value)


if __name__ == "__main__":
    unittest.main()
