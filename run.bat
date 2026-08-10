@echo off
setlocal
title CareerTrellis
echo ==========================================
echo    Starting CareerTrellis...
echo ==========================================

cd /d "%~dp0backend"
set "VENV_PYTHON=%CD%\venv\Scripts\python.exe"
set "REQUIREMENTS_IN=%CD%\requirements.in"
set "REQUIREMENTS_LOCK=%CD%\requirements.txt"
set "DEPENDENCY_LOCK_HELPER=%CD%\dependency_lock.py"
set "DEPENDENCY_STAMP=%CD%\venv\.jobapplier-requirements.sha256"
set "JOBAPPLIER_REQUESTED_PORT=%~1"
if not defined JOBAPPLIER_REQUESTED_PORT set "JOBAPPLIER_REQUESTED_PORT=8001"
powershell -NoProfile -Command "$value=0; if (-not [int]::TryParse($env:JOBAPPLIER_REQUESTED_PORT, [ref]$value) -or $value -lt 1 -or $value -gt 65535) { exit 1 }" >nul 2>nul
if errorlevel 1 goto invalid_port
set "PORT=%JOBAPPLIER_REQUESTED_PORT%"

"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10, 2) else 1)" >nul 2>nul
if errorlevel 1 goto repair_venv
"%VENV_PYTHON%" "%DEPENDENCY_LOCK_HELPER%" check --stamp "%DEPENDENCY_STAMP%" --lock "%REQUIREMENTS_LOCK%" "%REQUIREMENTS_IN%" "%REQUIREMENTS_LOCK%" >nul 2>nul
if not errorlevel 1 goto environment_ready
echo The dependency lock or installed environment changed. Rebuilding it...
set "BOOTSTRAP_PYTHON="
for /f "usebackq delims=" %%P in (`"%VENV_PYTHON%" -c "import sys; print(sys._base_executable)"`) do set "BOOTSTRAP_PYTHON=%%P"
goto find_bootstrap

:repair_venv
echo The project virtual environment is missing or invalid. Repairing it...
set "BOOTSTRAP_PYTHON="

:find_bootstrap
if defined BOOTSTRAP_PYTHON (
    "%BOOTSTRAP_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10, 2) else 1)" >nul 2>nul
    if errorlevel 1 set "BOOTSTRAP_PYTHON="
)
where python >nul 2>nul
if not defined BOOTSTRAP_PYTHON if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10, 2) else 1)" >nul 2>nul
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=python"
)

if not defined BOOTSTRAP_PYTHON (
    set "CODEX_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if exist "%CODEX_PYTHON%" set "BOOTSTRAP_PYTHON=%CODEX_PYTHON%"
)

if defined BOOTSTRAP_PYTHON (
    "%BOOTSTRAP_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10, 2) else 1)" >nul 2>nul
    if errorlevel 1 set "BOOTSTRAP_PYTHON="
)

if not defined BOOTSTRAP_PYTHON (
    echo Error: A working Python 3.10.2+ installation was not found.
    echo Install Python from python.org, then run this script again.
    pause
    exit /b 1
)

for /f "usebackq delims=" %%G in (`powershell -NoProfile -Command "[guid]::NewGuid().ToString('N')"`) do set "REBUILD_ID=%%G"
if not defined REBUILD_ID goto setup_failed
set "VENV_BACKUP=%CD%\.venv-previous-%REBUILD_ID%"
set "OLD_ENVIRONMENT_MOVED="

rem Preserve the old environment before changing it. Windows refuses this move
rem while extension modules are active, leaving the running copy intact.
if exist "%CD%\venv" (
    move "%CD%\venv" "%VENV_BACKUP%" >nul
    if errorlevel 1 goto environment_in_use
    set "OLD_ENVIRONMENT_MOVED=1"
)
set "REPLACEMENT_STARTED=1"

rem Build at the final path so activation scripts and executable entry points
rem contain stable paths. Any later failure restores the untouched backup.
"%BOOTSTRAP_PYTHON%" -m venv "%CD%\venv"
if errorlevel 1 goto setup_failed

:sync_environment
echo Synchronizing the reviewed dependency lock...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check --require-hashes --requirement "%REQUIREMENTS_LOCK%"
if errorlevel 1 goto setup_failed
"%VENV_PYTHON%" -m pip check
if errorlevel 1 goto setup_failed
"%VENV_PYTHON%" -m playwright install chromium
if errorlevel 1 goto setup_failed
"%VENV_PYTHON%" "%DEPENDENCY_LOCK_HELPER%" write --stamp "%DEPENDENCY_STAMP%" --lock "%REQUIREMENTS_LOCK%" "%REQUIREMENTS_IN%" "%REQUIREMENTS_LOCK%"
if errorlevel 1 goto setup_failed
if exist "%VENV_BACKUP%" (
    rmdir /s /q "%VENV_BACKUP%" 2>nul
    if exist "%VENV_BACKUP%" echo Warning: The previous environment remains at "%VENV_BACKUP%" and can be removed after the older CareerTrellis process stops.
)
echo Environment repair complete.

:environment_ready
echo Python environment verified.
echo Launching FastAPI Backend...
echo Access the Dashboard at http://127.0.0.1:%PORT%/
echo Press Ctrl+C to stop CareerTrellis.

start "" /b powershell -NoProfile -WindowStyle Hidden -Command "$ready=$false; 1..30 | ForEach-Object { if (-not $ready) { Start-Sleep 1; try { $v=Invoke-RestMethod 'http://127.0.0.1:%PORT%/api/version' -TimeoutSec 1; if ($v.build -eq '20260808.23') { $ready=$true; Start-Process 'http://127.0.0.1:%PORT%/?build=20260808.23' } } catch {} } }"
"%VENV_PYTHON%" -m uvicorn app:app --host 127.0.0.1 --port %PORT% --reload
set "SERVER_EXIT=%ERRORLEVEL%"
pause
exit /b %SERVER_EXIT%

:invalid_port
echo Error: Port must be a whole number from 1 through 65535.
exit /b 1

:setup_failed
if defined REPLACEMENT_STARTED if exist "%CD%\venv" rmdir /s /q "%CD%\venv" 2>nul
if defined OLD_ENVIRONMENT_MOVED (
    if exist "%VENV_BACKUP%" if not exist "%CD%\venv" move "%VENV_BACKUP%" "%CD%\venv" >nul
    if exist "%VENV_BACKUP%" echo The previous environment remains at "%VENV_BACKUP%" and could not be restored automatically.
)
echo Failed to repair the Python environment.
pause
exit /b 1

:environment_in_use
echo Unable to replace the virtual environment.
echo Stop any running CareerTrellis copy with Ctrl+C, then run this script again.
pause
exit /b 1
