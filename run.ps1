# Start Script for CareerTrellis
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8001
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   Starting CareerTrellis...              " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $ScriptDir "backend"
$VenvDir = Join-Path $BackendDir "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$RequirementsIn = Join-Path $BackendDir "requirements.in"
$RequirementsLock = Join-Path $BackendDir "requirements.txt"
$DependencyLockHelper = Join-Path $BackendDir "dependency_lock.py"
$DependencyStamp = Join-Path $VenvDir ".jobapplier-requirements.sha256"
Set-Location $BackendDir

function Test-PythonExecutable([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        & $Path -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10, 2) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-BootstrapPython([string]$Preferred = "") {
    $candidates = @()
    if ($Preferred) { $candidates += $Preferred }
    $pathPython = Get-Command "python" -ErrorAction SilentlyContinue
    if ($pathPython) { $candidates += $pathPython.Source }
    $candidates += Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

    foreach ($candidate in $candidates) {
        if (Test-PythonExecutable $candidate) { return $candidate }
    }
    return $null
}

$EnvironmentValid = Test-PythonExecutable $VenvPython
$NeedsEnvironmentRebuild = -not $EnvironmentValid
$PreferredBootstrapPython = ""

if ($EnvironmentValid) {
    & $VenvPython $DependencyLockHelper check --stamp $DependencyStamp --lock $RequirementsLock $RequirementsIn $RequirementsLock *> $null
    $NeedsEnvironmentRebuild = $LASTEXITCODE -ne 0
    if ($NeedsEnvironmentRebuild) {
        $PreferredBootstrapPython = (& $VenvPython -c "import sys; print(sys._base_executable)").Trim()
        Write-Host "The dependency lock or installed environment changed. Rebuilding it..." -ForegroundColor Yellow
    }
} else {
    Write-Host "The project virtual environment is missing or invalid. Repairing it..." -ForegroundColor Yellow
}

if ($NeedsEnvironmentRebuild) {
    $BootstrapPython = Find-BootstrapPython $PreferredBootstrapPython
    if (-not $BootstrapPython) {
        Write-Host "Error: A working Python 3.10.2+ installation was not found." -ForegroundColor Red
        Write-Host "Install Python from python.org, then run this script again." -ForegroundColor Red
        Exit 1
    }

    $RebuildId = [guid]::NewGuid().ToString("N")
    $BackupVenvDir = Join-Path $BackendDir ".venv-previous-$RebuildId"
    $OldEnvironmentMoved = $false
    $ReplacementStarted = $false

    try {
        # Preserve the old environment before changing it. Windows refuses this
        # move while extension modules are active, leaving the running copy intact.
        if (Test-Path -LiteralPath $VenvDir) {
            try {
                Move-Item -LiteralPath $VenvDir -Destination $BackupVenvDir
                $OldEnvironmentMoved = $true
            } catch {
                throw "Unable to replace the virtual environment. Stop any running CareerTrellis copy with Ctrl+C, then run this script again."
            }
        }
        $ReplacementStarted = $true

        # Build at the final path so activation scripts and executable entry points
        # contain stable paths. Any failure below restores the untouched backup.
        & $BootstrapPython -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "Failed to create the replacement virtual environment." }
        Write-Host "Synchronizing the reviewed dependency lock..." -ForegroundColor Yellow
        & $VenvPython -m pip install --disable-pip-version-check --require-hashes --requirement $RequirementsLock
        if ($LASTEXITCODE -ne 0) { throw "Failed to install Python dependencies." }

        & $VenvPython -m pip check
        if ($LASTEXITCODE -ne 0) { throw "The installed Python dependencies are inconsistent." }

        & $VenvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "Failed to install the Playwright browser." }

        & $VenvPython $DependencyLockHelper write --stamp $DependencyStamp --lock $RequirementsLock $RequirementsIn $RequirementsLock
        if ($LASTEXITCODE -ne 0) { throw "Failed to record the dependency lock state." }

        if ($OldEnvironmentMoved -and (Test-Path -LiteralPath $BackupVenvDir)) {
            try {
                Remove-Item -LiteralPath $BackupVenvDir -Recurse -Force
            } catch {
                Write-Warning "The previous environment remains at $BackupVenvDir and can be removed after the older CareerTrellis process stops."
            }
        }
    } catch {
        if ($ReplacementStarted -and (Test-Path -LiteralPath $VenvDir)) {
            Remove-Item -LiteralPath $VenvDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        if ($OldEnvironmentMoved -and (Test-Path -LiteralPath $BackupVenvDir)) {
            try {
                Move-Item -LiteralPath $BackupVenvDir -Destination $VenvDir
            } catch {
                throw "Environment repair failed and the previous environment could not be restored automatically. It remains at $BackupVenvDir."
            }
        }
        throw
    }
    Write-Host "Environment repair complete." -ForegroundColor Green
}

Write-Host "Python environment verified." -ForegroundColor Green

# Open the UI only after the backend responds, so a failed launch cannot leave
# a misleading cached dashboard in the browser.
Start-Job -ArgumentList $Port -ScriptBlock {
    param([int]$Port)
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $version = Invoke-RestMethod "http://127.0.0.1:$Port/api/version" -TimeoutSec 1
            if ($version.build -eq "20260808.23") {
                Start-Process "http://127.0.0.1:$Port/?build=20260808.23"
                return
            }
        } catch { }
    }
    Write-Host "The backend did not become ready; the dashboard was not opened." -ForegroundColor Red
} | Out-Null

Write-Host "Launching FastAPI Backend..." -ForegroundColor Yellow
Write-Host "Access the Dashboard at http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop CareerTrellis." -ForegroundColor Magenta
& $VenvPython -m uvicorn app:app --host 127.0.0.1 --port $Port --reload
