"""Display a recovery-only authorization response for the attended operator.

This page grants no authority. The protected terminal verifies state and PKCE,
then exchanges the code. Never pass a dict: preserve duplicate query parameters.
"""
import json
import re

from starlette.responses import HTMLResponse


STATE = re.compile(r'r1_[A-Za-z0-9_-]{40}')
HEADERS = {
    'Cache-Control': 'no-store',
    'Pragma': 'no-cache',
    'Referrer-Policy': 'no-referrer',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; connect-src 'none'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'",
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
}


def _pairs(pairs):
    if not isinstance(pairs, (list, tuple)) or len(pairs) > 16:
        raise ValueError('Invalid recovery callback.')
    if any(not isinstance(pair, (list, tuple)) or len(pair) != 2
           or type(pair[0]) is not str or type(pair[1]) is not str for pair in pairs):
        raise ValueError('Invalid recovery callback.')
    return pairs


def is_recovery_callback(pairs):
    """Recognize the reserved namespace, including malformed recovery attempts."""
    return any(key == 'state' and value.startswith('r1_') for key, value in _pairs(pairs))


def _printable(value, maximum):
    return 0 < len(value) <= maximum and all(33 <= ord(char) <= 126 for char in value)


def recovery_callback_response(pairs):
    pairs = _pairs(pairs)
    values = dict(pairs)
    if (len(pairs) != 2 or len(values) != 2
            or set(values) not in ({'code', 'state'}, {'error', 'state'})
            or STATE.fullmatch(values['state']) is None):
        raise ValueError('Invalid recovery callback.')
    if 'error' in values:
        if not _printable(values['error'], 2048):
            raise ValueError('Invalid recovery callback.')
        body = ('<h1>Recovery sign-in was not completed</h1>'
                '<p>Return to the recovery terminal. No recovery response is available.</p>')
    else:
        if (not _printable(values['code'], 2048)
                or len(json.dumps({'code': values['code'], 'state': values['state']},
                                  separators=(',', ':'), ensure_ascii=True)) > 4096):
            raise ValueError('Invalid recovery callback.')
        body = ('<h1>Continue account recovery</h1>'
                '<p>Copy the complete recovery response below and paste it into the hidden '
                'input in the recovery terminal that started this sign-in.</p>'
                '<p>Keep this response private. Close this page after pasting it.</p>'
                '<label for="recovery-response">Recovery response</label>'
                '<textarea id="recovery-response" readonly rows="8" cols="72" '
                'spellcheck="false" autocomplete="off"></textarea>'
                '<noscript>JavaScript must be enabled to display the recovery response.</noscript>')
    return HTMLResponse('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width, initial-scale=1">'
                        '<title>Account recovery</title>'
                        '<script src="/static/recovery-callback.js" defer></script>'
                        '</head><body>' + body + '</body></html>',
                        headers=HEADERS)
