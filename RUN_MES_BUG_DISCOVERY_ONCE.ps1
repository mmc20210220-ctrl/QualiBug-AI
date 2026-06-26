[CmdletBinding()]
param(
    [switch]$SkipDeepSeekCheck,
    [ValidateRange(5, 120)]
    [int]$TimeoutMinutes = 45
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$env:QUALIBUG_PROJECT = "real_project_demo"
$Project = $env:QUALIBUG_PROJECT
$OutDir = Join-Path $Root ("platform_outputs\" + $Project)
$ConfigPath = Join-Path $Root "platform_inputs\real_project_demo\real_project_config.json"
$TargetRoot = Join-Path $Root "mes_target\mes-buglab-target"

if (-not (Test-Path $ConfigPath)) { throw "MES project config is missing: $ConfigPath" }
if (-not (Test-Path $TargetRoot)) { throw "MES BugLab target is missing: $TargetRoot" }
if (-not (Test-Path (Join-Path $Root ".env.local"))) {
    throw ".env.local is missing. Run .\BOOTSTRAP_LOCAL.ps1, fill in the DeepSeek values, then run .\TEST_DEEPSEEK_CONNECTION.ps1."
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$PidPath = Join-Path $OutDir ".discovery_pid.json"

# A previously running worker is authoritative. Attach rather than starting a duplicate.
$existingPid = $null
if (Test-Path $PidPath) {
    try { $existingPid = (Get-Content $PidPath -Raw | ConvertFrom-Json).pid } catch {}
}
if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
    Write-Host "A MES discovery worker is already active (pid=$existingPid). Attaching to its status." -ForegroundColor Yellow
} else {
    & (Join-Path $Root "START_LOCAL_QUALIBUG.ps1") -Mode Once -SkipDeepSeekCheck:$SkipDeepSeekCheck
    if ($LASTEXITCODE -ne 0) { throw "MES discovery launch failed with exit code $LASTEXITCODE" }
}

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$lastSignature = ""
$pid = $null
while ((Get-Date) -lt $deadline) {
    if (-not $pid -and (Test-Path $PidPath)) {
        try { $pid = (Get-Content $PidPath -Raw | ConvertFrom-Json).pid } catch {}
        if ($pid) { Write-Host "MES discovery worker pid=$pid" -ForegroundColor Cyan }
    }

    $heartbeatPath = Join-Path $OutDir ".loop_heartbeat.json"
    if (Test-Path $heartbeatPath) {
        try {
            $hb = Get-Content $heartbeatPath -Raw | ConvertFrom-Json
            $signature = "$($hb.status)|$($hb.step)|$($hb.updated_at)"
            if ($signature -ne $lastSignature) {
                Write-Host ("[{0}] status={1} step={2} detail={3}" -f (Get-Date -Format "HH:mm:ss"), $hb.status, $hb.step, $hb.detail) -ForegroundColor DarkCyan
                $lastSignature = $signature
            }
        } catch {}
    }

    $resultPath = Join-Path $OutDir ".discovery_result.json"
    if (Test-Path $resultPath) {
        # Result is written atomically only when the worker exits.
        break
    }
    Start-Sleep -Seconds 5
}

if (-not (Test-Path (Join-Path $OutDir ".discovery_result.json"))) {
    throw "Timed out after $TimeoutMinutes minutes without a durable result. Run .\CHECK_LOCAL_QUALIBUG.ps1 and inspect $OutDir."
}

& (Join-Path $Root "SHOW_MES_DISCOVERY_RESULT.ps1")
$result = Get-Content (Join-Path $OutDir ".discovery_result.json") -Raw | ConvertFrom-Json
$terminal = [string]$result.terminal
if ($terminal -notin @("COMPLETED", "CONVERGED")) {
    throw "MES discovery ended with terminal=$terminal. The result and logs were retained for inspection; no failure was converted to success."
}

Write-Host "MES discovery completed. Candidate findings remain subject to evidence and human review." -ForegroundColor Green
