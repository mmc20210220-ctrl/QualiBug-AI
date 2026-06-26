[CmdletBinding()]
param(
    [ValidateSet("Once", "Daemon")]
    [string]$Mode = "Daemon",
    [switch]$SkipDeepSeekCheck
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Project = if ($env:QUALIBUG_PROJECT) { $env:QUALIBUG_PROJECT } else { "real_project_demo" }
$OutDir = Join-Path $Root ("platform_outputs\" + $Project)
$TargetRoot = Join-Path $Root "mes_target\mes-buglab-target"
$PidFile = Join-Path $OutDir "local_launcher_pids.json"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Find-Python {
    foreach ($name in @("python.exe", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "Python 3.11+ was not found on PATH. Run .\BOOTSTRAP_LOCAL.ps1 after activating your intended Python / Conda environment."
}

function Wait-ForHealth {
    param([int]$Seconds = 45)
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -Method Get -TimeoutSec 3
            if ($resp) { return $true }
        } catch {}
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

$Python = Find-Python
& $Python -c "import fastapi, uvicorn; print('DEPENDENCIES_OK')"
if ($LASTEXITCODE -ne 0) { throw "Missing FastAPI / Uvicorn. Run .\BOOTSTRAP_LOCAL.ps1." }

if (-not (Test-Path .\.env.local)) {
    Copy-Item .\.env.local.example .\.env.local
    throw ".env.local was created. Add your local DeepSeek key and model, then run .\TEST_DEEPSEEK_CONNECTION.ps1. No Loop was started."
}

# Validate local configuration without reading it into console output.
$envText = Get-Content .\.env.local -Raw
foreach ($key in @("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")) {
    if ($envText -notmatch "(?m)^$key=(?!\s*$)(?!REPLACE_WITH).+") {
        throw ".env.local does not contain a usable $key. The secret was not printed."
    }
}

if (-not $SkipDeepSeekCheck) {
    Write-Host "Checking DeepSeek network reachability..."
    $status = (& curl.exe -sS -o NUL -w "%{http_code}" -I https://api.deepseek.com) 2>$null
    if ($status -notmatch "^(200|401|403|404)$") {
        throw "DeepSeek network check failed (HTTP '$status'). Use .\TEST_DEEPSEEK_CONNECTION.ps1 after fixing network / proxy."
    }
    Write-Host "DeepSeek endpoint reachable (HTTP $status)." -ForegroundColor Green
}

$targetWasStarted = $false
if (Wait-ForHealth -Seconds 2) {
    Write-Host "MES BugLab target is already healthy on 127.0.0.1:8000." -ForegroundColor Green
} else {
    $targetOut = Join-Path $OutDir "mes_target.stdout.log"
    $targetErr = Join-Path $OutDir "mes_target.stderr.log"
    Write-Host "Starting MES BugLab target..."
    $targetProc = Start-Process -FilePath $Python -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $TargetRoot -PassThru -RedirectStandardOutput $targetOut -RedirectStandardError $targetErr -WindowStyle Hidden
    $targetWasStarted = $true
    if (-not (Wait-ForHealth -Seconds 45)) {
        if ($targetProc -and -not $targetProc.HasExited) { Stop-Process -Id $targetProc.Id -Force }
        throw "MES BugLab did not become healthy. Inspect $targetErr"
    }
    Write-Host "MES BugLab target is healthy." -ForegroundColor Green
}

$record = @{
    project = $Project
    started_at = (Get-Date).ToString("o")
    target_pid = if ($targetWasStarted) { $targetProc.Id } else { $null }
    mode = $Mode
    supervisor_pid = $null
} 

if ($Mode -eq "Once") {
    Write-Host "Starting exactly one supervised discovery worker..."
    & $Python .\run_cron_loop.py
    if ($LASTEXITCODE -ne 0) { throw "run_cron_loop.py failed with exit code $LASTEXITCODE" }
    Write-Host "Worker launch requested. Use .\CHECK_LOCAL_QUALIBUG.ps1 to monitor heartbeat and logs." -ForegroundColor Green
} else {
    $existingSupervisor = $null
    if (Test-Path $PidFile) {
        try { $existingSupervisor = (Get-Content $PidFile -Raw | ConvertFrom-Json).supervisor_pid } catch {}
    }
    if ($existingSupervisor -and (Get-Process -Id $existingSupervisor -ErrorAction SilentlyContinue)) {
        Write-Host "Local daemon already running (pid=$existingSupervisor). No duplicate supervisor was started." -ForegroundColor Yellow
    } else {
        $daemonOut = Join-Path $OutDir "local_daemon.stdout.log"
        $daemonErr = Join-Path $OutDir "local_daemon.stderr.log"
        Write-Host "Starting single supervised QualiBug daemon..."
        $daemonProc = Start-Process -FilePath $Python -ArgumentList @("-u", "loop_daemon.py") -WorkingDirectory $Root -PassThru -RedirectStandardOutput $daemonOut -RedirectStandardError $daemonErr -WindowStyle Hidden
        $record.supervisor_pid = $daemonProc.Id
        Write-Host "Daemon started (pid=$($daemonProc.Id))." -ForegroundColor Green
    }
}

$record | ConvertTo-Json -Depth 4 | Set-Content -Path $PidFile -Encoding UTF8
Write-Host "Started safely. Do not also start loop_daemon.py, run_loop1_sweep.py, run_loop2_improve.py, run_continuous_loop.py, or another scheduler." -ForegroundColor Yellow
Write-Host "Monitor with: .\CHECK_LOCAL_QUALIBUG.ps1" -ForegroundColor Cyan
