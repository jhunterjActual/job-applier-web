"""Synthetic phase-boundary and executable destination-gate regressions."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'deploy' / 'scripts'


class GeneratedObjectPhaseTests(unittest.TestCase):
    def test_capture_and_publish_have_disjoint_provider_reads(self):
        mirror = (SCRIPTS / 'mirror-generated-objects-onedrive.sh').read_text()
        capture = mirror.split('if [[ ${mode} != publish-captured ]]; then', 2)[2]
        capture, publish = capture.split('# The online phase reads only', 1)
        self.assertIn('generatedobjectsource:${source_bucket}', capture)
        self.assertNotIn('${onedrive_remote}:', capture)
        self.assertIn('seal-capture', capture)
        self.assertIn('if [[ ${mode} == capture ]]; then', capture)
        self.assertIn('trap - EXIT', capture)
        self.assertNotIn('publish_health_receipt', capture)
        self.assertNotIn('generatedobjectsource:', publish)
        self.assertIn('verify-capture', publish)
        self.assertIn('${capture_root}/${snapshot_id}/objects', publish)
        self.assertLess(publish.index('verify-capture'), publish.index('rclone_fixed mkdir'))
        self.assertGreater(publish.rindex('verify-capture'), publish.rindex('rclone_fixed check'))
        self.assertNotIn('rm -rf -- "${capture_root}', mirror)

    def test_online_restore_keeps_isolation_and_locks(self):
        restore = (SCRIPTS / 'test-generated-object-restore.sh').read_text()
        self.assertNotIn('systemctl is-active', restore)
        self.assertIn('acquire_careertrellis_maintenance_lock', restore)
        self.assertIn('flock -n 9', restore)
        self.assertIn('raise SystemExit(1 if source == target else 0)', restore)
        self.assertNotIn('RCLONE_CONFIG_GENERATEDOBJECTSOURCE_ACCESS_KEY_ID=', restore)
        self.assertIn('verify-manifest', restore)
        self.assertLess(restore.index('verify-manifest'), restore.index('rclone_fixed copy "${source_root}"'))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Bash harness requires Linux')
    def test_destination_gate_fails_closed_and_creates_missing_folder(self):
        mirror = (SCRIPTS / 'mirror-generated-objects-onedrive.sh').read_text()
        gate = mirror.split('destination=${onedrive_remote}:', 1)[1]
        gate = 'destination=${onedrive_remote}:' + gate.split('rclone_fixed copy "${source_root}"', 1)[0]
        cases = {
            'missing': (0, 'mkdir\nsize\n'),
            'empty': (0, 'mkdir\nsize\n'),
            'mkdir_denied': (1, 'mkdir\n'),
            'size_denied': (1, 'mkdir\nsize\n'),
            'nonempty': (1, 'mkdir\nsize\n'),
            'bad_json': (1, 'mkdir\nsize\n'),
            'boolean_count': (1, 'mkdir\nsize\n'),
        }
        for case, (expected_code, expected_calls) in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                log = Path(temporary) / 'calls'
                harness = '''set -Eeuo pipefail
onedrive_remote=synthetic
onedrive_root=isolated
backup_label=20260905-000000F
rclone_error_log=/dev/null
rclone_fixed() {
    [[ $1 == mkdir ]] || exit 10
    printf 'mkdir\\n' >> "$CALL_LOG"
    [[ $CASE != mkdir_denied ]]
}
run_rclone() {
    [[ $1 == size ]] || exit 11
    printf 'size\\n' >> "$CALL_LOG"
    case "$CASE" in
        size_denied) return 3 ;;
        nonempty) printf '{"count":1,"bytes":42}' ;;
        bad_json) printf 'not-json' ;;
        boolean_count) printf '{"count":false,"bytes":0}' ;;
        *) printf '{"count":0,"bytes":0}' ;;
    esac
}
'''
                env = dict(os.environ, CASE=case, CALL_LOG=str(log), python_command=sys.executable)
                result = subprocess.run(['/bin/bash', '-c', harness + gate], env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(expected_code, result.returncode, result.stderr)
                self.assertEqual(expected_calls, log.read_text())
                self.assertEqual('', result.stdout)


if __name__ == '__main__':
    unittest.main()
