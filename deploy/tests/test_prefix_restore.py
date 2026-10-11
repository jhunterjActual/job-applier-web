"""Offline contract tests execute the candidate's actual bash blocks with mock IO."""
import json
import os
from pathlib import Path
import subprocess
import shlex
import shutil
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/test-generated-object-prefix-restore.sh'
SOURCE = SCRIPT.read_text(encoding='utf-8')
GIT_BASH = Path(r'C:\Program Files\Git\bin\bash.exe')
BASH = str(GIT_BASH) if os.name == 'nt' and GIT_BASH.is_file() else shutil.which('bash')
PYTHON = Path(os.sys.executable).as_posix()
PREFIX = 'rehearsals/20261011T010203Z-' + 'a' * 32


@unittest.skipUnless(BASH, 'Bash unavailable: install Bash to run shell contract tests')
class PrefixRestoreTests(unittest.TestCase):
    def run_block(self, code, args=()):
        setup = 'set -- ' + ' '.join(shlex.quote(value) for value in args) + '\n'
        return subprocess.run([BASH, '-s'], input='set -Eeuo pipefail\n' + setup + code, capture_output=True, text=True)

    def test_syntax(self):
        result = subprocess.run([BASH, '-n'], input=SOURCE, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_prefix_and_legacy_output_contract(self):
        block = SOURCE[SOURCE.rindex('trap - EXIT'):]
        for prefix in ('', PREFIX):
            setup = (f'target_prefix={prefix}\nauthority_before=staging\nbackup_label=20261010-010203F\n' +
                     'expected_manifest_sha256='+'a'*64+'\nrelease_revision='+'b'*40+'\nsnapshot_id=snapshot\n')
            result = self.run_block(setup + block)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            if prefix:
                self.assertEqual(receipt['target_prefix'], prefix)
                self.assertEqual(receipt['result'], 'disposable_r2_restore_verified_and_left_intact')
            else:
                self.assertNotIn('target_prefix', receipt)
                self.assertEqual(receipt['result'], 'disposable_r2_restore_verified_and_left_intact')

    def argument_block(self):
        constants = SOURCE[SOURCE.index('readonly target_confirmation='):SOURCE.index('readonly release_root=')]
        return constants + SOURCE[SOURCE.index('release_dir=${1:-}'):SOURCE.index('if [[ ! ${backup_label}')]

    def test_legacy_and_explicit_prefix_acknowledgements(self):
        old = 'I_HAVE_VERIFIED_THE_R2_TARGET_IS_DISPOSABLE_AND_EMPTY'
        new = 'I_HAVE_VERIFIED_THE_R2_PREFIX_IS_DISPOSABLE_AND_EMPTY'
        leave = 'I_UNDERSTAND_THE_DISPOSABLE_R2_TARGET_WILL_BE_LEFT_INTACT'
        args = ['release', 'label', 'hash', 'env']
        cases = [(args+[old, leave], True), (args+[new, leave, PREFIX], True),
                 (args+[old, leave, PREFIX], False), (args+[new, leave], False),
                 (args+[new, leave, PREFIX+'/../v1'], False),
                 (args+[new, leave, PREFIX+'\n'], False),
                 (args+[new, leave, PREFIX.upper()], False),
                 (args+[new, leave, PREFIX, 'extra'], False)]
        for values, passes in cases:
            with self.subTest(values=values):
                result = self.run_block(self.argument_block(), values)
                self.assertEqual(result.returncode == 0, passes, result.stderr)

    def transfer_block(self):
        start = SOURCE.index('rclone_fixed copy "${source_root}" "${target_remote}"')
        return SOURCE[start:SOURCE.index('# Keep canonical v1 ciphertext', start)]

    def test_only_prefix_target_and_exact_post_copy_inventory(self):
        for count, size, passes in [(6, 72, True), (7, 72, False), (6, 73, False), (True, 72, False)]:
            with self.subTest(count=count, size=size):
                code = ('python_command=' + "'" + PYTHON + "'\n" +
                        'source_root=/local/source\n' +
                        f'target_remote=generatedobjectrestore:retained/{PREFIX}\n' +
                        'rclone_error_log=/dev/null\nhealth_object_count=6\nhealth_byte_count=72\n' +
                        'rclone_fixed() { printf "%s\\n" "$*"; }\n' +
                        "run_rclone() { printf '%s' '" + json.dumps({'count': count, 'bytes': size}) + "'; }\n" +
                        self.transfer_block())
                result = self.run_block(code)
                self.assertEqual(result.returncode == 0, passes, result.stderr)
                lines = result.stdout.splitlines()
                self.assertIn(f'copy /local/source generatedobjectrestore:retained/{PREFIX} --immutable', lines[0])
                self.assertIn(f'check /local/source generatedobjectrestore:retained/{PREFIX} --download', lines[1])

    def test_target_emptiness_is_scoped_and_fail_closed(self):
        start = SOURCE.index('target_remote=generatedobjectrestore:')
        block = SOURCE[start:SOURCE.index('snapshot_source=', start)]
        for count, passes in [(0, True), (1, False)]:
            code = (f"python_command='{PYTHON}'\ntarget_bucket=retained\ntarget_prefix={PREFIX}\n" +
                    'rclone_error_log=/dev/null\n' +
                    'run_rclone() { [[ "$2" == "generatedobjectrestore:retained/'+PREFIX+'" ]] || return 1; '+
                    "printf '%s' '"+json.dumps({'count': count, 'bytes': 0})+"'; }\n" + block)
            result = self.run_block(code)
            self.assertEqual(result.returncode == 0, passes, result.stderr)


if __name__ == '__main__':
    unittest.main()
