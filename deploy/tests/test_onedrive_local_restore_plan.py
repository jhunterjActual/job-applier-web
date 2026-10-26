import copy
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
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
    def test_attempts_separate_immutable_paths_preserving_input_bindings(self):
        first = plan()
        second = restore.plan(LABEL, ARCHIVE, GENERATION, TX, 32 * 1024 ** 2, attempt=2)
        self.assertEqual(first['attempt'], 1)
        self.assertEqual(first['native_plan_path'], restore.ROOT + '/' + TX + '/native-plan.json')
        self.assertEqual(first['native_intent_path'], restore.ROOT + '/' + TX + '/native-restore-intent.json')
        self.assertEqual(second['attempt_root'], restore.RESTORE_ROOT + '/' + TX + '-02')
        self.assertEqual(second['owner_unit'], 'careertrellis-onedrive-bundle-restore-' + TX + '-02.service')
        self.assertEqual(second['native_plan_path'], restore.ROOT + '/' + TX + '/native-plan-02.json')
        self.assertEqual(second['web_preflight_path'], restore.ROOT + '/' + TX + '/web-preflight-02.json')
        self.assertEqual(second['native_intent_path'], restore.ROOT + '/' + TX + '/native-restore-02-intent.json')
        self.assertEqual(second['prior_reconciliation_path'], restore.ROOT + '/' + TX + '/native-reconciliation-01.json')
        for field in ('transaction', 'archive_sha256', 'generation_sha256', 'repository_input_root'):
            self.assertEqual(second[field], first[field])
        self.assertNotEqual(second['repository_root'], first['repository_root'])
        restore.validate_plan(second)
        for invalid in (0, 10, True, '2'):
            with self.assertRaises(restore.RestoreRefused):
                restore.plan(LABEL, ARCHIVE, GENERATION, TX, 1, attempt=invalid)
        second['attempt'] = 1
        with self.assertRaises(restore.RestoreRefused): restore.validate_plan(second)

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
        repository = restore.RESTORE_ROOT + "/" + TX + "/repository"
        self.assertEqual(value["repository_input_root"], restore.ROOT + "/" + TX + "/downloaded-repository")
        for command in (value["info"], value["restore"], value["archive_get_template"]):
            self.assertIn("--repo=1", command)
            self.assertIn("--repo1-type=posix", command)
            self.assertIn("--repo1-path=" + repository, command)
            self.assertIn("--config=" + restore.CONFIG, command)
            self.assertFalse(any(argument.startswith('--spool-path=') for argument in command))
            self.assertIn("--config-include-path=" + value["include_root"], command)
            self.assertEqual(command[:7], ["/usr/bin/setpriv", "--reuid=postgres",
                "--regid=postgres", "--init-groups", "--reset-env", "--", "/usr/bin/pgbackrest"])
        options = next(item for item in value["start"] if item.startswith("--options="))
        self.assertIn("restore_command='/usr/bin/pgbackrest ", options)
        self.assertIn('--no-archive-async', value['archive_get_template'])
        self.assertIn('--no-archive-async archive-get', options)
        self.assertNotIn('--no-archive-async', value['info'])
        self.assertNotIn('--no-archive-async', value['restore'])
        self.assertNotIn('--spool-path=', options)
        self.assertIn("--repo1-type=posix --repo1-path=" + repository, options)
        for forbidden in ("r2:", "onedrive:", "s3://", "https://", "--repo1-host=", "password"):
            self.assertNotIn(forbidden, restore.canonical(value).decode())

    def test_fixed_paths_private_socket_and_isolation(self):
        value = plan()
        attempt = restore.RESTORE_ROOT + "/" + TX
        self.assertEqual(value["attempt_root"], attempt)
        self.assertEqual(value["socket_root"], attempt + "/socket")
        self.assertEqual(value["locks_in_order"], list(restore.LOCKS))
        self.assertEqual(value["minimum_free_bytes"], 576 * 1024 ** 2 + 5 * 1024 ** 3)
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
        self.assertIn('-c hot_standby=off', options)
        self.assertNotIn('max_worker_processes=0', options)
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


def installed_fixture_supported():
    if os.name != 'posix' or os.geteuid() != 0:
        return False
    if not all(Path(path).is_file() for path in ('/usr/bin/pgbackrest', '/usr/bin/setpriv')):
        return False
    try:
        import pwd
        pwd.getpwnam('postgres')
    except (ImportError, KeyError):
        return False
    return Path('/run').is_dir()


@unittest.skipUnless(installed_fixture_supported(), 'requires native root installed pgBackRest and postgres account')
class InstalledCommandProfileTests(unittest.TestCase):
    def test_exact_profiles_accept_options_against_empty_synthetic_repository(self):
        """No live config/data/provider: only owned empty POSIX fixture paths."""
        import pwd
        account = pwd.getpwnam('postgres')
        with tempfile.TemporaryDirectory(prefix='bundle-command-fixture-', dir='/run') as temporary:
            root = Path(temporary)
            os.chown(root, 0, account.pw_gid); os.chmod(root, 0o710)
            fixture = {}
            for name in ('repository', 'data', 'tablespaces', 'lock', 'configured-spool', 'includes'):
                path = root / name; path.mkdir(mode=0o700)
                os.chown(path, account.pw_uid, account.pw_gid)
                fixture[name] = path
            # A configured but inactive spool is deliberately present. None of
            # these commands accepts a CLI spool-path in this synchronous mode.
            config = root / 'pgbackrest.conf'
            config.write_text('[global]\nrepo1-type=posix\nrepo1-path=' + str(fixture['repository']) +
                '\narchive-async=n\nspool-path=' + str(fixture['configured-spool']) +
                '\n[careertrellis]\npg1-path=' + str(fixture['data']) + '\npg1-port=55432\n', encoding='ascii')
            os.chown(config, 0, account.pw_gid); os.chmod(config, 0o640)
            value = plan()
            replacements = {
                '--config=' + restore.CONFIG: '--config=' + str(config),
                '--repo1-path=' + value['repository_root']: '--repo1-path=' + str(fixture['repository']),
                '--config-include-path=' + value['include_root']: '--config-include-path=' + str(fixture['includes']),
                '--lock-path=' + value['lock_root']: '--lock-path=' + str(fixture['lock']),
                '--pg1-path=' + value['data_root']: '--pg1-path=' + str(fixture['data']),
                '--tablespace-map-all=' + value['tablespace_root']: '--tablespace-map-all=' + str(fixture['tablespaces']),
                '%f': '000000010000000000000001', '%p': str(fixture['data'] / 'synthetic-wal-output')}
            for phase, expected in (('info', 0), ('restore', 75), ('archive_get_template', 55)):
                argv = [replacements.get(argument, argument) for argument in value[phase]]
                # Assert that no production filesystem coordinate survived the
                # profile mapping before handing arguments to the real binary.
                for argument in argv:
                    self.assertNotIn(restore.ROOT, argument)
                    self.assertNotIn(restore.RESTORE_ROOT, argument)
                    self.assertNotIn(restore.CONFIG, argument)
                    self.assertNotIn('/var/lib/postgresql', argument)
                result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'},
                    timeout=30, check=False)
                self.assertNotEqual(result.returncode, 31, phase + ' options rejected')
                self.assertEqual(result.returncode, expected, phase + ' synthetic missing-input result')


if __name__ == "__main__":
    unittest.main()
