"""Isolated contract/P0 verification and a disposable loopback smoke."""
import argparse
import compileall
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--focused', action='store_true')
parser.add_argument('--recovery', action='store_true', help='Verify the actually disabled Claude correction')
args = parser.parse_args()
args.output = args.output.resolve()
if args.output.exists():
    parser.error('Choose a new receipt path')
backend = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(backend))
os.chdir(backend)
with tempfile.TemporaryDirectory(prefix='ct-ai001-') as temporary:
    os.environ.update(CAREERTRELLIS_DATA_DIR=temporary, CAREERTRELLIS_ENVIRONMENT='local',
                      CAREERTRELLIS_DATA_STORE='sqlite')
    os.environ.pop('CAREERTRELLIS_CONFIG_READ_ONLY', None)
    sys.pycache_prefix = str(Path(temporary) / 'bytecode')
    names = ['test_ai_contracts', 'test_claude_adapter', 'test_claude_integration', 'test_claude_storage',
             'test_claude_workflows', 'test_claude_search_failures', 'test_claude_browser',
             'test_claude_recovery']
    if args.recovery:
        import claude_adapter
        if claude_adapter.CLAUDE_ENABLED is not False:
            raise RuntimeError('Recovery verification requires Claude disabled in source')
        names = ['test_claude_recovery']
    if not args.focused:
        names += ['test_p0_regressions', 'test_object_store', 'test_object_import', 'test_stored_objects']
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    syntax = compileall.compile_dir(str(backend), quiet=1, rx=re.compile(r'[/\\]venv[/\\]'))
    receipt = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), syntax=syntax, passed=result.wasSuccessful() and syntax)
    if not args.focused and receipt['passed']:
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
        receipt['preview_pid'] = process.pid
        receipt['preview_port'] = port
        receipt['preview_started_at_unix'] = time.time()
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
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=3) as response:
                dashboard = response.status == 200 and b'<html' in response.read().lower()
            receipt['smoke'] = dict(version=version, dashboard=dashboard)
            receipt['passed'] &= bool(version) and dashboard
        finally:
            process.communicate(b'\n', timeout=25)
            receipt['preview_stopped_cleanly'] = process.returncode == 0
            with socket.socket() as probe:
                receipt['preview_listener_closed'] = probe.connect_ex(('127.0.0.1', port)) != 0
            receipt['passed'] &= receipt['preview_stopped_cleanly'] and receipt['preview_listener_closed']
receipt['temporary_data_removed'] = not Path(temporary).exists()
with args.output.open('x', encoding='utf-8') as stream:
    json.dump(receipt, stream, indent=2)
    stream.write('\n')
print(json.dumps(receipt))
raise SystemExit(0 if receipt['passed'] and receipt['temporary_data_removed'] else 1)
