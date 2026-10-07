"""Reviewable local-only startup verification; explicit --run required.

Execution requires applicable authority for this alternative to run.ps1.
Uses the invoking interpreter without repairing a
venv, temporary synthetic data, loopback only and an owned graceful-stop pipe.
"""
import json
import os
import runpy
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def serve(port):
    data = Path(os.environ['CAREERTRELLIS_DATA_DIR'])
    if (os.environ.get('CAREERTRELLIS_CONFIG_READ_ONLY')!='1'
        or os.environ.get('CAREERTRELLIS_ENVIRONMENT')!='local'
        or not data.name.startswith('ct-local-startup-') or not data.is_dir()):
        raise RuntimeError('Synthetic startup guard rejected')
    sys.path.insert(0,str(ROOT/'backend'))
    import uvicorn
    server=uvicorn.Server(uvicorn.Config('app:app',host='127.0.0.1',port=port,
        access_log=False,log_level='critical'))
    def stop():
        # EOF also ends this one owned server if the parent exits unexpectedly.
        sys.stdin.readline()
        server.should_exit=True
    threading.Thread(target=stop,daemon=True).start()
    server.run()


def run():
    with tempfile.TemporaryDirectory(prefix='ct-local-startup-') as folder:
        for name in ('uploads','output'): (Path(folder)/name).mkdir()
        env={name:os.environ[name] for name in ('SystemRoot','WINDIR','PATH','TEMP','TMP','LOCALAPPDATA') if name in os.environ}
        env.update(CAREERTRELLIS_CONFIG_READ_ONLY='1',CAREERTRELLIS_ENVIRONMENT='local',
            CAREERTRELLIS_DATA_DIR=folder,PYTHONDONTWRITEBYTECODE='1')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--serve',str(port)],
            cwd=ROOT/'backend',env=env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        ready=False
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Synthetic startup failed')
                try:
                    with opener.open(f'http://127.0.0.1:{port}/api/version',timeout=1) as response:
                        version=json.load(response)
                    break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('Synthetic readiness deadline')
            expected_build=runpy.run_path(str(ROOT/'backend/version.py'))['APP_BUILD']
            if version.get('build')!=expected_build: raise RuntimeError('Build mismatch')
            with opener.open(f'http://127.0.0.1:{port}/',timeout=2) as response:
                if response.status!=200 or b'CareerTrellis' not in response.read(): raise RuntimeError('Dashboard unavailable')
            with opener.open(f'http://127.0.0.1:{port}/api/session',timeout=2) as response:
                if json.load(response).get('authenticator_setup_available') is not False: raise RuntimeError('Expected default-off')
            ready=True
        finally:
            try:
                if process.poll() is None:
                    process.stdin.write(b'STOP\n'); process.stdin.flush()
                process.wait(timeout=10)
            except (OSError,subprocess.TimeoutExpired):
                if process.poll() is None: process.kill(); process.wait(timeout=5)
                raise RuntimeError('Graceful stop unverified')
            finally:
                process.stdin.close()
        if not ready or process.returncode!=0: raise RuntimeError('Startup acceptance incomplete')
    print('synthetic_loopback_version_dashboard_default_off_and_graceful_stop=passed')


if __name__=='__main__':
    try:
        if sys.argv[1:]==['--run']: run()
        elif len(sys.argv)==3 and sys.argv[1]=='--serve' and sys.argv[2].isdigit(): serve(int(sys.argv[2]))
        else: raise RuntimeError('Explicit rehearsal mode required')
    except Exception:
        print('Synthetic local startup verification incomplete.',file=sys.stderr)
        raise SystemExit(1)
