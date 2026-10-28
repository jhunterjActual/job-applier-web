param(
    [Parameter(Mandatory)][string]$ScriptPath,
    [Parameter(Mandatory)][ValidateSet('web','db')][string]$Role,
    [Parameter(Mandatory)][ValidateSet('preflight','stage','readback','stage-packet','readback-packet','export-plan','stage-plan','readback-plan')][string]$Mode,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$PackageManifestSha256,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$StdinSha256,
    [Parameter(Mandatory)][string]$ResultBase,
    [switch]$Run
)
$ErrorActionPreference = 'Stop'
Import-Module 'C:/Users/hunte/Documents/Codex/agent-tooling/scripts/WindowsWorkflow.psm1'
$null = Get-WorkflowEnvironment
$taskRoot = 'C:/Users/hunte/Documents/Antigravity/jobapplier'
$taskEvidence = [IO.Path]::GetFullPath((Join-Path $taskRoot 'artifacts/claude-cache-deploy-20261026'))
$taskResult = [IO.Path]::GetFullPath($ResultBase)
if ([IO.Path]::GetDirectoryName($taskResult) -ne $taskEvidence -or
    [IO.Path]::GetFileName($taskResult) -notmatch '^[A-Za-z0-9_-]+$') { throw 'Result path outside exact task evidence root' }
$taskHosts = @{web='hunter@129.212.153.245';db='hunter@157.245.113.244'}
$taskTarget = $taskHosts[$Role]
$taskSelector = 'careertrellis-preplan-public-'+$Mode+'-'+$Role+'-'+$PackageManifestSha256
$taskRequest = New-WorkflowSshPythonRequest -Target $taskTarget -ScriptPath $ScriptPath -UseSudo
$taskInput = [Text.Encoding]::UTF8.GetBytes($taskRequest.InputText)
$taskHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($taskInput)).ToLowerInvariant()
if ($taskHash -ne $StdinSha256 -or $taskInput.Contains([byte]13)) { throw 'Reviewed LF stdin binding mismatch' }
$taskArguments = @('-T','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','ForwardAgent=no',
    '-o','ClearAllForwardings=yes','-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no',
    '-o','HostKeyAlgorithms=ssh-ed25519',$taskTarget,('sudo -n /usr/bin/python3 -I -S -B - '+$taskSelector))
if (-not $Run) {
    [pscustomobject]@{execute=$false;target=$taskTarget;public_mode=$Mode;original_argv_selector=$taskSelector;stdin_sha256=$taskHash;
        stdin_bytes=$taskInput.Length;process_tree_timeout=$false;result_base=$taskResult}|ConvertTo-Json
    return
}
foreach ($taskSuffix in @('.planned.json','.started.json','.sent.json','.stdout.txt','.stderr.txt','.result.json','.unknown.json')) {
    if (Test-Path -LiteralPath ($taskResult+$taskSuffix)) { throw 'Existing attempt evidence; inspect original and do not replay' }
}
function Write-TaskReceipt([string]$Suffix, [object]$Value) {
    $taskBytes = [Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 6))
    $taskFile = [IO.File]::Open($taskResult+$Suffix,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $taskFile.Write($taskBytes); $taskFile.Flush($true) } finally { $taskFile.Dispose() }
}
function Write-TaskText([string]$Suffix,[string]$Value) {
    $taskBytes = [Text.Encoding]::UTF8.GetBytes($Value)
    $taskFile = [IO.File]::Open($taskResult+$Suffix,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $taskFile.Write($taskBytes); $taskFile.Flush($true) } finally { $taskFile.Dispose() }
}
Write-TaskReceipt '.planned.json' @{utc=[DateTime]::UtcNow.ToString('o');local_owner_pid=$PID;
    stdin_sha256=$taskHash;stdin_bytes=$taskInput.Length;target=$taskTarget;public_mode=$Mode;original_argv_selector=$taskSelector;
    process_tree_timeout=$false;remote_outcome_requires_receipt=$true}
$taskStart = [Diagnostics.ProcessStartInfo]::new('ssh.exe')
$taskStart.UseShellExecute=$false
$taskStart.CreateNoWindow=$true
$taskStart.RedirectStandardInput=$true
$taskStart.RedirectStandardOutput=$true
$taskStart.RedirectStandardError=$true
$taskStart.WorkingDirectory=$taskRoot
foreach ($taskArgument in $taskArguments) { $taskStart.ArgumentList.Add($taskArgument) }
$taskProcess = [Diagnostics.Process]::new()
$taskProcess.StartInfo=$taskStart
$taskStarted=$false
try {
    if (-not $taskProcess.Start()) { throw 'SSH process did not start' }
    $taskStarted=$true
    Write-TaskReceipt '.started.json' @{utc=[DateTime]::UtcNow.ToString('o');local_owner_pid=$PID;ssh_pid=$taskProcess.Id}
    $taskStdout = $taskProcess.StandardOutput.ReadToEndAsync()
    $taskStderr = $taskProcess.StandardError.ReadToEndAsync()
    $taskProcess.StandardInput.BaseStream.Write($taskInput,0,$taskInput.Length)
    $taskProcess.StandardInput.BaseStream.Flush()
    $taskProcess.StandardInput.Close()
    Write-TaskReceipt '.sent.json' @{utc=[DateTime]::UtcNow.ToString('o');ssh_pid=$taskProcess.Id;
        complete_stdin_sent=$true;stdin_sha256=$taskHash}
    # No timeout, cancellation, CloseMainWindow, Kill, Stop-Process or signal.
    # An unknown remote capture owner must remain for attended reconciliation.
    $taskProcess.WaitForExit()
    $taskOutput = $taskStdout.GetAwaiter().GetResult()
    $taskError = $taskStderr.GetAwaiter().GetResult()
    Write-TaskText '.stdout.txt' $taskOutput
    Write-TaskText '.stderr.txt' $taskError
    Write-TaskReceipt '.result.json' @{utc=[DateTime]::UtcNow.ToString('o');ssh_exit=$taskProcess.ExitCode;
        local_owner_pid=$PID;ssh_pid=$taskProcess.Id;stdin_sha256=$taskHash;
        remote_outcome='inspect native payload, owner, unit and receipts; SSH exit alone is insufficient'}
    [pscustomobject]@{ssh_exit=$taskProcess.ExitCode;result_base=$taskResult;
        remote_outcome_requires_inspection=$true}|ConvertTo-Json
}
catch {
    try { Write-TaskReceipt '.unknown.json' @{utc=[DateTime]::UtcNow.ToString('o');local_owner_pid=$PID;
        ssh_pid=$(if($taskStarted){$taskProcess.Id}else{$null});classification='local_transport_exception_inspect_no_replay'} } catch {}
    if ($taskStarted) {
        while (-not $taskProcess.HasExited) { Start-Sleep -Seconds 1 }
    }
    throw 'Retained transport error; inspect original attempt, never replay blindly'
}
finally {
    if (-not $taskStarted -or $taskProcess.HasExited) { $taskProcess.Dispose() }
}
