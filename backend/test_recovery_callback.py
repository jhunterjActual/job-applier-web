"""Recovery handoff is a non-authorizing, inert response with no cookie effects."""
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from recovery_callback import is_recovery_callback, recovery_callback_response


STATE = 'r1_' + 'A' * 40


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class RecoveryCallbackTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for browser script behavioral checks')
    def test_script_scrubs_before_display_and_rejects_ambiguous_queries(self):
        script = Path(__file__).with_name('static').joinpath('recovery-callback.js').read_text(encoding='utf-8')
        driver = r'''
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const results = input.queries.map(query => {
  const events = []; let output = '';
  const field = {set value(v) {events.push('value'); output = v;}};
  const context = {window: {location: {search: query}, history: {
    replaceState(a,b,c) {if(a !== null || b !== '' || c !== '/auth/callback') throw Error(); events.push('scrub');}
  }}, document: {getElementById(id) {if(id !== 'recovery-response') throw Error(); return field;}}, URLSearchParams};
  vm.runInNewContext(input.script, context, {timeout: 1000});
  return {events, output};
});
process.stdout.write(JSON.stringify(results));
'''
        queries = ['?code=%3C%2Ftextarea%3E%3Cscript%3E&state=' + STATE,
                   '?code=x&code=y&state=' + STATE, '?code=x&state=r1_bad',
                   '?error=private_error&state=' + STATE, '?code=has+space&state=' + STATE,
                   '?code=x&state=' + STATE + '&extra=x',
                   '?code=' + '%22' * 2048 + '&state=' + STATE,
                   '?code=' + '%5C' * 2048 + '&state=' + STATE]
        run = subprocess.run([shutil.which('node'), '-e', driver],
            input=json.dumps({'script': script, 'queries': queries}), text=True,
            capture_output=True, check=True, timeout=10)
        results = json.loads(run.stdout)
        self.assertEqual(json.loads(results[0]['output']), {'code': '</textarea><script>', 'state': STATE})
        self.assertEqual(results[0]['events'], ['scrub', 'value'])
        for result in results[1:]:
            self.assertEqual(result, {'events': ['scrub'], 'output': ''})

    def test_exact_payload_with_private_inert_headers(self):
        response = recovery_callback_response([('code', 'synthetic:opaque-code'), ('state', STATE)])
        body = response.body.decode()
        self.assertNotIn('synthetic:opaque-code', body)
        self.assertNotIn(STATE, body)
        self.assertIn('src="/static/recovery-callback.js"', body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        for directive in ("default-src 'none'", "form-action 'none'", "frame-ancestors 'none'"):
            self.assertIn(directive, response.headers['content-security-policy'])
        self.assertNotIn('set-cookie', response.headers)

    def test_html_special_characters_cannot_escape_textarea(self):
        code = '</textarea><script>alert("x")</script>&'
        response = recovery_callback_response([('state', STATE), ('code', code)])
        parser = Tags(); parser.feed(response.body.decode())
        self.assertEqual(sum(tag == 'textarea' for tag, _ in parser.tags), 1)
        self.assertFalse(any(tag in ('a', 'form', 'img', 'link', 'iframe') for tag, _ in parser.tags))
        self.assertEqual([attrs for tag, attrs in parser.tags if tag == 'script'],
                         [{'src': '/static/recovery-callback.js', 'defer': None}])
        self.assertTrue(all(not any(key.startswith('on') for key in attrs) for _, attrs in parser.tags))
        textarea = next(attrs for tag, attrs in parser.tags if tag == 'textarea')
        self.assertIn('readonly', textarea)

    def test_cancellation_never_echoes_error_or_state(self):
        response = recovery_callback_response([('error', 'private_provider_error'), ('state', STATE)])
        body = response.body.decode()
        self.assertNotIn('private_provider_error', body)
        self.assertNotIn(STATE, body)
        self.assertNotIn('textarea', body)

    def test_reserved_prefix_recognized_even_if_malformed_or_duplicated(self):
        self.assertTrue(is_recovery_callback([('state', 'r1_bad')]))
        self.assertTrue(is_recovery_callback([('state', 'ordinary'), ('state', STATE)]))
        self.assertFalse(is_recovery_callback([('state', 'ordinary')]))

    def test_exact_duplicate_free_keys_required(self):
        for pairs in ([('state', STATE)], [('code', 'x'), ('state', STATE), ('code', 'y')],
                      [('code', 'x'), ('state', STATE), ('error', 'cancelled')],
                      [('code', 'x'), ('state', STATE), ('extra', 'x')],
                      {'code': 'x', 'state': STATE}):
            with self.subTest(pairs=pairs), self.assertRaises(ValueError):
                recovery_callback_response(pairs)

    def test_invalid_state_and_code_rejected(self):
        for state in ('r1_short', 'r1_' + 'A' * 41, 'x1_' + 'A' * 40, 'r1_' + '/' * 40):
            with self.assertRaises(ValueError):
                recovery_callback_response([('code', 'x'), ('state', state)])
        for code in ('', 'has space', 'line\nbreak', 'nonascii\u00e9', '\x7f', 'x' * 2049):
            with self.assertRaises(ValueError):
                recovery_callback_response([('code', code), ('state', STATE)])

    def test_serialized_payload_fits_hidden_terminal_bound(self):
        for code in ('"' * 2048, '\\' * 2048):
            with self.assertRaises(ValueError):
                recovery_callback_response([('code', code), ('state', STATE)])
        # Worst-case escaping remains allowed whenever the full JSON still fits.
        self.assertEqual(recovery_callback_response(
            [('code', '"' * 2000), ('state', STATE)]).status_code, 200)
        self.assertEqual(recovery_callback_response(
            [('code', 'x' * 2048), ('state', STATE)]).status_code, 200)


if __name__ == '__main__':
    unittest.main()
