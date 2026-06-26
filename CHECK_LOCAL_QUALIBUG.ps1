[CmdletBinding()]
param(
    [int]$Tail = 30
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Project = if ($env:QUALIBUG_PROJECT) { $env:QUALIBUG_PROJECT } else { "real_project_demo" }
$OutDir = Join-Path $Root ("platform_outputs\" + $Project)

function Show-JsonFile {
    param([string]$Label, [string]$Path)
    if (Test-Path $Path) {
        Write-Host "`n=== $Label ===" -ForegroundColor Cyan
        Get-Content $Path -Raw
    } else {
        Write-Host "`n=== $Label ===`nnot found"
    }
}

Write-Host "QualiBug Local Status | project=$Project" -ForegroundColor Green
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -Method Get -TimeoutSec 3
    Write-Host "MES target: HEALTHY" -ForegroundColor Green
} catch {
    Write-Host "MES target: OFFLINE" -ForegroundColor Yellow
}

Show-JsonFile -Label "Launcher PIDs" -Path (Join-Path $OutDir "local_launcher_pids.json")
Show-JsonFile -Label "Worker PID" -Path (Join-Path $OutDir ".discovery_pid.json")
Show-JsonFile -Label "Heartbeat" -Path (Join-Path $OutDir ".loop_heartbeat.json")
Show-JsonFile -Label "Latest Result" -Path (Join-Path $OutDir ".discovery_result.json")
Show-JsonFile -Label "Last Failure" -Path (Join-Path $OutDir ".last_loop_failure.json")

foreach ($logName in @("cron_loop.log", "cron_worker.log", "loop_daemon.log", "local_daemon.stdout.log", "local_daemon.stderr.log", "mes_target.stderr.log")) {
    $log = Join-Path $OutDir $logName
    if (Test-Path $log) {
        Write-Host "`n=== tail $logName ===" -ForegroundColor Cyan
        Get-Content $log -Tail $Tail
    }
}
