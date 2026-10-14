"""Run isolated feedback regressions and a temporary loopback HTTP smoke."""
import argparse
import compileall
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--focused', action='store_true', help='Run feedback suites, syntax, and live smoke only')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('Choose a new receipt path')
    screenshot_dir = output.parent / (output.stem + '-screenshots')
    if screenshot_dir.exists():
        parser.error('Choose a receipt path with a new sibling screenshot directory')
    backend = Path(__file__).resolve().parents[1] / 'backend'
    sys.path.insert(0, str(backend))
    os.chdir(backend)
    receipt = {'started_at_unix': time.time(), 'focused': args.focused}
    with tempfile.TemporaryDirectory(prefix='ct-feedback-') as temporary:
        receipt['temporary_data_path'] = temporary
        # Set before importing app/config/database or any test module.
        os.environ.update(CAREERTRELLIS_DATA_DIR=temporary, CAREERTRELLIS_ENVIRONMENT='local',
                          CAREERTRELLIS_DATA_STORE='sqlite')
        os.environ.pop('CAREERTRELLIS_CONFIG_READ_ONLY', None)
        sys.pycache_prefix = str(Path(temporary) / 'bytecode')
        names = ['test_feedback', 'test_feedback_browser']
        if not args.focused:
            names = ['test_p0_regressions', 'test_object_store', 'test_object_import', 'test_stored_objects'] + names
        result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(names))
        skips = [{'test': test.id(), 'reason': reason} for test, reason in result.skipped]
        feedback_skips = any(item['test'].split('.')[0] in ('test_feedback', 'test_feedback_browser') for item in skips)
        syntax = compileall.compile_dir(str(backend), quiet=1,
            rx=re.compile(r'[/\\](?:venv|\.venv|__pycache__|\.pytest_cache)[/\\]'))
        receipt.update(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                       skipped=len(result.skipped), skipped_tests=skips, syntax=syntax,
                       passed=result.wasSuccessful() and syntax and not feedback_skips)
        if receipt['passed']:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            code = (
                'import sys,threading,uvicorn; '
                'server=uvicorn.Server(uvicorn.Config("app:app",host="127.0.0.1",port=int(sys.argv[1]),log_level="error")); '
                'thread=threading.Thread(target=server.run); thread.start(); '
                'sys.stdin.readline(); server.should_exit=True; thread.join(15); '
                'sys.exit(1 if thread.is_alive() else 0)'
            )
            process = subprocess.Popen([sys.executable, '-B', '-c', code, str(port)], cwd=backend,
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            receipt.update(preview_pid=process.pid, preview_port=port,
                           preview_started_at_unix=time.time())
            try:
                version = None
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/version', timeout=2) as response:
                            version = json.load(response)
                        break
                    except (OSError, ValueError):
                        if process.poll() is not None:
                            break
                        time.sleep(.2)
                paths = {}
                for path, expected in [('/', b'<html'), ('/feedback', b'feedback@jhunterj.com'),
                                       ('/static/feedback.css', b'focus'), ('/static/feedback.js', b'feedback@jhunterj.com')]:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}' + path, timeout=3) as response:
                        paths[path] = response.status == 200 and expected in response.read()
                receipt['smoke'] = {'version': version, 'paths': paths}
                receipt['passed'] &= bool(version) and all(paths.values())
                # Exercise the real response CSP, rather than the mocked browser-test responses.
                from playwright.sync_api import sync_playwright
                from urllib.parse import parse_qs, urlsplit
                screenshot_dir.mkdir()
                receipt['screenshots'] = []
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        page = browser.new_page(viewport={'width': 1280, 'height': 900})
                        page.route('**/*', lambda route: route.continue_()
                            if urlsplit(route.request.url).netloc == f'127.0.0.1:{port}' else route.abort())
                        response = page.goto(f'http://127.0.0.1:{port}/feedback?from=sign-in')
                        page.wait_for_function("document.getElementById('email-draft').href.includes('Sign-in')")
                        draft = urlsplit(page.locator('#email-draft').get_attribute('href'))
                        subject = parse_qs(draft.query)['subject'][0]
                        csp_present = bool(response.headers.get('content-security-policy'))
                        receipt['live_browser'] = {'csp_present': csp_present,
                            'context_subject_loaded': subject == 'CareerTrellis — Sign-in help',
                            'email_recipient_correct': draft.path == 'feedback@jhunterj.com'}
                        for label, width, height in [('desktop', 1280, 900), ('mobile', 320, 720)]:
                            page.set_viewport_size({'width': width, 'height': height})
                            path = screenshot_dir / (label + '.png')
                            page.screenshot(path=str(path), full_page=True)
                            no_overflow = page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                            receipt['screenshots'].append({'path': str(path), 'bytes': path.stat().st_size,
                                'viewport': {'width': width, 'height': height}, 'no_horizontal_overflow': no_overflow,
                                'owner': 'feedback implementation', 'retention': 'Retain for UX review with verification receipt'})
                            receipt['passed'] &= no_overflow
                        receipt['passed'] &= all(receipt['live_browser'].values())
                    finally:
                        browser.close()
            except Exception as error:
                receipt.update(passed=False, smoke_error='Loopback smoke or live browser verification did not complete',
                               smoke_error_type=type(error).__name__)
            finally:
                try:
                    process.communicate(b'\n', timeout=25)
                    receipt['preview_stopped_cleanly'] = process.returncode == 0
                except subprocess.TimeoutExpired:
                    # This handle belongs exclusively to the disposable child we created.
                    process.kill()
                    process.communicate(timeout=10)
                    receipt['preview_stopped_cleanly'] = False
                receipt['preview_stopped_at_unix'] = time.time()
                with socket.socket() as probe:
                    receipt['preview_listener_closed'] = probe.connect_ex(('127.0.0.1', port)) != 0
                receipt['passed'] &= receipt['preview_stopped_cleanly'] and receipt['preview_listener_closed']
    receipt['temporary_data_removed'] = not Path(temporary).exists()
    receipt['finished_at_unix'] = time.time()
    with output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(json.dumps(receipt))
    return 0 if receipt['passed'] and receipt['temporary_data_removed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
