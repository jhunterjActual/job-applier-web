# Regenerate the application dependency lock in disposable clean environments.
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepositoryRoot "backend"
$ToolLock = Join-Path $PSScriptRoot "dependency-tools.txt"
$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$WorkDir = Join-Path $TempRoot ("jobapplier-dependencies-" + [guid]::NewGuid().ToString("N"))
$WorkDir = [IO.Path]::GetFullPath($WorkDir)
$StagedLock = $null
$BackupLock = $null

function Test-PythonExecutable([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        & $Path -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10, 2) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-UpdatePython {
    if ($Python) {
        if (Test-PythonExecutable $Python) { return $Python }
        throw "The Python executable passed with -Python is unavailable or older than Python 3.10.2."
    }

    $candidates = @(Join-Path $BackendDir "venv\Scripts\python.exe")
    $pathPython = Get-Command "python" -ErrorAction SilentlyContinue
    if ($pathPython) { $candidates += $pathPython.Source }
    $candidates += Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    foreach ($candidate in $candidates) {
        if (Test-PythonExecutable $candidate) { return $candidate }
    }
    throw "A working Python 3.10.2+ installation was not found. Pass its full path with -Python."
}

if (-not $WorkDir.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to create a dependency workspace outside the system temporary directory."
}

try {
    $BootstrapPython = Find-UpdatePython
    New-Item -ItemType Directory -Path $WorkDir | Out-Null
    $CompilerVenv = Join-Path $WorkDir "compiler"
    $ValidationVenv = Join-Path $WorkDir "validation"
    $CompileDir = Join-Path $WorkDir "source"
    New-Item -ItemType Directory -Path $CompileDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $BackendDir "requirements.in") -Destination (Join-Path $CompileDir "requirements.in")

    & $BootstrapPython -m venv $CompilerVenv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the dependency compiler environment." }
    $CompilerPython = Join-Path $CompilerVenv "Scripts\python.exe"
    & $CompilerPython -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: --requirement $ToolLock
    if ($LASTEXITCODE -ne 0) { throw "Failed to install the hash-locked dependency compiler." }

    Push-Location $CompileDir
    try {
        & $CompilerPython -m piptools compile --generate-hashes --resolver=backtracking --strip-extras --no-emit-index-url --output-file requirements.txt requirements.in
        if ($LASTEXITCODE -ne 0) { throw "Failed to compile the dependency lock." }
    } finally {
        Pop-Location
    }

    $CandidateLock = Join-Path $CompileDir "requirements.txt"
    & $BootstrapPython -m venv $ValidationVenv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the clean validation environment." }
    $ValidationPython = Join-Path $ValidationVenv "Scripts\python.exe"
    & $ValidationPython -m pip install --disable-pip-version-check --require-hashes --requirement $CandidateLock
    if ($LASTEXITCODE -ne 0) { throw "The generated dependency lock could not be installed." }
    & $ValidationPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "The generated dependency lock is inconsistent." }

    $PreviousTestRequirementsIn = [Environment]::GetEnvironmentVariable("JOBAPPLIER_TEST_REQUIREMENTS_IN", "Process")
    $PreviousTestRequirementsLock = [Environment]::GetEnvironmentVariable("JOBAPPLIER_TEST_REQUIREMENTS_LOCK", "Process")
    [Environment]::SetEnvironmentVariable("JOBAPPLIER_TEST_REQUIREMENTS_IN", (Join-Path $CompileDir "requirements.in"), "Process")
    [Environment]::SetEnvironmentVariable("JOBAPPLIER_TEST_REQUIREMENTS_LOCK", $CandidateLock, "Process")
    Push-Location $BackendDir
    try {
        & $ValidationPython -m unittest test_p0_regressions.py
        if ($LASTEXITCODE -ne 0) { throw "P0 regression tests failed with the generated lock." }
        & $ValidationPython -m unittest test_object_store.py test_object_import.py test_stored_objects.py
        if ($LASTEXITCODE -ne 0) { throw "Private object-storage tests failed with the generated lock." }
        & $ValidationPython -m unittest test_analytics.py
        if ($LASTEXITCODE -ne 0) { throw "Analytics tests failed with the generated lock." }
        & $ValidationPython -m unittest test_observability.py
        if ($LASTEXITCODE -ne 0) { throw "Observability tests failed with the generated lock." }
        & $ValidationPython -m compileall -q .
        if ($LASTEXITCODE -ne 0) { throw "Python syntax validation failed with the generated lock." }
    } finally {
        Pop-Location
        [Environment]::SetEnvironmentVariable("JOBAPPLIER_TEST_REQUIREMENTS_IN", $PreviousTestRequirementsIn, "Process")
        [Environment]::SetEnvironmentVariable("JOBAPPLIER_TEST_REQUIREMENTS_LOCK", $PreviousTestRequirementsLock, "Process")
    }

    $DestinationLock = Join-Path $BackendDir "requirements.txt"
    $PublishId = [guid]::NewGuid().ToString("N")
    $StagedLock = Join-Path $BackendDir ".requirements.$PublishId.tmp"
    $BackupLock = Join-Path $BackendDir ".requirements.$PublishId.bak"
    Copy-Item -LiteralPath $CandidateLock -Destination $StagedLock
    if ((Get-FileHash -LiteralPath $CandidateLock -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $StagedLock -Algorithm SHA256).Hash) {
        throw "The staged dependency lock did not match the validated candidate."
    }
    if (Test-Path -LiteralPath $DestinationLock) {
        [IO.File]::Replace($StagedLock, $DestinationLock, $BackupLock, $true)
    } else {
        [IO.File]::Move($StagedLock, $DestinationLock)
    }
    $StagedLock = $null
    if (Test-Path -LiteralPath $BackupLock) {
        try {
            Remove-Item -LiteralPath $BackupLock -Force
        } catch {
            Write-Warning "The dependency lock was published, but its backup remains at $BackupLock."
        }
    }
    $BackupLock = $null
    Write-Host "Dependency lock regenerated and validated. Review requirements.in and requirements.txt before committing." -ForegroundColor Green
} finally {
    foreach ($PublishArtifact in @($StagedLock, $BackupLock)) {
        if ($PublishArtifact -and (Test-Path -LiteralPath $PublishArtifact)) {
            try { Remove-Item -LiteralPath $PublishArtifact -Force } catch { Write-Warning "Could not clean up $PublishArtifact" }
        }
    }
    if (Test-Path -LiteralPath $WorkDir) {
        try {
            $ResolvedWorkDir = [IO.Path]::GetFullPath((Resolve-Path -LiteralPath $WorkDir).Path)
            if (-not $ResolvedWorkDir.StartsWith($TempRoot, [StringComparison]::OrdinalIgnoreCase)) {
                Write-Warning "Refusing to remove a dependency workspace outside the system temporary directory."
            } else {
                Remove-Item -LiteralPath $ResolvedWorkDir -Recurse -Force
            }
        } catch {
            Write-Warning "Could not clean up the temporary dependency workspace: $($_.Exception.Message)"
        }
    }
}
