"""Verify role-review changes with disposable data and loopback-only HTTP."""
import argparse
import compileall
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--focused', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('Choose a new receipt path')
    backend = Path(__file__).resolve().parents[1] / 'backend'
    sys.path.insert(0, str(backend))
    os.chdir(backend)
    receipt = {}
    with tempfile.TemporaryDirectory(prefix='ct-search-review-') as temporary:
        os.environ.update(CAREERTRELLIS_DATA_DIR=temporary, CAREERTRELLIS_ENVIRONMENT='local', CAREERTRELLIS_DATA_STORE='sqlite')
        os.environ.pop('CAREERTRELLIS_CONFIG_READ_ONLY', None)
        sys.pycache_prefix = str(Path(temporary) / 'bytecode')
        names = ['test_search_review', 'test_role_queries', 'test_role_plan_browser',
                 'test_coverage_diagnostics', 'test_search_completion_browser', 'test_discovery_sources']
        if not args.focused:
            names += ['test_p0_regressions', 'test_object_store', 'test_object_import', 'test_stored_objects']
        result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(names))
        syntax = compileall.compile_dir(str(backend), quiet=1, rx=re.compile(r'[/\\](?:venv|\.venv|__pycache__)[/\\]'))
        receipt.update(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                       skips=[{'test': test.id(), 'reason': reason} for test, reason in result.skipped], syntax=syntax)
        receipt['passed'] = result.wasSuccessful() and syntax
        if receipt['passed']:
            import uvicorn
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config('app:app', host='127.0.0.1', port=port, log_level='error'))
            thread = threading.Thread(target=server.run)
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(.1)
                origin = f'http://127.0.0.1:{port}'
                with urllib.request.urlopen(origin + '/api/version', timeout=5) as response:
                    receipt['version'] = json.load(response)
                with urllib.request.urlopen(origin + '/', timeout=5) as response:
                    receipt['dashboard'] = response.status == 200 and b'search-title-plan' in response.read()
                request = urllib.request.Request(origin + '/api/jobs/search/plan',
                    data=json.dumps({'keywords': ''}).encode(), headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    receipt['private_preview'] = 'no-store' in response.headers.get('Cache-Control', '') and json.load(response)['mode'] == 'resume_suggested'
                from playwright.sync_api import sync_playwright
                from urllib.parse import urlsplit
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        page = browser.new_page()
                        page.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).netloc == f'127.0.0.1:{port}' else route.abort())
                        page.goto(origin)
                        page.wait_for_function("document.getElementById('search-title-plan').textContent.includes('shown in its result')")
                        page.locator('#s-keywords').evaluate("element => { element.value = 'Data Engineer'; element.dispatchEvent(new Event('input')); }")
                        page.wait_for_function("document.getElementById('search-title-plan').textContent.includes('Data Engineer')")
                        page.locator('#s-keywords').evaluate("element => { element.value = ''; element.dispatchEvent(new Event('input')); }")
                        page.wait_for_function("document.getElementById('search-title-plan').textContent.includes('shown in its result')")
                        receipt['browser_preview'] = True
                    finally:
                        browser.close()
            finally:
                server.should_exit = True
                thread.join(15)
                receipt['server_stopped'] = not thread.is_alive()
            receipt['passed'] &= receipt['dashboard'] and receipt['private_preview'] and receipt['browser_preview'] and receipt['server_stopped']
    receipt['temporary_data_removed'] = not Path(temporary).exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt))
    return 0 if receipt['passed'] and receipt['temporary_data_removed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
